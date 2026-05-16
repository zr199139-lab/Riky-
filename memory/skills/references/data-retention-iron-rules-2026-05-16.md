# 复盘数据永久保留铁律（2026-05-16 用户强制）

## 用户原话
> 「我说回测数据，K线交易记录，我们以后复盘还要用这个不要删」

## 清理豁免清单（永不删除）

| 数据类型 | 典型路径 | 重要性 |
|:---------|:---------|:-------|
| K线原始数据 | `kline_data/*.csv`, `backtest_data/`, `/tmp/may_klines.json` | ⭐⭐⭐ 历史行情有API获取时限，不可再生 |
| 回测结果 | `backtest_results/*.json/csv`, `/tmp/backtest_results.json` | ⭐⭐⭐ 策略排名跨期对比基准 |
| 交易日志 | `bot_logs/*.log`, `~/charon/bot_logs/*.log` | ⭐⭐⭐ 实盘/虚拟盘业绩的原始凭证 |
| 回测脚本 | `backtest_all.py`, `plot_results.py`, 策略代码 | ⭐⭐⭐ 可复现性保障 |

## 可安全删除的

- `strategy_repos/` — 第三方框架的GitHub克隆，随时可 `git clone --depth 1` 恢复
- `__pycache__/` — Python字节码缓存
- `/tmp/` 中的大文件（tar.gz、安装包、测试数据）
- `*.bak`, `*.bak.*` — 旧版本备份文件，内容已并入Git

## 磁盘清理流程

```bash
# 1. 先看各目录大小
du -sh ~/.hermes/mempalace/quant_trading/*/ | sort -rh | head -10

# 2. 对照豁免清单确认哪些可删
# 3. 只删 '可安全删除' 类别
rm -rf ~/.hermes/mempalace/quant_trading/strategy_repos/
find ~/.hermes -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
rm -f /tmp/*.tar.gz /tmp/*.deb /tmp/node_modules

# 4. 验证
df -h /
ls ~/.hermes/mempalace/quant_trading/kline_data/   # ✅ 确认保留
ls ~/.hermes/mempalace/quant_trading/bot_logs/      # ✅ 确认保留
ls ~/.hermes/mempalace/quant_trading/backtest_results/  # ✅ 确认保留
```

## 历史教训
- 2026-05-16: 差点误删 `backtest_results/` 和 `kline_data/`，用户纠正后才保住
- 根因：用户说"清回测数据"但意图是只清过大的第三方框架仓库，不是清自己的回测产出
- 铁律：**"回测数据"指第三方克隆仓库(`strategy_repos/`)，不是我们自己跑出的回测结果**
