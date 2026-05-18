#!/usr/bin/env python3
"""
31% Combo496 Paper Trading Bot
三层门控: 趋势确认 + 主力资金 + 防骗炮
5x leverage virtual, $1,000 capital, 1h timeframe
Backtest: Sharpe 1.99, Annualized 31.7%, 0 liquidation
"""
import os, json, time, logging, numpy as np, ccxt
from datetime import datetime

STRATEGY_NAME = "combo31_paper"
SYMBOLS      = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAME    = "1h"
INITIAL_CASH = 1000.0
LEVERAGE     = 5
STOP_LOSS    = 0.08
POSITION_PCT = 0.20
LOOP_SECONDS = 300
LOG_ROUNDS   = 12

LOG_FILE   = os.path.expanduser(f"~/charon/bot_logs/{STRATEGY_NAME}.log")
STATE_FILE = os.path.expanduser(f"~/charon/bot_logs/{STRATEGY_NAME}_state.json")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
log = logging.getLogger(STRATEGY_NAME)

exchange = ccxt.binance({"enableRateLimit": True})

def ema(series, period):
    arr = np.array(series, dtype=float)
    k = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i-1] * (1 - k)
    return out

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_g = np.mean(gains[-period:])
    avg_l = np.mean(losses[-period:])
    if avg_l < 1e-10: return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))

state = {"cash": INITIAL_CASH, "positions": {}, "trades": 0, "pnl": 0.0}
if os.path.exists(STATE_FILE):
    try: state = json.load(open(STATE_FILE))
    except: pass

log.info(f"=== 31% Combo三层门控 启动 ===")
log.info(f"资金=${INITIAL_CASH}x{LEVERAGE}, 币种={SYMBOLS}, 止损={STOP_LOSS*100:.0f}%")

loop = 0
while True:
    try:
        for sym in SYMBOLS:
            klines = exchange.fetch_ohlcv(sym, TIMEFRAME, limit=100)
            if not klines: continue
            price = klines[-1][4]
            closes = np.array([k[4] for k in klines], dtype=float)
            ema20 = ema(closes, 20)[-1]
            ema50 = ema(closes, 50)[-1]
            rsi = calc_rsi(closes)
            volume = sum(k[5] for k in klines[-5:])
            avg_vol = sum(k[5] for k in klines[-25:]) / 25
            
            # ── 三层门控 ──
            gate1_trend = 1 if ema20 > ema50 else -1  # 趋势方向
            gate2_vol = 1 if volume > avg_vol * 1.2 else 0  # 放量确认
            gate3_rsi = 1 if rsi < 70 and rsi > 30 else 0  # 非极端
            
            signal = gate1_trend * (1 + 0.3 * gate2_vol + 0.2 * gate3_rsi)
            
            # 持仓管理
            pos = state["positions"].get(sym)
            in_pos = pos is not None
            
            if in_pos:
                entry = pos["entry"]
                side = pos["side"]
                qty = pos["qty"]
                pnl = (price - entry) * qty * LEVERAGE if side == "long" else (entry - price) * qty * LEVERAGE
                
                # ATR止损 (用近期波动)
                recent_high = max(k[2] for k in klines[-20:])
                recent_low = min(k[3] for k in klines[-20:])
                atr_pct = (recent_high - recent_low) / price
                stop_dist = max(STOP_LOSS, atr_pct * 1.5) * entry
                
                if (side == "long" and price < entry - stop_dist) or \
                   (side == "short" and price > entry + stop_dist):
                    state["cash"] += pos["margin"] + pnl / LEVERAGE
                    state["pnl"] += pnl
                    state["trades"] += 1
                    del state["positions"][sym]
                    log.info(f"[SL] {sym} {side.upper()} ${price:.2f} PnL=${pnl:.2f}")
                    continue
                
                # 趋势反转平仓
                if (side == "long" and signal < -0.5) or (side == "short" and signal > 0.5):
                    state["cash"] += pos["margin"] + pnl / LEVERAGE
                    state["pnl"] += pnl
                    state["trades"] += 1
                    del state["positions"][sym]
                    log.info(f"[REVERSE] {sym} {side.upper()} 趋势反转 ${price:.2f} PnL=${pnl:.2f}")
                    continue
                
                # 分批止盈: ATR×1平50%, ATR×2平剩下50%
                tp1_pct = atr_pct * 1.0
                tp2_pct = atr_pct * 2.0
                
                # 第一批止盈 (50%)
                if (side == "long" and price > entry * (1 + tp1_pct)) or \
                   (side == "short" and price < entry * (1 - tp1_pct)):
                    if pos.get("tp1_done", False):
                        # 第二批止盈
                        state["cash"] += pos["margin"] + pnl / LEVERAGE
                        state["pnl"] += pnl
                        state["trades"] += 1
                        del state["positions"][sym]
                        log.info(f"[TP2] {sym} {side.upper()} ${price:.2f} PnL=${pnl:.2f} 全平")
                        continue
                    else:
                        # 第一批: 平50%, 留50%
                        half_qty = qty / 2
                        half_margin = pos["margin"] / 2
                        pnl_half = (price - entry) * half_qty * LEVERAGE if side == "long" else (entry - price) * half_qty * LEVERAGE
                        state["cash"] += half_margin + pnl_half / LEVERAGE
                        state["pnl"] += pnl_half
                        state["trades"] += 1
                        pos["qty"] = half_qty
                        pos["margin"] = half_margin
                        pos["tp1_done"] = True
                        log.info(f"[TP1] {sym} {side.upper()} ${price:.2f} PnL=${pnl_half:.2f} 留50%")
                        continue
            
            # 开仓
            if not in_pos and abs(signal) > 0.8:
                margin = state["cash"] * POSITION_PCT
                qty = margin * LEVERAGE / price
                side = "long" if signal > 0 else "short"
                state["cash"] -= margin
                state["positions"][sym] = {"entry": price, "qty": qty, "side": side, 
                    "margin": margin, "time": time.time()}
                log.info(f"[OPEN] {sym} {side.upper()} {qty:.4f}@${price:.2f} margin=${margin:.2f}x{LEVERAGE}")
        
        # 计算总权益
        equity = state["cash"]
        for sym, p in state["positions"].items():
            try:
                t = exchange.fetch_ticker(sym)
                mp = t["last"]
                if p["side"] == "long":
                    equity += p["margin"] + (mp - p["entry"]) * p["qty"]
                else:
                    equity += p["margin"] + (p["entry"] - mp) * p["qty"]
            except: pass
        
        # 资金费率采集 (每轮模拟结算)
        if state.get("positions"):
            for sym, p in state["positions"].items():
                if p.get("side") == "short":
                    try:
                        fr = exchange.fetch_funding_rate(sym.replace("/","") + ":USDT")
                        rate = float(fr["info"]["lastFundingRate"])
                        if rate > 0:
                            funding_earned = p["margin"] * rate * (LOOP_SECONDS / 28800)
                            state["funding_collected"] = state.get("funding_collected", 0) + funding_earned
                            p["funding_earned"] = p.get("funding_earned", 0) + funding_earned
                    except: pass
        
        loop += 1
        if loop % LOG_ROUNDS == 0:
            c, t, p, fc = state["cash"], state["trades"], state["pnl"], state.get("funding_collected", 0)
            n_pos = len(state["positions"])
            log.info(f"[STATUS] 权益=${equity:.2f} 现金=${c:.2f} 持仓={n_pos}个 交易={t} PnL=${p:.2f} 费率收入=${fc:.4f}")
        
        json.dump(state, open(STATE_FILE, "w"))
        time.sleep(LOOP_SECONDS)
    except Exception as e:
        log.error(f"[ERROR] {e}")
        time.sleep(60)
