# Riky-
<<<<<<< HEAD
# Riky-
=======
# ⚡ 暗黑星火计划 · Dark Spark Capital

> 从灰烬中重生，在混沌中建立秩序。

## 架构

```
暗黑星火V5 · 低频做空趋势系统
├── ds0_trader.py           # 交易执行引擎
├── stop_loss_multi.py      # 本地止损守护 (10s轮询)
├── hv_bot.py               # 合约执行Bot (Hedge模式)
├── ds0_analyst_v3.py       # 分析师引擎 (四模型)
├── emergency_*.py          # 紧急故障处理
│
├── dark_spark_capital_arch_*.md   # 树状架构文档
├── dark_spark_capital_tree_*.md   # 系统树形图
│
├── analysis/               # 交易数据分析
└── mempalace/              # 核心交易引擎
    └── autonomous/         # 自主交易Agent代码
```

## 核心理念

- **单进程PID锁** — 杜绝双进程互打
- **三币限定** — 只做BTC/ETH/SOL
- **做空趋势** — 熊市不做多
- **本地止损** — 亏$8硬止损，日亏$20熔断
- **低频交易** — ≤20笔/天，每笔间隔20分钟
- **0现货网格** — 永久停用（历史证明纯亏手续费）

## 提交规范

| 前缀 | 用途 |
|:----|:----|
| `[STRATEGY]` | 策略改动 |
| `[RISK]` | 风控规则 |
| `[BUGFIX]` | Bug修复 |
| `[DOC]` | 文档/树状架构 |
| `[REFACTOR]` | 代码重构 |
| `[INIT]` | 初始提交 |

## 记忆锚点

每次新会话启动时，DS-0执行：
```bash
git pull origin main        # 同步最新策略+代码
find . -name "*.md" -newer .git/HEAD | head -5  # 检查新文档
```

## 黑名单（永久封禁）
- AIGENSYN — 单币亏损$241，50x无止损
>>>>>>> 50edc84 ([INIT] 暗黑星火计划V5 · 完整代码库首次提交)
