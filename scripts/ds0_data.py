#!/usr/bin/env python3
"""
DS-0 全托管数据采集器
每15分钟由cron调用，采集所有数据供我分析决策
输出: bot_logs/ds0_data.json
"""
import os, sys, json, time, hashlib, base64, hmac
from datetime import datetime
from pathlib import Path
import requests

BASE = Path('/home/admin/charon')
LOGS = BASE / 'bot_logs'
SECURE = Path(os.path.expanduser('~/.hermes/mempalace/secure'))

def decrypt_creds():
    from cryptography.fernet import Fernet
    with open(f'{SECURE}/.key','rb') as f: salt=f.read()
    key=base64.urlsafe_b64encode(hashlib.sha256(salt).digest())
    return json.loads(Fernet(key).decrypt(open(f'{SECURE}/credentials.enc','rb').read()).decode())

def load_state(name):
    try: return json.load(open(LOGS / f'{name}_state.json'))
    except: return {}

def main():
    data = {'ts': datetime.now().isoformat(), 'ts_epoch': time.time()}
    creds = decrypt_creds()
    ak, sk = creds['BINANCE_API_KEY'], creds['BINANCE_API_SECRET']
    
    # ── 市场数据 ──
    ts = int(time.time() * 1000)
    sig = hmac.new(sk.encode(), f'timestamp={ts}'.encode(), hashlib.sha256).hexdigest()
    headers = {'X-MBX-APIKEY': ak}
    
    # 合约账户
    r = requests.get(f'https://fapi.binance.com/fapi/v2/account?timestamp={ts}&signature={sig}', headers=headers)
    if r.status_code == 200:
        a = r.json()
        data['futures'] = {
            'wallet': float(a['totalWalletBalance']),
            'upnl': float(a['totalUnrealizedProfit']),
            'margin': float(a['totalMarginBalance']),
            'available': float(a['availableBalance']),
            'position_margin': float(a['totalPositionInitialMargin'])
        }
    else:
        data['futures'] = {'error': r.text}
    
    # 合约持仓
    r2 = requests.get(f'https://fapi.binance.com/fapi/v2/positionRisk?timestamp={ts}&signature={sig}', headers=headers)
    if r2.status_code == 200:
        positions = []
        for p in r2.json():
            amt = abs(float(p.get('positionAmt', '0') or 0))
            if amt > 0.001:
                positions.append({
                    'symbol': p['symbol'], 'side': 'LONG' if float(p['positionAmt']) > 0 else 'SHORT',
                    'amount': amt, 'entry': float(p['entryPrice']), 'mark': float(p['markPrice']),
                    'upnl': float(p['unRealizedProfit']), 'liq': float(p.get('liquidationPrice', 0)),
                    'margin': float(p['isolatedWallet']), 'leverage': int(p['leverage'])
                })
        data['positions'] = positions
    else:
        data['positions'] = []
    
    # 现货账户
    ex_spot = __import__('ccxt').binance({'apiKey': ak, 'secret': sk, 'enableRateLimit': True})
    try:
        sb = ex_spot.fetch_balance()
        usdt = float(sb['total'].get('USDT', 0))
        coins = {k: float(v) for k, v in sb['total'].items() if float(v) > 0.001 and k != 'USDT'}
        spot_val = usdt
        for c, amt in coins.items():
            try:
                t = ex_spot.fetch_ticker(f'{c}/USDT')
                spot_val += amt * t['last']
            except: pass
        data['spot'] = {'usdt': usdt, 'coins': len(coins), 'total_equity': round(spot_val, 2)}
    except Exception as e:
        data['spot'] = {'error': str(e)}
    
    # 市场价格
    try:
        prices = {}
        for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'BCH/USDT', 'XRP/USDT']:
            t = ex_spot.fetch_ticker(sym)
            prices[sym] = {'last': t['last'], 'high': t['high'], 'low': t['low'],
                          'change': t['percentage'], 'volume': t['baseVolume']}
        data['prices'] = prices
    except: data['prices'] = {}
    
    # ── 虚拟盘状态 ──
    papers = {}
    for name in ['meanrevert_paper', 'rsi_meanrev_paper', 'combo31_paper', 'futures_paper']:
        s = load_state(name)
        if s:
            papers[name] = {
                'cash': s.get('cash', 0),
                'pnl': s.get('pnl', 0),
                'trades': s.get('trades', 0),
                'daily_pnl': s.get('daily_pnl', 0),
                'has_position': bool(s.get('position')) or bool(s.get('positions')),
                'position_detail': s.get('position') or (list(s.get('positions', {}).values()) if s.get('positions') else None)
            }
    data['paper_strategies'] = papers
    
    # ── 当前配置 ──
    try:
        data['shared_config'] = json.load(open(LOGS / 'shared_config.json'))
    except: data['shared_config'] = {}
    try:
        data['advisory'] = json.load(open(LOGS / 'advisory.json'))
    except: data['advisory'] = {}
    
    # ── 情报数据(从intel.json读) ──
    try:
        data['intel'] = json.load(open(BASE / 'memory' / 'intel.json'))
    except: data['intel'] = {}
    
    # ── PnL状态判断 ──
    total_pnl = data['futures'].get('upnl', 0)
    for n, s in papers.items():
        total_pnl += s.get('pnl', 0) + s.get('daily_pnl', 0)
    data['total_pnl_status'] = 'loss' if total_pnl < 0 else 'profit'
    data['total_pnl'] = round(total_pnl, 2)
    
    # ── 硬规则周期 ──
    btc_chg = data.get('prices', {}).get('BTC/USDT', {}).get('change', 0) or 0
    if btc_chg < -2: data['regime'] = 'bearish'
    elif btc_chg > 2: data['regime'] = 'bullish'
    else: data['regime'] = 'sideways'
    data['btc_24h_change'] = btc_chg
    
    # ── 上次全盘决策时间 ──
    try:
        last = json.load(open(LOGS / 'ds0_last_decision.json'))
        data['last_full_decision'] = last.get('time', '')
        data['hours_since_decision'] = (time.time() - last.get('epoch', 0)) / 3600
    except:
        data['last_full_decision'] = ''
        data['hours_since_decision'] = 99
    
    # 写文件
    json.dump(data, open(LOGS / 'ds0_data.json', 'w'), indent=2)
    
    # 输出摘要供cron读取
    pnl_str = f"{'🔴亏损' if data['total_pnl_status']=='loss' else '🟢盈利'} \${abs(data['total_pnl']):.2f}"
    pos_str = f"{len(data.get('positions',[]))}个实盘仓"
    paper_str = f"{sum(1 for s in papers.values() if s.get('has_position'))}/{len(papers)}虚拟有仓"
    freq = '15min(亏损)' if data['total_pnl_status']=='loss' else '1h(盈利)'
    print(f"[DS-0] {datetime.now().strftime('%H:%M')} | {data['regime'].upper()} | {pnl_str} | {pos_str} | {paper_str} | 频率={freq}")

if __name__ == '__main__':
    main()
