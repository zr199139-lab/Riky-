#!/usr/bin/env python3
"""Check POLYX position from saved status + binance API"""
import json, urllib.request, sys

# Read status first
try:
    with open('/root/charon/ds0_status.json') as f:
        status = json.load(f)
except (FileNotFoundError, PermissionError):
    import os
    home = os.path.expanduser('~')
    with open(f'{home}/charon/ds0_status.json') as f:
        status = json.load(f)

# Get live price
try:
    req = urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/price?symbol=POLYXUSDT")
    with urllib.request.urlopen(req, timeout=10) as r:
        price_data = json.loads(r.read())
        live_price = float(price_data['price'])
except Exception as e:
    live_price = status.get('price', 0)
    print(f"价格获取失败用状态值: {e}", file=sys.stderr)

# Report
print(f"status_price: {status.get('price', 'N/A')}")
print(f"live_price: {live_price}")
print(f"long_qty: {status.get('long_qty', 0)}")
print(f"short_qty: {status.get('short_qty', 0)}")
print(f"pnl: {status.get('pnl', 0)}")
print(f"wallet: {status.get('wallet', 0)}")
print(f"avail: {status.get('avail', 0)}")
print(f"liq_dist: {status.get('liq_dist', 0)}")
print(f"funding_rate: {status.get('funding_rate', 0)}")
print(f"has_position: {status.get('long_qty', 0) != 0 or status.get('short_qty', 0) != 0}")
print(f"trend: {status.get('trend', 'N/A')}")
print(f"cycle: {status.get('cycle', 'N/A')}")
print(f"action: {status.get('action', 'N/A')}")
