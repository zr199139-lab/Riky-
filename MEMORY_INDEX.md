# ⚡ 暗黑星火资本 · 完整记忆库

> 系统全貌、策略、回测结果、技能参考集
> 更新: 2026-05-16

## 目录

| 目录 | 内容 |
|:----|:----|
| `memory/skills/` | 量化交易技能参考文件 (dark-spark-plan, bot-lifecycle, funding-arb) |
| `memory/backtest/` | 回测结果JSON (bt_v2_results, free_choice, leveraged等) |
| `dark_spark_capital_arch_20260516.md` | 🆕 v5.0 全月整合(7策略+机构对比+May审计) |

## 核心策略（7个虚拟盘）

| 策略 | 文件 | 参数 | 5月回测PnL |
|:----|:----|:----|:----:|
| 波动率均值回归 | `strategies/meanrevert_paper.py` | ETH, BB+RSI, 1h, $1K | **+$10.34/均** |
| RSI均值回归 | `strategies/rsi_meanrev_paper.py` | DOGE, RSI<30/70, 1h, $1K | **+$4.66/均** |
| 31%Combo三层门控 | `strategies/combo31_paper.py` | BTC/ETH/SOL, 趋势+量+RSI, 1h, 5x | **+$3.43/均** |
| MACD趋势 | `strategies/macd_trend_paper.py` | ETH, MACD(12,26,9), 1h, $1K | -$6.52 |
| MACD+RSI双确认 | `strategies/macd_rsi_paper.py` | ETH, MACD+RSI, 1h, $1K | -$8.74 |
| 海龟交易 | `strategies/turtle_paper.py` | BTC/ETH, 20日突破, 4h, $1K | -$5.95 |
| 配对套利 | `strategies/pairs_paper.py` | BTC/ETH, 2σ, 1h, $1K | -$0.17 |

## 机构框架测试 (vs vnpy/freqtrade/jesse)

| 框架 | 策略 | 5月回测 | 结论 |
|:----|:----|:------:|:----|
| OURS | 波动率均值回归 | **+$10.34** | 🥇 我们自己写得更好 |
| OURS | RSI均值回归 | **+$4.66** | 🥈 |
| OURS | 31%Combo | **+$3.43** | 🥉 |
| freqtrade | RSI+MA | +$0.53 | ETH上不错(+$14.6) |
| vnpy | ATR通道 | -$13.17 | 震荡市亏 |
| vnpy | DualThrust | 0交易 | 5月无突破 |
| jesse | SuperTrend | 0交易 | 同上 |

## 系统铁律

```
#0 主模型永久焊死DeepSeek官方
#1 永不重启网关
#2 用户反馈≠操作指令
#3 建仓前必须先设止损
#4 先判断再执行，不无脑跟指令
#5 日亏$20熔断
#6 AIGENSYN永久黑名单
#7 只做BTC/ETH/SOL/DOGE
#8 熊市做空，不做多
#9 Git commit 前缀 [STRATEGY]/[RISK]/[BUGFIX]/[DOC]/[REFACTOR]
```

## 一键恢复

```bash
cd ~/charon
git pull origin main        # 拉最新代码
bash ~/.hermes/scripts/git_memory_anchor.sh  # 同步记忆
python3 -m py_compile strategies/*.py  # 验证语法
```
