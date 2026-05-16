# 动手前必做：系统状态验证三步法

## 背景

2026-05-15 用户连续纠正3次：
1. "你先看下整体架构" — 改了API Key但没先确认系统当前状态
2. "树状架构，你没了？看下记录" — 没检查架构文档就行动
3. "不对吧之前修改不是都合约V3.5了么" — 还在引用旧架构

## 三步检查法

在开始任何修改/新增/清理操作前，执行以下步骤：

### Step 1: 确认当前架构文档

```bash
# 找最新的架构文档
ls -lt /home/admin/.hermes/mempalace/quant_trading/autonomous/*architecture* 2>/dev/null
ls -lt /home/admin/.hermes/mempalace/quant_trading/autonomous/*contract_rules* 2>/dev/null
ls -lt /home/admin/.hermes/mempalace/quant_trading/autonomous/*tree* 2>/dev/null

# 读CHANGELOG最新条目
head -30 /home/admin/.hermes/mempalace/quant_trading/CHANGELOG.md
```

### Step 2: 确认哪些进程实际在运行

```bash
ps aux | grep -E "python3.*\.py" | grep -v grep | grep -v hermes
# 对比架构文档中描述的进程清单
```

特别检查：
- `ds0_analyst_v3.py` — 必须运行，AI决策大脑
- `hv_bot.py` — 可能已死，检查方式见 hv-bot-silent-death-detection
- `spot_bot.py` — 可能已死
- `risk_guardian.py` — 必须运行
- `darkspark_v3.py` — 必须已禁用(.DISABLED后缀)
- `/tmp/*.py` — 不应存在(违反树状架构)

### Step 3: 确认API Key配置

```bash
# 先查.env有什么
grep "API_KEY\|_KEY=" /home/admin/.hermes/.env | cut -d= -f1

# 再查config.yaml有什么
grep -n "api_key_env\|api_key:" /home/admin/.hermes/config.yaml | head -20

# 用户给新Key时：先在.env检查是否存在，再检查config.yaml引用
```

**铁律**: 用户说"你之前就有"时，必须先在.env搜索该Key，不要假设是新Key。

## 什么时候需要这套检查

| 触发条件 | 原因 |
|:--------|:-----|
| 用户给你新Key | 可能已经在.env里配好了，只是config.yaml硬编码没更新 |
| 用户说"看下整体架构" | 你未确认当前状态就动手了 |
| 用户说"我记不得了么" | 上次讨论的重要文档/配置你忘了 |
| 跨会话恢复工作时 | session恢复可能丢失上下文，必须重新检查实际状态 |

## 教训

每次写memory或改配置前，先用 terminal 验证文件系统实际状态。不要相信自己的记忆或之前的memory内容——文件系统才代表真实状态。
