# 币安美股代币仓位限额 (2026-05-09 发现)

## 问题
币安USDT合约中的股票代币（QQQ/MU/NVDA/MSFT/QCOM/AMD/BABA等）存在仓位限额。

开仓时可能返回code -2027:
```
binance {"code":-2027,"msg":"Exceeded the maximum allowable position at current leverage."}
```

## 测试记录
| 币种 | 开仓结果 | 后续持仓状态 |
|------|---------|-------------|
| QQQ | 开仓成功 | 后续fetch_positions返回amt=0 |
| MU | 开仓成功 | 后续fetch_positions返回amt=0 |
| NVDA | 开仓成功 | 后续fetch_positions返回amt=0 |
| MSFT | 开仓失败(code -2027) | - |
| QCOM | 开仓失败(code -2027) | - |
| AMD | 开仓失败(code -2027) | - |

## 影响
当前币安合约市场上，有费率的币种全部分为两类：
1. 美股代币(ATM<0.5%) → 仓位限额不可用
2. 高波山寨币(ATR 3-9%) → 波动大，费率收入覆盖不了
3. 中间态几乎不存在

## 自检逻辑
如果Bot连续2个结算周期(8h)持仓=0，自动标记黄牌提醒用户暂停

## 可能的原因
Binance对Equity Token有特殊风险管理，限制了单个用户的持仓规模。
即使账户有足够资金，也无法超出特定限额。
