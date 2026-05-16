#!/usr/bin/env python3
"""紧急减仓AIGENSYN V2 — 直接用ccxt交易"""
import os, json, ccxt, hashlib, base64, time, sys
from cryptography.fernet import Fernet

LOG = '/home/admin/charon/emergency_reduce.log'
RAW_SYM = 'AIGENSYNUSDT'
CCXT_SYM = 'AIGENSYN/USDT:USDT'

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

def get_aigensyn_pos(ex):
    """获取AIGENSYN持仓"""
    try:
        pos = ex.fetch_position(CCXT_SYM)
        qty = float(pos['contracts'] or 0)
        if qty != 0:
            return pos
        return None
    except Exception as e:
        log(f'fetch_position失败: {e}')
        # fallback
        positions = ex.fetch_positions([CCXT_SYM])
        for p in positions:
            qty = float(p['contracts'] or 0)
            if qty != 0:
                return p
        return None

def main():
    log('='*50)
    log('紧急减仓V2启动')
    ex = get_exchange()
    
    # 1. 检查持仓
    pos = get_aigensyn_pos(ex)
    if not pos:
        log('AIGENSYN无持仓，退出')
        return
    
    qty = float(pos['contracts'])
    entry = float(pos['entryPrice'])
    mark = float(pos['markPrice'])
    liq = float(pos['liquidationPrice'])
    pnl = float(pos['unrealizedPnl'])
    liq_dist = (mark - liq) / mark * 100
    
    log(f'持仓: {qty}个 @ ${entry:.6f}')
    log(f'当前: ${mark:.6f} | PnL: ${pnl:.2f}')
    log(f'清算: ${liq:.6f} | 距离: {liq_dist:.1f}%')
    
    if liq_dist >= 15:
        log('✅ 清算距离>=15%，安全，无需操作')
        return
    
    log('⚠️ 清算距离<15%，执行减仓')
    
    # 2. 先降杠杆到3x，降低风险
    log('Step 1: 降杠杆到3x...')
    try:
        ex.set_leverage(3, CCXT_SYM)
        log('杠杆已设为3x')
    except Exception as e:
        log(f'降杠杆失败: {e}')
    
    time.sleep(1)
    
    # 3. 减仓80%（留20%防突然反弹）
    reduce_pct = 0.8
    reduce_qty = int(qty * reduce_pct)
    if reduce_qty < 1:
        reduce_qty = int(qty)
        
    log(f'Step 2: 减仓{reduce_pct*100:.0f}% = {reduce_qty}个...')
    try:
        order = ex.create_market_sell_order(CCXT_SYM, reduce_qty, {'positionSide': 'LONG', 'reduceOnly': True})
        log(f'减仓成功: orderId={order.get("id","?")} 成交={order.get("filled",0)}')
    except Exception as e:
        log(f'reduceOnly减仓失败: {e}')
        try:
            order = ex.create_market_sell_order(CCXT_SYM, reduce_qty, {'positionSide': 'LONG'})
            log(f'减仓成功(无reduceOnly): orderId={order.get("id","?")}')
        except Exception as e2:
            log(f'减仓再次失败: {e2}')
            # 最后尝试：分批小量
            log('尝试分批减仓...')
            remaining = reduce_qty
            batch = 10000
            while remaining > 0:
                b = min(remaining, batch)
                try:
                    ex.create_market_sell_order(CCXT_SYM, b, {'positionSide': 'LONG'})
                    log(f'分批减仓{b}个成功')
                    remaining -= b
                except Exception as e3:
                    log(f'分批减仓{b}个失败: {e3}')
                    break
                time.sleep(1)
    
    time.sleep(2)
    
    # 4. 检查剩余
    pos2 = get_aigensyn_pos(ex)
    if pos2:
        rem = float(pos2['contracts'])
        pnl2 = float(pos2['unrealizedPnl'])
        liq2 = float(pos2['liquidationPrice'])
        mark2 = float(pos2['markPrice'])
        liq_dist2 = (mark2 - liq2) / mark2 * 100
        log(f'剩余: {rem}个 | PnL: ${pnl2:.2f} | 清算距离: {liq_dist2:.1f}%')
        if liq_dist2 >= 20:
            log('✅ 剩余仓位清算距离安全')
        else:
            log(f'⚠️ 剩余仓位清算距离仍不足，建议监控')
    else:
        log('✅ 仓位已清空')
    
    # 5. 更新ds0_status
    try:
        ex2 = get_exchange()
        bal = ex2.fetch_balance()
        info = bal['info']
        wallet = float(info.get('totalWalletBalance', 0))
        avail = float(info.get('availableBalance', 0))
        log(f'钱包: ${wallet:.2f} | 可用: ${avail:.2f}')
    except:
        pass
    
    log('=== 紧急减仓完成 ===')

if __name__ == '__main__':
    main()
