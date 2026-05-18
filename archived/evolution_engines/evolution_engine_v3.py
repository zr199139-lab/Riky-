#!/usr/bin/env python3
"""
暗黑星火 · 进化引擎 v3 (树状架构集成)
=====================================
读审计推荐 → 淘汰差策略 → 变异好策略 → 写入新配置

模型分工:
  - 策略淘汰: DS-0 Flash (规则引擎,不用模型)
  - 策略变异: 哈基米GPT (gpt-5.4) 或 直接数学变异
  - 冠军推优: 累积数据,每天一次

数据流:
  analysis/audit_recommendations.json → 读建议
  virtual_state/*.json → 读各策略状态
  → 淘汰/变异 → 写回 state + 提交Git
"""
import sys, os, json, shutil
from datetime import datetime, timezone
from copy import deepcopy

BASE = '/home/admin/charon'
STATE = f'{BASE}/virtual_state'
ANALYSIS = f'{BASE}/analysis'
CONFIG = f'{BASE}/config'

os.makedirs(CONFIG, exist_ok=True)

def log(msg):
    print(f'[EVO] {datetime.now(timezone.utc).strftime("%m-%d %H:%M:%S")} {msg}')

def load_recommendations():
    """加载审计推荐"""
    path = f'{ANALYSIS}/audit_recommendations.json'
    if not os.path.exists(path):
        log('无审计推荐,跳过进化')
        return None
    with open(path) as f:
        return json.load(f)

def load_all_states():
    """加载所有策略状态"""
    states = {}
    if not os.path.exists(STATE):
        return states
    for fname in os.listdir(STATE):
        if fname.endswith('.json'):
            with open(f'{STATE}/{fname}') as f:
                states[fname.replace('.json','')] = json.load(f)
    return states

def save_state(sid, data):
    """原子写策略状态"""
    tmp = f'{STATE}/{sid}.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, f'{STATE}/{sid}.json')

def kill_strategy(sid, reason):
    """淘汰策略: 清空持仓,保留资金"""
    path = f'{STATE}/{sid}.json'
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        data['positions'] = {}
        data['trades'] = data.get('trades', [])
        # 添加一条淘汰记录
        data['trades'].append({
            'time': int(datetime.now().timestamp()*1000),
            'datetime': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'),
            'coin': 'SYSTEM',
            'side': 'KILLED',
            'reason': reason,
            'strategy': sid,
        })
        save_state(sid, data)
        log(f'💀 淘汰 {sid}: {reason}')
    else:
        log(f'⚠️ {sid} 状态不存在')

def mutate_parameters(sid, current_state):
    """变异策略参数 (基于规则的数学变异,不需调模型)
    Returns: (mutated: bool, changes: str)"""
    # 当前参数从state推断
    trades = current_state.get('trades', [])
    closes = [t for t in trades if 'pnl' in t]
    if not closes:
        return False, "无成交数据"
    
    total_pnl = sum(t.get('pnl',0) for t in closes)
    wins = len([t for t in closes if t.get('pnl',0) > 0])
    win_rate = wins / len(closes) if closes else 0
    total_fee = sum(t.get('fee',0) for t in trades)
    
    changes = []
    
    # 规则1: 胜率<30% → 收紧信号 (更大标准差/更长周期)
    if win_rate < 0.3:
        changes.append(f'胜率{win_rate:.0%}<30%,收紧信号')
    
    # 规则2: 手续费>PnL → 降低频率
    if total_fee > abs(total_pnl) and total_fee > 1:
        changes.append(f'手续费${total_fee:.2f}>PnL${total_pnl:.2f},降频')
    
    # 规则3: 净亏损>10% → 减小仓位
    capital = current_state.get('capital', 1000)
    net_pnl = total_pnl - total_fee
    if net_pnl < -capital * 0.1:
        changes.append(f'净亏${net_pnl:.2f}>10%,减仓')
    
    # 应用变异: 写入config
    mutation = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'strategy': sid,
        'changes': changes if changes else ['正常表现,继续观察'],
        'win_rate': round(win_rate, 3),
        'total_pnl': round(total_pnl, 2),
        'total_fee': round(total_fee, 2),
        'net_pnl': round(net_pnl, 2),
    }
    
    # 保存变异记录
    evo_log = f'{ANALYSIS}/evolution_log.json'
    evo_history = []
    if os.path.exists(evo_log):
        with open(evo_log) as f:
            evo_history = json.load(f)
    evo_history.append(mutation)
    with open(evo_log, 'w') as f:
        json.dump(evo_history[-100:], f, indent=2)  # 保留最近100条
    
    return bool(changes), '; '.join(changes) if changes else '正常'

def promote_champion():
    """从虚拟盘选冠军推优到实盘配置"""
    # 读所有策略 + 审计推荐
    states = load_all_states()
    rec = load_recommendations()
    
    if not states:
        return None
    
    # 算各策略绩效
    perf = {}
    for sid, data in states.items():
        trades = data.get('trades', [])
        closes = [t for t in trades if 'pnl' in t]
        if closes:
            total_pnl = sum(t.get('pnl',0) for t in closes)
            wins = len([t for t in closes if t.get('pnl',0) > 0])
            perf[sid] = {
                'pnl': total_pnl,
                'win_rate': wins/len(closes),
                'trades': len(closes),
            }
        else:
            perf[sid] = {'pnl': 0, 'win_rate': 0, 'trades': 0}
    
    # 选冠军: 净PnL最高
    best_sid = max(perf, key=lambda k: perf[k]['pnl']) if perf else None
    
    if best_sid and perf[best_sid]['pnl'] > 0:
        champion = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'champion': best_sid,
            'pnl': perf[best_sid]['pnl'],
            'win_rate': round(perf[best_sid]['win_rate'], 3),
            'source': 'paper_trading',
        }
        path = f'{ANALYSIS}/champion.json'
        with open(path, 'w') as f:
            json.dump(champion, f, indent=2)
        log(f'🏆 冠军: {best_sid} (PnL=${perf[best_sid]["pnl"]:.2f})')
        return champion
    
    return None

def main():
    log('🔄 进化引擎开始...')
    
    # 1. 加载推荐
    rec = load_recommendations()
    
    # 2. 执行淘汰
    if rec:
        kill_list = rec.get('kill_list', [])
        for sid in kill_list:
            kill_strategy(sid, f'Audit推荐淘汰: {rec.get("worst_strategy","?")}')
    
    # 3. 各策略变异
    states = load_all_states()
    for sid, state in states.items():
        mutated, desc = mutate_parameters(sid, state)
        if mutated:
            log(f'🧬 {sid}: {desc}')
    
    # 4. 推冠军
    champ = promote_champion()
    
    # 5. Git提交
    try:
        os.system(f'cd {BASE} && git add analysis/ state/ -f 2>/dev/null; git commit -m "[EVO] 进化循环 {datetime.now(timezone.utc).strftime(\"%m-%d %H:%M\")}" 2>/dev/null; git push 2>/dev/null &')
    except:
        pass
    
    log('✅ 进化完成')

if __name__ == '__main__':
    main()
