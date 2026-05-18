#!/usr/bin/env python3
"""
暗黑星火 · 虚拟盘引擎 v1
=======================
主权AI全托管核心组件。
真实Binance K线 → 多策略并行模拟交易 → 输出绩效数据供GPT审计+进化

模型分工:
  - 引擎本身: DS-0 Flash (python逻辑)
  - 每日审计: GPT-5.x (1314/aipro)
  - 策略进化: 混合模型

策略池:
  1. 现货网格: 布林带+ATR自适应间距
  2. 趋势跟踪: 双EMA交叉+RSI过滤
  3. 均值回归: RSI超买超卖+Vol过滤
  4. 动量突破: 20日高低点突破+ATR跟踪止损
"""
import sys, os, json, time, hmac, hashlib
import traceback
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt

_CREDS = _decrypt()
API_KEY = _CREDS.get('BINANCE_API_KEY', '')
API_SECRET = _CREDS.get('BINANCE_API_SECRET', '')

BASE = '/home/admin/charon'
STATE = f'{BASE}/virtual_state'
ANALYSIS = f'{BASE}/analysis'
os.makedirs(STATE, exist_ok=True)
os.makedirs(ANALYSIS, exist_ok=True)

# ================================================================
# 配置
# ================================================================
INITIAL_CAPITAL = 1000.0  # 每个虚拟盘$1000起
FEE_MAKER = 0.0004  # 0.04%
FEE_TAKER = 0.0007  # 0.07%
LEVERAGE = 5

STRATEGIES = [
    {'id': 'grid_spot', 'name': '现货网格', 'coins': ['ETH', 'SOL', 'DOGE'], 'freq_hours': 4},
    {'id': 'trend_futures', 'name': '趋势跟踪', 'coins': ['ETH', 'BTC', 'SOL'], 'freq_hours': 1},
    {'id': 'meanrev_futures', 'name': '均值回归', 'coins': ['ETH', 'SOL', 'DOGE'], 'freq_hours': 1},
    {'id': 'momentum_breakout', 'name': '动量突破', 'coins': ['ETH', 'BTC'], 'freq_hours': 4},
]

# ================================================================
# 工具
# ================================================================
def log(msg):
    print(f'[{datetime.now(timezone.utc).strftime("%m-%d %H:%M:%S")}] {msg}')

def sign(params):
    qs = '&'.join([f'{k}={v}' for k,v in sorted(params.items())])
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def api_get(path, params):
    ts = int(time.time()*1000)
    params.update({'timestamp': str(ts), 'recvWindow': '10000'})
    sig = sign(dict(sorted(params.items())))
    url = f'https://api.binance.com{path}?{"&".join([f"{k}={v}" for k,v in sorted(params.items())])}&signature={sig}'
    try:
        import requests
        return requests.get(url, headers={'X-MBX-APIKEY': API_KEY}, timeout=15).json()
    except Exception as e:
        return {'error': str(e)}

def fetch_kline(symbol, interval='1h', limit=200):
    """拉取K线"""
    try:
        import requests
        url = f'https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}'
        r = requests.get(url, timeout=15)
        data = r.json()
        if isinstance(data, list):
            return [{
                'time': int(k[0]),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
            } for k in data]
        return []
    except Exception as e:
        log(f'K线获取失败 {symbol}: {e}')
        return []

def calc_sma(data, period):
    if len(data) < period: return None
    return sum(d['close'] for d in data[-period:]) / period

def calc_rsi(data, period=14):
    if len(data) < period + 1: return 50
    gains, losses = 0, 0
    for i in range(-period, 0):
        diff = data[i]['close'] - data[i-1]['close']
        if diff > 0: gains += diff
        else: losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_atr(data, period=14):
    if len(data) < period + 1: return 0
    trs = []
    for i in range(-period, 0):
        h, l, pc = data[i]['high'], data[i]['low'], data[i-1]['close']
        tr = max(h-l, abs(h-pc), abs(l-pc))
        trs.append(tr)
    return sum(trs) / len(trs)

def calc_ema(data, period):
    if len(data) < period: return None
    multiplier = 2 / (period + 1)
    ema = sum(d['close'] for d in data[:period]) / period
    for d in data[period:]:
        ema = (d['close'] - ema) * multiplier + ema
    return ema

def calc_macd(data):
    if len(data) < 26: return None, None, None
    ema12 = calc_ema(data, 12)
    ema26 = calc_ema(data, 26)
    if ema12 is None or ema26 is None: return None, None, None
    macd = ema12 - ema26
    signal = calc_ema([{'close': macd} for _ in range(9)], 9) if len(data) >= 35 else None
    return macd, signal, macd - (signal or 0)

def calc_bollinger(data, period=20):
    if len(data) < period: return None, None, None
    closes = [d['close'] for d in data[-period:]]
    ma = sum(closes) / period
    sd = (sum((c - ma)**2 for c in closes) / period) ** 0.5
    return ma, ma + 2*sd, ma - 2*sd

# ================================================================
# 策略实现
# ================================================================
class StrategyEngine:
    """策略基类"""
    def __init__(self, config):
        self.id = config['id']
        self.name = config['name']
        self.coins = config['coins']
        self.freq_hours = config.get('freq_hours', 4)
        self.capital = INITIAL_CAPITAL
        self.positions = {}  # coin -> {'side','entry_price','qty','time'}
        self.trades = []
        self.equity_curve = []
        self.last_check = 0
        self.kline_cache = {}  # coin -> klines (每个币自己的K线)
        
    def load_state(self):
        path = f'{STATE}/{self.id}.json'
        if os.path.exists(path):
            try:
                with open(path) as f:
                    s = json.load(f)
                self.capital = s.get('capital', INITIAL_CAPITAL)
                self.positions = s.get('positions', {})
                self.trades = s.get('trades', [])
                self.equity_curve = s.get('equity_curve', [])
                self.last_check = s.get('last_check', 0)
                log(f'  {self.id}: 加载状态, 资金${self.capital:.2f}, {len(self.positions)}仓, {len(self.trades)}笔')
            except: pass
    
    def save_state(self):
        # 原子写
        tmp = f'{STATE}/{self.id}.json.tmp'
        with open(tmp, 'w') as f:
            json.dump({
                'id': self.id,
                'capital': self.capital,
                'positions': self.positions,
                'trades': self.trades,
                'equity_curve': self.equity_curve,
                'last_check': self.last_check,
                'updated': datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
        os.replace(tmp, f'{STATE}/{self.id}.json')
    
    def execute_trade(self, coin, side, price, qty, reason=''):
        """执行一笔模拟交易"""
        cost = price * qty
        fee = cost * FEE_TAKER
        self.trades.append({
            'time': int(time.time()*1000),
            'datetime': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'),
            'coin': coin,
            'side': side,
            'price': round(price, 6),
            'qty': round(qty, 6),
            'cost': round(cost, 2),
            'fee': round(fee, 4),
            'reason': reason,
            'strategy': self.id,
        })
        self.capital -= fee
        return True
    
    def close_position(self, coin, current_price, reason='stop'):
        """平仓"""
        if coin not in self.positions:
            return
        pos = self.positions[coin]
        qty = pos['qty']
        entry = pos['entry_price']
        
        # 计算盈亏
        if pos['side'] == 'long':
            pnl = (current_price - entry) * qty
        else:
            pnl = (entry - current_price) * qty
        
        fee = current_price * qty * FEE_TAKER
        self.capital += pnl - fee
        
        self.trades.append({
            'time': int(time.time()*1000),
            'datetime': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'),
            'coin': coin,
            'side': f'close_{pos["side"]}',
            'price': round(current_price, 6),
            'qty': round(qty, 6),
            'cost': round(pnl, 2),
            'fee': round(fee, 4),
            'pnl': round(pnl, 2),
            'reason': reason,
            'strategy': self.id,
        })
        
        del self.positions[coin]
        log(f'  📉 平仓 {coin} {pos["side"]} @${current_price:.2f} PnL=${pnl:.2f} ({reason})')
        return pnl

class GridSpot(StrategyEngine):
    """现货网格: 布林带上下轨之间网格"""
    def check(self, per_coin_klines):
        """per_coin_klines: {coin: [kline,...]}"""
        signals = []
        for coin in self.coins:
            klines = per_coin_klines.get(coin, [])
            if not klines: continue
            current = klines[-1]['close']
            ma, upper, lower = calc_bollinger(klines)
            if ma is None: continue
            
            pos = self.positions.get(coin, {})
            in_pos = coin in self.positions
            
            if not in_pos and current <= lower * 1.01:
                qty = (self.capital * 0.3) / current
                if qty > 0.01:
                    self.execute_trade(coin, 'buy', current, qty, 'bollinger_lower')
                    self.positions[coin] = {'side':'long','entry_price':current,'qty':qty,'time':time.time()}
                    signals.append(f'{coin} 做多 @${current:.2f}')
            
            elif in_pos and current >= upper * 0.99:
                pnl = self.close_position(coin, current, 'bollinger_upper')
                signals.append(f'{coin} 止盈 @${current:.2f} PnL=${pnl:.2f}')
            
            elif in_pos:
                entry = pos['entry_price']
                if pos['side'] == 'long' and current < entry * 0.95:
                    self.close_position(coin, current, 'stop_loss_5pct')
                    signals.append(f'{coin} 止损 @${current:.2f}')
        
        return signals

class TrendFutures(StrategyEngine):
    """趋势跟踪: EMA12/26交叉+RSI过滤"""
    def check(self, per_coin_klines):
        signals = []
        for coin in self.coins:
            klines = per_coin_klines.get(coin, [])
            if not klines: continue
            
            macd, signal, hist = calc_macd(klines)
            rsi = calc_rsi(klines)
            if macd is None: continue
            
            current = klines[-1]['close']
            pos = self.positions.get(coin, {})
            in_pos = coin in self.positions
            
            # 做多: MACD金叉 + RSI<70 (不是超买)
            if not in_pos and hist > 0 and rsi < 70 and rsi > 30:
                qty = (self.capital * 0.4 * LEVERAGE) / current
                if qty > 0.001:
                    self.execute_trade(coin, 'long', current, qty, f'macd_bullish_rsi_{rsi:.0f}')
                    self.positions[coin] = {'side':'long','entry_price':current,'qty':qty,'time':time.time()}
                    signals.append(f'{coin} 做多 @${current:.2f}')
            
            # 做空: MACD死叉 + RSI>30
            elif not in_pos and hist < 0 and rsi > 30 and rsi < 70:
                qty = (self.capital * 0.4 * LEVERAGE) / current
                if qty > 0.001:
                    self.execute_trade(coin, 'short', current, qty, f'macd_bearish_rsi_{rsi:.0f}')
                    self.positions[coin] = {'side':'short','entry_price':current,'qty':qty,'time':time.time()}
                    signals.append(f'{coin} 做空 @${current:.2f}')
            
            # 平仓: MACD反转
            elif in_pos:
                entry = pos['entry_price']
                if pos['side'] == 'long' and hist < 0:
                    pnl = self.close_position(coin, current, 'macd_bearish')
                    signals.append(f'{coin} 平多 @${current:.2f} PnL=${pnl:.2f}')
                elif pos['side'] == 'short' and hist > 0:
                    pnl = self.close_position(coin, current, 'macd_bullish')
                    signals.append(f'{coin} 平空 @${current:.2f} PnL=${pnl:.2f}')
                # 止损 -8%
                elif abs(current - entry) / entry > 0.08:
                    side_label = 'long' if pos['side'] == 'long' else 'short'
                    pnl = self.close_position(coin, current, f'{side_label}_stop_8pct')
        
        return signals

class MeanRevFutures(StrategyEngine):
    """均值回归: RSI超买超卖"""
    def check(self, per_coin_klines):
        signals = []
        for coin in self.coins:
            klines = per_coin_klines.get(coin, [])
            if not klines: continue
            current = klines[-1]['close']
            rsi = calc_rsi(klines)
            atr = calc_atr(klines)
            if atr == 0: continue
            
            pos = self.positions.get(coin, {})
            in_pos = coin in self.positions
            
            # 超卖做多 (RSI<25 + ATR过滤极端波动)
            if not in_pos and rsi < 25 and atr / current < 0.05:
                qty = (self.capital * 0.3 * LEVERAGE) / current
                if qty > 0.001:
                    self.execute_trade(coin, 'long', current, qty, f'rsi_oversold_{rsi:.0f}')
                    self.positions[coin] = {'side':'long','entry_price':current,'qty':qty,'time':time.time()}
                    signals.append(f'{coin} 超卖做多 RSI={rsi:.0f}')
            
            # 超买做空 (RSI>75 + ATR过滤)
            elif not in_pos and rsi > 75 and atr / current < 0.05:
                qty = (self.capital * 0.3 * LEVERAGE) / current
                if qty > 0.001:
                    self.execute_trade(coin, 'short', current, qty, f'rsi_overbought_{rsi:.0f}')
                    self.positions[coin] = {'side':'short','entry_price':current,'qty':qty,'time':time.time()}
                    signals.append(f'{coin} 超卖做空 RSI={rsi:.0f}')
            
            # 回归平仓: RSI回到50附近 或 止损
            elif in_pos:
                entry = pos['entry_price']
                if pos['side'] == 'long':
                    if rsi >= 50:
                        pnl = self.close_position(coin, current, 'rsi_normalized')
                        signals.append(f'{coin} 均值回归平多 PnL=${pnl:.2f}')
                    elif current < entry * 0.93:
                        pnl = self.close_position(coin, current, 'stop_loss_7pct')
                else:  # short
                    if rsi <= 50:
                        pnl = self.close_position(coin, current, 'rsi_normalized')
                        signals.append(f'{coin} 均值回归平空 PnL=${pnl:.2f}')
                    elif current > entry * 1.07:
                        pnl = self.close_position(coin, current, 'stop_loss_7pct')
        
        return signals

class MomentumBreakout(StrategyEngine):
    """动量突破: 20日高低点突破"""
    def check(self, per_coin_klines):
        signals = []
        for coin in self.coins:
            klines = per_coin_klines.get(coin, [])
            if len(klines) < 21: continue
            
            current = klines[-1]['close']
            high_20 = max(k['high'] for k in klines[-20:])
            low_20 = min(k['low'] for k in klines[-20:])
            atr = calc_atr(klines)
            if atr == 0: continue
            
            pos = self.positions.get(coin, {})
            in_pos = coin in self.positions
            
            # 突破20日高点做多
            if not in_pos and current >= high_20:
                qty = (self.capital * 0.5 * LEVERAGE) / current
                if qty > 0.001:
                    self.execute_trade(coin, 'long', current, qty, 'breakout_20d_high')
                    self.positions[coin] = {'side':'long','entry_price':current,'qty':qty,'time':time.time()}
                    signals.append(f'{coin} 突破做多 @${current:.2f}')
            
            # 跌破20日低点做空
            elif not in_pos and current <= low_20:
                qty = (self.capital * 0.5 * LEVERAGE) / current
                if qty > 0.001:
                    self.execute_trade(coin, 'short', current, qty, 'breakdown_20d_low')
                    self.positions[coin] = {'side':'short','entry_price':current,'qty':qty,'time':time.time()}
                    signals.append(f'{coin} 跌破做空 @${current:.2f}')
            
            # ATR跟踪止损
            elif in_pos:
                entry = pos['entry_price']
                side = pos['side']
                stop_dist = atr * 2
                
                if side == 'long' and current < entry - stop_dist:
                    pnl = self.close_position(coin, current, 'atr_stop')
                    signals.append(f'{coin} ATR止损平多 PnL=${pnl:.2f}')
                elif side == 'short' and current > entry + stop_dist:
                    pnl = self.close_position(coin, current, 'atr_stop')
                    signals.append(f'{coin} ATR止损平空 PnL=${pnl:.2f}')
        
        return signals

# ================================================================
# 主循环
# ================================================================
def main():
    log('🏁 虚拟盘引擎启动')
    log(f'  策略: {", ".join(s["name"] for s in STRATEGIES)}')
    log(f'  初始资金: ${INITIAL_CAPITAL}/策略')
    
    # 初始化策略
    engines = {}
    for cfg in STRATEGIES:
        cls_map = {
            'grid_spot': GridSpot,
            'trend_futures': TrendFutures,
            'meanrev_futures': MeanRevFutures,
            'momentum_breakout': MomentumBreakout,
        }
        eng = cls_map[cfg['id']](cfg)
        eng.load_state()
        engines[cfg['id']] = eng
    
    while True:
        loop_start = time.time()
        try:
            # 1. 先判断哪些策略该检查了
            now = time.time()
            signals_all = []
            
            # 2. 全局拉一次K线 (复用)
            kline_cache = {}
            for eng in engines.values():
                for coin in eng.coins:
                    key = f'{coin}_{eng.freq_hours}h'
                    if key not in kline_cache:
                        kline_cache[key] = fetch_kline(coin, f'{eng.freq_hours}h', 100)
                        time.sleep(0.1)  # 限速
            
            # 3. 逐个策略检查
            for eng in engines.values():
                if now - eng.last_check < eng.freq_hours * 3600:
                    continue  # 还没到检查时间
                
                # 收集该策略每个币自己的K线 → per_coin_klines dict
                per_coin_klines = {}
                for coin in eng.coins:
                    key = f'{coin}_{eng.freq_hours}h'
                    k = kline_cache.get(key, [])
                    if k:
                        per_coin_klines[coin] = k
                
                if not per_coin_klines:
                    continue
                
                try:
                    sigs = eng.check(per_coin_klines)
                    signals_all.extend(sigs)
                except Exception as e:
                    log(f'  ❌ {eng.id} 策略出错: {e}')
                    import traceback; traceback.print_exc()
                
                eng.last_check = now
                
                # 4. 记录净值
                total_equity = eng.capital
                for coin, pos in eng.positions.items():
                    key = f'{coin}_{eng.freq_hours}h'
                    k = kline_cache.get(key, [])
                    if k:
                        cur = k[-1]['close']
                        if pos['side'] == 'long':
                            total_equity += (cur - pos['entry_price']) * pos['qty']
                        else:
                            total_equity += (pos['entry_price'] - cur) * pos['qty']
                    else:
                        total_equity += pos['entry_price'] * pos['qty']
                
                eng.equity_curve.append({
                    'time': time.time(),
                    'datetime': datetime.now(timezone.utc).strftime('%m-%d %H:%M'),
                    'equity': round(total_equity, 2),
                    'capital': round(eng.capital, 2),
                    'positions': len(eng.positions),
                })
                
                eng.save_state()
            
            # 5. 输出整体状态
            summary = []
            for eng in engines.values():
                total_equity = eng.capital
                for pos in eng.positions.values():
                    total_equity += pos['entry_price'] * pos['qty']
                win_trades = [t for t in eng.trades if t.get('pnl',0) > 0]
                total_pnl = sum(t.get('pnl',0) for t in eng.trades if 'pnl' in t)
                summary.append({
                    'id': eng.id,
                    'name': eng.name,
                    'equity': round(total_equity, 2),
                    'capital': round(eng.capital, 2),
                    'positions': len(eng.positions),
                    'trades': len(eng.trades),
                    'wins': len(win_trades),
                    'total_pnl': round(total_pnl, 2),
                })
            
            # 每4小时输出一次状态
            if int(now) % (4*3600) < 60:
                log(f'\n{"="*50}')
                log(f'📊 虚拟盘状态')
                log(f'{"="*50}')
                for s in summary:
                    wr = f'{s["wins"]/max(s["trades"],1)*100:.0f}%' if s['trades'] > 0 else '-'
                    log(f'  {s["name"]:12s} ${s["equity"]:<7.2f}  {s["trades"]}笔 胜率{wr}  PnL=${s["total_pnl"]:<+7.2f}')
            
            # 每4h保存汇总报告
            if int(now) % (4*3600) < 60:
                report_path = f'{ANALYSIS}/virtual_report.json'
                report = {
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'strategies': summary,
                    'total_equity': sum(s['equity'] for s in summary),
                    'total_trades': sum(s['trades'] for s in summary),
                }
                with open(report_path, 'w') as f:
                    json.dump(report, f, indent=2)
            
            # 如果有信号输出
            if signals_all:
                log(f'  📡 信号: {", ".join(signals_all[:5])}')
        
        except Exception as e:
            log(f'❌ 主循环错误: {e}')
            traceback.print_exc()
        
        # 60秒循环
        elapsed = time.time() - loop_start
        time.sleep(max(1, 60 - elapsed))

if __name__ == '__main__':
    main()
