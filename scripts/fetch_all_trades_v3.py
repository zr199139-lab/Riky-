#!/usr/bin/env python3
"""
暗黑星火 · 全量交易数据提取器 v3
==================================
从币安API拉取5月全部成交记录 (现货+合约)
修复签名问题: 用完整URL带签名而非requests的params
"""
import sys, os, json, time, hmac, hashlib, requests
from datetime import datetime, timezone

sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt

_CREDS = _decrypt()
API_KEY = _CREDS.get('BINANCE_API_KEY', '')
API_SECRET = _CREDS.get('BINANCE_API_SECRET', '')

OUT_DIR = '/home/admin/charon/analysis'
os.makedirs(OUT_DIR, exist_ok=True)

FAPI = 'https://fapi.binance.com'
SPOT = 'https://api.binance.com'

MAY_START = 1745971200000
MAY_END   = 1747699199000

TIME_WINDOWS = [
    (1745971200000, 1746575999000),
    (1746576000000, 1747180799000),
    (1747180800000, 1747699199000),
]

def sign(secret, query):
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

def fmt(ms):
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime('%m-%d %H:%M')

def make_signed_url(base, path, params):
    """构建带签名的完整URL"""
    query = '&'.join([f'{k}={v}' for k, v in sorted(params.items())])
    sig = sign(API_SECRET, query)
    return f'{base}{path}?{query}&signature={sig}'

def fetch_futures_trades(symbol, start_ms, end_ms):
    all_trades = []
    limit = 500
    from_id = None
    
    while True:
        params = {
            'symbol': symbol,
            'startTime': str(start_ms),
            'endTime': str(end_ms),
            'limit': str(limit),
            'timestamp': str(int(time.time() * 1000)),
            'recvWindow': '10000'
        }
        if from_id:
            params['fromId'] = str(from_id)
        
        url = make_signed_url(FAPI, '/fapi/v1/userTrades', params)
        
        try:
            r = requests.get(url, headers={'X-MBX-APIKEY': API_KEY}, timeout=30)
            data = r.json()
            
            if isinstance(data, dict) and 'code' in data:
                print(f"    ❌ {symbol}: code={data.get('code')} msg={data.get('msg','')[:50]}")
                break
            if not isinstance(data, list) or len(data) == 0:
                break
            
            all_trades.extend(data)
            from_id = int(data[-1]['id'])
            
            if len(data) < limit:
                break
            time.sleep(0.15)
        except Exception as e:
            print(f"    ⚠️ {symbol}: {e}")
            break
    
    return all_trades

def fetch_spot_trades(symbol, start_ms, end_ms):
    all_trades = []
    limit = 500
    from_id = None
    
    while True:
        params = {
            'symbol': symbol,
            'startTime': str(start_ms),
            'endTime': str(end_ms),
            'limit': str(limit),
            'timestamp': str(int(time.time() * 1000)),
            'recvWindow': '10000'
        }
        if from_id:
            params['fromId'] = str(from_id)
        
        url = make_signed_url(SPOT, '/api/v3/myTrades', params)
        
        try:
            r = requests.get(url, headers={'X-MBX-APIKEY': API_KEY}, timeout=30)
            data = r.json()
            
            if isinstance(data, dict) and 'code' in data:
                print(f"    ❌ {symbol}: code={data.get('code')} msg={data.get('msg','')[:50]}")
                break
            if not isinstance(data, list) or len(data) == 0:
                break
            
            all_trades.extend(data)
            from_id = int(data[-1]['id'])
            
            if len(data) < limit:
                break
            time.sleep(0.15)
        except Exception as e:
            print(f"    ⚠️ {symbol}: {e}")
            break
    
    return all_trades

def main():
    print("=" * 60)
    print("暗黑星火 · 全量交易数据提取器 v3")
    print(f"时间范围: {fmt(MAY_START)} ~ {fmt(MAY_END)}")
    print(f"时间窗口: {len(TIME_WINDOWS)}段")
    print("=" * 60)
    
    # 只拉我们交易过的币种（减少API调用）
    priority_symbols = [
        'BTCUSDT','ETHUSDT','BCHUSDT','SOLUSDT','DOGEUSDT','XRPUSDT','BNBUSDT',
        'ADAUSDT','AVAXUSDT','DOTUSDT','LINKUSDT','SUIUSDT','OPUSDT','ARBUSDT',
        'NEARUSDT','FILUSDT','ATOMUSDT','INJUSDT','SEIUSDT','TIAUSDT','JTOUSDT',
        'MLNUSDT','POLYXUSDT','AIGENSYNUSDT','RUNEUSDT','AAVEUSDT','UNIUSDT',
        'PEPEUSDT','WIFUSDT','APTUSDT','POLUSDT','CRVUSDT','LDOUSDT','GALAUSDT',
        'SANDUSDT','MANAUSDT','ENJUSDT','AXSUSDT','YGGUSDT','GMTUSDT','FTMUSDT',
        'ALGOUSDT','ICPUSDT','KAVAUSDT','ROSEUSDT','ANKRUSDT','BATUSDT','ZILUSDT',
        'IOSTUSDT','CVCUSDT','LRCUSDT','STORJUSDT','OCEANUSDT','AGIXUSDT','FETUSDT',
        'TFUELUSDT','AUDIOUSDT','CHZUSDT','SHIBUSDT','PEOPLEUSDT','BNXUSDT',
    ]
    
    print(f"  目标币种: {len(priority_symbols)}个")
    
    all_futures = {}
    all_spot = {}
    
    # 合约
    for wi, (ws, we) in enumerate(TIME_WINDOWS):
        print(f"\n📡 窗口{wi+1}: {fmt(ws)} ~ {fmt(we)}")
        total = 0
        for i, sym in enumerate(priority_symbols):
            if i % 20 == 0:
                print(f"   {i}/{len(priority_symbols)}...", end='\r')
            trades = fetch_futures_trades(sym, ws, we)
            if trades:
                if sym not in all_futures:
                    all_futures[sym] = []
                all_futures[sym].extend(trades)
                total += len(trades)
                print(f"  ✅ {sym}: {len(trades)}笔                     ")
        print(f"   窗口小计: {total}笔                    ")
    
    # 现货
    spot_pairs = [f"{c}USDT" for c in ['ETH','BTC','SOL','DOGE','BCH','XRP','JTO','LDO','INJ','PEPE','UNI','CRV','AAVE','SAND','MANA','GALA','FTM','ALGO','ROSE','SHIB']]
    print(f"\n💱 拉取现货 ({len(spot_pairs)}个币对)...")
    for wi, (ws, we) in enumerate(TIME_WINDOWS):
        for sym in spot_pairs:
            trades = fetch_spot_trades(sym, ws, we)
            if trades:
                if sym not in all_spot:
                    all_spot[sym] = []
                all_spot[sym].extend(trades)
                print(f"  ✅ 现货{sym}: {len(trades)}笔")
    
    # 统计
    total_ft = sum(len(v) for v in all_futures.values())
    total_st = sum(len(v) for v in all_spot.values())
    
    print("\n" + "=" * 60)
    print(f"📊 合计: 合约{total_ft}笔 + 现货{total_st}笔 = {total_ft+total_st}笔")
    print(f"   有交易的合约币种: {len(all_futures)}个")
    print(f"   有交易的现货币种: {len(all_spot)}个")
    if all_futures:
        by_vol = sorted(
            [(s, sum(abs(float(t.get('realizedPnl',0))) for t in ts)) for s, ts in all_futures.items()],
            key=lambda x: x[1], reverse=True
        )[:5]
        print(f"   PnL前5: {', '.join([f'{s}(${v:.2f})' for s,v in by_vol])}")
    
    output = {
        'meta': {
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'period': f'{fmt(MAY_START)} ~ {fmt(MAY_END)}',
            'total_futures': total_ft,
            'total_spot': total_st,
        },
        'futures': all_futures,
        'spot': all_spot,
    }
    
    out_path = f'{OUT_DIR}/all_trades_202605.json'
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\n✅ 已保存: {out_path}")
    print(f"   大小: {os.path.getsize(out_path)/1024:.1f} KB")

if __name__ == '__main__':
    main()
