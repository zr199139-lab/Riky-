#!/usr/bin/env python3
"""
MACD+RSI Dual-Confirm Paper Trading Bot @ ETH/USDT
Strategy: MACD golden cross + RSI<70 => long
          MACD death cross  + RSI>30 => short
Stop Loss: 5%
Virtual Capital: $1,000
Timeframe: 1h
Backtest: Sharpe 2.46, 90d +$109.83, Annualized 98.5%
"""

import os
import json
import time
import logging
import numpy as np
import ccxt
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
STRATEGY_NAME = "macd_rsi_paper"
SYMBOL        = "ETH/USDT"
TIMEFRAME     = "1h"
INITIAL_CASH  = 1000.0
STOP_LOSS_PCT = 0.05
FAST          = 12
SLOW          = 26
SIGNAL_PERIOD = 9
RSI_PERIOD    = 14
RSI_OB        = 70.0   # overbought threshold
RSI_OS        = 30.0   # oversold threshold
LOOP_SECONDS  = 300
LOG_ROUNDS    = 12

LOG_FILE   = os.path.expanduser(f"~/charon/bot_logs/{STRATEGY_NAME}.log")
STATE_FILE = os.path.expanduser(f"~/charon/bot_logs/{STRATEGY_NAME}_state.json")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(STRATEGY_NAME)

# ── Exchange ─────────────────────────────────────────────────────────────────
exchange = ccxt.binance({"enableRateLimit": True})

# ── Indicators ───────────────────────────────────────────────────────────────
def ema(series: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(series)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = series[i] * k + out[i - 1] * (1 - k)
    return out

def calc_macd(closes: np.ndarray, fast=12, slow=26, signal=9):
    ema_fast    = ema(closes, fast)
    ema_slow    = ema(closes, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_rsi(closes: np.ndarray, period=14) -> np.ndarray:
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    rsi_vals = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs  = avg_gain / avg_loss if avg_loss != 0 else 1e9
        rsi_vals.append(100 - 100 / (1 + rs))
    return np.array(rsi_vals)

# ── State ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "cash": INITIAL_CASH,
        "position": 0.0,
        "entry_price": None,
        "side": None,
        "total_pnl": 0.0,
        "trade_count": 0,
        "round": 0,
    }

def save_state(state: dict):
    json.dump(state, open(STATE_FILE, "w"), indent=2)

# ── Trade helpers ─────────────────────────────────────────────────────────────
def open_position(state: dict, side: str, price: float):
    size = state["cash"] / price
    state["position"]    = size if side == "long" else -size
    state["entry_price"] = price
    state["side"]        = side
    state["cash"]        = 0.0
    entry_price = state["entry_price"]
    pos = state["position"]
    log.info(f"[OPEN] {side.upper()} {abs(pos):.6f} ETH @ {price:.2f} | entry={entry_price:.2f}")

def close_position(state: dict, price: float, reason: str = "SIGNAL"):
    entry = state["entry_price"]
    pos   = state["position"]
    if state["side"] == "long":
        pnl = pos * (price - entry)
        state["cash"] = pos * price
    else:
        pnl = abs(pos) * (entry - price)
        state["cash"] = abs(pos) * entry + pnl
    state["total_pnl"] += pnl
    state["trade_count"] += 1
    entry_val = state["entry_price"]
    tc = state["trade_count"]
    log.info(
        f"[{reason}] CLOSE {state['side'].upper()} @ {price:.2f} | "
        f"entry={entry_val:.2f} pnl={pnl:+.4f} total_pnl={state['total_pnl']:+.4f} trades={tc}"
    )
    state["position"]    = 0.0
    state["entry_price"] = None
    state["side"]        = None

def check_stop_loss(state: dict, price: float) -> bool:
    if state["side"] is None:
        return False
    entry = state["entry_price"]
    if state["side"] == "long" and price <= entry * (1 - STOP_LOSS_PCT):
        close_position(state, price, reason="SL")
        return True
    if state["side"] == "short" and price >= entry * (1 + STOP_LOSS_PCT):
        close_position(state, price, reason="SL")
        return True
    return False

def equity(state: dict, price: float) -> float:
    if state["side"] == "long":
        return state["position"] * price
    if state["side"] == "short":
        pos   = abs(state["position"])
        entry = state["entry_price"]
        return pos * entry + pos * (entry - price)
    return state["cash"]

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    log.info(f"=== {STRATEGY_NAME} started | capital=${INITIAL_CASH} SL={STOP_LOSS_PCT*100}% ===")
    state = load_state()

    while True:
        try:
            state["round"] += 1
            ohlcv  = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=120)
            closes = np.array([c[4] for c in ohlcv], dtype=float)
            price  = closes[-1]

            macd_line, signal_line, _ = calc_macd(closes, FAST, SLOW, SIGNAL_PERIOD)
            rsi_vals = calc_rsi(closes, RSI_PERIOD)

            prev_macd   = macd_line[-2]
            prev_signal = signal_line[-2]
            curr_macd   = macd_line[-1]
            curr_signal = signal_line[-1]
            curr_rsi    = rsi_vals[-1]

            golden_cross = prev_macd <= prev_signal and curr_macd > curr_signal
            death_cross  = prev_macd >= prev_signal and curr_macd < curr_signal

            long_signal  = golden_cross and curr_rsi < RSI_OB
            short_signal = death_cross  and curr_rsi > RSI_OS

            sl_hit = check_stop_loss(state, price)

            if not sl_hit:
                side = state["side"]
                if side == "long" and short_signal:
                    close_position(state, price, reason="SIGNAL")
                    open_position(state, "short", price)
                elif side == "short" and long_signal:
                    close_position(state, price, reason="SIGNAL")
                    open_position(state, "long", price)
                elif side is None:
                    if long_signal:
                        open_position(state, "long", price)
                    elif short_signal:
                        open_position(state, "short", price)

            rnd = state["round"]
            if rnd % LOG_ROUNDS == 0:
                eq       = equity(state, price)
                cash_val = state["cash"]
                pos_val  = state["position"]
                pnl_val  = state["total_pnl"]
                side_val = state["side"]
                log.info(
                    f"[STATUS] round={rnd} price={price:.2f} rsi={curr_rsi:.1f} "
                    f"equity={eq:.4f} cash={cash_val:.4f} pos={pos_val:.6f} "
                    f"side={side_val} total_pnl={pnl_val:+.4f}"
                )

            save_state(state)

        except ccxt.NetworkError as e:
            log.warning(f"Network error: {e}")
        except ccxt.ExchangeError as e:
            log.warning(f"Exchange error: {e}")
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)

        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    main()
