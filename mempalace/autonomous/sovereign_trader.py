#!/usr/bin/env python3
"""
暗黑星火 · 主权AI交易系统 v7.1
================================
架构变更(V7, 2026-05-11):
  - 删除pool4 AI快速平仓(300s限频块)
  - 分析师全权控制: 同时管开仓和平仓决策
  - 最低持仓时间15分钟保护
  - 硬限制: 最多1仓,仅ETH,DOGE/XRP/SOL合约黑名单
  - 每笔决策附带分析理由,存入交易记忆

核心理念: 每个决策都有分析,不再有哑巴操作
"""

import os, sys, json, time, math, hmac, hashlib, traceback, threading
from datetime import datetime
# CEO模块已删除 — 2026-05-11 DS-0
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Optional
import numpy as np
import ccxt
import requests

# ── 路径 ──
BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "..", "bot_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ── 硬编码保护(不可进化) ──
MAX_LEVERAGE = 5
HARD_MAX_POSITIONS = 1

# ── 强制评估队列(外部注入,用户要求特定币种时用) ──
FORCE_SYMBOLS = []  # 格式: ["ETH/USDT:USDT"], 单次触发后自动清空
HARD_STOP_LOSS_PCT = 0.06
# TRADING_UNIVERSE 已删除 — V4全市场选币,不再硬限币种

# ── 美股过滤 ──
US_STOCKS = {'QQQ','MU','NVDA','MSFT','AMD','QCOM','BABA','TSLA',
    'AAPL','GOOGL','META','NFLX','INTC','PYPL','DIS',
    'BA','JPM','GS','WMT','COIN','MSTR','RIOT','MARA',
    'PLTR','HOOD','SOFI','CVNA','DKNG','RKLB','ASTS','IONQ','RDDT',
    'AMZN','AVGO','TSM','ORCL','CRM','ADBE','UBER','SNAP','PINS',
    'ARM','ANET','DDOG','MDB','SNOW','CRWD','PANW','ZS','OKTA',
    'SHOP','SQ','AFRM','UPST','LCID','CHWY','GME','BB',
    'EWY','FXI','KWEB','TQQQ','SQQQ','SOXS','SOXL','LABU','TNA',
    'SMH','XBI','IBB','BITO','IBIT','MCHI','EEM'}

# ── 合约黑名单(V7): 过去两天证明亏钱的币,禁止开合约 — 2026-05-11复盘
CONTRACT_BLACKLIST = {'DOGE','XRP','SOL','ATOM'}

STATE_FILE = os.path.join(LOG_DIR, "sovereign_state.json")
CONFIG_FILE = os.path.join(LOG_DIR, "sovereign_config.json")
TRADE_MEM_FILE = os.path.join(LOG_DIR, "sovereign_trade_memory.json")
EVO_FILE = os.path.join(LOG_DIR, "sovereign_evolution.json")
EVO_LOG = os.path.join(LOG_DIR, "evolution_changelog.md")
ANOMALY_LOG = os.path.join(LOG_DIR, "sovereign_anomalies.json")  # 自审计异常记录
HEAL_LOG = os.path.join(LOG_DIR, "sovereign_heal_log.json")      # 自修复历史

sys.path.insert(0, os.path.expanduser("~/.hermes/mempalace/secure"))
from decrypt_and_run import decrypt

# ══════════════════════════════════════════════════════════
# 日志
# ══════════════════════════════════════════════════════════
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] 👑 {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, "sovereign_trader.log"), "a") as f:
        f.write(line + "\n")

# ══════════════════════════════════════════════════════════
# LAYER 2: 交易记忆系统
# ══════════════════════════════════════════════════════════
@dataclass
class TradeRecord:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    margin: float
    pnl: float
    pnl_pct: float
    entry_time: float
    exit_time: float
    exit_reason: str  # stop_loss / take_profit / ai_close / timeout

class TradeMemory:
    """每笔平仓记录 + 模式分析 → 注入AI提示"""
    def __init__(self):
        self.records = deque(maxlen=200)
        self.total = 0; self.wins = 0; self.losses = 0; self.total_pnl = 0.0
        self.sym_stats = defaultdict(lambda: {"w":0,"l":0,"pnl":0.0})
        self.lw = 0; self.ll = 0; self.sw = 0; self.sl = 0

    def add(self, symbol, side, entry, exit_px, margin, pnl, reason):
        pnl_pct = pnl / margin * 100 if margin > 0 else 0
        r = TradeRecord(symbol, side, entry, exit_px, margin, pnl, pnl_pct,
                        entry, time.time(), reason)
        self.records.append(r)
        self.total += 1; self.total_pnl += pnl
        sym = symbol.split('/')[0]
        if pnl > 0:
            self.wins += 1; self.sym_stats[sym]["w"] += 1
            if side == "long": self.lw += 1
            else: self.sw += 1
        else:
            self.losses += 1; self.sym_stats[sym]["l"] += 1
            if side == "long": self.ll += 1
            else: self.sl += 1
        self.sym_stats[sym]["pnl"] += pnl
        self._save()

    def summary(self):
        """注入AI的摘要文本"""
        if self.total == 0:
            return "【交易记忆】暂无交易记录"
        wr = self.wins/self.total*100 if self.total else 0
        avg_w = sum(r.pnl for r in self.records if r.pnl > 0) / max(1, self.wins)
        avg_l = sum(r.pnl for r in self.records if r.pnl <= 0) / max(1, self.losses)
        ratio = abs(avg_w/avg_l) if avg_l != 0 else 0
        lwr = self.lw/max(1,self.lw+self.ll)*100
        swr = self.sw/max(1,self.sw+self.sl)*100
        best = sorted(self.sym_stats.items(), key=lambda x: -x[1]["pnl"])[:3]
        worst = sorted(self.sym_stats.items(), key=lambda x: x[1]["pnl"])[:3]
        bstr = " | ".join(f"{s}:{d['pnl']:+.2f}({d['w']}W/{d['l']}L)" for s,d in best) or "无"
        wstr = " | ".join(f"{s}:{d['pnl']:+.2f}({d['w']}W/{d['l']}L)" for s,d in worst) or "无"
        recent = list(self.records)[-5:]
        rpnl = sum(r.pnl for r in recent)
        rstr = "→".join("盈" if r.pnl>0 else"亏" for r in recent) if recent else "无"
        return (
            f"【交易记忆】{self.total}笔 | 胜率{wr:.0f}% | "
            f"总PnL${self.total_pnl:+.2f}\n"
            f"均盈${avg_w:+.2f} | 均亏${avg_l:+.2f} | 盈亏比{ratio:.1f}x\n"
            f"做多{lwr:.0f}%胜({self.lw}W/{self.ll}L) | "
            f"做空{swr:.0f}%胜({self.sw}W/{self.sl}L)\n"
            f"最佳: {bstr}\n最差: {wstr}\n"
            f"近5笔({rpnl:+.2f}): {rstr}"
        )

    def pattern_analysis(self):
        """供进化引擎使用的详细分析"""
        if self.total < 3: return {}
        records = list(self.records)
        sl_cnt = sum(1 for r in records if r.exit_reason == "stop_loss")
        tp_cnt = sum(1 for r in records if r.exit_reason == "take_profit")
        ai_cnt = sum(1 for r in records if r.exit_reason == "ai_close")
        to_cnt = sum(1 for r in records if r.exit_reason == "timeout")
        recent_pnl = sum(r.pnl for r in records[-min(10,len(records)):])
        return {
            "exit_reasons": {"stop_loss":sl_cnt,"take_profit":tp_cnt,"ai_close":ai_cnt,"timeout":to_cnt},
            "recent_10_pnl": recent_pnl,
            "long_wr": self.lw/max(1,self.lw+self.ll)*100,
            "short_wr": self.sw/max(1,self.sw+self.sl)*100,
        }

    def _save(self):
        try:
            data = {"total":self.total,"wins":self.wins,"losses":self.losses,
                    "total_pnl":self.total_pnl,"lw":self.lw,"ll":self.ll,
                    "sw":self.sw,"sl":self.sl,
                    "sym_stats":dict(self.sym_stats),
                    "records":[asdict(r) for r in self.records]}
            with open(TRADE_MEM_FILE,"w") as f: json.dump(data,f,indent=2)
        except: pass

    def load(self):
        if not os.path.exists(TRADE_MEM_FILE): return
        try:
            with open(TRADE_MEM_FILE) as f: data = json.load(f)
            self.total = data.get("total",0); self.wins = data.get("wins",0)
            self.losses = data.get("losses",0); self.total_pnl = data.get("total_pnl",0.0)
            self.lw = data.get("lw",0); self.ll = data.get("ll",0)
            self.sw = data.get("sw",0); self.sl = data.get("sl",0)
            for sym,st in data.get("sym_stats",{}).items(): self.sym_stats[sym]=st
            for r in data.get("records",[]): self.records.append(TradeRecord(**r))
            log(f"📂 加载交易记忆: {self.total}笔 总PnL=${self.total_pnl:+.2f}")
        except: pass

# ══════════════════════════════════════════════════════════
# LAYER 3: 智能进化引擎
# ══════════════════════════════════════════════════════════
class SmartEvolution:
    def __init__(self, memory):
        self.memory = memory
        self.last_run = 0
        self.count = 0
        self.params = {
            "stop_loss_pct": 0.04,
            "take_profit_pct": 0.06,
            "min_score": 6.0,
            "pos_size_usdt": 12.0,
            "long_bias": 0.5,
            "preferred": [],
            "blacklist": [],
            "rules": [],
        }
        self.history = []
        self.load()

    def detect_regime(self, market_data):
        """检测市场状态: volatile / trending / ranging"""
        if not market_data: return "unknown"
        atrs = [d.get("atr_pct",0) for d in market_data.values() if d.get("atr_pct")]
        if not atrs: return "unknown"
        avg = np.mean(atrs)
        if avg > 0.03: return "volatile"  # 高波→短线
        if avg > 0.015: return "trending"  # 趋势
        return "ranging"  # 震荡

    def build_prompt(self, regime, bal, pos_count, total_pnl, pattern):
        sym_analysis = self.memory.sym_stats
        profitable = [(s,d) for s,d in sym_analysis.items() if d["pnl"]>0]
        losing = [(s,d) for s,d in sym_analysis.items() if d["pnl"]<0]
        pstr = "\n".join(f"  {s}: {d['pnl']:+.2f} ({d['w']}W/{d['l']}L)" for s,d in sorted(profitable,key=lambda x:-x[1]["pnl"])[:5]) or "  无"
        lstr = "\n".join(f"  {s}: {d['pnl']:+.2f} ({d['w']}W/{d['l']}L)" for s,d in sorted(losing,key=lambda x:x[1]["pnl"])[:5]) or "  无"
        exit_r = pattern.get("exit_reasons",{})
        eff = [e for e in self.history[-5:] if e.get("effective")]
        evo_str = "\n".join(f"  {e['ts']}: {e['changes']}→${e.get('pnl_after',0)-e.get('pnl_before',0):+.2f}" for e in eff) or "  无"
        current_str = json.dumps(self.params, indent=2)

        return f"""【主权AI · 进化诊断 #{self.count+1}】
余额=${bal:.2f} | 持仓={pos_count} | 总PnL=${total_pnl:+.2f}
市场状态: {regime}

当前参数:
{current_str}

交易模式:
总交易: {self.memory.total}笔 | 胜率{self.memory.wins/max(1,self.memory.total)*100:.0f}%
均盈${sum(r.pnl for r in self.memory.records if r.pnl>0)/max(1,self.memory.wins):+.2f} | 均亏${sum(r.pnl for r in self.memory.records if r.pnl<=0)/max(1,self.memory.losses):+.2f}
最近10笔PnL: ${pattern.get('recent_10_pnl',0):+.2f}
做多{pattern.get('long_wr',0):.0f}%胜率 | 做空{pattern.get('short_wr',0):.0f}%胜率
止损{exit_r.get('stop_loss',0)}次 | 止盈{exit_r.get('take_profit',0)}次 | AI平{exit_r.get('ai_close',0)}次

盈利标的:
{pstr}

亏损标的:
{lstr}

历史进化效果:
{evo_str}

基于以上真实数据，分析当前策略的核心问题并给出有数据支撑的参数调整。
输出JSON:
{{
  "market_analysis": "一句话总结市场",
  "findings": ["关键发现(附数据)"],
  "param_changes": {{
    "stop_loss_pct": 0.04, "take_profit_pct": 0.06, "min_score": 6.0,
    "pos_size_usdt": 12.0, "long_bias": 0.5
  }},
  "preferred_add": ["看好的币"],
  "blacklist_add": ["要拉黑的币"],
  "new_rules": ["具体交易规则"],
  "reasoning": "数据驱动的调整理由"
}}
可改范围: stop_loss_pct[0.02-0.06], take_profit_pct[0.03-0.10], min_score[3-9], pos_size_usdt[5-20], long_bias[0-1]"""

    def apply(self, resp, regime, bal, pnl_now):
        try:
            text = resp.strip()
            if "```json" in text: text = text.split("```json")[1].split("```")[0]
            elif "```" in text: text = text.split("```")[1].split("```")[0]
            d = json.loads(text)
        except:
            log(f"  ⚠️ 进化解析失败"); return {}

        changes = {}
        for k,v in d.get("param_changes",{}).items():
            if k in self.params:
                old = self.params[k]
                if k == "stop_loss_pct": v = max(0.02,min(0.06,float(v)))
                elif k == "take_profit_pct": v = max(0.03,min(0.10,float(v)))
                elif k == "min_score": v = max(3,min(9,float(v)))
                elif k == "pos_size_usdt": v = max(5,min(20,float(v)))
                elif k == "long_bias": v = max(0,min(1,float(v)))
                if v != old:
                    changes[k] = {"from":old,"to":v}
                    self.params[k] = v

        pa = d.get("preferred_add",[])
        if pa: self.params["preferred"] = list(set(self.params["preferred"]+pa))[:5]
        ba = d.get("blacklist_add",[])
        if ba: self.params["blacklist"] = list(set(self.params["blacklist"]+ba))
        rules = d.get("new_rules",[])
        if rules: self.params["rules"] = rules[:5]

        rec = {"ts":datetime.now().strftime("%H:%M"),"regime":regime,
               "changes":changes,"pnl_before":pnl_now,"effective":False}
        self.history.append(rec)
        self.save()
        log(f"🧬 进化#{self.count} | {regime} | 变更: {changes}")
        if d.get("reasoning"): log(f"  📊 理由: {d['reasoning'][:120]}")
        if d.get("new_rules"): log(f"  📜 规则: {d['new_rules']}")
        return changes

    def update_effectiveness(self, pnl_now):
        for rec in self.history:
            if not rec["effective"] and rec.get("pnl_after",0)==0:
                if time.time()-self._ts_of(rec["ts"])>600:
                    rec["pnl_after"] = pnl_now
                    rec["effective"] = pnl_now > rec["pnl_before"]

    def _ts_of(self, ts_str):
        try:
            h,m = ts_str.split(":")
            now = datetime.now()
            return datetime(now.year,now.month,now.day,int(h),int(m)).timestamp()
        except: return 0

    def save(self):
        try:
            data = {"params":self.params,"history":self.history[-20:]}
            with open(EVO_FILE,"w") as f: json.dump(data,f,indent=2)
            with open(EVO_LOG,"a") as f:
                f.write(f"\n## 进化#{self.count} {datetime.now().strftime('%H:%M')}\n")
                f.write(f"市场:{self.detect_regime({})} | 变更:{self.history[-1]['changes'] if self.history else '无'}\n---\n")
        except: pass

    def load(self):
        if not os.path.exists(EVO_FILE): return
        try:
            with open(EVO_FILE) as f: data = json.load(f)
            self.params.update(data.get("params",{}))
            self.history = data.get("history",[])
            log(f"📂 加载进化状态: {self.params}")
        except: pass

# ══════════════════════════════════════════════════════════
# AI调用
# ══════════════════════════════════════════════════════════
class AI:
    def __init__(self):
        self.ds_key = ""
        self.mc_key = ""
        self.aipro_key = ""
        self._load_keys()

    def _load_keys(self):
        creds = decrypt()
        self.ds_key = creds.get("DEEPSEEK_API_KEY", "")
        env_path = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if 'DEEPSEEK_API_KEY' in line and not self.ds_key:
                        s = line.find('"'); e = line.rfind('"')
                        if s>=0 and e>s: self.ds_key = line[s+1:e]
                        elif '=' in line: self.ds_key = line.split('=',1)[1].strip().strip('"\'')
                    if 'API_1314MC_KEY' in line:
                        s = line.find('"'); e = line.rfind('"')
                        if s>=0 and e>s: self.mc_key = line[s+1:e]
                        elif '=' in line: self.mc_key = line.split('=',1)[1].strip().strip('"\'')
                    if 'AI_PRO_API_KEY' in line:
                        s = line.find('"'); e = line.rfind('"')
                        if s>=0 and e>s: self.aipro_key = line[s+1:e]
                        elif '=' in line: self.aipro_key = line.split('=',1)[1].strip().strip('"\'')

    def _call(self, url, headers, payload, timeout=30):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 200:
                content = r.json()['choices'][0]['message'].get('content')
                return content if content else None
        except: return None
        return None

    def _call_aipro(self, model, messages, timeout=45, max_tokens=400, temp=0.3):
        """Call vip.aipro.love endpoint with aipro key."""
        if not self.aipro_key: return None
        return self._call(
            "https://vip.aipro.love/v1/chat/completions",
            {"Authorization": f"Bearer {self.aipro_key}", "Content-Type": "application/json"},
            {"model": model, "messages": messages, "temperature": temp, "max_tokens": max_tokens},
            timeout=timeout
        )

    def _call_mc1314(self, model, messages, timeout=45, max_tokens=400, temp=0.3):
        """Call api.1314mc.net endpoint with mc1314 key."""
        if not self.mc_key: return None
        return self._call(
            "https://api.1314mc.net/v1/chat/completions",
            {"Authorization": f"Bearer {self.mc_key}", "Content-Type": "application/json"},
            {"model": model, "messages": messages, "temperature": temp, "max_tokens": max_tokens},
            timeout=timeout
        )

    def ds(self, prompt, temp=0.3, max_tok=500):
        content = self._call(
            "https://api.deepseek.com/v1/chat/completions",
            {"Authorization":f"Bearer {self.ds_key}","Content-Type":"application/json"},
            {"model":"deepseek-chat","messages":[{"role":"system","content":"你是暗黑星火主权AI交易员。基于数据做决策，输出JSON。"},{"role":"user","content":prompt}],"temperature":temp,"max_tokens":max_tok}
        )
        return content

    def claude(self, prompt):
        """Try mc1314 claude-sonnet-4-6 first, fallback to aipro claude-opus-4-7."""
        msgs = [{"role": "user", "content": prompt}]
        # Primary: mc1314 claude-sonnet-4-6 (更稳定,内容不空)
        content = self._call_mc1314("claude-sonnet-4-6", msgs, max_tokens=400)
        # Fallback: aipro claude-opus-4-7 (偶尔空content)
        if content is None:
            content = self._call_aipro("claude-opus-4-7", msgs, max_tokens=400)
        return content

    def gpt(self, prompt):
        """Try mc1314 gpt-5.4 first, fallback to aipro gpt-5.5."""
        msgs = [{"role": "user", "content": prompt}]
        # Primary: mc1314 gpt-5.4 (confirmed working)
        content = self._call_mc1314("gpt-5.4", msgs, max_tokens=300)
        # Fallback: aipro gpt-5.5 (may return empty content on some queries)
        if content is None:
            content = self._call_aipro("gpt-5.5", msgs, max_tokens=300)
        return content

    def gemini(self, prompt):
        """Try aipro gemini-3.1-pro-low, fallback mc1314 claude-opus-4-6."""
        msgs = [{"role": "user", "content": prompt}]
        # Primary: aipro gemini-3.1-pro-low (confirmed working)
        content = self._call_aipro("gemini-3.1-pro-low", msgs, max_tokens=300)
        # mc1314 fallback: use mc1314 with claude as substitute if gemini unavailable
        if content is None:
            content = self._call_mc1314("claude-opus-4-6", msgs, max_tokens=300)
        return content

# ══════════════════════════════════════════════════════════
# LAYER 1: 3阶段AI流水线
# ══════════════════════════════════════════════════════════

# ─── 池4: 持仓管理(8s快速检查) ───
def pool4_manage(ex, positions, bl, ai, trade_mem, evo_params, cycle):
    """Stage 1 — 每8s运行: 止损检查+AI快速决策哪些该平"""
    closed_pnl = 0.0
    sl_pct = evo_params.get("stop_loss_pct", 0.04) * 2  # 硬止损是进化止损的2倍
    # 硬止损(纯软件)
    for sym, pos in list(positions.items()):
        margin = pos.get("margin",1)
        total_pnl = pos.get("total_pnl",0)
        pnl_ratio = total_pnl/margin if margin>0 else 0
        # 只在亏损时止损，盈利不干预
        if pnl_ratio <= -HARD_STOP_LOSS_PCT:
            log(f"  🔴 硬止损: {sym.split('/')[0]} PnL/margin={pnl_ratio*100:.1f}% (亏损)")
            r = close_position(ex, positions.pop(sym))
            closed_pnl += r
            bl[sym] = cycle + 1
            trade_mem.add(sym.split('/')[0], pos.get("side","?"), pos["entry"],
                          pos.get("current_price",pos["entry"]), pos["margin"], r, "stop_loss")
            # 自审计: 止损触发但盈利 > $0.10 → 逻辑BUG
            if r > 0.10:
                audit_anomaly("false_stop_loss",
                    f"{sym.split('/')[0]} stop_loss但PnL=+${r:.2f} margin={margin:.1f}", r)

    # 浮盈止盈(纯软件): PnL/margin ≥ take_profit_pct → 锁利润
    tp_pct = evo_params.get("take_profit_pct", 0.05)
    for sym, pos in list(positions.items()):
        margin = pos.get("margin",1)
        total_pnl = pos.get("total_pnl",0)
        pnl_ratio = total_pnl/margin if margin>0 else 0
        if pnl_ratio >= tp_pct and total_pnl > 0:
            log(f"  🟢 浮盈止盈: {sym.split('/')[0]} PnL/margin={pnl_ratio*100:.1f}%")
            r = close_position(ex, positions.pop(sym))
            closed_pnl += r
            bl[sym] = cycle + 1
            trade_mem.add(sym.split('/')[0], pos.get("side","?"), pos["entry"],
                          pos.get("current_price",pos["entry"]), pos["margin"], r, "take_profit")

    # --- AI快速平仓已删除(V7) ---
    # 平仓决策由analyst_decide每60秒统一评估,不再由独立AI线程决定
    # 保留: 机械止损(+pool4) + 机械止盈 + 24h超时平仓
    return closed_pnl

# ─── 池2已删除 — V4研究员直通委员会,不再需要中间DS筛选 ───

# ─── 四模型决策委员会 — 已删除(V7.1: 由analyst_decide替代) ───

# ══════════════════════════════════════════════════════════
# 🏢 市场分析部 — 给交易委员会提供完整市场快照
# ══════════════════════════════════════════════════════════
def market_analysis_dept(ex, rates):
    """市场分析部 — 收集BTC大盘走势+情绪+关键位
    返回完整市场快照供分析师做深度决策
    """
    snapshot = {"btc_trend": "unknown", "btc_price": 0, "btc_24h_chg": 0,
                "sentiment": "unknown", "funding_bias": "neutral",
                "btc_support": 0, "btc_resistance": 0, "regime": "ranging"}
    try:
        # ── BTC 日线趋势(4h K线×90根≈15天) ──
        o_4h = ex.fetch_ohlcv("BTC/USDT", "4h", limit=90)
        if len(o_4h) >= 50:
            closes = np.array([c[4] for c in o_4h], dtype=float)
            snapshot["btc_price"] = float(closes[-1])
            snapshot["btc_24h_chg"] = float((closes[-1] - closes[-6]) / closes[-6] * 100)
            # EMA5/20 多头排列?
            kf, ks = 2/6, 2/21
            ema5 = closes[0]; ema20 = closes[0]
            for i in range(1, len(closes)):
                ema5 = closes[i]*kf + ema5*(1-kf)
                ema20 = closes[i]*ks + ema20*(1-ks)
            # 关键支撑阻力: 近90根K线的最高/最低
            highs = np.array([c[2] for c in o_4h], dtype=float)
            lows = np.array([c[3] for c in o_4h], dtype=float)
            snapshot["btc_resistance"] = float(highs[-30:].max())  # 近期高点
            snapshot["btc_support"] = float(lows[-30:].min())       # 近期低点
            # 趋势判断
            if ema5 > ema20 * 1.01:
                snapshot["btc_trend"] = "bullish"
                snapshot["regime"] = "bullish"
            elif ema5 < ema20 * 0.99:
                snapshot["btc_trend"] = "bearish"
                snapshot["regime"] = "bearish"
            else:
                snapshot["btc_trend"] = "neutral"
                snapshot["regime"] = "ranging"
            # 判断位置: 高于阻力=强势突破, 低于支撑=弱势
            pos_in_range = (closes[-1] - snapshot["btc_support"]) / max(1, snapshot["btc_resistance"] - snapshot["btc_support"])
            snapshot["btc_position"] = f"{pos_in_range*100:.0f}%区间"
    except Exception as e:
        log(f"  📊 市场分析部: BTC数据获取失败 {e}")

    try:
        # ── 市场情绪: 费率整体偏向 ──
        pos_count = sum(1 for r in rates if r["rate"] > 0.0001)
        neg_count = sum(1 for r in rates if r["rate"] < -0.0001)
        if pos_count > neg_count * 2:
            snapshot["funding_bias"] = "偏正(多头拥挤,谨慎追多)"
            snapshot["sentiment"] = "greedy"
        elif neg_count > pos_count * 2:
            snapshot["funding_bias"] = "偏负(空头拥挤,可能反弹)"
            snapshot["sentiment"] = "fearful"
        else:
            snapshot["funding_bias"] = "中性"
            snapshot["sentiment"] = "neutral"
        snapshot["rate_count"] = f"正{pos_count}/负{neg_count}"
    except: pass

    return snapshot


# ══════════════════════════════════════════════════════════
# 📏 15m/1h 关键位识别 — 提供精确入场/出场点位
# ══════════════════════════════════════════════════════════
def get_key_levels(ex, symbol="ETH/USDT"):
    """多时间框支撑阻力识别 — 给震荡吃单提供精确点位
    输出: support_1(近支撑) / support_2(强支撑) / resistance_1 / resistance_2 / mid / range_pct
    """
    try:
        o_15m = ex.fetch_ohlcv(symbol, "15m", limit=96)  # 24h
        o_1h = ex.fetch_ohlcv(symbol, "1h", limit=48)    # 48h
        if len(o_15m) < 20:
            return {}
        closes_15 = [c[4] for c in o_15m]
        price = closes_15[-1]

        # 近3h(12根×15m)关键位
        recent_high = max(c[2] for c in o_15m[-12:])
        recent_low = min(c[3] for c in o_15m[-12:])
        # 24h摆动位
        swing_high = max(c[2] for c in o_15m[-48:])
        swing_low = min(c[3] for c in o_15m[-48:])
        # 1h关键位(更大级别确认)
        if len(o_1h) >= 12:
            h1_high = max(c[2] for c in o_1h[-12:])
            h1_low = min(c[3] for c in o_1h[-12:])
            swing_high = max(swing_high, h1_high)
            swing_low = min(swing_low, h1_low)

        mid = (recent_high + recent_low) / 2
        range_pct = (recent_high - recent_low) / mid * 100

        # 当前价格在范围内的位置 (0~100%)
        pos_pct = (price - recent_low) / max(1, recent_high - recent_low) * 100

        return {
            "price": price,
            "support_1": recent_low,
            "support_2": swing_low * 0.995,
            "resistance_1": recent_high,
            "resistance_2": swing_high * 1.005,
            "mid": mid,
            "range_pct": range_pct,
            "position_pct": pos_pct,
            "atr_15m": (sum(c[2]-c[3] for c in o_15m[-24:]) / 24) / price * 100  # ATR%
        }
    except Exception as e:
        log(f"  ⚠️ get_key_levels: {e}")
        return {}


# ══════════════════════════════════════════════════════════
# 🎯 震荡市吃单策略 — 已删除(V7)
# 机械吃单无分析，由analyst_decide统一决策
# ══════════════════════════════════════════════════════════


# ─── 精选扫描: ETH/BTC 快速指标(替代全市场pool1_filter) ───
def build_eth_btc_candidates(ex, rates, positions, bl):
    """精选扫描 — 仅ETH/BTC, 趋势+费率快筛
    API: 每币1次fetch_ohlcv(1m,30), 无fetch_ticker
    """
    candidates = []
    for r in rates:
        sym = r["symbol"]
        if sym in positions or sym in bl: continue
        try:
            o = ex.fetch_ohlcv(sym, "1m", limit=30)
            if len(o) < 20: continue
            ind = calc_indicators(o)
            if ind['atr_pct'] < 0.001: continue
            if ind['atr_pct'] > 0.08: continue
            side = "sell" if r["rate"] > 0 else "buy"
            closes = [c[4] for c in o[-20:]]
            sma_s = sum(closes[-5:])/5; sma_l = sum(closes)/len(closes)
            trend = "up" if sma_s > sma_l else "down"
            pc5 = (closes[-1]-closes[-5])/closes[-5]*100 if len(closes)>=5 else 0
            vols = [c[5] for c in o[-10:]]
            vr = vols[-1]/(sum(vols[:-1])/max(len(vols)-1,1)) if len(vols)>1 else 1.0
            score = abs(r["rate"]) * max(vr, 0.5)
            candidates.append({"symbol":sym, "rate":r["rate"], "mark":r.get("mark", ind['price']),
                "atr_pct":ind['atr_pct'], "trend":trend, "suggested_side":side,
                "price_chg_5m":pc5, "vol_ratio":vr, "score":score,
                "vol_24h":0})  # ETH/BTC不需要24h量过滤
        except: pass
    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

    # ── 强制评估注入(用户要求特定币种时用) ──
    global FORCE_SYMBOLS
    if FORCE_SYMBOLS:
        for fsym in FORCE_SYMBOLS:
            fsym_short = fsym.split('/')[0]
            if not any(fsym_short in c['symbol'] for c in candidates):
                for r in rates:
                    if fsym_short in r['symbol']:
                        try:
                            o = ex.fetch_ohlcv(r['symbol'], "1m", limit=30)
                            if len(o) >= 20:
                                ind = calc_indicators(o)
                                closes = [c[4] for c in o[-20:]]
                                sma_s = sum(closes[-5:])/5; sma_l = sum(closes)/len(closes)
                                trend = "up" if sma_s > sma_l else "down"
                                # 强制评估时: 方向由趋势决定,不是费率
                                side = "buy" if trend == "up" else "sell"
                                pc5 = (closes[-1]-closes[-5])/closes[-5]*100 if len(closes)>=5 else 0
                                vols = [c[5] for c in o[-10:]]
                                vr = vols[-1]/(sum(vols[:-1])/max(len(vols)-1,1)) if len(vols)>1 else 1.0
                                candidates.insert(0, {"symbol":r['symbol'], "rate":r["rate"], "mark":ind['price'],
                                    "atr_pct":ind['atr_pct'], "trend":trend, "suggested_side":side,
                                    "price_chg_5m":pc5, "vol_ratio":vr, "score":999, "forced":True})
                                log(f"  🔴 强制评估: {fsym_short} 已注入候选(用户指定)")
                        except: pass
    return candidates


# ─── 交易分析师: 单模型深度决策(V7: 同时管开仓和平仓) ───
def analyst_decide(ex, candidates, positions, bal, ai, trade_mem, evo_params, bl, cycle, market_snap):
    """交易分析师 V7 — 同时评估现有持仓和新开仓
    核心变革:
      - 不再只看开仓: 每60秒评估是否该平仓
      - 不再机械开: 每笔附带分析理由
      - 最低持仓15分钟保护: 防止秒开秒平
    """
    # 不需要持仓时才叫我们: 即使满仓也要评估是否该平
    if not candidates and not positions:
        return 0.0, None

    global FORCE_SYMBOLS
    force_str = ""
    if FORCE_SYMBOLS:
        fs_list = "、".join(s.split('/')[0] for s in FORCE_SYMBOLS)
        force_str = f"\n⚠️ 【用户强制评估】用户指定评估 {fs_list} 的开仓机会，优先分析这些币。\n"
    FORCE_SYMBOLS = []

    max_open = HARD_MAX_POSITIONS - len(positions)
    clean_candidates = [c for c in (candidates or []) if isinstance(c, dict) and 'symbol' in c]

    # ── 构建分析师prompt: 市场快照 + 候选币 + 现有持仓评估 ──
    snap = market_snap or {}
    market_str = f"""【🏢 市场分析部 — 当前市场快照】
BTC: ${snap.get('btc_price',0):.0f} (24h {snap.get('btc_24h_chg',0):+.1f}%)
趋势: {snap.get('btc_trend','unknown')} | EMA5/20排列
位置: {snap.get('btc_position','?')} | 支撑${snap.get('btc_support',0):.0f} | 阻力${snap.get('btc_resistance',0):.0f}
情绪: {snap.get('sentiment','unknown')} | 费率偏向: {snap.get('funding_bias','?')}"""

    # 候选币
    cand_str = "\n".join([
        f"  {i+1}. {c['symbol'].split('/')[0]:<12} {c['suggested_side']:<5} "
        f"价${c.get('mark',0):.4f} 费率{c['rate']*100:+.3f}% "
        f"24h量${c.get('vol_24h',0)/1e6:.0f}M ATR{c['atr_pct']*100:.1f}%"
        for i, c in enumerate(clean_candidates[:8])
    ]) if clean_candidates else "  无候选币(当前市场条件无合适标的)"

    # 当前持仓(含持仓时间)
    pos_str_lines = []
    for s, p in positions.items():
        held_min = (time.time() - p.get("open_time", time.time())) / 60
        protect = "🔒<15min禁止平" if held_min < 15 else ""
        pos_str_lines.append(
            f"  {s.split('/')[0]:<12} {p.get('side','?'):<5} "
            f"入场${p.get('entry',0):.4f} PnL${p.get('total_pnl',0):+.2f} "
            f"持仓{held_min:.0f}分钟{protect}"
        )
    pos_str = "\n".join(pos_str_lines) if positions else "  空仓"

    # 最低持仓时间提示(注入prompt)
    min_hold_hint = ""
    for s, p in positions.items():
        held = time.time() - p.get("open_time", time.time())
        if held < 15 * 60:
            min_hold_hint = f"\n⚠️ 【保护】{s.split('/')[0]} 持仓不足15分钟({held/60:.0f}分钟)，禁止平仓"
            break

    mem_text = trade_mem.summary()
    dyn_margin = max(12.0, min(bal * 0.4, 50.0))

    prompt = f"""【暗黑星火交易分析师 — 开仓+持仓管理】V7.1

{market_str}

【🔬 研究员 — 候选币详细数据】
{cand_str}

【📊 当前持仓】
{pos_str}{min_hold_hint}

【💰 账户】余额${bal:.2f} | {len(positions)}/{HARD_MAX_POSITIONS}仓 | 可开{max_open}仓 | 杠杆{MAX_LEVERAGE}x

{mem_text}

=== 决策任务 ===
{force_str}你是暗黑星火交易分析师。执行两级评估：

**第一步：评估现有持仓(必须做)**
- **必须**对每一个现有持仓输出position_actions（空仓时不用）
- 每个持仓当前浮盈/浮亏情况
- 市场环境是否支持继续持有
- 浮盈到目标位(>5%)且市场可能反转→建议close
- 市场已转向不利方向→建议close止损
- **持仓不足15分钟的禁止close**
- 持有合理的→hold+写明理由

**第二步：评估新开仓(仅当有仓位空间)**
- ETH优先选(流动性最好)
- 候选币中有明确技术面信号才开
- 避开合约黑名单: DOGE/XRP/SOL/ATOM(过去2天亏钱已验证)

输出JSON:
{{
  "market_judgment": "bullish/bearish/ranging",
  "analysis": "2-3句当前市场分析",
  "position_actions": [
    {{"symbol":"COIN", "action":"hold/close/open",
      "reason":"具体理由",
      "side":"buy/sell",
      "margin":{dyn_margin:.0f}}}
  ]
}}
只输出JSON"""

    # ── 单模型深度分析(DS Pro, temp=0.4) ──
    resp = ai.ds(prompt, temp=0.4, max_tok=500)
    if not resp:
        log("  📉 分析师: AI无响应,跳过")
        return 0.0, "AI无响应"

    try:
        b1 = resp.find('{'); b2 = resp.rfind('}')
        d = json.loads(resp[b1:b2+1])
    except:
        log(f"  📉 分析师: 非JSON响应 {resp[:100]}")
        return 0.0, "非JSON响应"

    judgment = d.get("market_judgment", "?")
    analysis = d.get("analysis", "")
    actions = d.get("position_actions", [])

    # 安全兜底: 有持仓但AI没给出position_actions → 默认hold所有仓
    if positions and not actions:
        actions = [{"symbol": s.split('/')[0], "action": "hold", "reason": "AI未评估持仓,默认持有"}
                   for s in positions]

    # 如果既没持仓也无操作建议 → 观望
    if not actions:
        log(f"  📉 分析师: 无操作建议 | {analysis[:120]}")
        return 0.0, f"观望({judgment})"

    log(f"  📊 分析师: {judgment} | {analysis[:150]}")

    # ── 执行操作: 先平后开 ──
    pnl = 0.0

    # 先执行平仓建议
    for act in actions:
        sym = act.get("symbol", "")
        action = act.get("action", "hold")
        reason = act.get("reason", "")
        if not sym or action != "close":
            continue

        # 查找持仓
        fs = None
        for k in positions:
            if k.startswith(sym):
                fs = k; break
        if not fs:
            continue

        # 最低持仓时间检查
        held = time.time() - positions[fs].get("open_time", time.time())
        if held < 15 * 60:
            log(f"  ⏳ {sym} 持仓仅{held/60:.0f}分钟(<15min),禁止平仓 | {reason}")
            continue

        # 执行平仓
        pos = positions.pop(fs)
        r = close_position(ex, pos)
        pnl += r
        bl[fs] = cycle + 1
        trade_mem.add(fs.split('/')[0], pos.get("side","?"),
                      pos["entry"], pos.get("current_price", pos["entry"]),
                      pos["margin"], r, "analyst_close")
        log(f"  🎯 分析师建议平仓 {sym}: {reason} PnL=${r:.2f}")

    # 再执行开仓建议
    for act in actions:
        sym = act.get("symbol", "")
        action = act.get("action", "open")
        if not sym or action != "open":
            continue
        if len(positions) >= HARD_MAX_POSITIONS:
            log(f"  仓位已满({len(positions)}/{HARD_MAX_POSITIONS}),跳过{sym}开仓")
            break
        side = act.get("side", "buy")
        margin = min(float(act.get("margin", dyn_margin)), dyn_margin)
        reason = act.get("reason", "")

        # 合约黑名单检查
        base = sym.split('/')[0] if '/' in sym else sym
        if base in {'DOGE','XRP','SOL','ATOM'}:
            log(f"  ⛔ {base} 在合约黑名单中,跳过开仓(DOGE/XRP/SOL/ATOM过去2天亏$30+)")
            continue

        try:
            fs = sym if "/" in sym else f"{sym}/USDT:USDT"
            if fs in positions or fs in bl:
                continue
            t = ex.fetch_ticker(fs)
            mark = float(t["last"])
            pos = open_position(ex, fs, side, margin, mark)
            if pos:
                positions[fs] = pos
                log(f"  📈 分析师开仓 {sym} {side} m=${margin:.1f} @${mark:.4f} | {reason}")
                # 开仓时把理由存入pos
                positions[fs]["open_reason"] = reason
                pnl -= pos.get("fees_paid", 0)
        except Exception as e:
            log(f"  ⚠️ 开仓失败 {sym}: {e}")

    return pnl, analysis[:100] if analysis else None

# ══════════════════════════════════════════════════════════
# 指标计算 + 池1规则筛选
# ══════════════════════════════════════════════════════════
def calc_indicators(ohlcv):
    closes = np.array([c[4] for c in ohlcv], dtype=float)
    highs = np.array([c[2] for c in ohlcv], dtype=float)
    lows = np.array([c[3] for c in ohlcv], dtype=float)
    tr = np.maximum(highs[1:]-lows[1:],
        np.maximum(np.abs(highs[1:]-closes[:-1]), np.abs(lows[1:]-closes[:-1])))
    atr = np.mean(tr[-14:]) if len(tr) >= 14 else 0
    atr_pct = atr/closes[-1] if closes[-1] > 0 else 0
    kf, ks = 2/6, 2/21
    ema5 = [closes[0]]; ema20 = [closes[0]]
    for i in range(1, len(closes)):
        ema5.append(closes[i]*kf + ema5[-1]*(1-kf))
        ema20.append(closes[i]*ks + ema20[-1]*(1-ks))
    return {'atr_pct': atr_pct, 'atr_abs': atr,
        'trend': 'up' if ema5[-1] > ema20[-1] else 'down',
        'price': closes[-1], 'ema5': ema5[-1], 'ema20': ema20[-1]}

# pool1_filter — 已删除(V7.1: 全市场扫描已废弃)

# ══════════════════════════════════════════════════════════
# 执行函数
# ══════════════════════════════════════════════════════════
def set_leverage_rest(sym_raw):
    creds = decrypt(); key = creds.get("BINANCE_API_KEY",""); secret = creds.get("BINANCE_API_SECRET","")
    if not key: return False
    for _ in range(3):
        try:
            ts = int(time.time()*1000)
            body = f"symbol={sym_raw}&leverage={MAX_LEVERAGE}&timestamp={ts}"
            sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            r = requests.post(f"https://fapi.binance.com/fapi/v1/leverage?{body}&signature={sig}",
                headers={"X-MBX-APIKEY":key}, timeout=5)
            if r.status_code == 200 and r.json().get("leverage") == MAX_LEVERAGE: return True
        except: time.sleep(0.5)
    return False

def open_position(ex, symbol, side, margin, mark):
    try:
        # 先检查是否已有同币种持仓
        sym_raw = symbol.split('/')[0] + 'USDT'
        try:
            creds = decrypt()
            ts = int(time.time()*1000)
            qs = f'timestamp={ts}&recvWindow=10000'
            sig = hmac.new(creds['BINANCE_API_SECRET'].encode(), qs.encode(), hashlib.sha256).hexdigest()
            r = requests.get(f'https://fapi.binance.com/fapi/v2/positionRisk?{qs}&signature={sig}',
                headers={'X-MBX-APIKEY': creds['BINANCE_API_KEY']})
            for p in (r.json() if r.status_code == 200 else []):
                if p.get('symbol') == sym_raw and float(p.get('positionAmt',0) or 0) != 0:
                    log(f"  ⏭️ {sym_raw}已有持仓({p['positionAmt']}), 跳过开仓")
                    return None
        except: pass
        
        set_leverage_rest(sym_raw)
        fee_rate = 0.0004
        qty = margin * MAX_LEVERAGE / mark
        try:
            filters = ex.market(symbol)["info"]["filters"]
            lot_size = next((f for f in filters if f.get("filterType")=="LOT_SIZE"), None)
            if lot_size:
                step = float(lot_size.get("stepSize","0.001"))
                qty = math.floor(qty/step)*step if step > 0 else qty
        except: pass
        if qty < 0.001: return None
        order = ex.create_market_order(symbol, side, qty, params={"leverage": MAX_LEVERAGE})
        fq = float(order.get("executedQty", qty))
        fc = float(order.get("cummulativeQuoteQty", 0))
        avg = fc/fq if (fq > 0 and fc > 0) else mark
        fee = fq * avg * fee_rate
        log(f"  📈 开 {symbol.split('/')[0]} {side} q={fq:.4f} @${avg:.4f} m=${fq*avg/MAX_LEVERAGE:.2f}")
        pos_obj = {"symbol":symbol,"side":side,"margin":fq*avg/MAX_LEVERAGE,
            "qty":fq,"entry":avg,"current_price":avg,"atr_pct":0,
            "funding_rate":0,"open_time":time.time(),
            "funding_collected":0.0,"fees_paid":fee,
            "slippage":fq*avg*0.0001,"price_pnl":0.0,
            "total_pnl":-fee,"order_id":str(order.get("id",""))}
        log(f"    软件止损: HARD_STOP_LOSS_PCT={HARD_STOP_LOSS_PCT*100:.0f}%(60s扫描)")
        return pos_obj
    except Exception as e:
        log(f"  开仓失败 {symbol}: {e}")
        return None

def close_position(ex, pos):
    try:
        sym = pos["symbol"]
        side = "sell" if pos["side"]=="buy" else "buy"
        fee_rate = 0.0004
        order = ex.create_market_order(sym, side, abs(pos["qty"]),
            params={"reduceOnly": True, "leverage": MAX_LEVERAGE})
        fq = float(order.get("executedQty", 0))
        fr = float(order.get("cummulativeQuoteQty", 0))
        if fq <= 0:
            t = ex.fetch_ticker(sym); cp = float(t["last"])
            fq = abs(pos["qty"]); fr = fq * cp
        else: cp = fr/fq
        # 防御entry=0导致假PnL（cummulativeQuoteQty偶尔为0时）
        entry = pos["entry"] if pos.get("entry", 0) > 0 else cp
        pp = (cp-entry)*pos["qty"] if pos["side"]=="buy" else (entry-cp)*pos["qty"]
        fee = fq * cp * fee_rate
        total = pp + pos.get("funding_collected",0) - pos.get("fees_paid",0) - fee
        log(f"  💰 平 {sym.split('/')[0]} ${pos['entry']:.4f}→${cp:.4f} PnL=${total:.2f}")
        return total
    except Exception as e:
        log(f"  平仓失败 {pos['symbol']}: {e}")
        return 0

def audit_anomaly(anomaly_type, detail, pnl=0.0):
    """自审计: 记录异常关闭供进化引擎审计"""
    try:
        entry = {
            "ts": time.time(),
            "type": anomaly_type,
            "detail": detail,
            "pnl": pnl
        }
        anomalies = []
        if os.path.exists(ANOMALY_LOG):
            try:
                with open(ANOMALY_LOG) as f:
                    anomalies = json.load(f)
            except: pass
        anomalies.append(entry)
        # 只保留最近100条
        with open(ANOMALY_LOG, 'w') as f:
            json.dump(anomalies[-100:], f, indent=2)
        log(f"  ⚠️ 自审计异常: {anomaly_type} | {detail} | PnL=${pnl:+.2f}")
    except:
        pass

def self_heal(anomalies, ai):
    """🩺 AI自修复: 高频异常→AI分析根因→生成patch→语法验证→打补丁→返回True需重启"""
    HEAL_COOLDOWN = 3600   # 1小时冷却
    HEAL_MIN_COUNT = 3     # 同类型≥3次触发
    HEAL_MIN_CONF = 0.7    # AI信心≥0.7才应用

    try:
        now = time.time()
        # 冷却检查
        heal_history = []
        if os.path.exists(HEAL_LOG):
            with open(HEAL_LOG) as f:
                heal_history = json.load(f)
        if any(now - h.get("ts",0) < HEAL_COOLDOWN for h in heal_history):
            return False

        # 按类型分组
        from collections import Counter
        type_counts = Counter()
        for a in anomalies:
            if now - a.get("ts",0) < 1800:
                type_counts[a["type"]] += 1
        if not type_counts:
            return False
        top_type, top_count = type_counts.most_common(1)[0]
        if top_count < HEAL_MIN_COUNT:
            return False

        # 读相关代码片段(前后50行)
        func_map = {"false_stop_loss": "pool4_manage", "premature_ai_close": "pool4_manage"}
        func_name = func_map.get(top_type, "pool4_manage")
        code_snippet = ""
        try:
            import inspect
            src = inspect.getsource(eval(func_name))
            code_snippet = src[:3000]
        except:
            code_snippet = f"# 函数{func_name}源码读取失败"

        recent = [a for a in anomalies if a["type"]==top_type and now-a.get("ts",0)<1800]

        prompt = f"""【AI自修复 · 代码外科手术】
异常: {top_type} × {top_count}次/30min

最近详情: {json.dumps(recent[-3:], indent=2, ensure_ascii=False)}

当前代码 ({func_name}):
```
{code_snippet}
```

你是Python代码修复专家。分析根因，输出精确patch。
⚠️ old_lines必须从上面代码逐字复制(含缩进/空格)，new_lines必须语法正确。

输出JSON:
{{"root_cause":"根因分析","confidence":0.0-1.0,"old_lines":"精确复制","new_lines":"修复后代码"}}
仅输出JSON。"""

        # AI修复(优先Claude，回退DeepSeek)
        # Claude/GPT/Gemini 只接受prompt参数
        resp = ai.claude(prompt) or ai.ds(prompt, temp=0.1, max_tok=1500)
        if not resp:
            log("  🩺 自修复: AI无响应")
            return False
        try:
            b1 = resp.find('{'); b2 = resp.rfind('}')
            fix = json.loads(resp[b1:b2+1])
        except:
            log("  🩺 自修复: AI输出非JSON")
            return False

        if fix.get("confidence",0) < HEAL_MIN_CONF:
            log(f"  🩺 自修复: AI信心不足({fix.get('confidence'):.0%}),跳过")
            return False

        old = fix.get("old_lines","")
        new = fix.get("new_lines","")
        if not old or len(old) < 10:
            log(f"  🩺 自修复: old_lines过短({len(old)}c),拒绝")
            return False
        
        # 危险代码黑名单
        DANGEROUS = ["os.system", "subprocess", "__import__", "eval(", "exec(",
                     "rm -rf", "shutil.rmtree", "importlib", "compile("]
        if any(d in new for d in DANGEROUS):
            log(f"  🩺 自修复: AI生成了危险代码,已拒绝")
            return False

        # 读源文件
        src_path = __file__
        with open(src_path) as f:
            source = f.read()
        if old not in source:
            log(f"  🩺 自修复: old_lines未匹配源码")
            return False

        # 备份
        backup = src_path + f".heal_bak.{int(now)}"
        import shutil
        shutil.copy2(src_path, backup)
        
        # 清理旧备份(保留最近5个)
        import glob
        baks = sorted(glob.glob(src_path + ".heal_bak.*"), key=os.path.getmtime)
        for old_bak in baks[:-5]:
            try: os.remove(old_bak)
            except: pass

        # 打补丁
        patched = source.replace(old, new, 1)

        # 语法检查
        import py_compile, tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tf:
            tf.write(patched)
            tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            log(f"  🩺 自修复: 语法错误 {e}, 已回滚")
            os.remove(tmp)
            return False
        os.remove(tmp)

        # 写入
        with open(src_path, 'w') as f:
            f.write(patched)

        # 记录
        heal_history.append({
            "ts": now, "type": top_type, "count": top_count,
            "root_cause": fix.get("root_cause","?"),
            "backup": os.path.basename(backup)
        })
        with open(HEAL_LOG, 'w') as f:
            json.dump(heal_history[-20:], f, indent=2)

        log(f"  🩺✅ 自修复完成: {top_type} | 根因: {fix.get('root_cause','?')} | 备份: {os.path.basename(backup)}")
        return True

    except Exception as e:
        log(f"  🩺 自修复异常: {e}")
        return False

def sync_live_pnl(positions, ex):
    try:
        for sym, pos in list(positions.items()):
            try:
                t = ex.fetch_ticker(sym)
                mark = float(t['last'])
                pos['current_price'] = mark
                entry = pos['entry']; qty = pos['qty']; side = pos['side']
                pnl = (mark-entry)*qty if side=='buy' else (entry-mark)*qty
                pos['price_pnl'] = pnl
                fc = pos.get('funding_collected',0); fee = pos.get('fees_paid',0)
                pos['total_pnl'] = pnl + fc - fee
            except: pass
        return True
    except: return False

# ══════════════════════════════════════════════════════════
# 市场情报读取
# ══════════════════════════════════════════════════════════
# read_market_regime — 已删除(V7.1)

# ══════════════════════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════════════════════
def main():
    creds = decrypt()
    api_key = creds.get("BINANCE_API_KEY","")
    api_secret = creds.get("BINANCE_API_SECRET","")
    if not api_key or not api_secret:
        log("无API Key"); sys.exit(1)

    ex = ccxt.binance({"apiKey":api_key,"secret":api_secret,
        "enableRateLimit":True,"timeout":30000,"options":{"defaultType":"future"}})
    ex.load_markets()

    # 初始化各系统
    trade_mem = TradeMemory()
    trade_mem.load()
    evo = SmartEvolution(trade_mem)
    ai = AI()

    log("="*60)
    log("👑 主权AI交易系统 v7.1 — 分析师全权(开仓+平仓)")
    log(f"杠杆{MAX_LEVERAGE}x | 最多{HARD_MAX_POSITIONS}仓")
    log(f"架构: 🏢市场分析部(BTC多周期) → 📊分析师(评估开仓+平仓) → 🛡️机械止损(最后防线)")
    log(f"V7.0: 无机械吃单,无AI快平,分析师全权控制 | 记忆{len(trade_mem.records)}笔")
    log("="*60)

    # 同步持仓 — 修复: set_leverage_rest失败不中断整个同步
    positions = {}
    try:
        ts = int(time.time()*1000)
        qs = f'timestamp={ts}&recvWindow=10000'
        sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        resp = requests.get(f'https://fapi.binance.com/fapi/v2/positionRisk?{qs}&signature={sig}',
            headers={'X-MBX-APIKEY': api_key})
        for p in (resp.json() if resp.status_code == 200 else []):
            a = float(p.get("positionAmt",0) or 0)
            if abs(a) <= 0: continue
            sym_raw = p.get("symbol","")
            if not sym_raw.endswith('USDT'): continue
            sym = f'{sym_raw[:-4]}/USDT:USDT'
            ep = float(p.get("entryPrice",0) or 0)
            side = "buy" if a > 0 else "sell"
            nt = abs(a*ep)
            positions[sym] = {"symbol":sym,"side":side,"margin":nt/MAX_LEVERAGE,
                "qty":abs(a),"entry":ep,"current_price":ep,"atr_pct":0,
                "funding_rate":0,"open_time":time.time(),
                "funding_collected":0.0,"fees_paid":0.0,
                "slippage":0.0,"price_pnl":float(p.get("unrealizedProfit",0) or 0),
                "total_pnl":float(p.get("unrealizedProfit",0) or 0)}
            try:
                set_leverage_rest(sym_raw)
            except Exception as e:
                log(f"  杠杆设置跳过 {sym_raw}: {e}")
    except Exception as e:
        log(f"  同步失败: {e}, 空仓启动")
    log(f"  同步: {len(positions)}仓" if positions else "  空仓启动")
    
    # ── 启动审计: 清理黑名单中的继承仓位(V7.1修复) ──
    total_pnl = 0.0
    for sym in list(positions.keys()):
        base = sym.split('/')[0]
        if base in CONTRACT_BLACKLIST:
            log(f"  🧹 启动审计: {sym} 在黑名单({base}), 强制平仓")
            r = close_position(ex, positions.pop(sym))
            total_pnl += r
    
    bal = float(ex.fetch_balance()["total"].get("USDT",0))
    bl = {}; cycle = 0
    last_pool2 = 0; last_pool3 = 0; last_pool4 = 0; last_evo = time.time()
    pool4_interval = 8  # 8秒
    pool23_interval = 60  # 60秒
    evo_interval = 86400 * 365  # 内置进化已关闭，统一用 evolution_engine_v2.py
    last_anomaly_check = 0
    anomaly_check_interval = 600  # 每10分钟审计一次异常日志
    last_candidates = []  # 缓存研究员结果给分析师用
    last_market_snap = {}  # 缓存市场分析部快照
    committee_fail_streak = 0  # 分析师连续失败计数(仅日志)
    
    # ── 启动硬止损监控线程(独立于主循环,无视15min保护,V7.1修复) ──
    def emergency_stop_monitor():
        """独立线程: 每5秒检查硬止损, 不受任何保护期/analyst决策影响"""
        while True:
            time.sleep(5)
            try:
                for sym, pos in list(positions.items()):
                    margin = pos.get("margin", 1)
                    pnl = pos.get("total_pnl", 0)
                    if margin > 0 and pnl / margin <= -HARD_STOP_LOSS_PCT:
                        log(f"  🔴🆘 紧急硬止损(独立线程): {sym.split('/')[0]} PnL/margin={pnl/margin*100:.1f}%")
                        if sym in positions:
                            r = close_position(ex, positions.pop(sym))
            except Exception as e:
                log(f"  ⚠️ 紧急监控异常: {e}")
    threading.Thread(target=emergency_stop_monitor, daemon=True).start()
    log("  🛡️ 硬止损监控线程已启动(每5秒,独立于保护期)")
    
    while True:
        try:
            t0 = time.time()
            now = time.time()

            # ── 外部强制评估信号 ──
            ceo_cmd = {}
            force_signal_file = "/tmp/force_eval_symbols.json"
            if os.path.exists(force_signal_file):
                try:
                    with open(force_signal_file) as f:
                        sig = json.load(f)
                    os.remove(force_signal_file)
                    if isinstance(sig, list) and sig:
                        FORCE_SYMBOLS.extend(sig)
                        log(f"  🔴 收到外部强制评估信号: {sig}")
                except Exception as e:
                    log(f"  ⚠️ 强制评估信号文件读取失败: {e}")
                    try: os.remove(force_signal_file)
                    except: pass

            # 实时余额 — 重试3次防API瞬断
            bal = 0.0
            for _ in range(3):
                try:
                    bal = float(ex.fetch_balance()["total"].get("USDT",0))
                    if bal > 0: break
                except:
                    time.sleep(1)

            # 实时PnL同步
            sync_live_pnl(positions, ex)

            # ── Stage 1: 池4-持仓管理(每8秒) ──
            if now - last_pool4 >= pool4_interval:
                pnl = pool4_manage(ex, positions, bl, ai, trade_mem, evo.params, cycle)
                total_pnl += pnl
                if pnl != 0:
                    try: bal = float(ex.fetch_balance()["total"].get("USDT",0))
                    except: pass
                last_pool4 = now

            # ── Stage 2: 市场分析部 + 精选扫描(每60秒, 4币) ──
            if now - last_pool2 >= pool23_interval:
                try:
                    # 取4币费率(每次1次fetch_funding_rate API, 旧版扫全市场500+)
                    scan_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
                    rates = []
                    for sym_raw in scan_symbols:
                        try:
                            fr_data = ex.fetch_funding_rate(sym_raw)
                            rate = fr_data.get("fundingRate", 0) or 0
                            mark = fr_data.get("markPrice", 0) or 0
                            if mark <= 0:
                                t = ex.fetch_ticker(f"{sym_raw[:-4]}/USDT:USDT")
                                mark = float(t.get("last", 0))
                            rates.append({"symbol": f"{sym_raw[:-4]}/USDT:USDT",
                                         "rate": rate, "mark": mark})
                        except: pass

                    # 🏢 市场分析部: BTC趋势+情绪(1次API: fetch_ohlcv 4h)
                    market_snap = market_analysis_dept(ex, rates)
                    rate_str = " | ".join([
                        f"{['BTC','ETH','SOL','DOGE'][i]}费率{r['rate']*100:+.3f}%"
                        for i, r in enumerate(rates) if i < 4
                    ])
                    log(f"  🏢 市场分析部: BTC={market_snap.get('btc_trend','?')} "
                        f"${market_snap.get('btc_price',0):.0f} "
                        f"{market_snap.get('sentiment','?')} | {rate_str}")

                    # 📏 ETH 15m关键位(震荡市吃单用)
                    eth_levels = get_key_levels(ex)
                    if eth_levels:
                        log(f"  📏 ETH关键位: 支撑${eth_levels.get('support_1',0):.0f} "
                            f"阻力${eth_levels.get('resistance_1',0):.0f} "
                            f"位置{eth_levels.get('position_pct',50):.0f}%区间 "
                            f"幅度{eth_levels.get('range_pct',0):.1f}%")
                    else:
                        eth_levels = {}

                    # 🔬 精选扫描: 4币 趋势+量比(每币1次fetch_ohlcv 1m)
                    candidates = build_eth_btc_candidates(ex, rates, positions, bl)
                    log(f"  🔎 精选: {len(candidates)}/{len(rates)}候选 (ETH/BTC/SOL/DOGE)")
                    last_candidates = candidates
                    market_snap.update({"key_levels": eth_levels})
                    last_market_snap = market_snap
                    last_pool2 = now
                except Exception as e:
                    log(f"  ⚠️ 扫描异常: {e}")

            # ── Stage 3: 分析师统一决策(V7: 不分区震荡/趋势,统一评估开仓+平仓) ──
            if now - last_pool3 >= pool23_interval:
                pnl = 0.0

                # 分析师同时评估: 现有持仓该不该平 + 新仓该不该开
                # 即使满仓也要跑(分析师可能会建议平仓)
                if last_candidates or len(positions) > 0:
                    pnl, fail_reason = analyst_decide(ex, last_candidates, positions, bal,
                        ai, trade_mem, evo.params, bl, cycle, last_market_snap)
                    total_pnl += pnl
                    if fail_reason and fail_reason.startswith("观望"):
                        pass  # 观望不算失败
                    elif fail_reason:
                        pass  # 记录下就好
                else:
                    log("  📉 无候选币且无持仓,跳过分析师")

                if pnl != 0:
                    try: bal = float(ex.fetch_balance()["total"].get("USDT",0))
                    except: pass
                last_pool3 = now

            # ── 持仓超时检查 ──
            for sym, pos in list(positions.items()):
                if now - pos.get("open_time", now) > 24 * 3600:
                    log(f"  ⏰ {sym.split('/')[0]} 持仓超24h强制平仓")
                    r = close_position(ex, positions.pop(sym))
                    total_pnl += r; bl[sym] = cycle + 1
                    trade_mem.add(sym.split('/')[0], pos.get("side","?"),
                                  pos["entry"], pos.get("current_price",pos["entry"]),
                                  pos["margin"], r, "timeout")

            # ── 自审计: 异常日志检查+自修复(每10分钟) ──
            if now - last_anomaly_check >= anomaly_check_interval:
                try:
                    if os.path.exists(ANOMALY_LOG):
                        with open(ANOMALY_LOG) as f:
                            anomalies = json.load(f)
                        recent = [a for a in anomalies if now - a.get("ts",0) < anomaly_check_interval * 2]
                        if recent:
                            types = {}
                            for a in recent:
                                t = a.get("type","?")
                                types[t] = types.get(t,0) + 1
                            summary = ", ".join(f"{k}×{v}" for k,v in types.items())
                            log(f"  🔍 自审计: 近20分钟发现 {len(recent)} 条异常 [{summary}]")
                            for a in recent[-3:]:
                                log(f"     {a.get('type','?')}: {a.get('detail','?')}")
                            # 🩺 高频异常→AI自修复
                            if self_heal(anomalies, ai):
                                log("  🔄 自修复已应用,3秒后热重启...")
                                # 保存状态
                                try:
                                    save_state = {
                                        "positions": {k:{kk:vv for kk,vv in v.items() if kk!="symbol"} for k,v in positions.items()},
                                        "total_pnl": total_pnl, "balance": bal, "cycle": cycle,
                                    }
                                    with open(STATE_FILE,"w") as f: json.dump(save_state,f,indent=2,default=str)
                                except: pass
                                time.sleep(3)
                                os.execv(sys.executable, [sys.executable] + sys.argv)
                except: pass
                last_anomaly_check = now



            # ── Layer 3: 进化(每10分钟) ──
            if now - last_evo >= evo_interval:
                evo.count += 1
                # 收集市场数据
                market_data = {}
                for sym in list(positions.keys())[:5]:
                    try:
                        o = ex.fetch_ohlcv(sym, "5m", limit=20)
                        if len(o) >= 10:
                            ind = calc_indicators(o)
                            market_data[sym] = {"atr_pct": ind['atr_pct'], "trend": ind['trend']}
                    except: pass
                regime = evo.detect_regime(market_data)
                pattern = trade_mem.pattern_analysis()
                prompt = evo.build_prompt(regime, bal, len(positions), total_pnl, pattern)
                resp = ai.ds(prompt, temp=0.4, max_tok=800)
                if resp:
                    evo.apply(resp, regime, bal, total_pnl)
                evo.update_effectiveness(total_pnl)
                last_evo = now

            # ── 费率结算 ──
            for pos in positions.values():
                ntl = pos["qty"] * pos["entry"]
                sf = ntl * 0.0004 * 2
                fr = abs(pos.get("funding_rate",0))
                inc = ntl * fr - sf
                if inc > 0.01:
                    pos["funding_collected"] += inc
                    pos["total_pnl"] = pos["funding_collected"] + pos["price_pnl"] - pos["fees_paid"]

            # ── 状态保存 ──
            try:
                save_state = {
                    "positions": {k:{kk:vv for kk,vv in v.items() if kk!="symbol"} for k,v in positions.items()},
                    "total_pnl": total_pnl, "balance": bal, "cycle": cycle,
                }
                with open(STATE_FILE,"w") as f: json.dump(save_state,f,indent=2,default=str)
            except: pass

            # ── 汇总日志(每循环一次) ──
            pos_str = " ".join([f"{k.split('/')[0]}:${v.get('total_pnl',0):+.2f}({v.get('total_pnl',0)/max(1,v.get('margin',1))*100:.1f}%)"
                for k,v in positions.items()]) if positions else "空仓"
            log(f"💰 ${bal:.2f} | {len(positions)}仓 | PnL=${total_pnl:+.2f} | {pos_str}")

            cycle += 1
            sleep_time = max(1, 8 - (time.time() - t0))
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log("⏹ 停止"); break
        except Exception as e:
            log(f"🔴 异常: {e}")
            traceback.print_exc()
            time.sleep(5)

if __name__ == "__main__":
    main()
