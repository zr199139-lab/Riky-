# Bot启动时必须同步交易所真实状态，不信任本地state文件

## 发现 (2026-05-09)

用户手动平仓后，Bot报告5持仓但币安实际0持仓。
根因：`load_state()`直接从state.json加载持仓，不验证币安真实持仓。

## 受影响代码 (v3.3_funding_arb_live.py)

```python
# 🔴 错误模式：信任state.json
state = load_state()
positions = state.get("positions", {})
```

## 修复

```python
# ✅ 正确模式：强制同步币安真实持仓
try:
    ps = future.fetch_positions()
    real_positions = {}
    for p in ps:
        amt = float(p.get("positionAmt", 0))
        if abs(amt) <= 0:
            continue
        sym = p["symbol"]
        real_positions[sym] = {
            "symbol": sym,
            "side": "buy" if amt > 0 else "sell",
            "qty": abs(amt),
            # ... 从币安数据构建完整state
        }
    positions = real_positions
    log(f"✅ 同步币安持仓: {len(positions)} 个")
except Exception as e:
    log(f"⚠️ 持仓同步失败, 回退state文件: {e}")
    positions = state.get("positions", {})
```

## 黑名单也必须持久化+恢复

用户手动平仓后，黑名单存在旧state中。清空state重启后黑名单丢失→ACE重新出现。

**铁律**：黑名单/止损记录等运行时状态，在`save_state()`时必须同时写入state文件。重启时从state恢复，但不覆盖交易所真实持仓。

## 通用原则

| 数据 | 信任源 | 持久化方式 |
|------|--------|-----------|
| 持仓 | 交易所API (fetch_positions) | state存参考信息，不依赖 |
| 余额 | 交易所API (fetch_balance) | state存参考信息，不依赖 |
| 黑名单 | state文件（唯一可信存） | 必须持久化到state |
| 总PnL | state文件累计 | 持久化，但不要用于报告 |
| 策略参数 | 代码硬编码 | 不依赖state |

## 关联教训

- 2026-05-05 PnL假账: Bot内部累加器≠钱包余额变化
- 2026-05-06 state迁移TP用错基价: state中的buy_price≠实际成交价
- 2026-05-07 幽灵持仓: Bot不跟踪的旧持仓在交易所留滞

**所有本地缓存的交易数据，必须以交易所实时数据为准校正。**
