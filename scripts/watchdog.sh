#!/bin/bash
# 暗黑星火看门狗 - 每5分钟检查虚拟盘引擎
# 死掉则自动重启
LOG="/home/admin/charon/bot_logs/paper_engine.log"
PID_FILE="/home/admin/charon/virtual_state/engine.pid"
SCRIPT="/home/admin/charon/scripts/paper_engine_v1.py"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        exit 0
    fi
fi

# 进程不在,启动
cd /home/admin/charon
nohup python3 -u "$SCRIPT" >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"
echo "[$(date)] 看门狗重启虚拟盘引擎 PID=$!" >> "$LOG"
