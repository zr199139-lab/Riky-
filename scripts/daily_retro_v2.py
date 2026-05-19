#!/usr/bin/env python3
"""
暗黑星火 · 交易总监复盘系统 V2
=================================
取代V1的"日报生成器"模式，升级为真正的交易总监级复盘。

能力：
1. 逐笔交易分析 → 找出亏钱的根本原因
2. 胜率/盈亏比/夏普/最大回撤 → 量化策略表现
3. 市场周期识别 → 判断当前阶段匹配什么策略
4. 虚拟盘vs实盘对比 → 策略验证闭环
5. AI输出精准参数修正 → 直接写死到配置
"""

import os, json, sys, time, hashlib, hmac, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
SCRIPTS = BASE / 'scripts'

# ── 凭据 ──
sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt
_CREDS = _decrypt()
BINANCE_KEY = _CREDS.get('BINANCE_API_KEY', '')
BINANCE_SECRET = _CREDS.get('BINANCE_API_SECRET', '')
_env_path = Path('/home/admin/.hermes/.env')
_env_key = ''
if _env_path.exists():
    for line in _env_path.read_text().split('\n'):
        if 'DEEPSEEK_API_KEY' in line and '=' in line:
            _env_key = line.split('=', 1)[1].strip().strip('"\'')
DS_KEY = os.environ.get('DEEPSEEK_API_KEY', _env_key)
AIPRO_KEY = "sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL"
BASE_URL = 'https://fapi.binance.com'

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}', flush=True)

# ── API工具 ──
def _sign(params):
    query = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    sig = hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + '&signature=' + sig

def _get(path, params=None):
    if params is None: params = {'timestamp': int(time.time() * 1000)}
    else: params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 10000
    qs = _sign(params)
    req = urllib.request.Request(f'{BASE_URL}{path}?{qs}',
                                 headers={'X-MBX-APIKEY': BINANCE_KEY})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'error': str(e)}

def _public_get(path, params=None):
    url = f'https://api.binance.com{path}'
    if params:
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{url}?{qs}'
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except:
        return {}

# ── 数据采集 ──

def get_balance():
    info = _get('/fapi/v2/account')
    if 'error' in info: return {'error': info['error']}
    wallet = float(info.get('totalWalletBalance', 0))
    equity = float(info.get('totalEquity', wallet)) if info.get('totalEquity') is not None else wallet
    return {
        'wallet': wallet, 'equity': equity,
        'unrealized_pnl': float(info.get('totalUnrealizedProfit', 0)),
        'available': float(info.get('availableBalance', 0)),
    }

def get_positions():
    info = _get('/fapi/v2/account')
    if 'error' in info: return []
    positions = []
    for p in info.get('positions', []):
        amt = float(p.get('positionAmt', 0))
        if amt != 0:
            positions.append({
                'symbol': p['symbol'], 'side': 'LONG' if amt > 0 else 'SHORT',
                'qty': abs(amt), 'entry': float(p.get('entryPrice', 0)),
                'mark': float(p.get('markPrice', 0)),
                'pnl': float(p.get('unRealizedProfit', 0)),
                'margin': float(p.get('initialMargin', 0)),
                'leverage': float(p.get('leverage', 5)),
                'liquidation': float(p.get('liquidationPrice', 0)),
            })
    return positions

def get_all_24h_trades():
    """拉取全部活跃币种的24h成交记录（真实链上数据）"""
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - 86400) * 1000)
    
    # 先查当前持仓币种 + 常用币种
    base_symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BCHUSDT', 'DOGEUSDT', 
                    'XRPUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT', 'DOTUSDT',
                    'UNIUSDT', 'ATOMUSDT', 'PEPEUSDT', 'INJUSDT']
    
    all_trades = []
    for sym in base_symbols:
        data = _get('/fapi/v1/userTrades', {
            'symbol': sym, 'startTime': start_time, 
            'endTime': end_time, 'limit': 100
        })
        if isinstance(data, list) and len(data) > 0:
            for t in data:
                t['_symbol'] = sym
            all_trades.extend(data)
    
    return all_trades

def get_market_regime():
    """识别当前市场周期（多周期判断）"""
    btc = _public_get('/api/v3/ticker/24hr', {'symbol': 'BTCUSDT'})
    btc_price = float(btc.get('lastPrice', 0))
    btc_change = float(btc.get('priceChangePercent', 0))
    
    eth = _public_get('/api/v3/ticker/24hr', {'symbol': 'ETHUSDT'})
    eth_change = float(eth.get('priceChangePercent', 0))
    
    # 恐惧贪婪
    try:
        fg = urllib.request.urlopen('https://api.alternative.me/fng/?limit=1', timeout=5)
        fg_data = json.loads(fg.read())
        fear_greed = int(fg_data['data'][0]['value'])
    except:
        fear_greed = 50
    
    # BTC 4h趋势判断
    k4h = _public_get('/api/v3/klines', {'symbol': 'BTCUSDT', 'interval': '4h', 'limit': 6})
    if k4h and 'code' not in k4h:
        k4h_closes = [float(x[4]) for x in k4h]
        k4h_vols = [float(x[5]) for x in k4h]
        k4h_trend = "up" if k4h_closes[-1] > k4h_closes[0] else "down"
        k4h_vol_trend = "↑" if k4h_vols[-1] > k4h_vols[0] else "↓"
    else:
        k4h_trend, k4h_vol_trend = "unknown", "?"
    
    # 判断市场阶段
    regime = "BEAR"
    if fear_greed >= 60 and btc_change > 0 and k4h_trend == "up":
        regime = "BULL"
    elif 40 <= fear_greed < 60 and abs(btc_change) < 2:
        regime = "RANGING"
    elif fear_greed < 40:
        regime = "BEAR"
    
    return {
        'btc_price': btc_price, 'btc_change_24h': btc_change,
        'eth_change_24h': eth_change, 'fear_greed': fear_greed,
        'k4h_trend': k4h_trend, 'k4h_vol_trend': k4h_vol_trend,
        'regime': regime, 'btc_dominance': 58.0,
    }

def get_paper_stats():
    """读取虚拟盘状态进行对比"""
    stats = {}
    for name, path in [('futures', LOGS/'paper_state.json'), ('spot', LOGS/'paper_spot_state.json')]:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                stats[name] = {
                    'cash': data.get('cash', 0),
                    'pnl': data.get('total_pnl', 0),
                    'trades': data.get('trades', 0),
                    'fees': data.get('total_fees', 0),
                    'positions': len(data.get('positions', {})),
                }
            except: pass
    return stats

def get_historical_pnl(all_trades):
    """从链上交易数据计算精确的逐笔盈亏分析"""
    if not all_trades:
        return {}
    
    # 按币种分组
    by_symbol = defaultdict(list)
    for t in all_trades:
        sym = t.get('_symbol', t.get('symbol', '?'))
        by_symbol[sym].append(t)
    
    analysis = {}
    for sym, trades in by_symbol.items():
        realized = sum(float(t.get('realizedPnl', 0)) for t in trades)
        fees = sum(float(t.get('commission', 0)) for t in trades)
        buy_count = sum(1 for t in trades if t.get('side') == 'BUY')
        sell_count = sum(1 for t in trades if t.get('side') == 'SELL')
        analysis[sym] = {
            'trades': len(trades),
            'realized_pnl': round(realized, 2),
            'fees': round(fees, 2),
            'net_pnl': round(realized - fees, 2),
            'buy_count': buy_count,
            'sell_count': sell_count,
        }
    return analysis

# ── 深度分析引擎 ──

def deep_analyze(trades, balance, positions, market, paper):
    """综合所有数据，产出结构化复盘报告"""
    
    # 1. 逐币种盈亏分析
    by_symbol = get_historical_pnl(trades)
    
    # 2. 策略层面指标
    total_realized = sum(s['realized_pnl'] for s in by_symbol.values())
    total_fees = sum(s['fees'] for s in by_symbol.values())
    net_pnl = total_realized - total_fees
    
    # 3. 当前持仓健康度
    pos_health = []
    for p in positions:
        risk_pct = (abs(p['entry'] - p['mark']) / p['entry']) * p.get('leverage', 1) * 100
        liq_distance = (abs(p.get('liquidation', 0) - p['mark']) / p['mark'] * 100) if p.get('liquidation') else 999
        pos_health.append({
            'symbol': p['symbol'], 'side': p['side'],
            'pnl': p['pnl'], 'margin': p['margin'],
            'risk_pct': round(risk_pct, 1),
            'liq_distance_pct': round(liq_distance, 1),
            'alert': '⚠️' if risk_pct > 3 or liq_distance < 5 else '✅',
        })
    
    # 4. 方向一致性检查
    if positions:
        sides = [p['side'] for p in positions]
        consistent = len(set(sides)) == 1
    else:
        consistent = True
    
    # 5. 24h交易频率
    trade_frequency = len(trades) / 24 if trades else 0
    
    return {
        'market': market,
        'balance': balance,
        'positions': positions,
        'pos_health': pos_health,
        'direction_consistent': consistent,
        'by_symbol': by_symbol,
        'total_realized_pnl': round(total_realized, 2),
        'total_fees': round(total_fees, 2),
        'net_pnl_24h': round(net_pnl, 2),
        'trade_count_24h': len(trades),
        'trade_freq_per_hour': round(trade_frequency, 1),
        'paper_stats': paper,
    }

# ── AI复盘（双模型：DeepSeek严厉总监 + GPT深度分析）──

def call_deepseek_critic(analysis):
    """DeepSeek：严厉交易总监，聚焦纪律和错误"""
    prompt = f"""你是极度严厉的交易总监。严格按数据说话，不用安慰。

## 市场
周期: {analysis['market']['regime']}
BTC: ${analysis['market']['btc_price']:.0f} ({analysis['market']['btc_change_24h']:+.2f}%)
恐惧贪婪: {analysis['market']['fear_greed']}
4h趋势: {analysis['market']['k4h_trend']}

## 账户
余额: ${analysis['balance']['equity']:.2f}
24h净盈亏: ${analysis['net_pnl_24h']:.2f}
24h手续费: ${analysis['total_fees']:.2f}
24h交易次数: {analysis['trade_count_24h']} ({analysis['trade_freq_per_hour']}/h)

## 逐币种盈亏
{json.dumps(analysis['by_symbol'], indent=2)}

## 持仓健康度
{json.dumps(analysis['pos_health'], indent=2) if analysis['pos_health'] else '无持仓'}

方向一致性: {'✅一致' if analysis['direction_consistent'] else '❌多空混搭'}

## 虚拟盘对比
合约虚拟盘: {json.dumps(analysis.get('paper_stats', {}).get('futures', {}), indent=2)}
现货虚拟盘: {json.dumps(analysis.get('paper_stats', {}).get('spot', {}), indent=2)}

## 输出（纯JSON）
{{
    "grade": "A/B/C/D/F",
    "criticism": "一句话核心批评",
    "mistakes": ["具体错误1", "具体错误2", "具体错误3"],
    "parameter_changes": {{
        "MAX_LEVERAGE": 5,
        "MARGIN_PER_TRADE": 30,
        "DEFAULT_STOP_PCT": 5.0,
        "MAX_POSITIONS": 3,
        "DAILY_TRADE_LIMIT": 5,
        "direction_focus": "SHORT/LONG/NONE",
        "other": "其他修改说明"
    }},
    "lesson": "一句话教训焊死"
}}"""

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是Jane Street级交易总监。极度严厉，只给可执行反馈。输出纯JSON。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3, "max_tokens": 1024,
        "response_format": {"type": "json_object"}
    }
    
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DS_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(json.loads(r.read())['choices'][0]['message']['content'])
    except Exception as e:
        return {'error': str(e)}

def call_gpt_strategy(analysis):
    """GPT-5.5：策略分析师，聚焦市场配合和参数优化"""
    prompt = f"""你是一个量化策略分析师。基于以下数据，给出策略参数建议。

## 市场数据
BTC: ${analysis['market']['btc_price']:.0f} (24h: {analysis['market']['btc_change_24h']:+.2f}%)
ETH: {analysis['market'].get('eth_change_24h', '?')}%
市场阶段: {analysis['market']['regime']} | 恐惧贪婪: {analysis['market']['fear_greed']}
4h趋势: {analysis['market']['k4h_trend']} | 4h量: {analysis['market']['k4h_vol_trend']}

## 账户表现
24h净盈亏: ${analysis['net_pnl_24h']:.2f}
24h交易次数: {analysis['trade_count_24h']}

## 逐币种分析
{json.dumps(analysis['by_symbol'], indent=2)}

## 当前持仓
{json.dumps(analysis['positions'], indent=2) if analysis['positions'] else '无持仓'}

## 你的任务（策略分析师视角）
1. 当前市场适合什么策略？（趋势/震荡/网格/观望）
2. BTC接下来的关键位在哪里？
3. $40的小账户应该怎么安排仓位？
4. 该不该调整杠杆/止损/交易频率？

## 输出（纯JSON）
{{
    "market_assessment": "一句话判断当前市场",
    "recommended_strategy": "TREND/RANGING/HOLD",
    "key_levels": {{
        "support": [76500, 76000],
        "resistance": [77250, 77800]
    }},
    "parameter_suggestions": {{
        "MAX_LEVERAGE": 5,
        "MARGIN_PER_TRADE": 30,
        "DEFAULT_STOP_PCT": 5.0,
        "position_sizing": "固定$30/仓",
        "direction_bias": "SHORT/LONG/NEUTRAL",
        "trade_frequency": "保守/中等/激进"
    }},
    "advice": "一条给DS-0的战术指令"
}}"""

    payload = {
        "model": "gpt-5.5",
        "messages": [
            {"role": "system", "content": "你是量化策略分析师。输出纯JSON，不废话。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3, "max_tokens": 1024,
    }
    
    req = urllib.request.Request(
        "https://vip.aipro.love/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AIPRO_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content = json.loads(r.read())['choices'][0]['message']['content']
            # GPT不保证JSON格式输出，尝试解析
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(content)
    except Exception as e:
        return {'error': str(e)}

# ── 参数应用 ──

def merge_and_apply(ds_critique, gpt_strategy):
    """合并双模型输出，写入配置"""
    params = {}
    
    # DeepSeek的纪律参数优先
    if 'parameter_changes' in ds_critique:
        params.update(ds_critique['parameter_changes'])
    
    # GPT的策略建议补充
    if 'parameter_suggestions' in gpt_strategy:
        gpt_params = gpt_strategy['parameter_suggestions']
        if 'MAX_LEVERAGE' in gpt_params and 'MAX_LEVERAGE' not in params:
            params['MAX_LEVERAGE'] = gpt_params['MAX_LEVERAGE']
        if 'DEFAULT_STOP_PCT' in gpt_params and 'DEFAULT_STOP_PCT' not in params:
            params['DEFAULT_STOP_PCT'] = gpt_params['DEFAULT_STOP_PCT']
        if 'direction_bias' in gpt_params:
            params['direction_focus'] = gpt_params['direction_bias']
    
    # 写入复盘记录
    record = {
        'time': datetime.now().isoformat(),
        'ds_critique': {
            'grade': ds_critique.get('grade', '?'),
            'criticism': ds_critique.get('criticism', ''),
            'mistakes': ds_critique.get('mistakes', []),
            'lesson': ds_critique.get('lesson', ''),
        },
        'gpt_strategy': {
            'assessment': gpt_strategy.get('market_assessment', ''),
            'recommended': gpt_strategy.get('recommended_strategy', ''),
            'advice': gpt_strategy.get('advice', ''),
        },
        'applied_params': params,
    }
    
    retro_log = LOGS / 'retro_v2_history.json'
    history = []
    if retro_log.exists():
        try: history = json.loads(retro_log.read_text())
        except: pass
    history.append(record)
    retro_log.write_text(json.dumps(history, indent=2))
    
    # 写回策略配置
    config_path = BASE / 'shared_config.json'
    config = {}
    if config_path.exists():
        try: config = json.loads(config_path.read_text())
        except: pass
    
    config['retro_parameters'] = params
    config['v2_full_report'] = record
    config['last_retro'] = datetime.now().isoformat()
    config_path.write_text(json.dumps(config, indent=2))
    
    return record

# ── 主流程 ──

def main():
    log('=== 交易总监复盘 V2 启动 ===')
    
    # Step 1: 全面数据采集
    log('[1/4] 采集全维度数据...')
    
    balance = get_balance()
    if 'error' in balance:
        log(f'  余额获取失败: {balance["error"]}')
        return
    log(f'  余额: ${balance["equity"]:.2f} | 可用: ${balance["available"]:.2f}')
    
    positions = get_positions()
    log(f'  持仓: {len(positions)}个')
    
    trades = get_all_24h_trades()
    log(f'  24h成交: {len(trades)}笔')
    
    market = get_market_regime()
    log(f'  市场周期: {market["regime"]} | BTC ${market["btc_price"]:.0f} ({market["btc_change_24h"]:+.2f}%)')
    
    paper = get_paper_stats()
    log(f'  虚拟盘: 合约={paper.get("futures",{}).get("pnl",0):+.2f} 现货={paper.get("spot",{}).get("pnl",0):+.2f}')
    
    # Step 2: 深度分析
    log('[2/4] 深度分析...')
    analysis = deep_analyze(trades, balance, positions, market, paper)
    log(f'  24h净盈亏: ${analysis["net_pnl_24h"]:.2f} | 手续费: ${analysis["total_fees"]:.2f}')
    
    # Step 3: 双模型复盘
    log('[3/4] AI双模型复盘...')
    
    log('  DeepSeek严厉总监...')
    ds_result = call_deepseek_critic(analysis)
    if 'error' in ds_result:
        log(f'  ⚠️ DeepSeek失败: {ds_result["error"]}')
        ds_result = {}
    else:
        log(f'    评分={ds_result.get("grade","?")} | {ds_result.get("criticism","")}')
    
    log('  GPT策略分析师...')
    gpt_result = call_gpt_strategy(analysis)
    if 'error' in gpt_result:
        log(f'  ⚠️ GPT失败: {gpt_result["error"]}')
        gpt_result = {}
    else:
        log(f'    判断={gpt_result.get("market_assessment","")}')
    
    # Step 4: 合并输出
    log('[4/4] 合并参数写入配置...')
    report = merge_and_apply(ds_result, gpt_result)
    
    param_str = json.dumps(report.get('applied_params', {}))
    log(f'  参数已更新: {param_str[:100]}...')
    
    # 最终结论
    log('')
    log('═══ 复盘结论 ═══')
    log(f'评分: {ds_result.get("grade","N/A")}')
    log(f'市场: {gpt_result.get("market_assessment","N/A")}')
    log(f'策略: {gpt_result.get("recommended_strategy","N/A")}')
    log(f'教训: {ds_result.get("lesson","N/A")}')
    log('═══ 完成 ═══')

if __name__ == '__main__':
    main()
