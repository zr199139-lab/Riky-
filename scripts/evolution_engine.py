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
MODEL = 'deepseek-reasoner'  # Pro版, 更强推理, 不限制Token

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
    
    # 当前市场价格（喂给GPT算买入/卖出价）
    try:
        import ccxt
        ex = ccxt.binance({'enableRateLimit': True})
        prices = {}
        for sym in ['ETH/USDT', 'DOGE/USDT', 'SOL/USDT', 'BTC/USDT']:
            try:
                t = ex.fetch_ticker(sym)
                prices[sym] = {'last': t['last'], 'high_24h': t['high'], 'low_24h': t['low'], 'change': t['percentage']}
            except: pass
        data['market_prices'] = prices
    except: data['market_prices'] = {}
    
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
    
    prompt = f"""你是暗黑星火资本CEO，全托管模式。铁律只有一条：赚钱。其他都是扯淡。

=== 4虚拟盘状态 ===
{perf_summary if perf_summary else '  全部idle, 0交易'}

=== 币安5月真实历史 ===
{binance_summary}

=== 当前市场价格(用于计算买入卖出价) ===
{json.dumps(data.get('market_prices', {}), indent=2)}

=== 历史教训(喂给进化用) ===
- 用户自己操作=赚钱(BTC+$92, AVNT+$20, RUNE+$17)
- AI自作主张=全亏(5月净-$462, AIGENSYN单币亏-$241)
- 手续费$80是确定亏损, 高频交易=慢性自杀
- 熊市做空赚钱, 做多亏钱
- 主流币(BTC/ETH/SOL/DOGE)有流动性, 小币一买就套

|你的任务: 分析所有数据, 为4个虚拟盘、$250现货执行器和$50合约执行器生成最优参数。
不要保守。有信号就干, 没信号就等。
要赌就赌大的, 但不赌就是最稳的赚。
美国时间周一到周五波动最大。

$250现货执行器说明:
- 资金: $250 USDT 币安现货账户
- 选币: ETH/DOGE/SOL 三个主流币
- 策略: 挂限价单等深度回调(低于现价8-15%), 等明显反弹再出
- 不是网格, 低频, 每单目标赚$10-25
- 空仓时可挂2-3个限价单等触发
- 持仓后等反弹到目标出, 然后重新挂单

$50合约执行器说明(新增):
- 资金: $50 USDT 币安合约账户
- 方向: 熊市做空为主(历史验证), 牛市做多
- 杠杆: 5-10x, $50×10x=$500名义
- 单笔止损: -$3(本金的6%)
- 日亏上限: -$10(本金的20%)
- 止盈: +$5-15/单
- 只做ETH/BTC/SOL, 不做小币
- 不扛单, 到止损就砍

输出JSON格式:
{{{{"market": "bullish/bearish/sideways(一句话理由)",
  "strategies": {{{{
    "meanrevert_paper": {{{{"active": true/false,"rsi_oversold": 整数,"rsi_overbought": 整数,"position_pct": 浮点数,"action": "hold/open/close"}}}},
    "rsi_meanrev_paper": {{{{"active": true/false,"rsi_oversold": 整数,"rsi_overbought": 整数,"position_pct": 浮点数,"action": "hold/open/close"}}}},
    "combo31_paper": {{{{"active": true/false,"leverage": 整数,"position_pct": 浮点数,"action": "hold/open/close"}}}},
    "futures_paper": {{{{"active": true/false,"leverage": 整数,"position_pct": 浮点数,"action": "hold/open/close"}}}}
  }}}},
  "spot_execution": {{{{
    "active": true/false,
    "budget_usdt": 250,
    "orders": [
      {{{{ "symbol": "ETH/USDT", "buy_below": 价格, "sell_at": 价格, "allocation": USDT数额 }}}},
      {{{{ "symbol": "DOGE/USDT", "buy_below": 价格, "sell_at": 价格, "allocation": USDT数额 }}}},
      {{{{ "symbol": "SOL/USDT", "buy_below": 价格, "sell_at": 价格, "allocation": USDT数额 }}}}
    ]
  }}}},
  "contract": {{{{
    "active": true/false,
    "symbol": "币种/USDT",
    "direction": "long/short/none",
    "leverage": 整数5-10,
    "entry_type": "market/limit",
    "entry_price": 入场价格限价时用,
    "stop_loss_price": 止损价,
    "take_profit_price": 止盈价,
    "margin_usdt": 保证金数额(最大50),
    "reason": "一句话决策理由"
  }}}},
  "risk": {{{{
    "daily_loss_limit": 浮点数,
    "max_open_positions": 整数,
    "advice": "一句话建议"
    }}}}
}}
"""
    try:
        r = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            json={
                'model': MODEL,
                'messages': [
                    {'role': 'system', 'content': '你是暗黑星火资本CEO。全托管, 盈利是唯一真理。敢做敢当, 没信号就等, 有信号就干。输出严格JSON。'},
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
        'spot_execution': decision.get('spot_execution', {}),
        'contract': decision.get('contract', {}),
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
