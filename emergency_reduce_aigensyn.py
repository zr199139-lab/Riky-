#!/usr/bin/env python3
"""紧急减仓AIGENSYN — 清算距离仅11.6%，stop_loss_multi平仓失败"""
import os, json, ccxt, hashlib, base64, time
from cryptography.fernet import Fernet

LOG = '/home/admin/charon/emergency_reduce.log'
SYM = 'AIGENSYN/USDT:USDT'
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

def get_position(ex):
    positions = ex.fetch_positions()
    print(f'搜索 {RAW_SYM} 在 {len(positions)} 个持仓中', flush=True)
    for i, p in enumerate(positions):
        sym = p['symbol']
        qty = float(p['contracts'])
        print(f'  [{i}] sym={repr(sym)} qty={qty} raw_sym_in={RAW_SYM in sym}', flush=True)
        if qty != 0 and RAW_SYM in sym:
            print(f'  -> 匹配! 返回此持仓', flush=True)
            return p
    return None

def main():
    log('=== 紧急减仓启动 ===')
    ex = get_exchange()
    
    # 1. 检查当前持仓
    pos = get_position(ex)
    if not pos:
        log('AIGENSYN无持仓，退出')
        return
    
    qty = float(pos['contracts'])
    entry = float(pos['entryPrice'])
    mark = float(pos['markPrice'])
    liq = float(pos['liquidationPrice'])
    pnl = float(pos['unrealizedPnl'])
    lev = float(pos['leverage']) if pos.get('leverage') else 20
    
    liq_dist = (mark - liq) / mark * 100
    log(f'当前持仓: {qty}个 @ ${entry:.6f}')
    log(f'当前价: ${mark:.6f} | 浮亏: ${pnl:.2f}')
    log(f'清算价: ${liq:.6f} | 清算距离: {liq_dist:.1f}%')
    
    # 2. 策略：先降杠杆到5x，再减仓50%，再平剩余
    # 先降杠杆（全仓模式可以直接调）
    log('Step 1: 降低杠杆到5x...')
    try:
        lev_resp = ex.set_leverage(5, SYM)
        log(f'降杠杆响应: {json.dumps(lev_resp)[:200] if lev_resp else "null"}')
    except Exception as e:
        log(f'降杠杆失败(可能可忽略): {e}')
    
    # 2. 减仓50%
    reduce_qty = int(qty * 0.5)
    log(f'Step 2: 减仓 {reduce_qty}个 (50%)...')
    try:
        order = ex.create_market_sell_order(SYM, reduce_qty, {'positionSide': 'LONG'})
        log(f'减仓成功: {json.dumps(order)[:200]}')
    except Exception as e:
        log(f'减仓失败: {e}')
        # 尝试不加positionSide
        try:
            order = ex.create_market_sell_order(SYM, reduce_qty)
            log(f'减仓成功(无side): {json.dumps(order)[:200]}')
        except Exception as e2:
            log(f'减仓再次失败: {e2}')
    
    time.sleep(2)
    
    # 3. 检查剩余仓位并继续
    pos2 = get_position(ex)
    if pos2:
        remaining = float(pos2['contracts'])
        log(f'剩余仓位: {remaining}个')
        if remaining > 0:
            log('Step 3: 平剩余仓位...')
            try:
                order2 = ex.create_market_sell_order(SYM, remaining, {'positionSide': 'LONG'})
                log(f'平仓成功: {json.dumps(order2)[:200]}')
            except Exception as e:
                log(f'平仓失败: {e}')
                # 再降杠杆试试
                try:
                    ex.set_leverage(3, SYM)
                    time.sleep(1)
                    order2 = ex.create_market_sell_order(SYM, remaining, {'positionSide': 'LONG'})
                    log(f'降杠杆后平仓成功: {json.dumps(order2)[:200]}')
                except Exception as e2:
                    log(f'降杠杆后平仓仍失败: {e2}')
    else:
        log('✅ 仓位已清空')
    
    # 4. 最终检查
    time.sleep(2)
    pos3 = get_position(ex)
    if pos3 and float(pos3['contracts']) > 0:
        remaining_qty = float(pos3['contracts'])
        log(f'⚠️ 仍有剩余仓位: {remaining_qty}个')
        # 尝试分批小量平仓
        remaining = int(float(pos3['contracts']))
        while remaining > 0:
            batch = min(remaining, 10000)
            try:
                order = ex.create_market_sell_order(SYM, batch, {'positionSide': 'LONG'})
                log(f'分批平仓{batch}个成功')
                remaining -= batch
            except:
                log(f'分批平仓{batch}个失败，退出循环')
                break
            time.sleep(1)
    else:
        log('✅ 最终确认: 仓位已清空')
    
    log('=== 紧急减仓完成 ===')

if __name__ == '__main__':
    main()
