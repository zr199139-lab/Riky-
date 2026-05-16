---
name: zero-modification-bot-enhancement
description: 给正在运行的稳定Bot增加新功能时，0行修改现有代码的"纯追加"方法
version: 0.1.0
author: DS-0
created: 2026-05-01
updated: 2026-04-28
category: quantitative-trading
tags:
  - quantitative-trading
requires:
  bins:
    - python3
install:
  - kind: pip
    - python-dotenv
metadata:
  darkspark:
    skill_type: engine
    maturity: development
    risk_level: low
    requires_human_review: false
---

# 零修改 Bot 增强方案

## 触发条件

- 用户有一个正在运行且盈利的Bot
- 需要在Bot里加新功能（策略、信号、逻辑）
- 但**不能修改现有代码**——已跑通的逻辑不能碰，改出bug会直接亏损

## 核心原则

> 可追加，不可修改。可新增，不可重构。

所有改动必须是以下两种之一：
1. **新增导入行**（文件顶部）
2. **新增方法 + 调用行**（类内追加 + 主循环加一行调用）

## 操作步骤

### 1. 读代码，划清边界

先完整读一遍Bot，用思维缓存记住：
- 类结构（`__init__`的变量）
- 已有的所有方法名
- 主循环的调用顺序
- 持久化逻辑（save/load state）

明确 **哪些行不能碰**：
```python
# 红线：不可改
- scan_long() / scan_short() 完整函数体
- report() / init_*_grids() 
- 主循环里除了加新调用行之外的所有内容
```

### 2. 新增的结构

在文件的「最后、最后」的类末尾之前插入新方法：

```python
# 在最后一个方法之后、下一个同级别之前插入

    # ===== 新增模块：XXX =====
    def _fetch_data(self):  # 纯新增辅助方法
        ...

    def new_scan_method(self):
        # 状态变量
        if self.last_check_time + INTERVAL > now:
            return
        
        # 核心逻辑
        ...
```

然后主循环**只加一行**：
```python
grid.new_scan_method(prices)  # 纯新增调用行
```

### 3. 持久化处理

如果新模块需要持久化状态：
- `__init__`：加一行 `self.new_state = {}`（在最后一个变量后）
- `_save_state`：加一行 `'new_state': self.new_state,`（在已有key后）
- `_load_state`：加一行读取（如果已存在）

### 4. 验证方法

```bash
# 语法检查
python3 -c "import py_compile; py_compile.compile('your_bot.py', doraise=True)"

# 对比改动
git diff --stat  # 看是不是只有新增行

# 重启Bot
kill <PID> && nohup python3 -u your_bot.py &
sleep 30 && tail -20 bot.log  # 确认启动正常
```

## 陷阱

- ❌ 不要改已有if条件、for循环、方法体
- ❌ 不要用`patch`或`sed`替换已有代码块
- ❌ 不要重构任何已有的函数，不管它写得多烂
- ✅ 只在`__init__`尾部加变量、在最后一个方法后加新方法、在主循环加一行调用

## 范例

看 `dark_spark_grid.py` 增强做空信号的案例：
- 新增3个配置常量
- `__init__`尾部加2个状态变量
- `_save_state`尾部加1个key
- 在`_get_short_size`和`_log_trade`之间插入`scan_signal_short` + 3个辅助方法
- 主循环加一行 `grid.scan_signal_short(prices)`
- **0行修改** `scan_long`, `scan_short`, `report`, `init_*_grids` 等现有函数