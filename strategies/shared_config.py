#!/usr/bin/env python3
"""
暗黑星火 共享配置加载器
========================================
所有4个策略通过此模块读取 GPT 决策参数，实现热加载。

用法:
  from shared_config import load_strategy_params
  params = load_strategy_params('meanrevert_paper')
  if params: RSI_OVERSOLD = params['rsi_oversold']

配置来源优先级:
  1. shared_config.json (GPT每6h写入) — 动态参数
  2. 代码内硬编码默认值 — 兜底
"""
import os, json, time
from pathlib import Path

CONFIG_FILE = Path('/home/admin/charon/bot_logs/shared_config.json')
LAST_MTIME = 0
CACHE = {}

def load_strategy_params(name):
    """
    读取指定策略的GPT决策参数。
    返回 dict 或 None (使用代码默认值)
    """
    global LAST_MTIME, CACHE
    
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
        if mtime != LAST_MTIME:
            CACHE = json.load(open(CONFIG_FILE))
            LAST_MTIME = mtime
    except:
        if not CACHE:
            return None
    
    # 从策略配置中查找
    if 'strategies' in CACHE and name in CACHE['strategies']:
        return CACHE['strategies'][name]
    if 'futures' in CACHE and name in CACHE['futures']:
        return CACHE['futures'][name]
    
    return None

def get_risk_limits():
    """读取风险控制参数"""
    try:
        config = get_config()
        return config.get('risk_control', {})
    except:
        return {}

def get_regime():
    """读取硬规则周期判断"""
    try:
        config = get_config()
        return config.get('regime', 'unknown')  # regime is now hard-coded, not GPT-parsed
    except:
        return 'unknown'

def get_config():
    global LAST_MTIME, CACHE
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
        if mtime != LAST_MTIME:
            CACHE = json.load(open(CONFIG_FILE))
            LAST_MTIME = mtime
    except:
        pass
    return CACHE

def get_market_assessment():
    """读取GPT对当前市场的判断"""
    config = get_config()
    return config.get('market_assessment', 'unknown')
