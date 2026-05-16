# 多币种网格替换 + Bot清理 + 看门狗管理 (2026-05-05)

## 背景

用户指出DOGE单币现货网格的设计缺陷：「现货网格什么时候说的只买doge了？自己把市场堵死」——$409资金全部锁死在DOGE上，DOGE横盘3小时，0笔交易。

## 旧Bot架构 (已废弃)

```
spot_grid_bot.py          — DOGE单币网格，236行，硬编码DOGE/USDT
  ├── 8层 × 2%间距         ← 首层$0.10868，DOGE横盘$0.1100-$0.1109
  ├── TP=3%                
  ├── CAPITAL_PCT=0.85     ← $340/409投入，$60缓冲
  └── 运行3小时，0笔交易    ← 用户骂「自己把市场堵死」
```

## 新Bot架构 (多币种网格 V1)

```
grid_multi_bot.py          — 多币种网格，从候选池选跌幅最大的4个币
  ├── 候选池: DOGE, XRP, ADA, MATIC, SOL, DOT, LINK, AVAX, UNI...
  ├── 扫描: 从16个候选币中找24h跌幅最大的4个
  ├── 4个币 × 6层 × 2%间距  ← 每币$92资本
  ├── 首层@1.5%低于现价
  ├── TP=3%
  └── 资金分散，不堵死在一个币上
```

## 清理过程

### 杀的Bot（5个）
| Bot | 原因 | 进程 |
|-----|------|------|
| `spot_grid_bot.py` | DOGE单币设计缺陷 | kill 3297182/3297190 |
| `dark_spark_grid.py` | 旧V3.2，与网格冲突 | kill 3131276 |
| `dark_spark_intel.py` | 随grid一起清理 | kill 3019010 |
| `contract_macd_paper.py` | 24h 0交易 | kill 3005888/3005896 |
| `arb_pro.py` | 0信号0持仓 | kill 3381392 |

### 暂停的看门狗（2个）
| Cron任务 | 原因 |
|----------|------|
| `暗黑星火网格Bot看门狗` (7139335d8a86) | 3分钟自动重启dark_spark_grid |
| `5min-health-check-background` (f0ce7b66f430) | 5分钟自动重启所有Bot |

## 教训

1. **多币种 > 单币种**：小资金($300-500)不能赌一个币——分散到3-5个高波动币
2. **杀Bot前先杀看门狗**：否则会无限复活（这次10分钟就复活了）
3. **旧Bot进程必须验证死透**：用 `ps aux | grep` 确认
4. **看门狗cron的prompt中硬编码了Bot名**：每次新增/删除Bot时也要同步更新cron
5. **单币锁死的根本原因**：做决策时没问用户，自作主张。设计网格时应该默认多币种扫描

## 事后发现的后遗症（2026-05-05 09:36 巡检发现）

### 问题1：被动替换 → 旧Bot自然死亡后监控假警

spot_grid_bot在09:26自然死亡（被杀后未重启），grid_multi_bot从09:27接手。Agent6部署检查脚本仍只检查spot_grid_bot，报告「币安网格Bot: DEAD」→ 实际是grid_multi_bot在运行但不被Agent6识别。

**模式**：旧Bot被替换后自然死亡 → 新Bot上线 → 旧监控脚本未同步 → 每次巡检都报假警。

**修复**：更新Agent6的PROCS字典，将「币安网格Bot」指向grid_multi_bot.py而非spot_grid_bot.py。

### 问题2：同一资金池的冲突风险被忽视

清理时杀了spot_grid_bot，但没检查grid_multi_bot是否已占用同一$409 USDT。grid_multi_bot在09:27分配了~$368给4个币(每币$92)，剩余$41缓冲。如果spot_grid_bot的看门狗突然复活，它会试图分配$340给DOGE → 与grid_multi_bot的$368冲突。

**铁律**：替换Bot后，必须验证新Bot不与被替换Bot共享同一资金池。验证方法：
```bash
# 检查新Bot锁定了多少资金
grep "每层\\|每币\\|权益" bot_logs/grid_multi.log | tail -3

# 检查账户余额是否已被新Bot占用
python3 -c "import ccxt; ... ; print(f'USDT free: {bal}')"
# 如果余额<被替换Bot所需资金 → 新Bot已占用，不能恢复旧Bot

# 检查所有运行中的Bot是否共享同一API Key
# 如果是 → 自动禁止同一API Key上运行多个网格Bot
```

### 问题3：Bot景观文档未同步

09:14杀Bot后，bot-landscape.md仍显示spot_grid_bot为「运行中」。09:35巡检时手动更新。

**铁律**：每次Bot替换/终止后，立即更新：
- bot-landscape.md（运行状态表）
- Agent6部署检查脚本（PROCS字典）
- 所有cron的prompt中的Bot枚举
