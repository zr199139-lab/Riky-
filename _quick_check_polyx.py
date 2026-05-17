#!/usr/bin/env python3
"""DS-0 POLYX快速巡检"""
import os, sys, json, hashlib, base64
from cryptography.fernet import Fernet

d = os.path.expanduser('~/.hermes/mempalace/secure')
with open(os.path.join(d, '.key'), 'rb') as f:
    salt = f.read()
key = base64.urlsafe_b64encode(hashlib.sha256(salt).digest())
f = Fernet(key)
with open(os.path.join(d, 'credentials.enc'), 'rb') as fp:
    data = fp.read()
creds = json.loads(f.decrypt(data).decode())

import ccxt
ex = ccxt.binance({
    'apiKey': creds['BINANCE_API_KEY'],
    'secret': creds['BINANCE_API_SECRET'],
    'options': {'defaultType': 'future'}
})
ex.load_markets()

# 余额
bal = ex.fetch_balance()
info = bal['info']
wallet = float(info.get('totalWalletBalance', 0))
avail = float(info.get('availableBalance', 0))

# POLYX仓位
pos_list = ex.fetch_positions(['POLYX/USDT:USDT'])
polyx_pos = None
for p in pos_list:
    if float(p['contracts']) != 0:
        polyx_pos = {
            'symbol': p['symbol'],
            'side': 'long' if float(p['contracts']) > 0 else 'short',
            'qty': abs(float(p['contracts'])),
            'entry': float(p['entryPrice']),
            'mark': float(p['markPrice']),
            'liq': float(p['liquidationPrice']) if p['liquidationPrice'] else 0,
            'pnl': round(float(p['unrealizedPnl']), 2),
            'margin': float(p['initialMargin']),
            'leverage': float(p['leverage']) if p.get('leverage') else 0,
        }

# POLYX价格和费率
ticker = ex.fetch_ticker('POLYX/USDT:USDT')
fr = ex.fetch_funding_rate('POLYX/USDT:USDT')
funding_rate = float(fr['fundingRate'])
funding_pct = round(funding_rate * 100, 4)

# 挂单
orders = ex.fetch_open_orders('POLYX/USDT:USDT')
open_orders = [{
    'id': o['id'], 'side': o['side'], 'type': o['type'],
    'price': o['price'], 'amount': o['amount'], 'remaining': o['remaining']
} for o in orders]

result = {
    'time': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
    'price': ticker['last'],
    'price_change_24h': ticker['percentage'],
    'high_24h': ticker['high'],
    'low_24h': ticker['low'],
    'wallet': wallet,
    'avail': avail,
    'funding_rate': funding_rate,
    'funding_pct_8h': funding_pct,
    'position': polyx_pos,
    'open_orders': open_orders,
}

if polyx_pos:
    if polyx_pos['side'] == 'long':
        liq_dist = (polyx_pos['mark'] - polyx_pos['liq']) / polyx_pos['mark'] * 100
    else:
        liq_dist = (polyx_pos['liq'] - polyx_pos['mark']) / polyx_pos['mark'] * 100
    result['liq_dist_pct'] = round(liq_dist, 2)

print(json.dumps(result, indent=2))
