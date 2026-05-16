---
name: new-bot-onboarding-audit
description: 新Bot/Bot重大更新上线后必须执行的观察审计流程
version: 1.0
author: DS-0
created: 2026-05-09
category: quantitative-trading
tags:
  - onboarding
  - audit
  - quality-assurance
  - bug-detection
  - live-trading
---

# 新Bot上线观察审计流程

每次新Bot或Bot重大更新上线后，必须执行此流程。

## 零、启动前铁律（血泪教训 2026-05-09）

### 🔴 2026-05-12 新增：启动前必须确认交易所账户的 position 模式

Binance Futures 有两种模式，API 调用方式不同。不匹配则返回 -4061：

**诊断命令（必做）：**
```bash
cat > /tmp/check_position_mode.py << 'PYEOF'
import sys, json
sys.path.append('/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt
import requests, hashlib, hmac, time

c = decrypt()
key = c.get('BINANCE_API_KEY', '')
secret = c.get('BINANCE_API_SECRET', '')

def sign(p):
    q = '&'.join(f'{k}={v}' for k,v in sorted(p.items()))
    s = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    return q + '&signature=' + s

p = {'timestamp': int(time.time()*1000)}
r = requests.get('https://fapi.binance.com/fapi/v1/positionSide/dual?' + sign(p), headers={'X-MBX-APIKEY': key})
print(f"positionSide/dual: {r.json()}")
print(f"dualSidePosition=False → ONE-WAY模式 (不加positionSide)")
print(f"dualSidePosition=True  → HEDGE模式  (必须加positionSide)")
PYEOF
python3 /tmp/check_position_mode.py
```

| 模式 | `positionSide` | 开LONG | 开SHORT | 示例 |
|:---|:---|:---|:---|:---|
| **ONE-WAY** (BOTH) | **不加** | `side: 'BUY'` | `side: 'SELL'` | 默认模式 |
| **HEDGE** (LONG/SHORT) | **必须加** | `side: 'BUY', positionSide: 'LONG'` | `side: 'SELL', positionSide: 'SHORT'` | 需手动开启 |

**TP/SL 在 ONE-WAY 模式下：** 用 `reduceOnly: 'true'`，同样不加 `positionSide`。
**STOP_MARKET / TAKE_PROFIT_MARKET 用 Algo API 或循环检查**（见 crypto-exchange-api-troubleshooting section 10）。

```python
# ✅ ONE-WAY (BOTH) 模式 — 正确
order = _fapi('POST', '/fapi/v1/order', {
    'symbol': symbol, 'side': 'SELL',
    'type': 'MARKET', 'quantity': qty,
    'newOrderRespType': 'RESULT'
}, signed=True)

# ❌ 错误 — ONE-WAY 模式加了 positionSide → -4061
order = _fapi('POST', '/fapi/v1/order', {
    'symbol': symbol, 'side': 'SELL', 'positionSide': 'SHORT',
    'type': 'MARKET', 'quantity': qty,
}, signed=True)
```
```
1. ❌ 禁止「杀进程+清state+重启」三连
   这是破坏性操作。每次清仓重启损失手续费+价差≈$1/次
2. ✅ 多进程→只杀多余的，保留一个活的，不清state
3. ✅ state=现场证据→不清除，除非交易所持仓也清零了
4. ✅ 重启后黑名单丢失→手动重建，不能等Bot自己重新开ACE这种高波动币
5. ✅ 重启前先 fetch_positions() 确认交易所持仓
6. ✅ 重启后第一件事：对账 state positions vs 交易所真实持仓
```

**2026-05-09今天亏-$1.36的根因是我犯了以上所有错误**——6次清仓重启，白花$0.72手续费，黑名单丢失导致重开ACE又止损3次(-$5.89)。

## ⚠️ T+0 前置检查：系统风控启动验证

**新Bot/V4系统启动后，第一步不是检查Bot本身，而是检查风控系统能否正确感知全账户状态。**

### 必须验证项

```bash
# 1. 检查风控是否启动了
ps aux | grep risk_guardian | grep -v grep

# 2. 检查风险状态
python3 -c "import json; d=json.load(open('bot_logs/risk_state.json')); print(f'halt={d.get(\"halt\",\"?\")} global_halt={d.get(\"global_halt\",\"?\")} total_equity={d.get(\"total_equity\",\"?\")}')"

# 3. 验证total_equity ≈ 合约余额 + 现货USDT（不是只查合约！）
#    如果total_equity < 现货USDT余额 → 风控只查了合约，漏了现货 → 🔴 假HALT
```

### 🔴 已知陷阱：只查合约余额漏了现货

**2026-05-14 实案**：risk_guardian初始代码只查 `/fapi/v2/account`（合约钱包），总资产显示$801 → 触发全局HALT($1,600阈值)。实际总资产$1,788（合约$799 + 现货$989）。

**修复方式**：
```python
# 在风控主循环中加上现货余额
total_equity = acct.get('total_equity', 9999.0) + get_spot_usdt()

# get_spot_usdt() 查 /api/v3/account 的USDT余额
```

**铁律**：
- 双账户(合约+现货)系统的总资产计算必须同时查 fapi 和 sapi
- risk_state.json的 `global_halt: true` 跨重启保持 → 修复后需手动写空state覆盖！
- 修复HALT的完整流程：patch代码 → kill风控 → 清risk_state.json(写`{"status":"ok","halt":false,"global_halt":false,"ts":0}`) → 重启风控

## 时序检查点

### T+5min · 进程唯一性 — 正确清理方法
```bash
# 1. 查进程
ps aux | grep "脚本名" | grep -v grep

# 2. 如果多进程：
# 2a. 查交易所持仓
python3 -c "fetch_positions()  # 看旧仓还在吗"

# 2b. 杀所有同名进程
kill -9 $(pgrep -f "脚本名")

# 2c. 确认0残留
ps aux | grep "脚本名" | grep -v grep; echo $?  # 预期输出1(无结果)

# 2d. 读state + 对比交易所持仓
#     一致 → 保留state
#     交易所空 + state有 → state过期，清state
#     交易所非空 + state空 → 构建新state（不能清state）

# 2e. 启动新进程
```

### T+15min · 交易所持仓 vs Bot state 对账
```python
# 币安真实持仓
fetch_positions()  # 得到 {symbol: {amt, entry, mark, upnl, margin}}

# Bot state
state_file["positions"]  # 得到 {symbol: {qty, entry_price, margin}}
```
逐仓对比 `qty` 和 `entry_price`。差异 > 10% = 残留进程或state不一致。

**已知陷阱**：多进程写同state文件导致持仓数来回跳(3→5→3)、PnL假跳($0↔$89.75)。启动前必须杀干净所有同名进程+删state。

**陷阱：START_OFFSET过大导致0交易（2026-05-09）**
网格Bot的L0买入价 = current_price × (1 - START_OFFSET)。如果START_OFFSET=1.5%且当日在窄幅震荡(+1.8%)，L0买入价低于当日最低价 → **全天0交易**。
- 旧参数偏移1.5%，今日DOGE震荡1.8% → 0笔交易
- 新参数偏移0.1%，几乎立即入场 → 1笔交易

**铁律**：窄震荡市场（1h波动<0.5%）中，START_OFFSET必须≤0.5%，否则L0永不被触发。

### T+1h · 止损止盈触发验证
```
for each position:
    roi = upnl / margin * 100%
    
    if roi <= -5% AND NOT stopped:
        🔴 止损Bug! 应触发未触发
    
    if roi >= +10% AND NOT half-closed:
        🔴 止盈Bug! 应半仓锁利未触发
```

**已知止盈阈值Bug**：`price_pnl >= margin * TAKE_PROFIT_PCT` 条件中，`price_pnl` 已经包含了杠杆倍数(3x)。所以实际价格涨幅只需(10%/3)≈3.33%即可触发止盈。这是预期的。

### T+4h · 费率结算对账（仅费率套利Botv2）
- FUNDING_CYCLE=14400（4h），比V1的8h短一倍
- 每4小时重新扫描全市场费率
- 预期首次结算收入：5仓×平均0.1%费率×$20margin×3x≈$0.30/周期
- 对比币安fetch_funding_history()与Bot的funding_collected
```
每仓预期费率收入 = |rate| * notional ≈ |rate| * margin * leverage
total_funding = sum(settle_funding(pos))
```
对比币安 `fetch_funding_history()` 与 Bot 的 `funding_collected`。

### T+24h · 完整日终复盘
- 汇总所有交易记录
- 计算真实PnL（链上验证）
- 评估是否需要调整参数

## 已知Bug清单

| Bug | 现象 | 根因 | 修复 |
|-----|------|------|------|
| close_position假账 | PnL=$58.91但余额没涨 | close()返回filled=0→`(entry-0)*qty=巨量假利润` | 检查filled>0再算PnL，不信任executedQty |
| 多进程写state | 持仓数3↔5↔3跳变 | 两个进程同时读写同state文件 | 启动前杀所有同名进程+文件锁 |
| 交易所旧仓残留 | 币安持仓量≠Bot.state | 之前启动的旧仓还挂在交易所 | 启动前fetch_positions平掉所有非state仓位 |
| 止盈执行失败 | 半仓止盈时报-2022或被拒 | reduceOnly+stepSize精度两个Bug | 见 `references/funding-arb-take-profit-fix-2026-05-09.md` |
| 幽灵持仓残留 | 交易所旧仓占用资金，Bot不管理 | 旧Bot挂的仓还在交易所，新Bot不识别 | 手动卖出幽灵持仓释放USDT |

> 详细修复记录见 `bot-fault-prevention-scheme` skill 的 `references/close-position-fix-2026-05-09.md`

## PID文件锁防重复启动

```python
import os, fcntl
PID_FILE = "bot_logs/my_bot.pid"

def acquire_lock():
    fd = open(PID_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except IOError:
        print("🔴 已有实例运行，退出")
        sys.exit(1)
```

## 汇报格式模板

```
[DS-0] 新Bot状态报告
═══════════════════
Bot: {name} | PID={pid}
进程唯一: ✅/❌
持仓对账: {n}仓 ✅/❌
止损止盈: {n}达标/未触发 ✅/❌
异常: {list}
建议: {action}
═══════════════════
```
