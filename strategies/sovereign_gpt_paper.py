#!/usr/bin/env python3
"""
主权AI虚拟盘 · 全托管GPT驱动
================================
资金: $500
全权决策: GPT-5.5 (不请示用户)
流程: 三层数据 → GPT-5.5决策 → 执行 → 持仓监控 → 止盈止损 → 汇报
"""
import ccxt, json, time, os, sys, requests, numpy as np
from datetime import datetime

INITIAL_CASH = 500.0
LEVERAGE = 3
STATE_FILE = os.path.expanduser('~/charon/bot_logs/sovereign_gpt_state.json')
LOG_FILE = os.path.expanduser('~/charon/bot_logs/sovereign_gpt.log')
TAKER_FEE = 0.0004

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    t = datetime.now().strftime('%m-%d %H:%M')
    line = f'[{t}] {msg}'
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')
    print(line, flush=True)

# ── API配置 ──────────────────────────────────────────────
GPT_KEY = 'sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL'
GPT_URL = 'https://vip.aipro.love/v1/chat/completions'

# ── 交易所 ────────────────────────────────────────────────
ex = ccxt.binance({'enableRateLimit': True})
ex.load_markets()

# ── 状态 ──────────────────────────────────────────────────
state = {'cash': INITIAL_CASH, 'position': None, 'trades': 0, 'pnl': 0.0, 'fees': 0.0, 'equity_curve': []}
if os.path.exists(STATE_FILE):
    try: state = json.load(open(STATE_FILE))
    except: pass

# ── 数据采集 ──────────────────────────────────────────────
def get_price(symbol):
    try:
        t = ex.fetch_ticker(symbol)
        return float(t['last'])
    except:
        return None

def get_klines(symbol, tf='1h', limit=30):
    try:
        k = ex.fetch_ohlcv(symbol, tf, limit=limit)
        return {
            'closes': [float(x[4]) for x in k],
            'highs': [float(x[2]) for x in k],
            'lows': [float(x[3]) for x in k],
            'volumes': [float(x[5]) for x in k],
        }
    except:
        return None

def calc_rsi(closes, period=14):
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

def get_funding(symbol):
    try:
        s = symbol.replace('/', '')
        r = requests.get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}', timeout=5)
        return float(r.json().get('lastFundingRate', 0)) * 100
    except:
        return 0.0

def get_longshort(symbol):
    try:
        r = requests.get(
            f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1',
            timeout=5)
        d = r.json()[-1]
        return {
            'ratio': float(d['longShortRatio']),
            'long_pct': int(float(d['longAccount']) * 100),
            'short_pct': int(float(d['shortAccount']) * 100)
        }
    except:
        return {'ratio': 1.0, 'long_pct': 50, 'short_pct': 50}

def get_fng():
    try:
        r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        d = r.json()['data'][0]
        return int(d['value']), d['value_classification']
    except:
        return 50, 'Neutral'

# ── 数据汇总 ─────────────────────────────────────────────
def collect_data(symbols=['BTC/USDT', 'ETH/USDT']):
    data = {}
    for sym in symbols:
        k1h = get_klines(sym, '1h', 30)
        k4h = get_klines(sym, '4h', 30)
        k1d = get_klines(sym, '1d', 14)
        price = get_price(sym)
        
        closes_1h = k1h['closes'] if k1h else []
        closes_1d = k1d['closes'] if k1d else []
        
        data[sym] = {
            'price': price,
            'rsi_1h': calc_rsi(closes_1h) if closes_1h else 50,
            'rsi_4h': calc_rsi(k4h['closes']) if k4h else 50,
            'rsi_1d': calc_rsi(closes_1d) if closes_1d else 50,
            'high_20_1h': max(k1h['highs'][-20:]) if k1h and len(k1h['highs']) >= 20 else price,
            'low_20_1h': min(k1h['lows'][-20:]) if k1h and len(k1h['lows']) >= 20 else price,
            'high_20_4h': max(k4h['highs'][-20:]) if k4h and len(k4h['highs']) >= 20 else price,
            'low_20_4h': min(k4h['lows'][-20:]) if k4h and len(k4h['lows']) >= 20 else price,
            'funding': get_funding(sym),
        }
        
        # 聪明钱
        s = sym.replace('/', '')
        ls = get_longshort(s)
        data[sym]['ls_ratio'] = ls['ratio']
        data[sym]['ls_long_pct'] = ls['long_pct']
        data[sym]['ls_short_pct'] = ls['short_pct']
    
    fng_val, fng_class = get_fng()
    data['fng'] = {'value': fng_val, 'class': fng_class}
    return data

# ── GPT决策 ───────────────────────────────────────────────
def gpt_decide(data, position=None):
    pos_ctx = ''
    if position:
        elapsed = (time.time() - position.get('open_time', time.time())) / 3600
        pos_ctx = f"""
【当前持仓】
  币种: {position['symbol']}
  方向: {position['side']}
  入场价: ${position['entry']:.4f}
  当前价: ${data.get(position['symbol'], {}).get('price', 0):.4f}
  已持仓: {elapsed:.1f}小时
  浮盈: ${position.get('unrealized_pnl', 0):.2f}
  止损: ${position.get('stop', 0):.4f}
  止盈: ${position.get('tp', 0):.4f}
"""

    prompt = f"""你是专业加密货币交易员。以下是实时市场数据：

【市场情绪】
恐惧贪婪: {data['fng']['value']}/100 ({data['fng']['class']})

【BTC】
  现价: ${data['BTC/USDT']['price']:,.0f}
  RSI: 1h={data['BTC/USDT']['rsi_1h']:.1f} | 4h={data['BTC/USDT']['rsi_4h']:.1f} | 1d={data['BTC/USDT']['rsi_1d']:.1f}
  20日高: ${data['BTC/USDT']['high_20_1h']:,.0f} | 20日低: ${data['BTC/USDT']['low_20_1h']:,.0f}
  资金费率: {data['BTC/USDT']['funding']:+.3f}%
  多空比: {data['BTC/USDT']['ls_ratio']:.2f} (多{data['BTC/USDT']['ls_long_pct']}% 空{data['BTC/USDT']['ls_short_pct']}%)

【ETH】
  现价: ${data['ETH/USDT']['price']:.2f}
  RSI: 1h={data['ETH/USDT']['rsi_1h']:.1f} | 4h={data['ETH/USDT']['rsi_4h']:.1f} | 1d={data['ETH/USDT']['rsi_1d']:.1f}
  20日高: ${data['ETH/USDT']['high_20_4h']:.2f} | 20日低: ${data['ETH/USDT']['low_20_4h']:.2f}
  资金费率: {data['ETH/USDT']['funding']:+.3f}%
  多空比: {data['ETH/USDT']['ls_ratio']:.2f} (多{data['ETH/USDT']['ls_long_pct']}% 空{data['ETH/USDT']['ls_short_pct']}%)

{pos_ctx}
【资金】
现金: ${state['cash']:.2f} | 已交易: {state['trades']}笔 | 总PnL: ${state['pnl']:.2f}

你的任务是决定现在做什么。返回JSON（不要其他内容）:
{{"action": "long|short|close|hold", "symbol": "BTC/USDT或ETH/USDT", "entry_zone": "具体价格区间", "stop_loss": 价格, "take_profit": 价格, "leverage": 数字, "reason": "一句话原因"}}

规则：
- 有持仓且价格到止损或止盈 → close
- 有持仓但方向仍然有效 → hold
- 无持仓且有明确机会 → long或short
- 无明确机会 → hold
- 方向冲突时(funding和RSI矛盾) → hold
- 多空比>2.5且和你的方向一致 → 否决
"""

    try:
        r = requests.post(GPT_URL, json={
            'model': 'gpt-5.5',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 300,
            'temperature': 0.2
        }, headers={
            'Authorization': f'Bearer {GPT_KEY}',
            'Content-Type': 'application/json'
        }, timeout=30)
        
        if r.status_code == 200:
            content = r.json()['choices'][0]['message']['content']
            # 提取JSON
            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(content[start:end])
        return {'action': 'hold', 'reason': 'GPT error'}
    except Exception as e:
        return {'action': 'hold', 'reason': f'API error: {e}'}

# ── 安全校验 ─────────────────────────────────────────────
def safe_position(entry, stop, tp, leverage):
    """校验仓位安全性"""
    sl_dist = abs(entry - stop) / entry
    # 强平距估算
    liq_dist = 0.10 / leverage  # 10x→5%, 3x→3.3%
    
    if liq_dist < sl_dist * 1.5:
        return False, f'强平距不足(需>{sl_dist*1.5*100:.1f}%)'
    if sl_dist > 0.08:
        return False, f'止损太宽({sl_dist*100:.1f}%)'
    return True, 'OK'

def calc_pnl(entry, current, side, leverage, margin):
    if side == 'long':
        return (current - entry) / entry * leverage * margin
    else:
        return (entry - current) / entry * leverage * margin

# ── 保存状态 ─────────────────────────────────────────────
def save():
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# ── 主循环 ────────────────────────────────────────────────
log(f'=== 主权GPT虚拟盘 启动 | 资金${INITIAL_CASH} | 3x杠杆 ===')

while True:
    try:
        # 采集数据
        data = collect_data()
        log(f"数据: BTC${data['BTC/USDT']['price']:,.0f} RSI_1d={data['BTC/USDT']['rsi_1d']:.1f} | ETH${data['ETH/USDT']['price']:.2f} RSI_1d={data['ETH/USDT']['rsi_1d']:.1f}")
        
        # 检查持仓
        if state['position']:
            sym = state['position']['symbol']
            pos = state['position']
            current = data.get(sym, {}).get('price')
            
            if current:
                margin = state['cash'] * 0.25
                pos['unrealized_pnl'] = calc_pnl(pos['entry'], current, pos['side'], LEVERAGE, margin)
                
                log(f"持仓: {sym} {pos['side']} @{current:.4f} 浮${pos['unrealized_pnl']:.2f}")
                
                # 止损检查
                triggered = False
                action = ''
                pnl = 0
                
                if pos['side'] == 'long' and current <= pos['stop']:
                    action = '止损'
                    pnl = calc_pnl(pos['entry'], current, 'long', LEVERAGE, margin)
                    triggered = True
                elif pos['side'] == 'short' and current >= pos['stop']:
                    action = '止损'
                    pnl = calc_pnl(pos['entry'], current, 'short', LEVERAGE, margin)
                    triggered = True
                elif pos['side'] == 'long' and current >= pos['tp']:
                    action = '止盈'
                    pnl = calc_pnl(pos['entry'], current, 'long', LEVERAGE, margin)
                    triggered = True
                elif pos['side'] == 'short' and current <= pos['tp']:
                    action = '止盈'
                    pnl = calc_pnl(pos['entry'], current, 'short', LEVERAGE, margin)
                    triggered = True
                
                if triggered:
                    fee = margin * TAKER_FEE
                    state['cash'] += pnl - fee
                    state['pnl'] += pnl
                    state['fees'] += fee
                    state['trades'] += 1
                    log(f"✅ {action} | {sym} @{current:.4f} PnL=${pnl:.2f} 手续费${fee:.2f}")
                    log(f"   余额: ${state['cash']:.2f}")
                    state['position'] = None
                    save()
        
        # 无持仓 → GPT决策
        if not state['position']:
            decision = gpt_decide(data)
            log(f"GPT决策: {decision}")
            
            if decision.get('action') in ('long', 'short') and decision.get('symbol'):
                sym = decision['symbol']
                entry_price = data[sym]['price']
                stop = float(decision.get('stop_loss', entry_price * 0.97))
                tp = float(decision.get('take_profit', entry_price * 1.03))
                lev = int(decision.get('leverage', LEVERAGE))
                
                safe, msg = safe_position(entry_price, stop, tp, lev)
                if safe:
                    margin = state['cash'] * 0.25
                    state['position'] = {
                        'symbol': sym,
                        'side': decision['action'],
                        'entry': entry_price,
                        'stop': stop,
                        'tp': tp,
                        'leverage': lev,
                        'margin': margin,
                        'open_time': time.time(),
                        'unrealized_pnl': 0,
                        'reason': decision.get('reason', '')
                    }
                    save()
                    log(f"✅ 开仓 | {sym} {decision['action']} @{entry_price:.4f} 止损${stop:.4f} 目标${tp:.4f} {lev}x")
                else:
                    log(f"❌ 否决: {msg}")
            elif decision.get('action') == 'close' and state['position']:
                sym = state['position']['symbol']
                current = data[sym]['price']
                margin = state['cash'] * 0.25
                pnl = calc_pnl(state['position']['entry'], current, state['position']['side'], LEVERAGE, margin)
                fee = margin * TAKER_FEE
                state['cash'] += pnl - fee
                state['pnl'] += pnl
                state['fees'] += fee
                state['trades'] += 1
                log(f"✅ GPT平仓 | {sym} @{current:.4f} PnL=${pnl:.2f}")
                state['position'] = None
                save()
            else:
                log(f"🤚 GPT观望 | {decision.get('reason', '无信号')}")
        
        time.sleep(1800)  # 30分钟循环
        
    except Exception as e:
        import traceback
        log(f"异常: {e} | {traceback.format_exc()[-100:]}")
        time.sleep(300)
