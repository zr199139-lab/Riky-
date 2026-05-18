#!/usr/bin/env python3
"""
[DS-0] 紧急直接平仓 · 跳过config层
用rest API直接close ETH LONG + BCH LONG
"""
import os, json, time, hashlib, base64, hmac, requests
from cryptography.fernet import Fernet
from datetime import datetime

LOG = '/home/admin/charon/bot_logs/ds0_close_emergency.log'

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def get_creds():
    d = os.path.expanduser('~/.hermes/mempalace/secure')
    with open(os.path.join(d, '.key'), 'rb') as f: salt = f.read()
    key = base64.urlsafe_b64encode(hashlib.sha256(salt).digest())
    creds = json.loads(Fernet(key).decrypt(open(os.path.join(d, 'credentials.enc'), 'rb').read()).decode())
    return creds['BINANCE_API_KEY'], creds['BINANCE_API_SECRET']

def binance_request(ak, sk, method, path, params=None):
    ts = int(time.time() * 1000)
    p = params or {}
    p['timestamp'] = ts
    p['recvWindow'] = 5000
    qs = '&'.join(f'{k}={v}' for k, v in sorted(p.items()))
    sig = hmac.new(sk.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'https://fapi.binance.com{path}?{qs}&signature={sig}'
    hdrs = {'X-MBX-APIKEY': ak}
    if method == 'POST':
        r = requests.post(url, headers=hdrs)
    else:
        r = requests.get(url, headers=hdrs)
    return r

def main():
    log('=' * 60)
    log('DS-0 紧急平仓 · 直接API调用')
    
    ak, sk = get_creds()
    
    # Step 1: Check current positions
    r = binance_request(ak, sk, 'GET', '/fapi/v2/positionRisk')
    if r.status_code != 200:
        log(f'❌ 获取持仓失败: {r.status_code} {r.text}')
        return
    
    positions = r.json()
    to_close = []
    for p in positions:
        amt = float(p.get('positionAmt', '0') or 0)
        if abs(amt) > 0.001:
            sym = p['symbol']
            side = 'LONG' if amt > 0 else 'SHORT'
            liq = float(p.get('liquidationPrice', 0) or 0)
            mark = float(p['markPrice'])
            entry = float(p['entryPrice'])
            upnl = float(p['unRealizedProfit'])
            liq_dist = (mark - liq) / mark * 100 if liq > 0 and side == 'LONG' else (liq - mark) / mark * 100 if liq > 0 else 0
            log(f'📊 {sym} {side} {abs(amt):.4f} @ ${entry:.2f} | mark=${mark:.2f} | liq=${liq:.2f} | dist={liq_dist:.2f}% | upnl=${upnl:.2f}')
            to_close.append(p)
    
    if not to_close:
        log('✅ 无持仓需平仓')
        return
    
    log(f'⚠️ 发现 {len(to_close)} 个持仓，开始平仓...')
    
    for p in to_close:
        sym = p['symbol']
        amt = float(p['positionAmt'])
        side = 'LONG' if amt > 0 else 'SHORT'
        abs_amt = abs(amt)
        
        log(f'🔄 平仓 {sym} {side} {abs_amt:.4f}...')
        
        # For LONG position: SELL to close (hedge mode needs positionSide)
        if amt > 0:
            params = {
                'symbol': sym,
                'side': 'SELL',
                'type': 'MARKET',
                'quantity': abs_amt,
                'positionSide': 'LONG',
            }
        else:
            params = {
                'symbol': sym,
                'side': 'BUY',
                'type': 'MARKET',
                'quantity': abs_amt,
                'positionSide': 'SHORT',
            }
        
        r2 = binance_request(ak, sk, 'POST', '/fapi/v1/order', params)
        if r2.status_code == 200:
            order = r2.json()
            log(f'✅ 平仓成功! {sym} {side} {abs_amt:.4f} | orderId={order.get("orderId","?")} | 成交价={order.get("avgPrice","?")}')
        else:
            log(f'❌ 方法1失败: {r2.status_code} {r2.text}')
            # Try with ccxt instead
            time.sleep(1)
            try:
                import ccxt
                d = os.path.expanduser('~/.hermes/mempalace/secure')
                with open(os.path.join(d, '.key'), 'rb') as f: salt = f.read()
                key = base64.urlsafe_b64encode(hashlib.sha256(salt).digest())
                creds2 = json.loads(Fernet(key).decrypt(open(os.path.join(d, 'credentials.enc'), 'rb').read()).decode())
                ex = ccxt.binance({
                    'apiKey': creds2['BINANCE_API_KEY'],
                    'secret': creds2['BINANCE_API_SECRET'],
                    'options': {'defaultType': 'future'},
                })
                ex.load_markets()
                if amt > 0:
                    order2 = ex.create_market_sell_order(f'{sym}:USDT', abs_amt, {'positionSide': 'LONG'})
                else:
                    order2 = ex.create_market_buy_order(f'{sym}:USDT', abs_amt, {'positionSide': 'SHORT'})
                log(f'✅ CCXT平仓成功! orderId={order2.get("id","?")}')
            except Exception as e2:
                log(f'❌ CCXT也失败: {e2}')
        
        time.sleep(1)  # rate limit
    
    # Step 3: Verify
    time.sleep(2)
    rv = binance_request(ak, sk, 'GET', '/fapi/v2/positionRisk')
    if rv.status_code == 200:
        still_open = []
        for p in rv.json():
            if abs(float(p.get('positionAmt', '0') or 0)) > 0.001:
                still_open.append(f"{p['symbol']} {p.get('positionAmt')}")
        if still_open:
            log(f'⚠️ 仍有持仓: {", ".join(still_open)}')
        else:
            log('✅ 全部平仓完成! 无残留持仓')
    
    # Step 4: Wallet status
    rb = binance_request(ak, sk, 'GET', '/fapi/v2/account')
    if rb.status_code == 200:
        a = rb.json()
        log(f'💰 钱包: ${float(a["totalWalletBalance"]):.2f} | 可用: ${float(a["availableBalance"]):.2f} | upnl: ${float(a["totalUnrealizedProfit"]):.2f}')
    
    log('=' * 60)
    log('DS-0 紧急平仓完成')

if __name__ == '__main__':
    main()
