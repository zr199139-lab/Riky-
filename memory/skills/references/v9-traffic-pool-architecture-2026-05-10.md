# V9 流量池架构详细设计

## ByteDance模型在交易中的应用

字节跳动的流量池算法: 内容发布→小流量池(200-500曝光)→数据反馈→表现好→更大流量池→达标晋级→精品池。

**映射到交易**: 全量币种→规则过滤(池1)→AI精选(池2)→AI开仓决策(池3)→AI持仓监控(池4)

每层都有严格的KPI, 不达标淘汰, 达标晋级。

## 数据流

```
每10秒:
  t=0:  获取余额 + 费率排名
  t=0.5: 池1: 遍历前30个费率币, 获取K线, 计算ATR/趋势/量比
          → 输出Top 15候选 (过滤掉ATR>3% / 趋势反向 / 黑名单 / 已持仓)
  t=3:   池1完成 → 格式化15候选 + 当前持仓 → 1次DeepSeek调用
  t=6:   DeepSeek返回 → 解析JSON输出
          → 池4: 先关仓(释放保证金)
          → 池3: 后开仓(使用释放的保证金)
  t=7:   硬止损检查 + 总回撤检查 + 日亏检查
  t=8:   保存state + 日志
  t=10:  下一轮
```

## 单次AI调用的优势

**V8 (旧)**: 每个持仓每60秒独立AI调用。问题:
- AI不知道候选币情况 → 无法判断「换仓」是不是更好
- 各仓独立决策 → 可能同时开仓导致保证金不足
- 多个AI调用互相覆盖state → 数据不一致

**V9**: 1次AI调用包含所有信息 → AI可以看到:
- 15个候选币各自的费率/趋势/波动率
- 当前持仓的PnL/保证金/费率
- 余额和可用保证金

## AI输出格式

```json
{
  "pool2_top5": ["LAYER", "1000XEC", "GWEI", "ATA", "AIGENSYN"],
  "pool3_open": [
    {
      "symbol": "LAYER",
      "side": "sell",
      "margin": 15,
      "reason": "费率-2%+趋势向下"
    }
  ],
  "pool4_actions": {
    "ERA": "hold",
    "SNDK": "close"
  },
  "reasoning": "做空LAYER吃费率, ERA持有观察"
}
```

## 硬止损保护 (独立于AI)

```python
for sym in positions:
    pnl_pct = total_pnl / margin * 100
    if pnl_pct <= -6:  # 亏超6%保证金
        close_position(sym)  # 不看AI意见, 直接砍
```

## 动态日亏熔断

```python
if bal < 40:      daily_loss_limit = 2.5   # $2.5/天 封顶
elif bal < 60:    daily_loss_limit = 3.0
elif bal < 100:   daily_loss_limit = 6.0
else:             daily_loss_limit = 16.0
```

$29余额下日亏封顶$2.5 = 8.6%本金。一天最多亏$2.5, 之后熔断到第二天。
