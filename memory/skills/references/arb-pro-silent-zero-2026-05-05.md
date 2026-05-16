# Arb Pro 静默零产出 — 2026-05-05 实案

## 症状

arb_pro (套利Bot) 持续运行但完全零产出：

```
[01:00:28] 📊 ArbPro | 信号=0 开=0 PnL=$+0.00 WR=0/1 缓存=0市场 | 块#86396992
[01:04:41] 📊 ArbPro | 信号=0 开=0 PnL=$+0.00 WR=0/1 缓存=0市场 | 块#86397119
[01:08:55] 📊 ArbPro | 信号=0 开=0 PnL=$+0.00 WR=0/1 缓存=0市场 | 块#86397266
[01:13:12] 📊 ArbPro | 信号=0 开=0 PnL=$+0.00 WR=0/1 缓存=0市场 | 块#86397374
[01:17:29] 📊 ArbPro | 信号=0 开=0 PnL=$+0.00 WR=0/1 缓存=0市场 | 块#86397503
```

## 关键发现

| 指标 | 值 | 诊断 |
|:----|:--:|:-----|
| 缓存市场 | 0 | 🔴 市场数据从未加载——事件缓存为空 |
| 信号 | 0 | 缓存=0 → 无市场可扫描 → 无信号 |
| 开仓 | 0 | 无信号 → 无开仓 |
| PnL | $0.00 | 无交易 → 无PnL |
| WR | 0/1 | 仅尝试1次（失败） |
| **日志错误行** | **0行/574行** | 🔴 所有异常被`except: continue`吞噬 |

## 根因分析（实际发现）

Bot的 `process_block()` 方法使用链上事件日志填充价格缓存。实际根因：

### 根因1：CTF Exchange事件签名过时（主因）

```python
EVENT_SIG = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
```

`w3.eth.get_logs({"address": CTF_EXCHANGE, "topics": [EVENT_SIG]})` 返回空——该事件签名不再匹配CTF Exchange当前合约版本。Bot启动时没有做任何验证。

### 根因2：裸 `except: continue` 吞噬所有异常（使根因不可见）

```python
def process_block(self, bn):
    try:
        logs = self.w3.eth.get_logs({...})  # 返回空-无错误
        for l in logs:                      # 循环0次
            try:
                # ... 数据处理 ...
            except:
                continue  # 从未执行
    except Exception as e:
        if self.cycle % 100 == 0:
            log(f"块{bn}处理错误: {str(e)[:60]}")  # 极罕见触发
```

`get_logs` 返回空列表不是异常——它正常工作但不返回匹配事件。`for l in logs:` 直接跳过。外层except从不触发。根因完全不可见。

### 根因3：无初始化验证

Bot启动时检查了RPC连接和钱包余额，但**没有验证事件签名是否有效**：
```python
# 应该有：
# logs = w3.eth.get_logs({"address": CONTRACT, "fromBlock": LATEST-10, "topics": [EVENT_SIG]})
# if len(logs) == 0: sys.exit(1)  # 事件签名无效！
```

## 实际数据

- **总运行时间**: 2026-05-04 11:49 至 2026-05-05 01:25 (~13h35m)
- **总日志行数**: 574行
- **块处理次数**: 526次
- **错误/警告行数**: 0行 (0%)
- **缓存市场**: 始终为 0
- **内存占用**: ~77MB RSS
- **CPU占用**: ~1.5%
- **终止**: 2026-05-05 01:30 巡检时 `kill -9` 终止

## 诊断流程

```bash
# 1. 零错误日志 = 第一告警信号
grep -ci "error\|warn\|fail\|traceback\|exception\|异常" arb_pro.log
# → 0 → 疑似全吞异常处理

# 2. 检查代码中的裸except
grep -n "except.*:" arb_pro.py | grep -v "Exception\|Error\|raise"
# 找裸 except: 或 except: pass

# 3. 手动验证事件签名
cd ~/.hermes/mempalace/quant_trading
python3 -c "
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('https://polygon.drpc.org'))
sig = '0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee'
addr = Web3.to_checksum_address('0xE111180000d2663C0091e4f400237545B87B996B')
bn = w3.eth.block_number
logs = w3.eth.get_logs({'address': addr, 'fromBlock': hex(bn-100), 'toBlock': hex(bn), 'topics': [sig]})
print(f'最后100块匹配事件: {len(logs)}')
# → 0 → 事件签名无效
"

# 4. 检查是否为RPC问题
python3 -c "
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('https://polygon.drpc.org'))
print(f'Connected: {w3.is_connected()}')
print(f'Block: {w3.eth.block_number}')
# 如果RPC连通但get_logs空 = 事件签名/合约地址问题
"
```

## 影响

- Bot持续空转13.5小时，消耗内存+计算而不产生任何价值
- 每4分钟一次RPC调用浪费免费API额度
- 消耗1个Bot槽位——可用资源受限
- 无告警触发（PID正常，mtime新鲜，日志无错误）
- 最关键：**进程看起来完全健康但产出为零**

## 修复建议

1. **短期**：kill arb_pro进程，释放资源（已执行）
2. **中期**：验证CTF Exchange当前事件签名，或改用CLOB API获取市场数据
3. **长期（代码级别修复）**：
   - 在`process_block()`的`except`中记录完整堆栈：`log(traceback.format_exc())`
   - 增加连续空结果熔断：连续100块零事件→FATAL退出
   - 初始化时验证事件签名有效性
   - 所有处理循环必须记录成功/失败计数

## 与已记录故障模式的关联

- **全吞异常处理循环**新模式的典型案例 — `except: continue` 使根因完全不可见
- 不同于「无声依赖失败」（文件缺失）— 这里是事件签名不匹配但无文件依赖
- 不同于「API僵尸」（日志全是错误）— 这里日志完全没有错误
- 独特性：**进程完全健康、日志完全正常、产出完全为零**
