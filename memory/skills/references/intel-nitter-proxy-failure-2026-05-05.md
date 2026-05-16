# Nitter代理基础设施故障 — 2026-05-05 实案

## 症状

dark_spark_intel Bot 在21:30至01:20+期间丢失所有X/Twitter数据源：

```
[2026-05-04 21:30:24] X @cz_binance: all Nitter nodes dead (backoff enabled)
[2026-05-04 21:30:33] X @binance: all Nitter nodes dead (backoff enabled)
[2026-05-04 21:30:42] X: ALL accounts failed 3x consecutively — disabling X monitoring entirely for now
[2026-05-04 21:30:42] X @elonmusk: skipped (3 consecutive total failures)
[2026-05-04 21:30:43] DarkWeb DeFiLlama: 3 new events
```

## 影响范围

| 数据源 | 状态 | 持续时长 |
|:-------|:----:|:--------:|
| X @cz_binance | ❌ all Nitter nodes dead | 4h+ (持续中) |
| X @binance | ❌ all Nitter nodes dead | 4h+ (持续中) |
| X @binanceannouncement | ❌ all Nitter nodes dead | 4h+ (持续中) |
| X @elonmusk | ❌ all Nitter nodes dead | 4h+ (持续中) |
| SlowMist API | ❌ Connection refused | 4h+ (持续中) |
| DeFiLlama | ✅ 3 new events/5min | 正常工作 |
| Binance Announcements | ✅ (未显示失败) | 正常工作 |

情报覆盖率: 7源 → 1源 (约15%)

## 时间线

```
21:30:15  Bot启动 (V2)
21:30:24  Nitter第1个节点失败（cz_binance）
21:30:33  Nitter第2个节点失败（binance）
21:30:42  全部4个X账户3次失败→X源禁用
21:30:43  DeFiLlama正常工作
21:30:43  SlowMist API Connection refused
21:40:15  第1次X跳过
21:55:15  SlowMist再次失败
01:20:15  96次X跳过累计
01:21:15  持续运行中，仅DeFiLlama产出
```

## Bot内部的优雅降级逻辑

```
exponential backoff on failure)
↓
X timeout: 8s/node | Max X failures before full skip: 3
↓
Nitter nodes尝试 → 每个节点超时 → 全部失败
↓
连续3次全部失败 → X sources disabled → 后续循环跳过
↓
SlowMist: 指数退避重试（单独管理）
↓
其他源(DeFiLlama等)：不受影响
```

## 可用数据源对比

| 数据方案 | 依赖 | 稳定性 | 成本 | 备注 |
|:---------|:-----|:------:|:----:|:-----|
| Nitter公共节点 | 第三方无偿托管 | ❌ 低 | 免费 | 经常下线/被墙 |
| 自建Nitter实例 | 自有VPS | 🟡 中 | 低 | Docker部署，需维护 |
| X/Twitter API | API Key | ✅ 高 | 高(付费) | $100+/月，rate limit 1500 tweet/15min |
| CryptoPanic | API Key | 🟡 中 | 免费(有限额) | 聚合新闻，含X内容 |
| LunarCrush | API Key | 🟡 中 | 免费(有限额) | 社交指标，含X影响力评分 |
| RSS/Atom feeds | RSS | ✅ 高 | 免费 | 部分X账号可转RSS |
| Telegram频道 | TG Bot | ✅ 高 | 免费 | 需要用户手动添加频道 |

## 诊断命令

```bash
# 1. 确认Nitter确实不可达
python3 -c "
import requests
nodes = ['https://nitter.net', 'https://nitter.lacontrevoie.fr', 'https://nitter.1d4.us', 'https://nitter.kavin.rocks']
for n in nodes:
    try:
        r = requests.get(f'{n}/api/statuses/user_timeline/cz_binance?limit=1', timeout=5)
        print(f'{n}: {r.status_code}')
    except Exception as e:
        print(f'{n}: FAILED ({type(e).__name__})')
"

# 2. 检查Bot日志中的具体失败模式
grep -E "Nitter|X @|SlowMist|DeFiLlama" ~/.hermes/mempalace/quant_trading/bot_logs/dark_spark_intel_daemon.log | tail -20

# 3. 计算每5分钟的产出事件数
grep "new events" ~/.hermes/mempalace/quant_trading/bot_logs/dark_spark_intel_daemon.log | awk '{print $NF}' | sort | uniq -c
```

## 从pyc恢复Nitter节点列表的方法

如果Bot源文件损坏，Nitter节点列表可能只存在于pyc中：

```python
import marshal, struct

pyc_path = '__pycache__/dark_spark_intel.cpython-311.pyc'
with open(pyc_path, 'rb') as f:
    magic = f.read(4)
    flags = struct.unpack('<I', f.read(4))[0]
    if flags & 0x1: f.read(8)  # hash mode
    else: f.read(8)  # timestamp + size
    code = marshal.load(f)

strings = set()
def collect(c):
    for const in c.co_consts:
        if isinstance(const, str) and 'nitter' in const.lower():
            strings.add(const)
        elif hasattr(const, 'co_code'): collect(const)
collect(code)

print("Nitter nodes:", sorted(strings))
```

## 教训

1. 公共Nitter节点不可靠，关键情报不应单一依赖Nitter
2. dark_spark_intel的优雅降级设计合理（独立源+3次失败禁用），但缺乏替代数据路径
3. 需要至少2个互不依赖的X替代数据源作为fallback
4. SlowMist API同时下线是巧合（网络抖动？），但与Nitter问题叠加使情报覆盖率极度恶化
5. 巡检应增加「活跃数据源比例」指标
