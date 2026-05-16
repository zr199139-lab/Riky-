# 资本锁定 + 引擎死亡诊断 (2026-05-14 实案)

## 模式定义

主交易引擎（orchestrator）被删除/停止，但旧持仓仍在。现金几乎为0，资金全锁在无人管理的旧仓位中。

## 2026-05-14 实案

`darkspark_v3.py`（含SpotRotationEngine）被删除。旧网格遗留：
- MITO 8,016 ($520)
- ETH 0.188 ($425)
- UNI 118 ($423)
- DOGE 1,314 ($149)
- USDT现金仅 **$3**（占总权益$1,520的0.2%）

单独运行的`spot_bot.py`日志："总USDT≈$6.3" — 只有$6.3可用，无法建仓。

合约账户$343闲置，0持仓，0收益。

## 快速诊断命令

```bash
# 1. 查进程
ps aux | grep python | grep -v grep | awk '{print $11, $NF}'

# 2. 查主引擎文件存在
ls -la autonomous/darkspark_v3.py autonomous/spot_rotation.py 2>/dev/null || echo "⚠️ 主引擎不存在"

# 3. 查现货现金比例（REST API）
python3 << 'PYEOF'
import hmac,hashlib,requests,time,json,sys
sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt
c = decrypt(); k=c['BINANCE_API_KEY']; s=c['BINANCE_API_SECRET']
p={'timestamp':int(time.time()*1000)}
q='&'.join(f'{k}={v}' for k,v in sorted(p.items()))
sig=hmac.new(s.encode(),q.encode(),hashlib.sha256).hexdigest()
r=requests.get(f'https://api.binance.com/api/v3/account?{q}&signature={sig}',
  headers={'X-MBX-APIKEY':k}, timeout=10)
d=r.json()
usdt_cash = 0
total_val = 0
for b in d['balances']:
    tot = float(b['free']) + float(b.get('locked',0))
    if b['asset'] == 'USDT': usdt_cash = tot
    if tot > 0: print(f"  {b['asset']}: {tot}")
print(f"\nUSDT现金: ${usdt_cash:.2f}")
print(f"现金占比: {usdt_cash/total_val*100:.1f}%" if total_val > 0 else "无法计算总估值")
PYEOF

# 4. 查合约余额
python3 -c "
import hmac,hashlib,requests,time,sys
sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt
c=decrypt(); k=c['BINANCE_API_KEY']; s=c['BINANCE_API_SECRET']
p={'timestamp':int(time.time()*1000)}
q='&'.join(f'{k}={v}' for k,v in sorted(p.items()))
sig=hmac.new(s.encode(),q.encode(),hashlib.sha256).hexdigest()
r=requests.get(f'https://fapi.binance.com/fapi/v2/account?{q}&signature={sig}',
  headers={'X-MBX-APIKEY':k}, timeout=10)
a=r.json()
print(f'余额: \${float(a[\"totalWalletBalance\"]):.2f} 可用: \${float(a[\"availableBalance\"]):.2f}')
for p in a['positions']:
    if float(p['positionAmt'])!=0:
        print(f'  {p[\"symbol\"]}: {p[\"positionAmt\"]} @ {p[\"entryPrice\"]} PnL={p[\"unRealizedProfit\"]}')
"

# 5. advisory schema兼容性检查
python3 -c "
import json
with open('bot_logs/advisory.json') as f:
    a=json.load(f)
v = a.get('schema_version','?')
print(f'schema={v}, market={a.get(\"market_judgment\",\"?\")}')
"
```

## 恢复路径

| 路径 | 操作 | 释放资金 | 风险 |
|:----|:----|:--------:|:----|
| **A: 全清** | 卖所有旧持仓 | ~100% | 可能卖在低点 |
| **B: 部分清** | 卖弱势币，留强势 | ~60-70% | 需要判断强弱 |
| **C: 合约先动** | 合约资金开仓，现货等反弹 | 0% | 合约有杠杆风险 |

## 预防

1. 修改主引擎代码前先备份（`cp darkspark_v3.py darkspark_v3.py.bak`），不要直接删
2. 删文件前检查所有持仓是否已清空
3. 定期运行5分钟诊断清单，确保现金比例不<20%
