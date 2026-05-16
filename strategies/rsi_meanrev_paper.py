#!/usr/bin/env python3
"""
RSI Mean-Reversion Paper Trading Bot @ DOGE/USDT
Strategy: RSI<30 oversold => long, RSI>70 overbought => short
Stop Loss: 8%
Virtual Capital: $1,000
Timeframe: 1h
Backtest: Sharpe 2.04, 87% win rate, 90d +$30.37
"""
import os, json, time, logging, numpy as np, ccxt
from datetime import datetime

STRATEGY_NAME = "rsi_meanrev_paper"
SYMBOL        = "DOGE/USDT"
TIMEFRAME     = "1h"
INITIAL_CASH  = 1000.0
STOP_LOSS_PCT = 0.08
RSI_PERIOD    = 14
RSI_OB        = 70.0
RSI_OS        = 30.0
POSITION_PCT  = 0.30
LOOP_SECONDS  = 300
LOG_ROUNDS    = 12

LOG_FILE   = os.path.expanduser(f"~/charon/bot_logs/{STRATEGY_NAME}.log")
STATE_FILE = os.path.expanduser(f"~/charon/bot_logs/{STRATEGY_NAME}_state.json")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
log = logging.getLogger(STRATEGY_NAME)

exchange = ccxt.binance({"enableRateLimit": True})

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_g = np.mean(gains[-period:])
    avg_l = np.mean(losses[-period:])
    if avg_l < 1e-10: return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))

state = {"cash": INITIAL_CASH, "position": None, "trades": 0, "pnl": 0.0}
if os.path.exists(STATE_FILE):
    try:
        state = json.load(open(STATE_FILE))
    except: pass

log.info(f"=== RSI均值回归 启动 @ {SYMBOL} ===")
log.info(f"资金=${INITIAL_CASH}, RSI阈值={RSI_OS}/{RSI_OB}, 止损={STOP_LOSS_PCT*100:.0f}%")

loop = 0
while True:
    try:
        klines = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=100)
        if not klines: continue
        price = klines[-1][4]
        closes = [k[4] for k in klines]
        rsi = calc_rsi(closes)
        
        pos = state.get("position")
        in_pos = pos is not None
        
        if in_pos:
            entry = pos["entry"]
            side = pos["side"]
            qty = pos["qty"]
            pnl = (price - entry) * qty if side == "long" else (entry - price) * qty
            
            stop_dist = entry * STOP_LOSS_PCT
            if (side == "long" and price < entry - stop_dist) or \
               (side == "short" and price > entry + stop_dist):
                proceeds = qty * price if side == "long" else qty * (2*entry - price)
                state["cash"] += proceeds
                state["pnl"] += pnl
                state["trades"] += 1
                state["position"] = None
                log.info(f"[SL] {side.upper()} @ ${price:.4f} PnL=${pnl:.2f}")
                continue
            
            # Exit when RSI reverts
            if (side == "long" and rsi > 50) or (side == "short" and rsi < 50):
                proceeds = qty * price if side == "long" else qty * (2*entry - price)
                state["cash"] += proceeds
                state["pnl"] += pnl
                state["trades"] += 1
                state["position"] = None
                log.info(f"[EXIT] {side.upper()} RSI回归={rsi:.1f} @ ${price:.4f} PnL=${pnl:.2f}")
                continue
        
        if not in_pos:
            capital = state["cash"] * POSITION_PCT
            qty = capital / price
            
            if rsi < RSI_OS:
                state["cash"] -= capital
                state["position"] = {"entry": price, "qty": qty, "side": "long", "time": time.time()}
                log.info(f"[OPEN] LONG {qty:.2f} @ ${price:.4f} RSI={rsi:.1f}")
            elif rsi > RSI_OB:
                state["cash"] -= capital
                state["position"] = {"entry": price, "qty": qty, "side": "short", "time": time.time()}
                log.info(f"[OPEN] SHORT {qty:.2f} @ ${price:.4f} RSI={rsi:.1f}")
        
        equity = state["cash"]
        if state.get("position"):
            p = state["position"]
            if p["side"] == "long":
                equity += p["qty"] * price
            else:
                equity += p["qty"] * (2 * p["entry"] - price)
        
        loop += 1
        if loop % LOG_ROUNDS == 0:
            c, t, p = state["cash"], state["trades"], state["pnl"]
            pos_side = state.get("position", {}).get("side", "无")
            log.info(f"[STATUS] 权益=${equity:.2f} 现金=${c:.2f} 持仓={pos_side} RSI={rsi:.1f} 交易={t} PnL=${p:.2f}")
        
        json.dump(state, open(STATE_FILE, "w"))
        time.sleep(LOOP_SECONDS)
    except Exception as e:
        log.error(f"[ERROR] {e}")
        time.sleep(60)
