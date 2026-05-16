# Whale Hedge Bot v2 无声崩溃报告
> 2026-05-02 21:56-22:01 | DS-0 22:00 cron巡检发现

## 事件时间线

| 时间 | 事件 |
|:----:|------|
| 21:56:46 | 最后正常日志: `🐋 鲸鱼库: 50个 (新增0)` |
| 21:56:46 | `whale_hedge_v2_state.json` 最后更新 |
| 22:01:37 | 新日志出现: `总计2` — 说明有重启尝试(by whom?) |
| 22:01:39 | 重启后Bot再次消失 |
| 22:02:00 | DS-0 cron检测到whale_bot死亡 |
| 22:02:48 | DS-0手动重启，PID 1493793 |

**停机总时长**: ~5分钟(21:56→22:02)，因为cron 22:00运行快速发现。若无cron，可能像intel_bot那样停2小时。

## 诊断

**无声崩溃** — 日志尾部无Exception/Traceback/Error，进程直接从`ps aux`消失。

**根因分类**：疑似Python段错误/SIGKILL/OOM。不像异常退出（后者会写traceback）。  
**间接根因**：看门狗脚本指向v1，即使Bot重启尝试启动的也是v1，v2无保护。

## 文件状态

| 文件 | 线索 |
|------|------|
| `whale_hedge_v2.log` | 21:56停止，22:01重启后写入2条(总计2)后再次停止 |
| `whale_hedge_v2_state.json` | update="2026-05-02T14:01:37" (UTC), events=0, opps=0 |

## 修复

1. 更新 `whale_hedge_watchdog.sh`：`BOT_SCRIPT="whale_hedge_bot_v2.py"`, `PID_FILE="/tmp/whale_hedge_bot_v2.pid"`
2. 手动启动：`cd ~/.../quant_trading && python3 whale_hedge_bot_v2.py`

## 系统启示

这是一个**系统性反模式**的第二次出现：

| 事件 | 问题 | 修复后 |
|:----|------|:------:|
| intel_bot 不在Agent5检查 | 监控盲区 | Agent5加入intel_bot检查 |
| whale_bot 看门狗指向v1 | 监控基础设施未跟随版本升级 | 加入「版本升级→监控同步检查」流程 |

**铁则**：版本升级不是只改文件名。每当你改名/升级一个Bot，所有指向旧文件名的脚本（看门狗、cron健康检查、Agent5/Agent6监控）都必须同步更新。
