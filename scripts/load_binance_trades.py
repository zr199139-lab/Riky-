#!/usr/bin/env python3
"""
币安5月交易记录 → evolver memory 格式化管道 v2
分段查询(按天)+只扫描有成交的币种
"""
import os, json, time, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path('/home/admin/charon')
MEMORY = BASE / 'memory'
LOGS = BASE / 'bot_logs'
SECURE_DIR = Path.home() / '.hermes/mempalace/secure'

sys.path.insert(0, str(SECURE_DIR))
from decrypt_and_run import decrypt
import ccxt

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}', flush=True)
    with open(LOGS / 'binance_history_loader.log', 'a') as f:
        f.write(f'[{t}] {msg}\n')

def get_exchange():
    creds = decrypt()
    api_key = creds.get('BINANCE_API_KEY', '')
    api_secret = creds.get('BINANCE_API_SECRET', '')
    if not api_key or not api_secret:
        log('[ERR] Key未找到')
        return None
    ex = ccxt.binance({
        'apiKey': api_key, 'secret': api_secret,
        'enableRateLimit': True, 'options': {'defaultType': 'spot'}
    })
    ex.load_markets()
    return ex

def get_traded_symbols(ex):
    """从余额中找出用户实际交易过的币种"""
    b = ex.fetch_balance()
    # 所有有余额或有挂单的币
    coins = set()
    for k, v in b.get('total', {}).items():
        if float(v) > 0:
            coins.add(k)
    for k, v in b.get('free', {}).items():
        if float(v) > 0:
            coins.add(k)
    
    symbols = []
    for c in coins:
        if c != 'USDT':
            symbols.append(f'{c}/USDT')
    
    # 再加一些可能交易过但已清仓的主流币
    extra = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'XRP/USDT',
             'BNB/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'DOT/USDT']
    for e in extra:
        if e.replace('/USDT','') not in coins:
            symbols.append(e)
    
    log(f'要查询的交易对: {len(symbols)}个 (含余额币+主流币)')
    return list(set(symbols))

def fetch_trades_by_day(ex, symbol, start_day, end_day):
    """按天分段查询，避免-1127错误(最大24h)"""
    all_trades = []
    current = start_day
    while current < end_day:
        next_day = current + timedelta(days=1)
        start_ms = int(current.timestamp() * 1000)
        end_ms = int(next_day.timestamp() * 1000)
        try:
            trades = ex.fetch_my_trades(symbol, limit=1000, params={
                'startTime': start_ms, 'endTime': end_ms
            })
            if trades:
                all_trades.extend(trades)
        except Exception as e:
            msg = str(e)
            if 'no trade' in msg.lower() or 'not found' in str(msg):
                pass  # 正常跳过
            else:
                log(f'  {symbol}@{current.date()}: {msg[:50]}')
        time.sleep(0.25)
        current = next_day
    return all_trades

def analyze_trades(trades):
    """分析交易记录"""
    if not trades:
        return None
    
    # 按币种汇总
    by_symbol = {}
    total_fees = 0.0
    total_volume = 0.0
    
    for t in trades:
        sym = t.get('symbol', 'unknown')
        fee_cost = float(t.get('fee', {}).get('cost', 0))
        cost = float(t.get('cost', 0))
        side = t.get('side', '')
        
        if sym not in by_symbol:
            by_symbol[sym] = {'trades': 0, 'fees': 0.0, 'buy_vol': 0, 'sell_vol': 0,
                             'buys': 0, 'sells': 0, 'total_vol': 0}
        by_symbol[sym]['trades'] += 1
        by_symbol[sym]['fees'] += fee_cost
        by_symbol[sym]['total_vol'] += cost
        if side == 'buy': 
            by_symbol[sym]['buys'] += 1
            by_symbol[sym]['buy_vol'] += cost
        else:
            by_symbol[sym]['sells'] += 1
            by_symbol[sym]['sell_vol'] += cost
        
        total_fees += fee_cost
        total_volume += cost
    
    # 找问题币种
    high_fee = sorted([(s, d) for s, d in by_symbol.items() if d['fees'] > 1],
                     key=lambda x: x[1]['fees'], reverse=True)
    most_traded = sorted(by_symbol.items(), key=lambda x: x[1]['trades'], reverse=True)[:10]
    
    return {
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'total_trades': len(trades),
        'total_fees': round(total_fees, 2),
        'total_volume': round(total_volume, 2),
        'symbols': len(by_symbol),
        'high_fee_symbols': [{'symbol': s, 'fees': round(d['fees'], 2), 'trades': d['trades']} for s,d in high_fee],
        'most_traded': [{'symbol': s, 'trades': d['trades'], 'fees': round(d['fees'], 2)} for s,d in most_traded],
    }

def write_evolver_memory(analysis):
    """写入./memory/供evolver分析"""
    if not analysis:
        return
    
    # 1. 性能报告
    perf = {
        'timestamp': analysis['timestamp'],
        'source': 'binance_may_real_trades',
        'period': '2026-05-01 ~ 2026-05-18',
        'total_trades': analysis['total_trades'],
        'total_fees': analysis['total_fees'],
        'total_volume': analysis['total_volume'],
        'symbols_traded': analysis['symbols'],
        'high_fee_symbols': analysis['high_fee_symbols']
    }
    json.dump(perf, open(MEMORY / 'binance_may_trades.json', 'w'), indent=2)
    log(f'[1] 报告写入: {analysis["total_trades"]}笔, 手续费${analysis["total_fees"]}')
    
    # 2. 运行时日志 (evolver扫描找信号)
    with open(MEMORY / 'logs' / f'binance_may_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log', 'w') as f:
        f.write(f'=== 币安5月真实交易数据 ===\n')
        f.write(f'总交易: {analysis["total_trades"]}笔\n')
        f.write(f'总成交量: ${analysis["total_volume"]:.0f}\n')
        f.write(f'总手续费: ${analysis["total_fees"]:.2f}\n')
        f.write(f'交易币种: {analysis["symbols"]}个\n\n')
        
        if analysis['total_fees'] > 50:
            f.write(f'[error] 手续费过高: ${analysis["total_fees"]}\n')
        if analysis['total_trades'] > 100:
            f.write(f'[warning] 过度交易: {analysis["total_trades"]}笔\n')
        
        f.write('\n高费用币种:\n')
        for s in analysis['high_fee_symbols']:
            if s['fees'] > 2:
                f.write(f'  [high_cost] {s["symbol"]}: 手续费${s["fees"]} 交易{s["trades"]}笔\n')
        
        f.write('\n最活跃币种:\n')
        for s in analysis['most_traded']:
            f.write(f'  {s["symbol"]}: {s["trades"]}笔, 手续费${s["fees"]}\n')
    log(f'[2] 日志已写入')
    
    # 3. 信号
    sig_file = MEMORY / 'signals' / 'current.json'
    existing = []
    if sig_file.exists():
        try: existing = json.load(open(sig_file))
        except: pass
    existing = [s for s in existing if not s.get('source', '').startswith('binance_')]
    
    signals = []
    if analysis['total_fees'] > 50:
        signals.append({'signal': 'error', 'source': 'binance_high_fees',
                       'value': analysis['total_fees'], 'desc': f'5月总手续费${analysis["total_fees"]}'})
    for s in analysis['high_fee_symbols'][:3]:
        signals.append({'signal': 'inefficient', 'source': f'binance_{s["symbol"]}',
                       'value': s['fees'], 'desc': f'{s["symbol"]}手续费${s["fees"]}'})
    if analysis['total_trades'] > 200:
        signals.append({'signal': 'overtrading', 'source': 'binance_overtrade',
                       'value': analysis['total_trades'], 'desc': f'{analysis["total_trades"]}笔交易'})
    
    existing.extend(signals)
    json.dump(existing, open(sig_file, 'w'), indent=2)
    log(f'[3] 信号写入: {len(signals)}条')
    
    # 4. 更新策略性能表
    perf_file = MEMORY / 'strategy_performance.json'
    sp = {'strategies': {}}
    if perf_file.exists():
        try: sp = json.load(open(perf_file))
        except: pass
    
    sp['strategies']['binance_may_2026'] = {
        'pnl': -analysis['total_fees'],
        'trades': analysis['total_trades'],
        'cash': 0,
        'fees_paid': analysis['total_fees'],
        'has_position': False,
        'updated_at': analysis['timestamp'],
        'source': '币安5月真实交易'
    }
    json.dump(sp, open(perf_file, 'w'), indent=2)
    log(f'[4] strategy_performance已更新')

if __name__ == '__main__':
    log('=== 币安5月交易加载 ===')
    
    ex = get_exchange()
    if not ex: sys.exit(1)
    
    symbols = get_traded_symbols(ex)
    
    # 5月1日 ~ 5月18日
    may1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    may18 = datetime(2026, 5, 18, tzinfo=timezone.utc)
    
    all_trades = []
    total_syms = len(symbols)
    for i, sym in enumerate(sorted(symbols)):
        trades = fetch_trades_by_day(ex, sym, may1, may18)
        if trades:
            all_trades.extend(trades)
            log(f'  [{i+1}/{total_syms}] {sym}: {len(trades)}笔')
        if (i+1) % 20 == 0:
            log(f'  进度: {i+1}/{total_syms}, 已获取{len(all_trades)}笔')
    
    log(f'全部完成: {len(all_trades)}笔交易')
    
    analysis = analyze_trades(all_trades)
    write_evolver_memory(analysis)
    
    log('=== 完成 ===')
