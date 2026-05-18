#!/usr/bin/env python3
"""
暗黑星火 · 合约执行器 v1.0
===========================
$50 合约执行器。只执行GPT决策，不自主交易。

流程:
  1. 读shared_config.json → GPT合约指令
  2. 开仓/平仓/持仓管理
  3. 止损检查(每10秒)
  4. 日志 → evolution_engine下一轮读取
"""
import os, json, time, sys, ccxt
from datetime import datetime
from pathlib import Path

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
SECURE_DIR = Path.home() / '.hermes/mempalace/secure'
CONFIG_FILE = LOGS / 'shared_config.json'
STATE_FILE = LOGS / 'contract_executor_state.json'

sys.path.insert(0, str(SECURE_DIR))
from decrypt_and_run import decrypt

MAX_LOSS_PER_DAY = 10.0   # 日亏$10停
MAX_LOSS_PER_TRADE = 3.0  # 单笔亏$3止损

def log(msg):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOGS / 'contract_executor.log', 'a') as f:
        f.write(f'[{t}] {msg}\n')
    print(f'[{t}] {msg}', flush=True)

def get_exchange():
    creds = decrypt()
    ex = ccxt.binance({
        'apiKey': creds.get('BINANCE_API_KEY', ''),
        'secret': creds.get('BINANCE_API_SECRET', ''),
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    ex.load_markets()
    return ex

def get_balance(ex):
    """获取合约USDT余额"""
    try:
        b = ex.fetch_balance()
        return float(b.get('free', {}).get('USDT', 0))
    except:
        return 0

def get_positions(ex):
    """获取所有持仓"""
    try:
        return ex.fetch_positions()
    except:
        return []

def get_position(ex, symbol):
    """获取指定币种持仓"""
    sym = symbol.replace('/', '') + '/USDT:USDT'
    try:
        positions = ex.fetch_positions([sym])
        for p in positions:
            amt = float(p.get('positionAmt', 0) or 0)
            if abs(amt) > 0:
                return p
    except:
        pass
    return None

def set_leverage(ex, symbol, leverage):
    """设置杠杆"""
    try:
        ex.set_leverage(leverage, symbol)
    except:
        pass

def open_position(ex, symbol, side, qty, leverage, order_type='market', price=None):
    """开仓"""
    set_leverage(ex, symbol, leverage)
    sym = symbol.replace('/USDT', '') + '/USDT:USDT'
    
    try:
        if order_type == 'limit' and price:
            order = ex.create_order(sym, 'limit', side, qty, price)
        else:
            order = ex.create_market_order(sym, side, qty)
        
        if order and order.get('id'):
            filled = float(order.get('filled', 0))
            avg = float(order.get('average', 0))
            log(f'[OPEN] {side} {filled} {symbol}@{avg} {leverage}x')
        return order
    except Exception as e:
        log(f'  [ERR] 开仓失败: {e}')
        return None

def close_position(ex, symbol, qty, side):
    """平仓"""
    sym = symbol.replace('/USDT', '') + '/USDT:USDT'
    close_side = 'sell' if side == 'long' else 'buy'
    try:
        order = ex.create_market_order(sym, close_side, abs(qty))
        if order:
            log(f'[CLOSE] {side} {abs(qty)} {symbol}')
        return order
    except Exception as e:
        log(f'  [ERR] 平仓失败: {e}')
        return None

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except:
        return {'daily_pnl': 0.0, 'daily_date': datetime.now().strftime('%Y-%m-%d'), 'trades': 0}

def save_state(state):
    json.dump(state, open(STATE_FILE, 'w'), indent=2)

def read_gpt_contract():
    """读取GPT合约指令"""
    try:
        sc = json.load(open(CONFIG_FILE))
        return sc.get('contract', {})
    except:
        return {}

if __name__ == '__main__':
    log('=== 合约执行器 启动 ===')
    
    ex = get_exchange()
    state = load_state()
    
    # 日亏检查
    today = datetime.now().strftime('%Y-%m-%d')
    if state['daily_date'] != today:
        state['daily_pnl'] = 0.0
        state['daily_date'] = today
    
    balance = get_balance(ex)
    log(f'合约余额: ${balance:.2f} | 今日已亏: ${state[\"daily_pnl\"]:+.2f}')
    
    if state['daily_pnl'] <= -MAX_LOSS_PER_DAY:
        log(f'[HALT] 日亏已达${abs(state[\"daily_pnl\"]):.0f} > $10, 暂停交易')
        sys.exit(0)
    
    # 1. 检查现有持仓
    positions = get_positions(ex)
    active_pos = None
    for p in positions:
        amt = float(p.get('positionAmt', 0) or 0)
        if abs(amt) > 0:
            active_pos = p
            break
    
    # 2. 读GPT指令
    gpt = read_gpt_contract()
    
    if not gpt.get('active', False):
        log(f'[GPT] 无合约指令(active=false)')
        if active_pos:
            sym = active_pos.get('symbol', '')
            side = 'long' if float(active_pos.get('positionAmt', 0)) > 0 else 'short'
            amt = float(active_pos.get('positionAmt', 0))
            log(f'  GPT说关闭,但还有持仓, 平仓')
            close_position(ex, sym, amt, side)
        else:
            log('  空仓等待GPT指令')
        save_state(state)
        sys.exit(0)
    
    symbol = gpt.get('symbol', '')
    direction = gpt.get('direction', 'none')
    leverage = gpt.get('leverage', 5)
    sl_price = gpt.get('stop_loss_price', 0)
    tp_price = gpt.get('take_profit_price', 0)
    margin = gpt.get('margin_usdt', 0)
    reason = gpt.get('reason', '')
    
    if direction == 'none' or not symbol:
        log('[GPT] 无方向指令(none), 保持现状')
        save_state(state)
        sys.exit(0)
    
    log(f'[GPT] {symbol} {direction} {leverage}x | margin=${margin} | SL={sl_price} TP={tp_price}')
    log(f'  理由: {reason}')
    
    # 3. 如果有持仓,检查是否需要操作
    if active_pos:
        pos_sym = active_pos.get('symbol', '').replace('/USDT:USDT', '/USDT')
        pos_side = 'long' if float(active_pos.get('positionAmt', 0)) > 0 else 'short'
        pos_amt = float(active_pos.get('positionAmt', 0))
        entry = float(active_pos.get('entryPrice', 0))
        mark = float(active_pos.get('markPrice', 0))
        
        pnl = float(active_pos.get('unrealizedProfit', 0))
        log(f'  持仓: {pos_side} {abs(pos_amt)} {pos_sym} @{entry} 当前{mark} PnL=${pnl:+.2f}')
        
        # 止损检查
        if sl_price > 0:
            if (direction == 'short' and mark >= sl_price) or \
               (direction == 'long' and mark <= sl_price):
                log(f'  [SL] 止损触发! 当前{mark} 止损{sl_price}')
                close_position(ex, pos_sym, pos_amt, pos_side)
                state['daily_pnl'] += pnl
                state['trades'] += 1
                save_state(state)
                sys.exit(0)
        
        # 止盈检查
        if tp_price > 0:
            if (direction == 'short' and mark <= tp_price) or \
               (direction == 'long' and mark >= tp_price):
                log(f'  [TP] 止盈触发! 当前{mark} 止盈{tp_price}')
                close_position(ex, pos_sym, pos_amt, pos_side)
                state['daily_pnl'] += pnl
                state['trades'] += 1
                save_state(state)
                sys.exit(0)
        
        # 方向不一致 → 平仓
        if (direction == 'long' and pos_side != 'long') or \
           (direction == 'short' and pos_side != 'short'):
            log(f'  GPT方向({direction})≠持仓方向({pos_side}), 平仓重开')
            close_position(ex, pos_sym, pos_amt, pos_side)
            state['daily_pnl'] += min(pnl, 0)  # 只记亏损
            state['trades'] += 1
            save_state(state)
            time.sleep(1)
        else:
            log(f'  方向一致, 继续持有')
            save_state(state)
            sys.exit(0)
    
    # 4. 开新仓
    if margin <= 0 or margin > 50:
        margin = min(balance, 50)
    
    # 计算数量
    sym_usdt = symbol.replace('/USDT', '') + '/USDT:USDT'
    try:
        ticker = ex.fetch_ticker(sym_usdt)
        price = ticker['last']
    except:
        log(f'[ERR] 取价失败 {symbol}')
        sys.exit(1)
    
    qty = round(margin * leverage / price, 4)
    side = 'sell' if direction == 'short' else 'buy'
    
    if qty < 0.001:
        log(f'[ERR] 数量太小: {qty}')
        sys.exit(1)
    
    log(f'  开仓: {direction} {qty} {symbol} @${price:.2f} {leverage}x')
    result = open_position(ex, symbol, side, qty, leverage)
    
    if result:
        state['trades'] += 1
        save_state(state)
    
    save_state(state)
    pos = get_balance(ex)
    log(f'余额: ${pos:.2f} | 今日交易: {state[\"trades\"]}笔')
    log('=== 完成 ===')
