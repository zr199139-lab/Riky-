#!/usr/bin/env python3
"""
波动率均值回归 · 虚拟盘 (布林带+RSI)
价格触BB下轨+RSI<30做多, 触BB上轨+RSI>70做空
ATR动态止损
"""
INITIAL_CAPITAL = 1000.0
SYMBOL = 'ETH/USDT'  # 选BTC/ETH/SOL中波动率最合适的
TIMEFRAME = '1h'
BB_PERIOD = 20
BB_STD = 2.0
RSI_PERIOD = 14
RSI_OVERSOLD = 20       # 熊市收紧: 从30→20 (减少逆势做多)
RSI_OVERBOUGHT = 55     # 熊市放宽: 从70→55 (增加做空频率)
POSITION_PCT = 0.3      # 单笔30%仓位
LEVERAGE = 5            # 虚拟杠杆 (合约模式)
DAILY_LOSS_LIMIT = 5.0  # 日亏$5熔断(模拟$50本金的10%)

import ccxt, json, time, os
from datetime import datetime
import statistics

LOG_FILE = os.path.expanduser('~/charon/bot_logs/meanrevert_paper.log')
STATE_FILE = os.path.expanduser('~/charon/bot_logs/meanrevert_paper_state.json')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

ex = ccxt.binance()
ex.load_markets()

state = {'cash': INITIAL_CAPITAL, 'position': None, 'trades': 0, 'pnl': 0.0,
         'daily_pnl': 0.0, 'daily_date': '', 'funding_collected': 0.0}
if os.path.exists(STATE_FILE):
    try:
        state = json.load(open(STATE_FILE))
    except:
        pass

def fetch_klines(sym, limit=100):
    return ex.fetch_ohlcv(sym, TIMEFRAME, limit=limit)

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0: return 100
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def calc_bb(klines):
    closes = [k[4] for k in klines[-BB_PERIOD:]]
    if len(closes) < BB_PERIOD: return None, None, None
    ma = sum(closes) / len(closes)
    std = statistics.stdev(closes) if len(closes) > 1 else 0
    upper = ma + BB_STD * std
    lower = ma - BB_STD * std
    return upper, ma, lower

def calc_atr(klines, period=14):
    if len(klines) < period + 1: return 0
    trs = []
    for i in range(1, period + 1):
        hl = klines[-i][2] - klines[-i][3]
        hc = abs(klines[-i][2] - klines[-i-1][4])
        lc = abs(klines[-i][3] - klines[-i-1][4])
        trs.append(max(hl, hc, lc))
    return sum(trs) / len(trs)

log('=== 波动率均值回归 启动 ===')
log(f'资金: ${INITIAL_CAPITAL}, 币种: {SYMBOL}, 周期: {TIMEFRAME}')

loop = 0
while True:
    try:
        klines = fetch_klines(SYMBOL, 100)
        if not klines: continue
        price = klines[-1][4]
        closes = [k[4] for k in klines]
        rsi = calc_rsi(closes)
        upper, ma, lower = calc_bb(klines)
        atr = calc_atr(klines)
        
        pos = state.get('position')
        in_pos = pos is not None
        
        if in_pos:
            entry = pos['entry']
            side = pos['side']
            qty = pos['qty']
            pnl = ((price - entry) * qty if side == 'long' else (entry - price) * qty) * LEVERAGE
            
            # 日亏重置
            today = datetime.now().strftime('%Y-%m-%d')
            if state['daily_date'] != today:
                state['daily_pnl'] = 0.0
                state['daily_date'] = today
            
            # 日亏熔断
            if state['daily_pnl'] <= -DAILY_LOSS_LIMIT:
                dpnl = state['daily_pnl']
                log(f'[DAILY_LOSS] 日亏${dpnl:.2f} 熔断, 跳过本轮')
                time.sleep(300)
                continue
            
            # ATR止损 (合约版更紧: 1.0x instead of 1.5x)
            sl_dist = atr * 1.0
            if (side == 'long' and price < entry - sl_dist) or (side == 'short' and price > entry + sl_dist):
                state['cash'] += (qty * price if side == 'long' else qty * (2*entry - price))
                state['pnl'] += pnl
                state['daily_pnl'] += pnl
                state['trades'] += 1
                log(f'[SL] {SYMBOL} {side.upper()} @ {price:.2f} PnL=${pnl:.2f} (x{LEVERAGE})')
                state['position'] = None
                continue
            
            # 回归到均线就止盈
            if (side == 'long' and price >= ma) or (side == 'short' and price <= ma):
                state['cash'] += qty * price if side == 'long' else qty * (2*entry - price)
                state['pnl'] += pnl
                state['daily_pnl'] += pnl
                state['trades'] += 1
                log(f'[TP] {SYMBOL} {side.upper()} @ {price:.2f} (回归均线) PnL=${pnl:.2f} (x{LEVERAGE})')
                state['position'] = None
                continue
        
        # 开仓信号
        if not in_pos and lower is not None:
            # 做多: 触BB下轨 + RSI超卖
            if price <= lower * 1.005 and rsi < RSI_OVERSOLD:
                qty = state['cash'] * POSITION_PCT / price
                cost = qty * price
                state['cash'] -= cost
                state['position'] = {'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()}
                log(f'[OPEN] LONG {SYMBOL} {qty:.4f} @ {price:.2f} RSI={rsi:.1f} BB下轨={lower:.2f}')
            
            # 做空: 触BB上轨 + RSI超买
            elif price >= upper * 0.995 and rsi > RSI_OVERBOUGHT:
                qty = state['cash'] * POSITION_PCT / price
                state['cash'] -= qty * price
                state['position'] = {'entry': price, 'qty': qty, 'side': 'short', 'time': time.time()}
                log(f'[OPEN] SHORT {SYMBOL} {qty:.4f} @ {price:.2f} RSI={rsi:.1f} BB上轨={upper:.2f}')
        
        # 总权益 (合约模式: 保证金+杠杆浮动)
        equity = state['cash']
        pos = state.get('position')
        if pos:
            if pos['side'] == 'long':
                equity += pos['qty'] * price
            else:
                equity += pos['qty'] * (2*pos['entry'] - price)
            # 套用杠杆计算浮动盈亏
            upnl = ((price - pos['entry']) * pos['qty'] if pos['side'] == 'long' else (pos['entry'] - price) * pos['qty']) * (LEVERAGE - 1)
            equity += upnl
        
        # 资金费率采集
        funding_earned = 0.0
        if pos and pos['side'] == 'short':
            try:
                fr = ex.fetch_funding_rate(SYMBOL.replace('/','')+':USDT')
                rate = float(fr['info']['lastFundingRate'])
                if rate > 0:
                    margin_used = pos['qty'] * pos['entry'] / LEVERAGE
                    fee = margin_used * rate * (300 / 28800)
                    state['funding_collected'] = state.get('funding_collected', 0) + fee
                    funding_earned = fee
            except: pass
        
        loop += 1
        if loop % 12 == 0:
            pos_side = state.get("position",{}).get("side","无")
            c, t, fc = state["cash"], state["trades"], state.get("funding_collected", 0)
            dpnl = state.get('daily_pnl', 0)
            log(f'[STATUS] 权益=${equity:.2f} 现金=${c:.2f} 持仓={pos_side} RSI={rsi:.1f} '
                f'交易={t} PnL=${state["pnl"]:.2f} 日亏=${dpnl:.2f} '
                f'费率收入=${fc:.4f} (x{LEVERAGE})')
        
        json.dump(state, open(STATE_FILE, 'w'))
        time.sleep(300)
        
    except Exception as e:
        log(f'[ERROR] {e}')
        time.sleep(60)
