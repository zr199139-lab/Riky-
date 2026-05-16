# 系统维护例行清洁 (2026-05-16)

> 从暗黑星火V5清仓+清理的实战记录。账户余额~$1,057 USDT (现货), 合约$0。

## 进程清理

### 杀进程命令参考

```bash
# 按脚本名精确杀
pkill -f "binance_ws_stream"
pkill -f "spot_micro_pipe"
pkill -f "stop_loss_multi"

# 或按PID精确杀 (推荐，避免误杀)
ps aux | grep "[p]ython3" | grep -v "hermes\|gateway\|bridge\|http"
kill <PID1> <PID2> <PID3>
sleep 1
# 确认清理干净
ps aux | grep "[p]ython3" | grep -v "hermes\|gateway\|bridge\|http"
# 应返回空
```

### 保留白名单（永远不可杀）

| 进程 | 用途 | 说明 |
|:----|:----|:-----|
| `hermes` | Hermes Agent自身 | DS-0本体 |
| `gateway` | Hermes Gateway | 消息通道 |
| `bridge` | Hermes Web UI桥接 | Web面板 |
| `http.server` | HTTP服务 | Web UI/数据服务 |

### 实际案例 (2026-05-16)

清理前进程:
- `binance_ws_stream.py` — 运行5天, 163min CPU
- `spot_micro_pipe.py` — 运行2天
- `stop_loss_multi.py` — 运行4小时

清理后: 仅保留 hermes + gateway + bridge 核心进程

## 磁盘清理

### /tmp/ 目录

**2026-05-16实测**: `/tmp/` 从 1.7G 降到 28M

主要占用:
| 文件 | 大小 | 来源 |
|:----|:----|:-----|
| `ds_core.tar.gz` | 598M | 旧系统备份 |
| `dark_spark_full.tar.gz` | 559M | 旧系统备份 |
| `package/` | 186M | 旧测试 |
| `sdk_test/` | 164M | Polymarket SDK测试 |
| `polymarket_test/` | 111M | Polymarket测试 |
| `paperclip/` | 59M | 旧工具 |
| `node_modules/` | 23M | 旧npm |

清理命令:
```bash
find /tmp -name "*.csv" -o -name "*.json" -o -name "*.log" -o -name "*.py" -o -name "*.txt" -o -name "*.tar.gz" 2>/dev/null | xargs rm -f 2>/dev/null
rm -rf /tmp/package /tmp/sdk_test /tmp/polymarket_test /tmp/paperclip /tmp/node_modules /tmp/trojan-go* /tmp/gh.deb 2>/dev/null
```

### pycache 清理

```bash
find /home/admin -path "*/__pycache__/*" -type f -delete 2>/dev/null
find /home/admin -type d -name "__pycache__" -empty -delete 2>/dev/null
```

**2026-05-16实测**: 清理 28,130 个 pycache 文件

### 旧备份文件

```bash
# 确认数量
find /home/admin -name "*.bak*" -type f 2>/dev/null | wc -l
# 清理 (确认后)
rm -f /home/admin/backup_20260511/*.bak 2>/dev/null
```

### 磁盘基准

清仓前: 45% 使用 / 69G总
清仓后: 42% 使用 / 39G空余

**目标**: 保持 40G+ 空余

## 后台进程部署 (Hermes环境)

### ⚠️ Hermes限制

Hermes环境**禁止**以下操作:
- `nohup python3 script.py > log 2>&1 &`
- `python3 script.py &`
- `disown`
- 任何shell-level后台操作

### ✅ 正确部署方法

使用 Hermes 内置的 `terminal(background=true)`:

```python
# 每个策略单独启动
terminal(background=true, command="cd ~/charon && python3 -u strategies/turtle_paper.py")
terminal(background=true, command="cd ~/charon && python3 -u strategies/pairs_paper.py")
terminal(background=true, command="cd ~/charon && python3 -u strategies/meanrevert_paper.py")

# 等待初始化
sleep(5)

# 验证存活
terminal(command="tail -3 ~/charon/bot_logs/turtle_paper.log")
terminal(command="tail -3 ~/charon/bot_logs/pairs_paper.log")
terminal(command="tail -3 ~/charon/bot_logs/meanrevert_paper.log")
```

## f-string嵌套引用陷阱 (代码生成)

### 问题

通过 `write_file`/`patch` 写入Python代码时，f-string内使用 `"` 会被转义为 `\"`，导致 SyntaxError：

```python
# ❌ 语法错误!
log(f'现金=${state["cash"]:.2f}')
```

### 根因

Hermes的 `write_file`/`patch` 在写入时将 `"` 自动转义为 `\"`。f-string内 `\"` 被Python解析器拒绝。

### 修复方法

将f-string内的表达式先提取到变量：

```python
# ✅ 正确做法
c = state["cash"]
pos_side = state.get("position",{}).get("side","无")
t = state["trades"]
log(f'现金=${c:.2f} 持仓={pos_side} 交易={t}')
```

### 适用范围

所有通过 `write_file` 或 `patch` 生成的Python代码中，如果f-string内含有 `dict[key]` 或 `dict.get("key")`，都必须先赋值给变量。

## 机构策略虚拟盘部署模板

### 目录结构

```
~/charon/
├── strategies/
│   ├── turtle_paper.py        # 海龟趋势跟踪
│   ├── pairs_paper.py         # 配对统计套利
│   └── meanrevert_paper.py    # 波动率均值回归
├── bot_logs/
│   ├── turtle_paper.log
│   ├── pairs_paper.log
│   ├── meanrevert_paper.log
│   ├── turtle_paper_state.json
│   ├── pairs_paper_state.json
│   └── meanrevert_paper_state.json
└── (其他系统文件)
```

### 策略参数标准化

| 参数 | 海龟 | 配对 | 均值回归 |
|:----|:----:|:----:|:--------:|
| 初始资金 | $1,000 | $1,000 | $1,000 |
| 周期 | 4h | 1h | 1h |
| 检查间隔 | 300s | 300s | 300s |
| 仓位 | 50% | 30% | 30% |
| 止损 | ATR×2 | 比率回归0.5σ | ATR×1.5 |
| 最大持仓 | 2币并行 | 1仓位 | 1仓位 |

### 2026-05-16 部署日志

```
turtle:     ✅ 已运行 | 等待BTC/ETH突破信号
pairs:      ✅ 已运行 | 采集滚动窗口数据中
meanrevert: ✅ 已运行 | ETH做多 0.1368@$2192.77 (RSI=21超卖)
```
