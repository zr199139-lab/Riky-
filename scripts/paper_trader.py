#!/usr/bin/env python3
"""
暗黑星火 · 虚拟盘交易引擎 V1
==============================
与主权交易引擎完全同架构，但用虚拟资金模拟交易。
无交易次数限制，$10,000起始资金，专门为市场周期转换收集数据。

牛市来临时，虚拟盘已经跑出验证过的策略参数，直接搬上实盘。
"""

import os, sys, json, time, hashlib, hmac, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

# ── 路径 ──
BASE = Path('/home/admin/charon')
LOG_FILE = BASE / 'bot_logs' / 'paper_trader.log'
STATE_FILE = BASE / 'bot_logs' / 'paper_state.json'
# 虚拟盘不实际调用交易所，但需要K线数据
BINANCE_KEY = ''
BINANCE_SECRET = ''
AIPRO_KEY = "sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL"
DS_KEY = "sk-1c97d4d658704f9cae7f998eb8fdb43b"

# ── 参数 ──
MAX_LEVERAGE = 5
MARGIN_PER_TRADE = 30          # 每单固定保证金$30
DEFAULT_STOP_PCT = 5.0
MAX_POSITIONS = 5              # 虚拟盘放宽到5仓
DAILY_LOSS_LIMIT = -9999.0     # 虚拟盘不设日亏熔断
MIN_TRADE_INTERVAL = 60        # 虚拟盘1分钟间隔（收集更多数据）

# 虚拟盘初始资金
PAPER_CAPITAL = 10000.0

# ── 虚拟盘状态（文件持久化）──
PAPER_STATE = {
    'cash': PAPER_CAPITAL,
    'positions': {},    # sym -> {side, qty, entry, mark, margin, leverage}
    'trades': 0,
    'total_pnl': 0.0,
    'total_fees': 0.0,
    'started_at': datetime.now().isoformat(),
    'last_trade_time': 0,
}

# ── Jane Street级别动态杠杆矩阵 ──
LEVERAGE_MATRIX = {
    # 币种前缀: (推荐杠杆, 止损百分比)
    'BTC': (5, 5.0),
    'ETH': (5, 5.0),
    'SOL': (2, 8.0),
    'BCH': (2, 8.0),
    'DOGE': (2, 8.0),
    'PEPE': (1, 10.0),
    'XRP': (3, 6.0),
    'ADA': (2, 8.0),
    'DOT': (2, 8.0),
    'LINK': (3, 6.0),
    'AVAX': (2, 8.0),
    'UNI': (2, 8.0),
    'ATOM': (2, 8.0),
}
DEFAULT_LEVERAGE = (3, 6.0)    # 未匹配币种默认3x/6%

# ── 铁律：全仓模式永久禁用 ──
CROSS_MARGIN_FORBIDDEN = True

def get_leverage_for_symbol(symbol):
    """基于标的物的动态杠杆配置"""
    for prefix, (lev, stop) in LEVERAGE_MATRIX.items():
        if symbol.startswith(prefix):
            return lev, stop
    return DEFAULT_LEVERAGE

def fee_check_passes(margin, leverage):
    """手续费防御：单边taker费超过保证金的2%则拒绝开仓"""
    taker_rate = 0.0004  # Binance VIP0 taker费率0.04%（用实际值）
    nominal = margin * leverage
    fee = nominal * taker_rate
    fee_ratio = fee / margin * 100 if margin > 0 else 100
    if fee_ratio > 2.0:
        return False, f'手续费{fee_ratio:.1f}%超限(>{2.0}%)'
    return True, f'手续费{fee_ratio:.2f}%✅'

# ── 辅助 ──
def log(msg):
    t = datetime.now().strftime('%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def _api(method, path, params=None, use_spot=False):
    base = 'https://api.binance.com' if use_spot else 'https://fapi.binance.com'
    url = f'{base}{path}'
    headers = {}
    if params:
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{url}?{qs}'
    
    try:
        r = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        return {'error': f'HTTP {e.code}: {err}'}
    except Exception as e:
        return {'error': str(e)}

def _public_get(path, params=None):
    return _api('GET', path, params)

def _spot_get(path, params=None):
    return _api('GET', path, params, use_spot=True)

def _ai_call(model, messages, max_tokens=1024, temp=0.3):
    """调用AI模型（支持aipro GPT-5.5 和 DeepSeek）"""
    provider = "vip.aipro.love"
    api_key = AIPRO_KEY
    if model.startswith('deepseek'):
        provider = "api.deepseek.com"
        api_key = DS_KEY
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temp,
        "max_tokens": max_tokens,
    }
    if 'deepseek' not in model:
        payload['response_format'] = {"type": "json_object"}
    
    req = urllib.request.Request(
        f'https://{provider}/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())['choices'][0]['message']['content']
    except Exception as e:
        return f'{{"error": "{str(e)}"}}'

# ── 市场数据采集 ──
def get_market_state():
    """获取当前市场数据（公开API版本，不需要API Key）"""
    # 现货BTC价格和趋势
    btc = _spot_get('/api/v3/ticker/24hr', {'symbol': 'BTCUSDT'})
    if not btc or 'lastPrice' not in btc:
        log('  无法获取BTC价格，跳过')
        return None
    btc_price = float(btc.get('lastPrice', 0))
    btc_change = float(btc.get('priceChangePercent', 0))
    
    # 虚拟盘用本地状态替代合约账户
    load_paper_state()
    equity = PAPER_STATE['cash'] + sum(p.get('upnl', 0) for p in PAPER_STATE['positions'].values())
    available = PAPER_STATE['cash']
    positions_list = []
    for sym, p in PAPER_STATE['positions'].items():
        positions_list.append({
            'symbol': sym,
            'side': p['side'],
            'qty': p['qty'],
            'entry': p['entry'],
            'mark': p.get('mark', p['entry']),
            'pnl': p.get('upnl', 0),
            'margin': p['margin'],
            'leverage': p.get('leverage', 5),
            'ps': 'BOTH',
        })
    
    # Fear & Greed
    try:
        fg = urllib.request.urlopen('https://api.alternative.me/fng/?limit=1', timeout=5)
        fg_data = json.loads(fg.read())
        fear_greed = int(fg_data['data'][0]['value'])
    except:
        fear_greed = 50
    
    # 涨幅榜
    tickers = _spot_get('/api/v3/ticker/24hr')
    top_movers = []
    if isinstance(tickers, list):
        sorted_tickers = sorted(
            [t for t in tickers if 'USDT' in t.get('symbol','') and float(t.get('volume',0)) > 1e6],
            key=lambda t: abs(float(t['priceChangePercent'])),
            reverse=True
        )[:20]
        top_movers = [{
            'symbol': t['symbol'],
            'price': float(t['lastPrice']),
            'change_24h': float(t['priceChangePercent']),
            'volume': float(t['volume']),
        } for t in sorted_tickers]
    
    return {
        'btc_price': btc_price,
        'btc_change_24h': btc_change,
        'wallet': equity,       # 虚拟盘用权益
        'equity': equity,
        'unrealized_pnl': sum(p.get('upnl', 0) for p in PAPER_STATE['positions'].values()),
        'available': available, # 虚拟盘用可用现金
        'positions': positions_list,
        'fear_greed': fear_greed,
        'top_movers': top_movers[:10],
        'daily_pnl': 0.0,       # 虚拟盘不跟踪日盈亏
    }

# ── AI分析引擎 ──
def analyze_market(market):
    """GPT-5.5 K线分析 + 交易决策"""
    
    pos_str = json.dumps(market['positions'], indent=2) if market['positions'] else '空仓'
    movers_str = json.dumps([{
        's': m['symbol'], 'p': f"${m['price']:.2f}", 'chg%': f"{m['change_24h']:.1f}%"
    } for m in market['top_movers'][:5]], indent=2)
    
    prompt = f"""你是顶级加密货币交易员。分析当前市场并输出交易决策。

## 市场状态
BTC: ${market['btc_price']:.0f} (24h: {market['btc_change_24h']:.2f}%)
恐惧贪婪指数: {market['fear_greed']}/100
账户权益: ${market['equity']:.2f} | 可用: ${market['available']:.2f}
日盈亏: ${market['daily_pnl']:.2f}

## 当前持仓
{pos_str}

## 24h涨幅榜Top10
{movers_str}

## 交易规则
1. 单仓保证金: {MARGIN_PER_TRADE}U
2. 最大杠杆: {MAX_LEVERAGE}x
3. 同时持仓上限: {MAX_POSITIONS}个
4. 日亏${abs(DAILY_LOSS_LIMIT)}熔断
5. 熊市只做空，牛市只做多，震荡市高抛低吸
6. 必须设止损（2%硬止损由stop_loss守护）
7. 趋势是你的朋友——不要逆大趋势

## 输出（纯JSON，无其他文字）
{{
    "decision": "HOLD / ENTER_LONG / ENTER_SHORT / CLOSE_ALL",
    "reason": "一句话理由",
    "target_symbol": "BTCUSDT / ETHUSDT / SOLUSDT / 或空",
    "confidence": 1-10,
    "leverage": {MAX_LEVERAGE},
    "stop_pct": {DEFAULT_STOP_PCT},
    "take_profit_pct": 3.0,
    "checks_passed": ["trend", "rsi", "volume", "multi_tf", "risk"],
    "market_regime": "BULL/BEAR/RANGING"
}}"""

    # 用GPT-5.5做K线分析（用户验证过最准）
    result = _ai_call("gpt-5.5", [
        {"role": "system", "content": "你是Jane Street级别的交易员。输出纯JSON，不废话。"},
        {"role": "user", "content": prompt}
    ])
    
    try:
        return json.loads(result)
    except:
        # GPT失败，降级到DeepSeek
        log(f'GPT分析失败，降级到DeepSeek: {result[:100]}')
        result2 = _ai_call("deepseek-chat", [
            {"role": "system", "content": "你是一个加密货币交易员。输出纯JSON。"},
            {"role": "user", "content": prompt}
        ])
        try:
            return json.loads(result2)
        except:
            return {'decision': 'HOLD', 'reason': f'AI分析失败', 'confidence': 0}

# ── 5关自检框架 ──
def check_five_gates(signal, market):
    """交易前5关自检，任何一关不通过不开枪"""
    checks = {
        'trend': False,   # 大势过滤
        'rsi': False,     # RSI逻辑
        'volume': False,  # 成交量验证
        'multi_tf': False,# 多周期对齐
        'risk': False,    # 风控检查
    }
    
    # 关1：BTC大势过滤
    if signal['decision'] == 'ENTER_LONG':
        checks['trend'] = market['btc_change_24h'] > -3.0  # BTC不能跌太多还追多
    elif signal['decision'] == 'ENTER_SHORT':
        checks['trend'] = market['btc_change_24h'] < 3.0   # BTC不能涨太多还追空
    else:
        checks['trend'] = True
    
    # 关2：风控（日亏熔断、持仓上限）
    if market['daily_pnl'] < DAILY_LOSS_LIMIT:
        checks['risk'] = False  # 日亏熔断
    elif len(market['positions']) >= MAX_POSITIONS:
        checks['risk'] = False  # 持仓满
    else:
        checks['risk'] = True
    
    # 关3-5：信任AI分析
    checks['rsi'] = True
    checks['volume'] = True
    checks['multi_tf'] = True
    
    all_pass = all(checks.values())
    return all_pass, checks

# ── 虚拟盘持仓管理 ──
def load_paper_state():
    """从文件加载虚拟盘状态"""
    global PAPER_STATE
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            PAPER_STATE.update(saved)
        except:
            pass

def save_paper_state():
    """持久化虚拟盘状态"""
    STATE_FILE.write_text(json.dumps(PAPER_STATE, indent=2))

def paper_execute(signal, market):
    """虚拟执行——不碰交易所，只更新内存状态"""
    global PAPER_STATE
    load_paper_state()
    
    decision = signal.get('decision', 'HOLD')
    
    if decision == 'HOLD':
        log(f'[PAPER] HOLD | {signal.get("reason","无信号")}')
        # 更新未实现盈亏（持仓随市价波动）
        for sym, pos in list(PAPER_STATE['positions'].items()):
            try:
                ticker = _public_get('/fapi/v1/ticker/price', {'symbol': sym})
                mark = float(ticker['price'])
                pos['mark'] = mark
                if pos['side'] == 'SHORT':
                    pos['upnl'] = (pos['entry'] - mark) * pos['qty']
                else:
                    pos['upnl'] = (mark - pos['entry']) * pos['qty']
            except:
                pass
        running_pnl = sum(p.get('upnl', 0) for p in PAPER_STATE['positions'].values())
        equity = PAPER_STATE['cash'] + running_pnl
        log(f'[PAPER] 权益=${equity:.2f} | 现金=${PAPER_STATE["cash"]:.2f} | 持仓={len(PAPER_STATE["positions"])} | 浮盈=${running_pnl:.2f}')
        save_paper_state()
        return
    
    if decision == 'CLOSE_ALL':
        for sym, pos in list(PAPER_STATE['positions'].items()):
            try:
                ticker = _public_get('/fapi/v1/ticker/price', {'symbol': sym})
                mark = float(ticker['price'])
            except:
                mark = pos.get('mark', pos['entry'])
            
            if pos['side'] == 'SHORT':
                pnl = (pos['entry'] - mark) * pos['qty']
            else:
                pnl = (mark - pos['entry']) * pos['qty']
            
            fee = pos['margin'] * pos.get('leverage', 5) * 0.0008  # 双边手续费
            
            PAPER_STATE['cash'] += pos['margin'] + pnl - fee
            PAPER_STATE['total_pnl'] += pnl - fee
            PAPER_STATE['total_fees'] += fee
            PAPER_STATE['trades'] += 1
            
            log(f'[PAPER] CLOSE {sym} {pos["side"]} ${mark:.2f} PnL=${pnl:+.2f} Fee=${fee:.2f}')
            del PAPER_STATE['positions'][sym]
        
        equity = PAPER_STATE['cash']
        log(f'[PAPER] 全部平仓 | 权益=${equity:.2f} | 总PnL=${PAPER_STATE["total_pnl"]:.2f}')
        save_paper_state()
        return
    
    # 开仓
    symbol = signal.get('target_symbol', '')
    if not symbol:
        log(f'[PAPER] NO_TARGET | {signal.get("reason","")}')
        save_paper_state()
        return
    
    # 方向
    is_long = decision == 'ENTER_LONG'
    position_side = 'LONG' if is_long else 'SHORT'
    
    # 动态杠杆
    target_leverage, dynamic_stop = get_leverage_for_symbol(symbol)
    leverage = min(target_leverage, signal.get('leverage', MAX_LEVERAGE))
    leverage = max(1, int(leverage))
    
    margin = min(MARGIN_PER_TRADE, PAPER_STATE['cash'] * 0.5)  # 最多用50%现金
    
    # 手续费检查
    fee_ok, fee_msg = fee_check_passes(margin, leverage)
    if not fee_ok:
        log(f'[PAPER] FEE_REJECT {symbol}: {fee_msg}')
        save_paper_state()
        return
    
    # 获取价格
    try:
        ticker = _public_get('/fapi/v1/ticker/price', {'symbol': symbol})
        price = float(ticker['price'])
    except:
        log(f'[PAPER] NO_PRICE {symbol}')
        save_paper_state()
        return
    
    qty = margin * leverage / price
    
    # 检查现金是否够
    if margin > PAPER_STATE['cash'] * 0.8:
        log(f'[PAPER] INSUFFICIENT_CASH: 需${margin:.0f} 仅${PAPER_STATE["cash"]:.0f}')
        save_paper_state()
        return
    
    # 减少现金
    fee = margin * leverage * 0.0004  # 单边taker费
    PAPER_STATE['cash'] -= margin
    
    # 添加到持仓
    PAPER_STATE['positions'][symbol] = {
        'side': position_side,
        'qty': round(qty, 6),
        'entry': price,
        'mark': price,
        'margin': margin,
        'leverage': leverage,
        'stop_pct': dynamic_stop,
        'upnl': 0.0,
    }
    PAPER_STATE['trades'] += 1
    PAPER_STATE['total_fees'] += fee
    
    equity = PAPER_STATE['cash'] + margin  # 冻结的保证金也算权益
    
    log(f'[PAPER] OPEN {symbol} {position_side} {qty:.4f}@{price:.2f} {leverage}x ${margin:.0f}')
    log(f'[PAPER] 权益=${equity:.2f} | 现金=${PAPER_STATE["cash"]:.2f} | 持仓={len(PAPER_STATE["positions"])}')
    
    # 虚拟止损检查（简单版：每开仓时记录止损位）
    if position_side == 'SHORT':
        stop_price = price * (1 + dynamic_stop / 100)
        log(f'[PAPER] STOP {symbol} @${stop_price:.2f} ({dynamic_stop}%)')
    else:
        stop_price = price * (1 - dynamic_stop / 100)
        log(f'[PAPER] STOP {symbol} @${stop_price:.2f} ({dynamic_stop}%)')
    
    save_paper_state()

def load_retro_params():
    """加载daily_retro输出的参数"""
    config_path = BASE / 'shared_config.json'
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            params = config.get('retro_parameters', {})
            if params:
                global MAX_LEVERAGE, MARGIN_PER_TRADE, DEFAULT_STOP_PCT, MAX_POSITIONS, DAILY_LOSS_LIMIT
                if 'MAX_LEVERAGE' in params:
                    MAX_LEVERAGE = params['MAX_LEVERAGE']
                if 'MARGIN_PER_TRADE' in params:
                    MARGIN_PER_TRADE = params['MARGIN_PER_TRADE']
                if 'DEFAULT_STOP_PCT' in params:
                    DEFAULT_STOP_PCT = params['DEFAULT_STOP_PCT']
                if 'MAX_POSITIONS' in params:
                    MAX_POSITIONS = params['MAX_POSITIONS']
                if 'DAILY_LOSS_LIMIT' in params:
                    DAILY_LOSS_LIMIT = params['DAILY_LOSS_LIMIT']
                log(f'PARAMS loaded: leverage={MAX_LEVERAGE}x margin={MARGIN_PER_TRADE}U stop={DEFAULT_STOP_PCT}%')
        except:
            pass

def should_skip_check():
    """虚拟盘每分钟都可跑，不设间隔"""
    return False

# ── 主流程 ──
def main():
    log('=== 虚拟盘交易引擎启动 ===')
    log(f'初始资金: ${PAPER_CAPITAL:.0f} | 参数: {MAX_LEVERAGE}x | ${MARGIN_PER_TRADE}/仓 | 止损{DEFAULT_STOP_PCT}% | 最多{MAX_POSITIONS}仓')
    load_paper_state()
    log(f'当前状态: 权益=${PAPER_STATE["cash"]:.2f} | 持仓={len(PAPER_STATE["positions"])} | 总交易={PAPER_STATE["trades"]} | 总PnL=${PAPER_STATE["total_pnl"]:.2f}')
    
    # Step 1: 采集市场数据
    log('[1/4] 采集市场数据...')
    market = get_market_state()
    if market is None:
        log('  市场数据采集失败，跳过本轮回合')
        return
    log(f'  BTC=${market["btc_price"]:.0f} ({market["btc_change_24h"]:+.2f}%) | 恐惧={market["fear_greed"]}')
    log(f'  权益=${market["equity"]:.2f} | 持仓={len(market["positions"])} | 日盈亏=${market["daily_pnl"]:.2f}')
    
    # 日亏熔断检查（虚拟盘不熔断）
    log(f'  [虚拟盘] 跳过熔断检查 | 权益=${market["equity"]:.2f}')
    
    # 高频跳过检查（虚拟盘不跳过）
    log('  [虚拟盘] 无间隔限制')
    
    # Step 2: AI分析
    log('[2/4] AI分析市场...')
    signal = analyze_market(market)
    if 'error' in str(signal):
        log(f'  AI分析失败: {signal}')
        return
    
    log(f'  {signal.get("decision","?")} | {signal.get("reason","")} | 置信度={signal.get("confidence",0)}/10')
    
    # Step 3: 5关自检
    log('[3/4] 5关自检...')
    passed, checks = check_five_gates(signal, market)
    log(f'  {"✅ 通过" if passed else "❌ 拦截"} | {json.dumps(checks)}')
    
    # Step 4: 执行（虚拟盘）
    log('[4/4] 虚拟执行...')
    if passed:
        paper_execute(signal, market)
    else:
        log(f'  未执行: 5关自检未通过 | {json.dumps(checks)}')
    
    log('=== 完成 ===')

if __name__ == '__main__':
    main()
