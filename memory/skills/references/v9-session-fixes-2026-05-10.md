# V9 流量池 — 2026-05-10 Session 关键修复

## 1. fapiPost_leverage 静默失败 (🔴致命)

**现象**: V9代码写 `LEVERAGE=10`，交易所持仓实际 `lev=20x`。新开仓用的是币安默认20x。

**根因**: ccxt某些版本移除了 `fapiPost_leverage()` 方法（AttributeError被 `except:pass` 吞掉）。

```python
# ❌ 旧代码（静默失败）
ex.fapiPost_leverage({"symbol": sym_raw, "leverage": 10})
# AttributeError → except:pass → 永远不会设置杠杆

# ✅ 修复（REST API直接调用）
import hmac, hashlib
def set_leverage_rest(symbol_raw):
    for _ in range(3):
        try:
            ts = int(time.time()*1000)
            body = f"symbol={symbol_raw}&leverage={LEVERAGE}&timestamp={ts}"
            sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            r = requests.post(
                f"https://fapi.binance.com/fapi/v1/leverage?{body}&signature={sig}",
                headers={"X-MBX-APIKEY": key}, timeout=5
            )
            j = r.json()
            if r.status_code == 200 and j.get("leverage") == LEVERAGE:
                return True
        except:
            time.sleep(0.5)
    return False
```

**铁律**:
1. 设置杠杆后必须验证返回值的 `leverage` 字段
2. 余额<$30降杠杆(20x→10x)可能被拒("insufficient margin balance")
3. 全局存储API Key/Secret供REST调用
4. 启动时打印每个持仓的实际杠杆到日志

## 2. 单仓止损替代全局回撤 (推荐新范式)

**之前**: 全局回撤检查 `dd > STOP_DD (10%)` → 全部平仓 → 好仓坏仓一起砍 → 连坐效应

**现在**: `POSITION_STOP_LOSS_PCT = 0.06` (单仓亏超6%保证金硬砍) + 全局回撤只作为兜底

```python
# ✅ 新增在主循环中
for sym in list(positions.keys()):
    pos = positions[sym]
    pnl_pct = (pos["total_pnl"] / max(pos["margin"], 1)) * 100
    if pnl_pct <= -POSITION_STOP_LOSS_PCT * 100:
        close_position(ex, positions.pop(sym))
        bl[sym] = cycle + BLACKLIST_CYCLES
```

## 3. 持仓冷却 300s → 180s

用户反馈"300s就足够亏爆了"。

| 参数 | V8 | V9 |
|------|-----|-----|
| MIN_HOLD_BEFORE_AI_CLOSE | 300s | 180s |

## 4. AI决策冷却 60s → 30s

用户要求"不用省deepseek费用"，加快决策频率。

| 参数 | V8 | V9 |
|------|-----|-----|
| AI_DECISION_COOLDOWN | 60s | 30s |

## 5. 日亏动态计算平衡点

| 余额区间 | 日亏上限 |
|---------|---------|
| <$40 | $2.50 |
| $40-$60 | $3.00 |
| $60-$100 | $6.00 |
| >$100 | $16.00 |
