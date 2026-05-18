#!/usr/bin/env python3
"""
暗黑星火 自我进化引擎 v1.0
===========================
全托管核心：情报→决策→执行→进化 闭环

循环周期：
  - 每6h: GPT全面评估市场 + 生成交易参数
  - 每6h: 同步参数到4个虚拟盘的共享config
  - 每24h: 回溯分析 + 策略变异（赢家增仓/输家改参）

工作流:
  1. collect_intel()  → 读取intel_collector最新情报
  2. gpt_analyze()    → GPT评估市场 + 生成策略参数
  3. sync_config()    → 写入共享 config.json
  4. self_review()    → 24h回溯 + 自动变异
"""
import os, json, time, requests, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path('/home/admin/charon')
STRATEGIES = BASE / 'strategies'
LOGS = BASE / 'bot_logs'
os.makedirs(LOGS, exist_ok=True)

# ── API ──
AIPRO_KEY = 'sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL'
DS_KEY = os.environ.get('DEEPSEEK_API_KEY', '')

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOGS / 'evolution_log.txt', 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def load_state(name):
    f = LOGS / f'{name}_state.json'
    try: return json.load(open(f))
    except: return None

def load_intel():
    """读取情报系统最新数据"""
    intel_file = Path('/home/admin/.hermes/mempalace/quant_trading/autonomous/intel.json')
    if intel_file.exists():
        return json.load(open(intel_file))
    return {'time': datetime.now().isoformat(), 'fear_greed': 25, 'btc_dominance': 52.5,
            'market': 'bearish', 'news': '全线下跌, 无明显利好', 'note': '默认情报'}

def gpt_decision(market_data, strategy_performance):
    """调用GPT-5.5生成交易决策"""
    url = 'https://vip.aipro.love/v1/chat/completions'
    
    prompt = f"""你是暗黑星火资本的CEO, 全托管模式下直接决策。

当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}

=== 市场数据 ===
{json.dumps(market_data, indent=2)}

=== 策略表现(24h) ===
{json.dumps(strategy_performance, indent=2) if strategy_performance else "刚启动无数据"}

你的任务: 为以下4个虚拟盘生成下一周期的参数。必须输出JSON格式:

{{
  "market_assessment": "bearish/bullish/sideways (一句话理由)",
  "spot": {{
    "meanrevert_paper": {{
      "active": true/false,
      "rsi_oversold": 整数(当前20),
      "rsi_overbought": 整数(当前55),
      "position_pct": 浮点数(当前0.3),
      "stop_loss_atr": 浮点数(当前1.5),
      "action": "hold/open/close (不写hold以外的理由不开)"
    }},
    "rsi_meanrev_paper": {{
      "active": true/false,
      "rsi_oversold": 整数(当前30),
      "rsi_overbought": 整数(当前70),
      "position_pct": 浮点数(当前0.2),
      "stop_loss_pct": 浮点数(当前0.06),
      "action": "hold/open/close"
    }}
  }},
  "futures": {{
    "combo31_paper": {{
      "active": true/false,
      "leverage": 整数(当前5),
      "position_pct": 浮点数(当前0.2),
      "stop_loss": 浮点数(当前0.08),
      "tp_split_atr": 浮点数(当前1.0),
      "action": "hold/open/close"
    }},
    "futures_paper": {{
      "active": true/false,
      "leverage": 整数(当前5),
      "position_pct": 浮点数(当前0.4),
      "action": "hold/open/close"
    }}
  }},
  "risk_control": {{
    "daily_loss_limit": 浮点数(当前5.0),
    "max_positions": 整数(当前2),
    "urgent_advice": "一句话建议"
  }}
}}

只输出JSON, 不要markdown包裹。
"""

    try:
        r = requests.post(url, json={
            'model': 'gpt-5.5',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 1500,
            'temperature': 0.3
        }, headers={'Authorization': f'Bearer {AIPRO_KEY}'}, timeout=90)
        
        if r.status_code != 200:
            log(f'GPT调用失败 HTTP {r.status_code}')
            return None
        
        content = r.json()['choices'][0]['message']['content']
        # 去掉markdown包裹
        content = content.replace('```json', '').replace('```', '').strip()
        return json.loads(content)
    except Exception as e:
        log(f'[ERR] gpt_decision: {e}')
        return None

def sync_config(decision):
    """写入共享config供所有策略读取"""
    config_file = LOGS / 'shared_config.json'
    
    config = {
        'updated_at': datetime.now().isoformat(),
        'market_assessment': decision.get('market_assessment', 'unknown'),
        'risk_control': decision.get('risk_control', {}),
        'strategies': decision.get('spot', {}),
        'futures': decision.get('futures', {}),
    }
    
    json.dump(config, open(config_file, 'w'), indent=2)
    log(f'[CONFIG] 已同步 -> {config_file}')
    
    # 同时写advisory供futures/hv_bot读取
    advisory = {
        'time': datetime.now().isoformat(),
        'regime': 'bear' if 'bear' in decision.get('market_assessment','').lower() else 'bull' if 'bull' in decision.get('market_assessment','').lower() else 'sideways',
        'confidence': 0.7,
        'advice': decision.get('risk_control', {}).get('urgent_advice', ''),
        'override_tp': None,
        'override_sl': None,
        'pause': False
    }
    json.dump(advisory, open(LOGS / 'advisory.json', 'w'), indent=2)
    log(f'[ADVISORY] 已同步')

def load_market_data():
    """整合市场数据供GPT决策"""
    import ccxt
    ex = ccxt.binance({'enableRateLimit': True, 'rateLimit': 2000})
    
    data = {'prices': {}, 'funding': {}, 'regime': 'unknown'}
    
    for s in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT']:
        try:
            t = ex.fetch_ticker(s)
            data['prices'][s] = {'last': t['last'], 'change_24h': t['percentage']}
        except: pass
        
        try:
            fr = ex.fetch_funding_rate(s.replace('/','')+':USDT')
            data['funding'][s] = float(fr['info']['lastFundingRate'])
        except:
            data['funding'][s] = 0
    
    # 计算市场状态
    btc_change = data['prices'].get('BTC/USDT', {}).get('change_24h', 0)
    if btc_change < -2: data['regime'] = 'bearish'
    elif btc_change > 2: data['regime'] = 'bullish'
    else: data['regime'] = 'sideways'
    
    # 读取情报
    intel = load_intel()
    data['intel'] = intel
    
    return data

def analyze_performance():
    """读取4个策略的状态"""
    perf = {}
    for name in ['meanrevert_paper', 'rsi_meanrev_paper', 'combo31_paper', 'futures_paper']:
        s = load_state(name)
        if s:
            perf[name] = {
                'trades': s.get('trades', 0),
                'realized_pnl': s.get('pnl', 0),
                'daily_pnl': s.get('daily_pnl', 0),
                'cash': s.get('cash', 1000),
                'fees': s.get('fees_paid', 0),
                'has_position': (s.get('position') is not None) or (len(s.get('positions', {})) > 0)
            }
    return perf

def save_evolution_record(decision, performance):
    """保存每轮决策到历史"""
    record_file = LOGS / 'evolution_history.json'
    history = []
    if record_file.exists():
        try: history = json.load(open(record_file))
        except: pass
    
    record = {
        'time': datetime.now().isoformat(),
        'cycle': len(history) + 1,
        'decision_summary': {
            'market': decision.get('market_assessment', '?') if decision else 'failed',
            'actions': {k: v.get('action','?') for k,v in (decision or {}).get('spot',{}).items()} if decision else {},
            'futures_actions': {k: v.get('action','?') for k,v in (decision or {}).get('futures',{}).items()} if decision else {}
        },
        'performance': performance
    }
    
    history.append(record)
    if len(history) > 365: history = history[-365:]
    json.dump(history, open(record_file, 'w'), indent=2)

# ── 主循环 ──
if __name__ == '__main__':
    cycle_num = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    log(f'=== 进化引擎 启动 | 周期#{cycle_num} ===')
    
    # 1. 加载市场数据
    market = load_market_data()
    log(f'[DATA] BTC ${market["prices"].get("BTC/USDT",{}).get("last",0):.0f} {market["regime"]}')
    
    # 2. 读取策略表现
    perf = analyze_performance()
    log(f'[PERF] {len(perf)}个策略活跃')
    
    # 3. GPT决策
    decision = gpt_decision(market, perf)
    if decision:
        log(f'[GPT] 市场判断: {decision.get("market_assessment", "?")}')
        sync_config(decision)
        save_evolution_record(decision, perf)
    else:
        log('[GPT] 决策失败, 保持现有参数')
    
    # 4. 保存运行报告
    report = {
        'time': datetime.now().isoformat(),
        'cycle': cycle_num,
        'market': market,
        'performance': perf,
        'decision': decision,
        'summary': {
            'total_pnl': sum(p.get('realized_pnl',0) for p in perf.values()),
            'total_daily': sum(p.get('daily_pnl',0) for p in perf.values()),
            'total_fees': sum(p.get('fees',0) for p in perf.values()),
            'active_strategies': len(perf)
        }
    }
    json.dump(report, open(LOGS / f'evolution_report_{datetime.now().strftime("%Y%m%d_%H%M")}.json', 'w'), indent=2)
    
    log(f'=== 周期#{cycle_num} 完成 | 报告已保存 ===')
