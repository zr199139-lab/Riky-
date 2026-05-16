#!/usr/bin/env python3
"""紧急修复：真正平仓AIGENSYN（stop_loss_multi的close_position有bug，用BUY平多导致加仓）"""
import os, json, ccxt, hashlib, base64, time
from cryptography.fernet import Fernet

LOG = '/home/admin/charon/emergency_fix.log'
CCXT_SYM = 'AIGENSYN/USDT:USDT'
RAW_SYM = 'AIGENSYNUSDT'

def log(msg):
    t = time.strftime('%m-%d %H:%M:%S')
    with open(LOG, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def get_exchange():
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
    ex.load_markets()
    return ex

def main():
    log('='*60)
    log('紧急修复: 真正平仓AIGENSYN (修复stop_loss_multi加仓bug)')
    ex = get_exchange()
    
    # 1. 查持仓
    positions = ex.fetch_positions([CCXT_SYM])
    pos = None
    for p in positions:
        qty = float(p['contracts'] or 0)
        if qty != 0:
            pos = p
            break
    
    if not pos:
        log('AIGENSYN无持仓')
        return
    
    qty = float(pos['contracts'])
    entry = float(pos['entryPrice'])
    mark = float(pos['markPrice'])
    liq = float(pos['liquidationPrice'])
    pnl = float(pos['unrealizedPnl'])
    liq_dist = (mark - liq) / mark * 100
    
    log(f'当前持仓: {qty}个 LONG @ ${entry:.6f}')
    log(f'当前价: ${mark:.6f} | PnL: ${pnl:.2f}')
    log(f'清算价: ${liq:.6f} | 清算距离: {liq_dist:.1f}%')
    
    # 2. 真正平仓：用SELL side + LONG positionSide
    log(f'平仓 {qty}个 LONG (SELL+LONG)...')
    
    # 方法1: ccxt的create_market_sell_order + positionSide
    try:
        order = ex.create_market_sell_order(CCXT_SYM, int(qty), {'positionSide': 'LONG'})
        log(f'✅ 平仓成功! orderId={order.get("id","?")}')
    except Exception as e:
        log(f'平仓失败: {e}')
        # 方法2: 用reduceOnly
        try:
            order = ex.create_market_sell_order(CCXT_SYM, int(qty), {'positionSide': 'LONG', 'reduceOnly': True})
            log(f'✅ reduceOnly平仓成功! orderId={order.get("id","?")}')
        except Exception as e2:
            log(f'reduceOnly也失败: {e2}')
            # 方法3: 分批
            remaining = int(qty)
            batch = 10000
            while remaining > 0:
                b = min(remaining, batch)
                try:
                    ex.create_market_sell_order(CCXT_SYM, b, {'positionSide': 'LONG'})
                    log(f'分批平仓{b}个成功')
                    remaining -= b
                except Exception as e3:
                    log(f'分批平仓{b}个失败: {e3}')
                    break
                time.sleep(1)
    
    time.sleep(2)
    
    # 3. 验证
    positions2 = ex.fetch_positions([CCXT_SYM])
    still_have = False
    for p in positions2:
        q2 = float(p['contracts'] or 0)
        if q2 != 0:
            log(f'⚠️ 仍有仓位: {p["symbol"]} {q2}个 side={p["side"]} pnl={p["unrealizedPnl"]}')
            still_have = True
    
    if not still_have:
        log('✅ 仓位完全清空!')
    
    # 4. 最终钱包
    bal = ex.fetch_balance()
    info = bal['info']
    log(f'钱包: ${float(info["totalWalletBalance"]):.2f} | 可用: ${float(info["availableBalance"]):.2f}')
    
    log('='*60)
    log('修复完成')

if __name__ == '__main__':
    main()
