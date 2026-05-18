#!/usr/bin/env python3
"""Fetch comprehensive cryptocurrency market data from Binance and other APIs."""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

BINANCE_BASE = "https://api.binance.com/api/v3"
FNG_URL = "https://api.alternative.me/fng/?limit=1"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

def fetch_url(url, label=""):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data

def fetch_ticker_24hr(symbol):
    url = f"{BINANCE_BASE}/ticker/24hr?symbol={symbol}"
    return fetch_url(url, symbol)

def fetch_all_tickers():
    url = f"{BINANCE_BASE}/ticker/24hr"
    return fetch_url(url, "ALL_TICKERS")

def fetch_klines(symbol, interval, limit):
    url = f"{BINANCE_BASE}/klines?symbol={symbol}&interval={interval}&limit={limit}"
    raw = fetch_url(url, f"{symbol}_{interval}")
    # Parse klines: [open_time, open, high, low, close, volume, close_time, ...]
    parsed = []
    for k in raw:
        parsed.append({
            "open_time": k[0],
            "open_time_utc": datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).isoformat(),
            "open": k[1],
            "high": k[2],
            "low": k[3],
            "close": k[4],
            "volume": k[5],
            "close_time": k[6],
            "quote_asset_volume": k[7],
            "num_trades": k[8],
        })
    return parsed

def fetch_order_book(symbol, limit=10):
    url = f"{BINANCE_BASE}/depth?symbol={symbol}&limit={limit}"
    raw = fetch_url(url, f"{symbol}_DEPTH")
    return {
        "lastUpdateId": raw["lastUpdateId"],
        "bids": [{"price": b[0], "qty": b[1]} for b in raw["bids"]],
        "asks": [{"price": a[0], "qty": a[1]} for a in raw["asks"]],
    }

def fetch_fear_greed():
    data = fetch_url(FNG_URL, "FNG")
    entry = data["data"][0]
    return {
        "value": int(entry["value"]),
        "value_classification": entry["value_classification"],
        "timestamp": entry["timestamp"],
        "time_until_update": entry.get("time_until_update"),
    }

def fetch_btc_dominance():
    data = fetch_url(COINGECKO_GLOBAL, "COINGECKO_GLOBAL")
    market_cap_pct = data["data"]["market_cap_percentage"]
    total_market_cap = data["data"]["total_market_cap"].get("usd")
    total_volume = data["data"]["total_volume"].get("usd")
    return {
        "btc_dominance_pct": round(market_cap_pct.get("btc", 0), 4),
        "eth_dominance_pct": round(market_cap_pct.get("eth", 0), 4),
        "total_market_cap_usd": total_market_cap,
        "total_24h_volume_usd": total_volume,
        "active_cryptocurrencies": data["data"].get("active_cryptocurrencies"),
        "markets": data["data"].get("markets"),
    }

def get_gainers_losers(all_tickers, min_volume_usd=1_000_000, top_n=10):
    usdt_pairs = []
    for t in all_tickers:
        if not t["symbol"].endswith("USDT"):
            continue
        try:
            price = float(t["lastPrice"])
            volume_usd = float(t["quoteVolume"])
            pct_change = float(t["priceChangePercent"])
        except (ValueError, KeyError):
            continue
        if volume_usd < min_volume_usd or price <= 0:
            continue
        usdt_pairs.append({
            "symbol": t["symbol"],
            "price": t["lastPrice"],
            "price_change_pct_24h": t["priceChangePercent"],
            "price_change_24h": t["priceChange"],
            "volume_usdt": round(volume_usd, 2),
            "high_24h": t["highPrice"],
            "low_24h": t["lowPrice"],
        })

    sorted_by_change = sorted(usdt_pairs, key=lambda x: float(x["price_change_pct_24h"]), reverse=True)
    return {
        "top_gainers": sorted_by_change[:top_n],
        "top_losers": sorted_by_change[-top_n:][::-1],
    }

def format_ticker(t):
    return {
        "symbol": t["symbol"],
        "price": t["lastPrice"],
        "price_change_24h": t["priceChange"],
        "price_change_pct_24h": t["priceChangePercent"],
        "high_24h": t["highPrice"],
        "low_24h": t["lowPrice"],
        "volume": t["volume"],
        "quote_volume_usdt": t["quoteVolume"],
        "open_price": t["openPrice"],
        "prev_close": t["prevClosePrice"],
        "bid": t["bidPrice"],
        "ask": t["askPrice"],
        "weighted_avg_price": t["weightedAvgPrice"],
        "count": t["count"],
    }

def main():
    result = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "spot_tickers": {},
        "gainers_losers": {},
        "fear_greed_index": {},
        "btc_dominance": {},
        "btc_1h_klines_24": [],
        "eth_1h_klines_24": [],
        "btc_order_book_top10": {},
        "btc_4h_klines_6": [],
        "errors": {},
    }

    tasks = {
        "btc_ticker":    lambda: fetch_ticker_24hr("BTCUSDT"),
        "eth_ticker":    lambda: fetch_ticker_24hr("ETHUSDT"),
        "sol_ticker":    lambda: fetch_ticker_24hr("SOLUSDT"),
        "bch_ticker":    lambda: fetch_ticker_24hr("BCHUSDT"),
        "all_tickers":   fetch_all_tickers,
        "fear_greed":    fetch_fear_greed,
        "btc_dominance": fetch_btc_dominance,
        "btc_1h":        lambda: fetch_klines("BTCUSDT", "1h", 24),
        "eth_1h":        lambda: fetch_klines("ETHUSDT", "1h", 24),
        "btc_depth":     lambda: fetch_order_book("BTCUSDT", 10),
        "btc_4h":        lambda: fetch_klines("BTCUSDT", "4h", 6),
    }

    fetched = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                fetched[key] = future.result()
                print(f"  ✓ {key}")
            except Exception as e:
                fetched[key] = None
                result["errors"][key] = str(e)
                print(f"  ✗ {key}: {e}")

    # Spot tickers
    for sym, key in [("BTCUSDT","btc_ticker"),("ETHUSDT","eth_ticker"),
                     ("SOLUSDT","sol_ticker"),("BCHUSDT","bch_ticker")]:
        if fetched.get(key):
            result["spot_tickers"][sym] = format_ticker(fetched[key])

    # Gainers / losers
    if fetched.get("all_tickers"):
        result["gainers_losers"] = get_gainers_losers(fetched["all_tickers"])

    # Fear & Greed
    if fetched.get("fear_greed"):
        result["fear_greed_index"] = fetched["fear_greed"]

    # BTC dominance
    if fetched.get("btc_dominance"):
        result["btc_dominance"] = fetched["btc_dominance"]

    # Klines
    if fetched.get("btc_1h"):
        result["btc_1h_klines_24"] = fetched["btc_1h"]
    if fetched.get("eth_1h"):
        result["eth_1h_klines_24"] = fetched["eth_1h"]
    if fetched.get("btc_4h"):
        result["btc_4h_klines_6"] = fetched["btc_4h"]

    # Order book
    if fetched.get("btc_depth"):
        result["btc_order_book_top10"] = fetched["btc_depth"]

    out_path = "/home/admin/charon/crypto_market_data.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved to {out_path}")
    return result

if __name__ == "__main__":
    data = main()
    # Print a quick summary
    print("\n=== SUMMARY ===")
    for sym, t in data["spot_tickers"].items():
        print(f"  {sym}: ${t['price']} ({t['price_change_pct_24h']}%)")
    if data["fear_greed_index"]:
        fg = data["fear_greed_index"]
        print(f"  Fear & Greed: {fg['value']} ({fg['value_classification']})")
    if data["btc_dominance"]:
        print(f"  BTC Dominance: {data['btc_dominance']['btc_dominance_pct']}%")
    if data["gainers_losers"].get("top_gainers"):
        print(f"  Top gainer: {data['gainers_losers']['top_gainers'][0]['symbol']} "
              f"+{data['gainers_losers']['top_gainers'][0]['price_change_pct_24h']}%")
    if data["gainers_losers"].get("top_losers"):
        print(f"  Top loser:  {data['gainers_losers']['top_losers'][0]['symbol']} "
              f"{data['gainers_losers']['top_losers'][0]['price_change_pct_24h']}%")
