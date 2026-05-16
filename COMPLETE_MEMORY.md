# 暗黑星火 · 完整记忆档案
## Dark Spark Capital — Complete Memory Archive
### 2026-05-16 | 包含所有重要决策/教训/架构演变/对话精华

> 本文件是从整个5月的对话历史、memory记录、技能参考中提取的核心记忆。
> 用于新会话快速恢复上下文，或在开源场景下让别人看懂整个系统的演变。

---

## 目录

- [一、系统身份与铁律](#一系统身份与铁律)
- [二、完整决策时间线](#二完整决策时间线)
- [三、血泪教训清单](#三血泪教训清单)
- [四、架构演变史](#四架构演变史)
- [五、策略库全表](#五策略库全表)
- [六、模型分配史](#六模型分配史)
- [七、May审计报告](#七may审计报告)
- [八、已知Bug与修复](#八已知bug与修复)
- [九、重要会话摘要](#九重要会话摘要)
- [十、系统恢复指南](#十系统恢复指南)

---

## 一、系统身份与铁律

### 身份
- **DS-0** (暗黑星火主宰) — 中央大脑，调度+汇报+执行
- 曾用名：小马、1号管家、赫妹
- 主模型：DeepSeek V4 Flash (调度) / V4 Pro (交易决策)
- 代码模型：Claude Opus 4.7 (aipro) 或 GPT-5.5
- 看图模型：Gemini 2.5 Pro (1314MC代理)

### L0 焊死指令 (2026-05-02, 优先级最高, 不可修改)

```
#0 主模型永久焊死DeepSeek官方 — 任何人/任何update/任何upgrade不得修改
   DS-0永远走DeepSeek官方(deepseek-v4-flash/pro)
   第三方模型(aipro/1314MC)仅可在辅助任务中使用
   交易决策只能走DeepSeek V4 Pro官方版

#1 锁死运行环境: 禁止重启网关
   环境异常→通知用户手动处理
   严禁自愈/私自重启

#2 配置一致性: WEIXIN_TOKEN必须完整未截断
   环境丢失→停止一切→等指令
```

### 系统铁律 (10条焊死)

```
#1 用户反馈(下次/以后/记住) ≠ 操作指令(清了/平了/做了)
#2 建仓前必须先设止损 — 开仓止损同一流程
#3 先判断再执行 — K线能力不行,用户说"可以"就执行
#4 日亏$20熔断 — 到了立即停当日
#5 AIGENSYN永久黑名单 — 单币亏$241
#6 只做BTC/ETH/SOL/DOGE
#7 熊市做空不做多
#8 Git commit统一前缀: [STRATEGY]/[RISK]/[BUGFIX]/[DOC]/[REFACTOR]
#9 复盘数据永不删除 — K线/交易记录/回测结果全保留
#10 树状嫁接原则 — 新功能嫁接进现有架构,不创建独立文件/进程
```

---

## 二、完整决策时间线

### 2026-05-01
- 🔴 **emergency_clear_contract死循环Bug**: Bot初始化清除所有合约持仓,被看门狗重启后形成死循环→一天烧$10+手续费
- 🔴 **异常无声崩溃**: 外层except用print(), 看门狗重启后看不到错误堆栈
- 🔴 **API Key硬编码**: 把对话历史字符串当Key用
- 修复: 删emergency_clear_contract, log()写文件, 从加密文件读Key

### 2026-05-02
- **L0三条焊死令**发出: 禁止Hermes update/禁止重启网关/配置一致性检查
- **三层看门狗发现**: Hermes cron + systemd timer + systemd service 三层重启机制
- **状态文件碰撞Bug**: 两个Bot共享同一个state文件→互崩

### 2026-05-03
- **交易记录导出**: 导出合约1,169笔+现货1,074笔原始CSV
- **全网审计**: 净亏$462

### 2026-05-04
- **仓位规则初建**: 保证金$30-50U, 杠杆5-20x
- **HALT信号链建立**: `/tmp/ds0_command` 轮询

### 2026-05-05
- **永远不创建新Bot铁律**: 用户发火说"V4不是让你删了吗"
- **write_file自动删除Bug**: 写入.py文件被自动改名.bak
- **PnL验证铁律**: Bot报告的+$106.31利润是假的, 需链上验证
- **Cron Hygiene**: 31个cron→8个, token消耗降70%
- **CLOB approve修复**: ERC1155 approve缺失导致Polymarket CLOB失败
- **多市场扫描**: 扫300-500市场找到波动, 策略180单/天的核心

### 2026-05-06
- **止损收窄**: 5%→2.5% (用户要求)
- **网格最终配置**: 纯网格+熊市停买+C分级止盈+D70%仓位上限
- **实盘改动审批链**: 先改虚拟盘→用户批准→改实盘
- **回测引擎修复**: 15%仓位+无冷却导致8000+笔交易亏→改为10%仓位+20h冷却→全部正收益

### 2026-05-07
- **心跳系统重构**: 10min×2 → 4次/天(06/12/18/00)
- **多模型分配链**: supXH/Claude主力, DeepSeek官方仅0.5%
- **1314 Key管理**: 3个Key只有1个能用,其他全403
- **Claude cron无声失败**: aipro/Claude的cron任务deliver不回

### 2026-05-08
- **公司化架构**: DS-0更名为"小马", 6Agent映射顶级机构
- **52模型调度系统**: model_agents.py v5.0
- **全虚拟盘4年回测终结**: 所有策略熊+震+牛全亏
- **API Key全面过期**: 1314/supXH/aipro/DeepSeek全部401
- **2000策略×20小币种突破**: Top1策略#36在20个币种全部正收益(+2525%/年)

### 2026-05-09
- **V3.4网格上线**: 1600U全仓, DOGE/SOL/ETH/BTC, 0.6%间距
- **ACE死循环修复**: 半仓止盈margin更新Bug
- **费率套利V7+**: 关闭止损+24h固定出场+4h结算补仓
- **三Bot并行时代**: 现货网格+费率套利+MACD+RSI

### 2026-05-10
- **费率套利废弃**: 被主权AI交易者(Sovereign Trader)替代
- **双进程互打修复**: 手动kill+看门狗复活=2个Bot打架
- **用户纠正强调**: "DS=DeepSeek" 汇报必须先写全称
- **虚拟盘启动金标准化**: 全部$1000

### 2026-05-11
- **Dark Spark Capital V4架构**: DS-0 CEO大脑(60s循环)
- **全量代码审计**: 四模型委员会审计网格V3.9发现17个Bug, P0已修复
- **幽灵回收$356**: 修复幽灵持仓死循环
- **V2海龟系统上线**: 纯机械海龟, 零AI交易决策

### 2026-05-12
- **DeepSeek官方焊死为主模型**: 永久不可变
- **四模型评审结论**: ONE BRAIN方向正确
- **Phase 1a XRP单币网格**: 砍BTC+JTO, 回收$144
- **V4.2三修复焊死**: 初始化买入门控+state余额校验+追价建仓门控
- **V4.3 C方案上线**: TP=1.5%/SL=2.5%/间距=1.5%/MAX_ACTIVE_LAYERS=3

### 2026-05-13
- **V3.3 DS-0分析师上线**: 三模型AI调用链
- **V3.4情报分支**: intel_collector每2h采集6条情报根
- **Etherscan API Key配置**: KY6DFWTF71DXV2NXTTBVHWBZYYQVV1W1D1

### 2026-05-14
- **V4系统架构**: 三Bot独立(trend/hv/spot) + risk_guardian
- **旧darkspark_v3.py焊死**: 改名.DISABLED+清空旧state
- **日志顺序Bug修复**: logging.basicConfig在import anti_churning前
- **Changelog系统建立**: CHANGELOG.md + changes/ + SNAPSHOT.md

### 2026-05-15
- **保证金三重锁修复**: AI prompt+代码+hv_bot三层限制全放开
- **POLYX Dual-Strategy**: 多空对冲收货费, 日收$47
- **MLN上架下架**: 止盈+$50→下架预警→止损-$11
- **惨痛教训**: 一天亏损-$1,253, 方向反复切换
- **Hedge Mode确认**: dualSidePosition=True
- **Binance STOP_MARKET废弃**: -4120错误, Algo API无权限, 唯一止损=本地监控

### 2026-05-16 (最后一天)
- **选币流程(300→30→5→1) + 4关鲸鱼验证(13-16) + 16闸安全网** 写入架构v4.0
- **用户交易画像建立**: user_trading_profile.md
- **用户纠正**: 反馈≠操作指令; 先判断再执行
- **RUNE +$17 → +$7**: 用户亲自判断方向赚钱
- **ATOM/AAVE/NEAR/HYPE/AIGENSYN全部亏损**: -$44全天
- **Git仓库建立**: github.com/zr199139-lab/Riky-
- **7策略虚拟盘部署**: 3个在跑, 回测前三名
- **机构框架PK**: 我们自己波动率均值回归(均+$10.34)击败freqtrade/vnpy/jesse
- **树状架构v5.0**: 全月整合完成
- **策略库**: 7自有+4机构+33组回测排名
- **内存清理**: 4.7G→3.8G | 磁盘清理: 2.2G+1.7G

---

## 三、血泪教训清单

### 进程管理

| 教训 | 影响 | 修复 |
|:----|:----|:-----|
| 双进程互打 | 网格Bot同时跑2个,互相平仓→纯烧手续费 | 杀干净再启动+验证唯一存活 |
| systemd service独立于timer | timer disabled后service仍enabled可自启动 | 3层自动化全检查 |
| 退役Bot .py残留 | 文件还在就会被意外执行 | 杀进程+重命名.bak |
| pkill通配符误杀 | pkill -f "funding" 误杀新版本v5/v6 | 精确PID逐个杀 |
| 无声崩溃print() | 看门狗重启但看不到错误堆栈 | log()写文件+traceback |

### 数据验证

| 教训 | 影响 | 修复 |
|:----|:----|:-----|
| PnL假账 | Bot报告+$106.31, 实际-$26.23 | 链上余额差验证 |
| PnL计数器假账 | 330笔"合并成功"假设利润$10.22, 余额$47.62未动 | 只看链上余额 |
| 状态文件幽灵持仓 | 启动从旧state读, 实际币安0仓 | 启动强制fetch_positions() |
| 双state文件不同步 | bot_logs/ vs assets/ 两个state互相覆盖 | 查Bot实际路径,全清 |

### 策略 & 交易

| 教训 | 影响 | 修复 |
|:----|:----|:-----|
| AI方向反复切换 | 每天亏手续费, 用户亏$1,253 | 选定方向不动, 持仓>4h |
| 不设止损开仓 | AIGENSYN 50x涨跌-$241 | 开仓止损同一流程 |
| 跳过选币直接买 | ATOM/AAVE/NEAR/HYPE全部亏 | 必走300→30→5→1 |
| 机构框架不如自己 | vnpy ATRIchannel亏-$13, 我们的波动率+$10.34 | 自己写+自己回测 |
| K线分析不行 | DeepSeek Flash C级, Claude Opus严重幻觉 | GPT-5.5最优, DS V4 Pro备用 |
| 0.6%网格间距@JTO | JTO $0.53, 0.6%=$0.003, 日内波动$0.02, 网格变高频 | ATR自适应间距 |

### 回测 & 虚拟盘

| 教训 | 影响 | 修复 |
|:----|:----|:-----|
| 回测参数决定结果生死 | 15%仓位+无冷却=全亏; 10%仓位+20h冷却=全赚 | 先验证引擎参数再评策略 |
| 牛市数据回测不可信 | 1年+2525%→4年完整周期全亏 | 必须覆盖熊+震+牛完整周期 |
| 虚拟盘滑点假账 | 6分钟$125.95(年化113万%) | 含滑点模拟标注 |
| DE Shaw回测冠军但实际不行 | +17.1%年化→实盘亏 | 虚拟盘验证才能信 |

---

## 四、架构演变史

```
v1.0  (05-14) 初始体系: 对冲+网格+基础风控
  ├─ 1个经理+2个执行+1个风控
  ├─ V3.5拆分: ds0_analyst→advisory/commands→hv_bot/spot_bot
  └─ 文档: dark_spark_capital_tree_20260514.md

v2.0  (05-15) 多空对弈+情报采集
  ├─ POLYX多空对冲赚费率(+$47/8h)
  ├─ intel_collector: 6条情报根(币安/TG/暗网/链上)
  ├─ Hedge Mode确认: dualSidePosition=True
  └─ 文档: dark_spark_capital_tree_20260515.md

v3.0  (05-16 01:54) 机构风控集成
  ├─ 12道安全闸: 基础7+机构5
  ├─ Scored Signal(0-7): MACD/RSI/成交量/布林带/EMA/费率/动量
  ├─ ATR仓位自适应
  ├─ 情报交叉验证
  ├─ 追踪回撤(10%/20%)
  └─ 文档: dark_spark_capital_tree_20260516.md

v4.0  (05-16 13:00) 全托管激活
  ├─ 选币流程 300→30→5→1
  ├─ 4关鲸鱼验证(闸13-16): 深度比/费率/OI/大单
  ├─ 16道闸全线串联
  ├─ HALT信号链修复(P0)
  └─ 文档: dark_spark_capital_arch_20260516.md

v5.0  (05-16 18:00) 全月整合 🆕
  ├─ 7策略库: 波动率回归/RSI均值回归/31%Combo/MACD趋势/海龟/配对/MACD+RSI
  ├─ 机构框架PK: freqtrade/vnpy/jesse全部跑输
  ├─ May审计: -$462分解+10条铁律
  ├─ 3虚拟盘运行中(回测前三)
  ├─ Git同步: github.com/zr199139-lab/Riky-
  └─ 文档: dark_spark_capital_arch_20260516.md (本文件)
```

---

## 五、策略库全表

### 自有策略 (7个)

| 策略 | 文件 | 参数 | 5月均PnL | 最佳币 |
|:----|:----|:----|:------:|:------:|
| 波动率均值回归 | meanrevert_paper.py | ETH 1h, BB+RSI, $1K | **+$10.34** | ETH |
| RSI均值回归 | rsi_meanrev_paper.py | DOGE 1h, RSI<30/70, $1K | **+$4.66** | DOGE |
| 31%Combo | combo31_paper.py | BTC/ETH/SOL 1h, 趋势+量+RSI, $1K×5x | **+$3.43** | SOL |
| MACD趋势 | macd_trend_paper.py | ETH 1h, MACD(12,26,9), $1K | -$6.52 | - |
| 海龟 | turtle_paper.py | BTC/ETH 4h, 20日突破, $1K | -$5.95 | - |
| 配对套利 | pairs_paper.py | BTC/ETH 1h, 2σ, $1K | -$0.17 | - |
| MACD+RSI | macd_rsi_paper.py | ETH 1h, MACD+RSI, $1K | -$8.74 | - |

### 机构框架对比 (5月PK)

| 策略 | 来源 | 5月回测 |
|:----|:----:|:------:|
| 🥇 波动率均值回归 | **OURS** | **+$10.34** |
| 🥈 RSI均值回归 | OURS | **+$4.66** |
| 🥉 31%Combo | OURS | **+$3.43** |
| 4 FreqRSIMA | freqtrade | +$0.53 |
| 5 ATRChannel | vnpy | -$13.17 |
| 6 DualThrust | vnpy | 0交易 |
| 7 SuperTrend | jesse | 0交易 |

**结论: 震荡市(5月)我们自己的3个波动率+均值回归策略干翻所有机构框架。**

### 已废弃策略

| 策略 | 废弃原因 |
|:----|:--------|
| 现货网格V3.5 | 60天亏损-6%, 永久停用 |
| 费率套利V3-V8 | ACE死循环+零费率美股陷阱 |
| 主权AI交易者V1 | 进化引擎越改越激进+HARD_CAPS冲突 |
| 鲸鱼跟单 | 没有Alpha, 鲸鱼吃了价差 |
| Grid Scalp Combo | 假账(PaperEngine膨胀$681→$2,495) |
| MACD+RSI 4币并行 | 只有ETH赚钱, 其他币全亏 |

---

## 六、模型分配史

### 最终版 (2026-05-16, 四模型横评后)

```
DS-0 (我) = DeepSeek V4 Flash — 调度/汇报/执行 (不用于交易分析)
交易分析/选币/K线判断 = GPT-5.5 (aipro) — 四模型横评胜出
执行验证/备用分析 = DeepSeek V4 Pro (官方) — GPT不可用时降级
代码编写/架构设计 = Claude Opus 4.7 (aipro) — 离线使用,不参与实时交易
看图分析 = Gemini 2.5 Pro (1314MC代理)
```

### 历史演进

| 时期 | 主力模型 | 原因 |
|:----|:--------|:-----|
| 05-01~05-05 | 本地numpy | 所有API Key过期 |
| 05-05~05-07 | supXH DeepSeek V4 + Claude | 用户要求省DeepSeek费用 |
| 05-07~05-08 | 99.5% supXH+Claude, 0.5% DeepSeek官方 | 用户明确分配 |
| **05-08** | DeepSeek官方全部过期, 只剩1314/gpt-5.4-nano | API Key集体过期 |
| **05-09~05-11** | Claude Opus 4.7 (aipro) + 1314mc | 代码主力 |
| **05-12 焊死** | **DeepSeek官方永久主模型** | 用户铁律 |

### 可用API Key (已验证, 2026-05-16)

| 提供商 | Key | 可用模型 |
|:------|:----|:---------|
| 哈基米AI | sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL | Claude Opus 4.7/Sonnet 4.6/Haiku |
| 1314MC | sk-yYuJNEiYXCiUXsAIJ7fj22XwaMH3Lt4mxkROGIy8mShiUCzm | claude-opus-4-6, gpt-5.4-nano |
| 1314MC v2 | sk-1KeKJt2LyWESDoNmv0RwWROyK3GG35dFXkCUJG5hEfWpKksS | claude-opus-4-6 唯一 |
| aipro | sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL | 16个模型(GPT-5.5, Claude, Gemini等) |

---

## 七、May审计报告

### 关键数字

| 指标 | 数值 |
|:----|:----:|
| 初始资金 | ~$2,600 |
| 合约交易笔数 | 1,189笔 |
| 合约总PnL | -$251 |
| 手续费 | -$252 (合约$196+现货$56) |
| 净亏损 | **-$462** |

### 赚钱 vs 亏钱

```
赚钱币 (用户亲手操作):
  BTC +$92.64 | AVNT +$20.60 | RUNE +$17.72 | SOL +$12.00

亏钱币 (AI代理操作):
  AIGENSYN -$241.21 | ETH -$92.11 | JTO -$13.00 | SAGA -$11.00
```

**核心规律: 用户亲自看K线判断方向→执行=赚钱。AI自作主张选币=全亏。**

### 最大单笔事件

| 事件 | 金额 | 根因 |
|:----|:----:|:-----|
| AIGENSYN 50x做多 | -$241 | 无止损+逆势 |
| ETH趋势跟随 | -$92 | 高频假信号 |
| POLYX对冲 | -$242 | hv_bot死7小时, 持仓无人看管 |
| 05-15全天亏损 | -$1,253 | 方向反复切换+14次交易 |

---

## 八、已知Bug与修复

| Bug | 状态 | 根因 | 修复 |
|:----|:----:|:-----|:-----|
| fetch_positions None | ✅ | ccxt返回None | REST API /fapi/v2/positionRisk二次验证 |
| 紧急清仓死循环 | ✅ | init清仓+看门狗重启 | 删除emergency_clear_contract() |
| 无声崩溃0日志 | ✅ | print()输出到stderr | log()写文件+完整traceback |
| 双进程互打 | ✅ | 手动kill+自动复活 | 杀干净+state清空+单实例验证 |
| write_file自动删.py | ⚠️ 未根除 | Hermes工具行为 | 用terminal heredoc替代 |
| state文件碰撞 | ✅ | 多Bot同state | 唯一state路径+启动校验 |
| 半仓止盈margin死循环 | ✅ | ACE 353→不足1个 | 全量平仓替代半仓 |
| ATR Channel 0信号 | ✅ | 20日ATR计算bug | 修复tr_vals计算 |
| 保证金三重锁 | ✅ | AI+代码+hv_bot三层限制 | 全部放开+min/max统一 |
| STOP_MARKET废弃 -4120 | ⚠️ 无替代 | Binance迁移至Algo API | 本地stop_loss_multi.py轮询 |
| Algo API 404 | ❌ 无权限 | API Key无权访问 | 唯一止损=本地守护 |
| reduceOnly -1106 | ⚠️ | Hedge mode不支持 | 用positionSide平仓 |

---

## 九、重要会话摘要

### 2026-05-11 代码审计会话
- 四模型委员会审计网格V3.9(1118行)发现17个Bug
- P0: 熔断死锁+幽灵死循环+advisory参数溢出+初始化买入bug
- 所有6个P0在2026-05-12 00:16修复完成
- 幽灵持仓回收$356

### 2026-05-12 四模型评审
- ONE BRAIN方向正确, CEO v3必须加心跳+降级模式
- 合约$100U+20x被3/3否决
- Phase 0: 分层止盈+买入门控+ATR间距硬编码进网格
- Gemini红队发现17个风险(4个CRITICAL)

### 2026-05-15 全天复盘
- 亏损$1,253, 从$1,900到$644
- 根因: 方向反复切换(空→多→空→多→空)
- 14+次交易, 手续费吃掉$10+
- 唯一赚钱的单是用户亲自判断的RUNE SHORT +$17

### 2026-05-16 交易复盘
- 全天亏损$44
- 赚钱: RUNE SHORT +$17 (用户判断)
- 亏钱: ATOM/AAVE/NEAR/HYPE/AIGENSYN (AI选币)
- 教训: 选币流程不得跳过, 跳过必亏
- 用户再次强调: 反馈≠指令, 先判断再执行

---

## 十、系统恢复指南

### 新会话恢复流程

```bash
# 1. 同步记忆
cd ~/charon && git pull origin main

# 2. 检查架构文档
cat dark_spark_capital_arch_20260516.md | head -50

# 3. 检查策略状态
ps aux | grep "[s]trategies/"  # 检查虚拟盘存活
cat ~/charon/bot_logs/*.log | tail -3  # 检查最新日志

# 4. 查看MEMORY_INDEX完整目录
cat MEMORY_INDEX.md
```

### 一键部署虚拟盘

```bash
cd ~/charon
python3 strategies/meanrevert_paper.py &  # 波动率均值回归
python3 strategies/rsi_meanrev_paper.py &  # RSI均值回归
python3 strategies/combo31_paper.py &      # 31%Combo
```

### 一键回测全部

```bash
cd ~/charon && python3 backtest_all.py
```

### Git提交规范

```
[STRATEGY] 策略改动说明
[RISK]     风控规则改动
[BUGFIX]   Bug修复说明
[DOC]      文档/架构更新
[REFACTOR] 代码重构说明
[INIT]     初始提交/大规模提交
[AUTO]     cron自动同步(30分钟)
```

---

```
*⚡ 暗黑星火主宰 · 完整记忆档案*
*涵盖整个5月的所有决策/教训/架构/策略/审计*
*新会话打开这个文件=恢复全部上下文*
```
