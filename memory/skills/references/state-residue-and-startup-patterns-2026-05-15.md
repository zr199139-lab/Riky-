# 状态文件残留污染 + 启动模式 + Binance交易溯源（2026-05-15）

## 状态文件残留污染

**血案**: GRID Bot V4重启后捡了旧state的17个脏币（FET/APT/AVAX…），全在尝试重建网格但资金不够→全部跳过→$574现金躺尸。

**根因**: 旧版Bot在不同advisory期间积累了17个币种的state记录。重启后Bot遍历state.grids → 所有脏币加入symbols_to_track → 全部重建但资金不足 → 全部跳过。

**修复步骤**:
1. `kill old_bot`
2. `rm -f state_file.json`
3. `start new_bot`

**铁律**:
1. 杀Bot重启前必须清state（除非有正执行中的订单需保留）
2. 不清state = 100%污染——旧advisory的17个币种与新空间的$402可用现金不匹配
3. `grep STATE_FILE bot.py`找到真实路径，所有版本全清
4. 重启后验证state只包含advisory buy列表中的币种

## screen -dmS 启动模式

**问题**: Hermes的`terminal(background=true)`不支持shell重定向，nohup被前端拦截。

**正确方案**:
```bash
screen -dmS <session_name> bash -c "cd /path/to/autonomous && python3 -u bot.py > /path/to/logfile.log 2>&1"
```

**验证**: `ps aux | grep bot.py | grep -v grep | grep -v SCREEN`

## Binance API交易溯源

**场景**: 查"谁/什么时候/为什么"平了一个仓。

**流程**:
1. `GET /fapi/v1/allOrders?symbol=DOTUSDT&limit=10` — 找最后一条SELL MARKET（数量=开仓量）
2. `grep "DOT" hv_bot.log` → 找`reason=AI_CLOSE`
3. 交叉验证Bina的时间戳 vs Bot日志时间戳

**铁律**: Binance时间戳是唯一可信平仓时序，Bot日志可能丢行。
