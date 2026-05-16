# 监控脚本Bot清单漂移 — 2026-05-05

## 事件

Agent5风险检查脚本报告「3/5进程在线」，但实际所有6个Bot进程全部正常运行。问题：Agent5的检查清单指向已黑名单停用的旧Bot，遗漏新部署的Bot。

## 漂移链

```
2026-05-01: agent5_risk.py 创建, procs=[dark_spark_grid, whale_hedge, ...]
2026-05-04: dark_spark_grid→网-多币现货V3（Bot替换）
2026-05-05: agent5_risk.py 未同步更新
            → 仍检查 grid_multi_bot(停用), dark_spark_intel(停用)
            → 遗漏 网-多币现货V3(新), 合-猎豹V14-模拟(新)
            → 报告3/5 但实际6/6正常
```

## 根因

每次Bot替换/新增时，需手动更新`agent5_risk.py`和`agent6_deploy.py`中的procs列表。文档已充分记载此要求，但人为疏忽导致遗漏。

## 解决方案

**自动同步脚本**（推荐）：将Bot清单抽象为单一源，agent5/agent6从此源读取：

```python
# ~/.hermes/scripts/bot_registry.py (单一真相源)
REGISTERED_BOTS = {
    "鲸-跟单LIVE":    {"cmd": "whale_hedge",      "type": "live"},
    "鲸-跟单模拟":    {"cmd": "paper_whale",       "type": "paper"},
    "网-多币现货V3":  {"cmd": "网-多币现货V3",     "type": "live"},
    "合-MACD12-模拟": {"cmd": "合-MACD12-模拟",    "type": "paper"},
    "合-猎豹V14-模拟":{"cmd": "合-猎豹V14-模拟",   "type": "paper"},
}

# agent5_risk.py
from bot_registry import REGISTERED_BOTS
procs = [v["cmd"] for v in REGISTERED_BOTS.values()]

# agent6_deploy.py
from bot_registry import REGISTERED_BOTS
# ... 同样使用单一源
```

**手动检查清单**（快速替代）：agent5/agent6每次运行时，自动检测预期进程名与实际进程名的差异：

```python
# 在agent5_risk.py末尾加一行
import subprocess
actual = subprocess.run(["ps", "aux"], capture_output=True, text=True)
for bot in procs:
    if bot not in actual.stdout:
        print(f"⚠️ 监控盲区: {bot} 不在实际进程列表中（可能已被改名/替换）")
```

## 教训

1. 文档记载的流程≠被执行的流程。需要自动化防护
2. Bot清单的单一真相源（single source of truth）能彻底消除漂移
3. 每次显示「N/M在线」且N≠M时，首先怀疑Agent5的检查清单是否过时
