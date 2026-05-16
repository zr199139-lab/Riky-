# 自终止孤儿Cron模式 — 2026-05-09 真实案例

## 问题

`TREND-CONS实盘机器人` (cron `54c7564ec5bc`) 每小时检查一次 `trend_cons_real.py`。自2026-04-27起脚本文件已不存在，但cron仍然活跃，持续产生`[SILENT]`输出，每周烧~7次API调用。

已有「连续7次[SILENT]后自终止」的文档规则（2026-05-08记录），但**从未被实现为可执行逻辑**。cron系统不会自动执行文档中的规则——规则只在有人巡检时才能发现。

## 关键发现

**死Cron的检测分级：**

| 级别 | 描述 | 行动 |
|:----|:-----|:----|
| **LEVEL 1: 进程死亡** | 脚本文件存在，进程不存在 | 重启Bot |
| **LEVEL 2: 脚本死亡** | 脚本文件不存在，cron仍在运行 | 删除cron |
| **LEVEL 3: 自检死亡** | 巡检cron本身持续报告[SILENT] | 巡检cron应自行终止 |

## 根治方案

**所有检查性的cron任务在prompt中应包含自保逻辑：**

> "如果此cron持续3+次检查到脚本文件不存在，则执行 `hermes cronjob remove <自身job_id>` 终止自己。"

## 实际操作

2026-05-09 05:01 — 本次巡检发现死Cron `54c7564ec5bc`，已执行：
```bash
hermes cronjob remove 54c7564ec5bc
```

## 检查清单（部署cron前的三步验证）

1. [ ] 脚本文件是否存在？
2. [ ] cron引用的路径是否正确？
3. [ ] cron是否包含自终止条件？（脚本不存在→删除自己）

## 相关文件

- `bot-lifecycle-management/SKILL.md` — 死Cron管理流程
- `bot-lifecycle-management/scripts/dead-cron-detector.py` — 自动化死cron检测脚本
- `bot-lifecycle-management/references/trend-cons-real-never-deployed-2026-05-08.md` — 原始案例记录
