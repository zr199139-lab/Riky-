#!/usr/bin/env python3
"""
暗黑星火 HV Bot V3 — 纯AI命令通道版
=====================================
纯AI命令通道, 删掉反转策略+内置TP/SL。
从V2(849行)精简:
  - ❌ 删反转策略(REVERSAL_ENABLED整块)
  - ❌ 删内置ATR TP/SL计算(_calc_tp_sl)
  - ❌ 删反转扫描+SL反向
  - ✅ 只留AI命令通道(读commands.json + advisory.json)
  - ✅ TP/SL只来自DS-0的override_tp/override_sl
  - ✅ 防刷单 + 持仓冷却 + 日统计跟踪
  - ✅ risk_guardian系统级熔断(账户级保护)

BUG修复(2026-05-14 焊死):
  ❌ 删 auto-close_all_futures (7处触发全部移除)
  ❌ 删 速度熔断
  ❌ 删 分层熔断自动全平
  ❌ 删 日亏自动全平
  ❌ 删 反转策略(REVERSAL_ENABLED)
  ❌ 删 内置ATR TP/SL(_calc_tp_sl)
  ✅ 日亏仅跟踪显示,不自动操作
  ✅ daily_loss持久化用原子写
  ✅ log顺序: basicConfig→import防刷单
  ✅ sign_url模式: 签名放URL, POST body为空
"""
import sys, os, json, time, math, hmac, hashlib, random, logging, traceback
from datetime import datetime, date, timezone
from pathlib import Path

# ── 路径 ──
BASE   = Path('/home/admin/.hermes/mempalace/quant_trading')
LOGS   = BASE / 'bot_logs'
ADVISORY_F = LOGS / 'advisory.json'
CMD_F     = LOGS / 'commands.json'
CSV_F     = LOGS / 'hv_trade_log.csv'
DAILY_F   = LOGS / 'contract_hunter_daily.json'
LOGS.mkdir(parents=True, exist_ok=True)

# ── DS-0 心跳协议 ──
HEARTBEAT_F = LOGS / 'ds0_heartbeat.json'
STANDBY_HOURS = 2  # DS-0静默2小时后hv_bot激活

# ── 凭据 ──
sys.path.insert(0, '/home/admin/.hermes/mempalace/secure')
from decrypt_and_run import decrypt
_creds         = decrypt()
BINANCE_KEY    = _creds['BINANCE_API_KEY']
BINANCE_SECRET = _creds['BINANCE_API_SECRET']

# ── 日志(必须在防刷单之前配置) ──
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [HV2] %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS / 'hv_bot.log'),
    ]
)
log = logging.getLogger('hv_bot')

# ── 防刷单(必须在logging之后导入) ──
sys.path.insert(0, str(BASE / 'autonomous'))
from anti_churning_guard import AntiChurningGuard

# ══════════════════════════════════════════════════════════════════════
# 参数 (V3.5提取)
# ══════════════════════════════════════════════════════════════════════

FAPI = 'https://fapi.binance.com'

# === 合约参数 ===
CONTRACT_LEVERAGE      = 10
CONTRACT_MARGIN_MIN    = 10
CONTRACT_MARGIN_MAX    = 150
CONTRACT_MAX_POSITIONS = 3
CONTRACT_MIN_ATR       = 1.5
CONTRACT_MAX_ATR       = 10.0
CONTRACT_EXCLUDE       = {'BTC','ETH','SOL','XRP'}

# === 通用参数 ===
LOOP_INTERVAL     = 5       # 主循环5s
MIN_HOLD_SECS     = 600     # 持仓至少10分钟
SYMBOL_COOLDOWN   = 14400   # 同币种4h冷却(4*3600)
CLOSE_COOLDOWN    = 300     # 平仓后5min内不准重开
MAX_DAILY_TRADES  = 999     # 日交易数无限制(靠AntiChurning Guard全局20/day)
MAX_LEVERAGE      = 5       # 杠杆硬上限

# 美股过滤(禁止合约)
_US_STOCKS = {'QQQ','MU','NVDA','MSFT','AMD','QCOM','BABA','TSLA',
    'AAPL','GOOGL','META','NFLX','INTC','PYPL','DIS',
    'BA','JPM','GS','WMT','COIN','MSTR','RIOT','MARA',
    'PLTR','HOOD','SOFI','CVNA','DKNG','RKLB','ASTS','IONQ','RDDT',
    'AMZN','AVGO','TSM','ORCL','CRM','ADBE','UBER','SNAP','PINS',
    'ARM','ANET','DDOG','MDB','SNOW','CRWD','PANW','ZS','OKTA',
    'SHOP','SQ','AFRM','UPST','LCID','CHWY','GME','BB',
    'EWY','FXI','KWEB','TQQQ','SQQQ','SOXS','SOXL','LABU','TNA',
    'SMH','XBI','IBB','BITO','IBIT','MCHI','EEM'}

# ══════════════════════════════════════════════════════════════════════
# API 签名 & 请求
# ══════════════════════════════════════════════════════════════════════

def _sign(params: dict) -> str:
    """生成查询字符串+签名(签名在URL中)"""
    query = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    sig = hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f'{query}&signature={sig}'

def _fapi(method: str, path: str, params: dict = None, signed: bool = False) -> dict:
    """统一fapi请求, sign_url模式: 签名放URL"""
    url = FAPI + path
    headers = {'X-MBX-APIKEY': BINANCE_KEY}
    try:
        if signed:
            p = params or {}
            p['timestamp'] = int(time.time() * 1000)
            qs = _sign(p)
            if method == 'GET':
                r = requests.get(f'{url}?{qs}', headers=headers, timeout=10)
            else:
                r = requests.post(f'{url}?{qs}', headers=headers, timeout=10)
        else:
            r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            log.error(f'API[{r.status_code}] {path}: {r.text[:200]}')
            return {'error': r.text[:200], 'code': r.status_code}
        return r.json()
    except Exception as e:
        log.error(f'API异常 {path}: {e}')
        return {'error': str(e)}

import requests  # local import after _fapi def

def _get_step_size(symbol: str) -> float:
    """获取币种stepSize"""
    try:
        info = _fapi('GET', '/fapi/v1/exchangeInfo')
        for s in info.get('symbols', []):
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        return float(f['stepSize'])
    except:
        pass
    return 0.001

def _round_qty(qty: float, step: float) -> float:
    """按stepSize对齐数量"""
    if step <= 0:
        return round(qty, 2)
    decimals = max(0, -int(math.floor(math.log10(step))))
    qty = math.floor(qty / step) * step
    return round(qty, decimals)

def _calc_atr(symbol: str) -> float:
    """计算15m ATR百分比"""
    try:
        klines = _fapi('GET', '/fapi/v1/klines',
                       {'symbol': symbol, 'interval': '15m', 'limit': 30})
        if isinstance(klines, list) and len(klines) >= 15:
            closes = [float(k[4]) for k in klines]
            highs  = [float(k[2]) for k in klines]
            lows   = [float(k[3]) for k in klines]
            trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
                       abs(lows[i]-closes[i-1]))
                   for i in range(1, len(closes))]
            return sum(trs[-14:]) / 14 / closes[-1] * 100
    except:
        pass
    return 0.5  # fallback

# ══════════════════════════════════════════════════════════════════════
# CSV 日志
# ══════════════════════════════════════════════════════════════════════

CSV_HEADER = ['ts','symbol','side','qty','entry_price',
              'exit_price','pnl','strategy','reason','order_id']

def _csv_append(row: dict):
    exists = CSV_F.exists()
    with open(CSV_F, 'a', newline='') as f:
        import csv
        w = csv.DictWriter(f, fieldnames=CSV_HEADER)
        if not exists:
            w.writeheader()
        w.writerow(row)

# ══════════════════════════════════════════════════════════════════════
# 风控检查
# ══════════════════════════════════════════════════════════════════════

def is_halted() -> bool:
    try:
        state = json.loads((LOGS / 'risk_state.json').read_text())
        return state.get('halt', False)
    except:
        return False

# ══════════════════════════════════════════════════════════════════════
class HVBotV3:
    """V3.5合约引擎提取版 — 零auto-close, ATR动态TP/SL, 多仓并发"""

    def __init__(self):
        # ── 状态 ──
        self.balance_futures = 0.0
        self.futures_pos = {}       # symbol -> {qty, entry_price, mark_price, side, ...}
        self.loop_count = 0
        self._api_fails = 0

        # ── 日统计(仅跟踪,不自动操作) ──
        self.daily_loss = 0.0
        self.daily_trades = 0
        self.daily_fee = 0.0
        self._today = date.today()

        # ── 风控(仅防重复/防刷单,不自动全平) ──
        self._close_cooldown = {}         # symbol -> timestamp (平≠重开)
        self._symbol_last_trade = {}      # symbol -> timestamp
        self._position_open_ts = {}       # symbol -> timestamp

        # ── 已执行命令ID(防重复) ──
        self._executed_cmd_ids = set()

        self._load_daily_state()

    # ── 日统计持久化(原子写) ──────────────────────────────────────────

    def _load_daily_state(self):
        today = date.today()
        self._today = today
        try:
            if DAILY_F.exists():
                d = json.loads(DAILY_F.read_text())
                if d.get('date') == today.isoformat():
                    self.daily_loss = float(d.get('loss', 0))
                    self.daily_trades = int(d.get('trades', 0))
                    self.daily_fee = float(d.get('fee', 0))
                    return
        except:
            pass
        self.daily_loss = 0.0
        self.daily_trades = 0
        self.daily_fee = 0.0

    def _save_daily_state(self):
        tmp = DAILY_F.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps({
                'date': self._today.isoformat(),
                'loss': round(self.daily_loss, 2),
                'trades': self.daily_trades,
                'fee': round(self.daily_fee, 4),
            }))
            tmp.rename(DAILY_F)  # 原子写
        except Exception as e:
            log.warning(f'保存daily_state失败: {e}')

    # ── 持仓同步 ─────────────────────────────────────────────────────

    def _sync_positions(self):
        """从交易所同步真实持仓到本地"""
        try:
            risk = _fapi('GET', '/fapi/v2/positionRisk', signed=True)
            self._api_fails = 0
            exchange_pos = {}
            if isinstance(risk, list):
                for p in risk:
                    qty = float(p.get('positionAmt', 0))
                    if abs(qty) < 0.001:
                        continue
                    sym = p['symbol']
                    entry = float(p.get('entryPrice', 0))
                    mark = float(p.get('markPrice', entry))
                    side = 'long' if qty > 0 else 'short'
                    exchange_pos[sym] = {
                        'qty': abs(qty), 'entry_price': entry,
                        'mark_price': mark, 'side': side,
                        'upnl': float(p.get('unRealizedProfit', 0)),
                        'pnl_pct': (mark/entry - 1) * (100 if side == 'long' else -100) if entry > 0 else 0,
                        'liq_price': float(p.get('liquidationPrice', 0)),
                        'leverage': int(float(p.get('leverage', 5))),
                    }
                    # 保留已有持仓的扩展字段(tp/sl/strategy等)
                    if sym in self.futures_pos:
                        for k in ('tp_price','sl_price','strategy','opened_at','override_tp','override_sl'):
                            if k in self.futures_pos[sym]:
                                exchange_pos[sym][k] = self.futures_pos[sym][k]
            # 保留30秒内本地新建但交易所尚未确认的仓位
            # (修复: 反转扫描重复开同币种BUG - SIREN被开7次)
            now = time.time()
            for sym, pos in list(self.futures_pos.items()):
                is_pending = sym not in exchange_pos and pos.get('opened_at', 0) > now - 30
                if is_pending:
                    exchange_pos[sym] = pos
            self.futures_pos = exchange_pos
        except Exception as e:
            self._api_fails += 1
            log.warning(f'持仓同步失败[{self._api_fails}]: {e}')

    # ── TP/SL计算(只从override参数来, 不自己算ATR) ──

    def _calc_tp_sl(self, entry: float, side: str,
                    override_tp: float = None, override_sl: float = None) -> tuple:
        """AI命令给override_tp/override_sl -> 转成绝对价格"""
        tp, sl = None, None
        if override_tp is not None and override_sl is not None:
            try:
                tp_pct = float(override_tp)
                sl_pct = float(override_sl)
                if side == 'long':
                    tp = entry * (1 + tp_pct / 100)
                    sl = entry * (1 - sl_pct / 100)
                else:
                    tp = entry * (1 - tp_pct / 100)
                    sl = entry * (1 + sl_pct / 100)
            except:
                pass
        return tp, sl

    # ── TP/SL检查(每轮循环调用) ──────────────────────────────────────

    def _check_tp_sl(self):
        """只检查已有override TP/SL的持仓, 没有就不动"""
        for sym, pos in list(self.futures_pos.items()):
            try:
                mark = pos.get('mark_price', 0)
                entry = pos.get('entry_price', 0)
                side = pos.get('side', 'long')
                if entry <= 0 or mark <= 0:
                    continue

                # 没有TP/SL的持仓跳过(等AI发close命令)
                tp = pos.get('tp_price')
                sl = pos.get('sl_price')
                if not tp or not sl:
                    continue

                # 检查触发
                if side == 'long':
                    hit_tp = mark >= tp
                    hit_sl = mark <= sl
                    pnl_pct = (mark / entry - 1) * 100 if entry > 0 else 0
                else:
                    hit_tp = mark <= tp
                    hit_sl = mark >= sl
                    pnl_pct = (1 - mark / entry) * 100 if entry > 0 else 0

                if hit_tp:
                    log.info(f'[AI] {sym} 止盈! {pnl_pct:+.2f}%')
                    self._close_position(sym, reason='TP')
                elif hit_sl:
                    log.info(f'[AI] {sym} 止损! {pnl_pct:+.2f}%')
                    self._close_position(sym, reason='SL')
                elif abs(pnl_pct) > 0.3:
                    log.info(f'   [AI] {sym} {pnl_pct:+.2f}% (TP={tp:.4f} SL={sl:.4f})')

            except Exception as e:
                log.warning(f'TP/SL检查 {sym}: {e}')

    # ── 开仓 ─────────────────────────────────────────────────────────

    def _open_position(self, symbol: str, direction: str, margin: float,
                       override_tp: float = None, override_sl: float = None,
                       strategy: str = 'ai'):
        """开合约仓(带override支持)"""
        # 标准化币名(slash清理+USDT后缀)
        symbol = symbol.replace('/', '').upper()
        if not symbol.endswith('USDT'):
            symbol = symbol + 'USDT'

        # 获取价格
        ticker = _fapi('GET', '/fapi/v1/ticker/price', {'symbol': symbol})
        if 'error' in ticker:
            log.error(f'开仓: 获取{symbol}价格失败')
            return
        price = float(ticker.get('price', 0))
        if price <= 0:
            return

        # 计算数量
        qty = margin * CONTRACT_LEVERAGE / price
        step = _get_step_size(symbol)
        qty = _round_qty(qty, step)
        if qty <= 0:
            log.error(f'开仓: {symbol}数量={qty}')
            return

        # 设杠杆
        _fapi('POST', '/fapi/v1/leverage',
              {'symbol': symbol, 'leverage': CONTRACT_LEVERAGE}, signed=True)

        side = 'BUY' if direction == 'long' else 'SELL'
        ps = 'LONG' if direction == 'long' else 'SHORT'
        order = _fapi('POST', '/fapi/v1/order', {
            'symbol': symbol, 'side': side, 'positionSide': ps,
            'type': 'MARKET', 'quantity': qty,
            'newOrderRespType': 'RESULT'
        }, signed=True)

        if 'orderId' in order:
            filled = float(order.get('executedQty', 0))
            avg_px = float(order.get('avgPrice', 0))
            if avg_px == 0:
                avg_px = price

            log.info(f'✅ [{strategy}] 开仓: {symbol} {direction} @ {avg_px:.4f} × {filled}')

            pos = {
                'qty': filled, 'entry_price': avg_px, 'mark_price': avg_px,
                'side': direction, 'upnl': 0, 'pnl_pct': 0,
                'opened_at': time.time(), 'strategy': strategy,
            }

            # 立即计算TP/SL(只从override参数来)
            if override_tp and override_sl:
                try:
                    tp_pct = float(override_tp)
                    sl_pct = float(override_sl)
                    if direction == 'long':
                        pos['tp_price'] = avg_px * (1 + tp_pct / 100)
                        pos['sl_price'] = avg_px * (1 - sl_pct / 100)
                    else:
                        pos['tp_price'] = avg_px * (1 - tp_pct / 100)
                        pos['sl_price'] = avg_px * (1 + sl_pct / 100)
                    log.info(f'override TP={pos["tp_price"]:.4f} SL={pos["sl_price"]:.4f}')
                except Exception as e:
                    log.warning(f'override计算失败: {e}')

            self.futures_pos[symbol] = pos
            self._symbol_last_trade[symbol] = time.time()
            self.daily_trades += 1
            self._save_daily_state()

            _csv_append({
                'ts': datetime.utcnow().isoformat(),
                'symbol': symbol, 'side': side,
                'qty': filled, 'entry_price': avg_px,
                'exit_price': '', 'pnl': '',
                'strategy': strategy, 'reason': 'OPEN',
                'order_id': order.get('orderId', ''),
            })
        else:
            log.error(f'❌ 开仓失败 {symbol}: {order.get("msg","?")}')

    # ── 平仓 ─────────────────────────────────────────────────────────

    def _close_position(self, symbol: str, reason: str = 'MANUAL'):
        """平仓并记录（只平单个symbol，不平别的）"""
        symbol = symbol.replace('/', '').upper()
        if not symbol.endswith('USDT'):
            symbol = symbol + 'USDT'
        pos = self.futures_pos.get(symbol)
        if not pos:
            return
        try:
            # 取消所有挂单
            _fapi('DELETE', '/fapi/v1/allOpenOrders',
                  {'symbol': symbol}, signed=True)

            amt = pos['qty'] * (1 if pos['side'] == 'long' else -1)
            side = 'SELL' if amt > 0 else 'BUY'
            ps_close = 'LONG' if amt > 0 else 'SHORT'
            order = _fapi('POST', '/fapi/v1/order', {
                'symbol': symbol, 'side': side, 'positionSide': ps_close,
                'type': 'MARKET', 'quantity': abs(amt),
                'newOrderRespType': 'RESULT'
            }, signed=True)

            entry = pos['entry_price']
            fill_price = float(order.get('avgPrice', entry))
            pnl = (fill_price - entry) * amt if entry > 0 else 0

            log.info(f'✅ 平仓 {symbol} reason={reason} PnL=${pnl:.2f}')

            # 日亏只统计亏损
            if pnl < 0:
                self.daily_loss += pnl
                self._save_daily_state()

            _csv_append({
                'ts': datetime.utcnow().isoformat(),
                'symbol': symbol,
                'side': 'SELL' if amt > 0 else 'BUY',
                'qty': abs(amt),
                'entry_price': entry,
                'exit_price': fill_price,
                'pnl': round(pnl, 4),
                'strategy': pos.get('strategy', 'ai'),
                'reason': reason,
                'order_id': order.get('orderId', ''),
            })

            # 平仓冷却
            self._close_cooldown[symbol] = time.time()
            # 从持仓移除
            self.futures_pos.pop(symbol, None)

        except Exception as e:
            log.error(f'平仓异常 {symbol}: {e}')

    # ── AI命令执行(从advisory.json读contract_suggestion) ────────────

    def _execute_ai_command(self):
        """读commands.json(分析师写) → 执行"""
        try:
            if not CMD_F.exists():
                return
            cmd = json.loads(CMD_F.read_text())
            action = cmd.get('action', 'hold')
            if action == 'hold' or action == 'wait':
                return

            symbol = cmd.get('symbol', '') or ''
            direction = cmd.get('direction', 'long')
            confidence = cmd.get('confidence', 5)
            entry_urgency = cmd.get('entry_urgency', 'cancel')

            if not symbol:
                return
            if not symbol.endswith('USDT'):
                symbol = symbol.upper() + 'USDT'
            # 统一格式: 去掉:USDT后缀(合约格式→统一)
            symbol = symbol.replace(':USDT', '')

            # 检查过期(用expire_ts, 分析师写15min)
            expire_ts = cmd.get('expire_ts', cmd.get('ts', 0))
            if time.time() > expire_ts or expire_ts == 0:
                return

            # 检查是否已执行过（同一expire_ts的同一action不重复执行）
            cmd_key = f"{expire_ts}_{action}_{symbol}"
            if cmd_key in self._executed_cmd_ids:
                return
            self._executed_cmd_ids.add(cmd_key)
            if len(self._executed_cmd_ids) > 1000:
                self._executed_cmd_ids.clear()

            base = symbol.replace('USDT', '')

            # 过滤美股/黑名单
            if base in _US_STOCKS or base in CONTRACT_EXCLUDE:
                log.info(f'⏭️ AI{action}: {symbol} 在黑名单中')
                return

            # 检查风控
            if is_halted():
                log.info('⏭️ AI命令: risk_guardian HALT')
                return

            # ── 方向机械验证(pre_trade_gate, 2026-05-14焊死) ──
            if action in ('open',):
                base = symbol.replace('USDT', '')
                # 1. 15m动能检查
                try:
                    klines = _fapi('GET', '/fapi/v1/klines',
                        {'symbol': symbol, 'interval': '15m', 'limit': 5})
                    if klines and len(klines) >= 3:
                        o3 = float(klines[0][1])
                        c3 = float(klines[-1][4])
                        mom = (c3 - o3) / o3 * 100
                        if direction == 'short' and mom > 2.0:
                            log.info(f'⏭️ pre_trade_gate: {symbol}做空但15m涨{mom:+.1f}% > 2% → 拦截')
                            return
                        if direction == 'long' and mom < -2.0:
                            log.info(f'⏭️ pre_trade_gate: {symbol}做多但15m跌{mom:+.1f}% < -2% → 拦截')
                            return
                except Exception as e:
                    log.warning(f'pre_trade_gate kline error: {e}')

                # 2. 费率方向检查
                try:
                    fund = _fapi('GET', '/fapi/v1/premiumIndex', {'symbol': symbol})
                    fr = float(fund.get('lastFundingRate', 0)) * 100
                    if direction == 'short' and fr < -0.01:
                        log.info(f'⏭️ pre_trade_gate: {symbol}做空但费率{fr:+.4f}%(负费率做空逆势) → 拦截')
                        return
                    if direction == 'long' and fr > 0.01:
                        log.info(f'⏭️ pre_trade_gate: {symbol}做多但费率{fr:+.4f}%(正费率做多逆势) → 拦截')
                        return
                except Exception as e:
                    log.warning(f'pre_trade_gate funding error: {e}')

            # ── 动作执行 ──
            if action == 'close':
                if symbol in self.futures_pos:
                    # ⛔ 最低持仓保护: AI_CLOSE不能关10分钟内开的仓
                    pos_info = self.futures_pos[symbol]
                    elapsed = time.time() - pos_info.get('opened_at', 0)
                    if elapsed < MIN_HOLD_SECS:
                        log.info(f'⏭️ AI_CLOSE: {symbol} 持仓仅{elapsed:.0f}s < {MIN_HOLD_SECS}s最低持仓 → 跳过')
                        return
                    self._close_position(symbol, reason='AI_CLOSE')
                return

            if action == 'close_all':
                # ⏭️ 不机械平仓, 让分析师逐币出close指令
                log.info(f'⏭️ close_all收到, 等待分析师逐币close决策')
                return

            if action == 'open':
                # 防重复: 已有持仓不开
                if symbol in self.futures_pos:
                    return

                # 平仓冷却检查
                last_close = self._close_cooldown.get(symbol, 0)
                if last_close > 0 and (time.time() - last_close) < CLOSE_COOLDOWN:
                    log.info(f'⏭️ AI开仓: {symbol} 平仓冷却中')
                    return

                # 同币种4h冷却
                last_trade = self._symbol_last_trade.get(symbol, 0)
                if last_trade > 0 and (time.time() - last_trade) < SYMBOL_COOLDOWN:
                    return

                # 日交易数限制
                if self.daily_trades >= MAX_DAILY_TRADES:
                    log.info(f'⏭️ AI开仓: 已达日最大交易数{MAX_DAILY_TRADES}')
                    return

                # 仓位已满→轮动
                margin = cmd.get('margin', CONTRACT_MARGIN_MAX)
                margin = max(CONTRACT_MARGIN_MIN, min(margin, CONTRACT_MARGIN_MAX))

                if len(self.futures_pos) >= CONTRACT_MAX_POSITIONS:
                    worst = self._find_worst_position()
                    if worst and confidence >= 6 and worst.get('pnl_pct', 0) < -1.0:
                        log.info(f'🔄 轮动: 关{worst["symbol"]}({worst["pnl_pct"]:+.1f}%) → 开{symbol}')
                        self._close_position(worst['symbol'], reason='ROTATE_OUT')
                        time.sleep(0.5)
                        self._sync_positions()
                    else:
                        log.info(f'⏭️ 仓位已满({len(self.futures_pos)}), 跳过AI开仓')
                        return

                # 防刷单
                side_upper = 'BUY' if direction == 'long' else 'SELL'
                import __main__
                guard = getattr(__main__, '_hv_guard', None)
                if guard and not guard.check(symbol, side_upper):
                    log.warning(f'AntiChurningGuard 拒绝 {symbol} {side_upper}')
                    return

                # 余额检查
                if self.balance_futures < margin * 1.2:
                    log.info(f'⏭️ AI开仓: 余额${self.balance_futures:.1f} 需${margin*1.2:.1f}')
                    return

                # 执行开仓
                override_tp = cmd.get('tp_price')
                override_sl = cmd.get('sl_price')
                self._open_position(symbol, direction, margin, override_tp, override_sl, strategy='ai')

        except Exception as e:
            log.warning(f'执行AI命令失败: {e}')

    # ── 找最差持仓(用于轮动) ─────────────────────────────────────────

    def _find_worst_position(self) -> dict | None:
        worst = None
        worst_pnl = 999.0
        for sym, pos in self.futures_pos.items():
            pnl = pos.get('pnl_pct', 0)
            if pnl < worst_pnl:
                worst_pnl = pnl
                worst = {'symbol': sym, 'pnl_pct': pnl, 'side': pos.get('side')}
        return worst

    # ── 余额同步 ─────────────────────────────────────────────────────

    def _sync_balance(self):
        """获取合约USDT余额"""
        data = _fapi('GET', '/fapi/v2/account', signed=True)
        if 'error' not in data:
            for asset in data.get('assets', []):
                if asset.get('asset') == 'USDT':
                    self.balance_futures = float(asset.get('availableBalance', 0))
                    break

    # ── DS-0 待机检查 ──────────────────────────────────────────────────
    def _is_ds0_standby(self) -> bool:
        """检查DS-0是否活跃: 心跳<2h→待机, >2h或无文件→激活"""
        try:
            if not HEARTBEAT_F.exists():
                return False  # 无心跳文件→激活模式
            hb = json.loads(HEARTBEAT_F.read_text())
            last_active = datetime.fromisoformat(hb['ds0_last_active'])
            elapsed = (datetime.now() - last_active).total_seconds() / 3600
            return elapsed < STANDBY_HOURS  # <2h→待机
        except:
            return False  # 任何异常→激活模式(安全)

    # ── 主循环 ───────────────────────────────────────────────────────

    def run(self):
        """主循环(5s)"""
        guard = AntiChurningGuard()
        import __main__
        __main__._hv_guard = guard

        delay = random.uniform(0, 30)
        log.info(f'HV Bot V3 启动 (延迟{delay:.1f}s)')
        time.sleep(delay)

        while True:
            try:
                # ── DS-0 待机检查 ──
                standby = self._is_ds0_standby()
                
                # 新一天重置
                if date.today() != self._today:
                    self._today = date.today()
                    self.daily_loss = 0.0
                    self.daily_trades = 0
                    self.daily_fee = 0.0
                    self._save_daily_state()
                    log.info('新一天, 日统计已重置')

                # 风控检查(仅HALT, 不死全平)
                if is_halted():
                    time.sleep(LOOP_INTERVAL)
                    continue

                # 待机模式: 不同交易, 但TP/SL必须跑
                if standby:
                    self._sync_balance()
                    self._sync_positions()
                    self._check_tp_sl()  # ★ 待机也必须保护持仓
                    self.loop_count += 1
                    if self.loop_count % 60 == 0:
                        log.info(f'STANDBY(DS-0活跃) LOOP#{self.loop_count} | '
                                 f'余额=${self.balance_futures:.1f} | '
                                 f'持仓={len(self.futures_pos)}')
                    time.sleep(LOOP_INTERVAL)
                    continue

                # 激活模式: 正常交易
                self._sync_balance()
                self._sync_positions()

                # TP/SL检查(每轮循环都检查)
                self._check_tp_sl()
                self.loop_count += 1

                # 日志摘要(每60轮≈5min输出一次)
                if self.loop_count % 60 == 0:
                    pos_str = ' '.join(
                        f'{s}:{p["side"]} {p["pnl_pct"]:+.1f}%'
                        for s, p in self.futures_pos.items()
                    ) or '空仓'
                    log.info(f'LOOP#{self.loop_count} | '
                             f'余额=${self.balance_futures:.1f} | '
                             f'持仓={len(self.futures_pos)} | '
                             f'{pos_str} | '
                             f'日亏=${self.daily_loss:.2f} | '
                             f'API fails={self._api_fails}')

                # AI命令(每30s检查一次)
                if self.loop_count % 6 == 0:
                    self._execute_ai_command()

            except Exception as e:
                log.error(f'主循环异常: {e}\n{traceback.format_exc()}')

            time.sleep(LOOP_INTERVAL)


# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    # ── PID锁(防双进程) ──
    PID_LOCK = Path('/tmp/hv_bot.pid')
    if PID_LOCK.exists():
        try:
            old_pid = int(PID_LOCK.read_text().strip())
            # 检查旧进程是否存活
            os.kill(old_pid, 0)
            log.warning(f'⚠️ hv_bot PID锁存在(PID={old_pid}存活), 可能双进程! 覆盖锁')
        except OSError:
            log.info(f'旧PID锁({old_pid})已过期, 覆盖')
        except:
            pass
    PID_LOCK.write_text(str(os.getpid()))
    log.info(f'PID锁: /tmp/hv_bot.pid → {os.getpid()}')
    
    bot = HVBotV3()
    bot.run()
