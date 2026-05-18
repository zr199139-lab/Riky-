#!/usr/bin/env python3
"""多币种止损守护 V2 — 自动发现持仓 + ATR动态止损"""
import json, hashlib, hmac, time, requests, sys, threading, math
sys.path.append('/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt
_CREDS = _decrypt()
BINANCE_KEY = _CREDS.get('BINANCE_API_KEY', '')
BINANCE_SECRET = _CREDS.get('BINANCE_API_SECRET', '')
BASE = 'https://fapi.binance.com'
LOG_FILE = '/home/admin/charon/stop_loss_multi.log'

DEFAULT_STOP_PCT = 2.0
DEFAULT_TRAIL_PCT = 3.0

def log_msg(msg):
    t = time.strftime('%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def _fapi(method, path, params=None):
    ts = int(time.time() * 1000)
    p = dict(params or {})
    p['timestamp'] = ts
    qs = '&'.join(f'{k}={v}' for k, v in sorted(p.items()))
    sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'{BASE}{path}?{qs}&signature={sig}'
    hdrs = {'X-MBX-APIKEY': BINANCE_KEY}
    if method == 'GET':
        r = requests.get(url, headers=hdrs)
    elif method == 'POST':
        r = requests.post(url, headers=hdrs)
    return r.json()

def discover_positions():
    """从交易所自动发现所有持仓"""
    acct = _fapi('GET', '/fapi/v2/account')
    positions = {}
    for p in acct.get('positions', []):
        amt = float(p.get('positionAmt', 0))
        if abs(amt) < 0.001:
            continue
        sym = p['symbol']
        positions[sym] = {
            'side': 'SELL' if amt < 0 else 'BUY',
            'ps': p.get('positionSide', 'BOTH'),
            'qty': abs(amt),
            'entry': float(p.get('entryPrice', 0)),
            'upnl': float(p.get('unRealizedProfit', 0)),
        }
    return positions

def compute_atr(sym, period=14):
    try:
        klines = _fapi('GET', '/fapi/v1/klines', {
            'symbol': sym, 'interval': '1m', 'limit': period + 1
        })
        if not klines or 'code' in klines:
            return 0.5
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        tr_sum = 0
        for i in range(1, len(closes)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr_sum += max(hl, hc, lc)
        atr = tr_sum / (len(closes) - 1)
        avg_price = sum(closes) / len(closes)
        return atr / avg_price * 100 if avg_price > 0 else 0.5
    except:
        return 0.5

def close_position(sym, side, ps, qty):
    # BUGFIX: 平多单用SELL，平空单用BUY
    close_side = 'SELL' if side == 'BUY' else 'BUY'
    return _fapi('POST', '/fapi/v1/order', {
        'symbol': sym, 'side': close_side, 'type': 'MARKET',
        'quantity': qty, 'positionSide': ps,
        'newOrderRespType': 'RESULT'
    })

def get_liquidation_price(sym):
    """获取清算价"""
    try:
        r = requests.get(f'https://fapi.binance.com/fapi/v2/positionRisk',
            params={'symbol': sym})
        if r.status_code == 200:
            for p in r.json():
                if p.get('symbol') == sym:
                    liq = p.get('liquidationPrice', '0')
                    return float(liq) if liq else None
    except:
        pass
    return None

def monitor_position(sym, cfg):
    trail_pct = DEFAULT_TRAIL_PCT
    extreme = None
    stop_price = None
    last_recalc = 0

    while True:
        try:
            now = time.time()
            if stop_price is None or now - last_recalc > 60:
                atr_pct = compute_atr(sym)
                stop_dist = max(DEFAULT_STOP_PCT, atr_pct * 2)
                entry = cfg['entry']
                if cfg['side'] == 'SELL':
                    stop_price = entry * (1 + stop_dist / 100)
                else:
                    stop_price = entry * (1 - stop_dist / 100)

                # BUGFIX 2026-05-18: 确保止损价在清算价之前触发
                liq = get_liquidation_price(sym)
                if liq and liq > 0:
                    if cfg['side'] == 'SELL':
                        # Short: stop must be BELOW liquidation to trigger first
                        if stop_price >= liq:
                            old_stop = stop_price
                            stop_price = liq * 0.998  # 0.2% below liquidation
                            log_msg(f'[FIX] stop {old_stop:.2f} was >= liq {liq:.2f}, adjusted to {stop_price:.2f}')
                    else:
                        # Long: stop must be ABOVE liquidation
                        if stop_price <= liq:
                            old_stop = stop_price
                            stop_price = liq * 1.002  # 0.2% above liquidation
                            log_msg(f'[FIX] stop {old_stop:.2f} was <= liq {liq:.2f}, adjusted to {stop_price:.2f}')

                last_recalc = now
                log_msg(f'ATR={atr_pct:.2f}% stop_dist={stop_dist:.1f}% stop={stop_price:.6f}')

            r = requests.get(f'{BASE}/fapi/v1/premiumIndex', params={'symbol': sym})
            if r.status_code != 200:
                time.sleep(10)
                continue
            mark = float(r.json()['markPrice'])

            # 固定止损
            if (cfg['side'] == 'SELL' and mark >= stop_price) or \
               (cfg['side'] == 'BUY' and mark <= stop_price):
                upnl = cfg.get('upnl', 0)
                log_msg(f'STOP {sym} @${mark:.6f} (SL={stop_price:.6f}) upnl={upnl:+.2f}')
                r2 = close_position(sym, cfg['side'], cfg['ps'], cfg['qty'])
                if 'orderId' in r2:
                    oid = r2['orderId']
                    log_msg(f'CLOSED {sym} orderId={oid}')
                else:
                    err = r2.get('msg', '?')
                    log_msg(f'CLOSE_FAIL {sym}: {err}')
                return

            # 追踪止盈
            if trail_pct > 0:
                if extreme is None:
                    extreme = mark
                else:
                    if cfg['side'] == 'SELL':
                        if mark < extreme:
                            extreme = mark
                        bounce = (mark - extreme) / extreme * 100
                        if bounce >= trail_pct:
                            log_msg(f'TRAIL_TP {sym} @${mark:.6f} bounce={bounce:.1f}%')
                            r2 = close_position(sym, cfg['side'], cfg['ps'], cfg['qty'])
                            if 'orderId' in r2:
                                log_msg(f'CLOSED {sym}')
                            return
                    else:
                        if mark > extreme:
                            extreme = mark
                        drop = (extreme - mark) / extreme * 100
                        if drop >= trail_pct:
                            log_msg(f'TRAIL_TP {sym} @${mark:.6f} drop={drop:.1f}%')
                            r2 = close_position(sym, cfg['side'], cfg['ps'], cfg['qty'])
                            if 'orderId' in r2:
                                log_msg(f'CLOSED {sym}')
                            return
        except Exception as e:
            log_msg(f'ERR {sym}: {e}')
        time.sleep(10)

def main():
    log_msg('stop_loss_multi V2 启动')
    monitored = {}

    while True:
        try:
            positions = discover_positions()
            for sym, cfg in positions.items():
                if sym not in monitored or not monitored[sym].is_alive():
                    t = threading.Thread(target=monitor_position, args=(sym, cfg), daemon=True)
                    t.start()
                    monitored[sym] = t
                    log_msg(f'WATCH {sym} {cfg["side"]} {cfg["qty"]} @${cfg["entry"]:.6f}')

            if positions:
                parts = [f'{s}:{p["side"]} {p["qty"]}' for s, p in positions.items()]
                log_msg(f'POSITIONS: {", ".join(parts)}')
            else:
                log_msg('POSITIONS: empty')
        except Exception as e:
            log_msg(f'DISCOVER_ERR: {e}')
        time.sleep(60)

if __name__ == '__main__':
    main()
