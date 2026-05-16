#!/usr/bin/env python3
"""
Parse agent_contract.log to extract position snapshots and infer trade open/close times.
"""

import re
import json
from collections import defaultdict
from datetime import datetime

LOG_FILE = "/home/admin/.hermes/mempalace/quant_trading/bot_logs/agent_contract.log"
OUTPUT_FILE = "/home/admin/charon/analysis/log_extracted_trades.json"

# Pattern: [2026-05-10 10:34:34] 📡   API3/USDT:USDT sell m=$15.0 PnL=$-0.02(-0.1%) SL=$0.3791
# The 📡 emoji may be followed by spaces
POSITION_RE = re.compile(
    r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*?'
    r'(\w+)/USDT:USDT\s+(buy|sell)\s+'
    r'm=\$([0-9.]+)\s+'
    r'PnL=\$(-?[0-9.]+)\(([+-]?[0-9.]+)%\)\s+'
    r'SL=\$([0-9.]+)'
)

# Track per-symbol: list of snapshots
# Each snapshot: (timestamp_str, pnl_usd, pnl_pct, sl_price, margin, side)
symbol_snapshots = defaultdict(list)

# We also need to track "scan groups" — each time the bot does a full scan,
# it emits a batch of 📡 lines. We detect scan boundaries by timestamp changes
# or by looking at the sequence of lines.

# Strategy:
# 1. Parse all 📡 lines with their timestamps
# 2. Group by timestamp (each unique timestamp = one scan cycle)
# 3. For each scan, record which symbols are present
# 4. A symbol's first_seen = first scan it appears in
# 5. A symbol's last_seen = last scan it appears in
# 6. If a symbol disappears between two consecutive scans → it was closed

print(f"Reading {LOG_FILE}...")

scan_groups = {}  # timestamp -> set of symbols present
symbol_data = {}  # symbol -> {side, first_seen, last_seen, snapshots: [...]}

line_count = 0
match_count = 0

with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        line_count += 1
        m = POSITION_RE.search(line)
        if m:
            ts_str, symbol, side, margin, pnl_usd, pnl_pct, sl_price = m.groups()
            match_count += 1

            margin = float(margin)
            pnl_usd = float(pnl_usd)
            pnl_pct = float(pnl_pct)
            sl_price = float(sl_price)

            # Add to scan group
            if ts_str not in scan_groups:
                scan_groups[ts_str] = {}
            scan_groups[ts_str][symbol] = {
                'side': side,
                'margin': margin,
                'pnl_usd': pnl_usd,
                'pnl_pct': pnl_pct,
                'sl_price': sl_price,
            }

            # Track per-symbol
            if symbol not in symbol_data:
                symbol_data[symbol] = {
                    'side': side,
                    'first_seen': ts_str,
                    'last_seen': ts_str,
                    'snapshots': []
                }
            symbol_data[symbol]['last_seen'] = ts_str
            symbol_data[symbol]['snapshots'].append({
                'ts': ts_str,
                'pnl_usd': pnl_usd,
                'pnl_pct': pnl_pct,
                'sl_price': sl_price,
                'margin': margin,
                'side': side,
            })

print(f"Lines read: {line_count}")
print(f"Position snapshots matched: {match_count}")
print(f"Unique scan timestamps: {len(scan_groups)}")
print(f"Unique symbols: {len(symbol_data)}")

# Sort scan timestamps
sorted_timestamps = sorted(scan_groups.keys())

# Determine status: if a symbol's last_seen is the final scan timestamp, it's still open
final_ts = sorted_timestamps[-1] if sorted_timestamps else None
symbols_in_final_scan = set(scan_groups.get(final_ts, {}).keys()) if final_ts else set()

# Build trades list
trades = []
for symbol, data in sorted(symbol_data.items()):
    snapshots = data['snapshots']
    last_snap = snapshots[-1]

    # Determine status
    if symbol in symbols_in_final_scan:
        status = "open"
    else:
        status = "closed"

    trade = {
        "symbol": symbol,
        "side": data['side'],
        "first_seen": data['first_seen'],
        "last_seen": data['last_seen'],
        "last_pnl_usd": last_snap['pnl_usd'],
        "last_pnl_pct": last_snap['pnl_pct'],
        "sl_price": last_snap['sl_price'],
        "margin": last_snap['margin'],
        "status": status,
        "snapshot_count": len(snapshots),
    }
    trades.append(trade)

# Sort by first_seen
trades.sort(key=lambda x: x['first_seen'])

output = {
    "meta": {
        "source_file": LOG_FILE,
        "total_lines": line_count,
        "total_snapshots": match_count,
        "unique_scan_timestamps": len(scan_groups),
        "unique_symbols": len(symbol_data),
        "first_scan": sorted_timestamps[0] if sorted_timestamps else None,
        "last_scan": sorted_timestamps[-1] if sorted_timestamps else None,
        "open_positions": len(symbols_in_final_scan),
        "closed_positions": len(trades) - len(symbols_in_final_scan),
    },
    "trades": trades
}

with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\nOutput written to {OUTPUT_FILE}")
print(f"Total trades: {len(trades)}")
print(f"Open: {output['meta']['open_positions']}, Closed: {output['meta']['closed_positions']}")

# Print summary table
print("\n--- Trade Summary ---")
print(f"{'Symbol':<12} {'Side':<5} {'Status':<8} {'First Seen':<20} {'Last Seen':<20} {'Last PnL $':>10} {'Last PnL %':>10} {'Snaps':>6}")
print("-" * 100)
for t in trades:
    print(f"{t['symbol']:<12} {t['side']:<5} {t['status']:<8} {t['first_seen']:<20} {t['last_seen']:<20} {t['last_pnl_usd']:>10.2f} {t['last_pnl_pct']:>9.1f}% {t['snapshot_count']:>6}")
