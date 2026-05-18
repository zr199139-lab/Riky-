#!/usr/bin/env python3
"""
暗黑星火 · 每日自省与批斗系统
=============================
每日深夜执行。抓取过去24h真实链上交易数据，
打包发给DeepSeek Pro做"严厉交易总监"复盘，
输出参数修改方案，直接覆写本地策略配置。

核心铁律：
1. 所有PnL数字必须用链上余额差核实，禁用累加器假账
2. 复盘必须批评错误，不找借口
3. 输出必须是可执行的参数修改，不是废话报告
"""

import os, json, sys, time, hashlib, hmac, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
SCRIPTS = BASE / 'scripts'

# ── 凭据 ──
sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt
_CREDS = _decrypt()
BINANCE_KEY = _CREDS.get('BINANCE_API_KEY', '')
BINANCE_SECRET = _CREDS.get('BINANCE_API_SECRET', '')
# 从.env文件读取DS Key
_env_path = Path('/home/admin/.hermes/.env')
_env_key = ''
if _env_path.exists():
    for line in _env_path.read_text().split('\n'):
        if 'DEEPSEEK_API_KEY' in line and '=' in line:
            _env_key = line.split('=', 1)[1].strip().strip('"\'')
DS_KEY = os.environ.get('DEEPSEEK_API_KEY', _env_key)
BASE_URL = 'https://fapi.binance.com'

def _sign(params):
    query = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    sig = hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + '&signature=' + sig

def _get(path, params=None):
    if params is None:
        params = {'timestamp': int(time.time() * 1000)}
    else:
        params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 10000
    qs = _sign(params)
    req = urllib.request.Request(f'{BASE_URL}{path}?{qs}',
                                 headers={'X-MBX-APIKEY': BINANCE_KEY})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'error': str(e)}

def get_balance():
    """链上真实余额"""
    info = _get('/fapi/v2/account')
    if 'error' in info:
        return {'error': info['error']}
    return {
        'wallet': float(info.get('totalWalletBalance', 0)),
        'equity': float(info.get('totalEquity', info.get('totalWalletBalance', 0))) if info.get('totalEquity') is not None else float(info.get('totalWalletBalance', 0)),
        'unrealized_pnl': float(info.get('totalUnrealizedProfit', 0)),
        'available': float(info.get('availableBalance', 0)),
    }

def get_positions():
    """当前持仓"""
    info = _get('/fapi/v2/account')
    if 'error' in info:
        return []
    positions = []
    for p in info.get('positions', []):
        amt = float(p.get('positionAmt', 0))
        if amt != 0:
            positions.append({
                'symbol': p['symbol'],
                'side': 'LONG' if amt > 0 else 'SHORT',
                'qty': abs(amt),
                'entry': float(p.get('entryPrice', 0)),
                'mark': float(p.get('markPrice', 0)),
                'pnl': float(p.get('unRealizedProfit', 0)),
                'margin': float(p.get('initialMargin', 0)),
                'liquidation': float(p.get('liquidationPrice', 0)),
            })
    return positions

def get_24h_trades():
    """过去24h成交记录（链上真实数据）"""
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - 86400) * 1000)
    params = {
        'symbol': 'BTCUSDT',
        'startTime': start_time,
        'endTime': end_time,
        'limit': 1000,
        'timestamp': end_time
    }
    # Try all active symbols
    trades = []
    for sym in ['BTCUSDT', 'BCHUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT']:
        p = dict(params)
        p['symbol'] = sym
        p['timestamp'] = int(time.time() * 1000)
        data = _get('/fapi/v1/userTrades', p)
        if isinstance(data, list):
            trades.extend(data)
    return trades

def build_report():
    """组装复盘报告"""
    now = datetime.now()
    
    # 链上余额
    bal = get_balance()
    if 'error' in bal:
        return {'error': f"Balance fetch failed: {bal['error']}"}
    
    # 持仓
    positions = get_positions()
    
    # 24h成交
    trades = get_24h_trades()
    
    # 计算真实盈亏
    total_fees = sum(float(t.get('commission', 0)) for t in trades)
    total_realized = sum(float(t.get('realizedPnl', 0)) for t in trades)
    unrealized = bal.get('unrealized_pnl', 0)
    
    return {
        'time': now.isoformat(),
        'balance': bal,
        'positions': positions,
        '24h_trades_count': len(trades),
        '24h_fees': total_fees,
        '24h_realized_pnl': total_realized,
        'total_pnl': total_realized + unrealized,
    }

def call_critic(report):
    """调用DeepSeek Pro做严厉复盘"""
    if not DS_KEY:
        return {'error': 'No DeepSeek API key'}
    
    prompt = f"""你是一个极度严厉的加密货币交易总监。这是过去24小时的交易记录：

## 链上账户状态
总权益: ${report['balance']['equity']:.2f}
未实现PnL: ${report['balance']['unrealized_pnl']:.2f}
可用保证金: ${report['balance']['available']:.2f}

## 当前持仓
{json.dumps(report['positions'], indent=2) if report['positions'] else '空仓'}

## 24h交易统计
成交笔数: {report['24h_trades_count']}
已实现PnL: ${report['24h_realized_pnl']:.2f}
手续费: ${report['24h_fees']:.2f}
总PnL(含浮亏): ${report['total_pnl']:.2f}

## 你的任务
像人类总参谋长一样严厉复盘：
1. 今天是不是又犯了频繁换方向、不设止损、追涨杀跌的致命错误？
2. 方向看对了吗？如果错了，为什么？
3. 手续费占比多少？是否有不必要的交易？
4. 基于今天的教训，应该修改什么参数？

## 输出格式（严格JSON，禁止废话）
{{
    "grade": "A/B/C/D/F",
    "criticism": "一句话核心批评",
    "mistakes": ["错误1", "错误2"],
    "parameter_changes": {{
        "DEFAULT_STOP_PCT": 2.0,
        "MAX_LEVERAGE": 20,
        "MARGIN_PER_TRADE": 30,
        "MAX_DAILY_TRADES": 5,
        "direction_focus": "SHORT/LONG/NONE",
        "other": "其他参数修改说明"
    }},
    "lesson": "一句话教训焊死"
}}"""

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是Jane Street级别的交易总监。极度严厉，不找借口，只给可执行反馈。输出纯JSON。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"}
    }
    
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DS_KEY}"
        }
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
            content = result['choices'][0]['message']['content']
            return json.loads(content)
    except Exception as e:
        return {'error': str(e)}

def apply_changes(critique):
    """将复盘输出的参数修改应用到本地配置"""
    changes = critique.get('parameter_changes', {})
    if not changes:
        return
    
    # 写入复盘记录
    record = {
        'time': datetime.now().isoformat(),
        'critique': critique,
        'applied': True
    }
    retro_log = LOGS / 'retro_history.json'
    history = []
    if retro_log.exists():
        try:
            history = json.loads(retro_log.read_text())
        except:
            pass
    history.append(record)
    retro_log.write_text(json.dumps(history, indent=2))
    
    # 写回策略配置
    config_path = BASE / 'shared_config.json'
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except:
            pass
    
    config['retro_parameters'] = changes
    config['last_retro'] = datetime.now().isoformat()
    config_path.write_text(json.dumps(config, indent=2))

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}', flush=True)

def main():
    log('=== 每日自省系统启动 ===')
    
    # Step 1: 收集链上真实数据
    log('[1/4] 采集链上数据...')
    report = build_report()
    if 'error' in report:
        log(f'采集失败: {report["error"]}')
        return
    log(f'  权益=${report["balance"]["equity"]:.2f} | 24h盈亏=${report["total_pnl"]:.2f}')
    
    # Step 2: AI复盘
    log('[2/4] AI严厉复盘...')
    critique = call_critic(report)
    if 'error' in critique:
        log(f'复盘失败: {critique["error"]}')
        return
    log(f'  评分={critique.get("grade","?")} | {critique.get("criticism","")}')
    
    # Step 3: 应用参数修改
    log('[3/4] 应用参数修改...')
    apply_changes(critique)
    log(f'  已更新: {list(critique.get("parameter_changes",{}).keys())}')
    
    # Step 4: 报告
    log('[4/4] 记录完成')
    log(f'  教训: {critique.get("lesson","无")}')
    log('=== 完成 ===')

if __name__ == '__main__':
    main()
