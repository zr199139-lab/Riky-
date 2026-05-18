#!/usr/bin/env python3
"""
暗黑星火 · GPT策略审计器 v1
===========================
哈基米GPT (gpt-5.4@1314mc) 定期审计虚拟盘绩效。
输出: 策略评分 + 调参建议 + 黑名单/白名单

模型分工:
  - 常规审计: gpt-5.4 (1314mc)  ← 哈基米GPT
  - 深度分析: gpt-5.5 (1314mc)  ← 仅需要时唤醒
  
DS-0 Root调用流程:
  1. 读 virtual_state/*.json (各策略状态)
  2. 读 analysis/virtual_report.json (汇总)
  3. 读 analysis/income_30day_final.json (30天损益)
  4. 调哈基米GPT分析 → 输出建议
  5. 写 analysis/audit_recommendations.json
"""
import sys, os, json, time, requests
from datetime import datetime, timezone

BASE = '/home/admin/charon'
STATE = f'{BASE}/virtual_state'
ANALYSIS = f'{BASE}/analysis'

# 哈基米GPT: 1314mc gpt-5.4
GPT_API = "https://api.1314mc.net/v1"
GPT_KEY = "sk-5fZEPvB59BBqWDLU0JSK9heLCpIbCfXXbNCSjcbEyk1wlkVf"
GPT_MODEL = "gpt-5.4"  # 哈基米GPT
GPT_MODEL_DEEP = "gpt-5.5"  # 深度分析

# DeepSeek官方 (根通道备用)
DS_API = "https://api.deepseek.com/v1"
DS_KEY = os.environ.get('DEEPSEEK_API_KEY', 'sk-1c97d4dca64f3caa222dc9db6c8077b8a819e74081973fb29e1e65dfa1138771')

def log(msg):
    print(f'[AUDIT] {datetime.now(timezone.utc).strftime("%m-%d %H:%M:%S")} {msg}')

def call_gpt(prompt, model=GPT_MODEL, max_tokens=2000):
    """调哈基米GPT (或备用模型)"""
    try:
        r = requests.post(f'{GPT_API}/chat/completions', headers={
            'Authorization': f'Bearer {GPT_KEY}',
            'Content-Type': 'application/json',
        }, json={
            'model': model,
            'messages': [{'role':'user','content':prompt}],
            'max_tokens': max_tokens,
            'temperature': 0.3,
        }, timeout=30)
        data = r.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        log(f'哈基米GPT调用失败: {e}')
        return None

def call_deepseek(prompt, model='deepseek-v4-pro'):
    """备用: DS官方模型"""
    try:
        r = requests.post(f'{DS_API}/chat/completions', headers={
            'Authorization': f'Bearer {DS_KEY}',
            'Content-Type': 'application/json',
        }, json={
            'model': model,
            'messages': [{'role':'user','content':prompt}],
            'max_tokens': 2000,
            'temperature': 0.3,
        }, timeout=30)
        data = r.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        log(f'DS调用失败: {e}')
        return None

def load_strategy_data():
    """加载所有虚拟盘状态"""
    strategies = {}
    if not os.path.exists(STATE):
        return strategies
    for fname in os.listdir(STATE):
        if fname.endswith('.json'):
            with open(f'{STATE}/{fname}') as f:
                data = json.load(f)
            sid = data.get('id', fname.replace('.json',''))
            
            # 统计
            trades = data.get('trades', [])
            closes = [t for t in trades if 'pnl' in t]
            wins = [t for t in closes if t.get('pnl',0) > 0]
            total_pnl = sum(t.get('pnl',0) for t in closes)
            total_fee = sum(t.get('fee',0) for t in trades)
            
            strategies[sid] = {
                'name': data.get('name', sid),
                'capital': data.get('capital', 1000),
                'positions': len(data.get('positions', {})),
                'total_trades': len(trades),
                'closed_trades': len(closes),
                'wins': len(wins),
                'total_pnl': round(total_pnl, 2),
                'total_fee': round(total_fee, 2),
                'win_rate': f'{len(wins)/max(len(closes),1)*100:.0f}%',
            }
    return strategies

def load_income_data():
    """加载30天损益数据"""
    path = f'{ANALYSIS}/income_30day_final.json'
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def build_audit_prompt(strategies, income):
    """构建GPT审计提示词"""
    prompt = """你是一个量化交易策略审计专家。分析以下虚拟盘策略绩效数据:

"""
    for sid, s in strategies.items():
        prompt += f"""\n## {s['name']} ({sid})
- 资金: ${s['capital']:.2f}
- 持仓: {s['positions']}个
- 交易: {s['total_trades']}笔 (已平仓{s['closed_trades']}笔)
- 胜率: {s['win_rate']}
- 总PnL: ${s['total_pnl']}
- 总手续费: ${s['total_fee']}
- 净盈亏: ${s['total_pnl'] - s['total_fee']:.2f}
"""
    
    if income:
        s = income.get('summary', {})
        prompt += f"""
\n## 30天实盘损益参考
- 已实现PnL: ${s.get('realized_pnl',0)}
- 手续费: ${s.get('commission',0)}
- 资金费: ${s.get('funding',0)}
- 净盈亏: ${s.get('net',0)}

## 赚钱币Top5:
"""
        for c in income.get('by_coin', [])[:5]:
            prompt += f"- {c['symbol']}: PnL=${c['pnl']} Fee=${c['fee']}\n"
        prompt += "\n## 亏钱币Top5:\n"
        for c in income.get('by_coin', [])[-5:]:
            prompt += f"- {c['symbol']}: PnL=${c['pnl']} Fee=${c['fee']}\n"
    
    prompt += """
\n请输出以下格式(严格JSON,不要多余文字):
{
  "overall_assessment": "总体评价(一句话)",
  "best_strategy": "绩效最好的策略ID",
  "worst_strategy": "绩效最差的策略ID",
  "kill_list": ["建议砍掉的策略ID"],
  "promote_list": ["建议增加投资的策略ID"],
  "parameter_suggestions": {
    "grid_spot": {"调整建议": "如: 布林带2.0→2.5", "new_params": {}},
    "trend_futures": {"调整建议": "", "new_params": {}},
    "meanrev_futures": {"调整建议": "", "new_params": {}},
    "momentum_breakout": {"调整建议": "", "new_params": {}}
  },
  "blacklist": ["建议永久拉黑的币种"],
  "whitelist": ["建议增加关注的币种"],
  "action": "hold|kill_worst|promote_best|restart_all"
}
"""
    return prompt

def save_recommendations(text, strategies):
    """解析GPT输出并保存"""
    # 尝试从回复中提取JSON
    import re
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        try:
            rec = json.loads(json_match.group())
        except:
            rec = {'raw_output': text[:500]}
    else:
        rec = {'raw_output': text[:500]}
    
    rec['timestamp'] = datetime.now(timezone.utc).isoformat()
    rec['strategies'] = strategies
    
    path = f'{ANALYSIS}/audit_recommendations.json'
    with open(path, 'w') as f:
        json.dump(rec, f, indent=2)
    log(f'✅ 审计推荐已保存: {path}')
    return rec

def main():
    log('哈基米GPT审计开始...')
    
    # 1. 加载数据
    strategies = load_strategy_data()
    income = load_income_data()
    
    if not strategies:
        log('⚠️ 没有虚拟盘数据,跳过审计')
        return
    
    log(f'策略数: {len(strategies)}, 30天损益: {"有" if income else "无"}')
    
    # 2. 构建提示词
    prompt = build_audit_prompt(strategies, income)
    
    # 3. 调哈基米GPT
    log(f'调 {GPT_MODEL}...')
    response = call_gpt(prompt)
    
    if not response:
        log('哈基米GPT失败,切DS备用')
        response = call_deepseek(prompt)
    
    if not response:
        log('❌ 所有模型调用失败')
        return
    
    log(f'GPT回复: {response[:100]}...')
    
    # 4. 保存推荐
    rec = save_recommendations(response, strategies)
    
    # 5. 输出摘要
    action = rec.get('action', 'hold')
    best = rec.get('best_strategy', '?')
    worst = rec.get('worst_strategy', '?')
    log(f'推荐操作: {action} | 最佳: {best} | 最差: {worst}')

if __name__ == '__main__':
    main()
