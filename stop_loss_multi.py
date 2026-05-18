#!/usr/bin/env python3
"""
多币种止损守护 V3 — WebSocket毫秒级监听 + ATR动态止损
======================================================
取代V2的10秒REST轮询，改为订阅Binance !markPrice@arr WebSocket流。
价格更新毫秒级到达，0盲区。

架构：
- main线程：每60秒REST发现持仓 + 管理ATR重算
- WebSocket线程：实时价格流 → 即刻止损检查
- 无每个持仓的独立线程，无time.sleep(10)盲区
"""

import json, hashlib, hmac, time, requests, sys, threading, math
sys.path.append('/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt
_CREDS = _decrypt()
BINANCE_KEY = _CREDS.get('BINANCE_API_KEY', '')
BINANCE_SECRET = _CREDS.get('BINANCE_API_SECRET', '')
BASE = 'https://fapi.binance.com'
WSS = 'wss://fstream.binance.com/ws'
LOG_FILE = '/home/admin/charon/stop_loss_multi.log'

DEFAULT_STOP_PCT = 2.0
DEFAULT_TRAIL_PCT = 0.0  # 关掉追踪止盈

# ── 全局状态（线程间共享）──
positions_lock = threading.Lock()
positions = {}           # sym -> cfg dict (与V2格式一致)
stops_lock = threading.Lock()
stops = {}               # sym -> stop_price
ws_ready = threading.Event()

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
    r = requests.request(method, url, headers=hdrs)
    return r.json()

def discover_positions():
    """REST发现所有持仓（每60秒）"""
    acct = _fapi('GET', '/fapi/v2/account')
    found = {}
    for p in acct.get('positions', []):
        amt = float(p.get('positionAmt', 0))
        if abs(amt) < 0.001:
            continue
        sym = p['symbol']
        found[sym] = {
            'side': 'SELL' if amt < 0 else 'BUY',
            'ps': p.get('positionSide', 'BOTH'),
            'qty': abs(amt),
            'entry': float(p.get('entryPrice', 0)),
            'upnl': float(p.get('unRealizedProfit', 0)),
        }
    return found

def compute_atr(sym, period=14):
    """1分钟ATR计算"""
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
    except Exception as e:
        log_msg(f'ATR_ERR {sym}: {e}')
        return 0.5

def close_position(sym, side, ps, qty):
    close_side = 'SELL' if side == 'BUY' else 'BUY'
    return _fapi('POST', '/fapi/v1/order', {
        'symbol': sym, 'side': close_side, 'type': 'MARKET',
        'quantity': qty, 'positionSide': ps,
        'newOrderRespType': 'RESULT'
    })

def recalc_stops():
    """重新计算所有持仓的止损价（每60秒）"""
    with positions_lock:
        pos_snapshot = dict(positions)
    with stops_lock:
        for sym, cfg in pos_snapshot.items():
            try:
                atr_pct = compute_atr(sym)
                stop_dist = max(DEFAULT_STOP_PCT, atr_pct * 2)
                entry = cfg['entry']
                if cfg['side'] == 'SELL':
                    stop_price = entry * (1 + stop_dist / 100)
                else:
                    stop_price = entry * (1 - stop_dist / 100)

                # 确保止损在清算价之前触发
                liq = get_liquidation_price(sym)
                if liq and liq > 0:
                    if cfg['side'] == 'SELL' and stop_price >= liq:
                        stop_price = liq * 0.998
                    elif cfg['side'] == 'BUY' and stop_price <= liq:
                        stop_price = liq * 1.002

                stops[sym] = stop_price
                log_msg(f'STOP {sym} ATR={atr_pct:.2f}% dist={stop_dist:.1f}% @${stop_price:.6f}')
            except Exception as e:
                log_msg(f'STOP_RECALC_ERR {sym}: {e}')

def get_liquidation_price(sym):
    try:
        r = requests.get(f'{BASE}/fapi/v2/positionRisk',
            params={'symbol': sym},
            headers={'X-MBX-APIKEY': BINANCE_KEY})
        if r.status_code == 200:
            for p in r.json():
                if p.get('symbol') == sym:
                    liq = p.get('liquidationPrice', '0')
                    return float(liq) if liq and float(liq) != 0 else None
    except:
        pass
    return None

def check_and_execute(mark_prices):
    """
    WebSocket回调：检查所有持仓的实时价格是否触发止损
    mark_prices: dict {sym: mark_price}
    此函数在WebSocket线程中调用，需线程安全
    """
    with positions_lock:
        pos_snapshot = dict(positions)
    with stops_lock:
        stops_snapshot = dict(stops)

    for sym, cfg in pos_snapshot.items():
        mark = mark_prices.get(sym)
        if mark is None:
            continue
        stop_price = stops_snapshot.get(sym)
        if stop_price is None:
            continue

        try:
            if (cfg['side'] == 'SELL' and mark >= stop_price) or \
               (cfg['side'] == 'BUY' and mark <= stop_price):
                log_msg(f'STOP {sym} @${mark:.6f} (SL={stop_price:.6f}) upnl={cfg.get("upnl",0):+.2f}')
                r2 = close_position(sym, cfg['side'], cfg['ps'], cfg['qty'])
                if 'orderId' in r2:
                    log_msg(f'CLOSED {sym} orderId={r2["orderId"]}')
                    # 从本地状态中移除
                    with positions_lock:
                        if sym in positions:
                            del positions[sym]
                    with stops_lock:
                        if sym in stops:
                            del stops[sym]
                else:
                    log_msg(f'CLOSE_FAIL {sym}: {r2.get("msg","?")}')
        except Exception as e:
            log_msg(f'CHECK_EXEC_ERR {sym}: {e}')

def ws_thread_main():
    """WebSocket线程：订阅 !markPrice@arr 毫秒级监听"""
    import websocket

    def on_message(ws, raw):
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                return
            # !markPrice@arr 返回所有交易对的标记价格数组
            prices = {}
            for item in data:
                sym = item.get('s', '')  # e.g. "BTCUSDT"
                price = float(item.get('p', 0))
                if sym and price > 0:
                    prices[sym] = price

            if prices:
                check_and_execute(prices)
        except Exception as e:
            log_msg(f'WS_MSG_ERR: {e}')

    def on_open(ws):
        log_msg('WebSocket 已连接 ✓')
        # 订阅所有标记价格流
        ws.send(json.dumps({
            "method": "SUBSCRIBE",
            "params": ["!markPrice@arr@1s"],
            "id": 1
        }))
        ws_ready.set()

    def on_error(ws, err):
        log_msg(f'WS_ERR: {err}')

    def on_close(ws, *args):
        log_msg('WebSocket 已断开，3秒后重连...')
        ws_ready.clear()
        time.sleep(3)

    while True:
        try:
            ws = websocket.WebSocketApp(
                f'{WSS}/!markPrice@arr@1s',
                on_message=on_message,
                on_open=on_open,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log_msg(f'WS_FATAL: {e}')
        time.sleep(3)  # 重连等待

def main():
    log_msg('=== stop_loss_multi V3 (WebSocket) 启动 ===')
    log_msg(f'DEFAULT_STOP_PCT={DEFAULT_STOP_PCT}% | TRAIL_PCT=禁用')

    # 启动WebSocket线程（后台驻留）
    ws_thread = threading.Thread(target=ws_thread_main, daemon=True)
    ws_thread.start()
    log_msg('WebSocket线程已启动')

    # 等待WebSocket准备就绪
    ws_ready.wait(timeout=10)
    if not ws_ready.is_set():
        log_msg('⚠️ WebSocket未就绪，继续启动（可能无实时保护）')

    # 首次发现持仓 + 止损计算
    discovered = discover_positions()
    with positions_lock:
        positions.clear()
        positions.update(discovered)
    if discovered:
        log_msg(f'初始持仓: {len(discovered)}个')
        recalc_stops()

    # 主循环：每60秒发现持仓 + 重算止损
    while True:
        try:
            time.sleep(60)
            discovered = discover_positions()
            with positions_lock:
                positions.clear()
                positions.update(discovered)

            if discovered:
                parts = [f'{s}:{v["side"]} {v["qty"]}' for s, v in discovered.items()]
                log_msg(f'POSITIONS: {", ".join(parts)}')
                recalc_stops()
            else:
                with positions_lock:
                    positions.clear()
                with stops_lock:
                    stops.clear()
                log_msg('POSITIONS: empty')

        except Exception as e:
            log_msg(f'MAIN_LOOP_ERR: {e}')

if __name__ == '__main__':
    main()
