#!/usr/bin/env python3
"""
暗黑星火 · 现货执行器 v1.0
===========================
全托管 $250 现货交易系统。只执行GPT决策，不自主交易。
"""
import os, json, time, sys, ccxt
from datetime import datetime
from pathlib import Path

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
SECURE_DIR = Path.home() / '.hermes/mempalace/secure'
CONFIG_FILE = LOGS / 'shared_config.json'
STATE_FILE = LOGS / 'spot_executor_state.json'

sys.path.insert(0, str(SECURE_DIR))
from decrypt_and_run import decrypt

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOGS / 'spot_executor.log', 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def get_exchange():
    creds = decrypt()
    ex = ccxt.binance({
        'apiKey': creds.get('BINANCE_API_KEY', ''),
        'secret': creds.get('BINANCE_API_SECRET', ''),
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    ex.load_markets()
    return ex

def get_balance(ex):
    """获取现货USDT可用余额"""
    try:
        b = ex.fetch_balance()
        return float(b.get('free', {}).get('USDT', 0))
    except:
        return 0

def get_price(ex, symbol):
    """获取当前市价"""
    try:
        t = ex.fetch_ticker(symbol)
        return t['last']
    except:
        return 0

def has_position(ex, symbol, min_value=1.0):
    """检查是否有实际持仓(>1U)"""
    try:
        coin = symbol.split('/')[0]
        b = ex.fetch_balance()
        qty = float(b.get('free', {}).get(coin, 0))
        t = ex.fetch_ticker(symbol)
        value = qty * t['last']
        return value > min_value
    except:
        return False

def place_limit_order(ex, symbol, side, qty, price):
    """挂限价单"""
    try:
        order = ex.create_limit_order(symbol, side, qty, price, {'timeInForce': 'GTC'})
        if order and order.get('id'):
            log(f'[ORDER] {side} {qty} {symbol} @ {price} → #{order["id"]}')
        return order
    except Exception as e:
        log(f'  [ERR] 下单失败: {e}')
        return None

def cancel_order(ex, symbol, order_id):
    """取消订单"""
    try:
        ex.cancel_order(order_id, symbol)
        log(f'  [CANCEL] #{order_id} {symbol}')
    except:
        pass

def get_open_orders(ex, symbol=None):
    """获取未成交订单"""
    try:
        if symbol:
            return ex.fetch_open_orders(symbol)
        return ex.fetch_open_orders()
    except:
        return []

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except:
        return {'orders': {}, 'history': []}

def save_state(state):
    json.dump(state, open(STATE_FILE, 'w'), indent=2)

def read_gpt_orders():
    try:
        sc = json.load(open(CONFIG_FILE))
        spot = sc.get('spot_execution', {})
        if not spot.get('active', False):
            return []
        return spot.get('orders', [])
    except:
        return []

def check_filled_orders(ex, state):
    """检查订单是否成交"""
    changed = False
    for order_id, info in list(state.get('orders', {}).items()):
        try:
            order = ex.fetch_order(order_id, info['symbol'])
            status = order.get('status', '')
            
            if status == 'closed':
                qty = float(order.get('filled', 0))
                price = float(order.get('average', 0))
                cost = qty * price
                log(f'[FILLED] {info["side"]} {qty} {info["symbol"]} @ {price} = ${cost:.2f}')
                
                if info['side'] == 'BUY':
                    sell_price = info.get('sell_at', price * 1.08)
                    sell_order = place_limit_order(ex, info['symbol'], 'sell', qty, sell_price)
                    if sell_order:
                        state['orders'][str(sell_order['id'])] = {
                            'symbol': info['symbol'], 'side': 'SELL', 'qty': qty,
                            'price': sell_price, 'buy_cost': cost, 'buy_price': info['price']
                        }
                
                state['history'].append({
                    'time': datetime.now().isoformat(),
                    'symbol': info['symbol'], 'side': info['side'],
                    'qty': qty, 'price': price, 'cost': cost
                })
                del state['orders'][order_id]
                changed = True
            
            elif status == 'canceled' or status == 'expired':
                del state['orders'][order_id]
                changed = True
        except:
            pass
    
    if changed:
        save_state(state)
    return changed

if __name__ == '__main__':
    log('=== 现货执行器 启动 ===')
    
    ex = get_exchange()
    usdt = get_balance(ex)
    if usdt < 10:
        log(f'[ERR] USDT余额不足: ${usdt}')
        sys.exit(1)
    log(f'USDT余额: ${usdt:.2f}')
    
    state = load_state()
    
    # 1. 检查已有订单
    check_filled_orders(ex, state)
    
    # 2. 读GPT指令
    orders = read_gpt_orders()
    open_ords = get_open_orders(ex)
    
    if not orders:
        log(f'[GPT] 无执行指令 | {len(open_ords)}个已有挂单 | 空仓等待')
        save_state(state)
        sys.exit(0)
    
    log(f'[GPT] {len(orders)}个挂单指令')
    
    # 3. 已有买单币种
    existing = set()
    for oid, info in state.get('orders', {}).items():
        if info.get('side') == 'BUY':
            existing.add(info['symbol'])
    
    # 4. 执行GPT指令
    for order in orders:
        sym = order.get('symbol', '')
        buy_below = order.get('buy_below', 0)
        sell_at = order.get('sell_at', 0)
        alloc = order.get('allocation', 0)
        
        if not sym or buy_below <= 0 or alloc <= 0:
            continue
        if sym in existing:
            log(f'  {sym}: 已有买单,跳过')
            continue
        if has_position(ex, sym):
            log(f'  {sym}: 已持仓,跳过')
            continue
        
        current = get_price(ex, sym)
        if current <= 0:
            continue
        
        buy_price = min(buy_below, current * 0.99)
        qty = alloc / buy_price
        
        # 获取精度
        market = ex.market(sym)
        qty = round(qty / market['precision']['amount']) * market['precision']['amount']
        buy_price = round(buy_price / market['precision']['price']) * market['precision']['price']
        
        if qty < market['limits']['amount']['min']:
            log(f'  {sym}: 数量太小({qty}),跳过')
            continue
        
        log(f'  [GPT] {sym}: 当前${current:.2f} 挂买${buy_price:.2f} 卖${sell_at:.2f}')
        result = place_limit_order(ex, sym, 'buy', qty, buy_price)
        
        if result and result.get('id'):
            state['orders'][str(result['id'])] = {
                'symbol': sym, 'side': 'BUY', 'qty': qty,
                'price': buy_price, 'sell_at': sell_at,
                'allocation': alloc, 'created': datetime.now().isoformat()
            }
            existing.add(sym)
            time.sleep(0.5)
    
    save_state(state)
    
    open_cnt = len(state.get('orders', {}))
    hist = len(state.get('history', []))
    pos = get_balance(ex)
    log(f'状态: {open_cnt}个挂单 | USDT=${pos:.2f} | 历史{hist}笔')
    log('=== 完成 ===')
