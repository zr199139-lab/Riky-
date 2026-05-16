#!/usr/bin/env python3
"""
统一回测引擎：7策略 × 4币种 × 30天 K线
输出: 完整排名 + 每个策略的 PnL/夏普/胜率/回撤
"""
import json, numpy as np, sys, os, math

with open('/tmp/may_klines.json') as f:
    RAW = json.load(f)

# ── 指标函数 ──
def ema(s, p):
    a = np.array(s, dtype=float)
    k = 2.0/(p+1); o = np.empty_like(a); o[0]=a[0]
    for i in range(1,len(a)): o[i]=a[i]*k+o[i-1]*(1-k)
    return o

def macd_line(closes, f=12, s=26):
    return ema(closes, f) - ema(closes, s)

def rsi(closes, p=14):
    a = np.array(closes, dtype=float)
    d = np.diff(a); g = np.maximum(d,0); l = np.maximum(-d,0)
    ag = np.convolve(g, np.ones(p)/p, 'valid')
    al = np.convolve(l, np.ones(p)/p, 'valid')
    rs = np.divide(ag, al, out=np.ones_like(ag), where=al>1e-10)
    return np.concatenate([[50]*p, 100 - 100/(1+rs)])

def atr(klines, p=14):
    highs = np.array([k[2] for k in klines])
    lows = np.array([k[3] for k in klines])
    closes = np.array([k[4] for k in klines])
    tr = np.maximum(highs[1:]-lows[1:], np.maximum(abs(highs[1:]-closes[:-1]), abs(lows[1:]-closes[:-1])))
    atr_vals = np.convolve(tr, np.ones(p)/p, 'valid')
    return np.concatenate([[np.mean(tr[:p])]*p, atr_vals])

# ── 策略函数 ──
# 每个策略返回: (entry_prices, exit_prices, sides, reasons)
# sides: 1=long, -1=short, 0=exit

def strat_turtle(closes, highs, lows):
    """海龟: 20日突破入场, ATR×2止损, 10日出场"""
    n = len(closes)
    entries, exits, sides, reasons = [], [], [], []
    in_pos, entry_p, side, entry_idx = False, 0, 0, 0
    for i in range(21, n):
        hh20 = max(highs[i-20:i-1])
        ll20 = min(lows[i-20:i-1])
        hh10 = max(highs[i-10:i-1])
        ll10 = min(lows[i-10:i-1])
        atr_val = atr_data[i]
        price = closes[i]
        
        if not in_pos:
            if price > hh20:  # 突破买入
                entries.append(price); sides.append(1); reasons.append('turtle_long')
                entry_p, side, entry_idx = price, 1, i; in_pos = True
            elif price < ll20:  # 突破卖出
                entries.append(price); sides.append(-1); reasons.append('turtle_short')
                entry_p, side, entry_idx = price, -1, i; in_pos = True
        else:
            stop = entry_p - atr_val*2 if side==1 else entry_p + atr_val*2
            exit_sig = (price < ll10) if side==1 else (price > hh10)
            if (side==1 and price<stop) or (side==-1 and price>stop):
                exits.append(price); in_pos=False; reasons.append('turtle_sl')
            elif exit_sig:
                exits.append(price); in_pos=False; reasons.append('turtle_exit')
    return entries, exits, sides, [r for r in reasons if r]

def strat_macd_trend(closes):
    """MACD趋势: 金叉做多/死叉做空"""
    macd = macd_line(closes)
    sig = ema(macd, 9)
    n = len(closes); entries, exits, sides, reasons = [], [], [], []
    in_pos, entry_p, side = False, 0, 0
    for i in range(35, n):
        price = closes[i]
        if macd[i] > sig[i] and macd[i-1] <= sig[i-1]:
            if in_pos:
                if side == 1: continue
                exits.append(price); in_pos=False; reasons.append('macd_reverse')
            entries.append(price); sides.append(1); reasons.append('macd_long')
            entry_p, side = price, 1; in_pos = True
        elif macd[i] < sig[i] and macd[i-1] >= sig[i-1]:
            if in_pos:
                if side == -1: continue
                exits.append(price); in_pos=False; reasons.append('macd_reverse')
            entries.append(price); sides.append(-1); reasons.append('macd_short')
            entry_p, side = price, -1; in_pos = True
    return entries, exits, sides, reasons

def strat_macd_rsi(closes):
    """MACD+RSI双确认: 金叉+RSI<70做多, 死叉+RSI>30做空"""
    macd = macd_line(closes)
    sig = ema(macd, 9)
    rsi_vals = rsi(closes)
    n = len(closes); entries, exits, sides, reasons = [], [], [], []
    in_pos, entry_p, side = False, 0, 0
    for i in range(50, n):
        price = closes[i]
        if macd[i] > sig[i] and macd[i-1] <= sig[i-1] and rsi_vals[i] < 70:
            if in_pos:
                if side == -1:
                    exits.append(price); in_pos=False; reasons.append('macdrsi_reverse')
            entries.append(price); sides.append(1); reasons.append('macdrsi_long')
            entry_p, side = price, 1; in_pos = True
        elif macd[i] < sig[i] and macd[i-1] >= sig[i-1] and rsi_vals[i] > 30:
            if in_pos:
                if side == 1:
                    exits.append(price); in_pos=False; reasons.append('macdrsi_reverse')
            entries.append(price); sides.append(-1); reasons.append('macdrsi_short')
            entry_p, side = price, -1; in_pos = True
    return entries, exits, sides, reasons

def strat_rsi_meanrev(closes):
    """RSI均值回归: <30做多, >70做空"""
    rsi_vals = rsi(closes)
    n = len(closes); entries, exits, sides, reasons = [], [], [], []
    in_pos, entry_p, side = False, 0, 0
    rsi_os, rsi_ob = 30, 70
    for i in range(20, n):
        price = closes[i]
        r = rsi_vals[i]
        if not in_pos:
            if r < rsi_os:
                entries.append(price); sides.append(1); reasons.append('rsi_long')
                entry_p, side = price, 1; in_pos = True
            elif r > rsi_ob:
                entries.append(price); sides.append(-1); reasons.append('rsi_short')
                entry_p, side = price, -1; in_pos = True
        else:
            if (side==1 and r>50) or (side==-1 and r<50):
                exits.append(price); in_pos=False; reasons.append('rsi_exit')
    return entries, exits, sides, reasons

def strat_pairs_arb(btc_closes, eth_closes):
    """配对统计套利: BTC/ETH比率偏离>2σ"""
    n = min(len(btc_closes), len(eth_closes))
    ratio = np.array(btc_closes[:n]) / np.array(eth_closes[:n])
    entries, exits, sides, reasons = [], [], [], []
    in_pos, entry_r, side = False, 0, 0
    for i in range(30, n):
        window = ratio[i-30:i]
        m, s = np.mean(window), np.std(window)
        if s < 0.001: continue
        z = (ratio[i] - m) / s
        if not in_pos:
            if z > 2:
                entries.append(ratio[i]); sides.append(-1); reasons.append('pairs_short_btc')
                entry_r, side = ratio[i], -1; in_pos = True
            elif z < -2:
                entries.append(ratio[i]); sides.append(1); reasons.append('pairs_long_btc')
                entry_r, side = ratio[i], 1; in_pos = True
        else:
            z_exit = (ratio[i] - m) / s
            if abs(z_exit) < 0.5:
                exits.append(ratio[i]); in_pos=False; reasons.append('pairs_exit')
    return entries, exits, sides, reasons

def strat_combo31(closes, highs, lows, volumes):
    """31%三层门控: 趋势+量能+RSI"""
    e20 = ema(closes, 20); e50 = ema(closes, 50)
    rsi_vals = rsi(closes)
    n = len(closes)
    entries, exits, sides, reasons = [], [], [], []
    in_pos, entry_p, side = False, 0, 0
    for i in range(55, n):
        price = closes[i]
        vol_avg = np.mean(volumes[i-25:i])
        vol_ratio = volumes[i] / vol_avg if vol_avg > 0 else 0
        trend = 1 if e20[i] > e50[i] else -1
        gate2 = 1 if vol_ratio > 1.2 else 0
        gate3 = 1 if rsi_vals[i] < 70 and rsi_vals[i] > 30 else 0
        sig = trend * (1 + 0.3*gate2 + 0.2*gate3)
        
        if not in_pos and abs(sig) > 0.8:
            entries.append(price); sides.append(1 if sig>0 else -1)
            reasons.append(f'combo_{"long" if sig>0 else "short"}')
            entry_p, side = price, 1 if sig>0 else -1; in_pos = True
        elif in_pos:
            if (side==1 and sig<-0.5) or (side==-1 and sig>0.5):
                exits.append(price); in_pos=False; reasons.append('combo_reverse')
    return entries, exits, sides, reasons

def strat_meanrevert(closes, highs, lows):
    """波动率均值回归: BB+RSI"""
    n = len(closes)
    entries, exits, sides, reasons = [], [], [], []
    in_pos, entry_p, side = False, 0, 0
    rsi_vals = rsi(closes)
    for i in range(20, n):
        window = closes[i-20:i]
        m, s = np.mean(window), np.std(window)
        upper, lower = m + 2*s, m - 2*s
        price = closes[i]
        r = rsi_vals[i]
        
        if not in_pos:
            if price <= lower and r < 30:
                entries.append(price); sides.append(1); reasons.append('mr_long')
                entry_p, side = price, 1; in_pos = True
            elif price >= upper and r > 70:
                entries.append(price); sides.append(-1); reasons.append('mr_short')
                entry_p, side = price, -1; in_pos = True
        else:
            if (side==1 and price>=m) or (side==-1 and price<=m):
                exits.append(price); in_pos=False; reasons.append('mr_exit')
    return entries, exits, sides, reasons

# ── 回测引擎 ──
CAPITAL = 1000.0

def run_backtest(name, klines, entries, exits, sides):
    """执行回测, 返回 {总PnL, 夏普, 胜率, 回撤, 交易数}"""
    prices = [k['c'] for k in klines]
    capital = CAPITAL
    pnl_list = []
    wins = 0; trades = 0
    equity = CAPITAL
    peak = CAPITAL
    dd_max = 0
    
    for i in range(min(len(entries), len(exits)+1 if len(exits)<len(entries) else len(entries))):
        if i >= len(entries) or i >= len(sides): break
        entry_p = entries[i]; side = sides[i]
        exit_p = exits[i] if i < len(exits) else prices[-1]
        
        qty = capital * 0.2 / entry_p  # 20%仓位/笔
        pnl = qty * (exit_p - entry_p) * side
        capital += pnl
        pnl_list.append(pnl)
        trades += 1
        if pnl > 0: wins += 1
    
    # 计算权益曲线
    eq_curve = [CAPITAL]
    for p in pnl_list:
        eq_curve.append(eq_curve[-1] + p)
        peak = max(peak, eq_curve[-1])
        dd = (peak - eq_curve[-1]) / peak * 100
        dd_max = max(dd_max, dd)
    
    total_pnl = capital - CAPITAL
    if trades == 0: return None
    win_rate = wins / trades * 100
    sharpe = np.mean(pnl_list) / (np.std(pnl_list)+1e-10) * np.sqrt(24*30/len(pnl_list)) if len(pnl_list) > 1 else 0
    
    return {
        '策略': name, '总PnL': round(total_pnl,2), '收益率': f'{total_pnl/CAPITAL*100:.1f}%',
        '夏普': round(sharpe,2), '胜率': f'{win_rate:.0f}%',
        '回撤': round(dd_max,1), '交易': trades
    }

# ── 跑全部 ──
results = []
for cname, klines in RAW.items():
    closes = np.array([k['c'] for k in klines])
    highs = np.array([k['h'] for k in klines])
    lows = np.array([k['l'] for k in klines])
    volumes = np.array([k['v'] for k in klines])
    
    # 预计算ATR
    global atr_data
    tr = np.maximum(highs[1:]-lows[1:], np.maximum(abs(highs[1:]-closes[:-1]), abs(lows[1:]-closes[:-1])))
    atr_data = np.concatenate([[np.mean(tr[:14])]*14, np.convolve(tr, np.ones(14)/14, 'valid')])
    
    strategies = [
        ('海龟趋势', lambda: strat_turtle(closes, highs, lows)),
        ('MACD趋势', lambda: strat_macd_trend(closes)),
        ('MACD+RSI', lambda: strat_macd_rsi(closes)),
        ('RSI均值回归', lambda: strat_rsi_meanrev(closes)),
        ('波动率回归', lambda: strat_meanrevert(closes, highs, lows)),
        ('31%Combo', lambda: strat_combo31(closes, highs, lows, volumes)),
    ]
    
    for sname, sfunc in strategies:
        try:
            entries, exits, sides, reasons = sfunc()
            r = run_backtest(f'{sname}@{cname}', klines, entries, exits, sides)
            results.append(r)
        except Exception as e:
            results.append({'策略': f'{sname}@{cname}', '总PnL': f'ERR:{e}', '收益率':'','夏普':'','胜率':'','回撤':'','交易':''})
    
    # 配对套利 (BTC/ETH only)
    if cname == 'BTC':
        eth_c = np.array([k['c'] for k in RAW['ETH']])
        entries, exits, sides, reasons = strat_pairs_arb(closes, eth_c)
        r = run_backtest('配对套利@BTC/ETH', RAW['ETH'], entries, exits, sides)
        results.append(r)

    # ── 机构策略 ──
    # Dual Thrust (vnpy经典)
    try:
        entries, exits, sides, reasons = [], [], [], []
        d = 20; k1, k2 = 0.5, 0.5
        for i in range(d, len(closes)):
            hh = max(highs[i-d:i]); ll = min(lows[i-d:i])
            hc = max(highs[i-1-d:i-1]); lc = min(lows[i-1-d:i-1])
            range_v = max(hh - lc, hc - ll)
            buy_line = klines[i]['o'] + k1 * range_v
            sell_line = klines[i]['o'] - k2 * range_v
            if len(entries) == len(exits):
                if closes[i] > buy_line:
                    entries.append(closes[i]); sides.append(1)
                elif closes[i] < sell_line:
                    entries.append(closes[i]); sides.append(-1)
            else:
                if sides[-1]==1 and closes[i] < klines[i]['o']:
                    exits.append(closes[i])
                    entries.append(closes[i]); sides.append(-1)
                elif sides[-1]==-1 and closes[i] > klines[i]['o']:
                    exits.append(closes[i])
                    entries.append(closes[i]); sides.append(1)
        r = run_backtest(f'DualThrust@{cname}', klines, entries[:len(exits)], exits, sides[:len(exits)])
        results.append(r)
    except Exception as e: results.append({'策略': f'DualThrust@{cname}', '总PnL': f'ERR:{e}'})

    # ATR Channel (vnpy CTA)
    try:
        entries, exits, sides, reasons = [], [], [], []
        in_pos, entry_p, side = False, 0, 0
        for i in range(30, len(closes)):
            e20 = np.mean(closes[i-20:i])
            tr_vals = [max(highs[j]-lows[j], abs(highs[j]-closes[j-1]), abs(lows[j]-closes[j-1])) for j in range(i-14, i)]
            atr_v = np.mean(tr_vals)
            upper = e20 + atr_v * 2
            lower = e20 - atr_v * 2
            price = closes[i]
            if not in_pos:
                if price > upper:
                    entries.append(price); sides.append(1); entry_p, side = price, 1; in_pos=True
                elif price < lower:
                    entries.append(price); sides.append(-1); entry_p, side = price, -1; in_pos=True
            else:
                if price < e20 if side==1 else price > e20:
                    exits.append(price); in_pos=False
        r = run_backtest(f'ATRChannel@{cname}', klines, entries[:len(exits)], exits, sides[:len(exits)])
        results.append(r)
    except Exception as e: results.append({'策略': f'ATRChannel@{cname}', '总PnL': f'ERR:{e}'})

    # SuperTrend (jesse/vnpy经典)
    try:
        entries, exits, sides, reasons = [], [], [], []
        in_pos, entry_p, side, atr_mult = False, 0, 0, 3.0
        for i in range(20, len(closes)):
            hl2 = (highs[i] + lows[i]) / 2
            tr_vals = [max(highs[j]-lows[j], abs(highs[j]-closes[j-1]), abs(lows[j]-closes[j-1])) for j in range(i-10, i)]
            atr_v = np.mean(tr_vals) if tr_vals else 0
            upper_band = hl2 + atr_mult * atr_v
            lower_band = hl2 - atr_mult * atr_v
            price = closes[i]
            if not in_pos:
                if price > upper_band:
                    entries.append(price); sides.append(1); entry_p, side, in_pos = price, 1, True
                elif price < lower_band:
                    entries.append(price); sides.append(-1); entry_p, side, in_pos = price, -1, True
            else:
                if side==1 and price < lower_band:
                    exits.append(price); in_pos=False
                elif side==-1 and price > upper_band:
                    exits.append(price); in_pos=False
        r = run_backtest(f'SuperTrend@{cname}', klines, entries[:len(exits)], exits, sides[:len(exits)])
        if r: results.append(r)
    except Exception as e:
        results.append({'策略': f'SuperTrend@{cname}', '总PnL': -9999, '收益率':'ERR', '夏普':0, '胜率':'0%', '回撤':0, '交易':0})

    # Freqtrade RSI+MA (freqtrade sample strategy)
    try:
        entries, exits, sides, reasons = [], [], [], []
        in_pos, entry_p, side = False, 0, 0
        rsi_vals = rsi(closes)
        for i in range(35, len(closes)):
            price = closes[i]; r = rsi_vals[i]
            e20 = np.mean(closes[i-20:i]); e50 = np.mean(closes[i-50:i])
            if not in_pos:
                if r < 30 and e20 > e50:
                    entries.append(price); sides.append(1); entry_p, side = price, 1; in_pos=True
                elif r > 70 and e20 < e50:
                    entries.append(price); sides.append(-1); entry_p, side = price, -1; in_pos=True
            else:
                if side==1 and r > 70:
                    exits.append(price); in_pos=False
                elif side==-1 and r < 30:
                    exits.append(price); in_pos=False
        r = run_backtest(f'FreqRSIMA@{cname}', klines, entries[:len(exits)], exits, sides[:len(exits)])
        results.append(r)
    except Exception as e: results.append({'策略': f'FreqRSIMA@{cname}', '总PnL': f'ERR:{e}'})

# ── 按PnL排序 ──
results = [r for r in results if r is not None and isinstance(r, dict) and isinstance(r.get('总PnL'), (int, float))]
results.sort(key=lambda x: float(x['总PnL']), reverse=True)

print('='*100)
print(f'{"排名":>3} {"策略":<22} {"总PnL":>8} {"收益率":>8} {"夏普":>6} {"胜率":>6} {"回撤":>6} {"交易":>6}')
print('='*100)
for i, r in enumerate(results, 1):
    pnl = str(r['总PnL'])
    ret = str(r['收益率'])
    shp = str(r['夏普'])
    wr = str(r['胜率'])
    dd = str(r['回撤'])
    tr = str(r['交易'])
    print(f'{i:>3} {r["策略"]:<22} {pnl:>8} {ret:>8} {shp:>6} {wr:>6} {dd:>6} {tr:>6}')

# 汇总平均
print('='*100)
by_strat = {}
for r in results:
    name = r['策略'].split('@')[0]
    pnl = float(str(r['总PnL']).replace('ERR','-9999'))
    if name not in by_strat: by_strat[name] = []
    by_strat[name].append(pnl)

print(f'\n策略平均排名:')
avg_sorted = sorted(by_strat.items(), key=lambda x: np.mean(x[1]), reverse=True)
for name, pnls in avg_sorted:
    print(f'  {name:<20} 均PnL=${np.mean(pnls):+.2f}  ({", ".join(f"${p:+.1f}" for p in pnls)})')

json.dump(results, open('/tmp/backtest_results.json', 'w'), indent=2, ensure_ascii=False)
print(f'\n完整结果已保存: /tmp/backtest_results.json')
