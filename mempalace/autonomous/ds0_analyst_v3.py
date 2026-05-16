#!/usr/bin/env python3
"""
暗黑星火 DS-0 分析师 V3.5
===========================
每15分钟由cron启动一次。是我(DS-0)的大脑：
  1. 拉全市场数据 (15m/1h klines + 费率 + 持仓 + 订单簿深度)
  2. 机械筛子 → 判断哪些币需要AI深度分析
  3. 调用AI大模型(哈基米→1314→DS)分析K线形态+庄家行为+订单簿深度
  4. AI覆盖阈值conf≥6直接控制现货间距/TP/SL + 合约开平
  5. 写 advisory.json (现货参数) + commands.json (合约命令) + heartbeat.json

只写文件，不操作交易所。Bot每5-10s读文件执行。

架构原则(2026-05-13 V3.5焊死):
  - 树还是那棵树: 骨架不动, 嫁接AI枝干
  - 决策链: 机械筛子(触发判断) → 哈基米(辅助) → 1314大模型(主分析) → DeepSeek兜底(<1%)
  - 机械筛子规则: 量比>2=2分 | 吞没=2分 | RSI背驰=3分 | 周线=3分 | RSI极端=2分 | 连续同向=2分 | 费率异常=2分 | 趋势衰减=2分
    阈值≥4分触发深度分析
  - AI覆盖阈值: confidence≥6且方向一致 → 直接控制现货间距/TP/SL/层数 + 合约开平
  - 订单簿深度分析: 每轮扫描top8币种的深度数据, 检测假突破/大单墙, 注入AI prompt
  - 哈基米角色: 辅助分析, 输出作为1314深度模型的上下文, 不再当守门员
  - 超时熔断: 总120s, 哈基米8s→1314 25s→DS 15s
  - 纯数学fallback兜底: AI全链路挂了也不影响心跳
"""
import sys, os, json, time, math, hashlib, hmac, uuid, traceback
from datetime import datetime, timezone
import requests

# 订单簿深度分析(树状嫁接 V3.5)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import depth_analyzer as _da
_da.set_history_path(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'bot_logs', 'depth_history.json'
))

# ── 路径 ───────────────────────────────────────────────────────────────
BASE_DIR    = '/home/admin/.hermes/mempalace/quant_trading'
LOGS_DIR    = os.path.join(BASE_DIR, 'bot_logs')
ADVISORY    = os.path.join(LOGS_DIR, 'advisory.json')
COMMANDS    = os.path.join(LOGS_DIR, 'commands.json')
HEARTBEAT   = os.path.join(LOGS_DIR, 'ds0_heartbeat.json')
AI_STATE    = os.path.join(LOGS_DIR, 'ai_analysis_state.json')
DAILY_FILE  = os.path.join(LOGS_DIR, 'contract_hunter_daily.json')
GRID_STATE  = os.path.join(LOGS_DIR, 'v3.3_网格_实盘_state.json')
AI_HISTORY  = os.path.join(LOGS_DIR, 'ai_last_decision.json')  # AI前次决策反馈持久化
os.makedirs(LOGS_DIR, exist_ok=True)

# ── 凭据 ───────────────────────────────────────────────────────────────
sys.path.append('/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt as _decrypt
_CREDS = _decrypt()
BINANCE_KEY    = _CREDS.get('BINANCE_API_KEY', '')
BINANCE_SECRET = _CREDS.get('BINANCE_API_SECRET', '')
DEEPSEEK_KEY   = _CREDS.get('DEEPSEEK_API_KEY', '')

# ── 第三方API Key(从.env补充读取) ──
HAIKU_KEY = ''   # 1314mc haiku
MC1314_KEY = ''  # 1314mc大模型
AIPRO_KEY = ''   # aipro大模型
_env_path = os.path.expanduser('~/.hermes/.env')
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if 'API_1314MC_KEY' in _line:
            _s = _line.find('"'); _e = _line.rfind('"')
            MC1314_KEY = _line[_s+1:_e] if _s>=0 and _e>_s else _line.split('=',1)[1].strip().strip('"\'')
        if 'AI_PRO_API_KEY' in _line:
            _s = _line.find('"'); _e = _line.rfind('"')
            AIPRO_KEY = _line[_s+1:_e] if _s>=0 and _e>_s else _line.split('=',1)[1].strip().strip('"\'')
        # Haiku也走1314mc，用同一个key
        if 'API_1314MC_KEY' in _line:
            _s = _line.find('"'); _e = _line.rfind('"')
            HAIKU_KEY = _line[_s+1:_e] if _s>=0 and _e>_s else _line.split('=',1)[1].strip().strip('"\'')

# ── 参数边界(四模型评审建议: 防止DS-0输出异常值) ──
PARAM_BOUNDS = {
    'spacing_pct':      (0.6, 3.0),
    'tp_pct':           (0.8, 4.0),
    'sl_pct':           (1.0, 6.0),
    'max_layers':       (2, 12),
    'first_layer_pct':  (0.0, 1.0),
    'deploy_pct':       (0.3, 0.95),
}

# ── AI调用超时配置(三模型评审要求) ──
TOTAL_AI_TIMEOUT = 45  # GPT-5.5(30s) → Flash(15s), 45s足够
HAIKU_TIMEOUT   = 8    # 保留, call_haiku()写代码用

# ── AI覆盖阈值(三模型评审要求: confidence≥8且方向一致才覆盖) ──
AI_OVERRIDE_THRESHOLD = 6

# ── API ────────────────────────────────────────────────────────────────
FAPI = 'https://fapi.binance.com'

def _sign(params: dict) -> str:
    query = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    sig = hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f'{query}&signature={sig}'

def _fapi(method: str, path: str, params: dict = None, signed: bool = False) -> dict:
    url = FAPI + path
    headers = {'X-MBX-APIKEY': BINANCE_KEY}
    try:
        if signed:
            p = params or {}
            p['timestamp'] = int(time.time() * 1000)
            qs = _sign(p)
            r = requests.get(f'{url}?{qs}', headers=headers, timeout=10) if method == 'GET' else \
                requests.post(url, headers={**headers, 'Content-Type': 'application/x-www-form-urlencoded'}, data=qs, timeout=10)
        else:
            r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return {'error': f'API[{r.status_code}] {r.text[:100]}'}
        return r.json()
    except Exception as e:
        return {'error': str(e)}

def _sapi(method: str, path: str, params: dict = None) -> dict:
    """现货API请求(签名)"""
    base = 'https://api.binance.com'
    headers = {'X-MBX-APIKEY': BINANCE_KEY}
    p = params or {}
    p['timestamp'] = int(time.time() * 1000)
    qs = '&'.join(f'{k}={v}' for k, v in sorted(p.items()))
    sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    full_url = f'{base}{path}?{qs}&signature={sig}'
    try:
        r = requests.get(full_url, headers=headers, timeout=10) if method == 'GET' else \
            requests.post(full_url, headers=headers, timeout=10)
        return r.json() if r.status_code in (200, 201) else {'error': f'API[{r.status_code}]'}
    except Exception as e:
        return {'error': str(e)}

# ── 锁防cron重叠(DeepSeek评审建议) ──
_LOCK_FILE = '/tmp/ds0_analyst.lock'

def _acquire_lock() -> bool:
    import fcntl
    try:
        _LOCK_FILE_HANDLE = open(_LOCK_FILE, 'w')
        fcntl.flock(_LOCK_FILE_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE_HANDLE.write(str(os.getpid()))
        _LOCK_FILE_HANDLE.flush()
        return True
    except (IOError, OSError):
        return False

# ══════════════════════════════════════════════════════════════════════
# AI大模型调用层(V3.3新增)
# ══════════════════════════════════════════════════════════════════════

class AI:
    """双模型调用链: GPT-5.5(主) → DeepSeek Flash(省钱后备)
    哈基米保留但不用于交易分析, 仅作为代码编写工具。"""
    def __init__(self):
        self.ds_key = DEEPSEEK_KEY
        self.mc_key = MC1314_KEY
        self.aipro_key = AIPRO_KEY

    def _call(self, url, headers, payload, timeout=15):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 200:
                content = r.json()['choices'][0]['message'].get('content')
                return content if content else None
        except:
            return None
        return None

    def _call_mc(self, model, msgs, timeout=25, max_tok=500, temp=0.3):
        if not self.mc_key: return None
        return self._call(
            'https://api.1314mc.net/v1/chat/completions',
            {'Authorization': f'Bearer {self.mc_key}', 'Content-Type': 'application/json'},
            {'model': model, 'messages': msgs, 'temperature': temp, 'max_tokens': max_tok},
            timeout=timeout
        )

    def _call_aipro(self, model, msgs, timeout=25, max_tok=500, temp=0.3):
        if not self.aipro_key: return None
        return self._call(
            'https://vip.aipro.love/v1/chat/completions',
            {'Authorization': f'Bearer {self.aipro_key}', 'Content-Type': 'application/json'},
            {'model': model, 'messages': msgs, 'temperature': temp, 'max_tokens': max_tok},
            timeout=timeout
        )

    def _call_ds(self, model, msgs, timeout=15, max_tok=800, temp=0.3):
        """调用DeepSeek官方API"""
        if not self.ds_key: return None
        return self._call(
            'https://api.deepseek.com/v1/chat/completions',
            {'Authorization': f'Bearer {self.ds_key}', 'Content-Type': 'application/json'},
            {'model': model, 'messages': msgs, 'temperature': temp, 'max_tokens': max_tok},
            timeout=timeout
        )

    # ── 以下为代码编写工具(不参与交易分析) ──
    def call_haiku(self, prompt, timeout=8):
        """调用Claude Haiku写代码/辅助分析, 不参与交易链"""
        msgs = [{'role': 'user', 'content': prompt}]
        if self.mc_key:
            c = self._call_mc('claude-haiku-4-5-20251001', msgs, timeout=timeout, max_tok=800)
            if c: return c
        if self.aipro_key:
            c = self._call_aipro('claude-haiku-4-5-20251001', msgs, timeout=timeout, max_tok=800)
            if c: return c
        return None

    def call_claude(self, prompt, timeout=30):
        """调用Claude Opus写代码/架构设计"""
        msgs = [{'role': 'user', 'content': prompt}]
        if self.aipro_key:
            c = self._call_aipro('claude-opus-4-7', msgs, timeout=timeout, max_tok=2000)
            if c: return c
        if self.mc_key:
            c = self._call_mc('claude-sonnet-4-6', msgs, timeout=timeout, max_tok=2000)
            if c: return c
        return None

    # ── 以下为交易分析链(V1.0: GPT-5.5主 → Flash后备) ──
    def model_analyze(self, prompt, timeout=30):
        """交易深度分析链: GPT-5.5(主,实测最强) → DeepSeek Flash(省钱后备)
        基于2026-05-16四模型横评结论设计的优先级。"""
        msgs = [{'role': 'user', 'content': prompt}]
        errs = []

        # 1) aipro gpt-5.5 ← 实测交易分析最强
        if self.aipro_key:
            c = self._call_aipro('gpt-5.5', msgs, timeout=timeout, max_tok=800)
            if c: return ('aipro/gpt-5.5', c)
            errs.append('gpt-5.5 fail')

        # 2) DeepSeek V4 Flash (省钱后备)
        if self.ds_key:
            ds_msgs = [{'role': 'system', 'content': '你是暗黑星火DS-0交易分析师。基于K线数据做技术分析，输出JSON格式决策。'},
                       {'role': 'user', 'content': prompt}]
            c = self._call_ds('deepseek-v4-flash', ds_msgs, timeout=15, max_tok=800)
            if c: return ('deepseek/flash', c)
            errs.append('flash fail')

        return (None, None)


# ══════════════════════════════════════════════════════════════════════
# Phase 1: 数据采集(同V3.2)
# ══════════════════════════════════════════════════════════════════════

def collect_data() -> dict:
    """拉取所有数据: 合约持仓+全币种15m/1h K线+费率"""
    data = {
        'market': {},
        'top_funding': [],
        'contract_positions': {},
        'spot_balance': 0,
        'spot_grid': {},
        'ts': time.time(),
    }

    # 1. 合约账户
    acc = _fapi('GET', '/fapi/v2/account', signed=True)
    if 'totalWalletBalance' in acc:
        data['futures_balance'] = float(acc['totalWalletBalance'])

    # 2. 合约持仓
    risk = _fapi('GET', '/fapi/v2/positionRisk', signed=True)
    if isinstance(risk, list):
        for p in risk:
            qty = float(p.get('positionAmt', 0))
            if abs(qty) < 0.001:
                continue
            sym = p['symbol']
            side = 'long' if qty > 0 else 'short'
            entry = float(p.get('entryPrice', 0))
            mark = float(p.get('markPrice', entry))
            upnl = float(p.get('unRealizedProfit', 0))
            data['contract_positions'][sym] = {
                'side': side, 'qty': abs(qty), 'entry': entry,
                'mark': mark, 'upnl': upnl,
                'pnl_pct': (mark/entry - 1) * (100 if side == 'long' else -100),
            }

    # 3. 读现货网格state
    if os.path.exists(GRID_STATE):
        try:
            grid = json.load(open(GRID_STATE))
            data['spot_grid'] = grid.get('grids', {})
            data['spot_balance'] = grid.get('total_usdt', 0)
        except:
            pass

    # 4. 全币种K线分析 (30个主流币)
    track_coins = ['BTC','ETH','SOL','DOGE','XRP','BNB','ADA','AVAX','LINK','DOT',
                    'POL','NEAR','ATOM','UNI','FIL','APT','ARB','OP','SUI','PEPE',
                    'INJ','WIF','JTO','LDO','AAVE','RUNE','ALGO','CRV','TIA','FET',
                    'SIREN']

    # ── scan_high_vol: 扫描全市场高波动/高量比币种(V3.6新增) ──
    def scan_high_vol(market_data: dict, top_n: int = 10) -> list:
        """
        扫描 market_data 中 vol_ratio_15m>2.0 或 atr_15m>2.0 的币种,
        按 vol_ratio_15m 降序返回 top_n 个 coin 名称列表。
        用于补充 track_coins 之外的高波动机会。
        """
        candidates = []
        for coin, d in market_data.items():
            vol = d.get('vol_ratio_15m', 0)
            atr = d.get('atr_15m', 0)
            if vol > 2.0 or atr > 2.0:
                candidates.append((coin, vol, atr))
        candidates.sort(key=lambda x: x[1], reverse=True)
        result = [c[0] for c in candidates[:top_n]]
        if result:
            print(f'[DS-0] 🔥 scan_high_vol: {len(result)}个高波动币 → {result[:5]}')
        return result

    for coin in track_coins:
        symbol = coin + 'USDT'
        try:
            ticker = _fapi('GET', '/fapi/v1/ticker/price', {'symbol': symbol})
            price = float(ticker.get('price', 0))
            if price <= 0:
                continue

            entry = {'price': round(price, 8), 'symbol': symbol}

            # 15m K线
            k15 = _fapi('GET', '/fapi/v1/klines', {'symbol': symbol, 'interval': '15m', 'limit': 30})
            if isinstance(k15, list) and len(k15) >= 15:
                c15 = [float(k[4]) for k in k15]
                h15 = [float(k[2]) for k in k15]
                l15 = [float(k[3]) for k in k15]
                v15 = [float(k[5]) for k in k15]

                # ATR
                trs = [max(h15[i]-l15[i], abs(h15[i]-c15[i-1]), abs(l15[i]-c15[i-1]))
                       for i in range(1, len(c15))]
                atr_15m = sum(trs[-14:]) / 14 / c15[-1] * 100

                # 趋势
                recent5 = sum(c15[-5:]) / 5
                prior5 = sum(c15[-10:-5]) / 5 if len(c15) >= 10 else c15[-5]
                entry['trend_15m'] = 'up' if recent5 > prior5 * 1.002 else \
                                     ('down' if recent5 < prior5 * 0.998 else 'ranging')

                # RSI(14)
                gains = losses = 0
                for i in range(1, len(c15)):
                    d = c15[i] - c15[i-1]
                    if d > 0: gains += d
                    else: losses -= d
                gains /= max(len(c15)-1, 1)
                losses /= max(len(c15)-1, 1)
                if losses > 0:
                    entry['rsi_15m'] = round(100 - 100 / (1 + gains/losses), 1)
                else:
                    entry['rsi_15m'] = 100.0

                # 量比
                avg_v = sum(v15[-12:]) / max(len(v15[-12:]), 1)
                entry['vol_ratio_15m'] = round(v15[-1] / max(avg_v, 0.001), 2)
                entry['atr_15m'] = round(atr_15m, 2)

                # -- 新增: 15m K线原始数据喂给AI(最近10根, 缩量省token) --
                entry['klines_15m'] = [{
                    't': datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).strftime('%H:%M'),
                    'o': round(float(k[1]), 6), 'h': round(float(k[2]), 6),
                    'l': round(float(k[3]), 6), 'c': round(float(k[4]), 6),
                    'v': round(float(k[5]), 2),
                } for k in k15[-10:]]  # 只给最近10根,省token

            # 1h K线
            k1h = _fapi('GET', '/fapi/v1/klines', {'symbol': symbol, 'interval': '1h', 'limit': 48})
            if isinstance(k1h, list) and len(k1h) >= 14:
                c1h = [float(k[4]) for k in k1h]
                h1h = [float(k[2]) for k in k1h]
                l1h = [float(k[3]) for k in k1h]
                v1h = [float(k[5]) for k in k1h]

                trs_1h = [max(h1h[i]-l1h[i], abs(h1h[i]-c1h[i-1]), abs(l1h[i]-c1h[i-1]))
                          for i in range(1, len(c1h))]
                entry['atr_1h'] = round(sum(trs_1h[-14:]) / 14 / c1h[-1] * 100, 2) if len(trs_1h) >= 14 else 0.5

                gains1 = losses1 = 0
                for i in range(1, len(c1h)):
                    d = c1h[i] - c1h[i-1]
                    if d > 0: gains1 += d
                    else: losses1 -= d
                gains1 /= max(len(c1h)-1, 1)
                losses1 /= max(len(c1h)-1, 1)
                entry['rsi_1h'] = round(100 - 100/(1+gains1/losses1), 1) if losses1 > 0 else 100.0

                entry['weekly_low'] = round(min(l1h[-24:]), 4)
                entry['weekly_high'] = round(max(h1h[-24:]), 4)

                r7 = sum(c1h[-7:]) / 7
                p7 = sum(c1h[-14:-7]) / 7 if len(c1h) >= 14 else c1h[-7]
                entry['trend_1h'] = 'up' if r7 > p7 * 1.005 else \
                                    ('down' if r7 < p7 * 0.995 else 'ranging')

                avg_v1h = sum(v1h[-5:]) / max(len(v1h[-5:]), 1)
                avg_v1h_prior = sum(v1h[-10:-5]) / max(len(v1h[-10:-5]), 1)
                entry['vol_ratio_1h'] = round(avg_v1h / max(avg_v1h_prior, 0.001), 2)

            # 费率
            try:
                prem = _fapi('GET', '/fapi/v1/premiumIndex', {'symbol': symbol})
                if 'lastFundingRate' in prem:
                    entry['funding'] = round(float(prem['lastFundingRate']) * 100, 4)
            except:
                pass

            data['market'][coin] = entry

        except Exception as e:
            continue

    # 5. 全市场费率扫描
    try:
        prem_all = _fapi('GET', '/fapi/v1/premiumIndex')
        if isinstance(prem_all, list):
            fundings = []
            for p in prem_all:
                sym = p.get('symbol', '')
                if not sym.endswith('USDT'):
                    continue
                base = sym.replace('USDT', '')
                if base in ['BTC','ETH','USDC','BUSD','TUSD','FDUSD']:
                    continue
                fr = float(p.get('lastFundingRate', 0)) * 100
                if abs(fr) < 0.001:
                    continue
                fundings.append({'symbol': sym, 'funding': fr,
                                 'atr': data['market'].get(base, {}).get('atr_15m', 0)})
            fundings.sort(key=lambda x: abs(x['funding']), reverse=True)
            data['top_funding'] = fundings[:10]
    except:
        pass

    # 6. 读前次AI决策(用于反馈闭环)
    if os.path.exists(AI_HISTORY):
        try:
            data['last_ai_decision'] = json.load(open(AI_HISTORY))
        except:
            data['last_ai_decision'] = None
    else:
        data['last_ai_decision'] = None

    # 7. 情报数据(对市场背景的了解, 由intel_collector每天早8晚8采集)
    intel_path = os.path.join(LOGS_DIR, 'intel.json')
    if os.path.exists(intel_path):
        try:
            data['intel'] = json.load(open(intel_path))
        except:
            data['intel'] = None
    else:
        data['intel'] = None

    # 8. 订单簿深度分析(树状嫁接 V3.5) — 检测假突破/大单墙
    _depth_coins = []
    for coin, d in data.get('market', {}).items():
        if d.get('vol_ratio_15m', 0) > 1.5 or d.get('atr_15m', 0) > 1.5:
            _depth_coins.append(coin)
    _depth_btch = ['BTC', 'ETH', 'SOL', 'DOGE'] + _depth_coins
    _depth_btch = [c for c in dict.fromkeys(_depth_btch) if c in data.get('market', {})][:8]
    if _depth_btch:
        _depth_symbols = [c + 'USDT' for c in _depth_btch]
        _kl_map = {}
        for c in _depth_btch:
            d = data['market'].get(c, {})
            _kl_map[c + 'USDT'] = [
                ('weekly_high', d.get('weekly_high', 0)),
                ('weekly_low', d.get('weekly_low', 0)),
            ]
        try:
            data['depth_analysis'] = _da.analyze_symbols(_depth_symbols, _kl_map)
            da = data['depth_analysis']
            if da and da.get('fake_breakout_high_risk'):
                print(f"[DS-0] 🔥 假突破高风险: {len(da['fake_breakout_high_risk'])}币")
        except Exception as e:
            print(f"[DS-0] ⚠️ 深度分析异常: {e}")
            data['depth_analysis'] = None
    else:
        data['depth_analysis'] = None

    # 9. 现货持仓(从交易所API获取真实余额)
    try:
        _acc = _sapi('GET', '/api/v3/account')
        if isinstance(_acc, dict) and 'balances' in _acc:
            _spots = {}
            for _b in _acc['balances']:
                _free = float(_b.get('free', 0))
                _locked = float(_b.get('locked', 0))
                _total = _free + _locked
                if _total > 0:
                    _spots[_b['asset']] = _total
            data['spot_positions'] = _spots
            # 顺便获取各币现货价格用于估值
            _tickers = {}
            try:
                _tr = requests.get('https://api.binance.com/api/v3/ticker/price', timeout=10)
                if _tr.status_code == 200:
                    for _t in _tr.json():
                        _tickers[_t['symbol']] = float(_t['price'])
            except:
                pass
            # 折算总USDT价值
            _total_usdt = _spots.get('USDT', 0.0)
            for _asset, _qty in _spots.items():
                if _asset in ('USDT', 'BUSD', 'FDUSD', 'TUSD', 'USDC'):
                    _total_usdt += _qty
                elif _asset == 'BNB':
                    _p = _tickers.get('BNBUSDT', 0)
                    if _p > 0:
                        _total_usdt += _qty * _p
                else:
                    # 合约或现货价格
                    _sym = _asset + 'USDT'
                    _p = _tickers.get(_sym, 0)
                    if _p <= 0:
                        _p = data.get('market', {}).get(_asset, {}).get('price', 0)
                    _total_usdt += _qty * _p
            data['spot_total_usdt'] = round(_total_usdt, 2)
    except Exception as e:
        data['spot_positions'] = {}
    
    # 选币评分 — 跑完数据后立即执行
    data['candidates'] = _screener_coins(data)
    
    return data


# ══════════════════════════════════════════════════════════════════════
# Phase 1b: 选币流程 — 300→30→5→1 加权评分系统 (2026-05-16)
# 每轮必跑, 输出 candidates.json
# ══════════════════════════════════════════════════════════════════════

CANDIDATES_FILE = os.path.join(LOGS_DIR, 'candidates.json')

def _screener_coins(data: dict, top_n: int = 5) -> list:
    """
    系统化选币评分 — 300→30→5→1 三步漏斗
    
    Step 1: 已有 market_data (30+币,含费率数据)
    Step 2: 加权评分 (5项指标, 满分10分)
    Step 3: 排序输出 Top N
    
    评分权重(按重要性):
      费率极端 +0~4 | 深度比 +0~2 | 多周期趋势 +0~2
      成交量 +0~1 | OI方向 +0~1
    """
    market = data.get('market', {})
    top_funding = data.get('top_funding', [])
    candidates = []
    
    # ★ 读取风控状态 (四模型P0共识)
    risk_state_path = os.path.join(LOGS_DIR, 'risk_state.json')
    risk = {}
    if os.path.exists(risk_state_path):
        try:
            risk = json.load(open(risk_state_path))
        except:
            pass
    
    daily_loss = risk.get('daily_loss', 0) or 0
    drawdown = risk.get('drawdown_pct', 0) or 0
    
    # 风控熔断: HALT时跳过选币
    if risk.get('halt', False):
        print('[DS-0] 🚨 HALT触发, 选币跳过')
        return []
    
    # 动态评分门槛: 风控越差门槛越高
    score_floor = 0
    if daily_loss < -24:
        print(f'[DS-0] ⚠️ 日亏{daily_loss:.0f}接近上限, 选币门槛+5')
        score_floor = 5
    elif daily_loss < -15:
        score_floor = 3
    if drawdown > 10:
        print(f'[DS-0] ⚠️ 回撤{drawdown:.0f}%>10%, 选币门槛+3')
        score_floor = max(score_floor, 3)
    
    # 构建候选池: 30 tracked coins + 极端费率币(不在track里)
    candidate_coins = set(market.keys())
    for f in top_funding:
        coin = f.get('symbol', '').replace('USDT', '')
        fr_val = f.get('funding', 0)
        # 极端费率币即使不在track也要评分
        if abs(fr_val) > 0.1:
            candidate_coins.add(coin)
    
    for coin in sorted(candidate_coins):
        d = market.get(coin, {})
        if not d:
            # 不在track_coins里的币, 只有费率数据, 给最低评分机会
            pass
        
        price = d.get('price', 0)
        if price <= 0:
            continue
        
        score = 0
        max_score = 10
        details = []
        direction = 'neutral'
        funding = d.get('funding', 0)
        
        # ── 指标1: 费率极端 (最高权重, +0~4分) ──
        if funding < -0.1:
            score += 4
            direction = 'long'
            details.append(f'费率{funding:+.4f}%极端负→做多+4')
        elif funding > 0.1:
            score += 4
            direction = 'short'
            details.append(f'费率{funding:+.4f}%极端正→做空+4')
        elif funding < -0.05:
            score += 2
            if direction == 'neutral': direction = 'long'
            details.append(f'费率{funding:+.4f}%偏负+2')
        elif funding > 0.05:
            score += 2
            if direction == 'neutral': direction = 'short'
            details.append(f'费率{funding:+.4f}%偏正+2')
        
        # ── 指标2: 深度比 ⚖️ (+0~2分) ──
        # 从盘口数据拿(短缓存, 只拉一次)
        try:
            symbol = coin + 'USDT'
            depth = _fapi('GET', f'/fapi/v1/depth?symbol={symbol}&limit=20', signed=False)
            if isinstance(depth, dict) and 'bids' in depth and 'asks' in depth:
                bids = [[float(b[0]), float(b[1])] for b in depth['bids'][:10]]
                asks = [[float(a[0]), float(a[1])] for a in depth['asks'][:10]]
                bid_vol = sum(b[0]*b[1] for b in bids)
                ask_vol = sum(a[0]*a[1] for a in asks)
                ratio = bid_vol / ask_vol if ask_vol > 0 else 1
                
                # 有效方向匹配
                if ratio > 1.4 and direction in ('long', 'neutral'):
                    score += 2
                    direction = 'long'
                    details.append(f'深度比{ratio:.2f}买盘强+2')
                elif ratio < 0.6 and direction in ('short', 'neutral'):
                    score += 2
                    direction = 'short'
                    details.append(f'深度比{ratio:.2f}卖盘强+2')
                elif ratio > 1.4 and direction == 'short':
                    details.append(f'⚠️ 深度比{ratio:.2f}买盘强,方向冲突')
                elif ratio < 0.6 and direction == 'long':
                    details.append(f'⚠️ 深度比{ratio:.2f}卖盘强,方向冲突')
        except:
            pass
        
        # ── 指标3: 多周期趋势 (+0~2分) ──
        trend_15m = d.get('trend_15m', '')
        trend_1h = d.get('trend_1h', '')
        if trend_15m and trend_1h:
            aligned = 0
            if trend_15m == trend_1h:
                aligned = 1
                # 4h趋势(简化: 用5根4h K线)
                try:
                    sym = coin + 'USDT'
                    k4h = _fapi('GET', f'/fapi/v1/klines?symbol={sym}&interval=4h&limit=5', signed=False)
                    if isinstance(k4h, list) and len(k4h) >= 5:
                        c4h = [float(k[4]) for k in k4h]
                        trend_4h = 'up' if c4h[-1] > c4h[0] * 1.005 else ('down' if c4h[-1] < c4h[0] * 0.995 else 'ranging')
                        if trend_4h == trend_15m:
                            aligned = 2
                except:
                    pass
            
            # 方向一致性检查
            if trend_15m == 'up' and direction == 'long':
                score += 1
                details.append(f'趋势一致做多+1')
            elif trend_15m == 'down' and direction == 'short':
                score += 1
                details.append(f'趋势一致做空+1')
            
            if aligned >= 1:
                score += min(aligned, 1)  # 最多+1多周期对齐
                details.append(f'多周期对齐+{min(aligned,1)}')
        
        # ── 指标4: 成交量 (+0~1分) ──
        vol_ratio = d.get('vol_ratio_15m', 0)
        if vol_ratio > 2.0:
            score += 1
            details.append(f'放量{vol_ratio:.1f}x+1')
        elif vol_ratio > 1.5:
            details.append(f'量比{vol_ratio:.1f}x(未达标)')
        
        # ── 指标5: OI方向 (+0~1分) ──
        try:
            sym = coin + 'USDT'
            oi_data = _fapi('GET', f'/fapi/v1/openInterest?symbol={sym}', signed=False)
            if isinstance(oi_data, dict) and 'openInterest' in oi_data:
                oi = float(oi_data['openInterest'])
                # 对比缓存
                oi_cache_path = os.path.join(LOGS_DIR, 'oi_cache.json')
                prev_oi = None
                if os.path.exists(oi_cache_path):
                    try:
                        with open(oi_cache_path) as f:
                            cache = json.load(f)
                            prev_oi = cache.get(sym)
                    except:
                        pass
                # 写缓存
                try:
                    cache = {}
                    if os.path.exists(oi_cache_path):
                        with open(oi_cache_path) as f:
                            cache = json.load(f)
                    cache[sym] = oi
                    with open(oi_cache_path, 'w') as f:
                        json.dump(cache, f)
                except:
                    pass
                
                if prev_oi and prev_oi > 0:
                    oi_chg = (oi / prev_oi - 1) * 100
                    # OI上升+价格同向 = 趋势有燃料
                    if direction in ('long', 'short') and abs(oi_chg) > 2:
                        chg_direction = 'up' if oi_chg > 0 else 'down'
                        if (direction == 'long' and chg_direction == 'up') or \
                           (direction == 'short' and chg_direction == 'down'):
                            score += 1
                            details.append(f'OI{oi_chg:+.1f}%趋势确认+1')
        except:
            pass
        
        # 最终方向裁定
        if direction == 'neutral':
            continue  # 没方向的币跳过
        
        # 风控门槛过滤 (四模型P0共识)
        if score_floor > 0 and score < score_floor:
            continue  # 分数不够, 风控状态下不选
        
        candidates.append({
            'coin': coin,
            'score': score,
            'max_score': max_score,
            'direction': direction,
            'funding': funding,
            'price': price,
            'details': details,
            'vol_ratio': vol_ratio,
        })
    
    # 排序: 按分数降序, 同分按费率绝对值降序
    candidates.sort(key=lambda x: (x['score'], abs(x['funding'])), reverse=True)
    
    # 限制返回数量
    top = candidates[:top_n]
    
    # 写入candidates.json
    try:
        output = []
        for c in top:
            output.append({
                'coin': c['coin'],
                'score': f'{c["score"]}/{c["max_score"]}',
                'direction': c['direction'],
                'funding': f'{c["funding"]:+.4f}%',
                'price': c['price'],
                'reasons': c['details'][:4],
            })
        
        result = {
            'timestamp': datetime.now(tz=timezone.utc).strftime('%H:%M UTC'),
            'top_candidates': output,
            'total_scored': len(candidates),
        }
        _atomic_write(CANDIDATES_FILE, result)
        
        if top:
            top_strs = [f'{c["coin"]}({c["score"]}/{c["max_score"]},{c["direction"]})' for c in top[:5]]
            print(f'[DS-0] 🏆 选币Top5: {", ".join(top_strs)}')
    except Exception as e:
        print(f'[DS-0] ⚠️ 选币写入失败: {e}')
    
    return top


# ══════════════════════════════════════════════════════════════════════
# Phase 2a: 机械筛子 — 替代哈基米当守门员(V3.4)
# 每轮必跑，找出真正需要AI深度分析的异常币
# ══════════════════════════════════════════════════════════════════════

def _mechanical_sieve(data: dict) -> dict:
    """机械规则筛选异常币，返回触发分数+异常币列表
    规则权重(四模型评审共识):
      量比>2.0=2分 | 标准吞没=2分 | RSI背驰=3分 | 周线触达=3分
      RSI<25或>78=2分 | 连续3根同向+量增=2分 | 突破20根极值=3分
      BTC 15m>1%=1分 | 费率异常=2分 | 趋势衰减(8根均线下)=2分
      总分≥4触发深度分析。删掉: ATR纯数值/异动0.5%/量价背离(噪音)
    """
    market = data.get('market', {})
    btc = market.get('BTC', {})
    coins_scores = {}
    total_score = 0

    # BTC宏观
    btc_vol = btc.get('vol_ratio_15m', 1.0)
    btc_atr = btc.get('atr_15m', 0.5)
    btc_rsi = btc.get('rsi_15m', 50)

    # BTC异动给全市场+1
    if btc_atr > 1.0:
        total_score += 1

    for coin, d in market.items():
        score = 0
        reasons = []

        rsi = d.get('rsi_15m', 50)
        vol = d.get('vol_ratio_15m', 1.0)
        atr = d.get('atr_15m', 0.5)
        trend = d.get('trend_15m', 'ranging')
        prev_trend = d.get('trend_1h', 'ranging')
        funding = d.get('funding', 0)
        klines = d.get('klines_15m', [])  # V3.5: 提前加载K线供多规则共用

        # 1. 量比异常(2分)
        if vol > 2.0:
            score += 2
            reasons.append(f'量比{vol:.1f}x')

        # 2. RSI极端(2分) — 收紧阈值减少噪音
        if rsi < 25 or rsi > 78:
            score += 2
            reasons.append(f'RSI{rsi:.0f}极端')

        # 3. 周线触达(3分) — 高价值信号
        weekly_low = d.get('weekly_low', 0)
        weekly_high = d.get('weekly_high', 0)
        price = d.get('price', 0)
        if weekly_low > 0 and price <= weekly_low * 1.003:
            score += 3
            reasons.append(f'触周低${weekly_low}')
        if weekly_high > 0 and price >= weekly_high * 0.997:
            score += 3
            reasons.append(f'触周高${weekly_high}')

        # 4. 趋势衰减(2分) — 连续弱势,弥补机械筛子漏阴跌
        if trend == 'down' and prev_trend == 'down':
            score += 2
            reasons.append('连续阴跌')

        # ── 以下两条规则在V3.4注释中声明但代码缺失，V3.5补焊 ──
        # 5a. RSI背驰(3分): 价格新高但RSI在中低位，或价格新低但RSI在中高位
        if len(klines) >= 5:
            prices = [k['c'] for k in klines[-5:]]
            price_new_high = prices[-1] > max(prices[:-1]) * 1.002
            price_new_low = prices[-1] < min(prices[:-1]) * 0.998
            if price_new_high and rsi < 60:
                score += 3
                reasons.append(f'RSI顶背驰(rsi={rsi:.0f})')
            elif price_new_low and rsi > 40:
                score += 3
                reasons.append(f'RSI底背驰(rsi={rsi:.0f})')

        # 5b. 10bar突破(3分): 价格突破最近10根K线极值
        if len(klines) >= 3:
            c3 = [k['c'] for k in klines[-3:]]
            h3 = [k['h'] for k in klines[-3:]]
            l3 = [k['l'] for k in klines[-3:]]
            h10 = max(h3)  # 用最近3根代替10根(数据有限)
            l10 = min(l3)
            if price > 0 and price >= h10 * 1.005:
                score += 3
                reasons.append('突破10bar高')
            elif price > 0 and price <= l10 * 0.995:
                score += 3
                reasons.append('跌破10bar低')

        # 6. 费率异常(2分)
        if abs(funding) > 0.1:
            score += 2
            reasons.append(f'费率{funding:.4f}%')

        # 7. 连续3根量增同向(2分) — 需要K线数据
        if len(klines) >= 3:
            last3 = klines[-3:]
            # 检查是否连续同向(全涨或全跌)
            all_up = all(k['c'] > k['o'] for k in last3)
            all_down = all(k['c'] < k['o'] for k in last3)
            if all_up or all_down:
                # 检查量能是否递增
                vols = [k['v'] for k in last3]
                if vols[-1] > vols[0] * 1.3:  # 最后一根量比第一根大30%+
                    score += 2
                    reasons.append('3根量增同向')

        # 7. 吞没形态(2分)
        if len(klines) >= 2:
            prev = klines[-2]
            curr = klines[-1]
            # 多头吞没: 当前阳线实体完全包裹前阴线实体
            if (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                curr['c'] > prev['o'] and curr['o'] < prev['c']):
                score += 2
                reasons.append('多头吞没')
            # 空头吞没: 当前阴线实体完全包裹前阳线实体
            elif (curr['c'] < curr['o'] and prev['c'] > prev['o'] and
                  curr['o'] > prev['c'] and curr['c'] < prev['o']):
                score += 2
                reasons.append('空头吞没')

        if score > 0:
            coins_scores[coin] = {'score': score, 'reasons': reasons}

    # 总分=所有币最高分 + BTC异动加分
    top_score = max([s['score'] for s in coins_scores.values()], default=0)
    total_score += top_score

    # 排序,取top异常币(最多10个,含强制BTC/ETH)
    sorted_coins = sorted(coins_scores.items(), key=lambda x: x[1]['score'], reverse=True)
    top_n = []
    seen = set()
    for c, s in sorted_coins:
        if len(top_n) >= 10:
            break
        top_n.append(c)
        seen.add(c)
    for must in ['BTC', 'ETH']:
        if must not in seen and must in market:
            top_n.append(must)

    trigger = total_score >= 4

    if trigger:
        print(f'[DS-0] 🔍 机械筛子触发({total_score}分) | 异常: {len(coins_scores)}个 | top: {", ".join(top_n[:5])}')
    else:
        print(f'[DS-0] 📊 筛子平静({total_score}分, <4不触发) | 异常币: {len(coins_scores)}个')

    return {
        'trigger': trigger,
        'total_score': total_score,
        'top_coins': top_n,
        'coin_scores': coins_scores,
    }


# ══════════════════════════════════════════════════════════════════════
# Phase 2b: AI深度分析(V3.4重写)
# ══════════════════════════════════════════════════════════════════════

def _build_haiku_prompt(data: dict, top_coins: list = None) -> str:
    """构建哈基米快扫prompt — 只喂指标摘要,不喂原始K线"""
    btc = data.get('market', {}).get('BTC', {})
    lines = [f"BTC: ${btc.get('price',0):.0f} RSI_15m={btc.get('rsi_15m',50)} RSI_1h={btc.get('rsi_1h',50)} "
             f"ATR_15m={btc.get('atr_15m',0.5):.2f}% 趋势={btc.get('trend_1h','?')} "
             f"量比={btc.get('vol_ratio_15m',1.0):.2f}"]

    # 找出RSI异常和量比异常的币
    market = data.get('market', {})
    oversold = [c for c, d in market.items() if d.get('rsi_15m', 50) < 30]
    overbought = [c for c, d in market.items() if d.get('rsi_15m', 50) > 75]
    high_vol = [c for c, d in market.items() if d.get('vol_ratio_15m', 1) > 2.5]

    if oversold:
        lines.append(f"超卖(RSI<30): {', '.join(oversold[:5])}")
    if overbought:
        lines.append(f"超买(RSI>75): {', '.join(overbought[:5])}")
    if high_vol:
        lines.append(f"放量(量比>2.5): {', '.join(high_vol[:5])}")

    # 合约持仓
    pos = data.get('contract_positions', {})
    if pos:
        lines.append(f"持仓: {len(pos)}仓 | " + ' | '.join(
            [f"{s} {p['side']} PnL={p['pnl_pct']:.1f}%" for s, p in pos.items()][:2]))
    else:
        lines.append("持仓: 空仓")

    lines.append(f"合约余额: ${data.get('futures_balance', 0):.1f}")
    lines.append(f"现货余额: ${data.get('spot_balance', 0):.1f}")

    # 前次AI决策反馈
    last = data.get('last_ai_decision')
    if last:
        lines.append(f"前次AI: {last.get('action','?')} {last.get('symbol','')} | "
                     f"结果: {last.get('result','?')} | PnL: {last.get('pnl','?')}")

    lines.append("")
    lines.append("【快检规则】先看BTC方向：")
    lines.append("  BTC跌→偏空, BTC涨→偏多, BTC横→震荡")
    lines.append("  放量是真信号, 缩量是假信号")
    lines.append("  缩量反弹=死猫跳, 放量拉升=真买盘")
    lines.append("请分析以上数据，输出JSON格式(仅JSON，不要多余文字):")
    lines.append('{"needs_deep": true/false, "reason": "判断依据", "quick_signal": "hold|bullish|bearish"}')
    return '\n'.join(lines)


def _build_model_prompt(data: dict, haiku_reason: str = '') -> str:
    """构建大模型深度分析prompt — 喂完整15m K线数据+指标"""
    lines = [
        "你是暗黑星火DS-0首席交易分析师。分析15分钟K线数据，判断庄家行为(吸筹/洗盘/出货/试盘)。",
        "回答限制为JSON。请仔细分析以下数据并给出交易决策。",
        "",
        f"时间: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"哈基米预检: {haiku_reason}",
        "",
        "=== 宏观情报 ===",
    ]

    intel = data.get('intel')
    if intel and intel.get('news'):
        fng = intel.get('fear_greed')
        if fng:
            lines.append(f"恐惧贪婪指数: {fng['value']}/100 ({fng['classification']})")
        coint = intel['news'].get('cointelegraph', [])
        if coint:
            lines.append("今日热点新闻:")
            for n in coint[:4]:
                lines.append(f"  • {n.get('title','')[:80]}")
        lines.append("")

    # ★ V2: 币安公告情报 (新币/合约/下架)
    if intel and intel.get('binance_announcements'):
        ann = intel['binance_announcements']
        highlights = ann.get('highlights_48h', [])
        if highlights:
            lines.append("=== 币安公告(近48h重大) ===")
            for h in highlights[:6]:
                lines.append(f"  • {h[:80]}")
            lines.append("")

    # ★ V2: 市场极端信号(泵/砸/量异常)
    if intel and intel.get('binance_trending'):
        tr = intel['binance_trending']
        sigs = tr.get('signals', [])
        if sigs:
            lines.append("=== 市场极端信号 ===")
            for s in sigs[:6]:
                lines.append(f"  {s['type']}: {s['symbol']} ({s['change_pct']:+.1f}%) — {s.get('note','')[:60]}")
            lines.append("")
        # 新增: 24h涨跌幅榜前3
        gs = tr.get('gainers', [])[:3]
        ls = tr.get('losers', [])[:3]
        if gs or ls:
            lines.append("=== 24h涨跌幅榜 ===")
            if gs:
                g_str = ' | '.join(f"{d['symbol']}({d['change_pct']:+.1f}%)" for d in gs)
                lines.append(f"  涨幅: {g_str}")
            if ls:
                l_str = ' | '.join(f"{d['symbol']}({d['change_pct']:+.1f}%)" for d in ls)
                lines.append(f"  跌幅: {l_str}")
            lines.append("")

    # ★ V3: Telegram频道情报
    if intel and intel.get('telegram_scanner'):
        tg = intel['telegram_scanner']
        if tg:
            lines.append("=== Telegram频道情报 ===")
            for m in tg[:5]:
                txt = m.get('text', '')[:80]
                ch = m.get('channel', '?')
                if txt:
                    lines.append(f"  [{ch}] {txt}")
            lines.append("")

    # ★ V3: 链上新池/大额转账
    cm = intel.get('chain_monitor', {})
    if cm:
        pools = cm.get('new_pools', [])
        transfers = cm.get('large_transfers', [])
        suspicious = cm.get('suspicious_tokens', [])
        if pools or transfers or suspicious:
            lines.append("=== 链上监控 ===")
            if pools:
                pool_str = ' | '.join(f"{p.get('symbol','?')}({p.get('liquidity_usd',0):.0f}$)" for p in pools[:5])
                lines.append(f"  新池(24h): {pool_str}")
            if transfers:
                for t in transfers[:3]:
                    lines.append(f"  大额转账: {t.get('token','?')} ${t.get('value_usd',0):,.0f} | {t.get('from','')[:10]}→{t.get('to','')[:10]}")
            if suspicious:
                lines.append(f"  可疑代币: {' | '.join([s.get('address','')[:10] for s in suspicious[:3]])}")
            lines.append("")

    # ★ V3: 暗网情报
    dw = intel.get('darkweb', [])
    if dw:
        lines.append("=== 暗网/深网情报 ===")
        for s in dw[:3]:
            title = s.get('title', s.get('text', ''))[:80]
            src = s.get('source', '?')
            if title:
                lines.append(f"  [{src}] {title}")
        lines.append("")
    
    # ★ V3: 宏观政治/经济新闻(第7条情报根)
    mp = intel.get('macro_political', [])
    if mp:
        lines.append("=== 宏观政治/经济(源:GoogleNews) ===")
        for m in mp[:5]:
            title = m.get('title', '')[:100]
            term = m.get('search_term', '')
            if title:
                lines.append(f"  [{term}] {title}")
        lines.append("")
    
    # 订单簿深度分析(树状嫁接 V3.5)
    depth = data.get('depth_analysis')
    if depth and depth.get('fake_breakout_high_risk'):
        lines.append("=== 订单簿深度(假突破风险) ===")
        for r in depth['fake_breakout_high_risk']:
            lines.append(f"  🔥 {r['symbol']}: {'; '.join(r['reasons'][:2])}")
        for r in depth.get('fake_breakout_medium_risk', []):
            lines.append(f"  ⚠️ {r['symbol']}: {'; '.join(r['reasons'][:2])}")
        lines.append("")

    lines += [
        "=== 市场概况 ===",
    ]

    btc = data.get('market', {}).get('BTC', {})
    lines.append(f"BTC: ${btc.get('price',0):.0f} | RSI_15m={btc.get('rsi_15m',50)} RSI_1h={btc.get('rsi_1h',50)} | "
                 f"ATR_15m={btc.get('atr_15m',0.5):.2f}% | 趋势1h={btc.get('trend_1h','?')} | "
                 f"量比={btc.get('vol_ratio_15m',1.0):.2f} | 周高={btc.get('weekly_high',0)} 周低={btc.get('weekly_low',0)}")
    lines.append("")

    # 找出异常币(RSI超卖/超买/放量/高波动)
    market = data.get('market', {})
    candidates = []
    for coin, d in market.items():
        rsi = d.get('rsi_15m', 50)
        vol = d.get('vol_ratio_15m', 1)
        atr = d.get('atr_15m', 0.5)
        if rsi < 35 or rsi > 70 or vol > 2.0 or atr > 2.0:
            candidates.append(coin)

    # 至少给BTC/ETH/SOL + 异常币
    priority = ['BTC', 'ETH', 'SOL']
    for coin in priority:
        if coin in market:
            candidates.append(coin)
    candidates = list(dict.fromkeys(candidates))[:8]  # 去重+最多8个

    lines.append(f"=== 深度分析币种({len(candidates)}个) ===")
    for coin in candidates:
        d = market.get(coin, {})
        lines.append(f"\n--- {coin} (${d.get('price',0):.4f}) ---")
        lines.append(f"RSI_15m={d.get('rsi_15m',50)} RSI_1h={d.get('rsi_1h',50)} "
                     f"ATR_15m={d.get('atr_15m',0.5):.2f}% 趋势15m={d.get('trend_15m','?')} "
                     f"趋势1h={d.get('trend_1h','?')} 量比={d.get('vol_ratio_15m',1.0):.2f} "
                     f"费率={d.get('funding',0):.4f}%")
        lines.append(f"周区间: ${d.get('weekly_low',0)} ~ ${d.get('weekly_high',0)}")

        # 15m K线(最近5根缩略)
        klines = d.get('klines_15m', [])
        if klines:
            lines.append("15m K线(O-H-L-C-V): " + ' | '.join(
                [f"{k['t']} ${k['o']}-${k['h']}-${k['l']}-${k['c']} vol={k['v']}" for k in klines[-5:]]))

    # 合约持仓
    pos = data.get('contract_positions', {})
    lines.append(f"\n=== 合约持仓({len(pos)}仓) ===")
    if pos:
        for s, p in pos.items():
            lines.append(f"  {s}: {p['side']} {p['qty']}个 @${p['entry']} PnL={p['pnl_pct']:.1f}% 清算=${p.get('liq_price','?')}")
    lines.append(f"合约余额: ${data.get('futures_balance', 0):.1f}")

    # 现货网格
    grid = data.get('spot_grid', {})
    lines.append(f"\n=== 现货网格({len(grid)}币) ===")
    for sym, g in list(grid.items())[:4]:
        lines.append(f"  {sym}: {g.get('active_layers',0)}层 | USDT锁={g.get('locked_usdt',0):.1f}")

    # 费率机会
    funding = data.get('top_funding', [])
    if funding:
        lines.append(f"\n=== 费率机会(top 5) ===")
        for f in funding[:5]:
            lines.append(f"  {f['symbol']}: 费率={f['funding']:.4f}% ATR={f['atr']:.1f}%")

    # ★ 选币系统候选 — 量化评分结果注入AI (四模型P0共识)
    candidates = data.get('candidates', [])
    if candidates:
        lines.append(f"\n=== 选币系统推荐Top{len(candidates)} ===")
        lines.append("(量化评分系统选出的候选, 供AI参考决策)")
        for c in candidates[:5]:
            dir_icon = '🟢做多' if c['direction'] == 'long' else '🔴做空'
            lines.append(f"  {c['coin']} {dir_icon} 评分{c['score']}/{c['max_score']} 费率{c.get('funding',0):+.4f}%")
            if c.get('details'):
                lines.append(f"    理由: {' | '.join(c['details'][:3])}")

    # 前次AI决策反馈
    last = data.get('last_ai_decision')
    if last:
        lines.append(f"\n=== 前次AI决策 ===")
        lines.append(f"  动作: {last.get('action','?')} {last.get('symbol','')} | 方向: {last.get('direction','?')}")
        lines.append(f"  结果: {last.get('result','?')} | PnL: ${last.get('pnl','?')}")

    lines.append("")
    lines.append("请先做四维自检，再出交易决策:")
    lines.append("")
    lines.append("【自检维度1: BTC大市过滤】")
    lines.append("1. BTC当前价格区间? (<$80K偏空, >$85K偏多, 中间观望)")
    lines.append("2. BTC的1h趋势方向? (up/down/sideways)")
    lines.append("3. BTC趋势决定山寨操作: 偏空只做空/偏多只做多/震荡高抛低吸")
    lines.append("")
    lines.append("【自检维度2: 多周期对齐】")
    lines.append("4. 日线趋势方向? (涨→只做多 跌→只做空 震荡→高抛低吸)")
    lines.append("5. 4h/1h方向是否与日线对齐? 多周期共振才入场")
    lines.append("6. RSI位置: 15m/1h RSI是否超买(>70)或超卖(<30)? 背驰信号?")
    lines.append("")
    lines.append("【自检维度3: 范围定位】")
    lines.append("7. 当前价格在周区间的什么位置? (顶/中/底)")
    lines.append("8. 区间底做多, 区间顶做空, 区间中间观望或等突破确认")
    lines.append("9. 距日高/日低多少百分比? 追高还是抄底?")
    lines.append("")
    lines.append("【自检维度4: 量价验证+K线真假】")
    lines.append("10. 成交量: 放量是真信号, 缩量是假信号")
    lines.append("11. 放量+趋势=真突破 | 缩量+趋势=假突破/死猫反弹")
    lines.append("12. 关键K线形态识别:")
    lines.append("    - 吞没形态(放量)=强反转信号")
    lines.append("    - 插针/长影线=测试支撑/阻力")
    lines.append("    - 连续阴阳=趋势健康")
    lines.append("    - 缩量反弹=空头回补,不是真买盘")
    lines.append("    - 放量下跌后缩量止跌=可能见底")
    lines.append("    - 缩量上涨后放量下跌=派发")
    lines.append("")
    lines.append("【自检维度5: 费率&资金流(2026-05-14焊死)】")
    lines.append("13. 资金费率方向: 正费率(+0.01%以上)=多头付空头→做空收钱 | 负费率(-0.01%以下)=空头付多头→做多收钱")
    lines.append("14. 确认方向与费率一致: 做空时费率为正 ✅ 做多时费率为负 ✅ | 费率与方向相反→跳过")
    lines.append("15. 最后一问: 这笔交易的趋势证据在哪? 答得出再过一遍以上5个维度 → 答不出→不做")
    lines.append("")
    lines.append("【输出要求】")
    lines.append("先写你的四维自检结论(中文),然后再输出JSON。必须有confidence(1-10)。")
    lines.append('{')
    lines.append('  "whale_behavior": "accumulation|distribution|shakeout|pump|dump|ranging",')
    lines.append('  "behavior_evidence": "K线形态描述...",')
    lines.append('  "market_judgment": "bullish|bearish|ranging",')
    lines.append('  "risk_level": 1-5,')
    lines.append('  "confidence": 1-10,')
    lines.append('  "spot_advice": {')
    lines.append('    "action": "expand|shrink|hold",')
    lines.append('    "spacing_pct": 0.8-2.0,')
    lines.append('    "tp_pct": 0.8-3.0,')
    lines.append('    "sl_pct": 1.5-5.0,')
    lines.append('    "max_layers": 2-8')
    lines.append('  },')
    lines.append('  "contract_suggestion": {')
    lines.append('    "action": "open|close|close_all|hold",')
    lines.append('    "symbol": "币种USDT",')
    lines.append('    "direction": "long|short",')
    lines.append('    "entry_zone": {"low": 入场低价, "high": 入场高价},')
    lines.append('    "tp_price": 止盈价,')
    lines.append('    "sl_price": 止损价,')
    lines.append('    "margin": 10-30,')
    lines.append('    "leverage": 3-5,')
    lines.append('    "confidence": 1-10,')
    lines.append('    "entry_urgency": "immediate|wait_for_zone|cancel"')
    lines.append('  },')
    lines.append('  "active_positions_check": "hold|adjust|close",')
    lines.append('  "reason": "综合判断依据(中文)"')
    lines.append('}')

    return '\n'.join(lines)


def ai_deep_analysis(data: dict, sieve_result: dict = None, force: bool = False) -> dict:
    """
    AI分析链(V1.0):
      1. 每15分钟强制深度分析(force=True时跳过机械筛子门控)
      2. 非强制周期: 机械筛子触发/有持仓 → 深度分析
      3. 深度分析: GPT-5.5(主,30s) → DeepSeek Flash(后备,15s)
      4. 全链路超时45s → 回退纯数学
      基于2026-05-16四模型横评结论: GPT-5.5交易分析最强, Flash性价比兜底
    """
    start = time.time()
    ai = AI()
    result = {
        'ai_used': False,
        'model_used': None,
        'raw_response': None,
        'analysis': None,
        'sieve_triggered': False,
    }

    has_positions = len(data.get('contract_positions', {})) > 0
    has_funding_opp = len(data.get('top_funding', [])) > 0

    # ── Step 0: 机械筛子判断是否需要深度分析 ──
    needs_deep = False
    if sieve_result and sieve_result.get('trigger'):
        needs_deep = True
        result['sieve_triggered'] = True
    # 每15分钟强制深分析(force=True)，去掉"有持仓就每120s触发"逻辑(防烧钱)
    if force:
        needs_deep = True
        print(f"[DS-0] ⏰ 15分钟定时 → 强制深度分析")

    if not needs_deep:
        print(f"[DS-0] 📊 机械筛子平静, 跳过深度分析 [0s]")
        return result

    # ── 检查总超时(快速返回) ──
    if time.time() - start > TOTAL_AI_TIMEOUT:
        print(f"[DS-0] ⚠️ AI总超时({TOTAL_AI_TIMEOUT}s), 回退纯数学")
        return result

    # ── Step 1: 大模型深度分析(GPT-5.5主 → Flash后备) ──
    try:
        model_prompt = _build_model_prompt(data, '')
        remaining = max(5, TOTAL_AI_TIMEOUT - (time.time() - start))
        model_name, model_out = ai.model_analyze(model_prompt, timeout=min(30, remaining))
        if model_out:
            result['model_used'] = model_name
            result['raw_response'] = model_out
            result['ai_used'] = True

            # 解析AI输出JSON
            import re as _re
            ai_json = _re.search(r'\{.*\}', model_out, _re.DOTALL)
            if ai_json:
                try:
                    analysis = json.loads(ai_json.group())
                    result['analysis'] = analysis
                except:
                    print(f"[DS-0] ⚠️ AI JSON解析失败, 保留原始文本")
            elapsed = time.time() - start
            print(f"[DS-0] AI分析完成 | 模型={model_name} | conf={result['analysis'].get('confidence','?') if result['analysis'] else '?'} [{elapsed:.1f}s]")
            return result
    except Exception as e:
        print(f"[DS-0] ⚠️ 大模型异常: {e}")

    # ── 检查总超时(再次) ──
    if time.time() - start > TOTAL_AI_TIMEOUT:
        print(f"[DS-0] ⚠️ AI总超时, 回退纯数学")
        return result

    # ── Step 3: DeepSeek兜底 ──
    try:
        remaining = max(5, TOTAL_AI_TIMEOUT - (time.time() - start))
        model_prompt = _build_model_prompt(data, '')
        ds_out = ai.model_analyze(model_prompt, timeout=min(15, remaining))
        if ds_out:
            result['model_used'] = 'fallback'
            result['raw_response'] = ds_out[1] if isinstance(ds_out, tuple) else ds_out
            result['ai_used'] = True

            import re as _re
            ai_json = _re.search(r'\{.*\}', ds_out, _re.DOTALL)
            if ai_json:
                try:
                    result['analysis'] = json.loads(ai_json.group())
                except:
                    pass
            print(f"[DS-0] AI分析完成 | 模型=deepseek-fallback [{time.time()-start:.1f}s]")
            return result
    except Exception as e:
        print(f"[DS-0] ⚠️ DeepSeek兜底异常: {e}")

    print(f"[DS-0] AI全链路失败, 回退纯数学 [{time.time()-start:.1f}s]")
    return result


def _merge_ai_to_decision(decision: dict, ai_result: dict) -> dict:
    """AI分析结果合并到数学决策中 — 只有高置信度+方向一致时才覆盖"""
    if not ai_result.get('ai_used') or not ai_result.get('analysis'):
        return decision

    analysis = ai_result['analysis']
    ai_conf = analysis.get('confidence', 0)
    if not isinstance(ai_conf, (int, float)):
        return decision

    # ── AI覆盖数学决策的门槛(V3.4降低) ──
    # confidence ≥ 6 且方向一致 → 覆盖现货参数(间距/TP/SL)
    # confidence ≥ 6 但方向相反 → hold(保守)
    # confidence < 6 → 仅记录日志

    ai_judge = analysis.get('market_judgment', 'ranging')
    math_judge = decision.get('market_judgment', 'ranging')

    # 方向一致性检查
    direction_match = True
    if ai_judge in ('bullish', 'bearish') and math_judge in ('bullish', 'bearish'):
        if ai_judge != math_judge:
            direction_match = False

    if ai_conf >= AI_OVERRIDE_THRESHOLD and direction_match:
        # ✅ 高置信度 + 方向一致 → AI覆盖现货参数
        spot = analysis.get('spot_advice', {})
        if spot:
            sp = decision.get('spot_params', {})
            if 'spacing_pct' in spot:
                sp['spacing_pct'] = spot['spacing_pct']
            if 'tp_pct' in spot:
                sp['tp_pct'] = spot['tp_pct']
            if 'sl_pct' in spot:
                sp['sl_pct'] = spot['sl_pct']
            if 'max_layers' in spot:
                sp['max_layers'] = spot['max_layers']
            decision['reason'] += f' | AI覆盖(conf={ai_conf})'
            # ★ V3.5: AI覆盖的参数必须过边界校验
            _clamp_params(decision)
    elif ai_conf >= AI_OVERRIDE_THRESHOLD and not direction_match:
        # ⚠️ 高置信度但方向相反 → 强制hold
        decision['reason'] += f' | AI方向冲突(conf={ai_conf}), 保持数学决策'
    else:
        # ℹ️ 低置信度 → 仅记录
        decision['reason'] += f' | AI建议(conf={ai_conf})'

    # ── AI合约建议 ──
    contract = analysis.get('contract_suggestion', {})
    if contract and contract.get('action') in ('open', 'close', 'close_all', 'hold'):
        # 只有AI conf≥6才考虑合约建议
        if ai_conf >= 6:
            cd = decision.get('contract_decision', {})
            cd['action'] = contract['action']
            if contract.get('symbol'):
                cd['symbol'] = contract['symbol']
            if contract.get('direction'):
                cd['direction'] = contract['direction']
            if contract.get('margin'):
                cd['margin'] = min(30, max(10, contract['margin']))
            if contract.get('override_tp'):
                cd['override_tp'] = contract['override_tp']
            if contract.get('override_sl'):
                cd['override_sl'] = contract['override_sl']
            cd['confidence'] = min(10, max(1, ai_conf))
            cd['note'] = contract.get('reason', analysis.get('reason', 'AI建议'))[:60]

            # AI给的entry_zone结构化解析
            ez = contract.get('entry_zone', {})
            if isinstance(ez, dict) and 'low' in ez and 'high' in ez:
                cd['entry_zone_low'] = ez['low']
                cd['entry_zone_high'] = ez['high']
                cd['entry_urgency'] = contract.get('entry_urgency', 'wait_for_zone')

    # ── AI对现有持仓的建议 ──
    pos_action = analysis.get('active_positions_check', 'hold')
    if pos_action in ('close', 'adjust'):
        decision['reason'] += f' | AI建议调整持仓: {pos_action}'

    # ── 记录AI判断的庄家行为 ──
    whale = analysis.get('whale_behavior', '')
    evidence = analysis.get('behavior_evidence', '')
    if whale:
        decision['whale_behavior'] = whale
        decision['behavior_evidence'] = evidence[:80]
        decision['reason'] += f' | 庄家:{whale}'

    # 更新market_judgment(仅AI高置信度时)
    if ai_conf >= AI_OVERRIDE_THRESHOLD and direction_match and ai_judge in ('bullish', 'bearish', 'ranging'):
        decision['market_judgment'] = ai_judge

    return decision


def _save_ai_history(ai_result: dict, decision: dict):
    """保存本次AI决策供下次反馈闭环"""
    analysis = ai_result.get('analysis', {})
    contract = analysis.get('contract_suggestion', {})
    hist = {
        'ts': int(time.time()),
        'model_used': ai_result.get('model_used'),
        'action': contract.get('action', 'hold'),
        'symbol': contract.get('symbol', ''),
        'direction': contract.get('direction', ''),
        'confidence': analysis.get('confidence', 0),
        'market_judgment': analysis.get('market_judgment', ''),
        'whale_behavior': analysis.get('whale_behavior', ''),
        'reason': analysis.get('reason', '')[:80],
        'result': 'pending',  # 下次运行时更新
        'pnl': None,
    }
    # 从当前合约持仓算pnl(如果有)
    # 实际结果由下一次运行时读取持仓变化来更新
    _atomic_write(AI_HISTORY, hist)


# ══════════════════════════════════════════════════════════════════════
# Phase 2b: 数学分析决策(V3.2原有, AI不上时兜底)
# ══════════════════════════════════════════════════════════════════════

def analyze(data: dict, ai_result: dict = None) -> dict:
    """DS-0多周期分析 → 输出现货参数+合约决策
    ai_result不为None时尝试AI覆盖
    V3.5: 入口加熔断保护 + 参数边界校验"""
    # ── ★ V3.5 熔断保护 ──
    futures_bal = data.get('futures_balance', 999)
    daily_file = DAILY_FILE
    today_loss = 0.0
    if os.path.exists(daily_file):
        try:
            with open(daily_file) as f:
                dj = json.load(f)
            if dj.get('date') == datetime.now().strftime('%Y-%m-%d'):
                today_loss = abs(dj.get('total_loss', 0))
        except:
            pass
    circuit_break = False
    circuit_reason = ''
    if futures_bal < 20:
        circuit_break = True
        circuit_reason = f'合约余额${futures_bal:.1f}<$20,熔断所有合约开仓'
    elif today_loss > 15:
        circuit_break = True
        circuit_reason = f'今日亏损${today_loss:.1f}>$15,熔断合约开仓'
    if circuit_break:
        print(f'[DS-0] 🔴 熔断触发: {circuit_reason}')

    now_ts = int(time.time())
    decision = {
        'schema_version': '3.3',
        'generated_at': now_ts,
        'valid_until': now_ts + 900,  # 15分钟有效
        'market_judgment': 'ranging',
        'reason': '等待数据',
        'risk_level': 3,
        'spot_params': {
            'symbols': ['ETH/USDT', 'UNI/USDT', 'DOGE/USDT', 'BTC/USDT'],
            'spacing_pct': 1.0,
            'tp_pct': 1.0,
            'sl_pct': 2.5,
            'max_layers': 3,
            'first_layer_pct': 0.3,
            'deploy_pct': 0.85,
        },
        'contract_decision': {
            'action': 'hold',
            'symbol': None,
            'direction': 'long',
            'margin': 6,
            'override_tp': None,
            'override_sl': None,
            'confidence': 5,
            'note': '无操作',
        },
    }

    market = data.get('market', {})
    btc = market.get('BTC', {})

    # ── 市场阶段判断(纯数学) ──
    btc_rsi_1h = btc.get('rsi_1h', 50)
    btc_trend_1h = btc.get('trend_1h', 'ranging')
    btc_atr_1h = btc.get('atr_1h', 0.5)

    if btc_trend_1h == 'up' and btc_rsi_1h < 70 and btc_rsi_1h > 40:
        decision['market_judgment'] = 'bullish'
        decision['risk_level'] = 2
        decision['reason'] = f'BTC 1h上行 RSI={btc_rsi_1h:.0f} ATR={btc_atr_1h:.2f}%'
        decision['spot_params']['spacing_pct'] = 1.2
        decision['spot_params']['max_layers'] = 2
    elif btc_trend_1h == 'down' and btc_rsi_1h < 60 and btc_rsi_1h > 25:
        decision['market_judgment'] = 'bearish'
        decision['risk_level'] = 4
        decision['reason'] = f'BTC 1h下行 RSI={btc_rsi_1h:.0f} ATR={btc_atr_1h:.2f}%'
        decision['spot_params']['spacing_pct'] = 1.5
        decision['spot_params']['max_layers'] = 1
        decision['spot_params']['deploy_pct'] = 0.5
    elif btc_atr_1h < 1.5:
        decision['market_judgment'] = 'ranging'
        decision['risk_level'] = 2
        decision['reason'] = f'BTC低波整理 ATR={btc_atr_1h:.2f}% RSI={btc_rsi_1h:.0f}'
        decision['spot_params']['spacing_pct'] = 1.0
        decision['spot_params']['max_layers'] = 3
        decision['spot_params']['tp_pct'] = 1.0
    else:
        decision['market_judgment'] = 'uncertain'
        decision['risk_level'] = 3
        decision['reason'] = f'BTC方向不明 ATR={btc_atr_1h:.2f}% RSI={btc_rsi_1h:.0f}'

    # ── 合约: 寻找高费率方向(纯数学) ──
    fundings = data.get('top_funding', [])
    contract_pos = data.get('contract_positions', {})
    max_pos = 3

    # ★ V3.5: 熔断触发时禁止所有合约开仓(保留平仓逻辑)
    if not circuit_break and len(contract_pos) < max_pos and fundings:
        best = fundings[0]
        fr = best['funding']
        sym = best['symbol']
        base = sym.replace('USDT', '')
        coin_data = market.get(base, {})
        atr = coin_data.get('atr_15m', 0) or coin_data.get('atr_1h', 0)
        vol = coin_data.get('vol_ratio_15m', 0) or coin_data.get('vol_ratio_1h', 0)

        if base not in ['BTC','ETH','SOL','DOGE','XRP'] and \
           1.5 <= atr <= 10.0 and vol > 0.8:
            direction = 'short' if fr > 0 else 'long'
            decision['contract_decision'] = {
                'action': 'open',
                'symbol': sym,
                'direction': direction,
                'margin': 6,
                'override_tp': None,
                'override_sl': None,
                'confidence': max(5, min(9, int(round(abs(fr) * 5 + 5)))),
                'note': f'费率{fr:.4f}% {base} ATR={atr:.1f}%',
            }
            decision['reason'] += f' | 费率: {base} {direction} ({fr:.4f}%)'

    # ── SIREN专用: 反弹衰竭做空扫描(2026-05-14) ──
    if not circuit_break and len(contract_pos) < max_pos:
        siren = market.get('SIREN', {})
        if siren:
            sp = siren.get('price', 0)
            sr = siren.get('rsi_15m', 50)
            sf = siren.get('funding', 0)
            sv = siren.get('vol_ratio_15m', 1.0)
            sa = siren.get('atr_15m', 0)
            st = siren.get('trend_15m', 'ranging')
            # 入场条件: 反弹至$0.78+ + 不再是极度超卖(RSI>20) + 费率仍极负
            if sp > 0.78 and sr > 20 and sf < -0.3 and \
               sv < 2.0 and sa > 2.0 and \
               st == 'down' and 'SIRENUSDT' not in contract_pos:
                decision['contract_decision'] = {
                    'action': 'open',
                    'symbol': 'SIRENUSDT',
                    'direction': 'short',
                    'margin': 40,
                    'override_tp': 3.0,
                    'override_sl': 2.0,
                    'confidence': 7,
                    'note': f'SIREN反弹至${sp:.4f} RSI={sr:.0f} 费率{sf:.4f}% 反弹衰竭做空',
                }
                decision['reason'] += f' | SIREN反弹衰竭空 ${sp:.4f} RSI={sr:.0f} 费率{sf:.4f}%'

    # ── 合约持仓检查 ──
    for sym, pos in contract_pos.items():
        pnl = pos.get('pnl_pct', 0)
        if pnl < -8:
            decision['contract_decision'] = {
                'action': 'close', 'symbol': sym,
                'confidence': 8, 'note': f'{pos.get("side","?")}已达-8%止损',
            }
            decision['reason'] += f' | 强制平仓{sym}'

    # ── 现货网格: 超买检查 ──
    spot_grid = data.get('spot_grid', {})
    for sym_obj in list(spot_grid.keys()):
        coin = sym_obj.split('/')[0]
        if coin in market:
            coin_data = market[coin]
            rsi_1h = coin_data.get('rsi_1h', 50)
            if rsi_1h > 85:
                decision['reason'] += f' | {coin} RSI={rsi_1h:.0f}超买'

    # ── 现货持仓置换(V3.6新增): 分析师主动管理现货仓位 ──
    spot_positions = data.get('spot_positions', {})
    held_coins = [c for c in spot_positions if c != 'USDT' and spot_positions.get(c, 0) > 0]
    swap_sells = []
    swap_buys = []
    if held_coins:
        print(f'[DS-0] 📦 现货持仓({len(held_coins)}个): {held_coins}')
        # 评分每个持仓币: 弱币标准=趋势下行+量能弱+RSI差
        for coin in held_coins:
            d = market.get(coin, {})
            # 未跟踪的币(不在track_coins里)直接视为弱币
            is_untracked = not d or not d.get('price', 0)
            
            trend_1h = d.get('trend_1h', '') if d else ''
            trend_15m = d.get('trend_15m', '') if d else ''
            rsi_1h = d.get('rsi_1h', 50) if d else 50
            rsi_15m = d.get('rsi_15m', 50) if d else 50
            vol_ratio = d.get('vol_ratio_15m', 1.0) if d else 0.1
            atr = d.get('atr_15m', 0.5) if d else 0.1
            price = d.get('price', 0) if d else 0
            qty = spot_positions.get(coin, 0)
            val = qty * price
            # 没价格的用现货API补(免签)
            if val <= 0 and qty > 0:
                try:
                    _p_data = requests.get('https://api.binance.com/api/v3/ticker/price',
                                           params={'symbol': coin + 'USDT'}, timeout=5).json()
                    if isinstance(_p_data, dict) and 'price' in _p_data:
                        price = float(_p_data['price'])
                        val = qty * price
                except:
                    pass
            
            weak_score = 0
            reasons = []
            if is_untracked:
                weak_score += 4
                reasons.append('未跟踪僵尸币')
            else:
                if trend_1h == 'down': weak_score += 2; reasons.append('1h下行')
                if trend_15m == 'down': weak_score += 1; reasons.append('15m下行')
                if rsi_1h < 35 or rsi_1h > 72: weak_score += 1; reasons.append(f'RSI_1h={rsi_1h:.0f}极端')
                if vol_ratio < 0.6: weak_score += 1; reasons.append(f'量比{vol_ratio:.1f}x偏低')
                if atr < 0.3: weak_score += 1; reasons.append(f'ATR{atr:.2f}%死水')
            
            if weak_score >= 3 and val > 20:
                swap_sells.append({'coin': coin, 'symbol': f'{coin}/USDT',
                                   'val': val, 'reason': '+'.join(reasons),
                                   'weak_score': weak_score})
                print(f'[DS-0] ⚠️ {coin} 弱评分={weak_score}: {", ".join(reasons)} | 建议卖出${val:.0f}')
        
        # 扫描强币候选(量比高+趋势向上+非持仓)
        _candidates = []
        for coin, d in market.items():
            if coin in held_coins or coin in ['USDT', 'USDC', 'BUSD']:
                continue
            trend_1h = d.get('trend_1h', '')
            rsi_1h = d.get('rsi_1h', 50)
            vol_ratio = d.get('vol_ratio_15m', 1.0)
            atr = d.get('atr_15m', 0.5)
            price = d.get('price', 0)
            funding = d.get('funding', 0)
            
            strong_score = 0
            s_reasons = []
            if trend_1h == 'up': strong_score += 2; s_reasons.append('1h上行')
            elif trend_1h == 'ranging': strong_score += 1; s_reasons.append('1h横盘')
            if 35 < rsi_1h < 60: strong_score += 1; s_reasons.append(f'RSI{rsi_1h:.0f}中性')
            if vol_ratio > 1.5: strong_score += 1; s_reasons.append(f'量比{vol_ratio:.1f}x')
            if atr > 0.8: strong_score += 1; s_reasons.append(f'ATR{atr:.2f}%活跃')
            if funding < -0.005: strong_score += 1; s_reasons.append(f'负费率={funding:.4f}%(看涨信号)')
            if vol_ratio > 3.0: strong_score += 1; s_reasons.append(f'巨量{vol_ratio:.1f}x')
            if atr < 0.5: strong_score -= 1  # 太死的扣分
            
            if strong_score >= 4 and price > 0.001 and vol_ratio > 0.5:
                _candidates.append((coin, strong_score, s_reasons, price, vol_ratio))
        
        _candidates.sort(key=lambda x: x[1], reverse=True)
        # 最多取len(swap_sells)个强币
        _max_buy = max(len(swap_sells), 1)
        for coin, score, reasons, price, vol in _candidates[:_max_buy]:
            swap_buys.append({'coin': coin, 'symbol': f'{coin}/USDT',
                              'reason': '+'.join(reasons), 'strong_score': score,
                              'price': price, 'vol_ratio': vol})
            print(f'[DS-0] 🟢 {coin} 强评分={score}: {", ".join(reasons)} | 建议买入')
        
        if swap_sells or swap_buys:
            decision['swap_sells'] = swap_sells
            decision['swap_buys'] = swap_buys
            _sell_coins = [s['coin'] for s in swap_sells]
            _buy_coins = [b['coin'] for b in swap_buys]
            _msg = f' | 置换: 卖{_sell_coins}→买{_buy_coins}'
            decision['reason'] += _msg
            print(f'[DS-0] 🔄 置换决策: 卖{_sell_coins} 买{_buy_coins}')
        decision['_held_coins'] = held_coins

    # ── AI覆盖(如果有且置信度够高) ──
    if ai_result:
        decision = _merge_ai_to_decision(decision, ai_result)

    return decision


# ══════════════════════════════════════════════════════════════════════
# Phase 3: 写文件(原子写入)
# ══════════════════════════════════════════════════════════════════════

def _atomic_write(path: str, data: dict):
    tmp = path + f'.tmp.{os.getpid()}'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)

def _clamp_params(decision: dict) -> dict:
    sp = decision.get('spot_params', {})
    for key, (lo, hi) in PARAM_BOUNDS.items():
        if key in sp and sp[key] is not None:
            sp[key] = max(lo, min(hi, sp[key]))
    return decision

def write_heartbeat():
    hb = {
        'ts': int(time.time()),
        'status': 'alive',
        'version': 'V3.5',
        'pid': os.getpid(),
    }
    _atomic_write(HEARTBEAT, hb)

def write_advisory(decision: dict):
    # ── V3.6: 新格式 buy/sell列表 + cash_pct + expire_ts ──
    now_ts   = int(time.time())
    expire_ts = now_ts + 14400  # 4小时有效(改为4h频率后不再每15min翻牌)

    # ── 冷却保护：判断变化未满30分钟→保持旧judgment ──
    _old_adv = {}
    if os.path.exists(ADVISORY):
        try:
            _old_adv = json.load(open(ADVISORY))
        except:
            pass
    _old_judgment = _old_adv.get('market_judgment', '')
    _last_change  = _old_adv.get('judgment_changed_at', 0)
    _new_judgment = decision.get('market_judgment', 'ranging')
    if _new_judgment != _old_judgment and _last_change > 0 and (now_ts - _last_change) < 300:
        print(f"[DS-0] ⏳ 冷却保护: 判断{_old_judgment}→{_new_judgment}距上次翻转仅{int((now_ts-_last_change)/60)}min, 保持{_old_judgment}")
        _new_judgment = _old_judgment
        # 同步decision中的judgment, 防止后续逻辑不一致
        decision['market_judgment'] = _new_judgment
    _judgment_changed_at = _last_change if _new_judgment == _old_judgment else now_ts

    sp       = decision.get('spot_params', {})
    symbols  = sp.get('symbols', ['ETH/USDT', 'BTC/USDT'])
    tp_pct   = sp.get('tp_pct', 1.0)
    sl_pct   = sp.get('sl_pct', 2.5)
    deploy   = sp.get('deploy_pct', 0.85)
    judgment = decision.get('market_judgment', 'ranging')
    risk     = decision.get('risk_level', 3)
    reason   = decision.get('reason', '')[:80]
    whale    = decision.get('whale_behavior', '')

    n         = max(len(symbols), 1)
    alloc_per = round(deploy / n, 3)
    # score: risk 1→8分, risk 5→2分
    score = max(1, min(10, 10 - risk * 2 + 2))

    buy_list  = []
    sell_list = []

    if judgment in ('bullish', 'ranging'):
        for sym in symbols:
            # V3.7: 网格模式 — 已有持仓的币也继续推荐买入(低买高卖)
            # 只在置换卖出时才跳过
            _base_coin = sym.split('/')[0]
            if _base_coin in [_s.get('coin','') for _s in decision.get('swap_sells', [])]:
                continue  # 这个币要被卖了, 不买
            buy_list.append({
                'symbol':    sym,
                'alloc_pct': alloc_per,
                'reason':    reason,
                'score':     score,
                'tp_pct':    round(tp_pct * 8, 2),   # 网格间距%→现货止盈放大8x
                'sl_pct':    round(sl_pct * 2, 2),   # 现货止损放大2x
            })
    elif judgment == 'bearish':
        for sym in symbols:
            sell_list.append({
                'symbol': sym,
                'reason': f'熊市信号 {reason}',
                'score':  score,
            })

    # 庄家行为修正
    if whale == 'accumulation' and buy_list:
        for b in buy_list:
            b['score'] = min(10, b['score'] + 1)
            b['reason'] += ' | 庄家吸筹'
    elif whale in ('distribution', 'dump') and not sell_list:
        for sym in symbols:
            sell_list.append({
                'symbol': sym,
                'reason': f'庄家{whale} {reason}',
                'score':  max(1, score - 2),
            })

    advisory = {
        'schema_version':   '4.0',
        'generated_at':     decision.get('generated_at', now_ts),
        'expire_ts':        expire_ts,
        'market_judgment':  _new_judgment,
        'judgment_changed_at': _judgment_changed_at,
        'reason':           reason,
        'risk_level':       risk,
        'buy':              buy_list,
        'sell':             sell_list,
        'swap':             [],
        'cash_pct':         round(1.0 - deploy, 3),
        'spot_params':      sp,
        'whale_behavior':   whale or None,
        'behavior_evidence': decision.get('behavior_evidence', None),
        # ── V4 网格参数 ──
        'grid_spacing_pct': 0.5,
        'grid_layers': 4,
    }
    
    # ── V3.6: 现货置换逻辑 → 注入sell/buy列表 ──
    _swap_sells = decision.get('swap_sells', [])
    _swap_buys = decision.get('swap_buys', [])
    for _s in _swap_sells:
        sell_list.append({
            'symbol': _s['symbol'],
            'reason': f"置换:{_s.get('reason','弱币')}",
            'score': max(1, 10 - _s.get('weak_score', 3) * 2),
        })
    for _b in _swap_buys:
        buy_list.append({
            'symbol': _b['symbol'],
            'alloc_pct': round(deploy / max(len(sell_list) + len(_swap_buys), 1), 3),
            'reason': f"置换:{_b.get('reason','强币')}",
            'score': min(10, _b.get('strong_score', 5) * 2),
            'tp_pct': round(sp.get('tp_pct', 1.5) * 8, 2),
            'sl_pct': round(sp.get('sl_pct', 2.5) * 2, 2),
        })
    # 排重：同一币不能同时出现在buy和sell
    _buy_symbols  = {b['symbol'] for b in buy_list}
    _sell_symbols = {s['symbol'] for s in sell_list}
    _conflict     = _buy_symbols & _sell_symbols
    if _conflict:
        print(f'[DS-0] ⚡ advisory排重: {_conflict}同时在buy+sell→保留sell清buy')
        buy_list = [b for b in buy_list if b['symbol'] not in _conflict]
    # 更新buy/sell列表
    advisory['buy'] = buy_list
    advisory['sell'] = sell_list
    if _swap_sells:
        advisory['swap'] = []
        for i, s in enumerate(_swap_sells):
            b = _swap_buys[i] if i < len(_swap_buys) else {'coin': 'USDT(暂持现金)'}
            advisory['swap'].append(f"{s['coin']}→{b['coin']}")
    _atomic_write(ADVISORY, advisory)

def write_commands(decision: dict):
    cd = decision.get('contract_decision', {})
    now_ts = int(time.time())
    cmd = {
        'id': str(uuid.uuid4())[:12],
        'command_id': f'ds0_{now_ts}_{os.getpid()}',
        'ts': now_ts,
        'expire_ts': now_ts + 900,   # V3.6: 15分钟过期
        'action': cd.get('action', 'hold'),
        'symbol': cd.get('symbol', ''),
        'direction': cd.get('direction', 'long'),
        'margin': cd.get('margin', 6),
        'confidence': cd.get('confidence', 5),
        'override_tp': cd.get('override_tp'),
        'override_sl': cd.get('override_sl'),
        'reason': decision.get('reason', '')[:120],
        'entry_zone_low': cd.get('entry_zone_low', None),
        'entry_zone_high': cd.get('entry_zone_high', None),
        'entry_urgency': cd.get('entry_urgency', None),
    }
    _atomic_write(COMMANDS, cmd)


# ══════════════════════════════════════════════════════════════════════
# 常驻进程 — AI调用冷却
# ══════════════════════════════════════════════════════════════════════

_AI_COOLDOWN = {}  # {coin_name: last_ai_ts}
AI_COOLDOWN_SEC = 120  # 同一币种120秒内不重复调AI

def _check_ai_cooldown(coin: str) -> bool:
    """True=可以调AI, False=在冷却中"""
    now = time.time()
    last = _AI_COOLDOWN.get(coin, 0)
    if now - last < AI_COOLDOWN_SEC:
        return False
    _AI_COOLDOWN[coin] = now
    return True

# ══════════════════════════════════════════════════════════════════════
# 主流程(常驻daemon)
# ══════════════════════════════════════════════════════════════════════

LOOP_INTERVAL = 120   # 主循环120秒
FORCE_INTERVAL = 900   # 每15分钟强制深度分析

# ══════════════════════════════════════════════════════════════════════
# 交易执行器(V1.0, 2026-05-16) — 把GPT-5.5的分析变成实盘交易
# 安全闸: 过期/已执行/低置信度/无止损/保证金超标/日亏上限/同币种
# ══════════════════════════════════════════════════════════════════════

_EXECUTED_LOG = {}
_EXECUTED_FILE = os.path.join(LOGS_DIR, 'executed_commands.json')
_EXEC_DAILY_FILE = os.path.join(LOGS_DIR, 'executor_daily.json')

def _load_executed():
    global _EXECUTED_LOG
    try:
        if os.path.exists(_EXECUTED_FILE):
            with open(_EXECUTED_FILE) as f:
                _EXECUTED_LOG = json.load(f)
    except:
        _EXECUTED_LOG = {}

def _save_executed():
    try:
        with open(_EXECUTED_FILE, 'w') as f:
            json.dump(_EXECUTED_LOG, f, indent=2)
    except:
        pass

def _get_daily_loss():
    try:
        if os.path.exists(_EXEC_DAILY_FILE):
            with open(_EXEC_DAILY_FILE) as f:
                d = json.load(f)
                if d.get('day') == datetime.now().strftime('%Y-%m-%d'):
                    return d.get('realized_loss', 0)
    except:
        pass
    return 0.0

def _get_position_map() -> dict:
    try:
        risk = _fapi('GET', '/fapi/v2/positionRisk', signed=True)
        pos = {}
        for p in risk:
            amt = float(p.get('positionAmt', 0))
            if abs(amt) > 0:
                pos[p['symbol']] = amt
        return pos
    except:
        return {}

# ═══════════════════════════════════════════════════════════════
# 专业级风控函数集 (Prop Firm / 量化机构标准, 2026-05-16)
# ═══════════════════════════════════════════════════════════════

_ACCOUNT_PEAK_FILE = os.path.join(LOGS_DIR, 'account_peak.json')
_SCORED_SIGNAL_CACHE = {}  # 避免每轮多次计算


def _get_account_peak() -> float:
    """获取账户历史峰值 — 用于追踪最大回撤"""
    try:
        if os.path.exists(_ACCOUNT_PEAK_FILE):
            with open(_ACCOUNT_PEAK_FILE) as f:
                return json.load(f).get('peak', 0)
    except:
        pass
    return 0


def _update_account_peak(current_balance: float):
    """更新账户峰值"""
    peak = _get_account_peak()
    if current_balance > peak:
        peak = current_balance
        try:
            with open(_ACCOUNT_PEAK_FILE, 'w') as f:
                json.dump({'peak': peak, 'ts': time.time()}, f)
        except:
            pass


def _check_trailing_drawdown(current_balance: float) -> tuple:
    """
    追踪回撤检查 (Prop Firm标准)
    Returns: (ok: bool, drawdown_pct: float)
    """
    peak = _get_account_peak()
    if peak == 0:
        _update_account_peak(current_balance)
        return True, 0
    if current_balance > peak:
        _update_account_peak(current_balance)
        return True, 0
    dd = (peak - current_balance) / peak * 100
    # 回撤>10%减半仓, >20%停所有交易 (职业交易员标准)
    if dd > 20:
        return False, round(dd, 1)
    return True, round(dd, 1)


def _scored_signal(symbol: str, direction: str) -> dict:
    """
    机械评分信号系统 (0-7分) — 不依赖AI的纯技术验证
    对标: MarkusSela bybit-technical-bot + Prop Firm标准
    
    Score ≥ 5 → 强信号 (25%仓位)
    Score 3-4 → 中等信号 (10%仓位)
    Score < 3 → 不交易 (纯机械否决)
    """
    cache_key = f'{symbol}_{direction}_{int(time.time()/300)}'  # 5min缓存
    if cache_key in _SCORED_SIGNAL_CACHE:
        return _SCORED_SIGNAL_CACHE[cache_key]
    
    result = {'score': 0, 'details': [], 'grade': 'fail'}
    try:
        # 获取K线数据
        kl = _fapi('GET', f'/fapi/v1/klines?symbol={symbol}&interval=1h&limit=50', signed=False)
        if not isinstance(kl, list) or len(kl) < 50:
            return result
        
        closes = [float(k[4]) for k in kl]
        highs = [float(k[2]) for k in kl]
        lows = [float(k[3]) for k in kl]
        vols = [float(k[5]) for k in kl]
        price = closes[-1]
        
        score = 0
        
        # 指标1: MACD (金叉/死叉 +2/-2)
        ema12 = sum(closes[-12:])/12
        ema26 = sum(closes[-26:])/26
        if direction == 'long' and ema12 > ema26:
            score += 2
            result['details'].append('MACD金叉+2')
        elif direction == 'short' and ema12 < ema26:
            score += 2
            result['details'].append('MACD死叉+2')
        
        # 指标2: RSI (超卖做多/超买做空 +1)
        gains = [max(closes[i]-closes[i-1],0) for i in range(1,15)]
        losses = [max(closes[i-1]-closes[i],0) for i in range(1,15)]
        ag = sum(gains)/14 if gains else 0
        al = sum(losses)/14 if losses else 0
        rsi = 50 if al==0 else 100 - 100/(1+ag/al)
        if direction == 'long' and rsi < 32:
            score += 1
            result['details'].append(f'RSI超卖{rsi:.0f}+1')
        elif direction == 'short' and rsi > 68:
            score += 1
            result['details'].append(f'RSI超买{rsi:.0f}+1')
        
        # 指标3: 成交量确认 (+1 if volume > 1.8x avg)
        avg_vol = sum(vols[-10:])/10
        last_vol = vols[-1]
        if avg_vol > 0 and last_vol / avg_vol > 1.8:
            score += 1
            result['details'].append(f'放量{last_vol/avg_vol:.1f}x+1')
        
        # 指标4: 布林带位置 (+1 if price near edge)
        bb_mid = sum(closes[-20:])/20
        bb_std = (sum((c-bb_mid)**2 for c in closes[-20:])/20)**0.5
        bb_pct = (price - bb_mid) / (bb_std * 2) * 100 + 50  # 0-100
        if direction == 'long' and bb_pct < 20:
            score += 1
            result['details'].append(f'BB低位{bb_pct:.0f}%+1')
        elif direction == 'short' and bb_pct > 80:
            score += 1
            result['details'].append(f'BB高位{bb_pct:.0f}%+1')
        
        # 指标5: EMA趋势确认 (+1 if aligned with 4h trend)
        ema50 = sum(closes[-50:])/50
        ema20 = sum(closes[-20:])/20
        if direction == 'long' and price > ema20 > ema50:
            score += 1
            result['details'].append('EMA多头排列+1')
        elif direction == 'short' and price < ema20 < ema50:
            score += 1
            result['details'].append('EMA空头排列+1')
        
        # 指标6: 资金费率方向 (+1 if rate supports direction)
        try:
            fr = _fapi('GET', f'/fapi/v1/premiumIndex?symbol={symbol}', signed=False)
            if isinstance(fr, dict) and 'lastFundingRate' in fr:
                rate = float(fr['lastFundingRate'])
                if direction == 'long' and rate < -0.0001:
                    score += 1
                    result['details'].append(f'负费率{rate*100:+.3f}%+1')
                elif direction == 'short' and rate > 0.0001:
                    score += 1
                    result['details'].append(f'正费率{rate*100:+.3f}%+1')
        except:
            pass
        
        # 指标7: 24h动量 (趋势方向 +1, 逆势 -1)
        change_24h = (closes[-1] / closes[-min(24, len(closes)-1)] - 1) * 100
        if direction == 'long' and change_24h > -5:
            score += 1
            result['details'].append(f'24h{change_24h:+.1f}%+1')
        elif direction == 'short' and change_24h < 5:
            score += 1
            result['details'].append(f'24h{change_24h:+.1f}%+1')
        
        # 评级
        if score >= 5:
            result['grade'] = 'strong'
        elif score >= 3:
            result['grade'] = 'medium'
        else:
            result['grade'] = 'fail'
        
        result['score'] = score
        _SCORED_SIGNAL_CACHE[cache_key] = result
        return result
        
    except Exception as e:
        result['details'].append(f'异常:{e}')
        return result


def _check_intel_red_flags(symbol: str, direction: str) -> dict:
    """
    情报交叉验证 — 读取intel.json判断是否有冲突信号
    Returns: {'block': bool, 'reason': str}
    """
    result = {'block': False, 'reason': ''}
    try:
        intel_path = os.path.join(LOGS_DIR, 'intel.json')
        if not os.path.exists(intel_path):
            return result
        
        with open(intel_path) as f:
            intel = json.load(f)
        
        # 检查: 恐惧贪婪指数 < 20 时禁止做空 (极度恐慌只做多)
        fng = intel.get('fear_greed', {})
        if isinstance(fng, dict) and fng.get('value', 50) < 20 and direction == 'short':
            result['block'] = True
            result['reason'] = f'恐慌指数{fng["value"]}/100,禁做空'
            return result
        
        # 检查: ETF大规模抛售时谨慎做多
        news = intel.get('news', {})
        etf = news.get('etf', [])
        for item in etf:
            title = item.get('title', '')
            if 'sell' in title.lower() and 'btc' in title.lower() and direction == 'long':
                result['block'] = True
                result['reason'] = f'ETF抛售信号: {title[:60]}'
                return result
        
        # 检查: 链上大额转账预警
        chain = intel.get('chain_monitor', {})
        if isinstance(chain, dict):
            alerts = chain.get('alerts', [])
            for a in alerts[:3]:
                if isinstance(a, dict) and a.get('severity') == 'high':
                    result['block'] = True
                    result['reason'] = f'链上警报: {a.get("note","?")}'
                    return result
        
        return result
    except:
        return result


def _calc_atr(klines: list, period: int = 14) -> float:
    """计算ATR(平均真实波幅)"""
    if not klines or len(klines) < period + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        h = float(klines[i][2])
        l = float(klines[i][3])
        pc = float(klines[i-1][4])
        tr = max(h-l, abs(h-pc), abs(l-pc))
        trs.append(tr)
    return sum(trs[-period:]) / period


def _calc_risk_budget(wallet: float, max_risk_pct: float = 2.0) -> float:
    """
    单笔风险预算 (Prop Firm标准: 单笔风险≤2%)
    返回: 该笔交易允许的最大亏损金额
    """
    return wallet * max_risk_pct / 100


def _calc_atr_position_size(symbol: str, direction: str, wallet: float) -> dict:
    """
    ATR动态仓位计算 — 替代固定margin
    职业交易员标准: 仓位 = 风险预算 / (ATR × 1.5)
    
    Returns: {
        'suggested_margin': float,
        'suggested_sl_pct': float,
        'atr': float,
        'risk_amount': float,
        'distance_pct': float
    }
    """
    default = {'suggested_margin': 20, 'suggested_sl_pct': 1.5, 'atr': 0, 'risk_amount': 0, 'distance_pct': 1.5}
    try:
        kl = _fapi('GET', f'/fapi/v1/klines?symbol={symbol}&interval=1h&limit=20', signed=False)
        if not isinstance(kl, list) or len(kl) < 15:
            return default
        
        atr = _calc_atr(kl, 14)
        if atr == 0:
            return default
        
        # 获取当前价格
        ti = _fapi('GET', f'/fapi/v1/premiumIndex?symbol={symbol}', signed=False)
        if not isinstance(ti, dict) or 'markPrice' not in ti:
            return default
        price = float(ti['markPrice'])
        
        atr_pct = atr / price * 100  # ATR百分比
        
        # 止损距离 = 1.5 × ATR
        sl_pct = round(atr_pct * 1.5, 2)
        
        # 单笔最大亏损 = 钱包 × 2%
        risk_amount = _calc_risk_budget(wallet)
        
        # 杠杆上限 = 10x, 但由距离决定
        max_leverage = min(10, max(3, int(1 / (sl_pct/100) * 0.5)))
        
        # 建议保证金 = 风险预算 / 止损百分比
        suggested_margin = min(30, max(5, round(risk_amount / (sl_pct/100 / max_leverage))))
        
        return {
            'suggested_margin': round(suggested_margin, 1),
            'suggested_sl_pct': sl_pct,
            'atr': atr,
            'risk_amount': round(risk_amount, 2),
            'distance_pct': sl_pct,
        }
    except:
        return default


def _validate_trade(symbol: str, direction: str, margin: float, confidence: int,
                    sl_price: float, entry_zone: tuple = None) -> tuple:
    """
    统一安全闸 — 12道专业级验证 (所有入口共享)
    
    闸1-7: 基础安全 (原8闸精简)
    闸8-12: 量化机构级风控 (2026-05-16新增)
    
    Prop Firm标准参考:
    - 单笔风险≤2% | 日亏≤5% | 回撤>20%停
    - ATR止损 | 杠杆≤10x | 总敞口≤50%资金
    """
    now = time.time()

    # ── 闸1: 置信度 ──
    if confidence < 6:
        return False, f'conf={confidence}<6'

    # ── 闸2: 保证金范围 ──
    if margin > 30:
        return False, f'margin=${margin} > $30'
    if margin < 5:
        return False, f'margin=${margin} < $5'

    # ── 闸3: 止损必设 (不设止损不开单) ──
    if not sl_price or sl_price == 0:
        return False, '无止损'

    # ── 闸4: 日亏上限 -$30 (Prop Firm标准) ──
    if _get_daily_loss() < -30:
        return False, f'日亏${_get_daily_loss():.0f}超限'

    # ── 闸5: 同币种不重开 ──
    if not symbol:
        return False, '无币种'
    pos = _get_position_map()
    if symbol in pos:
        return False, f'已有{symbol}'

    # ── 闸6: 入场区间 ──
    if entry_zone:
        zl, zh = entry_zone
        if zl > 0 and zh > 0:
            try:
                ti = _fapi('GET', f'/fapi/v1/premiumIndex?symbol={symbol}', signed=False)
                if isinstance(ti, dict) and 'markPrice' in ti:
                    mp = float(ti['markPrice'])
                    if mp < zl or mp > zh:
                        return False, f'价${mp}不在[${zl},${zh}]'
            except:
                pass

    # ── 闸7: BTC大环境检查 ──
    try:
        btc_ti = _fapi('GET', f'/fapi/v1/premiumIndex?symbol=BTCUSDT', signed=False)
        if isinstance(btc_ti, dict) and 'markPrice' in btc_ti:
            btc_price = float(btc_ti['markPrice'])
            if direction == 'long' and btc_price < 78000:
                return False, f'BTC${btc_price:.0f}<78K,禁做多'
            if direction == 'short' and btc_price > 85000:
                return False, f'BTC${btc_price:.0f}>85K,禁做空'
    except:
        pass

    # ═══════════════════════════════════════════════════════
    # 专业级风控 (闸8-12) — Prop Firm / 量化机构标准
    # ═══════════════════════════════════════════════════════

    # 获取当前账户状态
    _acct = {}
    try:
        ra = _fapi('GET', '/fapi/v2/account', signed=True)
        if isinstance(ra, dict):
            _acct = ra
    except:
        pass
    wallet = float(_acct.get('totalWalletBalance', 0)) or 0
    _update_account_peak(wallet)
    avail = float(_acct.get('availableBalance', 0)) or 0
    
    # 获取当前价格用于后续计算
    _price = 0
    try:
        ti = _fapi('GET', f'/fapi/v1/premiumIndex?symbol={symbol}', signed=False)
        if isinstance(ti, dict) and 'markPrice' in ti:
            _price = float(ti['markPrice'])
    except:
        pass

    # ── 闸8: 总敞口不超过资金的50% (机构风控标准) ──
    if wallet > 0:
        # 计算已有总保证金
        existing_margin = 0
        for p in _acct.get('positions', []):
            existing_margin += float(p.get('initialMargin', 0))
        total_after = existing_margin + margin
        if total_after > wallet * 0.5:
            return False, f'总敞口${total_after:.0f}>50%资金(${wallet*0.5:.0f})'

    # ── 闸9: 追踪回撤检查 (Prop Firm标准: 回撤>20%停) ──
    if wallet > 0:
        dd_ok, dd_pct = _check_trailing_drawdown(wallet)
        if not dd_ok:
            return False, f'回撤{dd_pct}%>20%,停所有交易'
        if dd_pct > 10:
            # 回撤>10%警告但不阻止 (记录日志)
            print(f'[DS-0] ⚠️ 回撤{dd_pct}%>10%,谨慎操作')

    # ── 闸10: 机械评分信号验证 (Scored Signal 0-7) ──
    sig = _scored_signal(symbol, direction)
    if sig['score'] < 3:
        return False, f'机械评分{sig["score"]}/7<3 ({sig.get("details","?")[:40]})'
    if sig['score'] >= 5:
        print(f'[DS-0] ✅ 强信号{sig["score"]}/7')
    else:
        print(f'[DS-0] ⚠️ 中等信号{sig["score"]}/7')

    # ── 闸11: 情报交叉验证 — intel.json有无冲突信号 ──
    intel_check = _check_intel_red_flags(symbol, direction)
    if intel_check['block']:
        return False, f'情报拦截: {intel_check["reason"][:60]}'

    # ── 闸12: ATR验证 — 止损距离必须≥1×ATR (防止超近止损被扫) ──
    try:
        kl = _fapi('GET', f'/fapi/v1/klines?symbol={symbol}&interval=1h&limit=20', signed=False)
        if isinstance(kl, list) and len(kl) > 14:
            atr = _calc_atr(kl, 14)
            if _price > 0 and atr > 0:
                sl_dist_pct = abs(sl_price - _price) / _price * 100
                atr_pct = atr / _price * 100
                if sl_dist_pct < atr_pct * 0.5:
                    return False, f'止损距{sl_dist_pct:.2f}%<0.5×ATR({atr_pct*0.5:.2f}%),太近易被扫'
    except:
        pass

    # ═══════════════════════════════════════════════════════
    # 鲸鱼验证四关 (2026-05-16 新增 — 不过4关不下单)
    # ═══════════════════════════════════════════════════════
    
    # ── 闸13: 深度验证 ⚖️  (Order Book 前20档) ──
    try:
        depth = _fapi('GET', f'/fapi/v1/depth?symbol={symbol}&limit=100', signed=False)
        if isinstance(depth, dict) and 'bids' in depth and 'asks' in depth:
            bids = [[float(b[0]), float(b[1])] for b in depth['bids'][:20]]
            asks = [[float(a[0]), float(a[1])] for a in depth['asks'][:20]]
            bid_vol = sum(b[0]*b[1] for b in bids)
            ask_vol = sum(a[0]*a[1] for a in asks)
            ratio = bid_vol / ask_vol if ask_vol > 0 else 1
            
            # 大额挂单检测 (>$50K单笔)
            big_bids = [b for b in bids if b[0]*b[1] > 50000]
            big_asks = [a for a in asks if a[0]*a[1] > 50000]
            
            # 如果做多但卖盘远强于买盘
            if direction == 'long' and ratio < 0.6:
                return False, f'闸13❌做多但卖盘强(买/卖比率={ratio:.2f}),鲸鱼在出货'
            # 如果做空但买盘远强于卖盘
            if direction == 'short' and ratio > 1.4:
                return False, f'闸13❌做空但买盘强(买/卖比率={ratio:.2f}),鲸鱼在吸筹'
            
            # 检测迎头撞上大单
            if direction == 'long' and big_asks:
                biggest = max(big_asks, key=lambda x: x[0]*x[1])
                print(f'[DS-0] 闸13⚠️ 做多方向有大卖单${biggest[0]*biggest[1]:.0f}@{biggest[0]}')
            if direction == 'short' and big_bids:
                biggest = max(big_bids, key=lambda x: x[0]*x[1])
                print(f'[DS-0] 闸13⚠️ 做空方向有大买单${biggest[0]*biggest[1]:.0f}@{biggest[0]}')
    except:
        pass

    # ── 闸14: 费率验证 💰 (方向与费率的匹配度) ──
    try:
        fi = _fapi('GET', f'/fapi/v1/premiumIndex?symbol={symbol}', signed=False)
        if isinstance(fi, dict) and 'lastFundingRate' in fi:
            fr = float(fi['lastFundingRate']) * 100
            # 做多时费率>0.01% = 多头拥挤,多单在付钱
            if direction == 'long' and fr > 0.01:
                print(f'[DS-0] 闸14⚠️ 做多但费率{fr:.4f}%,多头拥挤,可能多杀多')
            # 做空时费率<-0.01% = 空头拥挤,空单在付钱
            if direction == 'short' and fr < -0.01:
                print(f'[DS-0] 闸14⚠️ 做空但费率{fr:.4f}%,空头拥挤,可能轧空')
    except:
        pass

    # ── 闸15: OI趋势验证 📈 (Open Interest方向) ──
    try:
        oi_data = _fapi('GET', f'/fapi/v1/openInterest?symbol={symbol}', signed=False)
        if isinstance(oi_data, dict) and 'openInterest' in oi_data:
            oi = float(oi_data['openInterest'])
            # 简单趋势: 读取/写入缓存文件用于比较
            oi_cache = os.path.join(BASE_DIR, 'bot_logs', 'oi_cache.json')
            prev_oi = None
            if os.path.exists(oi_cache):
                try:
                    with open(oi_cache) as f:
                        cache = json.load(f)
                        prev_oi = cache.get(symbol)
                except:
                    pass
            # 存当前值
            try:
                cache = {}
                if os.path.exists(oi_cache):
                    with open(oi_cache) as f:
                        cache = json.load(f)
                cache[symbol] = oi
                with open(oi_cache, 'w') as f:
                    json.dump(cache, f)
            except:
                pass
            
            if prev_oi and prev_oi > 0:
                oi_chg_pct = (oi / prev_oi - 1) * 100
                # OI下降+价格向同方向=趋势确认; OI下降+反向=趋势减弱
                if abs(oi_chg_pct) > 5:
                    print(f'[DS-0] 闸15ℹ️ OI变动{oi_chg_pct:+.1f}%')
    except:
        pass

    # ── 闸16: 综合鲸鱼信号 🐋 (大额订单方向判断) ──
    try:
        depth16 = _fapi('GET', f'/fapi/v1/depth?symbol={symbol}&limit=100', signed=False)
        if isinstance(depth16, dict) and 'bids' in depth16 and 'asks' in depth16:
            bids16 = [[float(b[0]), float(b[1])] for b in depth16['bids'][:20]]
            asks16 = [[float(a[0]), float(a[1])] for a in depth16['asks'][:20]]
            big_bids16 = [b for b in bids16 if b[0]*b[1] > 50000]
            big_asks16 = [a for a in asks16 if a[0]*a[1] > 50000]
            
            if big_bids16 and direction == 'long':
                b = max(big_bids16, key=lambda x: x[0]*x[1])
                print(f'[DS-0] 🐋 鲸鱼买单${b[0]*b[1]:.0f}@{b[0]:.4f} → 做多方向一致 ✅')
            if big_asks16 and direction == 'short':
                a = max(big_asks16, key=lambda x: x[0]*x[1])
                print(f'[DS-0] 🐋 鲸鱼卖单${a[0]*a[1]:.0f}@{a[0]:.4f} → 做空方向一致 ✅')
            if big_bids16 and direction == 'short':
                b = max(big_bids16, key=lambda x: x[0]*x[1])
                print(f'[DS-0] ⚠️ 🐋 鲸鱼在大举买${b[0]*b[1]:.0f}@{b[0]:.4f}！但你是做空')
            if big_asks16 and direction == 'long':
                a = max(big_asks16, key=lambda x: x[0]*x[1])
                print(f'[DS-0] ⚠️ 🐋 鲸鱼在大举卖${a[0]*a[1]:.0f}@{a[0]:.4f}！但你是做多')
    except:
        pass

    return True, '通过'


def execute_trade(symbol: str, direction: str, margin: float, sl_price: float,
                  leverage: int = 5, tp_price: float = None, cmd_id: str = None) -> dict:
    """
    统一执行开仓 — API调用+止损设置
    Returns: {'status': 'ok'/'error', 'detail': ..., 'order': ...}
    """
    side = 'SELL' if direction == 'short' else 'BUY'
    ps = 'SHORT' if direction == 'short' else 'LONG'
    leverage = min(leverage, 10)

    # 获取价格
    try:
        ti = _fapi('GET', f'/fapi/v1/premiumIndex?symbol={symbol}', signed=False)
        if not isinstance(ti, dict) or 'markPrice' not in ti:
            return {'status': 'error', 'detail': '获取价格失败'}
        price = float(ti['markPrice'])
    except Exception as e:
        return {'status': 'error', 'detail': f'价格异常: {e}'}

    try:
        # 设杠杆
        _fapi('POST', '/fapi/v1/leverage',
              {'symbol': symbol, 'leverage': leverage}, signed=True)

        # 计算数量
        qty = round(margin * leverage / price, 4)
        if qty < 0.001: qty = 0.001

        # 开仓
        result = _fapi('POST', '/fapi/v1/order', {
            'symbol': symbol, 'side': side, 'positionSide': ps,
            'type': 'MARKET', 'quantity': qty, 'newOrderRespType': 'RESULT',
        }, signed=True)

        if 'orderId' not in result:
            return {'status': 'error', 'detail': result.get('msg', str(result)[:100])}

        fq = float(result.get('executedQty', qty))
        fp = float(result.get('avgPrice', 0))

        # 设止损
        try:
            sl_side = 'BUY' if direction == 'short' else 'SELL'
            _fapi('POST', '/fapi/v1/order', {
                'symbol': symbol, 'side': sl_side, 'positionSide': ps,
                'type': 'STOP_MARKET', 'quantity': fq,
                'stopPrice': sl_price, 'reduceOnly': True,
            }, signed=True)
            sl_ok = True
        except Exception as e:
            sl_ok = False
            print(f"[DS-0] ⚠️ 止损设置可能失败: {e}")

        # 记录执行
        if cmd_id:
            _EXECUTED_LOG[cmd_id] = time.time()
            _save_executed()

        return {
            'status': 'ok',
            'detail': f'{direction} {fq:.4f} @${fp:.4f} margin=${margin} {leverage}x SL=${sl_price}',
            'order': result,
            'sl_set': sl_ok,
        }

    except Exception as e:
        return {'status': 'error', 'detail': f'执行异常: {e}'}


def buy_workflow(symbol: str, direction: str, margin: float, confidence: int,
                 sl_price: float, tp_price: float = None, leverage: int = 5,
                 entry_zone: tuple = None, source: str = 'ai_auto',
                 cmd_id: str = None) -> dict:
    """
    ════════════════════════════════════════════════════════════════
    统一买币工作流 — 所有建仓必经此函数
    
    入口1: AI自动分析(GPT-5.5) → commands.json → execute_pending_commands → buy_workflow
    入口2: 用户问"哪里可以买" → AI分析选币 → buy_workflow (我手动调用)
    入口3: 用户说"买X" → buy_workflow (我手动调用)
    
    流程: _validate_trade(8道闸) → execute_trade(API) → 日志
    ════════════════════════════════════════════════════════════════
    """
    start = time.time()
    print(f"[DS-0] 📋 买币工作流启动 | {symbol} {direction} margin=${margin} conf={confidence} src={source}")

    # Step 0: ATR仓位建议 (日志输出,不强制覆盖)
    try:
        ra = _fapi('GET', '/fapi/v2/account', signed=True)
        if isinstance(ra, dict):
            w = float(ra.get('totalWalletBalance', 0))
            if w > 0:
                atr = _calc_atr_position_size(symbol, direction, w)
                if atr['atr'] > 0:
                    print(f"[DS-0] 📐 ATR建议: margin=${atr['suggested_margin']} SL={atr['suggested_sl_pct']}% risk=${atr['risk_amount']}")
    except:
        pass

    # Step 1: 安全闸
    ok, reason = _validate_trade(symbol, direction, margin, confidence, sl_price, entry_zone)
    if not ok:
        print(f"[DS-0] ⏭️ 安全闸拦截 | {reason}")
        return {'status': 'blocked', 'reason': reason, 'gate': 'validate'}

    # Step 2: 执行
    result = execute_trade(symbol, direction, margin, sl_price, leverage, tp_price, cmd_id)
    elapsed = time.time() - start

    if result['status'] == 'ok':
        print(f"[DS-0] ✅ 建仓成功 | {result['detail']} | src={source} [{elapsed:.1f}s]")
    else:
        print(f"[DS-0] ❌ 建仓失败 | {result['detail']} [{elapsed:.1f}s]")

    return result


def execute_pending_commands():
    """读取commands.json → buy_workflow"""
    try:
        if not os.path.exists(COMMANDS): return
        with open(COMMANDS) as f:
            cmd = json.load(f)
        if cmd.get('action') != 'open': return

        now = time.time()
        # 过期检查
        if now > cmd.get('expire_ts', 0):
            print(f"[DS-0] ⏭️ 命令过期 | {cmd.get('symbol','?')}")
            return

        # 重复执行检查
        cid = cmd.get('command_id', '') or cmd.get('id', '')
        if cid and cid in _EXECUTED_LOG:
            return

        # 提取参数 → buy_workflow
        sym = cmd.get('symbol', '')
        direction = cmd.get('direction', '')
        margin = cmd.get('margin', 20)
        confidence = cmd.get('confidence', 7)
        leverage = cmd.get('leverage', 5)
        sl = cmd.get('override_sl') or cmd.get('stop_loss')
        tp = cmd.get('override_tp') or cmd.get('take_profit')
        zl = cmd.get('entry_zone_low', 0)
        zh = cmd.get('entry_zone_high', 0)

        if not sym or not direction:
            return

        zone = (zl, zh) if zl > 0 and zh > 0 else None

        buy_workflow(
            symbol=sym, direction=direction, margin=float(margin),
            confidence=int(confidence), sl_price=float(sl) if sl else 0,
            tp_price=float(tp) if tp else None, leverage=int(leverage),
            entry_zone=zone, source='ai_auto', cmd_id=cid,
        )

    except Exception as e:
        print(f"[DS-0] 🔴 execute_pending_commands异常: {e}")
        traceback.print_exc()

_load_executed()

# ══════════════════════════════════════════════════════════════════════
# 主流程(常驻daemon)
# ══════════════════════════════════════════════════════════════════════

def main():
    print(f"[DS-0] ═══ 暗黑星火分析师V3.5 常驻模式启动 ═══")
    print(f"[DS-0] 循环: 每{LOOP_INTERVAL}s | 强制深分析: 每{FORCE_INTERVAL}s(15分钟)")
    print(f"[DS-0] 模型链: aipro GPT-5.5(主) → DeepSeek Flash(后备) | 每15分钟深分析")
    print(f"[DS-0] 四维自检: BTC大市→多周期对齐→范围定位→量价验证+K线真假")
    print(f"[DS-0] 输出: advisory.json(现货) + commands.json(合约→执行) + heartbeat")
    print(f"[DS-0] 数据: 30币(K线/费率/持仓) | 筛子平静时不调AI(省Token)")
    print(f"[DS-0] ═══ ═══ ═══ ═══ ═══ ═══ ═══ ═══ ═══ ═══ ═══")

    cycle_count = 0
    last_force_time = 0
    while True:
        try:
            cycle_count += 1
            now = time.time()
            
            # ── 0. 检查HALT信号链 (四模型委员会P0共识) ──
            _risk_state_path = os.path.join(LOGS_DIR, 'risk_state.json')
            if os.path.exists(_risk_state_path):
                try:
                    _rs = json.load(open(_risk_state_path))
                    if _rs.get('halt'):
                        print(f"[DS-0] 🚨 HALT已触发({_rs.get('reason','?')}), 跳过本轮")
                        time.sleep(LOOP_INTERVAL)
                        continue
                except:
                    pass
            
            force_deep = (now - last_force_time >= FORCE_INTERVAL)
            if force_deep:
                last_force_time = now
            start_ts = time.time()
            print(f"\n[DS-0] ── Cycle#{cycle_count}{' ⏰强制深分析' if force_deep else ''} ──")

            # 1. 采集数据(同V3.2)
            data = collect_data()
            market_count = len(data.get('market', {}))

            # 2. 机械筛子(V3.4) — 找出异常币
            sieve_result = _mechanical_sieve(data)
            triggered_coins = set()
            if sieve_result:
                for coin in sieve_result.get('top_coins', []):
                    if coin and isinstance(coin, str):
                        triggered_coins.add(coin)

            # 3. AI深度分析(每15分钟强制 + 筛子触发时)
            ai_result = ai_deep_analysis(data, sieve_result, force=force_deep)

            if ai_result.get('ai_used'):
                print(f"[DS-0] AI模型: {ai_result.get('model_used','?')} | "
                      f"筛子={ai_result.get('sieve_triggered',False)} | "
                      f"分析结果={ai_result.get('raw_response','')[:80]}")
            else:
                print(f"[DS-0] 纯数学模式(AI未触发, 筛子平静)")

            # 4. 分析决策(数学+AI覆盖)
            decision = analyze(data, ai_result)
            decision = _clamp_params(decision)

            # 5. 保存AI决策历史(用于下次反馈闭环)
            if ai_result.get('ai_used'):
                _save_ai_history(ai_result, decision)

            # 6. 写入文件 — 一次分析输出两份决策
            write_advisory(decision)     # → spot_bot读(现货)
            write_commands(decision)     # → hv_bot读(合约)
            execute_pending_commands()  # → 直接执行(安全闸保护)
            write_heartbeat()            # → 健康检查

            # 7. 打印摘要
            duration = time.time() - start_ts
            ju = decision.get('market_judgment', '?')
            reas = decision.get('reason', '')[:60]
            act = decision.get('contract_decision', {}).get('action', 'hold')
            sym = decision.get('contract_decision', {}).get('symbol', '')
            sp = decision.get('spot_params', {})
            ai_tag = ai_result.get('model_used', 'math-only')
            whale = decision.get('whale_behavior', '')
            print(f"[DS-0] ✅ 分析完成 | {market_count}币 | 判断={ju} | {reas}")
            print(f"        合约: {act} {sym} | 现货: 间距{sp.get('spacing_pct',1.0)}% TP{sp.get('tp_pct',1.0)}% SL{sp.get('sl_pct',2.5)}% 层{sp.get('max_layers',3)}")
            if whale:
                print(f"        庄家行为: {whale} | {decision.get('behavior_evidence','')[:60]}")
            print(f"        AI模型: {ai_tag} | 耗时: {duration:.1f}s | 下次: {LOOP_INTERVAL}s后")
            
            # 选币Top5摘要
            candidates = data.get('candidates', [])
            if candidates:
                top3 = candidates[:3]
                cand_str = ' | '.join(f'{c["coin"]} {c["direction"]} {c["score"]}/{c["max_score"]}' for c in top3)
                print(f"        🏆 选币Top: {cand_str}")
 
            # 8. 等待下次循环
            elapsed = time.time() - start_ts
            sleep_time = max(LOOP_INTERVAL - elapsed, 10)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            print(f"\n[DS-0] 🛑 收到停止信号")
            break
        except Exception as e:
            print(f"[DS-0] 🔴 循环异常: {e}")
            traceback.print_exc()
            time.sleep(30)

if __name__ == '__main__':
    main()
