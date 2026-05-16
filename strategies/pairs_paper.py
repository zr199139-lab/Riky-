#!/usr/bin/env python3
"""
BTC-ETH配对统计套利 · 虚拟盘
当BTC/ETH比率偏离均值>2σ时, 做空强势/做多弱势
"""
INITIAL_CAPITAL = 1000.0
TIMEFRAME = '1h'
ZSCORE_ENTRY = 2.0
ZSCORE_EXIT = 0.5

import ccxt, json, time, os, sys
from datetime import datetime
import statistics

LOG_FILE = os.path.expanduser('~/charon/bot_logs/pairs_paper.log')
STATE_FILE = os.path.expanduser('~/charon/bot_logs/pairs_paper_state.json')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

ex = ccxt.binance()
ex.load_markets()

state = {'cash': INITIAL_CAPITAL, 'position': None, 'trades': 0, 'pnl': 0.0, 'ratio_history': []}
if os.path.exists(STATE_FILE):
    try:
        state = json.load(open(STATE_FILE))
    except:
        pass

def fetch_klines(symbol, limit=100):
    return ex.fetch_ohlcv(symbol, TIMEFRAME, limit=limit)

log('=== 配对统计套利 启动 ===')
log(f'资金: ${INITIAL_CAPITAL}, Z阈值: {ZSCORE_ENTRY}σ')

loop = 0
while True:
    try:
        btc = fetch_klines('BTC/USDT', 100)
        eth = fetch_klines('ETH/USDT', 100)
        if not btc or not eth: continue
        
        btc_p = btc[-1][4]
        eth_p = eth[-1][4]
        ratio = btc_p / eth_p
        
        # 维护滚动窗口
        state['ratio_history'].append(ratio)
        if len(state['ratio_history']) > 100:
            state['ratio_history'] = state['ratio_history'][-100:]
        
        pos = state.get('position')
        in_pos = pos is not None
        
        if len(state['ratio_history']) >= 30:
            mean = statistics.mean(state['ratio_history'])
            std = statistics.stdev(state['ratio_history']) if len(state['ratio_history']) > 1 else 0.001
            z = (ratio - mean) / std if std > 0 else 0
            
            if in_pos:
                # 检查退出条件
                pnl = 0
                entry_ratio = pos['entry_ratio']
                side = pos['side']
                
                # 当前盈亏 (用比率回归估算)
                if side == 'short_btc':
                    pnl = (entry_ratio - ratio) / entry_ratio * pos['capital']
                else:
                    pnl = (ratio - entry_ratio) / entry_ratio * pos['capital']
                
                if abs(z) < ZSCORE_EXIT:
                    state['cash'] += pos['capital'] + pnl
                    state['pnl'] += pnl
                    state['trades'] += 1
                    log(f'[CLOSE] 比率回归 z={z:.2f} PnL=${pnl:.2f}')
                    state['position'] = None
            else:
                # 检查入场条件
                if z > ZSCORE_ENTRY:
                    # BTC太贵, ETH便宜 → 做空BTC/做多ETH
                    capital = state['cash'] * 0.3
                    state['cash'] -= capital
                    state['position'] = {'side': 'short_btc', 'entry_ratio': ratio, 'capital': capital, 'time': time.time()}
                    log(f'[OPEN] BTC做空/ETH做多 @ 比率={ratio:.2f} σ={z:.2f}')
                elif z < -ZSCORE_ENTRY:
                    # ETH太贵, BTC便宜 → 做多BTC/做空ETH
                    capital = state['cash'] * 0.3
                    state['cash'] -= capital
                    state['position'] = {'side': 'long_btc', 'entry_ratio': ratio, 'capital': capital, 'time': time.time()}
                    log(f'[OPEN] BTC做多/ETH做空 @ 比率={ratio:.2f} σ={z:.2f}')
        
        # 计算总权益
        equity = state['cash']
        pos = state.get('position')
        if pos:
            current_ratio = btc_p / eth_p
            if pos['side'] == 'short_btc':
                equity += pos['capital'] + (pos['entry_ratio'] - current_ratio) / pos['entry_ratio'] * pos['capital']
            else:
                equity += pos['capital'] + (current_ratio - pos['entry_ratio']) / pos['entry_ratio'] * pos['capital']
        
        loop += 1
        if loop % 24 == 0:
            pos_side = state.get("position",{}).get("side","无")
            log(f'[STATUS] 权益=${equity:.2f} 现金=${state["cash"]:.2f} 持仓={pos_side} 交易={state["trades"]} PnL=${state["pnl"]:.2f}')
        
        json.dump(state, open(STATE_FILE, 'w'))
        time.sleep(300)
        
    except Exception as e:
        log(f'[ERROR] {e}')
        time.sleep(60)
