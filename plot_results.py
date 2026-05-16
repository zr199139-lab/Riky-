#!/usr/bin/env python3
"""生成策略对比图"""
import json, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

with open('/tmp/backtest_results.json') as f:
    data = json.load(f)

# 按策略类别汇总
categories = {}
for r in data:
    name = r['策略'].split('@')[0]
    if name not in categories: categories[name] = []
    categories[name].append(r)

# 排序
avg_sorted = sorted(categories.items(), key=lambda x: np.mean([r['总PnL'] for r in x[1]]), reverse=True)

fig, ax = plt.subplots(figsize=(14, 8))

colors = {'波动率回归': '#00ff88', 'RSI均值回归': '#00ccff', '31%Combo': '#ffaa00',
          'FreqRSIMA': '#ff6600', '配对套利': '#888888', 'MACD趋势': '#ff4466',
          '海龟趋势': '#cc44ff', 'MACD+RSI': '#ff3388', 'ATRChannel': '#993300'}
          
labels = {'波动率回归':'OURS·MeanRev', 'RSI均值回归':'OURS·RSI_MeanRev', '31%Combo':'OURS·31%Combo',
          'FreqRSIMA':'freqtrade·RSI_MA', '配对套利':'OURS·PairsArb', 'MACD趋势':'OURS·MACD_Trend',
          '海龟趋势':'OURS·Turtle', 'MACD+RSI':'OURS·MACD_RSI', 'ATRChannel':'vnpy·ATR_Channel'}

x_pos = []
x_labels = []
colors_list = []
pnls_list = []
avgs = []

idx = 0
for name, results in avg_sorted:
    pnls = [r['总PnL'] for r in results]
    avg = np.mean(pnls)
    avgs.append(avg)
    
    for j, p in enumerate(pnls):
        x_pos.append(idx + j * 0.15)
        pnls_list.append(p)
        colors_list.append(colors.get(name, '#666666'))
    
    x_labels.append((idx + (len(pnls)-1)*0.075, labels.get(name, name)))
    idx += 1.5

bars = ax.bar(x_pos, pnls_list, width=0.12, color=colors_list, alpha=0.85)

# 加平均线
for i, (name, results) in enumerate(avg_sorted):
    avg = np.mean([r['总PnL'] for r in results])
    x_center = i * 1.5 + (len(results)-1)*0.075
    ax.axhline(y=avg, xmin=(x_center-0.4)/x_pos[-1]*0.9, xmax=(x_center+0.4)/x_pos[-1]*0.9, 
               color=colors.get(name,'#666'), linewidth=2, linestyle='--', alpha=0.5)
    ax.text(x_center, avg + 1.5, f'${avg:.1f}', ha='center', fontsize=9, fontweight='bold',
            color=colors.get(name,'#666'))

# 零线
ax.axhline(y=0, color='white', linewidth=1, alpha=0.3)

ax.set_ylabel('PnL ($)', fontsize=12, color='white')
ax.set_title('DS-O Strategy vs Institution · May Backtest ($1,000 virtual)', fontsize=14, fontweight='bold', color='white')
ax.set_xticks([x[0] for x in x_labels])
ax.set_xticklabels([x[1] for x in x_labels], rotation=30, ha='right', fontsize=10, color='white')
ax.set_facecolor('#1a1a2e')
fig.patch.set_facecolor('#1a1a2e')
ax.tick_params(colors='white')
ax.spines['bottom'].set_color('#333')
ax.spines['top'].set_color('#333')
ax.spines['left'].set_color('#333')
ax.spines['right'].set_color('#333')
ax.grid(axis='y', alpha=0.15, color='white')

plt.tight_layout()
plt.savefig('/tmp/strategy_compare.png', dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
print('Saved: /tmp/strategy_compare.png')
