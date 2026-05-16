#!/usr/bin/env python3
"""DS-0 暗黑星火 · 15分钟自动巡检
无LLM调用, 纯REST API检查, 只在异常时通知
"""
import os, hashlib, base64, json, ccxt, time
from cryptography.fernet import Fernet

# === 凭据 ===
d = os.path.expanduser('~/.hermes/mempalace/secure')
with open(os.path.join(d, '.key'), 'rb') as f: salt = f.read()
key = base64.urlsafe_b64encode(hashlib.sha256(salt).digest())
f = Fernet(key)
with open(os.path.join(d, 'credentials.enc'), 'rb') as fp: data = fp.read()
creds = json.loads(f.decrypt(data).decode())

ex = ccxt.binance({
    'apiKey': creds['BINANCE_API_KEY'],
    'secret': creds['BINANCE_API_SECRET'],
    'options': {'defaultType': 'future'}
})

# === 数据采集 ===
t = ex.fetch_ticker('POLYX/USDT:USDT')
pos = ex.fetch_positions(['POLYX/USDT:USDT'])
bal = ex.fetch_balance()
orders = ex.fetch_open_orders('POLYX/USDT:USDT')

info = bal['info']
wallet = float(info['totalWalletBalance'])
avail = float(info.get('availableBalance', 0))

# === 分析 ===
q, e, pnl, liq = 0, 0, 0, 0
for p in pos:
    if float(p['contracts']) != 0:
        q = float(p['contracts'])
        e = float(p['entryPrice'])
        pnl = float(p['unrealizedPnl'])
        liq = float(p['liquidationPrice'])

price = t['last']
dist_to_liq = (price - liq) / price * 100 if liq > 0 else 999

# === 检查挂单状态 ===
sells = [o for o in orders if o['side']=='sell']
total_sell = sum(float(o['amount']) for o in sells)

# === 输出状态 (用于cron) ===
print(f"TIME={time.strftime('%H:%M')}")
print(f"PRICE={price:.5f}")
print(f"POS={int(q)}")
print(f"ENTRY={e:.5f}")
print(f"PNL={pnl:.2f}")
print(f"LIQ={liq:.5f}")
print(f"DIST={dist_to_liq:.1f}")
print(f"WALLET={wallet:.2f}")
print(f"AVAIL={avail:.2f}")
print(f"SELLS={len(sells)}")
print(f"SELL_QTY={int(total_sell)}")

# === 异常检查 ===
alerts = []
if dist_to_liq < 5:  alerts.append(f"🔴 清算逼近! 仅{dist_to_liq:.1f}%")
elif dist_to_liq < 8: alerts.append(f"🟡 清算注意 {dist_to_liq:.1f}%")

if avail < 50: alerts.append(f"🟡 可用余额仅${avail:.0f}")

if total_sell > q * 1.1:
    alerts.append(f"🔴 卖单超持仓! 需取消多余单")

if alerts:
    print(f"ALERT={' | '.join(alerts)}")
else:
    print("ALERT=OK")
