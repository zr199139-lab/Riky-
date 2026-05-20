#!/usr/bin/env python3
"""
熊市做空策略 · 虚拟盘
========================
市场周期: 熊市/BTC日线RSI>60 or BTC<$70000
做空逻辑:
  1. BTC/ETH跌破20日均线 → 做空
  2. 选正费率币种做空（月费>0.005%）
  3. ATR×1.5止损
  4. 目标: 均线回归或-5%强制止盈
币种: BTC, ETH, 高正费率山寨(DOT/NEAR/SUI)
初始资金: $500
杠杆: 3x
"""
import ccxt, json, time, os, sys, numpy as np
from datetime import datetime

INITIAL_CASH = 500.0
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'NEAR/USDT', 'DOT/USDT', 'SUI/USDT']
TIMEFRAME = '4h'
LEVERAGE = 3
STOP_MULT = 1.5
TAKER_FEE = 0.0004

LOG_FILE = os.path.expanduser('~/charon/bot_logs/bear_short_paper.log')
STATE_FILE = os.path.expanduser('~/charon/bot_logs/bear_short_paper_state.json')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    t = datetime.now().strftime('%m-%d %H:%M')
    line = f'[{t}] {msg}'
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')
    print(line, flush=True)

ex = ccxt.binance({'enableRateLimit': True})
ex.load_markets()

state = {'cash': INITIAL_CASH, 'positions': {}, 'trades': 0, 'pnl': 0.0, 'fees': 0.0}
if os.path.exists(STATE_FILE):
    try: state = json.load(open(STATE_FILE))
    except: pass

def fetch_klines(symbol, tf='4h', limit=50):
    try:
        k = ex.fetch_ohlcv(symbol, tf, limit=limit)
        return [(float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in k]
    except Exception as e:
        log(f"K线获取失败 {symbol}: {e}")
        return []

def calc_ma(closes, period=20):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

def calc_atr(klines, period=14):
    if len(klines) < period + 1:
        return 1.0
    highs = [k[0] for k in klines]
    lows = [k[1] for k in klines]
    closes = [k[2] for k in klines]
    trs = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        trs.append(max(hl, hc, lc))
    return sum(trs[-period:]) / period if len(trs) >= period else 1.0

def get_funding(symbol):
    try:
        s = symbol.replace('/', '')
        r = requests_get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}")
        return float(r.get('lastFundingRate', 0)) * 100
    except:
        return 0.0

def requests_get(url):
    import requests as req
    return req.get(url, timeout=5).json()

def check_regime():
    """判断是否熊市"""
    btc_k = fetch_klines('BTC/USDT', '1d', 30)
    if not btc_k:
        return False
    btc_closes = [k[2] for k in btc_k]
    btc_rsi = calc_rsi_raw(btc_closes)
    btc_price = btc_closes[-1]
    ma20 = calc_ma(btc_closes, 20)
    # 熊市条件: BTC跌破MA20 或 RSI>60 或 价格<$70000
    is_bear = (ma20 and btc_price < ma20) or btc_rsi > 60 or btc_price < 70000
    return is_bear

def calc_rsi_raw(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_g = np.mean(gains[-period:])
    avg_l = np.mean(losses[-period:])
    if avg_l < 1e-10:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))

def short_signal(symbol):
    """做空信号: 跌破20日均线 + ATR适中"""
    klines = fetch_klines(symbol, TIMEFRAME, 50)
    if not klines:
        return None
    closes = [k[2] for k in klines]
    current = closes[-1]
    ma20 = calc_ma(closes, 20)
    atr = calc_atr(klines)
    atr_pct = atr / current
    
    if ma20 and current < ma20:
        # 跌破均线，检查ATR是否适中(0.5%~8%)
        if 0.005 < atr_pct < 0.08:
            funding = get_funding(symbol)
            # 正费率有利于做空
            if funding >= 0:
                stop = current + (atr * STOP_MULT)
                tp = current - (atr * 2)
                return {
                    'side': 'short',
                    'entry': current,
                    'stop': stop,
                    'tp': tp,
                    'atr_pct': atr_pct * 100,
                    'funding': funding
                }
    return None

def save_state():
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

log(f"=== 熊市做空策略 启动 | 资金${INITIAL_CASH} | 杠杆{LEVERAGE}x ===")

# 主循环
while True:
    try:
        is_bear = check_regime()

        if not is_bear:
            log("非熊市环境，策略暂停")
            time.sleep(3600)
            continue

        log(f"检测到熊市信号 | 现金${state['cash']:.2f} | 持仓{len(state['positions'])}个")

        # 检查现有持仓
        for sym, pos in list(state['positions'].items()):
            klines = fetch_klines(sym, TIMEFRAME, 10)
            if klines:
                current = klines[-1][2]
                entry = pos['entry']
                stop = pos['stop']
                tp = pos['tp']

                pnl_pct = (entry - current) / entry * LEVERAGE
                pnl_usd = pos.get('margin', state['cash'] * 0.2) * pnl_pct

                # 止损检查
                if current >= stop:
                    fee = pos.get('margin', state['cash'] * 0.2) * TAKER_FEE
                    state['cash'] -= abs(pnl_usd) + fee
                    state['pnl'] += pnl_usd
                    state['fees'] += fee
                    state['trades'] += 1
                    log(f"[止损] {sym} @{current:.4f} PnL=${pnl_usd:.2f}")
                    del state['positions'][sym]
                    save_state()
                    continue

                # 止盈检查(-5%)
                if pnl_pct >= 0.05:
                    fee = pos.get('margin', state['cash'] * 0.2) * TAKER_FEE
                    state['cash'] += pnl_usd - fee
                    state['pnl'] += pnl_usd
                    state['fees'] += fee
                    state['trades'] += 1
                    log(f"[止盈] {sym} @{current:.4f} PnL=${pnl_usd:.2f}")
                    del state['positions'][sym]
                    save_state()
                    continue

        # 扫描新做空机会
        for sym in SYMBOLS:
            if sym in state['positions']:
                continue
            if len(state['positions']) >= 2:
                break

            sig = short_signal(sym)
            if sig:
                margin = state['cash'] * 0.2
                log(f"[做空信号] {sym} @{sig['entry']:.4f} ATR={sig['atr_pct']:.1f}% 费率={sig['funding']:+.3f}%")
                state['positions'][sym] = {
                    'entry': sig['entry'],
                    'stop': sig['stop'],
                    'tp': sig['tp'],
                    'side': 'short',
                    'funding': sig['funding'],
                    'margin': margin,
                    'qty': margin * LEVERAGE / sig['entry']
                }
                save_state()
                log(f"[持仓] {sym} 做空 @{sig['entry']:.4f} 止损@{sig['stop']:.4f} 保证${margin:.2f}")

        time.sleep(1800)

    except Exception as e:
        import traceback
        log(f"异常: {e} | {traceback.format_exc()[:100]}")
        time.sleep(300)
