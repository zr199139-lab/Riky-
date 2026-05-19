#!/bin/bash
# 暗黑星火 · 虚拟盘监控器
# 每15分钟跑一次，只汇报BUY/SELL动作

BOT_LOG="/home/admin/charon/bot_logs/paper_spot_trader.log"
SCRIPT="/home/admin/charon/scripts/paper_spot_trader.py"
LAST_LINE_FILE="/tmp/paper_last_line"

# 记录当前日志行数作为起点
if [ -f "$LAST_LINE_FILE" ]; then
    LAST_LINE=$(cat "$LAST_LINE_FILE")
else
    LAST_LINE=0
fi

# 运行一轮
cd /home/admin/charon && python3 "$SCRIPT" > /dev/null 2>&1

# 检查新日志中是否有BUY或SELL
NEW_LINES=$(wc -l < "$BOT_LOG")
if [ "$NEW_LINES" -gt "$LAST_LINE" ]; then
    ACTIONS=$(tail -n +$((LAST_LINE+1)) "$BOT_LOG" | grep -E "\[SPOT\] (BUY|SELL)")
    if [ -n "$ACTIONS" ]; then
        echo "$ACTIONS"
    fi
fi

echo "$NEW_LINES" > "$LAST_LINE_FILE"
