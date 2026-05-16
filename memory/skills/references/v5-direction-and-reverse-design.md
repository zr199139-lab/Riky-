# V5完整设计记录（2026-05-09）

## 一句话总结

> 半仓止盈是骗子，手续费吃掉一切。固定%止损不如ATR动态。方向判断不看费率符号看EMA交叉。缺仓自动补。

## 核心改动（V4→V5）

| 项目 | V4 | V5 |
|------|-------|-------|
| 趋势判断 | 4h/24h价格变化 | **EMA5/20 1分钟K线交叉** |
| 止损 | 固定-8%(价格2.67%) | **2xATR动态止损** |
| 止盈 | 固定+5% | **EMA5/20交叉反转止盈** |
| 检查周期 | 3分钟 | 3分钟（读1m K线判断，不操作） |
| 补仓 | 等下一周期 | **不满5仓自动补** |
| 反手 | 无 | **EMA交叉确认趋势反转才反手（不是止损就反手）** |
| 波动率过滤 | ATR>0.5%跳过 | **ATR定仓位（不跳过）** |
| 半仓止盈 | 无 | **无（全量平仓）** |

## 1分钟检查周期的成本分析

从币安拉2000根1m K线回测：

| 策略 | 交易 | 胜率 | 手续费 | vs基准 |
|------|------|------|--------|--------|
| A: 3分钟检查（基准） | 3 | 100% | $0.048 | 1x |
| B: 1分钟检查+反手2x | 5 | 40% | $0.208 | 4.3x |
| C: 紧止损+反手 | 12 | 42% | $0.480 | 10x |

**结论**: 1分钟检查增加4倍手续费但胜率从100%→40%
**1分钟K线的正确用法**：读趋势不操作。开平仓只在EMA交叉确认后执行。

## 反手策略的设计选择

### 止损就反手（已否决）
价格触发止损→立刻反方向开仓。纯价格触发，无趋势确认。
- 优点：反应快，不错过行情
- 缺点：假突破时双倍亏损（止损亏+反手亏）

### EMA确认趋势反转再反手（V5采用）
```python
# 止损触发（价格反向2xATR）
if pnl <= -SL_PCT:
    close_position()  # 先平仓止损
    # 不立即反手！等EMA交叉确认
    
# 下一周期检查：
if ema5_crossed_below_ema20:  # 死叉确认
    open_position(side='sell')  # 确认空头趋势再反手
```

- 优点：确定性高，减少假信号
- 缺点：可能滞后错过开头行情

## ATR动态止损公式

```python
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0

# 开仓时计算
if side == "buy":
    stop_price = entry_price * (1 - atr_pct * ATR_STOP_MULTIPLIER)
else:
    stop_price = entry_price * (1 + atr_pct * ATR_STOP_MULTIPLIER)

# 每周期更新（跟随市场波动）
atr_pct = calc_atr(ohlcv_1m)
if side == "buy":
    pos["stop_price"] = cur_price * (1 - atr_pct * ATR_STOP_MULTIPLIER)
else:
    pos["stop_price"] = cur_price * (1 + atr_pct * ATR_STOP_MULTIPLIER)
```

ACE ATR=3.9% → 止损在7.8%价格反向，而不是固定2.67%

## EMA反转止盈（代替固定%止盈）

```python
# 每周期检查EMA5/20交叉
calc_indicators(ohlcv_1m)  # 返回: trend_dir, cross_up, cross_down

# 做多时趋势变空→平仓
if pos["side"] == "buy" and trend_dir == 'down' and cross_down:
    close_position()  # EMA反转止盈，不是固定%

# 做空时趋势变多→平仓
if pos["side"] == "sell" and trend_dir == 'up' and cross_up:
    close_position()
```

今天ACE做空方向完全正确（0.1663→0.1537，-7.8%），如果用了V5：
- 不会在17:11半仓止盈触发（减少手续费）
- 不会在17:17再次半仓（仓位不会被切成0.69个）
- EMA5/20一直向下没反转 → 一直持有到收盘 → 总PnL = $1.10×3x杠杆 = $3.3+
- 对比实际：15次半仓 + 手续费$0.36 + 最后仓位只剩0.69个 → 实际PnL ≈ $0.50

## 不满5仓自动补仓

```python
# 每个结算周期(4h)结束时
if len(positions) < MAX_POSITIONS and not daily_loss_stop:
    # 按趋势强度降序选币
    sorted_analysis = sorted(analysis, key=lambda a: abs(a.trend_score), reverse=True)
    for a in sorted_analysis:
        if len(positions) >= MAX_POSITIONS: break
        if a.symbol in positions: continue
        
        margin = min(a.margin, balance - reserved - 10)
        if margin >= 5:
            pos = open_position(a.symbol, a.side, margin, ...)
            if pos: 
                positions[a.symbol] = pos
                reserved += pos.margin
```

## 方向判断正确率（三次回测对比）

| 回测 | 方法 | 交易数 | 正确率 |
|:----|:----|:-----:|:-----:|
| 费率符号 | 模拟200次历史 | 192 | **39.6%** |
| 费率符号 | 今日15笔实盘 | 10 | **50%** |
| EMA5/20交叉 | 1m K线(无前视) | ~5 | **~60%**(样本小) |
| 反手策略B | 1m检查+反手2x | 5 | **40%** |
| 反手策略C | 紧止损+反手 | 12 | **42%** |

## 用户明确要求的行为模式（必须遵守）

1. **不要主动汇报中间状态** — 用户要求后端运行。只汇报结果，不推过程。除非致命错误或需要决策，否则沉默执行。
2. **不要给选择题** — 用户原话「我需要你主动思考，而不是我教你怎么做」。带数据+方案直接执行，不给A/B选项。
3. **一切以数据说话** — 任何声称的PnL/收益必须有链上验证或回测数据支撑。用户对数据造假零容忍。
4. **直接下结论，不反复问** — 当需要决策时，自己查数据做判断。用户原话「自己查数据做判断，分析不要一直问我」。
5. **代码质量铁律**：语法检查必须通过再上线。启动前要先杀旧进程。PID互斥。

## 当前市场诊断（2026-05-09）

币安USDT合约市场的费率套利现状：
- 主流币（BTC/ETH/DOGE/SOL）= 月化费率 < 1%，无肉可吃
- 美股代币（QQQ/MU/NVDA/MSFT/QCOM/AMD）= 仓位限额code -2027
- 高波山寨币（ACE/SIREN/DEEP/JTO）= 月化5-7%但ATR 3-6%，费波比≈1-2，勉强
- 结论：当前市场不适合费率套利，V5空仓等待是正确状态
