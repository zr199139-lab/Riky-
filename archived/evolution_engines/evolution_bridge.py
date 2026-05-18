#!/usr/bin/env python3
"""
暗黑星火 + evolver GEP进化桥
==============================
自动闭环：memory写入 → evolver分析 → GPT解读GEP → shared_config热加载

每6h由cron触发一次。
"""
import os, json, sys, subprocess, re
from datetime import datetime
from pathlib import Path

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
SCRIPTS = BASE / 'scripts'
MEMORY = BASE / 'memory'
NPM_PATH = os.path.expanduser('~/.npm-global/bin')

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOGS / 'evolution_bridge.log', 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def run_evolver():
    """运行evolver并获取GEP输出"""
    env = os.environ.copy()
    env['PATH'] = f'{NPM_PATH}:{env.get("PATH", "")}'
    
    try:
        r = subprocess.run(
            ['evolver', '--routing=fastest'],
            cwd=str(BASE),
            env=env,
            capture_output=True,
            text=True,
            timeout=30  # --routing=fastest 模式很快
        )
        output = r.stdout + r.stderr
        log(f'evolver exit={r.returncode}, output_len={len(output)}')
        return output
    except subprocess.TimeoutExpired:
        log('[WARN] evolver超时(60s)')
        return None
    except Exception as e:
        log(f'[ERR] evolver运行失败: {e}')
        return None

def parse_gep_signals(evolver_output):
    """从evolver输出中提取GEP信号"""
    if not evolver_output:
        return []
    
    signals = []
    
    # 提取 Trigger Signals
    m = re.search(r'"trigger_signals"\s*:\s*\[(.*?)\]', evolver_output)
    if m:
        raw = m.group(1)
        sigs = re.findall(r'"([^"]+)"', raw)
        signals.extend(sigs)
    
    # 提取 Intent
    m = re.search(r'"intent"\s*:\s*"([^"]+)"', evolver_output)
    if m:
        signals.append(f'intent:{m.group(1)}')
    
    # 提取 Category
    m = re.search(r'"category"\s*:\s*"([^"]+)"', evolver_output)
    if m:
        signals.append(f'category:{m.group(1)}')
    
    # 提取 risk_level
    m = re.search(r'"risk_level"\s*:\s*"([^"]+)"', evolver_output)
    if m:
        signals.append(f'risk:{m.group(1)}')
    
    if not signals:
        signals = ['gep_cycle_completed']
    
    return signals

def create_gep_prompt(signals, strategy_perf):
    """为GPT构建带GEP上下文的分析prompt"""
    
    perf_summary = ""
    for name, data in strategy_perf.get('strategies', {}).items():
        if data.get('trades', 0) > 0 or data.get('pnl', 0) != 0:
            perf_summary += f"\n  {name}: PnL=${data['pnl']:.2f} | 交易={data['trades']} | 资金=${data['cash']:.0f}"
    
    prompt = f"""你是暗黑星火资本的进化分析师。当前evolver GEP进化引擎输出以下信号:

GEP Signals: {json.dumps(signals)}

策略表现:
{perf_summary if perf_summary else '  所有策略刚启动,暂无可分析数据'}

你的任务:
1. 分析GEP信号含义
2. 结合策略表现给出参数调整建议
3. 输出JSON格式决策

输出格式:
{{
  "gep_analysis": "一句话分析GEP信号",
  "market_adjustment": "bullish/bearish/sideways - 基于信号判断",
  "params": {{
    "meanrevert_paper": {{
      "active": true,
      "rsi_oversold": 20,
      "rsi_overbought": 55,
      "position_pct": 0.3,
      "stop_loss_atr": 1.5,
      "action": "hold"
    }},
    "rsi_meanrev_paper": {{
      "active": true,
      "rsi_oversold": 30,
      "rsi_overbought": 70,
      "position_pct": 0.2,
      "action": "hold"
    }},
    "combo31_paper": {{
      "active": true,
      "leverage": 5,
      "position_pct": 0.2,
      "stop_loss": 0.08,
      "action": "hold"
    }},
    "futures_paper": {{
      "active": true,
      "leverage": 5,
      "position_pct": 0.4,
      "action": "hold"
    }}
  }},
  "risk_control": {{
    "daily_loss_limit": 5.0,
    "max_positions": 2,
    "urgent_advice": "一句话建议"
  }}
}}

只输出JSON，不要markdown包裹。
"""
    return prompt

def call_gpt(prompt):
    """调用DeepSeek官方API做GEP解读"""
    ds_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not ds_key:
        log('[ERR] DEEPSEEK_API_KEY 未设置')
        return None
    
    try:
        import requests
        r = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            json={
                'model': 'deepseek-chat',
                'messages': [
                    {'role': 'system', 'content': '你是暗黑星火资本的进化分析师。输出严格JSON格式。'},
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': 2000,
                'temperature': 0.3
            },
            headers={'Authorization': f'Bearer {ds_key}'},
            timeout=90
        )
        if r.status_code != 200:
            log(f'[ERR] GPT调用失败 HTTP {r.status_code}')
            return None
        
        content = r.json()['choices'][0]['message']['content']
        # 去掉markdown包裹
        content = content.replace('```json', '').replace('```', '').strip()
        return json.loads(content)
    except json.JSONDecodeError as e:
        log(f'[ERR] GPT返回非JSON: {e}')
        return None
    except Exception as e:
        log(f'[ERR] GPT调用异常: {e}')
        return None

def sync_shared_config(decision):
    """写入shared_config供策略热加载"""
    if not decision:
        return False
    
    config = {
        'updated_at': datetime.now().isoformat(),
        'market_assessment': decision.get('market_adjustment', 'unknown'),
        'risk_control': decision.get('risk_control', {}),
        'strategies': decision.get('params', {}),
    }
    
    config_file = LOGS / 'shared_config.json'
    json.dump(config, open(config_file, 'w'), indent=2)
    log(f'[CONFIG] shared_config已更新 -> {config_file}')
    
    # 也写advisory给其他系统
    advisory = {
        'time': datetime.now().isoformat(),
        'regime': 'bear' if 'bear' in decision.get('market_adjustment','').lower() 
                  else 'bull' if 'bull' in decision.get('market_adjustment','').lower() 
                  else 'sideways',
        'confidence': 0.7,
        'advice': decision.get('risk_control', {}).get('urgent_advice', ''),
        'override_tp': None,
        'override_sl': None,
        'pause': False
    }
    json.dump(advisory, open(LOGS / 'advisory.json', 'w'), indent=2)
    log('[ADVISORY] 已同步')
    return True

def read_strategy_performance():
    """从memory读取策略性能数据"""
    perf_file = MEMORY / 'strategy_performance.json'
    if perf_file.exists():
        try:
            return json.load(open(perf_file))
        except:
            pass
    return {'strategies': {}}

def record_cycle(gep_signals, decision, status):
    """记录进化周期到history"""
    history_file = LOGS / 'evolution_history.json'
    history = []
    if history_file.exists():
        try:
            history = json.load(open(history_file))
        except:
            pass
    
    record = {
        'time': datetime.now().isoformat(),
        'cycle': len(history) + 1,
        'gep_signals': gep_signals,
        'decision_summary': {
            'market': decision.get('market_adjustment', '?') if decision else 'failed',
            'actions': {k: v.get('action','?') for k,v in (decision or {}).get('params',{}).items()} if decision else {}
        },
        'status': status
    }
    history.append(record)
    if len(history) > 365:
        history = history[-365:]
    json.dump(history, open(history_file, 'w'), indent=2)

if __name__ == '__main__':
    log('=== GEP进化桥 启动 ===')
    
    # Step 1: 先跑memory写入
    log('[1/5] 写入memory数据...')
    r = subprocess.run(['bash', str(SCRIPTS / 'write_memory.sh')], 
                       capture_output=True, text=True, timeout=30)
    log(f'  write_memory: {r.stdout.strip()[-80:]}')
    
    # Step 2: 运行evolver
    log('[2/5] 运行evolver...')
    evolver_output = run_evolver()
    if not evolver_output:
        log('[2/5] evolver无输出,跳过GEP分析')
        record_cycle([], None, 'evolver_failed')
        sys.exit(1)
    
    # Step 3: 解析GEP信号
    log('[3/5] 解析GEP信号...')
    signals = parse_gep_signals(evolver_output)
    log(f'  GEP信号: {signals}')
    
    # Step 4: GPT解读GEP + 生成参数
    log('[4/5] GPT分析GEP信号并生成策略参数...')
    perf = read_strategy_performance()
    prompt = create_gep_prompt(signals, perf)
    decision = call_gpt(prompt)
    
    if decision:
        log(f'  GPT决策: 市场={decision.get("market_adjustment","?")}')
        sync_shared_config(decision)
        record_cycle(signals, decision, 'success')
    else:
        log('[4/5] GPT分析失败,保持现有参数')
        record_cycle(signals, None, 'gpt_failed')
    
    # Step 5: Git提交当前状态
    log('[5/5] Git提交...')
    try:
        subprocess.run(['git', 'add', '-A'], cwd=str(BASE), capture_output=True, timeout=10)
        subprocess.run(['git', 'commit', '-m', f'[AUTO] GEP进化周期 #{len(json.load(open(LOGS/"evolution_history.json")))}'], 
                      cwd=str(BASE), capture_output=True, timeout=10)
        subprocess.run(['git', 'push'], cwd=str(BASE), capture_output=True, timeout=30)
        log('  Git提交完成')
    except Exception as e:
        log(f'  Git提交跳过: {e}')
    
    log('=== GEP进化桥 完成 ===')
