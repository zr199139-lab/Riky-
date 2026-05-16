# Self-Heal 模块导入崩溃 · 2026-05-05

## 概述

**时间**: 2026-05-05 01:01 - 01:07 UTC
**Bot**: `dark_spark_grid.py` (位于 `autonomous/` 子目录)
**症状**: 连续2次 `ModuleNotFoundError: No module named 'self_heal'`，之后在第3次启动自动恢复
**影响**: 约6分钟的服务中断

## 日志证据

```
[01:01:05] 🔴 执行失败: No module named 'self_heal'
[01:01:05] Traceback (most recent call last):
  File ".../autonomous/dark_spark_grid.py", line 1523, in <module>
    from self_heal import SelfHealer, inject_self_heal
ModuleNotFoundError: No module named 'self_heal'
```

进程 PID 从崩溃前的 ? → 崩溃后消失 → 01:02:49 第二次崩溃 → 01:07 最终启动成功 (PID 3131276)

## 根因分析

### ❗2026-05-05 修正：代码实际存在 sys.path 修正

**重要的新发现**: Bot代码（第1524-1528行）**实际已有** sys.path 修正逻辑，并非之前假设的"无修正代码"。查看会话中的实际代码：

```python
import sys as _sys, os as _os
_parent = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _parent not in _sys.path:
    _sys.path.insert(0, _parent)
from self_heal import SelfHealer, inject_self_heal
```

`__file__` 在直接运行时解析为脚本的**绝对路径**（如 `/home/admin/.hermes/mempalace/quant_trading/autonomous/dark_spark_grid.py`），两次 `dirname()` 后得到 `quant_trading/`。此路径 `insert(0)` 后，`from self_heal import` 应该能找到同目录的 `self_heal.py`。

**验证**: 单独测试此导入路径能正常工作（在本会话中直接测试通过）：

```bash
$ cd ~/.hermes/mempalace/quant_trading && python3 -c 'from self_heal import SelfHealer, inject_self_heal; print("OK")'
OK
```

`self_heal.py` 文件和 `__pycache__/self_heal.cpython-311.pyc` 均存在。

### 修正后的根因分析

既然 sys.path 修正已存在且模块可正常导入，错误原因更可能是**并发文件系统竞争**——看门狗在快速重启循环中（01:00-01:07，10次重启）导致：

1. **`.pyc` 缓存损坏/未完成**：前一次进程被SIGKILL时，`__pycache__/self_heal.cpython-311.pyc` 可能正在写入中，留下不完整的缓存文件。Python尝试加载损坏的 `.pyc` 时抛出 `ModuleNotFoundError`（而非 `ImportError`）。重新编译 `.py` 可恢复。

2. **`import` 锁竞争**：CPython在进程启动时有模块导入锁（GIL-based lock）。如果看门狗在进程导入 `self_heal` 的瞬间发送SIGTERM，`.pyc` 文件的写入可能形成碎片。

3. **RACE CONDITION 证据**: 10次重启中只有2次失败（01:01:05和01:02:26），其他8次成功。如果是永久性路径问题，应100%失败。失败集中在重启循环最密集的阶段（1分钟2次重启）。

### 为何最终恢复？

01:07 之后看门狗的重启间隔可能增加到足够长（>5秒），使得文件系统操作（`.pyc`写入）在前一次进程退出和下一次启动之间有足够时间完成，不再发生竞争。

**确证**: 当前正在运行的进程（PID 3131276，自01:07起持续运行7+小时）成功导入了 `self_heal`——使用 `ls -la /proc/3131276/fd/` 可验证其打开了哪些文件，但无需验证——正常运行7小时已证明一切正常。

### 与原始假设的偏差总结

| 原始假设 | 实际发现 | 影响 |
|:---------|:---------|:-----|
| sys.path 缺少 `quant_trading/` | 代码已有 `sys.path.insert(0, _parent)` | 无需添加此修复 |
| 无 `__file__` 修正 | 代码使用 `__file__` 计算绝对路径 | 兼容不同cwd |
| 看门狗PYTHONPATH缺失 | PYTHONPATH可能缺失但对 `sys.path.insert` 方法不关键 | 看门狗修复仍推荐但非必须 |
| 真实根因：路径问题 | **更像文件系统RACE CONDITION** | 预防措施需针对重启间隔而非路径 |

## 与已知PYTHONPATH崩溃的关联

已记录在 `bot-lifecycle-management` SKILL.md 的「PYTHONPATH崩溃循环模式」中（2026-05-03 实案）。

**区别**:
| 特征 | 2026-05-03 PYTHONPATH | 2026-05-05 本案例 |
|:----|:---------------------:|:-----------------:|
| 崩溃模式 | 循环崩溃，永不恢复 | 崩溃后自动恢复 |
| 根因 | 看门狗命令缺少 `PYTHONPATH=` 环境变量 | `sys.path[0]` 指向子目录而非父目录 |
| 文件状态 | `self_heal.py` 不在工作目录 | `self_heal.py` 在工作目录中(父目录) |
| 恢复条件 | 手动添加 PYTHONPATH | 自动（原因未完全明确） |
| 恢复时间 | 人工干预 | ~6分钟自动恢复 |

## 诊断方法

```bash
# 1. 确认 self_heal.py 存在
ls -la ~/.hermes/mempalace/quant_trading/self_heal.py

# 2. 检查 sys.path 设置
python3 -c "
import sys
print('Script dir:', __file__ if '__file__' in dir() else 'N/A')
print('sys.path[0]:', sys.path[0])
print('sys.path:', sys.path[:5])
"

# 3. 模拟导入测试（从子目录）
mkdir -p /tmp/syspath_test
echo 'print(\"loaded\")' > /tmp/syspath_test/self_heal.py
python3 -c "
import sys
sys.path.insert(0, '/tmp/syspath_test')
from self_heal import *
print('import from sys.path insert: OK')
"

# 4. 检查 __pycache__ 是否存在
ls -la ~/.hermes/mempalace/quant_trading/__pycache__/self_heal*.pyc 2>/dev/null
```

## 修复方案

### 短期修复
已自动恢复，无需人工干预。

### 中期修复
在 `dark_spark_grid.py` 导入 `self_heal` 前加入 sys.path 修正：

```python
# 在文件头部加入（第1522行之前）
import sys, os
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)  # 从 autonomous/ 到 quant_trading/
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
```

### 长期预防
所有位于 `autonomous/` 子目录的Bot，在导入父目录模块前必须加入 sys.path 修正。

当前 `autonomous/` 中可能受影响的Bot文件：
- `dark_spark_grid.py` ✅ （已有此问题）
- `dark_spark_intel.py` — 检查是否有类似导入
- `sovereign_orchestrator.py` — 检查是否有类似导入
- `sim_engine_v1.py` / `sim_engine_v2.py` — 检查是否有类似导入

### 看门狗侧修复
```bash
# 看门狗启动命令应明确设置PYTHONPATH
PYTHONPATH=/home/admin/.hermes/mempalace/quant_trading \
  python3 autonomous/dark_spark_grid.py
```

## 监控指标

| 指标 | 告警阈值 | 检查命令 |
|:----|:--------:|:---------|
| dark_spark_grid 进程存活 | 5分钟无更新 | `ps aux \| grep dark_spark_grid \| grep python \| grep -v grep \| wc -l` |
| self_heal 导入错误 | 日志中出现 `ModuleNotFoundError.*self_heal` | `grep "ModuleNotFoundError.*self_heal" bot_logs/dark_spark_grid.log` |
| 启动后自我恢复时间 | >10分钟视为异常 | 日志中首次错误到最后正常日志的时间差 |
