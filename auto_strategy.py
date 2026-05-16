#!/usr/bin/env python3
"""DS-0 暗黑星火 · 战略评估 (4h一次, LLM辅助)
检查市场状态, 自动调整网格参数
"""
import os, hashlib, base64, json, ccxt, time
from cryptography.fernet import Fernet

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

now = time.time()

# === 数据采集 ===
t = ex.fetch_ticker('POLYX/USDT:USDT')
pos = ex.fetch_positions(['POLYX/USDT:USDT'])
bal = ex.fetch_balance()
orders = ex.fetch_open_orders('POLYX/USDT:USDT')
fund = ex.fetch_funding_rate('POLYX/USDT:USDT')

# Open interest
try:
    oi_raw = ex.fetch_open_interest('POLYX/USDT:USDT')
    oi = float(oi_raw['openInterestAmount'])
except:
    oi = 0

info = bal['info']
wallet = float(info['totalWalletBalance'])
avail = float(info.get('availableBalance', 0))

q, e, pnl, liq = 0, 0, 0, 0
for p in pos:
    if float(p['contracts']) != 0:
        q = float(p['contracts'])
        e = float(p['entryPrice'])
        pnl = float(p['unrealizedPnl'])
        liq = float(p['liquidationPrice'])

price = t['last']
fr = float(fund['fundingRate']) * 100
dist = (price - liq) / price * 100 if liq > 0 else 999

sells = [o for o in orders if o['side']=='sell']
total_sell_qty = sum(float(o['amount']) for o in sells)

# === 自动调整逻辑 ===

# 1. 检查卖单是否超出持仓
if total_sell_qty > q * 1.05:
    print(f"⚠️ 卖单({int(total_sell_qty)})超持仓({int(q)}), 需调整")
    # Cancel all sells and redeploy with correct size
    for s in sells:
        ex.cancel_order(s['id'], 'POLYX/USDT:USDT')
        time.sleep(0.1)
    # Redeploy at correct size
    layer_qty = int(50 * 20 / price)
    new_qty = int(q * 0.8)  # sell max 80% of position
    levels = [0.06170, 0.06180, 0.06190]
    for lv in levels:
        sell_qty = min(layer_qty, new_qty)
        if sell_qty <= 0: break
        ex.create_limit_sell_order('POLYX/USDT:USDT', sell_qty, lv, {'positionSide': 'LONG'})
        new_qty -= sell_qty
        time.sleep(0.1)
    print(f"✅ 卖单已重挂 @ {levels[:3]}")
    sells = [o for o in ex.fetch_open_orders('POLYX/USDT:USDT') if o['side']=='sell']

# 2. 检查费率方向
if fr > 0:
    print(f"⚠️ 费率翻正({fr:.4f}%), 多头付钱! 考虑减仓")

# 3. 检查清算距离
if dist < 5:
    print(f"🔴 清算仅{dist:.1f}%! 建议减仓50%")
elif dist < 10:
    print(f"🟡 清算{dist:.1f}%, 注意监控")

# 4. 检查止盈是否全成交
if len(sells) == 0 and q > 0:
    print(f"✅ 全部止盈已成交! 重新部署网格...")
    layer_qty = int(50 * 20 / price)
    levels = [0.06170, 0.06180, 0.06190]
    remaining = int(q * 0.8)
    for lv in levels:
        sell_qty = min(layer_qty, remaining)
        if sell_qty <= 0: break
        ex.create_limit_sell_order('POLYX/USDT:USDT', sell_qty, lv, {'positionSide': 'LONG'})
        remaining -= sell_qty
        time.sleep(0.1)
    print(f"✅ 新网格已部署 @ {levels}")

# === 输出报告 ===
print(f"\n{'='*40}")
print(f"📊 暗黑星火 战略评估")
print(f"{'='*40}")
print(f"时间: {time.strftime('%H:%M')} UTC")
print(f"价格: ${price:.5f}")
print(f"持仓: {int(q):,} LONG @ ${e:.5f}")
print(f"PnL: ${pnl:.2f}")
print(f"清算: ${liq:.5f} ({dist:.1f}%)")
print(f"可用: ${avail:.2f}  钱包: ${wallet:.2f}")
print(f"费率: {fr:.4f}%/8h")
print(f"止盈: {len(sells)}单 总卖{int(total_sell_qty):,}个")
print(f"OI: {int(oi):,} POLYX")
print(f"{'='*40}")
print(f"建议: {'持有等弹' if dist>10 else '注意风险'}")
print(f"下次评估: 4h后")
