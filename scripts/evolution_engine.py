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
    
    # 硬规则周期判断（不交给GPT）
    prices = data.get('market_prices', {})
    btc_price = prices.get('BTC/USDT', {}).get('last', 0)
    btc_chg = prices.get('BTC/USDT', {}).get('change', 0) or 0
    if btc_chg < -2: hard_regime = 'bearish'
    elif btc_chg > 2: hard_regime = 'bullish'
    else: hard_regime = 'sideways'

    prompt = f"""你是暗黑星火资本CEO。赚钱活下去是你的本能。禁止犹豫、禁止hold、禁止空仓等死。

=== 硬规则周期判断（不可改变）===
BTC 24h变化: {btc_chg:+.2f}%
当前周期: {hard_regime.upper()}
→ 周期决定方向：bearish必须做空 || bullish必须做多 || sideways可做空也可做多

=== 虚拟盘状态 ===
{perf_summary if perf_summary else '全部idle'}

=== 币安历史 ===
{binance_summary}

=== 当前价格 ===
{json.dumps(prices, indent=2)}

你的任务：
1. 周期={hard_regime} → 方向已锁定，不能选hold/观望
2. 为4个虚拟盘调参数（RSI阈值/仓位比例/止损），让它们按方向交易
3. 如果有合适机会，给$50合约执行器下单指令
4. 如果行情不适合开仓，就设更严格的参数等机会，但不能hold不动

记住：不交易就没有利润数据，没有数据就无法进化。hold是慢性死亡。

输出JSON格式：
{{{{"market": "{hard_regime}(你的分析理由)",
  "strategies": {{{{
    "meanrevert_paper": {{{{"rsi_oversold": 整数10-30,"rsi_overbought": 整数50-80,"position_pct": 浮点数0.1-0.5,"action": "open/close"}}}},
    "rsi_meanrev_paper": {{{{"rsi_oversold": 整数10-30,"rsi_overbought": 整数50-80,"position_pct": 浮点数0.1-0.5,"action": "open/close"}}}},
    "combo31_paper": {{{{"leverage": 整数1-5,"position_pct": 浮点数0.1-0.5,"action": "open/close"}}}},
    "futures_paper": {{{{"leverage": 整数1-5,"position_pct": 浮点数0.1-0.5,"action": "open/close"}}}}
  }}}},
  "spot_execution": {{{{
    "active": true/false,
    "orders": [
      {{{{ "symbol": "ETH/USDT", "buy_below": 价格, "sell_at": 价格, "allocation": USDT数额 }}}},
      {{{{ "symbol": "SOL/USDT", "buy_below": 价格, "sell_at": 价格, "allocation": USDT数额 }}}},
      {{{{ "symbol": "DOGE/USDT", "buy_below": 价格, "sell_at": 价格, "allocation": USDT数额 }}}}
    ]
  }}}},
  "contract": {{{{
    "active": true/false,
    "symbol": "币种/USDT",
    "direction": "long/short/none",
    "leverage": 5-10,
    "margin_usdt": 整数10-50,
    "stop_loss_price": 止损价,
    "take_profit_price": 止盈价,
    "reason": "决策理由"
  }}}},
  "risk": {{{{
    "daily_loss_limit": 浮点数3-10,
    "max_positions": 整数1-3,
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
def apply_decision(decision, hard_regime='unknown'):
    """写shared_config + advisory，方向由硬规则强制"""
    if not decision:
        return False
    
    config = {
        'updated_at': datetime.now().isoformat(),
        'market_assessment': f"{hard_regime}: {decision.get('market', '')}",
        'regime': hard_regime,  # 硬规则，不依赖GPT文本
        'risk_control': {
            'daily_loss_limit': decision.get('risk', {}).get('daily_loss_limit', 5.0),
            'max_positions': decision.get('risk', {}).get('max_positions', 2),
            'urgent_advice': decision.get('risk', {}).get('advice', ''),
        },
        'strategies': decision.get('strategies', {}),
        'spot_execution': decision.get('spot_execution', {}),
        'contract': decision.get('contract', {}),
        'version': 4  # bumped: hard regime enforcement
    }
    json.dump(config, open(LOGS / 'shared_config.json', 'w'), indent=2)
    log(f'[CONFIG] 已更新: 市场={config["market_assessment"]}')
    
    advisory = {
        'time': datetime.now().isoformat(),
        'regime': hard_regime,  # 硬规则
        'advice': decision.get('risk', {}).get('advice', ''),
        'pause': False
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
    
    # 硬规则周期判断
    prices = data.get('market_prices', {})
    btc_chg = prices.get('BTC/USDT', {}).get('change', 0) or 0
    if btc_chg < -2: hard_regime = 'bearish'
    elif btc_chg > 2: hard_regime = 'bullish'
    else: hard_regime = 'sideways'
    log(f'  BTC 24h={btc_chg:+.2f}% → 硬规则周期={hard_regime.upper()}')
    
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
    apply_decision(decision, hard_regime)
    
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
