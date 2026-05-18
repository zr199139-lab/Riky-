#!/usr/bin/env python3
"""
暗黑星火 · 合约执行器 v2.0
===========================
$5/笔 低风险模式 · 一次一单 · daemon持续运行

规则:
  - 每笔$5保证金, 一次只开一单
  - 最多10次交易 (累计)
  - 日亏上限$10
  - 每10秒检查止损止盈
  - 平仓后暂停30分钟再开新单 (给市场时间)
"""
import os, json, time, sys, ccxt, requests, hashlib, hmac
from datetime import datetime
from pathlib import Path

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
SECURE_DIR = Path.home() / '.hermes/mempalace/secure'
CONFIG_FILE = LOGS / 'shared_config.json'
STATE_FILE = LOGS / 'contract_executor_state.json'

sys.path.insert(0, str(SECURE_DIR))
from decrypt_and_run import decrypt

# ===== 硬编码风控 (用户铁律) =====
MAX_MARGIN_PER_TRADE = 5.0     # $5/笔
MAX_TRADES_TOTAL = 10          # 最多10次
MAX_LOSS_PER_DAY = 10.0        # 日亏$10停
MAX_LOSS_PER_TRADE = 2.0       # 单笔亏$2强平
MIN_IDLE_BETWEEN_TRADES = 1800 # 平仓后等30min再开(秒)
CHECK_INTERVAL = 10            # 每10秒检查

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
    try:
        b = ex.fetch_balance()
        info = b.get('info', {})
        return float(info.get('availableBalance', 0))
    except:
        return 0

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except:
        return {
            'daily_pnl': 0.0,
            'daily_date': datetime.now().strftime('%Y-%m-%d'),
            'trades': 0,
            'last_close_time': 0,
            'current_symbol': '',
            'current_direction': '',
            'current_entry': 0,
            'active_trade_id': 0
        }

def save_state(state):
    json.dump(state, open(STATE_FILE, 'w'), indent=2)

def get_mark_price(ex, symbol):
    """通过REST API获取标记价格"""
    sym = symbol.replace('/USDT', '')
    try:
        r = requests.get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}USDT')
        return float(r.json().get('markPrice', 0))
    except:
        return 0


def get_credentials():
    """从解密模块获取API凭据"""
    creds = decrypt()
    return creds.get('BINANCE_API_KEY', ''), creds.get('BINANCE_API_SECRET', '')

def sign_request(secret, query):
    """HMAC SHA256签名"""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

def has_open_position(ex):
    """通过REST API检查是否有活跃持仓 (修复hedge mode下ccxt返回positionAmt=0的bug)"""
    try:
        api_key, api_secret = get_credentials()
        ts = str(int(time.time() * 1000))
        query = 'timestamp=' + ts + '&recvWindow=5000'
        sig = sign_request(api_secret, query)
        
        r = requests.get('https://fapi.binance.com/fapi/v2/positionRisk',
            params={'timestamp': ts, 'recvWindow': '5000', 'signature': sig},
            headers={'X-MBX-APIKEY': api_key})
        positions = r.json() if isinstance(r.json(), list) else []
        
        for p in positions:
            amt = float(p.get('positionAmt', 0))
            if abs(amt) > 0.0001:
                # 构造一个类position dict
                return {
                    'symbol': p['symbol'],
                    'positionAmt': p['positionAmt'],
                    'entryPrice': p.get('entryPrice', '0'),
                    'markPrice': p.get('markPrice', '0'),
                    'unrealizedProfit': p.get('unRealizedProfit', '0'),
                    'positionSide': p.get('positionSide', 'SHORT'),
                    'side': 'short' if float(amt) < 0 else 'long'
                }
    except Exception as e:
        log(f'[ERR] has_open_position: {e}')
        # fallback to ccxt
        try:
            positions = ex.fetch_positions()
            for p in positions:
                amt = float(p.get('positionAmt', 0) or 0)
                if abs(amt) > 0:
                    p['side'] = 'short' if amt < 0 else 'long'
                    return p
        except:
            pass
    return None

def open_short(ex, symbol, margin, leverage):
    """做空开仓"""
    sym = symbol.replace('/USDT', '') + '/USDT:USDT'
    try:
        # 设置杠杆
        ex.set_leverage(leverage, sym)
    except:
        pass
    
    try:
        # 强制逐仓模式
        ex.set_margin_mode('isolated', sym)
    except:
        pass
    
    try:
        ticker = ex.fetch_ticker(sym)
        price = ticker['last']
    except:
        log(f'[ERR] 取价失败 {symbol}')
        return None
    
    qty = round(margin * leverage / price, 4)
    if qty < 0.001:
        log(f'[ERR] 数量太小: {qty} (margin={margin} lev={leverage} price={price})')
        return None
    
    log(f'[OPEN] SHORT {qty} {symbol} @${price:.2f} {leverage}x | margin=${margin}')
    try:
        order = ex.create_market_order(sym, 'sell', qty, None, {'positionSide': 'SHORT'})
        if order and order.get('id'):
            filled = float(order.get('filled', 0))
            avg = float(order.get('average', 0))
            log(f'  [OK] 成交: {filled} @${avg:.2f}')
            return {'qty': filled, 'price': avg, 'order': order, 'margin': margin}
    except Exception as e:
        log(f'  [ERR] 开仓失败: {e}')
    return None

def close_position(ex, pos):
    """平仓"""
    symbol = pos.get('symbol', '')
    amt = float(pos.get('positionAmt', 0) or 0)
    pos_side = pos.get('positionSide', 'SHORT')
    side = 'buy' if amt < 0 else 'sell'  # 做空→买入平仓
    qty = abs(amt)
    
    log(f'[CLOSE] 平仓 {qty} {symbol}')
    try:
        order = ex.create_market_order(symbol, side, qty, None, {'positionSide': pos_side})
        if order:
            log(f'  [OK] 平仓完成')
        return order
    except Exception as e:
        log(f'  [ERR] 平仓失败: {e}')
    return None

def read_gpt_contract():
    """读取GPT合约指令"""
    try:
        sc = json.load(open(CONFIG_FILE))
        return sc.get('contract', {})
    except:
        return {}

def update_config_for_next_cycle():
    """更新配置文件让GPT下一轮知道用了$5模式"""
    try:
        cfg = json.load(open(CONFIG_FILE))
        if 'contract' in cfg:
            cfg['contract']['margin_usdt'] = MAX_MARGIN_PER_TRADE
            cfg['contract']['entry_type'] = 'market'
            cfg['contract']['entry_price'] = 0  # will be updated after fill
        json.dump(cfg, open(CONFIG_FILE, 'w'), indent=2)
    except:
        pass

def run():
    log('=' * 50)
    log('合约执行器 v2.0 启动 [5U/笔模式]')
    log('=' * 50)
    
    ex = get_exchange()
    state = load_state()
    balance = get_balance(ex)
    
    log(f'合约余额: ${balance:.2f} | 累计交易: {state["trades"]}/{MAX_TRADES_TOTAL}')
    
    # 检查交易数量限制
    if state['trades'] >= MAX_TRADES_TOTAL:
        log(f'[HALT] 已达到最大交易次数 {MAX_TRADES_TOTAL}, 停止')
        return
    
    # 日亏检查
    today = datetime.now().strftime('%Y-%m-%d')
    if state['daily_date'] != today:
        log(f'新的一天: {today}, 重置日亏')
        state['daily_pnl'] = 0.0
        state['daily_date'] = today
        save_state(state)
    
    if state['daily_pnl'] <= -MAX_LOSS_PER_DAY:
        dp = abs(state['daily_pnl'])
        log(f'[HALT] 日亏已达${dp:.0f} > ${MAX_LOSS_PER_DAY:.0f}, 暂停交易')
        return
    
    # 检查余额
    if balance < 5:
        log(f'[HALT] 余额${balance:.2f}不足$5, 无法交易')
        return
    
    # 检查冷却期
    now = time.time()
    idle = now - state['last_close_time']
    if state['last_close_time'] > 0 and idle < MIN_IDLE_BETWEEN_TRADES:
        remain = int(MIN_IDLE_BETWEEN_TRADES - idle)
        log(f'[WAIT] 冷却期还剩{remain}s, 等待中...')
        return  # 下次检查再说
    
    # === 主循环 (daemon) ===
    while True:
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            if state['daily_date'] != today:
                state['daily_pnl'] = 0.0
                state['daily_date'] = today
                save_state(state)
            
            # 日亏检查
            if state['daily_pnl'] <= -MAX_LOSS_PER_DAY:
                dp2 = abs(state['daily_pnl'])
                log(f'[HALT] 日亏已达${dp2:.0f}')
                time.sleep(60)
                continue
            
            if state['trades'] >= MAX_TRADES_TOTAL:
                log(f'[DONE] 已完成{MAX_TRADES_TOTAL}笔交易, 停止运行')
                return
            
            balance = get_balance(ex)
            if balance < 5:
                log(f'[WAIT] 余额不足: ${balance:.2f}, 10秒后重试')
                time.sleep(CHECK_INTERVAL)
                continue
            
            # 1. 检查现有持仓
            pos = has_open_position(ex)
            
            if pos:
                # 有持仓 → 监控止损止盈
                amt = float(pos.get('positionAmt', 0) or 0)
                entry = float(pos.get('entryPrice', 0))
                mark = float(pos.get('markPrice', 0))
                upnl = float(pos.get('unrealizedProfit', 0))
                side = 'short' if amt < 0 else 'long'
                symbol = pos.get('symbol', '')
                
                # 读取GPT止损止盈设置
                gpt = read_gpt_contract()
                sl = gpt.get('stop_loss_price', 0)
                tp = gpt.get('take_profit_price', 0)
                
                log(f'[HOLD] {side} {abs(amt):.4f} {symbol}@{entry:.4f} mark={mark:.4f} PnL=${upnl:+.2f} SL={sl} TP={tp}')
                
                # 手工止损检查 (硬止盈止损)
                if sl > 0:
                    if (side == 'short' and mark >= sl) or (side == 'long' and mark <= sl):
                        log(f'  [SL] 止损! mark={mark:.2f} sl={sl}')
                        close_position(ex, pos)
                        state['daily_pnl'] += upnl
                        state['trades'] += 1
                        state['last_close_time'] = time.time()
                        state['current_symbol'] = ''
                        state['current_direction'] = ''
                        save_state(state)
                        time.sleep(60)  # 止损后休息1分钟
                        continue
                
                if tp > 0:
                    if (side == 'short' and mark <= tp) or (side == 'long' and mark >= tp):
                        log(f'  [TP] 止盈! mark={mark:.2f} tp={tp} PnL=${upnl:+.2f}')
                        close_position(ex, pos)
                        state['daily_pnl'] += upnl
                        state['trades'] += 1
                        state['last_close_time'] = time.time()
                        state['current_symbol'] = ''
                        state['current_direction'] = ''
                        save_state(state)
                        time.sleep(60)
                        continue
                
                # 单笔亏损硬止损
                if upnl <= -MAX_LOSS_PER_TRADE:
                    log(f'  [SL-HARD] 单笔亏损${abs(upnl):.2f} > ${MAX_LOSS_PER_TRADE:.0f}, 强制平仓')
                    close_position(ex, pos)
                    state['daily_pnl'] += upnl
                    state['trades'] += 1
                    state['last_close_time'] = time.time()
                    state['current_symbol'] = ''
                    state['current_direction'] = ''
                    save_state(state)
                    time.sleep(60)
                    continue
                
                time.sleep(CHECK_INTERVAL)
                continue
            
            # 2. 无持仓 → 读GPT决策开仓
            gpt = read_gpt_contract()
            if not gpt.get('active', False):
                log('[IDLE] GPT无合约指令, 等待中...')
                time.sleep(60)
                continue
            
            symbol = gpt.get('symbol', '')
            direction = gpt.get('direction', 'none')
            leverage = min(int(gpt.get('leverage', 5)), 10)  # 上限10x
            
            if direction != 'short' or not symbol:
                log(f'[IDLE] GPT方向={direction} symbol={symbol}, 不是做空指令, 等待')
                time.sleep(60)
                continue
            
            # 检查冷却期
            now = time.time()
            idle = now - state['last_close_time']
            if state['last_close_time'] > 0 and idle < MIN_IDLE_BETWEEN_TRADES:
                remain = int(MIN_IDLE_BETWEEN_TRADES - idle)
                log(f'[WAIT] 冷却期还剩{remain}s...')
                time.sleep(CHECK_INTERVAL)
                continue
            
            # 开仓
            sl_val = gpt.get('stop_loss_price', 0)
            tp_val = gpt.get('take_profit_price', 0)
            log(f'[GPT] {symbol} {direction} {leverage}x | margin=${MAX_MARGIN_PER_TRADE} | SL={sl_val} TP={tp_val}')
            
            result = open_short(ex, symbol, MAX_MARGIN_PER_TRADE, leverage)
            if result:
                state['current_symbol'] = symbol
                state['current_direction'] = direction
                state['current_entry'] = result['price']
                state['active_trade_id'] += 1
                save_state(state)
                
                # 更新config记录实际入场价
                try:
                    cfg = json.load(open(CONFIG_FILE))
                    cfg['contract']['entry_price'] = result['price']
                    cfg['contract']['margin_usdt'] = MAX_MARGIN_PER_TRADE
                    cfg['contract']['last_trade_id'] = state['active_trade_id']
                    json.dump(cfg, open(CONFIG_FILE, 'w'), indent=2)
                except:
                    pass
                
                log(f'  [OK] 持仓#{state["active_trade_id"]} 等待止盈止损...')
            else:
                log(f'  [ERR] 开仓失败, 10秒后重试')
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            log('[STOP] 用户中断')
            break
        except Exception as e:
            log(f'[ERR] 异常: {e}')
            time.sleep(30)
            continue

if __name__ == '__main__':
    run()
