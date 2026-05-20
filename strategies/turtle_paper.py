#!/usr/bin/env python3
"""
海龟交易系统 · 虚拟盘
经典趋势跟踪：20日突破入场, ATR×2止损, 10日反向突破离场
"""
INITIAL_CAPITAL = 500.0  # 虚拟本金
SYMBOLS = ['BTC/USDT', 'ETH/USDT']
TIMEFRAME = '4h'
UNIT_RISK = 0.01  # 每单位风险1%本金
ATR_STOP_MULT = 2.0

import ccxt, json, time, os, sys, hashlib, hmac, urllib.parse, requests
from datetime import datetime
from pathlib import Path

LOG_FILE = os.path.expanduser('~/charon/bot_logs/turtle_paper.log')
STATE_FILE = os.path.expanduser('~/charon/bot_logs/turtle_paper_state.json')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

ex = ccxt.binance()
ex.load_markets()

state = {'cash': INITIAL_CAPITAL, 'positions': {}, 'trades': 0, 'pnl': 0.0}
if os.path.exists(STATE_FILE):
    try:
        state = json.load(open(STATE_FILE))
    except:
        pass

def fetch_klines(symbol, limit=100):
    return ex.fetch_ohlcv(symbol, TIMEFRAME, limit=limit)

def calc_atr(klines, period=14):
    highs = [k[2] for k in klines[-period-1:]]
    lows = [k[3] for k in klines[-period-1:]]
    closes = [k[4] for k in klines[-period-1:]]
    tr = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr.append(max(hl, hc, lc))
    return sum(tr[-period:]) / period if len(tr) >= period else 1.0

def entry_signal(klines):
    closes = [k[4] for k in klines]
    if len(closes) < 21: return 0
    hh20 = max(k[2] for k in klines[-21:-1])  # 20日高点
    ll20 = min(k[3] for k in klines[-21:-1])  # 20日低点
    now = closes[-1]
    if now > hh20: return 1   # 突破买入
    if now < ll20: return -1  # 突破卖出
    return 0

def exit_signal(klines, side):
    closes = [k[4] for k in klines]
    if len(closes) < 11: return False
    hh10 = max(k[2] for k in klines[-11:-1])
    ll10 = min(k[3] for k in klines[-11:-1])
    now = closes[-1]
    if side == 'long' and now < ll10: return True
    if side == 'short' and now > hh10: return True
    return False

def position_size(atr, price):
    risk_per_unit = INITIAL_CAPITAL * UNIT_RISK
    raw = risk_per_unit / (atr * ATR_STOP_MULT) if atr > 0 else 0
    val = raw * price
    max_val = state['cash'] * 0.5
    if val > max_val: raw = max_val / price
    return round(raw, 6)

log('=== 海龟交易系统 启动 ===')
log(f'资金: ${INITIAL_CAPITAL}, 周期: {TIMEFRAME}, 币种: {SYMBOLS}')

loop = 0
while True:
    try:
        for sym in SYMBOLS:
            klines = fetch_klines(sym, 100)
            if not klines: continue
            price = klines[-1][4]
            atr = calc_atr(klines)
            sig = entry_signal(klines)
            
            # 持仓管理
            pos = state['positions'].get(sym)
            in_pos = pos is not None
            
            if in_pos:
                entry_p = pos['entry']
                side = pos['side']
                qty = pos['qty']
                pnl = (price - entry_p) * qty if side == 'long' else (entry_p - price) * qty
                
                # ATR止损
                if side == 'long' and price < entry_p - atr * ATR_STOP_MULT:
                    state['cash'] += qty * price
                    state['pnl'] += pnl
                    state['trades'] += 1
                    del state['positions'][sym]
                    log(f'[SL] {sym} LONG {qty:.4f} @ {price:.2f} PnL=${pnl:.2f}')
                    continue
                if side == 'short' and price > entry_p + atr * ATR_STOP_MULT:
                    state['cash'] += qty * (2*entry_p - price) if side=='short' else 0
                    # simplified short close
                    state['cash'] += qty * entry_p - qty * price + qty * entry_p
                    state['pnl'] += pnl
                    state['trades'] += 1
                    del state['positions'][sym]
                    log(f'[SL] {sym} SHORT {qty:.4f} @ {price:.2f} PnL=${pnl:.2f}')
                    continue
                
                # 10日反向突破离场
                if exit_signal(klines, side):
                    state['cash'] += qty * price if side == 'long' else qty * (2*entry_p - price)
                    state['pnl'] += pnl
                    state['trades'] += 1
                    del state['positions'][sym]
                    log(f'[EXIT] {sym} {side.upper()} @ {price:.2f} PnL=${pnl:.2f}')
                    continue
            
            # 开新仓
            if not in_pos and sig != 0:
                qty = position_size(atr, price)
                cost = qty * price
                if cost <= state['cash']:
                    state['cash'] -= cost
                    side = 'long' if sig == 1 else 'short'
                    # For short, cash reserves full
                    state['positions'][sym] = {'entry': price, 'qty': qty, 'side': side, 'time': time.time()}
                    log(f'[OPEN] {sym} {side.upper()} {qty:.4f} @ {price:.2f} ATR={atr:.4f}')
        
        # 计算总权益
        equity = state['cash']
        for sym, pos in state['positions'].items():
            try:
                ticker = ex.fetch_ticker(sym)
                mp = ticker['last']
                if pos['side'] == 'long':
                    equity += pos['qty'] * mp
                else:
                    equity += pos['qty'] * (2*pos['entry'] - mp)
            except:
                pass
        
        # 每20轮报一次状态
        loop += 1
        if loop % 20 == 0:
            c, p, t = state["cash"], len(state["positions"]), state["trades"]
            log(f'[STATUS] 权益=${equity:.2f} 现金=${c:.2f} 持仓={p} 交易={t} PnL=${state["pnl"]:.2f}')
        
        json.dump(state, open(STATE_FILE, 'w'))
        time.sleep(300)  # 5分钟检查一次 (4h K线，不用太频繁)
        
    except Exception as e:
        log(f'[ERROR] {e}')
        time.sleep(60)
