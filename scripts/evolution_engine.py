#!/usr/bin/env python3
"""
暗黑星火 · GPT进化引擎
=========================
全托管自动进化：数据采集 → GPT分析 → 参数优化 → shared_config热加载 → Git提交

每6h由cron触发。核心原则：能赚钱就是好系统。
"""
import os, json, sys, subprocess, requests
from datetime import datetime
from pathlib import Path

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
MEMORY = BASE / 'memory'
SCRIPTS = BASE / 'scripts'

DS_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
MODEL = 'deepseek-chat'

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOGS / 'evolution_engine.log', 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

# ── Step 1: 收集所有数据 ──
def collect_data():
    """收集币安5月真实数据 + 4虚拟盘状态 + 市场数据"""
    data = {'timestamp': datetime.now().isoformat()}
    
    # 币安5月真实交易
    try:
        bt = json.load(open(MEMORY / 'binance_may_trades.json'))
        data['binance_may'] = bt
    except: data['binance_may'] = None
    
    # 4虚拟盘状态
    strategies = {}
    for name in ['meanrevert_paper', 'rsi_meanrev_paper', 'combo31_paper', 'futures_paper']:
        f = LOGS / f'{name}_state.json'
        try:
            d = json.load(open(f))
            pos = d.get('position', {})
            if isinstance(pos, dict):
                strategies[name] = {
                    'cash': d.get('cash', 1000),
                    'pnl': d.get('pnl', 0),
                    'trades': d.get('trades', 0),
                    'fees': d.get('fees_paid', 0),
                    'position_qty': pos.get('qty', 0),
                    'position_price': pos.get('price', 0),
                }
        except: pass
    data['strategies'] = strategies
    
    # 当前shared_config
    try:
        sc = json.load(open(LOGS / 'shared_config.json'))
        data['current_config'] = sc.get('strategies', {})
    except: data['current_config'] = {}
    
    return data

# ── Step 2: GPT决策 ──
def gpt_evolve(data):
    """GPT读所有数据，输出参数优化方案"""
    if not DS_KEY:
        log('[ERR] DEEPSEEK_API_KEY 未设置')
        return None
    
    # 构建prompt
    perf_summary = ""
    for name, s in data.get('strategies', {}).items():
        if s.get('trades', 0) > 0:
            perf_summary += f"\n  {name}: PnL=${s['pnl']:+.2f} | 交易={s['trades']}笔 | 资金=${s['cash']:.0f}"
    
    binance_summary = ""
    bt = data.get('binance_may')
    if bt:
        binance_summary = f"""
5月币安真实交易:
  总交易: {bt['total_trades']}笔
  总成交量: ${bt['total_volume']:,.0f}
  总手续费: ${bt['total_fees']} ← 这是确定亏损
  交易币种: {bt['symbols_traded']}个
高手续费币种:"""
        for s in bt.get('high_fee_symbols', [])[:5]:
            binance_summary += f"\n  {s['symbol']}: {s['trades']}笔 手续费${s['fees']}"
    else:
        binance_summary = "\n  暂无币安数据"
    
    prompt = f"""你是暗黑星火资本CEO，全托管模式。唯一目标：赚钱。

=== 4虚拟盘状态 ===
{perf_summary if perf_summary else '  全部idle, 0交易'}

=== 币安5月真实历史 ===
{binance_summary}

=== 核心教训(历史证明) ===
- 用户自己操作=赚钱(BTC+$92, AVNT+$20)
- AI自作主张=全亏(AIGENSYN -$241, ETH -$92)
- 只做主流币(BTC/ETH/SOL/DOGE), 不做小币(AIGENSYN永久黑名单)
- 手续费$80+是确定亏损, 高频交易=烧钱
- 熊市只做空不做多
- 日亏$5熔断

你的任务: 为4个策略生成下一周期的参数。
如果策略0交易0PnL, 保持hold等信号。
如果策略持续亏损, 缩小仓位或暂停。

只输出以下JSON, 不要markdown包裹:
{{{{
  "market": "bullish/bearish/sideways",
  "strategies": {{{{
    "meanrevert_paper": {{{{
      "active": true/false,
      "rsi_oversold": 数值(当前20),
      "rsi_overbought": 数值(当前55),
      "position_pct": 浮点数(当前0.3),
      "action": "hold/open/close"
    }}}},
    "rsi_meanrev_paper": {{{{
      "active": true/false,
      "rsi_oversold": 数值(当前30),
      "rsi_overbought": 数值(当前70),
      "position_pct": 浮点数(当前0.2),
      "action": "hold/open/close"
    }}}},
    "combo31_paper": {{{{
      "active": true/false,
      "leverage": 整数(当前5),
      "position_pct": 浮点数(当前0.2),
      "action": "hold/open/close"
    }}}},
    "futures_paper": {{{{
      "active": true/false,
      "leverage": 整数(当前5),
      "position_pct": 浮点数(当前0.4),
      "action": "hold/open/close"
    }}}}
  }}}},
  "risk": {{{{
    "daily_loss_limit": 浮点数(当前5.0),
    "max_open_positions": 整数(当前2),
    "advice": "一句话建议"
  }}}}
}}}}
"""
    try:
        r = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            json={
                'model': MODEL,
                'messages': [
                    {'role': 'system', 'content': '你是暗黑星火资本CEO。全托管，盈利唯一目标。输出严格JSON。'},
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': 2000,
                'temperature': 0.3
            },
            headers={'Authorization': f'Bearer {DS_KEY}'},
            timeout=90
        )
        if r.status_code != 200:
            log(f'[ERR] HTTP {r.status_code}')
            return None
        content = r.json()['choices'][0]['message']['content']
        content = content.replace('```json', '').replace('```', '').strip()
        return json.loads(content)
    except Exception as e:
        log(f'[ERR] GPT: {e}')
        return None

# ── Step 3: 写配置 ──
def apply_decision(decision):
    """写shared_config + advisory"""
    if not decision:
        return False
    
    config = {
        'updated_at': datetime.now().isoformat(),
        'market_assessment': decision.get('market', 'unknown'),
        'risk_control': {
            'daily_loss_limit': decision.get('risk', {}).get('daily_loss_limit', 5.0),
            'max_positions': decision.get('risk', {}).get('max_open_positions', 2),
            'urgent_advice': decision.get('risk', {}).get('advice', ''),
        },
        'strategies': decision.get('strategies', {}),
        'version': 3
    }
    json.dump(config, open(LOGS / 'shared_config.json', 'w'), indent=2)
    log(f'[CONFIG] 已更新: 市场={config["market_assessment"]}')
    
    advisory = {
        'time': datetime.now().isoformat(),
        'regime': 'bear' if 'bear' in decision.get('market','').lower() 
                  else 'bull' if 'bull' in decision.get('market','').lower() 
                  else 'sideways',
        'advice': decision.get('risk', {}).get('advice', ''),
        'pause': any(not s.get('active', True) for s in decision.get('strategies', {}).values())
    }
    json.dump(advisory, open(LOGS / 'advisory.json', 'w'), indent=2)
    log(f'[ADVISORY] 已同步')
    return True

# ── 主流程 ──
if __name__ == '__main__':
    log('=== GPT进化引擎 启动 ===')
    
    # Step 1: 写memory
    log('[1/4] 采集数据...')
    subprocess.run(['bash', str(SCRIPTS / 'write_memory.sh')], 
                   capture_output=True, timeout=30)
    
    # Step 2: 收集所有数据
    data = collect_data()
    ns = len(data.get('strategies', {}))
    nt = data.get('binance_may', {}).get('total_trades', 0)
    log(f'  {ns}个策略状态, {nt}笔币安历史交易')
    
    # Step 3: GPT进化决策
    log('[2/4] GPT分析+决策...')
    decision = gpt_evolve(data)
    if not decision:
        log('[ERR] GPT决策失败, 保持现有参数')
        sys.exit(1)
    
    mkt = decision.get('market', '?')
    actions = {k: v.get('action', '?') for k, v in decision.get('strategies', {}).items()}
    log(f'  市场={mkt} | 动作={actions}')
    log(f'  建议: {decision.get("risk", {}).get("advice", "无")[:80]}')
    
    # Step 4: 应用配置
    log('[3/4] 更新配置...')
    apply_decision(decision)
    
    # Step 5: Git提交
    log('[4/4] Git提交...')
    try:
        subprocess.run(['git', 'add', '-A'], cwd=str(BASE), capture_output=True, timeout=10)
        subprocess.run(['git', 'commit', '-m', f'[EVO] GPT进化: {mkt} {actions}'], 
                      cwd=str(BASE), capture_output=True, timeout=10)
        subprocess.run(['git', 'push'], cwd=str(BASE), capture_output=True, timeout=30)
        log('  Git完成')
    except Exception as e:
        log(f'  Git跳过: {e}')
    
    log('=== 完成 ===')
