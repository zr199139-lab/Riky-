#!/usr/bin/env python3
"""POLYX position check via Binance API"""
import requests, json

# Position risk check
headers = {"Content-Type": "application/json"}
resp = requests.get("https://fapi.binance.com/fapi/v2/positionRisk", headers=headers, timeout=10)
data = resp.json()

found = False
for pos in data:
    symbol = pos.get('symbol','')
    if 'POLYX' in symbol.upper():
        found = True
        print(f"Symbol: {pos.get('symbol')}")
        print(f"PositionAmt: {pos.get('positionAmt')}")
        print(f"EntryPrice: {pos.get('entryPrice')}")
        print(f"MarkPrice: {pos.get('markPrice')}")
        print(f"LiquidationPrice: {pos.get('liquidationPrice')}")
        print(f"UnrealizedPnL: {pos.get('unRealizedProfit')}")
        print(f"Leverage: {pos.get('leverage')}")
        print(f"MarginType: {pos.get('marginType')}")
        print(f"PositionSide: {pos.get('positionSide')}")

if not found:
    print("NO_POLYX_POSITION")

# Also check funding rate
fr_resp = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=POLYXUSDT", headers=headers, timeout=10)
fr_data = fr_resp.json()
print(f"FundingRate: {fr_data.get('lastFundingRate', 'N/A')}")
print(f"MarkPrice: {fr_data.get('markPrice', 'N/A')}")
