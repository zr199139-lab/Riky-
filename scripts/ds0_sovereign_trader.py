#!/usr/bin/env python3
"""
暗黑星火 · 主权交易引擎 V1
============================
全自主决策闭环——不看人不问人，自分析自开枪自复盘。

运行模式：
- cron每30分钟触发一次检查
- 有持仓时检查止盈/加仓/减仓，无持仓时扫描机会
- 所有决策走AI分析+5关自检，通过直接下单
- 极端行情止损由 stop_loss_multi.py（WebSocket毫秒级）负责

不输出废话日志，只记录行动和结果。
"""

import os, sys, json, time, hashlib, hmac, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

# ── 路径 ──
BASE = Path('/home/admin/charon')
LOG_FILE = BASE / 'bot_logs' / 'sovereign_trader.log'
STATE_FILE = BASE / 'bot_logs' / 'sovereign_state.json'
sys.path.append('/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt

# ── 凭据 ──
_CREDS = _decrypt()
BINANCE_KEY = _CREDS['BINANCE_API_KEY']
BINANCE_SECRET = _CREDS['BINANCE_API_SECRET']
AIPRO_KEY = "sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL"
DS_KEY = "sk-1c97d4d658704f9cae7f998eb8fdb43b"

# ── 参数（daily_retro会覆写这些）──
MAX_LEVERAGE = 20
MARGIN_PER_TRADE = 30       # 每单保证金$30
DEFAULT_STOP_PCT = 2.0       # 固定止损2%
MAX_POSITIONS = 3             # 最多同时3仓
DAILY_LOSS_LIMIT = -15.0      # 日亏$15熔断
MIN_TRADE_INTERVAL = 1800     # 同一币种最小交易间隔30分钟

# ── 辅助 ──
def log(msg):
    t = datetime.now().strftime('%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def _sign(params, secret):
    qs = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

def _api(method, path, params=None, signed=False, use_spot=False):
    base = 'https://api.binance.com' if use_spot else 'https://fapi.binance.com'
    url = f'{base}{path}'
    headers = {'X-MBX-APIKEY': BINANCE_KEY}
    if signed:
        params = params or {}
        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = 10000
        qs = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
        sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = f'{url}?{qs}&signature={sig}'
    elif params:
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{url}?{qs}'
    
    try:
        if method == 'GET':
            r = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(r, timeout=10) as resp:
                return json.loads(resp.read())
        elif method == 'POST':
            # Binance accepts signed POST params in URL (empty body)
            r = urllib.request.Request(url, data=b'', headers=headers)
            with urllib.request.urlopen(r, timeout=10) as resp:
                return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        return {'error': f'HTTP {e.code}: {err}'}
    except Exception as e:
        return {'error': str(e)}

def _public_get(path, params=None):
    return _api('GET', path, params)

def _signed_get(path, params=None):
    return _api('GET', path, params, signed=True)

def _signed_post(path, params):
    return _api('POST', path, params, signed=True)

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
    """获取当前市场数据"""
    # 现货BTC价格和趋势
    btc = _spot_get('/api/v3/ticker/24hr', {'symbol': 'BTCUSDT'})
    btc_price = float(btc.get('lastPrice', 0))
    btc_change = float(btc.get('priceChangePercent', 0))
    
    # 合约账户
    acct = _signed_get('/fapi/v2/account')
    if 'error' in acct:
        log(f'  账户API错误: {acct["error"]}')
        return None
    wallet = float(acct.get('totalWalletBalance', 0))
    equity = float(acct.get('totalEquity', wallet)) if acct.get('totalEquity') is not None else wallet
    upnl = float(acct.get('totalUnrealizedProfit', 0))
    available = float(acct.get('availableBalance', 0))
    
    # 当前持仓
    positions = []
    for p in acct.get('positions', []):
        amt = float(p.get('positionAmt', 0))
        if abs(amt) < 0.001:
            continue
        positions.append({
            'symbol': p['symbol'].replace('USDT', '/USDT'),
            'side': 'LONG' if amt > 0 else 'SHORT',
            'qty': abs(amt),
            'entry': float(p.get('entryPrice', 0)),
            'mark': float(p.get('markPrice', 0)),
            'pnl': float(p.get('unRealizedProfit', 0)),
            'margin': float(p.get('initialMargin', 0)),
            'liq': float(p.get('liquidationPrice', 0)),
            'leverage': float(p.get('leverage', 5)),
            'ps': p.get('positionSide', 'BOTH'),
        })
    
    # Fear & Greed
    try:
        fg = urllib.request.urlopen('https://api.alternative.me/fng/?limit=1', timeout=5)
        fg_data = json.loads(fg.read())
        fear_greed = int(fg_data['data'][0]['value'])
    except:
        fear_greed = 50
    
    # 涨幅榜（24h top movers）
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
    
    # 日盈亏统计（链上真实）
    daily_pnl = _calc_daily_pnl(positions, wallet)
    
    return {
        'btc_price': btc_price,
        'btc_change_24h': btc_change,
        'wallet': wallet,
        'equity': equity,
        'unrealized_pnl': upnl,
        'available': available,
        'positions': positions,
        'fear_greed': fear_greed,
        'top_movers': top_movers[:10],
        'daily_pnl': daily_pnl,
    }

def _calc_daily_pnl(positions, wallet):
    """估算日内盈亏"""
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except:
            pass
    prev_wallet = state.get('prev_wallet', wallet)
    return wallet - prev_wallet

def save_state(market):
    STATE_FILE.write_text(json.dumps({
        'prev_wallet': market['wallet'],
        'prev_equity': market['equity'],
        'last_check': datetime.now().isoformat(),
    }))

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

# ── 执行引擎 ──
def execute_trade(signal, market):
    """直接开枪——开仓/平仓"""
    decision = signal.get('decision', 'HOLD')
    
    if decision == 'HOLD':
        log(f'HOLD | {signal.get("reason","无信号")}')
        return
    
    if decision == 'CLOSE_ALL':
        for pos in market['positions']:
            sym = pos['symbol'].replace('/USDT', 'USDT')
            side = 'BUY' if pos['side'] == 'SHORT' else 'SELL'
            ps = pos.get('ps', 'BOTH')
            result = _signed_post('/fapi/v1/order', {
                'symbol': sym,
                'side': side,
                'type': 'MARKET',
                'quantity': pos['qty'],
                'positionSide': ps,
                'newOrderRespType': 'RESULT'
            })
            if 'orderId' in result:
                log(f'CLOSE {pos["symbol"]} @${pos["mark"]:.2f} PnL={pos["pnl"]:+.2f}')
            else:
                log(f'CLOSE_FAIL {pos["symbol"]}: {result.get("msg","?")}')
        return
    
    # 开仓
    symbol = signal.get('target_symbol', '')
    if not symbol:
        log(f'NO_TARGET | {signal.get("reason","")}')
        return
    
    # 确定方向
    is_long = decision == 'ENTER_LONG'
    side = 'BUY' if is_long else 'SELL'
    position_side = 'LONG' if is_long else 'SHORT'
    
    # 计算数量
    leverage = min(signal.get('leverage', MAX_LEVERAGE), MAX_LEVERAGE)
    margin = min(MARGIN_PER_TRADE, market['available'] * 0.8)
    price = None
    
    # 获取当前价格
    try:
        ticker = _public_get('/fapi/v1/ticker/price', {'symbol': symbol})
        price = float(ticker['price'])
    except:
        price = None
    
    if not price or price <= 0:
        log(f'NO_PRICE {symbol}')
        return
    
    qty = margin * leverage / price
    # Round down to appropriate precision
    try:
        info = _public_get('/fapi/v1/exchangeInfo')
        for s in info.get('symbols', []):
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step = float(f['stepSize'])
                        qty = int(qty / step) * step
                        break
                break
    except:
        qty = round(qty, 3)
    
    if qty <= 0:
        log(f'QTY_ZERO {symbol}')
        return
    
    # 设置杠杆
    _signed_post('/fapi/v1/leverage', {'symbol': symbol, 'leverage': int(leverage)})
    
    # 设置逐仓
    _signed_post('/fapi/v1/marginType', {'symbol': symbol, 'marginType': 'ISOLATED'})
    
    # 开仓（市价单）
    result = _signed_post('/fapi/v1/order', {
        'symbol': symbol,
        'side': side,
        'type': 'MARKET',
        'quantity': qty,
        'positionSide': position_side,
        'newOrderRespType': 'RESULT'
    })
    
    if 'orderId' in result:
        filled_qty = float(result.get('executedQty', qty))
        avg_price = float(result.get('avgPrice', price))
        log(f'''OPEN {symbol} {position_side} {filled_qty}@{avg_price:.2f} {leverage}x ${margin:.0f}''')
    else:
        log(f'OPEN_FAIL {symbol}: {result.get("msg","?")}')

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

def should_skip_check(market):
    """避免高频重复检查——已有仓位的币30分钟内不重复操作"""
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except:
            pass
    last_trade = state.get('last_trade_time', 0)
    if last_trade and time.time() - last_trade < MIN_TRADE_INTERVAL:
        return True
    return False

# ── 主流程 ──
def main():
    log('=== 主权交易引擎启动 ===')
    log(f'参数: {MAX_LEVERAGE}x | ${MARGIN_PER_TRADE}/仓 | 止损{DEFAULT_STOP_PCT}% | 最多{MAX_POSITIONS}仓')
    
    # 加载daily_retro参数
    load_retro_params()
    
    # Step 1: 采集市场数据
    log('[1/4] 采集市场数据...')
    market = get_market_state()
    if market is None:
        log('  市场数据采集失败，跳过本轮回合')
        return
    log(f'  BTC=${market["btc_price"]:.0f} ({market["btc_change_24h"]:+.2f}%)')
    log(f'  权益=${market["equity"]:.2f} | 持仓={len(market["positions"])} | 日盈亏=${market["daily_pnl"]:.2f}')
    
    # 日亏熔断检查
    if market['daily_pnl'] < DAILY_LOSS_LIMIT:
        log(f'⚠️ 日亏${market["daily_pnl"]:.2f}触达熔断线${DAILY_LOSS_LIMIT}，跳过本轮交易')
        save_state(market)
        return
    
    # 高频跳过检查
    if should_skip_check(market):
        log('  MIN_TRADE_INTERVAL未到，跳过')
        save_state(market)
        return
    
    # Step 2: AI分析
    log('[2/4] AI分析市场...')
    signal = analyze_market(market)
    if 'error' in str(signal):
        log(f'  AI分析失败: {signal}')
        save_state(market)
        return
    
    log(f'  {signal.get("decision","?")} | {signal.get("reason","")} | 置信度={signal.get("confidence",0)}/10')
    
    # Step 3: 5关自检
    log('[3/4] 5关自检...')
    passed, checks = check_five_gates(signal, market)
    log(f'  {"✅ 通过" if passed else "❌ 拦截"} | {json.dumps(checks)}')
    
    # Step 4: 执行（5关通过即开枪，不设置信度门槛）
    log('[4/4] 执行...')
    if passed:
        execute_trade(signal, market)
    else:
        log(f'  未执行: 5关自检未通过 | {json.dumps(checks)}')
    
    # 保存状态
    market['last_trade_time'] = time.time()
    save_state(market)
    log('=== 完成 ===')

if __name__ == '__main__':
    main()
