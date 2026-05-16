#!/usr/bin/env python3
"""
暗黑星火主宰 · DS-0 控盘数据采集引擎
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
接管人: DS-0 | 用户: @rikyha0 (控盘人,只看不操作)
版本: v2.0 | 2026-05-15 20:15 CST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
职责: 每5分钟采集全市场数据 → 供给AI做决策
AI决策结果 → 直接执行交易 → 汇报给用户
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os, json, ccxt, time
from datetime import datetime

# ─── 凭据 ─────────────────────────────────
def get_exchange():
    import hashlib, base64
    from cryptography.fernet import Fernet
    d = os.path.expanduser('~/.hermes/mempalace/secure')
    with open(os.path.join(d, '.key'), 'rb') as f: salt = f.read()
    key = base64.urlsafe_b64encode(hashlib.sha256(salt).digest())
    f = Fernet(key)
    with open(os.path.join(d, 'credentials.enc'), 'rb') as fp: data = fp.read()
    creds = json.loads(f.decrypt(data).decode())
    ex = ccxt.binance({
        'apiKey': creds['BINANCE_API_KEY'],
        'secret': creds['BINANCE_API_SECRET'],
        'options': {'defaultType': 'future'}
    })
    ex.load_markets()
    return ex, creds

# ─── 全市场扫描 ──────────────────────────
WATCH_LIST = ['POLYX/USDT:USDT', 'BTC/USDT:USDT', 'ETH/USDT:USDT',
              'MLN/USDT:USDT', 'HYPE/USDT:USDT', 'BILL/USDT:USDT',
              'LAB/USDT:USDT', 'PLAY/USDT:USDT']

def collect_data(ex):
    """采集全市场数据 → 写入 ~/charon/ds0_data.json"""
    data = {
        'time': datetime.now().strftime('%H:%M CST'),
        'timestamp': datetime.now().isoformat(),
    }

    # 1. 账户总览
    bal = ex.fetch_balance()
    info = bal['info']
    data['wallet'] = float(info.get('totalWalletBalance', 0))
    data['avail'] = float(info.get('availableBalance', 0))
    mr = info.get('marginRatio')
    data['margin_ratio'] = float(mr) * 100 if mr else 0  # %

    # 2. 全仓持仓
    positions = ex.fetch_positions()
    live_pos = []
    total_pnl = 0
    for p in positions:
        qty = float(p['contracts'])
        if qty == 0:
            continue
        side = 'long' if qty > 0 else 'short'
        entry = float(p['entryPrice'])
        mark = float(p['markPrice'])
        liq_raw = p['liquidationPrice']
        liq = float(liq_raw) if liq_raw is not None else 0.0
        pnl = float(p['unrealizedPnl'])
        total_pnl += pnl
        live_pos.append({
            'symbol': p['symbol'],
            'side': side,
            'qty': abs(qty),
            'entry': entry,
            'mark': mark,
            'liq': liq,
            'pnl': round(pnl, 2),
            'margin': float(p['initialMargin']),
            'leverage': float(p['leverage']) if p.get('leverage') else 0,
        })
    data['positions'] = live_pos
    data['positions_pnl'] = round(total_pnl, 2)

    # 3. 持仓币种逐个深度扫描 + 主要币种
    scan_symbols = set()
    for pos in live_pos:
        scan_symbols.add(pos['symbol'])
    # 外加主要关注
    for sym in WATCH_LIST:
        scan_symbols.add(sym)

    data['tickers'] = {}
    for sym in scan_symbols:
        try:
            t = ex.fetch_ticker(sym)
            data['tickers'][sym] = {
                'last': t['last'],
                'change': t['percentage'],
                'volume': t['baseVolume'],
                'high_24h': t['high'],
                'low_24h': t['low'],
            }
        except:
            pass

    # 4. 费率数据
    data['funding'] = {}
    for sym in scan_symbols:
        try:
            fr = ex.fetch_funding_rate(sym)
            data['funding'][sym] = {
                'rate': float(fr['fundingRate']),
                'time': fr['fundingTime'],
            }
        except:
            pass

    # 5. 挂单
    data['open_orders'] = {}
    for sym in scan_symbols:
        try:
            orders = ex.fetch_open_orders(sym)
            data['open_orders'][sym] = [{
                'id': o['id'],
                'side': o['side'],
                'type': o['type'],
                'price': o['price'],
                'amount': o['amount'],
                'remaining': o['remaining']
            } for o in orders]
        except:
            pass

    # 6. 今日交易次数
    trade_log = os.path.expanduser('~/charon/trade_count.json')
    try:
        with open(trade_log) as f:
            tc = json.load(f)
        data['trade_count_today'] = tc.get('count', 0) if tc.get('date') == datetime.now().strftime('%Y-%m-%d') else 0
    except:
        data['trade_count_today'] = 0

    # 写入文件
    out = os.path.expanduser('~/charon/ds0_data.json')
    with open(out, 'w') as f:
        json.dump(data, f, indent=2)

    return data

def collect_selected_tickers(ex, symbols):
    """额外扫描指定的交易对"""
    result = {}
    for sym in symbols:
        try:
            t = ex.fetch_ticker(sym)
            fr = ex.fetch_funding_rate(sym)
            result[sym] = {
                'last': t['last'],
                'change': t['percentage'],
                'volume': t['baseVolume'],
                'high_24h': t['high'],
                'low_24h': t['low'],
                'funding': float(fr['fundingRate']) if fr else 0,
            }
        except:
            pass
    return result

if __name__ == '__main__':
    try:
        ex, _ = get_exchange()
        data = collect_data(ex)
        print(f"[DS-0] ✅ 数据采集完成 | 钱包:${data['wallet']} | 持仓:{len(data['positions'])} | PnL:${data['positions_pnl']}")
        print(f"[DS-0] 📋 今日交易: {data['trade_count_today']}次")
        for p in data['positions']:
            liq_dist = (p['mark'] - p['liq']) / p['mark'] * 100
            print(f"[DS-0] 📊 {p['symbol']} {p['side']} {p['qty']:.0f} @ ${p['entry']} | PnL:${p['pnl']} | 清算:{liq_dist:.1f}%")
    except Exception as e:
        print(f"[DS-0] ❌ 数据采集失败: {e}")
