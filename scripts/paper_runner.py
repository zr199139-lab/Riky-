#!/usr/bin/env python3
"""
暗黑星火 · 虚拟盘持续运行器
每15分钟跑一次paper_spot_trader.py，检测到BUY/SELL时输出到stdout
"""
import subprocess, time, os, sys
from pathlib import Path

LOG_FILE = Path('/home/admin/charon/bot_logs/paper_spot_trader.log')
SCRIPT = '/home/admin/charon/scripts/paper_spot_trader.py'
LAST_SIZE_FILE = Path('/tmp/paper_last_size')

def get_log_size():
    return LOG_FILE.stat().st_size if LOG_FILE.exists() else 0

def check_new_actions(last_size):
    if not LOG_FILE.exists():
        return None, last_size
    current_size = get_log_size()
    if current_size <= last_size:
        return None, current_size
    
    with open(LOG_FILE, 'r') as f:
        f.seek(last_size)
        new_content = f.read()
    
    actions = []
    for line in new_content.split('\n'):
        if '[SPOT] BUY' in line or '[SPOT] SELL' in line:
            actions.append(line.strip())
    
    return actions, current_size

def main():
    last_size = LAST_SIZE_FILE.read_text().strip() if LAST_SIZE_FILE.exists() else '0'
    last_size = int(last_size) if last_size.isdigit() else 0
    
    # 运行一轮
    result = subprocess.run(
        ['python3', SCRIPT],
        cwd='/home/admin/charon',
        capture_output=True, text=True, timeout=60
    )
    
    # 检查新动作
    actions, new_size = check_new_actions(last_size)
    
    # 保存当前大小
    LAST_SIZE_FILE.write_text(str(new_size))
    
    if actions:
        for a in actions:
            print(a, flush=True)
        return True
    return False

if __name__ == '__main__':
    had_action = main()
    if not had_action:
        # Silent exit - nothing to report
        pass
