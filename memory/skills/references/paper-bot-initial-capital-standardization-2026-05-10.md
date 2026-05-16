# 虚拟盘初始资金标准化记录 (2026-05-10)

## 触发
用户一句「所有虚拟盘启动金1000u」—— 6个并行虚拟盘需要统一初始资金。

## 当前虚拟盘清单（统一$1,000后）

| 虚拟盘 | 文件 | 启动金 | PID | 状态 |
|--------|------|:------:|:---:|:----:|
| 方向狙击 | v3.3_方向狙击虚拟盘.py | $1,000 ✅ | 3215367 | 🟢 BTC多 @$80,913 |
| MACD+RSI 4币 | v3.3_macd_rsi_4coin.py | $1,000 ✅ | 未运行 | ⏸ 待重启 |
| MACD+RSI 单币 | v3.3_MACD_RSI_虚拟盘.py | $1,000 ✅ **刚修复** | 3219752 | 🟢 空仓 |
| 趋势跟踪 | v3.3_趋势跟踪_虚拟盘.py | $1,000 ✅ **刚修复** | 3219733 | 🟢 BTC/ETH双多 |
| 替代策略 | v3.3_替代策略_虚拟盘.py | $1,000 ✅ (E+F各$500) | 3185983 | 🟢 运行中 |
| 信息差 | v3.3_信息差_趋势检测_虚拟盘.py | $1,000 ✅ | 3215309 | 🟢 运行中 |

## 修复记录

### 需要修复的2个Bot

| Bot | 旧值 | 新值 | 原因 |
|-----|:----:|:----:|:----:|
| 趋势跟踪_虚拟盘 | $500 | $1,000 | INITIAL_CAPITAL硬编码500 |
| MACD_RSI_虚拟盘(单币) | $500 | $1,000 | INITIAL_CAPITAL硬编码500 |

### 修复步骤（每个Bot）

1. `sed -i 's/INITIAL_CAPITAL = 500.0/INITIAL_CAPITAL = 1000.0/' v3.3_*_虚拟盘.py`
2. `kill <PID>` — 杀旧进程
3. 更新state.json：`cash=1000.0, initial_capital=1000.0, positions={}`
4. `terminal(background=true)` 重启

### 关键教训

**只改代码不够。** state.json中的cash字段必须在重启前更新，否则新进程从state读出旧值，覆盖新代码的INITIAL_CAPITAL。

```python
# 恢复逻辑中的典型代码：
self.engine.cash = s.get("cash", INITIAL_CAPITAL)  # state优先于代码
```

### 验证方法

重启后检查日志首行：
```
📈 权益=$1000.00 | PnL=$+0.00 | 现金=$1000.00 | 持仓=0/3  ✅
```

或检查state文件：
```bash
python3 -c "import json; d=json.load(open('bot_logs/trend_tracker_state.json')); print(f'cash=${d[\"cash\"]}')"
```
