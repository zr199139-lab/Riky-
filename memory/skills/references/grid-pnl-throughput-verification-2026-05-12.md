# Grid Bot PnL 吞吐验证方法论（2026-05-12）

## 背景

2026-05-12本session中，基于CSV记录的+$5.39净利展开了辩论。直到拉取币安API数据才发现：CSV只记录了57笔事件，但币安记录了**646笔实际撮合交易**，资金吞吐$8,714。CSV的+$5.39只是0.06%的效率。

## 根因：CSV记录的不是完整资金流

CSV的格式：`time,event,sym,qty,price,pnl`
- `TP_*` / `SL_*` / `TIMEOUT_*` — 事件级（起始或结束一个网格层循环）
- `INIT_*` / `BUY_*` — 建仓事件，pnl=0
- **问题**：CSV不记录每个网格层的完整买入→卖出配对，只记录事件间的净利

币安API返回的才是**每一笔真实撮合交易**：
- 买入时：扣USDT，加币
- 卖出时：扣币，加USDT
- 净现金流 = 全部卖出收到的USDT - 全部买入付出的USDT

## 验证工作流

### Step 1: 获取正确API Key

```bash
# ❌ 错误：memory中硬编码的Key是合约Key，拉不到现货数据
BINANCE_API_KEY=m3hzNSZmuW98S4akqBSPZxyozl5iQ43ldbCbl0QFNjXy6w56s9X5UrMHrcu28jBZ  # 合约only！

# ✅ 正确：从credentials.enc解密获取
eval $(cd /home/admin/.hermes/mempalace/secure && python3 -c "
import sys; sys.path.insert(0,'.')
from decrypt_and_run import decrypt
c=decrypt()
print(f'export BAPI_KEY=\"{c[\"BINANCE_API_KEY\"]}\"')
print(f'export BAPI_SECRET=\"{c[\"BINANCE_API_SECRET\"]}\"')
")
# BAPI_KEY ends with ...AoVNjecrsdbE — 有现货权限
# BAPI_SECRET ends with ...ecPohCFfyN6J
```

### Step 2: 拉取7天交易记录

```bash
BASE="https://api.binance.com"
NOW_MS=$(date +%s%3N)
START_MS=$((NOW_MS - 7*24*3600*1000))

for sym in BTCUSDT DOGEUSDT ETHUSDT SOLUSDT JTOUSDT XRPUSDT; do
    TS=$(date +%s%3N)
    query="symbol=${sym}&startTime=${START_MS}&limit=1000&timestamp=${TS}"
    sig=$(echo -n "$query" | openssl dgst -sha256 -hmac "$BAPI_SECRET" | sed 's/.* //')
    curl -s -H "X-MBX-APIKEY: ${BAPI_KEY}" "${BASE}/api/v3/myTrades?${query}&signature=${sig}" > /tmp/${sym}_binance.json
    sleep 0.25
done
```

注意：`myTrades` API需要`startTime`参数和`timestamp`参数，两者缺一不可。

### Step 3: 汇总分析

```python
import json, os
totals = {}
for sym in ['BTCUSDT','DOGEUSDT','ETHUSDT','SOLUSDT','JTOUSDT','XRPUSDT']:
    fp=f'/tmp/{sym}_binance.json'
    if not os.path.exists(fp): continue
    data=json.load(open(fp))
    if not isinstance(data,list): continue
    buys=[t for t in data if t.get('isBuyer')]
    sells=[t for t in data if not t.get('isBuyer')]
    buy_q=sum(float(t['quoteQty']) for t in buys)
    sell_q=sum(float(t['quoteQty']) for t in sells)
    totals[sym]=buy_q, sell_q
total_b=sum(v[0] for v in totals.values())
total_s=sum(v[1] for v in totals.values())
print(f"总买入: ${total_b:.2f}")
print(f"总卖出: ${total_s:.2f}")
print(f"净现金流: ${total_s-total_b:+.2f}")
```

### Step 4: 对比CSV

```python
csv_pnl = 5.39  # 从CSV的TP-SL汇总
net_flow = total_s - total_b
print(f"CSV净利: +${csv_pnl}")
print(f"币安净现金流: ${net_flow:+.2f}")
print(f"差异: ${net_flow - csv_pnl:+.2f}")
print(f"吞吐效率: {csv_pnl/total_b*100:.4f}%")
if abs(net_flow) > csv_pnl * 5:
    print("⚠️ 库存成本远大于已平仓利润")
    print("   → 网格大量资金卡在持仓里")
    print("   → CSV报告的'利润'不代表真实系统表现")
```

## 时间分布分析（从币安API）

```python
from collections import Counter
hours = Counter()
for sym_data_files:
    data = json.load(open(fp))
    if not isinstance(data,list): continue
    sells = [t for t in data if not t.get('isBuyer')]
    for t in sells:
        from datetime import datetime, timezone
        h = datetime.fromtimestamp(t['time']/1000, tz=timezone.utc).hour
        hours[h] += 1

# UTC 03-04是卖出高峰（已证实）
print("最忙卖出时段:")
for h, c in hours.most_common(5):
    print(f"  UTC {h:02d}:00 — {c}笔卖出")
```

## 已知的Key权限陷阱

这个系统有**两套Binance API Key**：

| 来源 | 前缀 | 权限 | 可用于 |
|------|------|------|--------|
| credentials.enc解密 | ends with `...jecrsdbE` | **现货+合约** | 网格Bot / myTrades API / 账户信息 |
| memory硬编码 | `m3hzNSZmu...`以`28jBZ`结尾 | **合约only** | 合约Bot / premiumIndex / 合约持仓 |

**铁律**：除非确定任务只需合约数据，否则永远用`decrypt_and_run.decrypt()`获取API Key。硬编码在memory中的Key是从早期session残留的，只能查合约。

## 2026-05-12 实案输出

```
币种        总笔   买入   卖出   买入$             卖出$             净现金流
BTCUSDT      54     38     16    1146.27          1001.76          -144.51
DOGEUSDT    290    168    122    2559.98          2418.53          -141.45
ETHUSDT     130     69     61    2011.62          1908.53          -103.08
SOLUSDT     110     54     56    1702.39          1602.58           -99.81
JTOUSDT      43     28     15     683.41           681.05            -2.37
XRPUSDT      19     12      7     610.11           614.76            +4.65
总计         646    369    277    8713.78          8227.22          -486.57

CSV净利: +$5.39
币安净现金流: $-486.57
差异: $-491.95
吞吐效率: 0.06%
```

**结论**：+$5.39的CSV净利完全不能代表系统表现。$8,714的吞吐量只有0.06%转化成了已平仓利润，其余$486卡在当前持仓里。网格策略的本质是「大量资金搬运，极薄利润差」。
