# 🔒 DS-0 主模型焊死锁 — 2026-05-12

## 铁律（优先级高于一切）
**主模型 = DeepSeek官方API (api.deepseek.com)**
- 当前: deepseek-v4-flash (日常) / deepseek-reasoner (复杂推理)
- 非DeepSeek官方模型只允许用于：代码编写(Claude Opus 4.7)、辅助分析(四模型委员会临时调用)
- DS-0中枢、进化引擎、交易决策 → **必须DeepSeek官方**

## 锁定措施
1. config.yaml default model = deepseek-v4-flash ✅ 已确认
2. provider = deepseek (api.deepseek.com) ✅ 已确认
3. 禁止任何cron/技能自动修改model配置
4. 禁止任何系统更新更改provider

## 验证命令
```bash
grep -A5 'default_model\|model:\|provider:' ~/.hermes/config.yaml | head -10
# 必须输出: deepseek-v4-flash / deepseek / api.deepseek.com
```

*违反此铁律=DS-0系统级故障，立即停交易并通知用户*
