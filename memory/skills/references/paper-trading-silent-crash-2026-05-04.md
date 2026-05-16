# Paper Trading Engine 无声崩溃 — 2026-05-04

## 事件概述

- **时间**：2026-05-04 10:22 左右（日志最后时间戳 10:22:07）
- **Bot**：`paper_trading_engine.py`（Polymarket 虚拟盘引擎 v2.0）
- **发现时间**：2026-05-04 10:44（cron Bot健康检查）
- **死亡模式**：无声崩溃 — 无Python异常堆栈、无日志错误、进程从ps列表消失
- **最大停机时间**：约22分钟
- **恢复方式**：自动重启（隶属6Bot定期健康检查）

## 日志分析

最后50行日志全部为重复性 WARN：
```
[WARN] ⚠️ Data API leaderboard 已废弃，尝试从链上缓存恢复...
[WARN] ⚠️ 使用硬编码鲸鱼回退 (2个)，后续由链上交易动态发现
[INFO] 🐋 总加载 2 鲸鱼
```
从 09:01 到 10:22，每5分钟重复一次，共重复~16轮。日志在 10:22:07 后**直接结束**，无任何异常输出。

## 根因推断

**最可能原因**：OOM killer（内存超限被杀）

依据：
1. 无Python异常堆栈 — 排除Python层面崩溃
2. 进程从ps列表消失 — 排除僵尸进程模式
3. 日志无ERROR/WARN/CRITICAL — 排除看门狗SIGTERM（SIGTERM有signal handler输出）
4. 系统内存压力 — 当时运行6个Bot + cron任务，8GB内存可能不足
5. 之前同模式发生过：老日志在 08:16 同样无声结束（bot_logs/paper_trading.log 版本）

```bash
# OOM确认命令（运维时可用）
dmesg | grep -i oom | tail -10
dmesg | grep -i "paper_trading" | tail -5
```

## 双日志文件问题

此Bot存在**双日志文件**的部署配置问题：

| 文件 | 来源 | 写入者 |
|------|------|--------|
| `paper_trading.log` | 脚本内部 `log()` 函数 | 脚本自身 (第15行: `LOG_FILE`) |
| `bot_logs/paper_trading.log` | shell stdout重定向 `> bot_logs/paper_trading.log 2>&1` | 脚本的 `print()` / stderr |

当通过shell wrapper启动时（旧模式），两个日志都被写入。
当通过 `terminal(background=true)` 启动时（新模式），**只有** `paper_trading.log` 被写入。

**诊断陷阱**：检查 `bot_logs/paper_trading.log` 的mtime可能产生误判，因为新模式不写此文件。

**建议**：统一为单一日志路径，或让shell wrapper输出到 `/dev/null` 而非 `bot_logs/`。

## 重启后的验证

1. 启动命令（Hermes环境）：
   ```
   terminal(background=true, command="cd ~/.hermes/mempalace/quant_trading && /home/admin/.hermes/hermes-agent/venv/bin/python3 paper_trading_engine.py")
   ```

2. 验证日志最新时间戳：
   ```
   tail -5 ~/.hermes/mempalace/quant_trading/paper_trading.log
   ```

3. 验证进程存活：
   ```
   ps aux | grep paper_trading_engine | grep -v grep
   ```

4. 正常启动日志示例：
   ```
   [10:44:04] 🚀 虚拟盘引擎启动 - 24小时运行中...
   [10:44:04] 🐋 总加载 2 鲸鱼
   [10:44:04] 📊 快照: 0.0h | 资金=$500.0 | 持仓=$0 | 平仓=0 | 胜率=0.0% | PnL=$0
   ```

## 已知无声崩溃Bot状态表（更新）

| Bot | 脚本 | 累计崩溃次数 | 最新一次 | 上次恢复后运行时长 | 最可能根因 |
|-----|------|:-----------:|:--------:|:-----------------:|:---------:|
| 虚拟盘引擎 | `paper_trading_engine.py` | 2+次 | **2026-05-04 10:22** | ~1h (09:01→10:22) | OOM killer |
| 情报Bot | `dark_spark_intel.py` | 3+次 | 2026-05-02 | — | 疑似systemd超时误杀 |
| 鲸鱼监控 | `whale_hedge_bot_v2.py` | 2+次 | 2026-05-02 | — | 版本升级后监控未同步 |
| 网格Bot(PYTHONPATH) | `dark_spark_grid.py` | 1次 | 2026-05-03 | — | 看门狗未设PYTHONPATH |
