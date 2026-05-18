#!/usr/bin/env python3
"""
暗黑星火 合约虚拟盘 · 熊市专用版
====================================
三模式集成：
  Mode 1: MACD死叉做空 (趋势跟踪)
  Mode 2: 波动率回归偏空 (BB+RSI放宽做空)
  Mode 3: 死猫反弹做空 (急跌+无量反弹)

虚拟杠杆 3-5x, 模拟合约保证金模式
资金费率监控 (当前≈0%, 极端时吃费率)
"""
import os, json, time, logging, numpy as np, ccxt
from datetime import datetime
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared_config import load_strategy_params, get_risk_limits

# ── 配置 ──
MODE = 1          # 1=MACD空头, 2=波动率偏空, 3=死猫反弹
SYMBOLS = ['ETH/USDT', 'BTC/USDT']  # 主币+备用
TIMEFRAME = '1h'   # 主信号周期
INITIAL_CAPITAL = 1000.0  # 虚拟本金
LEVERAGE = 5       # 虚拟杠杆 3-5x
MAX_POSITIONS = 1   # $50本金最多1仓
DAILY_LOSS_LIMIT = 5.0  # 日亏$5停机(按$50本金的10%)
INITIAL_CASH_FRAC = 0.4  # 每仓最多40%本金
TAKER_FEE = 0.0004  # Binance合约taker 0.04%

LOG_FILE = os.path.expanduser('~/charon/bot_logs/futures_paper.log')
STATE_FILE = os.path.expanduser('~/charon/bot_logs/futures_paper_state.json')
DAILY_FILE = os.path.expanduser('~/charon/bot_logs/futures_paper_daily.json')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [FUT] %(levelname)s %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
log = logging.getLogger('futures_paper')

# ── 初始化 ──
ex = ccxt.binance({'enableRateLimit': True})

state = {'cash': INITIAL_CAPITAL, 'position': None, 'trades': 0, 'pnl': 0.0,
         'daily_pnl': 0.0, 'daily_date': '', 'funding_collected': 0.0, 'fees_paid': 0.0}
if os.path.exists(STATE_FILE):
    try: state = json.load(open(STATE_FILE))
    except: pass

# ── 指标 ──
def ema(s, p):
    a = np.array(s, dtype=float); k = 2.0/(p+1); o = np.empty_like(a); o[0]=a[0]
    for i in range(1,len(a)): o[i]=a[i]*k+o[i-1]*(1-k)
    return o

def rsi(closes, p=14):
    a = np.array(closes, dtype=float)
    d = np.diff(a); g = np.maximum(d,0); l = np.maximum(-d,0)
    ag = np.mean(g[-p:]); al = np.mean(l[-p:])
    if al < 1e-10: return 100.0
    return 100.0 - 100.0/(1.0 + ag/al)

def atr(klines, p=14):
    highs = np.array([k[2] for k in klines[-p-1:]])
    lows = np.array([k[3] for k in klines[-p-1:]])
    closes = np.array([k[4] for k in klines[-p-1:]])
    tr = np.maximum(highs[1:]-lows[1:], np.maximum(abs(highs[1:]-closes[:-1]), abs(lows[1:]-closes[:-1])))
    return np.mean(tr)

# ── 市场数据 ──
def fetch_market(sym):
    klines = ex.fetch_ohlcv(sym, TIMEFRAME, limit=100)
    if not klines: return None
    price = klines[-1][4]
    closes = np.array([k[4] for k in klines], dtype=float)
    highs = np.array([k[2] for k in klines])
    lows = np.array([k[3] for k in klines])
    vols = np.array([k[5] for k in klines], dtype=float)
    atr_val = atr(klines)
    atr_pct = atr_val / price * 100 if price > 0 else 0
    rsi_val = rsi(closes)
    
    # 资金费率
    funding = 0.0
    try:
        fr = ex.fetch_funding_rate(sym.replace('/','')+':USDT')
        funding = float(fr['info']['lastFundingRate']) if 'lastFundingRate' in fr['info'] else 0
    except:
        pass
    
    return {'price': price, 'closes': closes, 'highs': highs, 'lows': lows,
            'vols': vols, 'atr': atr_val, 'atr_pct': atr_pct, 'rsi': rsi_val, 'funding': funding,
            'ema20': ema(closes,20)[-1], 'ema50': ema(closes,50)[-1]}

# ── 策略信号 ──
def signal_macd_short(m):
    """Mode 1: MACD做空趋势跟踪 (熊市版, 不要求新鲜死叉)"""
    macd = ema(m['closes'],12) - ema(m['closes'],26)
    sig = ema(macd, 9)
    if len(macd) < 2: return 0
    # 趋势过滤: EMA20 < EMA50 (空头趋势)
    if m['ema20'] >= m['ema50']: return 0
    # 空头趋势中 + MACD在SIG下方 → 持有空单
    if macd[-1] < sig[-1]:
        # 没有持仓 → 开空(持续空头,不是新鲜死叉也能开)
        return -1
    # MACD金叉 → 平空
    if macd[-1] > sig[-1]:
        return 1
    return 0

def signal_meanrev_short(m):
    """Mode 2: 波动率回归偏空版"""
    bb_period = 20; bb_std = 2.0
    window = m['closes'][-bb_period:]
    ma = np.mean(window); sd = np.std(window)
    upper = ma + bb_std * sd; lower = ma - bb_std * sd
    
    # 放宽做空: RSI>55 + 触布林上轨(不是70)
    if m['price'] >= upper * 0.995 and m['rsi'] > 55:
        return -1
    # 收紧做多: RSI<20 + 触布林下轨(不是30)
    if m['price'] <= lower * 1.005 and m['rsi'] < 20:
        return 1
    # 持仓止盈: 回到均线
    if m['price'] >= ma:
        return 1  # 空单回到均线平仓
    return 0

def signal_deadcat_short(m, prev_klines):
    """Mode 3: 死猫反弹做空"""
    # 需要前4小时K线
    if len(prev_klines) < 5: return 0
    prev_closes = [k[4] for k in prev_klines[-5:]]
    prev_vols = [k[5] for k in prev_klines[-20:]]
    avg_vol = np.mean(prev_vols) if prev_vols else 1
    
    # 4h跌幅 > 5%
    drop_4h = (prev_closes[0] - prev_closes[-1]) / prev_closes[0]
    # 当前反弹 > 1.5% 
    bounce = (m['price'] - prev_closes[-1]) / prev_closes[-1]
    # 成交量萎缩
    vol_ratio = m['vols'][-1] / avg_vol if avg_vol > 0 else 1
    
    if drop_4h > 0.05 and bounce > 0.015 and vol_ratio < 0.7:
        return -1  # 反弹无力, 做空
    return 0

# ── 主循环 ──
log.info(f"=== 合约虚拟盘 启动 | 本金=${INITIAL_CAPITAL} x{LEVERAGE} | 模式{MODE} | 币种={SYMBOLS} ===")

loop = 0
while True:
    try:
        # 热加载GPT参数
        gp = load_strategy_params('futures_paper')
        if gp:
            gp_lev = gp.get('leverage')
            if gp_lev: LEVERAGE = int(gp_lev)
            gp_pct = gp.get('position_pct')
            if gp_pct: INITIAL_CASH_FRAC = float(gp_pct)
            if gp.get('active') == False:
                log.info(f'[GPT] 策略暂停指令, 跳过本轮')
                time.sleep(300); continue
        
        today = datetime.now().strftime('%Y-%m-%d')
        # 日亏重置
        if state['daily_date'] != today:
            state['daily_pnl'] = 0.0
            state['daily_date'] = today
            # 日亏熔断检查
            if state['daily_pnl'] <= -DAILY_LOSS_LIMIT:
                log.info(f"[DAILY_LOSS] 日亏${state['daily_pnl']:.2f} 已超${DAILY_LOSS_LIMIT}熔断线, 本轮跳过")
                time.sleep(3600)
                continue
        
        for sym in SYMBOLS:
            m = fetch_market(sym)
            if not m: continue
            price = m['price']
            
            log.debug(f"[{sym}] ${price:.2f} RSI={m['rsi']:.1f} ATR={m['atr_pct']:.2f}% 费率={m['funding']*100:.4f}%")
            
            pos = state.get('position')
            in_pos = pos is not None and pos['symbol'] == sym
            
            # ── 策略选择 ──
            sig = 0
            if MODE == 1:
                sig = signal_macd_short(m)
            elif MODE == 2:
                sig = signal_meanrev_short(m)
            elif MODE == 3:
                # 需要前4hK线
                prev = ex.fetch_ohlcv(sym, TIMEFRAME, limit=30)
                sig = signal_deadcat_short(m, prev)
            
            # ── 持仓管理 ──
            if in_pos:
                entry = pos['entry']
                side = pos['side']  # 'short'
                qty = pos['qty']
                
                # 计算PnL (合约模式: 保证金 + 浮动盈亏)
                pnl_raw = (entry - price) * qty if side == 'short' else (price - entry) * qty
                pnl = pnl_raw * LEVERAGE
                
                # 止损: ATR×1.0 或 固定3%取小值
                stop_dist = min(m['atr'] * 1.0, entry * 0.03)
                if side == 'short':
                    stop_price = entry + stop_dist
                    if price >= stop_price:
                        # 平仓
                        fee = qty * price * TAKER_FEE
                        state['fees_paid'] = state.get('fees_paid', 0) + fee
                        margin_return = pos['margin'] + pnl
                        state['cash'] += margin_return - fee
                        state['pnl'] += pnl
                        state['daily_pnl'] += pnl
                        state['trades'] += 1
                        state['funding_collected'] += pos.get('funding_earned', 0)
                        log.info(f"[SL] {sym} SHORT @${price:.2f} (止损) PnL=${pnl:.2f} 手续费=${fee:.4f}")
                        state['position'] = None
                        continue
                    
                    # 止盈: ATR×2.0
                    tp_price = entry - m['atr'] * 2.0
                    if price <= tp_price:
                        fee = qty * price * TAKER_FEE
                        state['fees_paid'] = state.get('fees_paid', 0) + fee
                        margin_return = pos['margin'] + pnl
                        state['cash'] += margin_return - fee
                        state['pnl'] += pnl
                        state['daily_pnl'] += pnl
                        state['trades'] += 1
                        state['funding_collected'] += pos.get('funding_earned', 0)
                        log.info(f"[TP] {sym} SHORT @${price:.2f} PnL=${pnl:.2f} 手续费=${fee:.4f}")
                        state['position'] = None
                        continue
                    
                    # 信号反转平仓
                    if MODE == 1 and sig == 1:
                        fee = qty * price * TAKER_FEE
                        state['fees_paid'] = state.get('fees_paid', 0) + fee
                        margin_return = pos['margin'] + pnl
                        state['cash'] += margin_return - fee
                        state['pnl'] += pnl
                        state['daily_pnl'] += pnl
                        state['trades'] += 1
                        state['funding_collected'] += pos.get('funding_earned', 0)
                        log.info(f"[REVERSE] {sym} SHORT @${price:.2f} MACD金叉 PnL=${pnl:.2f} 手续费=${fee:.4f}")
                        state['position'] = None
                        continue
                else:
                    # long (熊市禁用, 但保留逻辑)
                    stop_price = entry - stop_dist
                    if price <= stop_price:
                        margin_return = pos['margin'] + pnl
                        state['cash'] += margin_return
                        state['pnl'] += pnl
                        state['trades'] += 1
                        log.info(f"[SL] {sym} LONG @${price:.2f} PnL=${pnl:.2f}")
                        state['position'] = None
                        continue
            
            # ── 开仓 ──
            if not in_pos and sig == -1 and len([p for p in [state.get('position')] if p]) < MAX_POSITIONS:
                # 费率检查: 如果费率>0.01%做空收钱, 正常开; 如果费率<0做空付钱, 跳过
                if m['funding'] < -0.0001:
                    log.info(f"[SKIP] {sym} 费率={m['funding']*100:.4f}% (做空付钱), 跳过")
                    continue
                
                # 仓位大小: 现金 × 40%
                margin = state['cash'] * INITIAL_CASH_FRAC
                if margin < 5:  # 最低$5保证金
                    continue
                qty = margin * LEVERAGE / price
                
                # 费率收益估算 (每8h结算一次, 按当前费率)
                funding_8h = margin * m['funding']  # 做空收正费率
                
                fee_open = qty * price * TAKER_FEE
                state['fees_paid'] = state.get('fees_paid', 0) + fee_open
                state['cash'] -= margin + fee_open
                state['position'] = {
                    'symbol': sym, 'entry': price, 'qty': qty, 'margin': margin,
                    'side': 'short', 'time': time.time(), 'funding_rate': m['funding'],
                    'funding_earned': 0
                }
                stop_dist_open = min(m['atr'] * 1.0, price * 0.03)
                log.info(f"[OPEN] {sym} SHORT {qty:.4f}@${price:.2f} margin=${margin:.2f}x{LEVERAGE} "
                         f"止损=+${stop_dist_open:.2f} 费率={m['funding']*100:.4f}%")
        
        # ── 资金费率采集 (每轮结算) ──
        if state.get('position'):
            pos = state['position']
            if pos['side'] == 'short' and pos['funding_rate'] > 0:
                # 做空收正费率, 每5分钟按比例计入
                funding_earned = pos['margin'] * pos['funding_rate'] * (300 / 28800)  # 5min占8h的比例
                state['funding_collected'] += funding_earned
                pos['funding_earned'] = pos.get('funding_earned', 0) + funding_earned
        
        # ── 权益计算 ──
        equity = state['cash']
        pos = state.get('position')
        if pos:
            try:
                t = ex.fetch_ticker(pos['symbol'])
                mp = t['last']
                if pos['side'] == 'short':
                    equity += pos['margin'] + (pos['entry'] - mp) * pos['qty'] * LEVERAGE
                else:
                    equity += pos['margin'] + (mp - pos['entry']) * pos['qty'] * LEVERAGE
            except:
                equity += pos['margin']
        
        # ── 状态日志 ──
        loop += 1
        if loop % 12 == 0:
            pos_side = state.get('position', {}).get('side', '无')
            pos_sym = state.get('position', {}).get('symbol', '')
            fr = state.get('position', {}).get('funding_rate', 0)
            log.info(f"[STATUS] 权益=${equity:.2f} | 现金=${state['cash']:.2f} | "
                     f"持仓={pos_side}@{pos_sym} | 交易={state['trades']}笔 | "
                     f"PnL=${state['pnl']:.2f} | 日亏=${state['daily_pnl']:.2f} | "
                     f"费率收入=${state['funding_collected']:.4f}")
        
        json.dump(state, open(STATE_FILE, 'w'))
        time.sleep(300)  # 5分钟一轮
        
    except Exception as e:
        log.error(f"[ERR] {e}")
        time.sleep(60)
