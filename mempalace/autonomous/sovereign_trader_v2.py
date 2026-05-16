#!/usr/bin/env python3
"""
暗黑星火 V2 · 海龟趋势跟踪系统
================================
架构: 纯机械规则, 零AI交易决策, 专业风控
入场: 突破20日最高/最低点
仓位: 海龟Unit公式 (账户1% / ATR)
止损: 2×ATR动态
出场: 反向10日极端
风控: 日亏损$5停机, 单笔≤2%本金, 有效杠杆≤5x

设计哲学: 执行40年验证的策略, 不要用AI重新发明轮子
"""

import os, sys, json, time, math, hashlib, hmac, traceback, threading
from datetime import datetime
from collections import defaultdict, deque
import requests
import numpy as np

# ── 路径 ──
BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "..", "bot_logs")
os.makedirs(LOG_DIR, exist_ok=True)

sys.path.insert(0, os.path.expanduser("~/.hermes/mempalace/secure"))
from decrypt_and_run import decrypt

# ═══════════════════════════════════════════
# 海龟系统参数
# ═══════════════════════════════════════════

SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT']  # 只做蓝筹
MAX_LEVERAGE = 5
HARD_MAX_POSITIONS = 1

# 海龟参数
TURTLE_ENTRY = 15      # 突破周期
TURTLE_EXIT = 10       # 反向出场周期
TURTLE_ATR = 14        # ATR周期
TURTLE_ATR_STOP = 2.0  # 止损倍数
TURTLE_RISK = 0.02     # 单笔风险1%

# 风控
DAILY_LOSS_LIMIT = 3.0       # 日亏$5停机
HARD_STOP_LOSS_PCT = 0.06    # 硬止损6%
MIN_HOLD_SECONDS = 900       # 最低持仓15分钟
SCAN_INTERVAL = 60           # 主循环间隔

# 状态文件
STATE_FILE = os.path.join(LOG_DIR, "v2_state.json")
TRADE_MEM_FILE = os.path.join(LOG_DIR, "v2_trade_memory.json")
PID_FILE = os.path.join(LOG_DIR, "v2_pid.txt")

# ── 日志 ──
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] 🐢 {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, "v2_sovereign.log"), "a") as f:
        f.write(line + "\n")

# ═══════════════════════════════════════════
# 交易所API
# ═══════════════════════════════════════════

def get_exchange():
    creds = decrypt()
    api_key = creds.get("BINANCE_API_KEY", "")
    api_secret = creds.get("BINANCE_API_SECRET", "")
    if not api_key:
        log("🔴 无API Key，退出")
        sys.exit(1)
    return api_key, api_secret

API_KEY, API_SECRET = get_exchange()

def binance_request(method, path, params=None, sign=False):
    """REST API直连（跳过ccxt）"""
    base = "https://fapi.binance.com"
    headers = {"X-MBX-APIKEY": API_KEY}
    if params is None:
        params = {}
    if sign:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        full_url = base + path + "?" + query + "&signature=" + sig
    else:
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            full_url = base + path + "?" + query
        else:
            full_url = base + path
    try:
        if method == "GET":
            r = requests.get(full_url, headers=headers, timeout=10)
        else:
            r = requests.post(full_url, headers=headers, timeout=10)
        if r.status_code in (200, 201):
            return r.json()
        log(f"🔴 API错误 {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        log(f"🔴 API异常: {e}")
        return None

def get_positions():
    """REST API获取所有合约持仓"""
    data = binance_request("GET", "/fapi/v2/positionRisk", {"timestamp": int(time.time()*1000)}, sign=True)
    if not data:
        return []
    positions = []
    for p in data:
        amt = float(p.get("positionAmt", 0))
        if amt != 0:
            positions.append({
                "symbol": p["symbol"],
                "side": "long" if amt > 0 else "short",
                "amt": abs(amt),
                "entry": float(p.get("entryPrice", 0)),
                "mark": float(p.get("markPrice", 0)),
                "pnl": float(p.get("unRealizedProfit", 0)),
                "leverage": int(float(p.get("leverage", 5))),
            })
    return positions

def get_balance():
    """合约钱包余额"""
    data = binance_request("GET", "/fapi/v2/account", {"timestamp": int(time.time()*1000)}, sign=True)
    if not data:
        return 0
    return float(data.get("totalWalletBalance", 0))

def get_ohlcv(symbol, interval="1h", limit=100):
    """获取K线数据"""
    sym_ccxt = symbol.replace("/USDT:USDT", "USDT")
    params = {"symbol": sym_ccxt, "interval": interval, "limit": limit,
              "timestamp": int(time.time()*1000)}
    data = binance_request("GET", "/fapi/v1/klines", params)
    if not data:
        return []
    return [{
        "time": int(k[0]),
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
        "volume": float(k[5]),
    } for k in data]

def place_order(symbol, side, qty, order_type="MARKET", reduce=False, price=None):
    """下单"""
    sym = symbol.replace("/USDT:USDT", "USDT")
    params = {
        "symbol": sym,
        "side": "BUY" if side == "buy" else "SELL",
        "type": order_type,
        "quantity": qty,
        "timestamp": int(time.time()*1000),
    }
    if reduce:
        params["reduceOnly"] = "true"
    if price and order_type == "LIMIT":
        params["price"] = price
        params["timeInForce"] = "GTC"
    return binance_request("POST", "/fapi/v1/order", params, sign=True)

def set_leverage(symbol, leverage):
    """设置杠杆"""
    sym = symbol.replace("/USDT:USDT", "USDT")
    params = {"symbol": sym, "leverage": min(leverage, MAX_LEVERAGE),
              "timestamp": int(time.time()*1000)}
    log(f"  🔧 杠杆={leverage}x | {sym}")
    return binance_request("POST", "/fapi/v1/leverage", params, sign=True)

# ═══════════════════════════════════════════
# 指标计算
# ═══════════════════════════════════════════

def calc_atr(ohlcv, period=TURTLE_ATR):
    """ATR计算"""
    if len(ohlcv) < period + 1:
        return 0
    trs = []
    for i in range(1, len(ohlcv)):
        high = ohlcv[i]["high"]
        low = ohlcv[i]["low"]
        prev_close = ohlcv[i-1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0
    return sum(trs[-period:]) / period

def calc_sma(ohlcv, period):
    """简单移动平均"""
    if len(ohlcv) < period:
        return None
    closes = [c["close"] for c in ohlcv[-period:]]
    return sum(closes) / period

def calc_highest(ohlcv, period):
    """最高价"""
    if len(ohlcv) < period:
        return None
    return max(c["high"] for c in ohlcv[-period:])

def calc_lowest(ohlcv, period):
    """最低价"""
    if len(ohlcv) < period:
        return None
    return min(c["low"] for c in ohlcv[-period:])

# ═══════════════════════════════════════════
# 交易记忆
# ═══════════════════════════════════════════

class TradeMemory:
    def __init__(self):
        self.records = deque(maxlen=100)
        self.total = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.daily_pnl = 0.0
        self._load()

    def add(self, symbol, side, entry, exit_px, margin, pnl, reason):
        self.records.append({
            "symbol": symbol, "side": side,
            "entry": entry, "exit": exit_px,
            "margin": margin, "pnl": pnl,
            "entry_time": 0, "exit_time": time.time(),
            "reason": reason,
        })
        self.total += 1
        self.total_pnl += pnl
        self.daily_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self._save()

    def summary(self):
        if self.total == 0:
            return "暂无交易记录"
        wr = self.wins / self.total * 100
        return (f"{self.total}笔 | 胜率{wr:.0f}% | "
                f"总PnL${self.total_pnl:+.2f} | "
                f"日PnL${self.daily_pnl:+.2f}")

    def _save(self):
        try:
            with open(TRADE_MEM_FILE, "w") as f:
                json.dump({
                    "total": self.total, "wins": self.wins,
                    "losses": self.losses, "total_pnl": self.total_pnl,
                    "daily_pnl": self.daily_pnl,
                    "records": list(self.records),
                }, f, indent=2)
        except:
            pass

    def _load(self):
        if not os.path.exists(TRADE_MEM_FILE):
            return
        try:
            with open(TRADE_MEM_FILE) as f:
                data = json.load(f)
            self.total = data.get("total", 0)
            self.wins = data.get("wins", 0)
            self.losses = data.get("losses", 0)
            self.total_pnl = data.get("total_pnl", 0.0)
            self.daily_pnl = data.get("daily_pnl", 0.0)
            for r in data.get("records", []):
                self.records.append(r)
        except:
            pass

# ═══════════════════════════════════════════
# 持仓管理
# ═══════════════════════════════════════════

class PositionManager:
    def __init__(self):
        self.position = None  # {symbol, side, qty, entry, atr, entry_time}
        self.trade_mem = TradeMemory()
        self.daily_loss_triggered = False
        self.last_daily_check = time.time()

    def has_position(self):
        return self.position is not None

    def open_position(self, symbol, side, qty, entry_price, atr):
        self.position = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry": entry_price,
            "atr": atr,
            "entry_time": time.time(),
        }
        log(f"📈 开仓 {symbol} {side} x{qty:.4f} @${entry_price:.2f} "
            f"ATR=${atr:.4f} 止损=${self.get_stop_price():.2f}")

    def close_position(self, exit_price, reason):
        if not self.position:
            return 0
        p = self.position
        side = p["side"]
        entry = p["entry"]
        qty = p["qty"]
        direction = 1 if side == "long" else -1
        pnl = direction * (exit_price - entry) * qty
        margin = entry * qty / 5  # 5x杠杆
        log(f"📉 平仓 {p['symbol']} {side} @${exit_price:.2f} "
            f"PnL=${pnl:.2f} ({pnl/margin*100:.1f}%) | {reason}")
        self.trade_mem.add(p["symbol"], side, entry, exit_price, margin, pnl, reason)
        self.position = None
        return pnl

    def get_stop_price(self):
        if not self.position:
            return 0
        p = self.position
        stop_distance = p["atr"] * TURTLE_ATR_STOP
        if p["side"] == "long":
            return p["entry"] - stop_distance
        else:
            return p["entry"] + stop_distance

    def check_daily_loss(self):
        """日亏损检查"""
        now = time.time()
        # 每天重置一次
        if now - self.last_daily_check > 86400:
            self.trade_mem.daily_pnl = 0
            self.daily_loss_triggered = False
            self.last_daily_check = now
        if self.trade_mem.daily_pnl <= -DAILY_LOSS_LIMIT:
            self.daily_loss_triggered = True
            return True
        return False

    def save(self):
        state = {
            "position": self.position,
            "daily_loss_triggered": self.daily_loss_triggered,
            "last_daily_check": self.last_daily_check,
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except:
            pass

    def load(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            self.position = state.get("position")
            self.daily_loss_triggered = state.get("daily_loss_triggered", False)
            self.last_daily_check = state.get("last_daily_check", time.time())
        except:
            pass

# ═══════════════════════════════════════════
# 风控监控 (独立线程)
# ═══════════════════════════════════════════

class RiskMonitor:
    def __init__(self, pos_mgr):
        self.pos_mgr = pos_mgr
        self.running = True

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        while self.running:
            try:
                # 5秒检查一次硬止损
                if self.pos_mgr.has_position():
                    positions = get_positions()
                    for p in positions:
                        sym = p["symbol"] + "/USDT:USDT"
                        if sym == self.pos_mgr.position["symbol"]:
                            pnl_pct = (p["mark"] - self.pos_mgr.position["entry"]) / self.pos_mgr.position["entry"]
                            if self.pos_mgr.position["side"] == "short":
                                pnl_pct = -pnl_pct
                            if pnl_pct <= -HARD_STOP_LOSS_PCT:
                                log(f"🚨 硬止损触发! {sym} {pnl_pct*100:.1f}%亏损")
                                self._emergency_close(sym)
                time.sleep(5)
            except:
                pass

    def _emergency_close(self, symbol):
        """紧急平仓"""
        sym = symbol.replace("/USDT:USDT", "USDT")
        positions = get_positions()
        for p in positions:
            if p["symbol"] == sym:
                side = "SELL" if p["side"] == "long" else "BUY"
                params = {
                    "symbol": sym,
                    "side": side,
                    "type": "MARKET",
                    "quantity": p["amt"],
                    "reduceOnly": "true",
                    "timestamp": int(time.time()*1000),
                }
                binance_request("POST", "/fapi/v1/order", params, sign=True)
                mark = p.get("mark", 0)
                self.pos_mgr.close_position(mark or 0, "hard_stop")

# ═══════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════

def main():
    log("🐢 暗黑星火 V2 · 海龟趋势跟踪 启动")
    log(f"  杠杆={MAX_LEVERAGE}x | 币种={SYMBOLS} | "
        f"周期={TURTLE_ENTRY}/{TURTLE_EXIT} | "
        f"ATR止损={TURTLE_ATR_STOP}x | 日亏上限=${DAILY_LOSS_LIMIT}")

    # 写PID
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # 加载状态
    mgr = PositionManager()
    mgr.load()
    log(f"  📂 交易记忆: {mgr.trade_mem.summary()}")

    # 风控监控
    RiskMonitor(mgr).start()

    # 设置杠杆
    for sym in SYMBOLS:
        set_leverage(sym, MAX_LEVERAGE)

    # 检查现有持仓
    log("  📡 获取现有持仓...")
    try:
        positions = get_positions()
        log(f"  📡 持仓数: {len(positions)}")
    except Exception as e:
        log(f"  ⚠️ 获取持仓失败: {e}")
        positions = []
    for p in positions:
        sym = p["symbol"] + "/USDT:USDT"
        if sym in SYMBOLS:
            log(f"  🔄 同步现有持仓: {p['symbol']} {p['side']} "
                f"x{p['amt']:.4f} @${p['entry']:.2f}")
            # 获取ATR
            try:
                ohlcv = get_ohlcv(sym, "1h", TURTLE_ATR + 10)
                atr = calc_atr(ohlcv)
                mgr.open_position(sym, p["side"], p["amt"], p["entry"], atr)
            except Exception as e:
                log(f"  ⚠️ ATR计算失败: {e}")

    cycles = 0
    while True:
        try:
            cycle_start = time.time()
            cycles += 1

            # ── 1. 日亏损检查 ──
            if mgr.check_daily_loss():
                log(f"⚠️ 日亏损已达${mgr.trade_mem.daily_pnl:.2f}，停机")
                if mgr.has_position():
                    # 有持仓也平掉
                    positions = get_positions()
                    for p in positions:
                        sym = p["symbol"] + "/USDT:USDT"
                        if sym in SYMBOLS:
                            params = {
                                "symbol": p["symbol"],
                                "side": "SELL" if p["side"] == "long" else "BUY",
                                "type": "MARKET",
                                "quantity": p["amt"],
                                "reduceOnly": "true",
                                "timestamp": int(time.time()*1000),
                            }
                            binance_request("POST", "/fapi/v1/order", params, sign=True)
                            mgr.close_position(p.get("markPrice", 0), "daily_loss_stop")
                mgr.save()
                time.sleep(SCAN_INTERVAL)
                continue

            # ── 2. 同步API持仓 ──
            api_positions = get_positions()
            has_api_pos = len(api_positions) > 0
            mgr_pos = mgr.has_position()

            # 检测幽灵持仓（API有但管理器中无）
            if has_api_pos and not mgr_pos:
                for p in api_positions:
                    sym = p["symbol"] + "/USDT:USDT"
                    if sym in SYMBOLS:
                        log(f"  🔄 发现外部持仓 {p['symbol']}，同步管理")
                        ohlcv = get_ohlcv(sym, "1h", TURTLE_ATR + 10)
                        atr = calc_atr(ohlcv)
                        mgr.open_position(sym, p["side"], p["amt"], p["entry"], atr)

            # 管理器有但API无 → 清理
            if mgr_pos and not has_api_pos:
                log(f"  ⚠️ 管理器持仓但API无，清理")
                mgr.position = None

            # ── 3. 获取市场数据 ──
            market_data = {}
            for sym in SYMBOLS:
                ohlcv = get_ohlcv(sym, "1h", max(TURTLE_ENTRY, TURTLE_ATR) + 10)
                if len(ohlcv) < max(TURTLE_ENTRY, TURTLE_ATR):
                    continue
                atr = calc_atr(ohlcv)
                sma = calc_sma(ohlcv, TURTLE_EXIT)
                highest = calc_highest(ohlcv, TURTLE_ENTRY)
                lowest = calc_lowest(ohlcv, TURTLE_ENTRY)
                exit_low = calc_lowest(ohlcv, TURTLE_EXIT)
                exit_high = calc_highest(ohlcv, TURTLE_EXIT)
                current = ohlcv[-1]["close"]
                market_data[sym] = {
                    "atr": atr, "sma": sma,
                    "highest": highest, "lowest": lowest,
                    "exit_low": exit_low, "exit_high": exit_high,
                    "current": current, "ohlcv": ohlcv,
                }

            # ── 4. 无持仓 → 找入场机会 ──
            if not mgr.has_position():
                for sym in SYMBOLS:
                    md = market_data.get(sym)
                    if not md or not md["atr"] or md["atr"] <= 0:
                        continue
                    current = md["current"]
                    highest = md["highest"]
                    lowest = md["lowest"]

                    # 突破入场
                    if current >= highest:
                        # 做多
                        balance = get_balance()
                        unit = max(1, (balance * TURTLE_RISK) / (md["atr"] * 2))
                        # 最小合约规模检查
                        qty = unit / current
                        if sym == "BTC/USDT:USDT":
                            qty = math.floor(qty * 1000) / 1000
                        else:
                            qty = math.floor(qty * 100) / 100
                        if qty <= 0:
                            continue
                        log(f"🔵 20日突破! {sym} ${current:.2f} > 高点${highest:.2f}")
                        result = place_order(sym, "buy", qty)
                        if result and result.get("orderId"):
                            mgr.open_position(sym, "long", qty, current, md["atr"])
                            log(f"  ✅ 开多成功: {sym} x{qty:.4f} @${current:.2f}")
                        break

                    elif current <= lowest:
                        # 做空
                        balance = get_balance()
                        unit = max(1, (balance * TURTLE_RISK) / (md["atr"] * 2))
                        qty = unit / current
                        if sym == "BTC/USDT:USDT":
                            qty = math.floor(qty * 1000) / 1000
                        else:
                            qty = math.floor(qty * 100) / 100
                        if qty <= 0:
                            continue
                        log(f"🔴 20日跌破! {sym} ${current:.2f} < 低点${lowest:.2f}")
                        result = place_order(sym, "sell", qty)
                        if result and result.get("orderId"):
                            mgr.open_position(sym, "short", qty, current, md["atr"])
                            log(f"  ✅ 开空成功: {sym} x{qty:.4f} @${current:.2f}")
                        break

            # ── 5. 有持仓 → 检查出场 ──
            if mgr.has_position():
                pos = mgr.position
                md = market_data.get(pos["symbol"])
                if md:
                    current = md["current"]
                    stop_price = mgr.get_stop_price()
                    entry = pos["entry"]
                    side = pos["side"]
                    held = time.time() - pos["entry_time"]

                    # ATR止损检查
                    if side == "long" and current <= stop_price:
                        log(f"🛑 ATR止损! {pos['symbol']} ${current:.2f} ≤ ${stop_price:.2f}")
                        result = place_order(pos["symbol"], "sell", pos["qty"], reduce=True)
                        if result and result.get("orderId"):
                            mgr.close_position(current, "stop_loss")
                        elif result is None:
                            # 重试
                            result2 = place_order(pos["symbol"], "sell", pos["qty"], reduce=True)
                            if result2 and result2.get("orderId"):
                                mgr.close_position(current, "stop_loss")

                    elif side == "short" and current >= stop_price:
                        log(f"🛑 ATR止损! {pos['symbol']} ${current:.2f} ≥ ${stop_price:.2f}")
                        result = place_order(pos["symbol"], "buy", pos["qty"], reduce=True)
                        if result and result.get("orderId"):
                            mgr.close_position(current, "stop_loss")

                    # 趋势出场: 反向突破
                    elif held >= MIN_HOLD_SECONDS:
                        if side == "long" and current <= md["exit_low"]:
                            log(f"📉 趋势出场(跌破{TURTLE_EXIT}日低) {pos['symbol']} ${current:.2f}")
                            result = place_order(pos["symbol"], "sell", pos["qty"], reduce=True)
                            if result and result.get("orderId"):
                                mgr.close_position(current, "trend_exit")
                        elif side == "short" and current >= md["exit_high"]:
                            log(f"📈 趋势出场(突破{TURTLE_EXIT}日高) {pos['symbol']} ${current:.2f}")
                            result = place_order(pos["symbol"], "buy", pos["qty"], reduce=True)
                            if result and result.get("orderId"):
                                mgr.close_position(current, "trend_exit")

            # ── 保存状态 ──
            mgr.save()

            # ── 循环报告 ──
            if cycles % 10 == 0:
                bal = get_balance()
                pos_str = f"{mgr.position['symbol'][:8]} {mgr.position['side']} @${mgr.position['entry']:.2f}" if mgr.has_position() else "空仓"
                log(f"📊 余额=${bal:.2f} | {pos_str} | "
                    f"{mgr.trade_mem.summary()}")

            elapsed = time.time() - cycle_start
            sleep_time = max(1, SCAN_INTERVAL - elapsed)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log("🛑 收到退出信号")
            break
        except Exception as e:
            log(f"🔴 循环异常: {e}")
            log(traceback.format_exc())
            time.sleep(10)

    # 清理PID
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

if __name__ == "__main__":
    main()
