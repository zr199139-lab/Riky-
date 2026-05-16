---
name: paper-batch-restart-workflow
description: 虚拟盘批量重启+强制建仓+检视流程 — 用户要求所有虚拟盘"立马建仓，今早给我数据"
version: 1.1
author: DS-0
created: 2026-05-09
updated: 2026-05-09
category: quantitative-trading
tags:
  - paper-trading
  - batch
  - deployment
  - force-entry
  - restart
---

# 虚拟盘批量重启工作流

## 触发条件
用户说以下任意一句话时立即执行：
- "全都重启"
- "虚拟盘跑一下"
- "今早给我数据"
- "全都建仓"
- "启动虚拟盘"

## 执行流程

### 步骤1: 扫描所有虚拟盘文件
```bash
# 只扫 v3.3_* 核心虚拟盘（10个），不扫废弃的 paper_1000u
ls autonomous/v3.3*虚拟盘*.py autonomous/v3_3*虚拟盘*.py 2>/dev/null
```

### 步骤2: 强制建仓（可选，按需）
在虚拟盘主循环之前插入强制买入（85%资金），确保在窄震行情中也能立即有持仓测试。

### 步骤3: 并行启动所有虚拟盘（🔥 必须用 terminal(background=True)）

**不要用 nohup bash for-loop！** 在Hermes环境中 nohup 循环启动经常无声失败（0进程存活）。
正确方式：逐个调用 `terminal(background=True)`，每个间隔2秒：

```python
# Hermes内部执行（非shell脚本）
from hermes_tools import terminal
import time

scripts = [
    "v3.3_网格_虚拟盘.py",
    "v3.3_趋势跟踪_虚拟盘.py",
    "v3.3_MACD_RSI_虚拟盘.py",
    "v3.3_多策略_虚拟盘.py",
    "v3.3_替代策略_虚拟盘.py",
    "v3.3_DE_Shaw_EMA_v2_虚拟盘.py",
    "v3.3_grid_scalp_combo_虚拟盘.py",
    "v3.3_信息差_趋势检测_虚拟盘.py",
    "v3.3_TopTrader跟单_虚拟盘.py",
    "v3_3_合约31pct_虚拟盘.py",
]

for name in scripts:
    terminal(
        f"cd ~/.hermes/mempalace/quant_trading && "
        f"python3 -u autonomous/{name} 2>&1 | tee bot_logs/{name.replace('.py', '.log')}",
        background=True
    )
    time.sleep(2)
```

**关键：只启动10个v3.3核心虚拟盘，不要启动全部32个paper_1000u脚本**（那些是独立实验脚本，多数已废弃）。

### 步骤4: 验证 + 错误恢复

```bash
# 验证存活数
ps aux | grep "python3.*虚拟盘" | grep -v grep | awk '{print $NF}' | sort

# 目标：10个唯一策略名
# 如果少于10，逐个检查日志找崩溃原因
```

**错误恢复标准流程：**
1. 读日志尾15行 → 定位崩溃原因（通常 NameError / ImportError / API限流）
2. patch修复 → 重新 `terminal(background=True)` 启动该单个策略
3. 5秒后验证PID存活

### 步骤5: 验证所有进程存活
```bash
# 唯一策略计数（去重）
ps aux | grep "python3.*虚拟盘.*\.py" | grep -v grep | awk '{print $NF}' | sort -u | wc -l
# 预期：10

# 检查0字节日志
find bot_logs/ -name "v3.3*虚拟盘*.log" -size 0
```

## 常见崩溃模式 & 修复模板

### NameError: 未定义变量
```
NameError: name 'MAX_POSITIONS' is not defined
```
**修复**: 用 `patch` 将变量替换为硬编码默认值，然后 `terminal(background=True)` 重新启动该单个策略。

### ImportError / 模块缺失
检查代码顶部的 import 行，用 pip install 补装后重新启动。

### API限流 (HTTP 429)
等待30秒后重新启动该单个策略，其他不受影响的策略保持运行。

## 坑

1. **nohup 循环启动不可靠** — Hermes环境中 `nohup ... &` 的for循环经常无声失败（0进程存活）。必须用 `terminal(background=True)` 逐个启动
2. **策略启动崩溃静默** — 脚本可能因未定义变量（如 `MAX_POSITIONS`）直接崩溃，日志只有traceback。措施：启动后3-5秒检查PID存活
3. **只启动核心v3.3虚拟盘（10个）** — autonomous/ 下有32个paper_*脚本，多数是废弃实验。只启动这10个：v3.3_网格/趋势跟踪/MACD_RSI/多策略/替代策略/DE_Shaw_EMA_v2/grid_scalp_combo/信息差/TopTrader跟单/合约31pct
4. **多策略虚拟盘用共享引擎** — 先检查有没有 `PaperEngine` 类或共享state文件，避免双写冲突
5. **0字节日志≠进程死亡** — 先查PID再查日志内容
6. **有些虚拟盘的日志文件名不匹配** — 代码内写的是 `{name}_1000u.log`，tee重定向是 `{name}_虚拟盘.log`
7. **Hermes后台进程可能在会话切换时消失** — 持续运行必须用 terminal(background=True)
8. **启动间隔至少2秒** — 币安API 1200次/分钟限制，10个虚拟盘同时冷启动会触发限流
