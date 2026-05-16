# Whale Bot v4→v5 版本冲突 — 2026-05-02 23:52

## 发现

夜间巡检（cron job at 23:52）发现 `whale_hedge_bot_v4.py` 和 `whale_hedge_bot_v5.py` **同时运行**。两进程都在处理相同的Polymarket Data API鲸鱼信号：

- v4: 检测到2鲸鱼BUY但 `⏭ 无法获取token_id`（CLOB token_id解析失败）
- v5: 检测到同一信号但 `⏭ 价差不足: 0.0% < 2.0%`（v5修复了token_id解析，但市场无价差）

## 根因

1. v5.py 文件在 2026-05-02 22:26 创建（修复了v4的3个Bug）
2. v5被部署为进程时，**v4未被停止**
3. agent5_risk.py 和 agent6_deploy.py 仍指向 `whale_hedge_bot_v4` 
4. 如果下次agent6巡检发现"v4 dead" → 会试图重启v4 → 增加冲突实例

## 修复动作

| 步骤 | 操作 | 时间 |
|:----|------|:----:|
| 1 | `kill` v4 Python进程 (PID 1537872/1537881) | 23:52 |
| 2 | 清理v4工件：`whale_v4.log`, `whale_v4_state.json`, `whale_v4_positions.json` | 23:52 |
| 3 | `agent6_deploy.py`: `whale_hedge_bot_v4` → `whale_hedge_bot_v5` | 23:52 |
| 4 | `agent5_risk.py`: `whale_hedge_bot_v4` → `whale_hedge_bot_v5` | 23:52 |
| 5 | 验证v5单实例存活 | 23:52 |

## v5 vs v4 差异

v5修复了v4的3个Bug：
1. **clobTokenIds解析**: v4直接使用字符串 → v5用`json.loads()`解析
2. **CLOB价格获取**: v4用`/price/{token_id}` (404) → v5用`/book` order book中位价
3. **token匹配**: v4用`asset`字段 → v5用`outcomeIndex`索引

## 硬编码私钥

v5和第(和v2.1一样)在源码硬编码了私钥和钱包地址：
- `PK = "0x517064a5dd3d6a9ef6ed23ba28bc082099a7e59fc4b218d1c38a918caed37a99"`
- `WALLET = "0x9155c62561bF0Fdc5e6eF74f1938409931407685"`（非主钱包0x6854...F572F6）

## 系统状态（修复后）

- ✅ 5个Bot全部运行中（Grid/Whale v5/Intel/Paper/CLOB Arb）
- ✅ 监控脚本指向同一版本号（v5）
- ⚠️ CLOB无活跃市场：v5检测到鲸鱼但价差0%，跳过交易
