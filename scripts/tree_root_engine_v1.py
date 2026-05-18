#!/usr/bin/env python3
"""
暗黑星火 · 全托管进化树 核心调度器 v1
=====================================
DS-0 Root: 永远在线的DeepSeek V4 Flash
- 模型: deepseek-v4-flash (DeepSeek官方)
- 30秒心跳循环
- 数据流: 读income_30d → 分析 → 写commands → 执行
- 深度分析唤醒GPT

架构:
  Root: DS-0 Flash (调度·心跳·协调)
  ├── 数据层: 哈基米GPT/1314普通模型 (数据提取·市场扫描)
  ├── 策略层: GPT-5+Claude (K线分析·回测·参数调优)
  └── 执行层: DS-0 Flash直接调用交易所 (网格·合约)
      └── 进化引擎: 混合模型 (绩效评估→策略变异)
"""
import sys, os, json, time, hmac, hashlib
from datetime import datetime, timezone

sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt

_CREDS = _decrypt()
API_KEY = _CREDS.get('BINANCE_API_KEY', '')
API_SECRET = _CREDS.get('BINANCE_API_SECRET', '')

BASE = '/home/admin/charon'
ANALYSIS = f'{BASE}/analysis'
STATE = f'{BASE}/state'
os.makedirs(ANALYSIS, exist_ok=True)
os.makedirs(STATE, exist_ok=True)

# ================================================================
# 工具函数
# ================================================================
def log(msg):
    ts = datetime.now(timezone.utc).strftime('%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}')

def sign(params):
    qs = '&'.join([f'{k}={v}' for k,v in sorted(params.items())])
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def api_get(path, params):
    ts = int(time.time()*1000)
    params.update({'timestamp': str(ts), 'recvWindow': '10000'})
    qs = '&'.join([f'{k}={v}' for k,v in sorted(params.items())])
    sig = sign(dict(sorted(params.items())))
    url = f'https://fapi.binance.com{path}?{qs}&signature={sig}'
    try:
        r = __import__('requests').get(url, headers={'X-MBX-APIKEY': API_KEY}, timeout=15)
        return r.json()
    except Exception as e:
        return {'error': str(e)}

# ================================================================
# 数据层 (Model: 哈基米GPT/1314普通)
# ================================================================
def refresh_income_data():
    """拉取最新income数据 (最近30天)"""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 30*24*3600*1000
    
    all_income = []
    cursor = start_ms
    while cursor < now_ms:
        data = api_get('/fapi/v1/income', {
            'startTime': str(int(cursor)),
            'endTime': str(now_ms),
            'limit': '1000',
        })
        if isinstance(data, dict) and ('code' in data or 'error' in data):
            log(f'API错误: {data.get("msg",data.get("error","?"))[:60]}')
            break
        if not data:
            break
        all_income.extend(data)
        cursor = int(data[-1]['time']) + 1
        if len(data) < 1000:
            break
        time.sleep(0.1)
    
    # 保存
    out = f'{ANALYSIS}/income_raw_{datetime.now(timezone.utc).strftime("%m%d")}.json'
    with open(out, 'w') as f:
        json.dump(all_income, f)
    log(f'Income: {len(all_income)}条 → {out}')
    return all_income

def analyze_income(data):
    """分析income数据 → 输出绩效报告"""
    total_pnl = sum(float(i['income']) for i in data if i['incomeType']=='REALIZED_PNL')
    total_fee = sum(float(i['income']) for i in data if i['incomeType']=='COMMISSION')
    total_funding = sum(float(i['income']) for i in data if i['incomeType']=='FUNDING_FEE')
    
    coins = {}
    for i in data:
        sym = i.get('symbol','N/A')
        it = i['incomeType']
        amt = float(i['income'])
        if sym not in coins:
            coins[sym] = {'pnl':0,'fee':0,'trades':0}
        if it == 'REALIZED_PNL': coins[sym]['pnl'] += amt
        elif it == 'COMMISSION': coins[sym]['fee'] += amt
        coins[sym]['trades'] += 1
    
    # 分类: 赚钱币/亏钱币/手续费黑洞
    winners = [(s,v) for s,v in coins.items() if v['pnl'] > 1.0]
    losers = [(s,v) for s,v in coins.items() if v['pnl'] < -1.0]
    fee_hogs = [(s,v) for s,v in coins.items() if v['fee'] > 3.0 and v['pnl'] < v['fee']]
    
    report = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'total_pnl': round(total_pnl,2),
        'total_fee': round(total_fee,2),
        'total_funding': round(total_funding,2),
        'net': round(total_pnl+total_fee+total_funding,2),
        'coins_total': len(coins),
        'winners': sorted(winners, key=lambda x: x[1]['pnl'], reverse=True)[:10],
        'losers': sorted(losers, key=lambda x: x[1]['pnl'])[:10],
        'fee_hogs': sorted(fee_hogs, key=lambda x: x[1]['fee'], reverse=True)[:10],
    }
    
    with open(f'{ANALYSIS}/income_report.json', 'w') as f:
        json.dump(report, f, indent=2)
    return report

# ================================================================
# 策略层 (Model: GPT-5.x / Claude)
# ================================================================
def call_gpt_analysis(report):
    """唤醒GPT做深度K线/策略分析
    实际调用通过delegate_task或API,此处写分析框架"""
    if not report:
        return None
    
    # 构建分析输入
    context = {
        'pnl': report['total_pnl'],
        'fee': report['total_fee'],
        'net': report['net'],
        'winners': [{'sym':s,'pnl':v['pnl'],'fee':v['fee']} for s,v in report['winners'][:5]],
        'losers': [{'sym':s,'pnl':v['pnl'],'fee':v['fee']} for s,v in report['losers'][:5]],
        'fee_hogs': [{'sym':s,'pnl':v['pnl'],'fee':v['fee']} for s,v in report['fee_hogs'][:5]],
    }
    
    # 构造GPT prompt (模型切换点)
    prompt = f"""你是一个量化交易策略高级分析师。分析以下30天交易数据:

总PnL: ${context['pnl']}  总手续费: ${context['fee']}  净盈亏: ${context['net']}

赚钱币: {context['winners']}
亏钱币: {context['losers']}
手续费黑洞: {context['fee_hogs']}

请输出:
1. 哪些币应该永久拉黑 (亏钱+高手续费)
2. 哪些币值得加大投入 (赚钱+低手续费)
3. 策略调整建议 (频率/杠杆/方向)
4. 当前最应该立即执行的一个操作"""
    
    return prompt

# ================================================================
# 执行层 (Model: DS-0 Flash直接调用)
# ================================================================
def check_balance():
    """查合约余额"""
    data = api_get('/fapi/v2/account', {})
    if isinstance(data, dict) and 'totalWalletBalance' in data:
        return {
            'wallet': float(data['totalWalletBalance']),
            'margin': float(data['totalMarginBalance']),
            'pnl': float(data['totalUnrealizedProfit']),
        }
    return None

def check_positions():
    """查持仓"""
    data = api_get('/fapi/v2/positionRisk', {})
    if isinstance(data, list):
        return [p for p in data if float(p.get('positionAmt',0)) != 0]
    return []

# ================================================================
# 主循环 (Root: DS-0 Flash)
# ================================================================
def main_loop():
    log('🌲 暗黑星火进化树 Root启动')
    
    while True:
        loop_start = time.time()
        
        try:
            # 1. 查状态
            bal = check_balance()
            pos = check_positions()
            
            log(f'余额: ${bal["wallet"] if bal else "?"}  '
                f'持仓: {len(pos)}个  '
                f'时间: {datetime.now(timezone.utc).strftime("%H:%M")}')
            
            # 2. 每4h全量刷新一次income
            current_hour = datetime.now(timezone.utc).hour
            last_refresh_file = f'{STATE}/last_income_refresh.txt'
            should_refresh = False
            if current_hour % 4 == 0:
                if os.path.exists(last_refresh_file):
                    with open(last_refresh_file) as f:
                        last_h = int(f.read().strip())
                    should_refresh = current_hour != last_h
                else:
                    should_refresh = True
            
            if should_refresh:
                log('🔄 4h定时: 刷新income数据')
                income_data = refresh_income_data()
                report = analyze_income(income_data)
                with open(last_refresh_file, 'w') as f:
                    f.write(str(current_hour))
                
                # 3. 每4h唤醒一次GPT深度分析
                analysis_prompt = call_gpt_analysis(report)
                log(f'📊 GPT分析请求已就绪 (明天整合GPT API调用)')
            
            # 4. 简单的风控: 检查实时持仓是否爆仓风险
            for p in pos:
                pnl_pct = float(p.get('unRealizedProfit',0)) / max(float(p.get('isolatedWallet',0)), 0.01) * 100
                if pnl_pct < -70:
                    log(f'⚠️ {p["symbol"]} 亏损{pnl_pct:.0f}% 接近爆仓!')
            
        except Exception as e:
            log(f'❌ 循环错误: {e}')
        
        # 固定30秒间隔
        elapsed = time.time() - loop_start
        sleep_time = max(1, 30 - elapsed)
        time.sleep(sleep_time)

if __name__ == '__main__':
    main_loop()
