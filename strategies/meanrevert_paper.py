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
LEVERAGE = 1            # 现货无杠杆
DAILY_LOSS_LIMIT = 5.0  # 日亏$5熔断(模拟$50本金的10%)
TAKER_FEE = 0.0004      # Binance合约taker费率 0.04%

import ccxt, json, time, os
from datetime import datetime
import statistics
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared_config import load_strategy_params, get_risk_limits, get_regime

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
         'daily_pnl': 0.0, 'daily_date': '', 'funding_collected': 0.0, 'fees_paid': 0.0}
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
        # 热加载GPT参数
        gp = load_strategy_params('meanrevert_paper')
        if gp:
            RSI_OVERSOLD = gp.get('rsi_oversold', RSI_OVERSOLD)
            RSI_OVERBOUGHT = gp.get('rsi_overbought', RSI_OVERBOUGHT)
            POSITION_PCT = gp.get('position_pct', POSITION_PCT)
            hl_atr = gp.get('stop_loss_atr', 1.5)
        
        # 读取周期方向锁
        regime = get_regime()
        # bearish→只做空, bullish→只做多, sideways→双向
        direction_lock = 'short' if regime == 'bearish' else ('long' if regime == 'bullish' else None)
        log(f'[REGIME] {regime} | 方向锁={"无限制" if not direction_lock else direction_lock.upper()}')
        
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
            
            # ======== 强制平仓 (GPT指令) ========
            if gp and gp.get('action') == 'close':
                fee = qty * price * TAKER_FEE
                state['fees_paid'] = state.get('fees_paid', 0) + fee
                state['cash'] += (qty * price if side == 'long' else qty * (2*entry - price)) - fee
                state['pnl'] += pnl - fee
                state['daily_pnl'] += pnl - fee
                state['trades'] += 1
                log(f'[FORCE_CLOSE] {SYMBOL} {side.upper()} @ {price:.2f} PnL=${pnl:.2f} (x{LEVERAGE})')
                state['position'] = None
                continue
            
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
            
            # ATR止损 (从热配置读取)
            sl_dist = atr * hl_atr
            if (side == 'long' and price < entry - sl_dist) or (side == 'short' and price > entry + sl_dist):
                # 手续费
                fee = qty * price * TAKER_FEE
                state['fees_paid'] = state.get('fees_paid', 0) + fee
                state['cash'] += (qty * price if side == 'long' else qty * (2*entry - price)) - fee
                state['pnl'] += pnl - fee
                state['daily_pnl'] += pnl - fee
                state['trades'] += 1
                log(f'[SL] {SYMBOL} {side.upper()} @ {price:.2f} PnL=${pnl:.2f} (x{LEVERAGE})')
                state['position'] = None
                continue
            
            # 回归到均线就止盈
            if (side == 'long' and price >= ma) or (side == 'short' and price <= ma):
                fee = qty * price * TAKER_FEE
                state['fees_paid'] = state.get('fees_paid', 0) + fee
                state['cash'] += (qty * price if side == 'long' else qty * (2*entry - price)) - fee
                state['pnl'] += pnl - fee
                state['daily_pnl'] += pnl - fee
                state['trades'] += 1
                log(f'[TP] {SYMBOL} {side.upper()} @ {price:.2f} (回归均线) PnL=${pnl:.2f} (x{LEVERAGE})')
                state['position'] = None
                continue
        
        # 开仓信号（受方向锁约束）
        if not in_pos and lower is not None:
            # 做多: 触BB下轨 + RSI超卖 (方向锁不能是short才做多)
            if (direction_lock != 'short') and price <= lower * 1.005 and rsi < RSI_OVERSOLD:
                qty = state['cash'] * POSITION_PCT / price
                cost = qty * price
                fee = cost * TAKER_FEE  # 开仓手续费
                state['fees_paid'] = state.get('fees_paid', 0) + fee
                state['cash'] -= cost + fee
                state['position'] = {'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()}
                log(f'[OPEN] LONG {SYMBOL} {qty:.4f} @ {price:.2f} RSI={rsi:.1f} BB下轨={lower:.2f} 手续费=${fee:.4f}')
            
            # 做空: 触BB上轨 + RSI超买 (方向锁不能是long才做空)
            if (direction_lock != 'long') and price >= upper * 0.995 and rsi > RSI_OVERBOUGHT:
                qty = state['cash'] * POSITION_PCT / price
                cost = qty * price
                fee = cost * TAKER_FEE
                state['fees_paid'] = state.get('fees_paid', 0) + fee
                state['cash'] -= cost + fee
                state['position'] = {'entry': price, 'qty': qty, 'side': 'short', 'time': time.time()}
                log(f'[OPEN] SHORT {SYMBOL} {qty:.4f} @ {price:.2f} RSI={rsi:.1f} BB上轨={upper:.2f}')
        
        # 总权益 (现货模式: 无杠杆, 简单持仓价值)
        equity = state['cash']
        pos = state.get('position')
        if pos:
            if pos['side'] == 'long':
                equity += pos['qty'] * price
            else:
                equity += pos['qty'] * (2*pos['entry'] - price)
        
        loop += 1
        if loop % 12 == 0:
            pos_side = state.get("position",{}).get("side","无")
            c, t, fc = state["cash"], state["trades"], state.get("fees_paid", 0)
            dpnl = state.get('daily_pnl', 0)
            log(f'[STATUS] 权益=${equity:.2f} 现金=${c:.2f} 持仓={pos_side} RSI={rsi:.1f} '
                f'交易={t} PnL=${state["pnl"]:.2f} 日亏=${dpnl:.2f} 手续费=${fc:.4f}')
        
        json.dump(state, open(STATE_FILE, 'w'))
        time.sleep(300)
        
    except Exception as e:
        log(f'[ERROR] {e}')
        time.sleep(60)
