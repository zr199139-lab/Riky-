#!/usr/bin/env python3
"""
币安5月交易记录 → evolver memory 格式化管道
读取币安真实成交记录，写入 ./memory/ 供 evolver GEP分析
"""
import os, json, time, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/home/admin/charon')
MEMORY = BASE / 'memory'
LOGS = BASE / 'bot_logs'
SECURE_DIR = Path.home() / '.hermes/mempalace/secure'

sys.path.insert(0, str(SECURE_DIR))
from decrypt_and_run import decrypt

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}', flush=True)
    with open(LOGS / 'binance_history_loader.log', 'a') as f:
        f.write(f'[{t}] {msg}\n')

def get_exchange():
    """解密API凭据并连接币安"""
    creds = decrypt()
    api_key = creds.get('BINANCE_API_KEY', '')
    api_secret = creds.get('BINANCE_API_SECRET', '')
    
    if not api_key or not api_secret:
        log('[ERR] 币安API Key未找到')
        return None
    
    import ccxt
    ex = ccxt.binance({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'rateLimit': 2000,
        'options': {'defaultType': 'spot'}
    })
    return ex

def fetch_all_trades(ex, start_ts, end_ts=None):
    """拉取币安5月所有成交记录 - 按币种逐对查询"""
    if end_ts is None:
        end_ts = int(time.time() * 1000)
    
    # 先获取所有交易对
    ex.load_markets()
    symbols = list(ex.symbols)
    # 只取USDT交易对
    usdt_symbols = [s for s in symbols if s.endswith('/USDT')]
    log(f'USDT交易对: {len(usdt_symbols)}个')
    
    all_trades = []
    limit = 1000
    
    for sym in usdt_symbols:
        try:
            trades = ex.fetch_my_trades(sym, limit=limit, params={
                'startTime': start_ts, 'endTime': end_ts
            })
            if trades:
                for t in trades:
                    t['account'] = 'spot'
                all_trades.extend(trades)
                time.sleep(0.3)  # 限速
        except Exception as e:
            msg = str(e)
            if 'no trade' in msg.lower() or 'not found' in msg.lower():
                continue  # 无交易记录的正常跳过
            log(f'  {sym}: {msg[:60]}')
            time.sleep(1)
    
    # 也查合约
    try:
        ex_fut = ccxt.binance({
            'apiKey': ex.apiKey,
            'secret': ex.secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        ex_fut.load_markets()
        for sym in usdt_symbols:
            try:
                raw = sym.replace('/USDT', '') + '/USDT:USDT'
                trades = ex_fut.fetch_my_trades(raw, limit=limit, params={
                    'startTime': start_ts, 'endTime': end_ts
                })
                if trades:
                    for t in trades:
                        t['account'] = 'futures'
                    all_trades.extend(trades)
                    time.sleep(0.3)
            except:
                time.sleep(0.3)
    except Exception as e:
        log(f'合约查询跳过: {e}')
    
    log(f'总交易记录: {len(all_trades)}笔')
    return all_trades

def process_trades(trades):
    """分析成交记录，提炼evolver需要的信号"""
    if not trades:
        return None
    
    # 按币种汇总
    by_symbol = {}
    total_fees = 0.0
    total_volume = 0.0
    total_pnl_approx = 0.0  # 近似PnL (buy-sell价差)
    
    for t in trades:
        sym = t.get('symbol', 'unknown')
        fee_cost = float(t.get('fee', {}).get('cost', 0))
        cost = float(t.get('cost', 0))
        side = t.get('side', '')
        
        if sym not in by_symbol:
            by_symbol[sym] = {'buys': 0, 'sells': 0, 'buy_vol': 0.0, 'sell_vol': 0.0, 
                              'fees': 0.0, 'trades': 0, 'pnl': 0.0}
        
        by_symbol[sym]['fees'] += fee_cost
        by_symbol[sym]['trades'] += 1
        by_symbol[sym][f'{side}_vol'] += cost
        if side == 'buy': by_symbol[sym]['buys'] += 1
        else: by_symbol[sym]['sells'] += 1
        
        total_fees += fee_cost
        total_volume += cost
    
    # 生成报告
    report = {
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'binance_may_trades',
        'period': '2026-05-01 to 2026-05-18',
        'total_trades': len(trades),
        'total_volume': round(total_volume, 2),
        'total_fees': round(total_fees, 2),
        'symbols_traded': len(by_symbol),
        'by_symbol': by_symbol,
        'summary': {
            'total_buys': sum(s['buys'] for s in by_symbol.values()),
            'total_sells': sum(s['sells'] for s in by_symbol.values()),
            'net_volume': round(sum(s['buy_vol'] - s['sell_vol'] for s in by_symbol.values()), 2),
        }
    }
    
    # 找亏钱的币
    losers = []
    high_fee = []
    for sym, data in by_symbol.items():
        if data['fees'] > 5:
            high_fee.append({'symbol': sym, 'fees': round(data['fees'], 2), 'trades': data['trades']})
    
    report['alerts'] = {
        'high_fee_symbols': high_fee,
        'most_traded': sorted(by_symbol.items(), key=lambda x: x[1]['trades'], reverse=True)[:5]
    }
    
    return report

def write_to_memory(report):
    """写入./memory/供evolver扫描"""
    # 1. 主报告
    json.dump(report, open(MEMORY / 'binance_may_trades.json', 'w'), indent=2)
    log(f'主报告写入: {len(report.get("by_symbol",{}))}币种')
    
    # 2. 写入运行时日志 (evolver扫描这个找信号)
    log_file = MEMORY / 'logs' / f'binance_trades_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    with open(log_file, 'w') as f:
        f.write(f'[binance_trade_loader] 5月交易数据\n')
        f.write(f'总成交: {report["total_trades"]}笔\n')
        f.write(f'总成交量: ${report["total_volume"]:.0f}\n')
        f.write(f'总手续费: ${report["total_fees"]:.2f}\n')
        f.write(f'交易币种: {report["symbols_traded"]}个\n\n')
        
        f.write('各币种费用:\n')
        for sym, data in report.get('by_symbol',{}).items():
            if data['fees'] > 1:
                f.write(f'  [high_fee] {sym}: 手续费=${data["fees"]:.2f} 交易={data["trades"]}笔\n')
        
        f.write('\n异常信号:\n')
        for a in report.get('alerts',{}).get('high_fee_symbols',[]):
            f.write(f'  [error] high_fee: {a["symbol"]} ${a["fees"]} 手续费\n')
        
        if report['total_fees'] > 50:
            f.write(f'  [error] total_fees_excessive: 总手续费${report["total_fees"]:.2f}\n')
        
        if report['symbols_traded'] > 10:
            f.write(f'  [warning] too_many_symbols: {report["symbols_traded"]}个币种分散资金\n')
    
    log(f'日志写入: {log_file}')
    
    # 3. 写入信号 (evolver用signals_match匹配)
    signals = []
    for a in report.get('alerts',{}).get('high_fee_symbols',[]):
        signals.append({"signal": "error", "source": f"binance_{a['symbol']}", 
                       "value": a['fees'], "desc": f"{a['symbol']}手续费${a['fees']}"})
    
    if report['total_fees'] > 50:
        signals.append({"signal": "high_cost", "source": "binance_may", 
                       "value": report['total_fees'], "desc": f"总手续费${report['total_fees']}"})
    
    if report['total_trades'] > 100:
        signals.append({"signal": "overtrading", "source": "binance_may",
                       "value": report['total_trades'], "desc": f"高频交易{report['total_trades']}笔"})
    
    # 合并到现有信号
    sig_file = MEMORY / 'signals' / 'current.json'
    existing = []
    if sig_file.exists():
        try: existing = json.load(open(sig_file))
        except: pass
    
    # 保留非binance信号，追加新信号
    existing = [s for s in existing if not s.get('source','').startswith('binance_')]
    existing.extend(signals)
    json.dump(existing, open(sig_file, 'w'), indent=2)
    log(f'信号写入: {len(signals)}条交易信号')

if __name__ == '__main__':
    log('=== 币安5月交易记录加载 ===')
    
    ex = get_exchange()
    if not ex:
        log('[ERR] 无法连接币安')
        sys.exit(1)
    
    # 5月1日到现在的毫秒时间戳
    may_1 = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    
    trades = fetch_all_trades(ex, may_1)
    if not trades:
        log('[WARN] 没有获取到5月交易记录，尝试从Bot日志读取')
        # fallback: 读本地已有的trade_log
        sys.exit(0)
    
    report = process_trades(trades)
    if report:
        write_to_memory(report)
        
        # 同步更新策略性能文件
        perf_file = MEMORY / 'strategy_performance.json'
        existing = {'strategies': {}}
        if perf_file.exists():
            try: existing = json.load(open(perf_file))
            except: pass
        
        existing['strategies']['binance_may_trades'] = {
            'pnl': -report['total_fees'],  # 近似: 手续费就是确定亏损
            'trades': report['total_trades'],
            'cash': 0,
            'fees_paid': report['total_fees'],
            'has_position': False,
            'updated_at': report['timestamp'],
            'source': 'binance_5月真实交易',
            'total_volume': report['total_volume']
        }
        json.dump(existing, open(perf_file, 'w'), indent=2)
        log('strategy_performance已更新: 含币安5月数据')
    
    log('=== 完成 ===')
