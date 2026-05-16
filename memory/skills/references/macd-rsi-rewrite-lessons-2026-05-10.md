# MACD+RSI 虚拟盘重写教训 — 2026-05-10

## 背景

原 `v3.3_MACD_RSI_虚拟盘.py` 是一个126行的回测包装器，每次 `run_once()` 调用 `strat_macd_rsi()` 跑完整90天历史数据。PnL永远不变——它只是在重复跑同一个历史回测。

## 架构区分：回测优化器 vs 实时交易器

| 模式 | 目的 | 数据输入 | PnL行为 |
|:----|:-----|:---------|:--------|
| 🔄 回测优化器 | 找最佳参数 | 全量历史K线 | 相同参数每次跑相同PnL |
| 📡 实时交易器 | 模拟真实交易 | 最新K线流 | PnL随时间累积变化 |

用户的「72h跑数据」期望的是📡模式，但原代码是🔄模式。

## 完全重写 (~350行)

关键改动：
1. 用 `fetch_klines(symbol, "1h", limit=200)` 只拿最新K线
2. 计算实时MACD/RSI，只在最后1根K线上判断信号
3. 维护持仓状态(MACD金叉开仓、死叉平仓、止损检查)
4. State持久化(save_state/load_state)
5. 进化引擎集成(8个可进化参数)

## 关键Bug

### Bug 1: `eq` UnboundLocalError

```python
# ❌ 原代码
try:
    eq, pnl, prices = self.run_cycle()
except Exception as e:
    log(f"主循环: {e}")
    prices = {}          # 只设了prices，eq未赋值
# 后面 evolution 引用 eq → UnboundLocalError!

# ✅ 修复
except Exception as e:
    log(f"主循环: {e}")
    prices = {}
    eq = self.engine.equity() if hasattr(self, 'engine') else INITIAL_CAPITAL
```

**教训**：exception handler中所有后续引用的变量都必须有默认值。

### Bug 2: 进化引擎 CHECK_INTERVAL 暴走

进化引擎将 `CHECK_INTERVAL` 从600s改到3600s（1小时），导致Bot几乎不做事。

```python
# ✅ 修复：EvolutionEngine.get_param() 加硬上限
HARD_CAPS = {
    "CHECK_INTERVAL": (60, 600),  # 最大10分钟
}
```

### Bug 3: State文件格式冲突

旧state有 `trades: 57`(int)，新代码期望 `trades: []`(list)。启动时清空旧state。

## 铁律

1. 部署新虚拟盘前先确认是🔄模式还是📡模式
2. 回测优化器的PnL不能当「交易战绩」汇报
3. 72h验证只对📡模式有意义
4. except块中所有后续引用的变量必须初始化
