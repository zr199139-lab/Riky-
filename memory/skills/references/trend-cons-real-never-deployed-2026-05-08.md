# TREND-CONS 实盘机器人 — 从未部署的脚本 + 持续死Cron模式

## 概要

`trend_cons_real.py` 是一个**从未被成功部署**的量化Bot脚本（趋势跟随一致性策略实盘版本）。自2026-04-27起脚本文件就完全不存在于文件系统中，无进程、无日志、无状态文件。

对应的cron任务 `54c7564ec5bc` 每小时检查一次，持续产生 `[SILENT]` 输出——该状态已持续10+次cron周期。

## 演化的启示

| 日期 | 事件 | 启示 |
|:---|:-----|:-----|
| 2026-04-26 01:00 | 最后一次Bot活跃（空仓，资金$528.51） | Bot曾短暂运行 |
| 2026-04-27 ~03:00 | 脚本文件被删除/清理 | 清理操作未同步取消cron |
| 2026-04-27 ~10:00 | cron首次报告「脚本不存在」 | 持续报告模式开始 |
| 2026-04-27 → 2026-05-08 | 10+次cron运行，每次报告[SILENT] | 无用API调用累积 |
| **2026-05-08** | 加入自终止规则：连续7次[SILENT]后删除cron | 防止此类资源浪费 |

## 技术特征

- **脚本路径**：`/home/admin/.hermes/mempalace/quant_trading/trend_cons_real.py` (不存在)
- **进程检查命令**：`ps aux | grep trend_cons_real.py`
- **cron job ID**：`54c7564ec5bc`
- **频率**：每小时（`0 * * * *`）
- **预期运行环境**：`/home/admin/.hermes/mempalace/quant_trading/bt_venv/bin/python3`
- **脚本特征**：含无限循环，不适合直接cron运行

## 根因分析

最可能的场景链：
1. 用户曾尝试创建`trend_cons_real.py` 但从未完成（文件为空/从未写入）
2. 用户创建了cron检查任务以监控Bot状态
3. 后续文件被系统清理（mtime检查误删）或用户手动删除
4. cron残留——从未被移除
5. 因`[SILENT]`机制，系统每周烧~7次API调用而不产生任何价值

## 教训

1. **创建cron时设定过期条件**：如「如果连续3次检查脚本文件不存在→删除此cron」
2. **脚本删除前先搜索cron引用**：删除.py文件应先检查`hermes cronjob list`是否有引用
3. **「从未部署」≠「已崩溃」**：两者在巡检中应区别处理（前者终止cron，后者恢复流程）

## 相关文件

- `bot-lifecycle-management/SKILL.md` — "死Cron任务—脚本从未部署 vs 被删除" + 自终止规则
- `system-recovery-workflow/references/cron-stale-state-silent-pattern.md` — [SILENT]模式协议
- `bot-lifecycle-management/scripts/dead-cron-detector.py` — 自动化死cron检测

2026-05-08 · DS-0 暗黑星火主宰
