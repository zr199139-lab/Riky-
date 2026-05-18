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
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared_config import load_strategy_params, get_risk_limits

STRATEGY_NAME = "rsi_meanrev_paper"
SYMBOL        = "DOGE/USDT"
TIMEFRAME     = "1h"
INITIAL_CASH  = 1000.0
STOP_LOSS_PCT = 0.06    # 从8%→6% (加了杠杆, 止损更紧)
RSI_PERIOD    = 14
RSI_OB        = 70.0
RSI_OS        = 30.0
POSITION_PCT  = 0.20    # 从30%→20% (加了杠杆, 仓位更轻)
LOOP_SECONDS  = 300
LOG_ROUNDS    = 12
LEVERAGE      = 1       # 现货无杠杆
TAKER_FEE     = 0.0004  # Binance合约taker费率 0.04%

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

state = {"cash": INITIAL_CASH, "position": None, "trades": 0, "pnl": 0.0,
         "daily_pnl": 0.0, "daily_date": "", "funding_collected": 0.0, "fees_paid": 0.0}
if os.path.exists(STATE_FILE):
    try:
        state = json.load(open(STATE_FILE))
    except: pass

log.info(f"=== RSI均值回归 启动 @ {SYMBOL} ===")
log.info(f"资金=${INITIAL_CASH}, RSI阈值={RSI_OS}/{RSI_OB}, 止损={STOP_LOSS_PCT*100:.0f}%")

loop = 0
while True:
    try:
        # 热加载GPT参数
        gp = load_strategy_params('rsi_meanrev_paper')
        if gp:
            gp_rsi_os = gp.get('rsi_oversold')
            if gp_rsi_os: RSI_OS = float(gp_rsi_os)
            gp_rsi_ob = gp.get('rsi_overbought')
            if gp_rsi_ob: RSI_OB = float(gp_rsi_ob)
            gp_pct = gp.get('position_pct')
            if gp_pct: POSITION_PCT = float(gp_pct)
            gp_sl = gp.get('stop_loss_pct')
            if gp_sl: STOP_LOSS_PCT = float(gp_sl)
            if gp.get('active') == False:
                log.info(f'[GPT] 策略暂停指令, 跳过本轮')
                time.sleep(300); continue
        
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
            pnl = ((price - entry) * qty if side == "long" else (entry - price) * qty) * LEVERAGE
            
            # 日亏重置
            today_d = datetime.now().strftime('%Y-%m-%d')
            if state["daily_date"] != today_d:
                state["daily_pnl"] = 0.0
                state["daily_date"] = today_d
            
            stop_dist = entry * STOP_LOSS_PCT
            if (side == "long" and price < entry - stop_dist) or \
               (side == "short" and price > entry + stop_dist):
                proceeds = qty * price if side == "long" else qty * (2*entry - price)
                fee = qty * price * TAKER_FEE
                state["fees_paid"] = state.get("fees_paid", 0) + fee
                state["cash"] += proceeds - fee
                state["pnl"] += pnl - fee
                state["daily_pnl"] += pnl - fee
                state["trades"] += 1
                state["position"] = None
                log.info(f"[SL] {side.upper()} @ ${price:.4f} PnL=${pnl:.2f} 手续费=${fee:.4f} (x{LEVERAGE})")
                continue
            
            # Exit when RSI reverts
            if (side == "long" and rsi > 50) or (side == "short" and rsi < 50):
                proceeds = qty * price if side == "long" else qty * (2*entry - price)
                fee = qty * price * TAKER_FEE
                state["fees_paid"] = state.get("fees_paid", 0) + fee
                state["cash"] += proceeds - fee
                state["pnl"] += pnl - fee
                state["daily_pnl"] += pnl - fee
                state["trades"] += 1
                state["position"] = None
                log.info(f"[EXIT] {side.upper()} RSI回归={rsi:.1f} @ ${price:.4f} PnL=${pnl:.2f} 手续费=${fee:.4f} (x{LEVERAGE})")
                continue
        
        if not in_pos:
            capital = state["cash"] * POSITION_PCT
            qty = capital / price
            fee_open = capital * TAKER_FEE
            
            if rsi < RSI_OS:
                state["fees_paid"] = state.get("fees_paid", 0) + fee_open
                state["cash"] -= capital + fee_open
                state["position"] = {"entry": price, "qty": qty, "side": "long", "time": time.time()}
                log.info(f"[OPEN] LONG {qty:.2f} @ ${price:.4f} RSI={rsi:.1f} 手续费=${fee_open:.4f}")
            elif rsi > RSI_OB:
                state["fees_paid"] = state.get("fees_paid", 0) + fee_open
                state["cash"] -= capital + fee_open
                state["position"] = {"entry": price, "qty": qty, "side": "short", "time": time.time()}
                log.info(f"[OPEN] SHORT {qty:.2f} @ ${price:.4f} RSI={rsi:.1f} 手续费=${fee_open:.4f}")
        
        equity = state["cash"]
        if state.get("position"):
            p = state["position"]
            if p["side"] == "long":
                equity += p["qty"] * price
            else:
                equity += p["qty"] * (2 * p["entry"] - price)
        
        loop += 1
        if loop % LOG_ROUNDS == 0:
            c, t, p, fc = state["cash"], state["trades"], state["pnl"], state.get("fees_paid", 0)
            dpnl = state.get("daily_pnl", 0)
            pos_side = state.get("position", {}).get("side", "无")
            log.info(f"[STATUS] 权益=${equity:.2f} 现金=${c:.2f} 持仓={pos_side} RSI={rsi:.1f} "
                     f"交易={t} PnL=${p:.2f} 日亏=${dpnl:.2f} 手续费=${fc:.4f}")
        
        json.dump(state, open(STATE_FILE, "w"))
        time.sleep(LOOP_SECONDS)
    except Exception as e:
        log.error(f"[ERROR] {e}")
        time.sleep(60)
