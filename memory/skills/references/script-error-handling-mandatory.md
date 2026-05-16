---
name: script-error-handling-mandatory
description: 强制要求所有被Agent调用的Python脚本必须包含try-except异常处理——静默崩溃零容忍
version: 0.1.0
author: DS-0
created: 2026-05-01
updated: 2026-05-01
category: software-development
tags:
  - software-development
requires:
  bins:
    - python3
install:
  - kind: pip
    - python-dotenv
metadata:
  darkspark:
    skill_type: tool
    maturity: development
    risk_level: low
    requires_human_review: false
---

# 🛡️ 脚本异常处理强制规范

## 铁律

**所有被Agent调用的Python脚本或终端工具，必须包含 try-except 异常处理！**
即使工具内部报错或查不到数据，也必须输出一段错误信息，**绝对不允许静默崩溃或什么都不返回。**

## ⚠️ 关键教训（2026-05-01 重大更新）

**不要用 `print()` 向 stderr 输出错误！** 在后台进程（nohup/systemd）中，stderr 可能被缓冲或完全丢失，导致「无声崩溃」——BOT无声退出但日志文件无任何记录。

**始终用 `log()` 函数写入日志文件 + `traceback.format_exc()` 输出完整堆栈。**

**证据**：2026-05-01，Bot每2-4分钟无声退出，外层except用`print(f"...", file=sys.stderr)`输出，nohup重定向让stderr缓冲丢失。看门狗检测到「日志超过5分钟未更新」才重启，但重启也看不到任何错误信息。持续12.5小时无人发现。

## 强制性检查清单

每次创建或修改一个Python脚本时，必须检查以下条款：

### 1. 顶层执行体必须有 try-except 包裹（用log()写文件，不是print()到stderr）

```python
# ✅ 正确：写入日志文件
import traceback

LOG_FILE = "/path/to/script.log"
def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")

if __name__ == '__main__':
    try:
        # ... 主逻辑 ...
    except KeyboardInterrupt:
        log("⚠️ [script_name] 执行被用户中断")
        sys.exit(130)
    except Exception as e:
        log(f"🔴 [script_name] 执行失败: {e}")
        log(traceback.format_exc())  # ← 必须：完整堆栈
        sys.exit(1)
```

```python
# ❌ 错误：print到stderr（后台进程丢失）
except Exception as e:
    print(f"⚠️ 执行失败: {e}", file=sys.stderr)  # ← 在nohup下可能丢失！
    sys.exit(1)
```

### 2. 每个API/网络调用必须有局部try保护（最佳实践）

```python
try:
    result = exchange.fetch_balance()
except Exception as e:
    log(f"⚠️ 获取余额失败: {e}")
    log(traceback.format_exc())  # ← 记录完整堆栈
    result = {}  # 返回安全默认值
```

### 3. 错误信息格式

| 场景 | 输出 | 目标 |
|------|------|------|
| 用户中断 | `log("⚠️ [脚本名] 执行被用户中断")` | 日志文件 |
| 一般异常 | `log(f"🔴 [脚本名] 执行失败: {e}")` + `log(traceback.format_exc())` | 日志文件 |
| API查询失败 | `log(f"⚠️ 查询[接口名]失败: {traceback.format_exc()}")` | 日志文件 |
| 数据为空 | `log(f"⚠️ [脚本名] 未获取到数据，可能网络问题或API限频")` | 日志文件 |

### 4. 输出目标决策表

| 运行环境 | 错误输出目标 | 理由 |
|---------|:-----------:|:----|
| 交互式CLI | `print(f"...", file=sys.stderr)` | 用户立刻看到 |
| 后台进程(nohup/systemd/cron) | **`log()`写入日志文件** | stderr可能缓冲丢失 |
| 定时任务(cron) | **`log()`写入日志文件** | 无终端，print去/var/log/syslog不可靠 |

### 5. 退出码规范

- 正常退出: `sys.exit(0)` 或自然结束
- 用户中断: `sys.exit(130)`
- 异常退出: `sys.exit(1)`

### 6. 验证方法

每次修改后必须用以下命令验证语法正确性：
```bash
python3 -c "import ast; ast.parse(open('路径').read()); print('✅ 语法OK')"
```

## 已应用此规范的脚本清单

| 脚本 | 路径 |
|------|------|
| check_balance.py | ~/.hermes/mempalace/quant_trading/ |
| analyze_short.py | ~/.hermes/hermes-agent/ |
| backtest_btc_5x_short.py | ~/.hermes/hermes-agent/ |
| daily_evolution.py | ~/.hermes/mempalace/quant_trading/autonomous/ |
| dark_spark_intel.py | ~/.hermes/mempalace/quant_trading/autonomous/ |
| sovereign_orchestrator.py | ~/.hermes/mempalace/quant_trading/autonomous/ |
| dark_spark_pipeline_v2.py | ~/.hermes/mempalace/quant_trading/ |
| env_loader.py | ~/.hermes/mempalace/secure/ |
| dark_spark_short.py | ~/.hermes/mempalace/quant_trading/autonomous/ |
| dark_spark_grid.py | ~/.hermes/mempalace/quant_trading/autonomous/ |

## 新脚本模板

```python
#!/usr/bin/env python3
"""脚本描述"""
import sys
import traceback
from datetime import datetime

LOG_FILE = "/path/to/script.log"
def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")

def main():
    # 主要逻辑
    pass

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("⚠️ [脚本名] 执行被用户中断")
        sys.exit(130)
    except Exception as e:
        log(f"🔴 [脚本名] 执行失败: {e}")
        log(traceback.format_exc())  # ← 必须：完整堆栈
        sys.exit(1)
```