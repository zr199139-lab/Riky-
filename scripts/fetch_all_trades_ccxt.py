#!/usr/bin/env python3
"""
暗黑星火 · 全量交易数据提取器 ccxt版
====================================
用ccxt拉取5月全部成交记录 (现货+合约)
ccxt内部处理签名，不会-1022
"""
import sys, os, json, time
from datetime import datetime, timezone

sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt

OUT_DIR = '/home/admin/charon/analysis'
os.makedirs(OUT_DIR, exist_ok=True)

# 用ccxt
import ccxt

_CREDS = _decrypt()
exchange = ccxt.binance({
    'apiKey': _CREDS.get('BINANCE_API_KEY', ''),
    'secret': _CREDS.get('BINANCE_API_SECRET', ''),
    'options': {'defaultType': 'future'},
    'enableRateLimit': True,
})

MAY_START = 1745971200000  # 2026-05-01 00:00 UTC
MAY_END   = 1747699199000  # 2026-05-19 23:59 UTC

def fmt(ms):
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime('%m-%d %H:%M')

def fetch_futures_trades_ccxt(symbol, since_ms, end_ms):
    """ccxt拉合约成交记录"""
    all_trades = []
    limit = 500
    cursor = since_ms
    
    while cursor < end_ms:
        try:
            trades = exchange.fetch_my_trades(symbol, since=cursor, limit=limit)
            if not trades:
                break
            
            # 过滤掉超出endTime的
            valid = [t for t in trades if t['timestamp'] <= end_ms]
            all_trades.extend(valid)
            
            if len(trades) < limit:
                break
            
            # 下一页：取最后一条的时间戳+1ms
            cursor = trades[-1]['timestamp'] + 1
            time.sleep(0.1)
        except Exception as e:
            print(f"    ❌ {symbol}: {str(e)[:80]}")
            break
    
    return all_trades

def fetch_spot_trades_ccxt(symbol, since_ms, end_ms):
    """ccxt拉现货成交记录"""
    exchange.options['defaultType'] = 'spot'
    result = fetch_futures_trades_ccxt(symbol, since_ms, end_ms)
    exchange.options['defaultType'] = 'future'
    return result

def main():
    print("=" * 60)
    print("暗黑星火 · 全量交易数据提取器 ccxt版")
    print(f"时间范围: {fmt(MAY_START)} ~ {fmt(MAY_END)}")
    print("=" * 60)
    
    # 只拉我们交易过的币种
    priority_symbols = [
        'BTC/USDT','ETH/USDT','BCH/USDT','SOL/USDT','DOGE/USDT','XRP/USDT','BNB/USDT',
        'ADA/USDT','AVAX/USDT','DOT/USDT','LINK/USDT','SUI/USDT','OP/USDT','ARB/USDT',
        'NEAR/USDT','FIL/USDT','ATOM/USDT','INJ/USDT','SEI/USDT','TIA/USDT','JTO/USDT',
        'MLN/USDT','POLYX/USDT','AIGENSYN/USDT','RUNE/USDT','AAVE/USDT','UNI/USDT',
        'PEPE/USDT','WIF/USDT','APT/USDT','POL/USDT','CRV/USDT','LDO/USDT','GALA/USDT',
        'SAND/USDT','MANA/USDT','ENJ/USDT','AXS/USDT','YGG/USDT','GMT/USDT','FTM/USDT',
        'ALGO/USDT','ICP/USDT','KAVA/USDT','ROSE/USDT','ANKR/USDT','BAT/USDT','ZIL/USDT',
        'IOST/USDT','CVC/USDT','LRC/USDT','STORJ/USDT','OCEAN/USDT','AGIX/USDT','FET/USDT',
        'TFUEL/USDT','AUDIO/USDT','CHZ/USDT','SHIB/USDT','PEOPLE/USDT','BNX/USDT',
    ]
    
    print(f"  目标币种: {len(priority_symbols)}个")
    
    all_futures = {}
    all_spot = {}
    
    # 合约
    print(f"\n📡 拉取合约成交...")
    total_ft = 0
    for i, sym in enumerate(priority_symbols):
        if i % 10 == 0:
            print(f"  进度: {i}/{len(priority_symbols)}...")
        trades = fetch_futures_trades_ccxt(sym, MAY_START, MAY_END)
        if trades:
            all_futures[sym] = trades
            total_ft += len(trades)
            print(f"  ✅ {sym}: {len(trades)}笔")
    
    # 现货
    spot_pairs = [f"{c}/USDT" for c in ['ETH','BTC','SOL','DOGE','BCH','XRP','JTO','LDO','INJ','PEPE','UNI','CRV','AAVE','SAND','MANA','GALA','FTM','ALGO','ROSE','SHIB']]
    print(f"\n💱 拉取现货成交...")
    total_st = 0
    for i, sym in enumerate(spot_pairs):
        trades = fetch_spot_trades_ccxt(sym, MAY_START, MAY_END)
        if trades:
            all_spot[sym] = trades
            total_st += len(trades)
            print(f"  ✅ 现货{sym}: {len(trades)}笔")
    
    # 统计
    print("\n" + "=" * 60)
    print(f"📊 合计: 合约{total_ft}笔 + 现货{total_st}笔 = {total_ft+total_st}笔")
    print(f"   有交易的合约币种: {len(all_futures)}个")
    print(f"   有交易的现货币种: {len(all_spot)}个")
    
    if all_futures:
        by_pnl = sorted(
            [(s, sum(float(t.get('info',{}).get('realizedPnl',0) or 0) for t in ts)) for s, ts in all_futures.items()],
            key=lambda x: x[1]
        )
        print(f"   亏损Top5: {', '.join([f'{s}(${v:.2f})' for s,v in by_pnl[:5]])}")
        print(f"   盈利Top5: {', '.join([f'{s}(${v:.2f})' for s,v in reversed(by_pnl[-5:])])}")
    
    output = {
        'meta': {
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'period': f'{fmt(MAY_START)} ~ {fmt(MAY_END)}',
            'total_futures': total_ft,
            'total_spot': total_st,
        },
        'futures': {k: [{'symbol':t['symbol'],'side':t['side'],'price':t['price'],'amount':t['amount'],'cost':t['cost'],'fee':t['fee'],'timestamp':t['timestamp'],'pnl':t.get('info',{}).get('realizedPnl')} for t in v] for k,v in all_futures.items()},
        'spot': {k: [{'symbol':t['symbol'],'side':t['side'],'price':t['price'],'amount':t['amount'],'cost':t['cost'],'fee':t['fee'],'timestamp':t['timestamp']} for t in v] for k,v in all_spot.items()},
    }
    
    out_path = f'{OUT_DIR}/all_trades_ccxt_202605.json'
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)
    size_kb = os.path.getsize(out_path)/1024
    print(f"\n✅ 已保存: {out_path}")
    print(f"   大小: {size_kb:.1f} KB")

if __name__ == '__main__':
    main()
