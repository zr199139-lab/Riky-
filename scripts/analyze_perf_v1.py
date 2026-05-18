#!/usr/bin/env python3
"""
暗黑星火 · 交易绩效分析器 v1
=============================
读提取好的成交数据 → 分类策略 → 算指标 → 输出报告
"""
import json, os, sys
from datetime import datetime, timezone
from collections import defaultdict
import math

ANALYSIS_DIR = '/home/admin/charon/analysis'

def load_trades():
    """加载成交数据"""
    path = f'{ANALYSIS_DIR}/all_trades_202605.json'
    if not os.path.exists(path):
        # fallback到现有数据
        path = f'{ANALYSIS_DIR}/bot_trade_logs_12d.json'
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None
    
    with open(path) as f:
        return json.load(f)

def classify_trade(sym, side, qty, price, time_ms):
    """把一笔交易分类到某个策略"""
    sym_base = sym.replace('USDT', '')
    
    # 1. 现货交易
    # 2. 合约交易 - 按模式和币种分类
    return {
        'ETHUSDT': 'manual_short',  # 用户做的ETH做空
        'BCHUSDT': 'manual_short',  # 用户做的BCH做空
        'BTCUSDT': 'manual_trade',
        'SOLUSDT': 'manual_trade',
        'DOGEUSDT': 'grid_trade',  # 网格交易
        'XRPUSDT': 'grid_trade',
    }.get(sym, 'other')

def calculate_pnl(trades_list):
    """从合约成交记录计算净PnL"""
    total_pnl = 0.0
    total_fees = 0.0
    total_volume = 0.0
    wins = 0
    losses = 0
    pnls = []
    
    for t in trades_list:
        realized_pnl = float(t.get('realizedPnl', 0))
        fee = float(t.get('commission', 0))
        qty = float(t.get('qty', 0))
        price = float(t.get('price', 0))
        
        total_pnl += realized_pnl
        total_fees += abs(fee)
        total_volume += qty * price
        
        if realized_pnl > 0.001: wins += 1
        elif realized_pnl < -0.001: losses += 1
        
        if abs(realized_pnl) > 0.001:
            pnls.append(realized_pnl)
    
    total_trades = wins + losses
    win_rate = wins / total_trades if total_trades > 0 else 0
    avg_win = sum(p for p in pnls if p > 0) / wins if wins > 0 else 0
    avg_loss = sum(p for p in pnls if p < 0) / losses if losses > 0 else 0
    profit_factor = abs(sum(p for p in pnls if p > 0) / sum(p for p in pnls if p < 0)) if sum(p for p in pnls if p < 0) != 0 else float('inf')
    
    # Sharpe (简化为 daily)
    if pnls:
        avg = sum(pnls) / len(pnls)
        var = sum((p - avg) ** 2 for p in pnls) / len(pnls)
        std = var ** 0.5
        sharpe = (avg / std) * (365 ** 0.5) if std > 0 else 0
    else:
        sharpe = 0
    
    return {
        'trades': total_trades,
        'pnl': round(total_pnl, 2),
        'fees': round(total_fees, 2),
        'net_pnl': round(total_pnl - total_fees, 2),
        'volume': round(total_volume, 2),
        'fee_ratio': round(total_fees / abs(total_pnl) * 100, 1) if abs(total_pnl) > 0.01 else 0,
        'win_rate': round(win_rate * 100, 1),
        'wins': wins,
        'losses': losses,
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'sharpe': round(sharpe, 2),
        'avg_pnl_per_trade': round(total_pnl / total_trades, 2) if total_trades > 0 else 0,
    }

def analyze():
    """主分析"""
    print("=" * 60)
    print("暗黑星火 · 5月交易绩效分析")
    print("=" * 60)
    
    data = load_trades()
    if not data:
        print("❌ 无数据")
        return
    
    # 提取数据
    futures = data.get('futures', {})
    spot = data.get('spot', {})
    
    # 如果没有API数据，可能只有bot_logs
    if not futures and data.get('trade_lines'):
        print("⚠️ 使用 bot_trade_logs数据(supplementary)")
    
    all_futures_trades = []
    for sym, trades in futures.items():
        all_futures_trades.extend(trades)
    
    all_spot_trades = []
    for sym, trades in spot.items():
        all_spot_trades.extend(trades)
    
    print(f"\n📊 总数据: 合约{len(all_futures_trades)}笔 + 现货{len(all_spot_trades)}笔")
    
    # === 按币种分析 ===
    print(f"\n{'─'*60}")
    print("📈 按币种 (合约)")
    print(f"{'─'*60}")
    print(f"{'币种':<14} {'笔数':<6} {'总PnL':<10} {'手续费':<10} {'净PnL':<10} {'胜率':<8} {'均盈亏':<10}")
    print(f"{'─'*60}")
    
    by_symbol = defaultdict(list)
    for t in all_futures_trades:
        sym = t.get('symbol', '?')
        by_symbol[sym].append(t)
    
    total_pnl = 0
    total_net = 0
    total_fees = 0
    for sym in sorted(by_symbol.keys()):
        r = calculate_pnl(by_symbol[sym])
        if r['trades'] > 0:
            print(f"{sym:<14} {r['trades']:<6} ${r['pnl']:<8} ${r['fees']:<8} ${r['net_pnl']:<8} {r['win_rate']:<7}% ${r['avg_pnl_per_trade']:<8}")
            total_pnl += r['pnl']
            total_net += r['net_pnl']
            total_fees += r['fees']
    
    print(f"{'─'*60}")
    print(f"{'合计':<14} {len(all_futures_trades):<6} ${total_pnl:<8.2f} ${total_fees:<8.2f} ${total_net:<8.2f}")
    
    # === 按方向分析 ===
    print(f"\n{'─'*60}")
    print("📊 按方向")
    print(f"{'─'*60}")
    buy_trades = [t for t in all_futures_trades if t.get('side') == 'BUY']
    sell_trades = [t for t in all_futures_trades if t.get('side') == 'SELL']
    
    print(f"  {'买入':<10} {len(buy_trades):<8}笔 PnL=${sum(float(t.get('realizedPnl',0)) for t in buy_trades):.2f}")
    print(f"  {'卖出':<10} {len(sell_trades):<8}笔 PnL=${sum(float(t.get('realizedPnl',0)) for t in sell_trades):.2f}")
    
    # === 整体指标 ===
    print(f"\n{'─'*60}")
    print("🏆 整体指标")
    print(f"{'─'*60}")
    
    overall = calculate_pnl(all_futures_trades)
    for k in ['trades','pnl','fees','net_pnl','win_rate','profit_factor','sharpe','avg_pnl_per_trade']:
        print(f"  {k:20s}: {overall[k]}")
    
    # === 按天分析 ===
    print(f"\n{'─'*60}")
    print("📅 按天")
    print(f"{'─'*60}")
    
    by_day = defaultdict(list)
    for t in all_futures_trades:
        day = datetime.fromtimestamp(t.get('time', 0)/1000, tz=timezone.utc).strftime('%m-%d')
        by_day[day].append(t)
    
    for day in sorted(by_day.keys()):
        r = calculate_pnl(by_day[day])
        print(f"  {day}: {r['trades']:3d}笔  PnL=${r['pnl']:>+8.2f}  净=${r['net_pnl']:>+8.2f}  胜率={r['win_rate']}%")
    
    # === 现货分析 ===
    if all_spot_trades:
        print(f"\n{'─'*60}")
        print("💱 现货交易")
        print(f"{'─'*60}")
        by_spot_sym = defaultdict(list)
        for t in all_spot_trades:
            sym = t.get('symbol', '?')
            by_spot_sym[sym].append(t)
        
        for sym in sorted(by_spot_sym.keys()):
            trades = by_spot_sym[sym]
            cost = sum(float(t.get('quoteQty', 0)) for t in trades if t.get('isBuyer'))
            qty = sum(float(t.get('qty', 0)) for t in trades)
            print(f"  {sym}: {len(trades):3d}笔  买入{sum(1 for t in trades if t.get('isBuyer')):3d}笔/卖出{sum(1 for t in trades if not t.get('isBuyer')):3d}笔  总金额=${cost:.2f}")
    
    # === 总结 ===
    print(f"\n{'='*60}")
    print("📋 5月总账")
    print(f"{'='*60}")
    print(f"  合约交易: {len(all_futures_trades)}笔")
    print(f"  总PnL: ${overall['pnl']:.2f}")
    print(f"  手续费: ${overall['fees']:.2f}")
    print(f"  净盈亏: ${overall['net_pnl']:.2f}")
    print(f"  胜率: {overall['win_rate']}%")
    print(f"  夏普比率: {overall['sharpe']}")
    print(f"  利润因子: {overall['profit_factor']}")
    print(f"  综合评分: {overall['sharpe'] * overall['win_rate']/100 * overall['profit_factor']:.2f}")
    
    return overall

if __name__ == '__main__':
    analyze()
