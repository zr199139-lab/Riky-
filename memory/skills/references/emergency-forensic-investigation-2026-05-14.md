# 紧急事件法医鉴定工作流 — Emergency Forensic Investigation

## 适用场景
- 用户说「怎么又回到快进快出了？」「复盘一下今天怎么回事」「检查交易记录」
- Bot异常交易/未知亏损/策略偏离设计模式
- 怀疑Bot进入了死循环（微仓分批买卖）
- 需要区分「策略性交易」vs「Bug产生的无效交易」

## 触发条件
| 触发词 | 含义 |
|:------|:------|
| "暂停" + "检查" + "复盘" | 先停再查 |
| "怎么回事" | 用户不知道发生了什么 |
| "又回到" | 旧问题复发 |

## 工作流（三步）

### Step 1: 立即暂停（不等待分析结果）
```
1. 写 commands.json → action=close_all（立即关闭所有合约仓位）
2. 暂停DS-0分析师cron（阻止新开单）
3. 汇报「已暂停」
```
**不要先分析再暂停**。暂停和分析并行。

### Step 2: 拉取币安原始数据（不信任Bot日志）
使用`_fapi`风格签名（requests库），拉两份数据：

**数据A: UserTrades（成交明细）**
- 端点: `/fapi/v1/userTrades`
- 时间范围: 从本日UTC 00:00至今
- 关键字段: `time`, `symbol`, `side`, `qty`, `price`, `commission`, `realizedPnl`
- 作用: 看到每一笔成交。按时间排序即可重建Bot完整操作序列

**数据B: Income（收支明细）**
- 端点: `/fapi/v1/income`
- 时间范围: 同上
- 筛选: REALIZED_PNL / COMMISSION / FUNDING_FEE / TRANSFER
- 作用: 区分「手续费vs盈亏vs入金」，一眼看出亏损是否来自手续费

**⚠️ 签名陷阱（2026-05-14验证）**
```python
# ✅ 正确 — 用requests库
def _sign(params):
    query = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    sig = hmac.new(SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f'{query}&signature={sig}'

def _get(path, params):
    p = params or {}
    p['timestamp'] = int(time.time() * 1000)
    qs = _sign(p)
    r = requests.get(f'{BASE}{path}?{qs}', headers={'X-MBX-APIKEY': KEY}, timeout=15)
    # 必须检查200，否则读body里的error信息
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        return None
    return r.json()
```

```bash
# ❌ 错误 — urllib的urlopen()在处理HMAC签名时容易400错
# 用requests替代urllib
```

**💡 数据解读技巧：**
- **多笔在同一秒成交且同向** → 分单买入模式的标志（旧Bot的特征）
- **同一币种买卖循环超过20次** → 死循环标志
- **realizedPnl小额正向但commission也高** → 微利死循环，手续费吃利润
- **TRANSFER入金** → 用户手动转入，从净PnL中排除

### Step 3: 交叉验证 + 根因定位

| 问题模式 | 数据特征 | 根因 |
|:---------|:---------|:-----|
| 微仓死循环 | 6-8个币/笔, 同一秒16档价格, 买卖交替>50次 | Bot分单逻辑被困 |
| 轮动抄底 | 5层买+1分钟跌完, 每5分钟扫一次 | 轮动策略在熊市无效 |
| 幽灵交易 | Bot说有持仓, 币安没有 | state.json脏数据 |
| 双Bot互殴 | 同币种买卖交叉, 手续费double | 多进程同时运行 |

### 汇总格式（直接给用户看）

```
## 📊 今日完整交易复盘

### 总账
| 项目 | 金额 |
|:----|:----:|
| 实现PnL | +$XX.XX |
| 手续费 | -$XX.XX |
| 资金费 | -$X.XX |
| 总交易笔数 | XXX笔 |
| 其中{主犯币种} | XXX笔（XX%）|

### 时间轴
{时间}→{事件}

### 根因
{一句话说清}
```

## 历史教训
- **2026-05-14**: contract_hunter.py在SIREN上513笔交易死循环，70分钟微仓买卖，手续费$10.55。诊断方法：从币安API拉UserTrades看到同一秒16档分单买入→确认是旧Bot的反转猎手模式。**
