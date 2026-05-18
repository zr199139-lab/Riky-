#!/usr/bin/env python3
"""
暗黑星火 · GPT决策引擎
=======================
所有交易决策统一走GPT，不依赖内置指标规则。
combo31/futures_paper/meanrevert/rsi_meanrev 都通过此引擎获取信号。

调用方式: python3 gpt_decision.py "ETH/USDT short" "当前市场数据..."
返回: JSON {action, entry, stop_loss, take_profit, reason}
"""
import json, requests, os, sys
from datetime import datetime

# GPT API配置
API_KEY = 'sk-BLzmIrUAOsZOpwUPf1IuILbxnyaq0bitkntL3aHiEIO29mtL'
API_URL = 'https://vip.aipro.love/v1/chat/completions'
MODEL = 'gpt-5.5'

def gpt_decide(symbol, market_data, strategy_type, position=None):
    """GPT做交易决策"""
    
    pos_context = ""
    if position:
        pos_context = f"""
当前持仓:
  方向: {position.get('side', '无')}
  入场价: ${position.get('entry', 0)}
  当前浮盈: ${position.get('unrealized_pnl', 0)}
  已持有时长: {position.get('hold_hours', 0)}小时
"""
    
    prompt = f"""你是一个加密货币量化交易系统的决策AI。当前时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}。

你的角色: 根据市场数据决定是否开仓/平仓/持仓不动。

策略类型: {strategy_type} (spot=现货1x, futures=合约5x)
交易标的: {symbol}

当前市场数据:
{json.dumps(market_data, indent=2, ensure_ascii=False)}
{pos_context}

请做出决策。返回JSON格式:
{{
    "action": "open_short" | "open_long" | "close" | "hold",
    "confidence": 1-10,
    "reason": "100字以内中文解释",
    "sl_pct": 止损百分比(0.5-3.0),
    "tp_pct": 止盈百分比(1.0-6.0),
    "position_size_pct": 仓位比例(0.1-0.5)
}}

规则:
- 当前空头趋势，做空优先级高于做多
- 现货不做空，只做多
- 合约可以做多做空
- 止损必须设，不低于0.5%
- 每笔交易要有明确的理由
- 没有明确信号就hold
"""
    
    try:
        r = requests.post(API_URL, json={
            'model': MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 500,
            'temperature': 0.3,
            'response_format': {'type': 'json_object'}
        }, headers={
            'Authorization': f'Bearer {API_KEY}',
            'Content-Type': 'application/json'
        }, timeout=30)
        
        if r.status_code != 200:
            return {'action': 'hold', 'reason': f'GPT API error: {r.status_code}', 'confidence': 0}
        
        result = r.json()
        content = result['choices'][0]['message']['content']
        return json.loads(content)
    
    except Exception as e:
        return {'action': 'hold', 'reason': f'GPT error: {str(e)}', 'confidence': 0}

if __name__ == '__main__':
    # 测试
    test_data = {
        'price': 2115.29,
        'rsi_1h': 52.6,
        'ema20': 2169.58,
        'ema50': 2188.43,
        'atr_pct': 0.77,
        'funding_rate': 0.0,
        'trend': 'down',
        'volume_ratio': 0.85
    }
    result = gpt_decide('ETH/USDT', test_data, 'futures')
    print(json.dumps(result, indent=2, ensure_ascii=False))
