# 幽灵持仓清理实战记录 (2026-05-10)

## 场景
V8+跑了几小时后, 交易所累积15+个仓位, 余额$87全被占用, 可用$1.08。

## 根因链
1. V8的`fetch_positions()`返回positionAmt=None → 手动检测判定"全被手动平了"
2. 本地state删除仓位 → 触发补仓 → 交易所开新仓
3. 旧仓位还在交易所但本地不知道 → 新仓位叠加在旧仓上
4. 15个仓累计占用$86.9

## 清理步骤

```bash
# 1. 杀Bot
pkill -f "v3.3_funding_arb_v8"

# 2. 获取exact精度
PREC=$(curl -s 'https://fapi.binance.com/fapi/v1/exchangeInfo' | python3 -c "
import sys,json
d=json.load(sys.stdin)
for s in d['symbols']:
  ls=[f for f in s['filters'] if f['filterType']=='LOT_SIZE'][0]
  print(f\"{s['symbol']}:{ls['stepSize']}:{ls['minQty']}\")
")

# 3. 用REST API查真实持仓
curl -sH "X-MBX-APIKEY: $KEY" "https://fapi.binance.com/fapi/v2/positionRisk?timestamp=$TS&signature=$SIG" | \
  python3 -c "import sys,json; [print(p['symbol'],p['positionAmt']) for p in json.load(sys.stdin) if abs(float(p.get('positionAmt',0)or 0))>0.001]"

# 4. 逐仓精确平仓
# side = BUY if amt < 0 else SELL
# qty = math.floor(abs(amt) / stepSize) * stepSize
# 带 reduceOnly=true 防止翻转
```

## 关键发现
- ccxt `fetch_positions()` 在小币种上positionAmt返回None → 不可信
- REST API `fapi/v2/positionRisk` 永远返回真实数据
- `reduceOnly=true` 防止平仓指令变开仓
- 循环清理3-5轮确保旧仓不重叠
