# 费率套利 → 主权AI交易者 迁移记录

## 2026-05-10 清理 & 迁移

### 触发事件
用户转入$40到合约账户后，V7+费率套利Bot将$25→$65余额，但开仓(MXZN/ARIA/SONIC/BTR/PLTR)持续亏损，余额跌至$63。

### 根因分析
1. 选币AI（DeepSeek池2-3）选的是高波山寨币，手续费+滑点 > 费率收入
2. 零费率代币（AVGO/CRCL等美股）费率=0%，纯亏手续费
3. 没有生存压力机制——AI做不出好决策也能一直耗下去

### 执行的全重置流程

```bash
# 1. 平仓所有合约持仓
python3 /tmp/close_all_futures.py

# 2. 杀进程
pkill -f "v3.3_funding_arb_v7"

# 3. 清状态
rm -f bot_logs/v3.3_funding_arb_v7_state.json

# 4. 启动新主权AI系统替代
python3 autonomous/sovereign_v1.py &
python3 autonomous/evolution_engine_v2.py &
```

### 关键参数差异

| | V7+费率套利 | 主权AI v1.0 |
|---|:---:|:---:|
| AI决策频率 | 10秒 | 60秒 |
| 多模型 | DeepSeek单一 | DeepSeek+GPT-5.4双模型 |
| 生存机制 | 无 | Token预算$5+压力分级 |
| 进化机制 | 无 | 10分钟三模型共识 |
| 选币策略 | 全市场费率排序 | AI全流程过滤 |

### 新系统文件
- `autonomous/sovereign_v1.py` — 主权AI交易者（核心Bot）
- `autonomous/evolution_engine_v2.py` — 多模型群协作进化引擎
- `bot_logs/token_ledger.json` — Token收支账本
- `bot_logs/evo_overrides.json` — 进化参数覆盖
