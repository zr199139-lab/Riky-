# V8 Session Trading Analysis — 2026-05-10

## Session Overview

V8 bot ran for ~8 hours on 2026-05-10 across multiple sessions, with ~$45 starting balance. This document captures the per-coin PnL patterns, failure modes, and improvements needed.

## Phase 1: V7+ Old Version (08:37-10:09)

**Balance**: $88 → ~$45 (lost $43 to fees + bad trades)
**Mode**: 3 positions, 20x leverage, "loss reversal" mechanism active

### The Death Spiral — INX

```
08:40  open INX short @$0.0187
08:41  loss → reverse to buy
08:43  loss → reverse to sell  
08:44  loss → reverse to buy
...continues 16+ times...
10:09  still reversing INX
```

**Root Cause**: "loss reversal" (亏损反手) mechanism would reverse position every time PnL turned negative. In a sideways market, this meant:

1. Open short → price barely moves → PnL slightly negative due to fees
2. Reverse to long → same thing
3. Each reversal = 2 trades (close + open) = 2 × fees
4. Net per cycle: -$0.03 to -$0.32
5. Over 16 reversals: ~$3-5 in pure fees

**Lesson**: Loss reversal is a death trap for small accounts. Remove it entirely.

### Coins Traded (All Losers)

| Coin | Direction | Outcome | Pattern |
|------|-----------|---------|---------|
| INX | sell/buy flip-flop | -$3+ fees | Reversal death spiral |
| AIGENSYN | sell/buy flip-flop | -$2+ fees | Reversal death spiral |
| AIA | sell | -$1+ fees | Price went sideways → ate fees |
| API3 | buy then sell | -$2+ fees | Wrong direction + reversal |
| BULLA | sell | -$0.50 fees | Opened and immediately closed |
| TAKE | sell | -$0.50 fees | Same pattern |

**Total Phase 1 PnL**: -$43 (net loss from $88 to $45)

## Phase 2: V8 Transition (14:00-14:14)

**Balance**: $45 → ~$43
**Mode**: V8 Clean Architecture, 10x, 2 positions, AI-assisted

First genuine profitable trades:

| Time | Action | PnL | AI Decision |
|------|--------|-----|-------------|
| 14:09 | Close LAYER sell | **+$4.70** | ✅ "High funding rate -2%, take profit" |
| 14:09 | Close 1000XEC sell | **+$2.96** | ✅ "Profit 14.4%, funding rate -0.511%, lock gains" |

**But**: AI offline bug (false heartbeat) → all positions force-closed → lost $1.74 in fees.

**Net Phase 2**: ~+$2.92 (profit from good trades minus fees from false closes)

## Phase 3: V8 Clean (14:21-14:56)

**Balance**: reset to $42.71 → peaked at $51.96 → ended at ~$43
**Mode**: V8 with all fixes applied

### Round-by-Round

| Round | Positions | Gross PnL | Outcome | Analysis |
|-------|-----------|-----------|---------|----------|
| 1 | LAYER + 1000XEC | **-$1.66** | ❌ | Stale peak triggered STOP_DD. Peak=$47.38 from old session, bal=$42.31, dd=10.7% → false close |
| 2 | LAYER + 1000XEC | **+$4.80** | ✅ | AI locked LAYER (-$0.52), AI locked 1000XEC (+$5.32). **This is what V8 should do every time.** |
| 3 | CRCL buy + ERA sell | **-$0.86** | ❌ | Wrong coin selection. CRCL went nowhere. ERA went against direction. |
| 4 | LAYER + 1000XEC | **+$4.83** | ✅ | LAYER +$3.39, 1000XEC +$1.44. Both AI-locked at profit. |
| 5 | CRCL buy + ERA sell | **-$0.86** | ❌ | Same coin selection failure. AI held both waiting for reversal. Never came. |
| 6 | LAYER + 1000XEC | **-$3.63** | ⚠️ | LAYER +$3.96 ✅, 1000XEC -$7.59 ❌. 1000XEC suddenly spiked, triggered STOP_DD. |

**Net Phase 3**: ~+$2.62

### Coin PnL Per Round

| Coin | Round 1 | Round 2 | Round 3 | Round 4 | Round 5 | Round 6 | TOTAL |
|------|---------|---------|---------|---------|---------|---------|-------|
| **LAYER sell** | -$0.38 | **-$0.52** | — | **+$3.39** | — | **+$3.96** | **+$6.45** |
| **1000XEC sell** | -$1.28 | **+$5.32** | — | **+$1.44** | — | **-$7.59** | **-$2.11** |
| CRCL buy | — | — | -$0.17 | — | -$0.17 | — | -$0.34 |
| ERA sell | — | — | -$0.69 | — | -$0.69 | — | -$1.38 |

### Key Insight: LAYER is the Money Maker

```
LAYER short: 4 rounds, +$6.45 total. 
  Pattern: Consistently trending down. AI correctly holds until profit peak then closes.
  
1000XEC short: 4 rounds, -$2.11 total.
  Pattern: High volatility. When it trends down, +$5.32. When it spikes, -$7.59 in minutes.
  Problem: 10x leverage × $25 margin = $250 notional. A 3% spike = $7.50 loss.
  
CRCL buy: Always losing. Fee positive but price doesn't move. 
  Problem: Not a funding arb candidate. Price drift eats the fee profit.

ERA sell: Always losing. Sideways to up.
  Problem: Wrong direction for the coin's price action.
```

## Critical Failure Patterns

### Pattern 1: STOP_DD is Global, Not Per-Position

1000XEC spiked and lost -$7.59. LAYER was +$3.96. Total per-position was -$3.63 + fees.
But DD was calculated against PEAK ($51.96) vs current bal (~$45.97): `(51.96-45.97)/51.96=11.53%`.
Both positions force-closed, even the profitable LAYER.

**Fix**: Either:
(a) Per-position stop (not global)  
(b) Remove STOP_DD entirely and rely on AI for exit decisions  
(c) Make STOP_DD much wider (e.g. 30%) and let AI manage exits  

### Pattern 2: Coin Selection Algorithm is Broken

Current selection: "Top funding rates → pick highest absolute rates → open positions"

This picks LAYER (-1.5% to -2.0% funding) and 1000XEC (-0.5% to -0.7%) consistently.
But coin quality varies enormously:
- LAYER: Good - consistent trend, good funding, AI can manage
- 1000XEC: Bad - spiky, unpredictable, one bad spike wipes out 2 rounds of profit
- CRCL: Bad - low volume, no price action, just eats fees
- ERA: Bad - doesn't follow funding direction

**Fix**: Better coin scoring. Instead of just `abs(funding_rate)`, use a composite score:
```
coin_score = abs(funding_rate) * 1000        # High funding = good
           - atr_pct_4h * 10                 # High volatility = bad  
           + direction_alignment_score * 5    # Price moving with funding = good
           - (fees_paid / margin) * 50        # Already lost money on this coin = bad
```

### Pattern 3: Peak/Daily-Start Stale State on Restart

Every restart caused false STOP_DD and false daily loss triggers. Fixed with startup guards.

### Pattern 4: AI Decision Quality Depends on Prompt Quality

The AI correctly identified:
- LAYER: "funding rate -2%, take profit" → closed at +21% ✅
- 1000XEC: "profit 14.4%, funding -0.511%" → closed at +22% ✅
- CRCL: "loss -0.4%, fee +0.21%, hold" → held correctly ✅
- ERA: "loss -3.6%, close" → closed correctly ✅

But the AI also made bad calls:
- ERA: "switch to LAYER for -2% funding" → closed ERA prematurely, but LAYER was already volatile
- CRCL: closed after holding for minutes → wasted fee, no real risk change

**Fix**: Give AI more context about the coin's volatility pattern, not just current PnL and fees.

## What Worked

| Feature | Works? | Evidence |
|---------|--------|----------|
| AI profit-taking | ✅ | LAYER +$6.45 across 3 correct closes |
| MIN_HOLD=300s | ✅ | Prevented premature closes |
| 10x leverage | ✅ | Manageable risk, no margin calls |
| Daily loss limit | ✅ | Stopped at $2.5-3/day (6% of $45) |
| Position cooling | ✅ | AI didn't close new positions within 5min |
| Heartbeat fix | ✅ | No more false "AI offline" triggers during Phase 3 |

## What Failed

| Feature | Fails? | Reason |
|---------|--------|--------|
| Coin selection | ❌ | Picks high-fee coins regardless of volatility |
| Global STOP_DD | ❌ | Killed profitable LAYER because 1000XEC spiked |
| Stale state | ❌ | Every restart poisoned peak/daily_start |
| 1000XEC | ❌ | Too volatile for $45 balance at 10x |

## Next Actions

1. **Filter 1000XEC out** — add ATR volatility filter (>3% ATR = skip)
2. **Per-coin stop**, not global — let AI decide each coin independently
3. **Composite coin score** — funding × direction × volatility × history
4. **Better AI prompt** — give volatility context, not just raw PnL
