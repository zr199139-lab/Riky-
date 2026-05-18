#!/bin/bash
# 暗黑星火 → evolver 内存数据管道
# 每10分钟由cron调用，把策略性能写入 ./memory/ 供evolver分析
# Usage: bash scripts/write_memory.sh

cd /home/admin/charon || exit 1

MEMORY_DIR="./memory"
LOGS_DIR="./bot_logs"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

mkdir -p "$MEMORY_DIR/logs" "$MEMORY_DIR/signals"

# ── 1. 写入策略性能快照 ──
PERF_FILE="$MEMORY_DIR/strategy_performance.json"
echo '{' > "$PERF_FILE"
echo "  \"timestamp\": \"$TIMESTAMP\"," >> "$PERF_FILE"
echo '  "strategies": {' >> "$PERF_FILE"

FIRST=true
for state_file in "$LOGS_DIR"/*_state.json; do
    [ -f "$state_file" ] || continue
    name=$(basename "$state_file" _state.json)
    
    # 解析 state
    pnl=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('pnl',0))" 2>/dev/null || echo 0)
    trades=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('trades',0))" 2>/dev/null || echo 0)
    cash=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('cash',1000))" 2>/dev/null || echo 1000)
    fees=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('fees_paid',0))" 2>/dev/null || echo 0)
    
    # 检查是否有持仓
    pos_check=$(python3 -c "
import json
d=json.load(open('$state_file'))
pos = d.get('position')
if pos and pos.get('qty',0) > 0:
    print('true')
else:
    positions = d.get('positions',{})
    if any(p.get('qty',0) > 0 for p in positions.values()):
        print('true')
    else:
        print('false')
" 2>/dev/null || echo "false")
    
    $FIRST || echo "," >> "$PERF_FILE"
    FIRST=false
    
    cat >> "$PERF_FILE" << PERF_ENTRY
    "$name": {
      "pnl": $pnl,
      "trades": $trades,
      "cash": $cash,
      "fees_paid": $fees,
      "has_position": $pos_check,
      "updated_at": "$TIMESTAMP"
    }
PERF_ENTRY
done

echo '  }' >> "$PERF_FILE"
echo '}' >> "$PERF_FILE"

# ── 2. 写入运行时日志（evolver扫描信号用） ──
LOG_FILE="$MEMORY_DIR/logs/runtime_$TIMESTAMP.log"
{
    echo "[$TIMESTAMP] === 暗黑星火 策略运行报告 ==="
    
    # 汇总
    total_pnl=$(python3 -c "import json; d=json.load(open('$PERF_FILE')); print(sum(s['pnl'] for s in d['strategies'].values()))" 2>/dev/null || echo 0)
    total_trades=$(python3 -c "import json; d=json.load(open('$PERF_FILE')); print(sum(s['trades'] for s in d['strategies'].values()))" 2>/dev/null || echo 0)
    active=$(python3 -c "
import json
d=json.load(open('$PERF_FILE'))
count=0
for s in d['strategies'].values():
    if s['pnl'] != 0 or s['trades'] > 0:
        count+=1
print(count)
" 2>/dev/null || echo 0)
    
    echo "总PnL: \$${total_pnl}" | python3 -c "import sys; s=sys.stdin.read().strip(); print(s)" 2>/dev/null || echo "总PnL: \$$total_pnl"
    echo "总交易: $total_trades"
    echo "活跃策略: $active"
    
    # 检查异常信号
    for state_file in "$LOGS_DIR"/*_state.json; do
        [ -f "$state_file" ] || continue
        name=$(basename "$state_file" _state.json)
        pnl=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('pnl',0))" 2>/dev/null || echo 0)
        
        # 负收益标记为 warning
        if [ "$(echo "$pnl < -5" | bc -l 2>/dev/null)" = "1" ]; then
            echo "[warning] $name: pnl=$pnl 亏损超过-5USDT"
        elif [ "$(echo "$pnl < 0" | bc -l 2>/dev/null)" = "1" ]; then
            echo "[info] $name: pnl=$pnl 小幅亏损"
        elif [ "$(echo "$pnl > 10" | bc -l 2>/dev/null)" = "1" ]; then
            echo "[success] $name: pnl=$pnl 盈利超过+10USDT"
        fi
    done
    
    # 检查错误日志
    for log_file in "$LOGS_DIR"/*.log; do
        [ -f "$log_file" ] || continue
        name=$(basename "$log_file" .log)
        errors=$(grep -ci "error\|exception\|traceback\|failed" "$log_file" 2>/dev/null || echo 0)
        if [ "$errors" -gt 0 ]; then
            echo "[error] $name: 发现 $errors 个错误"
        fi
    done
    
    echo "[$TIMESTAMP] === 报告结束 ==="
} > "$LOG_FILE" 2>/dev/null

# ── 3. 写入信号摘要（evolver用signals_match匹配） ──
SIGNAL_FILE="$MEMORY_DIR/signals/current.json"
echo '[' > "$SIGNAL_FILE"
FIRST=true

# 检查各信号类型
for state_file in "$LOGS_DIR"/*_state.json; do
    [ -f "$state_file" ] || continue
    name=$(basename "$state_file" _state.json)
    pnl=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('pnl',0))" 2>/dev/null || echo 0)
    trades=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('trades',0))" 2>/dev/null || echo 0)
    
    if [ "$(echo "$pnl < -5" | bc -l 2>/dev/null)" = "1" ]; then
        $FIRST || echo "," >> "$SIGNAL_FILE"
        FIRST=false
        echo "{\"signal\": \"unstable\", \"source\": \"$name\", \"value\": $pnl, \"desc\": \"亏损>5USDT\"}" >> "$SIGNAL_FILE"
    fi
    if [ "$(echo "$trades == 0" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$pnl == 0" | bc -l 2>/dev/null)" = "1" ]; then
        $FIRST || echo "," >> "$SIGNAL_FILE"
        FIRST=false
        echo "{\"signal\": \"idle\", \"source\": \"$name\", \"value\": 0, \"desc\": \"0交易0盈亏\"}" >> "$SIGNAL_FILE"
    fi
    if [ "$(echo "$pnl > 10" | bc -l 2>/dev/null)" = "1" ]; then
        $FIRST || echo "," >> "$SIGNAL_FILE"
        FIRST=false
        echo "{\"signal\": \"success\", \"source\": \"$name\", \"value\": $pnl, \"desc\": \"盈利>10USDT\"}" >> "$SIGNAL_FILE"
    fi
done

# 检查error信号
error_count=0
for log_file in "$LOGS_DIR"/*.log; do
    [ -f "$log_file" ] || continue
    ec=$(grep -ci "error\|exception\|traceback\|failed" "$log_file" 2>/dev/null || echo "0")
    error_count=$((error_count + ${ec:-0}))
done
if [ "$error_count" -gt 0 ]; then
    $FIRST || echo "," >> "$SIGNAL_FILE"
    FIRST=false
    echo "{\"signal\": \"error\", \"source\": \"system\", \"value\": $error_count, \"desc\": \"共有$error_count个错误\"}" >> "$SIGNAL_FILE"
fi

echo ']' >> "$SIGNAL_FILE"

# 清理旧日志（保留最近50条）
ls -t "$MEMORY_DIR/logs/" 2>/dev/null | tail -n +51 | while read f; do rm -f "$MEMORY_DIR/logs/$f"; done

echo "✅ memory已更新: $TIMESTAMP | $(python3 -c "import json; d=json.load(open('$PERF_FILE')); print(len(d['strategies']),'策略')" 2>/dev/null)"
