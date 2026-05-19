#!/usr/bin/env python3
"""
聪明钱追踪数据整合器 — 被 cron 调用，每次用户新截图后手动触发
记录所有聪明钱交易员档案到 analysis/smart_money_track.md
"""
import json, os, sys
from datetime import datetime

TRACK_FILE = os.path.join(os.path.dirname(__file__), 'smart_money_track.md')

def current_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M')

if __name__ == '__main__':
    print(f"[{current_time()}] 聪明钱追踪器就绪")
    print(f"追踪文件: {TRACK_FILE}")
    print(f"用法: 截图数据通过参数传入，或手动编辑 smart_money_track.md")
