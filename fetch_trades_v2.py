#!/usr/bin/env python3
"""
币安交易记录拉取脚本 v2
时间范围: 2026-04-30 00:00:00 UTC → 2026-05-12 23:59:59 UTC
修复: Futures API 最大7天窗口限制 → 拆成两段
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

# ── 时间窗口 (Futures最大7天/窗口) ────────────────────────────────────────
# 窗口1: 2026-04-30 00:00:00 UTC → 2026-05-06 23:59:59 UTC  (7天)
# 窗口2: 2026-05-07 00:00:00 UTC → 2026-05-12 23:59:59 UTC  (6天)
TIME_WINDOWS = [
    (1745971200000, 1746575999000),   # Apr 30 – May 6
    (1746576000000, 1747094399000),   # May 7  – May 12
]
FULL_START = 1745971200000
FULL_END   = 1747094399000

OUT_DIR = '/home/admin/charon/analysis'
os.makedirs(OUT_DIR, exist_ok=True)

FUTURES_BASE = 'https://fapi.binance.com'
SPOT_BASE    = 'https://api.binance.com'
HEADERS = {'X-MBX-APIKEY': API_KEY}

def _sign(params: dict) -> str:
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f'{query}&signature={sig}'

def get_json(url, params):
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 10000
    qs = _sign(params)
    try:
        r = requests.get(f'{url}?{qs}', headers=HEADERS, timeout=15)
    except Exception as e:
        return None, f"网络错误: {e}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json(), None

def fetch_trades_in_window(symbol, start_ms, end_ms, base_url, endpoint):
    """在单个时间窗口内分页拉取成交记录（startTime滚动分页）"""
    trades = []
    current_start = start_ms
    while True:
        params = {
            'symbol': symbol,
            'startTime': current_start,
            'endTime': end_ms,
            'limit': 1000,
        }
        data, err = get_json(f'{base_url}{endpoint}', params)
        if err:
            # 400通常是该交易对无权限/无数据，静默跳过
            if 'HTTP 400' not in err and 'HTTP 404' not in err:
                print(f"    ⚠️  {symbol} 错误: {err}")
            return trades
        if not data:
            break
        trades.extend(data)
        if len(data) < 1000:
            break
        # 滚动 startTime 到最后一条记录时间+1ms
        last_time = data[-1].get('time', data[-1].get('updateTime', current_start))
        current_start = last_time + 1
        if current_start > end_ms:
            break
        time.sleep(0.15)
    return trades

def fetch_all_windows(symbol, base_url, endpoint):
    """跨所有时间窗口拉取，合并去重"""
    all_trades = []
    seen_ids = set()
    for (w_start, w_end) in TIME_WINDOWS:
        batch = fetch_trades_in_window(symbol, w_start, w_end, base_url, endpoint)
        for t in batch:
            tid = t.get('id', t.get('tradeId'))
            if tid not in seen_ids:
                seen_ids.add(tid)
                all_trades.append(t)
        time.sleep(0.1)
    return all_trades

# ══════════════════════════════════════════════════════════════════════════
# 1. 合约交易记录
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("📊 拉取合约交易记录 (Futures USDT-M Perpetual)")
print("="*60)

print("→ 获取 /fapi/v1/exchangeInfo ...")
ei_r = requests.get(f'{FUTURES_BASE}/fapi/v1/exchangeInfo', timeout=15)
all_futures_symbols = []
if ei_r.status_code == 200:
    for s in ei_r.json().get('symbols', []):
        if (s.get('quoteAsset') == 'USDT'
                and s.get('contractType') == 'PERPETUAL'
                and s.get('status') == 'TRADING'):
            all_futures_symbols.append(s['symbol'])
    print(f"  共 {len(all_futures_symbols)} 个USDT永续合约")
else:
    print(f"  ⚠️  exchangeInfo失败({ei_r.status_code})，使用默认列表")
    all_futures_symbols = [
        'BTCUSDT','ETHUSDT','SOLUSDT','DOGEUSDT','BNBUSDT',
        'XRPUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT',
        'MATICUSDT','LTCUSDT','UNIUSDT','ATOMUSDT','NEARUSDT',
        'APTUSDT','ARBUSDT','OPUSDT','INJUSDT','SUIUSDT',
        'PEPEUSDT','WIFUSDT','BONKUSDT','SHIBUSDT','FLOKIUSDT',
        'TRXUSDT','ETCUSDT','FILUSDT','AAVEUSDT','MKRUSDT',
    ]

futures_all = {}
futures_total = 0
symbols_with_trades = []

for i, sym in enumerate(all_futures_symbols):
    trades = fetch_all_windows(sym, FUTURES_BASE, '/fapi/v1/userTrades')
    if trades:
        futures_all[sym] = trades
        futures_total += len(trades)
        symbols_with_trades.append(sym)
        print(f"  ✅ {sym}: {len(trades)} 条")
    if (i + 1) % 50 == 0:
        print(f"  ... 进度 {i+1}/{len(all_futures_symbols)}，有交易: {len(symbols_with_trades)} 个")
    time.sleep(0.08)

print(f"\n合约汇总: {len(symbols_with_trades)} 个交易对，共 {futures_total} 条成交")

futures_out = {
    'meta': {
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'period': '2026-04-30T00:00:00Z ~ 2026-05-12T23:59:59Z',
        'time_windows': [
            {'start_ms': w[0], 'end_ms': w[1]} for w in TIME_WINDOWS
        ],
        'total_trades': futures_total,
        'symbols_with_trades': symbols_with_trades,
    },
    'trades_by_symbol': futures_all,
}

futures_path = os.path.join(OUT_DIR, 'futures_trades_12d.json')
with open(futures_path, 'w', encoding='utf-8') as f:
    json.dump(futures_out, f, ensure_ascii=False, indent=2)
print(f"💾 合约记录 → {futures_path}")

# ══════════════════════════════════════════════════════════════════════════
# 2. 现货交易记录
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("📊 拉取现货交易记录 (Spot)")
print("="*60)

print("→ 获取现货 /api/v3/exchangeInfo ...")
sei_r = requests.get(f'{SPOT_BASE}/api/v3/exchangeInfo', timeout=15)
all_spot_symbols = []
if sei_r.status_code == 200:
    for s in sei_r.json().get('symbols', []):
        if (s.get('quoteAsset') == 'USDT'
                and s.get('status') == 'TRADING'
                and s.get('isSpotTradingAllowed', False)):
            all_spot_symbols.append(s['symbol'])
    print(f"  共 {len(all_spot_symbols)} 个USDT现货交易对")
else:
    print(f"  ⚠️  现货exchangeInfo失败，使用默认列表")
    all_spot_symbols = [
        'BTCUSDT','ETHUSDT','SOLUSDT','DOGEUSDT','BNBUSDT',
        'XRPUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT',
        'MATICUSDT','LTCUSDT','UNIUSDT','ATOMUSDT','NEARUSDT',
        'APTUSDT','ARBUSDT','OPUSDT','INJUSDT','SUIUSDT',
        'PEPEUSDT','WIFUSDT','BONKUSDT','SHIBUSDT','FLOKIUSDT',
    ]

spot_all = {}
spot_total = 0
spot_symbols_with_trades = []

for i, sym in enumerate(all_spot_symbols):
    # 现货 myTrades 也有时间窗口限制，同样分窗口拉
    trades = fetch_all_windows(sym, SPOT_BASE, '/api/v3/myTrades')
    if trades:
        spot_all[sym] = trades
        spot_total += len(trades)
        spot_symbols_with_trades.append(sym)
        print(f"  ✅ {sym}: {len(trades)} 条")
    if (i + 1) % 100 == 0:
        print(f"  ... 进度 {i+1}/{len(all_spot_symbols)}，有交易: {len(spot_symbols_with_trades)} 个")
    time.sleep(0.08)

print(f"\n现货汇总: {len(spot_symbols_with_trades)} 个交易对，共 {spot_total} 条成交")

spot_out = {
    'meta': {
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'period': '2026-04-30T00:00:00Z ~ 2026-05-12T23:59:59Z',
        'time_windows': [
            {'start_ms': w[0], 'end_ms': w[1]} for w in TIME_WINDOWS
        ],
        'total_trades': spot_total,
        'symbols_with_trades': spot_symbols_with_trades,
    },
    'trades_by_symbol': spot_all,
}

spot_path = os.path.join(OUT_DIR, 'spot_trades_12d.json')
with open(spot_path, 'w', encoding='utf-8') as f:
    json.dump(spot_out, f, ensure_ascii=False, indent=2)
print(f"💾 现货记录 → {spot_path}")

# ══════════════════════════════════════════════════════════════════════════
# 3. 提取 bot_logs 交易日志
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
print("📋 提取 bot_logs 交易日志")
print("="*60)

BOT_LOGS = '/home/admin/.hermes/mempalace/quant_trading/bot_logs'
KEYWORDS = ['TRADE', '开仓', '平仓', 'PnL', 'profit', 'Profit',
            'OPEN', 'CLOSE', 'filled', 'FILLED', 'ORDER', 'order']

extracted = []
log_files_scanned = []

for root, dirs, files in os.walk(BOT_LOGS):
    for fname in sorted(files):
        fpath = os.path.join(root, fname)
        if not any(fname.endswith(ext) for ext in ['.log', '.csv', '.txt']):
            continue
        log_files_scanned.append(fpath)
        try:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as lf:
                for lineno, line in enumerate(lf, 1):
                    if any(kw in line for kw in KEYWORDS):
                        extracted.append({
                            'file': os.path.relpath(fpath, BOT_LOGS),
                            'line': lineno,
                            'text': line.rstrip(),
                        })
        except Exception as e:
            print(f"  ⚠️  读取 {fname} 失败: {e}")

print(f"  扫描 {len(log_files_scanned)} 个日志文件，提取 {len(extracted)} 条交易相关行")

# 读取 CSV 交易日志全文
csv_trades = {}
for fname in ['spot_trade_log.csv', 'hv_trade_log.csv']:
    fpath = os.path.join(BOT_LOGS, fname)
    if os.path.exists(fpath):
        try:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as cf:
                content = cf.read()
            csv_trades[fname] = content
            print(f"  📄 {fname}: {len(content.splitlines())} 行")
        except Exception as e:
            print(f"  ⚠️  读取 {fname} 失败: {e}")

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
print(f"💾 bot日志 → {botlog_path}")

# ══════════════════════════════════════════════════════════════════════════
# 4. 汇总 summary.json
# ══════════════════════════════════════════════════════════════════════════
summary = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'period': '2026-04-30 ~ 2026-05-12',
    'note': '输出目录为 /home/admin/charon/analysis (无 /root 写权限)',
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
summary_path = os.path.join(OUT_DIR, 'summary.json')
with open(summary_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print("\n" + "="*60)
print("✅ 全部完成")
print("="*60)
print(f"  合约成交: {futures_total} 条 / {len(symbols_with_trades)} 个交易对")
print(f"  现货成交: {spot_total} 条 / {len(spot_symbols_with_trades)} 个交易对")
print(f"  bot日志行: {len(extracted)} 条")
print(f"\n输出文件:")
for p in [futures_path, spot_path, botlog_path, summary_path]:
    sz = os.path.getsize(p) / 1024
    print(f"  {p}  ({sz:.1f} KB)")
