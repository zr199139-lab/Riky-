#!/usr/bin/env python3
"""
币安交易记录拉取脚本
时间范围: 2026-04-30 00:00:00 UTC → 2026-05-12 23:59:59 UTC
"""
import sys, os, json, time, hmac, hashlib, requests
from datetime import datetime, timezone

sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt

# ── 凭据 ──────────────────────────────────────────────────────────────────
_CREDS = _decrypt()
API_KEY    = _CREDS.get('BINANCE_API_KEY', '')
API_SECRET = _CREDS.get('BINANCE_API_SECRET', '')

if not API_KEY or not API_SECRET:
    print("❌ 凭据加载失败")
    sys.exit(1)
print(f"✅ 凭据加载成功: key={API_KEY[:8]}...")

# ── 时间范围 ───────────────────────────────────────────────────────────────
START_MS = 1745971200000   # 2026-04-30 00:00:00 UTC
END_MS   = 1747094399000   # 2026-05-12 23:59:59 UTC

OUT_DIR = '/home/admin/charon/analysis'
os.makedirs(OUT_DIR, exist_ok=True)

FUTURES_BASE = 'https://fapi.binance.com'
SPOT_BASE    = 'https://api.binance.com'

HEADERS = {'X-MBX-APIKEY': API_KEY}

def _sign(params: dict) -> str:
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f'{query}&signature={sig}'

def _ts():
    return int(time.time() * 1000)

def get_json(url, params):
    """带签名的GET请求"""
    params['timestamp'] = _ts()
    params['recvWindow'] = 10000
    qs = _sign(params)
    r = requests.get(f'{url}?{qs}', headers=HEADERS, timeout=15)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json(), None

# ══════════════════════════════════════════════════════════════════════════
# 1. 合约交易记录
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("📊 拉取合约交易记录 (Futures)")
print("="*60)

# 1a. 获取所有USDT永续合约交易对
print("→ 获取 exchangeInfo ...")
ei_r = requests.get(f'{FUTURES_BASE}/fapi/v1/exchangeInfo', timeout=15)
all_futures_symbols = []
if ei_r.status_code == 200:
    ei = ei_r.json()
    for s in ei.get('symbols', []):
        if (s.get('quoteAsset') == 'USDT'
                and s.get('contractType') == 'PERPETUAL'
                and s.get('status') == 'TRADING'):
            all_futures_symbols.append(s['symbol'])
    print(f"  共 {len(all_futures_symbols)} 个USDT永续合约")
else:
    print(f"  ⚠️ exchangeInfo失败: {ei_r.status_code}, 使用默认列表")
    all_futures_symbols = [
        'BTCUSDT','ETHUSDT','SOLUSDT','DOGEUSDT','BNBUSDT',
        'XRPUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT',
        'MATICUSDT','LTCUSDT','UNIUSDT','ATOMUSDT','NEARUSDT',
        'APTUSDT','ARBUSDT','OPUSDT','INJUSDT','SUIUSDT',
        'PEPEUSDT','WIFUSDT','BONKUSDT','SHIBUSDT','FLOKIUSDT',
        'TRXUSDT','ETCUSDT','FILUSDT','AAVEUSDT','MKRUSDT',
    ]

def fetch_futures_trades(symbol):
    """分批拉取某合约交易对的全部成交记录"""
    trades = []
    from_id = None
    start = START_MS
    batch = 0
    while True:
        params = {
            'symbol': symbol,
            'startTime': start,
            'endTime': END_MS,
            'limit': 1000,
        }
        if from_id:
            params['fromId'] = from_id
            del params['startTime']
            del params['endTime']

        data, err = get_json(f'{FUTURES_BASE}/fapi/v1/userTrades', params)
        if err:
            print(f"    ⚠️ {symbol} 批次{batch} 错误: {err}")
            break
        if not data:
            break
        trades.extend(data)
        batch += 1
        if len(data) < 1000:
            break
        # 下一批从最后一条的id+1开始
        from_id = data[-1]['id'] + 1
        time.sleep(0.12)  # 限速
    return trades

futures_all = {}
futures_total = 0
symbols_with_trades = []

for i, sym in enumerate(all_futures_symbols):
    trades = fetch_futures_trades(sym)
    if trades:
        futures_all[sym] = trades
        futures_total += len(trades)
        symbols_with_trades.append(sym)
        print(f"  ✅ {sym}: {len(trades)} 条")
    else:
        # 无交易的不打印，减少噪音
        pass
    # 每50个symbol打印进度
    if (i+1) % 50 == 0:
        print(f"  ... 已扫描 {i+1}/{len(all_futures_symbols)} 个交易对，有交易: {len(symbols_with_trades)} 个")
    time.sleep(0.08)

print(f"\n合约汇总: {len(symbols_with_trades)} 个交易对，共 {futures_total} 条成交记录")

futures_out = {
    'meta': {
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'start_time': '2026-04-30T00:00:00Z',
        'end_time':   '2026-05-12T23:59:59Z',
        'start_ms':   START_MS,
        'end_ms':     END_MS,
        'total_trades': futures_total,
        'symbols_with_trades': symbols_with_trades,
    },
    'trades_by_symbol': futures_all,
}

futures_path = os.path.join(OUT_DIR, 'futures_trades_12d.json')
with open(futures_path, 'w', encoding='utf-8') as f:
    json.dump(futures_out, f, ensure_ascii=False, indent=2)
print(f"💾 合约记录已写入: {futures_path}")

# ══════════════════════════════════════════════════════════════════════════
# 2. 现货交易记录
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("📊 拉取现货交易记录 (Spot)")
print("="*60)

# 2a. 获取所有USDT现货交易对
print("→ 获取现货 exchangeInfo ...")
sei_r = requests.get(f'{SPOT_BASE}/api/v3/exchangeInfo', timeout=15)
all_spot_symbols = []
if sei_r.status_code == 200:
    sei = sei_r.json()
    for s in sei.get('symbols', []):
        if (s.get('quoteAsset') == 'USDT'
                and s.get('status') == 'TRADING'
                and s.get('isSpotTradingAllowed', False)):
            all_spot_symbols.append(s['symbol'])
    print(f"  共 {len(all_spot_symbols)} 个USDT现货交易对")
else:
    print(f"  ⚠️ 现货exchangeInfo失败，使用默认列表")
    all_spot_symbols = [
        'BTCUSDT','ETHUSDT','SOLUSDT','DOGEUSDT','BNBUSDT',
        'XRPUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT',
        'MATICUSDT','LTCUSDT','UNIUSDT','ATOMUSDT','NEARUSDT',
        'APTUSDT','ARBUSDT','OPUSDT','INJUSDT','SUIUSDT',
        'PEPEUSDT','WIFUSDT','BONKUSDT','SHIBUSDT','FLOKIUSDT',
    ]

def fetch_spot_trades(symbol):
    """分批拉取某现货交易对的全部成交记录"""
    trades = []
    from_id = None
    start = START_MS
    batch = 0
    while True:
        params = {
            'symbol': symbol,
            'startTime': start,
            'endTime': END_MS,
            'limit': 1000,
        }
        if from_id:
            params['fromId'] = from_id
            del params['startTime']
            del params['endTime']

        data, err = get_json(f'{SPOT_BASE}/api/v3/myTrades', params)
        if err:
            # 400通常是该交易对没有权限或不存在，静默跳过
            if 'HTTP 400' not in err:
                print(f"    ⚠️ {symbol} 批次{batch} 错误: {err}")
            break
        if not data:
            break
        trades.extend(data)
        batch += 1
        if len(data) < 1000:
            break
        from_id = data[-1]['id'] + 1
        time.sleep(0.12)
    return trades

spot_all = {}
spot_total = 0
spot_symbols_with_trades = []

for i, sym in enumerate(all_spot_symbols):
    trades = fetch_spot_trades(sym)
    if trades:
        spot_all[sym] = trades
        spot_total += len(trades)
        spot_symbols_with_trades.append(sym)
        print(f"  ✅ {sym}: {len(trades)} 条")
    if (i+1) % 100 == 0:
        print(f"  ... 已扫描 {i+1}/{len(all_spot_symbols)} 个交易对，有交易: {len(spot_symbols_with_trades)} 个")
    time.sleep(0.08)

print(f"\n现货汇总: {len(spot_symbols_with_trades)} 个交易对，共 {spot_total} 条成交记录")

spot_out = {
    'meta': {
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'start_time': '2026-04-30T00:00:00Z',
        'end_time':   '2026-05-12T23:59:59Z',
        'start_ms':   START_MS,
        'end_ms':     END_MS,
        'total_trades': spot_total,
        'symbols_with_trades': spot_symbols_with_trades,
    },
    'trades_by_symbol': spot_all,
}

spot_path = os.path.join(OUT_DIR, 'spot_trades_12d.json')
with open(spot_path, 'w', encoding='utf-8') as f:
    json.dump(spot_out, f, ensure_ascii=False, indent=2)
print(f"💾 现货记录已写入: {spot_path}")

# ══════════════════════════════════════════════════════════════════════════
# 3. 提取 bot_logs 中的交易日志行
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("📋 提取 bot_logs 交易日志")
print("="*60)

BOT_LOGS = '/home/admin/.hermes/mempalace/quant_trading/bot_logs'
KEYWORDS = ['TRADE', '开仓', '平仓', 'PnL', 'profit', 'Profit',
            'OPEN', 'CLOSE', 'filled', 'FILLED', 'order', 'ORDER']

extracted = []
log_files_scanned = []

for root, dirs, files in os.walk(BOT_LOGS):
    # 也扫描archived子目录
    for fname in files:
        fpath = os.path.join(root, fname)
        # 只扫描文本日志文件
        if not any(fname.endswith(ext) for ext in ['.log', '.csv', '.txt']):
            continue
        log_files_scanned.append(fpath)
        try:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as lf:
                for lineno, line in enumerate(lf, 1):
                    if any(kw in line for kw in KEYWORDS):
                        extracted.append({
                            'file': fpath.replace(BOT_LOGS + '/', ''),
                            'line': lineno,
                            'text': line.rstrip(),
                        })
        except Exception as e:
            print(f"  ⚠️ 读取 {fname} 失败: {e}")

print(f"  扫描了 {len(log_files_scanned)} 个日志文件")
print(f"  提取到 {len(extracted)} 条交易相关日志行")

# 也读取 CSV 交易日志
csv_trades = {}
for fname in ['spot_trade_log.csv', 'hv_trade_log.csv']:
    fpath = os.path.join(BOT_LOGS, fname)
    if os.path.exists(fpath):
        try:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as cf:
                content = cf.read()
            csv_trades[fname] = content
            print(f"  📄 读取 {fname}: {len(content.splitlines())} 行")
        except Exception as e:
            print(f"  ⚠️ 读取 {fname} 失败: {e}")

bot_log_out = {
    'meta': {
        'extracted_at': datetime.now(timezone.utc).isoformat(),
        'log_files_scanned': log_files_scanned,
        'total_matching_lines': len(extracted),
        'keywords': KEYWORDS,
    },
    'trade_lines': extracted,
    'csv_trade_logs': csv_trades,
}

botlog_path = os.path.join(OUT_DIR, 'bot_trade_logs_12d.json')
with open(botlog_path, 'w', encoding='utf-8') as f:
    json.dump(bot_log_out, f, ensure_ascii=False, indent=2)
print(f"💾 bot日志已写入: {botlog_path}")

# ══════════════════════════════════════════════════════════════════════════
# 4. 汇总摘要
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("📊 最终汇总")
print("="*60)
print(f"  合约成交: {futures_total} 条  ({len(symbols_with_trades)} 个交易对)")
print(f"  现货成交: {spot_total} 条  ({len(spot_symbols_with_trades)} 个交易对)")
print(f"  bot日志行: {len(extracted)} 条")
print(f"\n输出文件:")
print(f"  {futures_path}")
print(f"  {spot_path}")
print(f"  {botlog_path}")

# 写一个简洁的摘要文件
summary = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'period': '2026-04-30 ~ 2026-05-12',
    'futures': {
        'total_trades': futures_total,
        'symbols': symbols_with_trades,
        'file': futures_path,
    },
    'spot': {
        'total_trades': spot_total,
        'symbols': spot_symbols_with_trades,
        'file': spot_path,
    },
    'bot_logs': {
        'matching_lines': len(extracted),
        'files_scanned': len(log_files_scanned),
        'file': botlog_path,
    },
}
with open(os.path.join(OUT_DIR, 'summary.json'), 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"  {os.path.join(OUT_DIR, 'summary.json')}")
print("\n✅ 全部完成")
