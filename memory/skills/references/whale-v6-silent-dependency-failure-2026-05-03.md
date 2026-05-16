# Whale v6 Silent Dependency Failure — 2026-05-03

## 事件摘要

2026-05-03 18:06 cron巡检发现 `whale_hedge_bot_v6.py` (PID 2054987, started 15:58) 进程存活但 `whale_v6.log` 内容冻结在10:07:40。日志mtime持续更新（18:06），但文件大小不变，无新内容写入。

## 诊断过程

### 第一步：进程存活确认
```
ps aux | grep whale_hedge | grep python | grep -v grep
→ admin 2054987  python3 whale_hedge_bot_v6.py
→ PID存活，State: S (sleeping)，RSS: 51MB，CPU: 0:15/2h
```

### 第二步：日志新鲜度双重检查
```
mtime: 1777802799 (2026-05-03 18:06) ✅ 新鲜
tail -10 whale_v6.log → 内容最后时间戳 10:07:40 ❌ 冻结
```
Content-mtime gap > 8小时 → 异常。

### 第三步：进程文件描述符 (fd) 检查
```
ls -la /proc/2054987/fd/
→ fd0: pipe (stdin)
→ fd1: pipe (stdout)  
→ fd2: pipe (stderr)
→ fd3: /dev/urandom
→ fd4: socket
→ ❌ 无 log 文件 fd
```
正常Bot应有指向 `whale_v6.log` 的fd（Python `open()` 后保持fd）。无log fd = Bot未成功进入主循环。

### 第四步：Python解释器检查
```
ls -la /proc/2054987/exe
→ /home/admin/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/bin/python3.11
→ 非 venv python (预期 /home/admin/.hermes/hermes-agent/venv/bin/python3)
```
Bash wrapper `source ~/.hermes/hermes-agent/venv/bin/activate && python3 ...` 激活了venv但运行了系统默认的`python3`（uv安装版）。不过对于whale_v6来说这不应导致问题——纯Python脚本不依赖venv包。

### 第五步：依赖文件检查
```
ls -la liquid_markets.json
→ ❌ 文件不存在
```

## 根因

`liquid_markets.json` 被夜间文件清理流程删除（2026-05-03 清理14个文件/195KB时波及）。

Bot启动流程：
```
main() → log(初始化信息) → load_liquid_markets() 
  → os.path.exists(LIQUID_MARKETS_FILE) = False
  → log("❌ 流动性市场文件不存在") → return []
→ if not liquid_markets: log("❌ 无流动性市场可用，退出") → return
→ if __name__ == "__main__": main() → 执行完毕
→ print()输出→pipe（无人读取）
→ Python进程exit → 但bash wrapper /usr/bin/bash -lic ... 保持存活
```

为什么进程没exit？Possibly `main()` `return` 后 Python 解释器退出，但 bash wrapper 的 `set +m` 和 `-lic` 选项导致它等待子进程... 实际上 Python 的退出码通过 pipe 传播。当 pipe 的另一端关闭时，bash 也可能退出。但在这里 bash wrapper 也保持S状态。

最可能的原因：`log()` 函数在写入文件时发生了阻塞或异常，导致 `main()` 从未完成执行。检查 `log()` 实现：
```python
def log(msg, level="INFO"):
    print(line, flush=True)          # stdout → pipe
    with open(LOG_FILE, "a") as f:   # LOG_FILE = whale_v6.log
        f.write(line + "\n")
```
如果 `open(LOG_FILE, "a")` 返回的文件句柄未关闭（with块在异常时可能未执行exit），或者文件被其他进程以独占方式打开...但之前 `tail` 工作正常，说明文件可读。

## 修复建议

1. **短期**：重启whale v6前重建 `liquid_markets.json`
2. **代码修复**：`main()` 中 `load_liquid_markets()` 失败时调用 `sys.exit(1)` 而非 `return`，确保进程真正死亡，看门狗可正常重启
3. **代码修复**：Bot启动时在 `log()` 之前先验证所有依赖文件
4. **监控强化**：cron巡检增加 `/proc/<PID>/fd/` 检查，确认每个Bot的log fd存在
5. **文件清理防护**：白名单机制——清理前检查文件是否被任何运行中Bot引用

## 关键诊断命令速查

```bash
# 1. 进程诊断
PID=<pid>
cat /proc/$PID/status | grep -E "State|Threads|VmRSS"
ls -la /proc/$PID/fd/
ls -la /proc/$PID/exe
ls -la /proc/$PID/cwd

# 2. 日志内容vs mtime差异
python3 -c "
import os, re
LOG = 'whale_v6.log'
mtime = os.path.getmtime(LOG)
with open(LOG) as f:
    lines = f.readlines()
ts_pattern = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
last_ts = None
for l in reversed(lines):
    m = re.search(ts_pattern, l)
    if m:
        last_ts = m.group(1)
        break
print(f'gap: {(mtime - __import__(\"datetime\").datetime.strptime(last_ts, \"%Y-%m-%d %H:%M:%S\").timestamp())/3600:.1f}h')
"

# 3. 检查已知依赖文件
for f in "liquid_markets.json" "polymarket_live_key.json"; do
    [ -f "$f" ] && echo "✅ $f" || echo "❌ $f"
done
```
