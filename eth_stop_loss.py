#!/usr/bin/env python3
"""ETH止损守护 - 150x版"""
import json, hashlib, hmac, time, requests, sys
sys.path.append('/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt
_CREDS = _decrypt()
BINANCE_KEY = _CREDS.get('BINANCE_API_KEY', '')
BINANCE_SECRET = _CREDS.get('BINANCE_API_SECRET', '')
BASE = 'https://fapi.binance.com'

STOP_PRICE = 2180.0
QTY = 0.449
CHECK_INTERVAL = 5
LOG_FILE = '/home/admin/charon/eth_stop_loss.log'

def log(msg):
    t = time.strftime('%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def req(method, path, params=None):
    ts = int(time.time()*1000)
    q = params or {}
    q['timestamp'] = ts
    qs = '&'.join([f'{k}={v}' for k,v in sorted(q.items())])
    sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'{BASE}{path}?{qs}&signature={sig}'
    r = requests.get(url, headers={'X-MBX-APIKEY': BINANCE_KEY}) if method == 'GET' else requests.post(url, headers={'X-MBX-APIKEY': BINANCE_KEY})
    return r.json()

log(f'ETH止损150x版启动 | 止损${STOP_PRICE} | 每5s检查')

while True:
    try:
        ticker = req('GET', '/fapi/v1/premiumIndex', {'symbol': 'ETHUSDT'})
        mark = float(ticker.get('markPrice', 0))
        
        if mark <= STOP_PRICE and mark > 0:
            log(f'止损触发: ${mark:.2f} ≤ ${STOP_PRICE}')
            r = req('POST', '/fapi/v1/order', {
                'symbol': 'ETHUSDT', 'side': 'SELL', 'type': 'MARKET',
                'quantity': QTY, 'positionSide': 'LONG'
            })
            if 'orderId' in r:
                log(f'平仓成功! orderId={r["orderId"]}')
                break
            else:
                log(f'平仓失败: {r}')
        elif mark <= STOP_PRICE + 5:
            log(f'接近止损: ${mark:.2f}')
    except Exception as e:
        log(f'异常: {e}')
    time.sleep(CHECK_INTERVAL)
