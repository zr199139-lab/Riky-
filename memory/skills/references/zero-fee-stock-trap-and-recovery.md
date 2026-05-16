# 零费率美股代币陷阱 & 恢复流程 (2026-05-10)

## 故障现象

费率套利Bot余额从$65持续下降到$63，日亏$0.86，持仓不开新仓。

## 根因

AVGO/CRCL/AMZN/PLTR等**币安美股代币(equity tokens)**的`funding_rate`恒为0%：
- 做多方向永远收不到任何资金费
- 每天消耗双向手续费(0.04%×2=$0.05-0.10/天/仓)
- `funding_collected=0` 且 `funding_rate=0` → 纯亏

这类代币包括: AVGO, CRCL, AMZN, PLTR, AMD, NVDA, HOOD, SONIC(美股相关), BABA, TSLA等。

## 诊断命令

```python
# 检查持仓的funding_rate
with open('bot_logs/v3.3_funding_arb_v7_state.json') as f:
    state = json.load(f)
    for sym, p in state['positions'].items():
        if p.get('funding_rate', 0) == 0:
            print(f"⚠️ 零费率: {sym}")
```

## 恢复流程 (已验证)

```bash
# 1. 生成creds
cd ~/.hermes/mempalace/secure && python3 -c "
import sys,json; sys.path.insert(0,'.')
from decrypt_and_run import decrypt
d=decrypt()
with open('/tmp/creds.txt','w') as f:
    json.dump({'key':d['BINANCE_API_KEY'],'secret':d['BINANCE_API_SECRET']},f)
"

# 2. 全平所有合约持仓
python3 /tmp/close_all_futures.py

# 3. 杀旧Bot
pkill -f "funding_arb_v7"

# 4. 清state (可选)
rm -f bot_logs/v3.3_funding_arb_v7_state.json

# 5. 重启或切换到新Bot
# 如果是恢复V7+: python3 autonomous/v3.3_funding_arb_v7.py --start
# 如果已切换到主权交易者: 不需要额外操作
```

## 铁律

费率套利Bot中任何持仓的`funding_rate`必须 > 0.01%才有经济意义。
如果`funding_collected`累计为0且`funding_rate`为0，立即平仓换币。
