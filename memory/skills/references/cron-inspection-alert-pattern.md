# Cron巡检告警模式 — 结构化JSON告警文件

## 场景

cron定时任务执行巡检脚本（如 `agent_spot.py`），发现异常（MA20跌破、接近止损、网格状态异常等）时，需要：
1. 写入结构化告警文件供后续查询
2. 响应 `[SILENT]` 不推送消息（零打扰模式）

## 标准告警文件结构

```json
{
  "timestamp": "2026-05-10 14:56:56",
  "type": "MA20_BREAK",
  "severity": "WARNING",
  "details": {
    "DOGE": {
      "price": 0.1084,
      "ma20": 0.1089,
      "offset_pct": -0.49,
      "status": "跌破MA20",
      "duration_cycles": 3
    }
  },
  "grid_positions": {
    "SOL/USDT": {"layers": 1, "unrealized_pnl": 0.21}
  },
  "near_sl_layers": [],
  "xiaoma_regime": "ranging",
  "xiaoma_grid_pause": false,
  "xiaoma_arb_pause": false
}
```

## 关键字段说明

| 字段 | 含义 | 示例 |
|------|------|------|
| `type` | 告警分类 | `MA20_BREAK`, `NEAR_SL`, `GRID_ERROR` |
| `severity` | 严重等级 | `WARNING`, `CRITICAL` |
| `duration_cycles` | 该异常持续巡检次数 | 如连续3次都跌破MA20=3 |
| `near_sl_layers` | 接近止损的网格层列表 | `["DOGE L1: -1.8%"]` |
| `xiaoma_regime` | 小马当前周期判断 | `ranging`, `bull`, `bear` |

## 写入方式

```python
import json, os

ALERT_FILE = os.path.join(LOG_DIR, "agent_spot_alert.json")

alert = {
    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    "type": "MA20_BREAK",
    "severity": "WARNING",
    "details": {...},
    "grid_positions": {...},
    "near_sl_layers": [...],
    "xiaoma_regime": regime,
    "xiaoma_grid_pause": grid_pause,
    "xiaoma_arb_pause": arb_pause,
}

with open(ALERT_FILE, "w") as f:
    json.dump(alert, f, indent=2)
```

## 持久性跟踪

`duration_cycles` 参数记录异常持续次数。实现方式：

```python
# 读取上次告警文件
prev = {}
if os.path.exists(ALERT_FILE):
    prev = json.load(open(ALERT_FILE))

# 如果同类型告警继续存在，递增
if prev.get("type") == current_type and prev.get("details", {}).get("DOGE", {}).get("status") == current_status:
    duration = prev.get("duration_cycles", 0) + 1
else:
    duration = 1
```

## 使用规则

- 只有 **首次出现** 或 **严重等级升级**（WARNING→CRITICAL）时考虑推送消息
- 持续存在的相同告警 → 只更新文件，响应 `[SILENT]`
- 如果所有指标恢复正常 → 删除告警文件或写入 `"severity": "CLEARED"` 版本
