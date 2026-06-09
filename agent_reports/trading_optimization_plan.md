# CB6 Quantum — Trading Engine Optimization Plan
**Agent:** CIPHER + SHADOW + SENTINEL
**Phase:** 7 — Trading System Improvement
**Date:** 2026-06-05

---

## Absolute Rules (SENTINEL Override — Never Violate)

1. **Never promise profit** — optimization is about edge, not certainty
2. **Never bypass risk engine** — guards are non-negotiable, optimization happens within them
3. **Never allow user capital without disclaimer** — SaaS signals are for education only
4. **Never auto-expand risk** — no "compound aggression" logic ever
5. **Never deploy untested models** — shadow mode always before any ML → signal path
6. **50-trade observation window** — no strategy change until 50 live trades logged

---

## Current Engine Status (2026-06-05)

### NSE Engine
- Strategy: ICT Silver Bullet (CHoCH + BOS + FVG sweep)
- Live trades: Observation window (< 50 trades)
- Win rate: Unknown — insufficient live data
- Gate for SaaS: ≥ 56% WR validated over 3+ months, n ≥ 100

### GFT $5K Engine
- Strategy: Sweep DOL → CHoCH → FVG fill
- Poll: 15s
- Active symbols: XAGUSD + USOIL
- Current PnL: -$33 (Phase 1 in progress)

### GFT $1K Instant Engine
- Status: Fresh account, no trades yet
- Same strategy as GFT $5K
- Risk: 0.25% per trade = $2.50, max lot 0.01

### ML System
- Status: Shadow mode only (DNN + CNN + RNN)
- Trains: Every 20 trades or 7 days
- Impact on orders: Zero (never touches execution)

---

## Optimization Area 1: Signal Quality

### Current Signal Generation
```
Market data → Pattern scanner → Signal (if criteria met) → Order
```

### Target Signal Generation (optimized)
```
Market data → Pattern scanner → Signal candidate
    → H4 bias filter (pass/fail)
    → Session filter (kill zone? pass/fail)
    → News blackout filter (30-min exclusion)
    → Score calculator (1-20)
    → Grade filter (A/B/A+ only)
    → ML confidence check (shadow, logged not gating)
    → User alert OR order (based on mode)
```

### Signal Quality Metrics to Track
| Metric | Current | Target |
|---|---|---|
| False positive rate | Unknown | < 35% |
| A+ grade win rate | 62.3% (backtest) | ≥ 60% (live) |
| A grade win rate | ~55% (backtest) | ≥ 52% (live) |
| B grade win rate | ~45% (backtest) | Tracked only |
| Overall win rate | ~55% (backtest raw) | ≥ 56% (live validated) |
| Signal frequency | Unknown | 2–5 per day per index |

### Quality Improvement Actions

**Action 1: HTF Alignment Score**
- Current: Binary pass/fail H4 bias check
- Improved: Score the HTF alignment (H4 + D1 both aligned = +2pts, H4 only = +1pt, counter-H4 = -3pts)
- Impact: Reduces counter-trend entries that look good on M15 but fail on context

**Action 2: CHoCH Quality Score**
- Not all CHoCH are equal — a CHoCH on high volume after sweep = stronger
- Score: Clean CHoCH with volume confirmation = +2pts vs weak CHoCH = +1pt
- Impact: Reduces false CHoCH signals

**Action 3: FVG Size Threshold**
- Minimum FVG size filter: reject FVGs smaller than 0.3× ATR(14)
- Too-small FVGs fill instantly without providing meaningful entry window
- Impact: Better entry timing, lower slippage risk

**Action 4: Session Quality Filter**
- Score sessions: London open first hour > London mid > NY > Asian
- Asian session setups (low liquidity) should require higher score threshold (≥ 15 vs ≥ 13)
- Impact: Fewer low-liquidity false setups

---

## Optimization Area 2: Drawdown Reduction

### Current State
- GFT $5K: $170/day hard stop (internal guard), $200 official limit
- GFT $1K: $30/day hard stop
- NSE: No formal drawdown pacing yet (add this)

### Drawdown Reduction Actions

**Action 1: Consecutive Loss Reduction**
- After 2 consecutive losses: reduce next trade size by 25%
- After 3 consecutive losses: reduce by 50%
- After 4 consecutive losses: halt trading for current session
- Reset: After 1 winning trade restores previous size

**Action 2: Daily PnL Pacing**
- Morning (9:15–11:30): Full risk allowed
- If -$50 by 11:30 on GFT $5K: reduce to 50% size for afternoon
- If -$100 by any time: trigger internal warning, go defensive
- If -$140: 50% size mandatory
- If -$170: Hard stop, no more trades today

**Action 3: Weekly Drawdown Awareness**
- If week is down > $300 on GFT $5K: reduce to 50% size for rest of week
- No "revenge trading" mode — system enforces this mechanically

**Action 4: Correlation Risk**
- XAGUSD and USOIL can be correlated in risk-off environments
- Maximum concurrent exposure: 1 open trade per correlated symbol group
- No double-exposure to same directional bias simultaneously

---

## Optimization Area 3: Setup Filtering

### Current Filters
- H4 bias (mandatory)
- Kill zone (07-12 UTC, 16-20 UTC)
- News blackout (30 min)
- Score threshold (≥ 13 required)

### Additional Filters to Add

**Filter 1: Sweep Authenticity Check**
- A sweep must clear the DOL by at least 0.3× ATR(14) before reversing
- Tiny wicks that "technically" sweep DOL are weaker setups
- Impact: Reduces shallow sweep false signals

**Filter 2: CHoCH Confirmation Delay**
- After CHoCH, wait for candle close confirmation before entering FVG
- Do not enter on CHoCH wicks — only confirmed closes
- Impact: Reduces premature entries on false CHoCH

**Filter 3: FVG Freshness**
- Only enter FVGs that haven't been tested more than once already
- Repeated FVG tests weaken the zone
- Impact: Better zone quality

**Filter 4: Pre-Event Blackout**
- Existing: 30 min post-event
- Add: 1 hour PRE-event blackout for high-impact events (RBI rate decisions, US CPI, NFP)
- Impact: Avoids entering positions that get stopped by event volatility

**Filter 5: London-NY Overlap Preference**
- London-NY overlap (12:30–15:00 UTC) has highest liquidity + trend continuation
- During overlap: accept A grade setups
- Outside overlap but in kill zone: require A+ grade
- Impact: Concentrates entries in highest-quality execution windows

---

## Optimization Area 4: Session Filters

### Current Sessions
- London: 07:00–12:00 UTC
- NY: 16:00–20:00 UTC

### Session-Level Optimization

**NSE Sessions (IST)**
- 10:00–11:00 IST: Primary window (Silver Bullet 1)
- 13:00–14:00 IST: Secondary window (Silver Bullet 2)
- 15:00–15:30 IST: Tertiary window (Silver Bullet 3, lower volume = require A+ only)

**Session quality scoring:**
```
Primary window (10-11 IST): Base score multiplier 1.0×
Secondary window (13-14 IST): Base score multiplier 0.9×
Tertiary window (15-15:30 IST): Require score ≥ 16 (A+ only)
```

**Forex sessions:**
```
London open first hour (07-08 UTC): 1.2× quality premium
London-NY overlap (12:30-15:30 UTC): 1.1× quality premium
NY open first hour (13:30-14:30 UTC): 1.0×
End of NY (18-20 UTC): Require A+ only (thin liquidity)
```

---

## Optimization Area 5: Symbol Filters

### Active Symbols (Non-negotiable)
- NSE: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY (all active)
- GFT: XAGUSD + USOIL only (XAUUSD permanently disabled)

### Symbol-Level Quality Filters

**NIFTY vs BANKNIFTY:**
- Both active, but BANKNIFTY is higher volatility = larger ATR = wider SL
- BANKNIFTY trades require higher score threshold: ≥ 15 (vs ≥ 13 for NIFTY)
- FINNIFTY + MIDCPNIFTY: Lower liquidity → require A+ grade only

**XAGUSD:**
- Most liquid of the GFT actives in London session
- Strong during UK metal market hours (08:00-17:00 UTC)
- Weak in Asian session → block XAGUSD entries 00:00-06:00 UTC

**USOIL:**
- Energy-driven — sensitive to API/EIA inventory reports (Wednesday 14:30 UTC)
- Block USOIL entries 30 min before and 1 hour after EIA report
- Add EIA report calendar to news blackout system

---

## Optimization Area 6: Prop-Firm Compliance

### GFT $5K Compliance Checks (automated)
```python
def pre_trade_compliance_check(account: GFT5kState, signal: Signal) -> bool:
    # Daily loss check
    if account.daily_pnl <= -170:
        return False  # Hard stop
    if account.daily_pnl <= -140:
        signal.lot_size *= 0.5  # Reduce size
    
    # Total drawdown check
    if account.total_pnl <= -430:
        return False  # Total halt
    if account.total_pnl <= -350:
        signal.lot_size *= 0.5  # Reduce size
    
    # Kill zone check
    if not in_kill_zone(signal.timestamp):
        return False
    
    # News blackout check
    if in_news_blackout(signal.timestamp):
        return False
    
    # Symbol whitelist
    if signal.symbol in DISABLED_SYMBOLS:
        return False
    
    return True
```

### GFT $1K Compliance Checks
- Same logic, different thresholds ($30 daily, $60 total)
- Maximum lot: 0.01 (enforced at order level, not just check)

### Trading Day Tracking
- Phase 1 requires minimum 3 trading days
- System tracks days with at least 1 completed trade
- Alert when: "You need X more trading days for Phase 1 compliance"

---

## Optimization Area 7: ML Confidence Scoring

### Current State
- DNN, CNN, RNN models running in shadow mode
- Predictions logged, never act on orders
- Accuracy tracked per model

### Target State (Shadow → Influence, future)
- Phase 1: Shadow only (current) — log predictions, track accuracy
- Phase 2 (after n=100 live trades + 60% model accuracy): Use ML to adjust score
  - ML consensus high: +1 point to setup score (not gating, just scoring)
  - ML consensus low: -1 point to setup score (not gating, just scoring)
- Phase 3 (after proven accuracy): ML as tiebreaker for A vs A+ classification
- **ML never blocks or places trades independently — ever**

### Model Performance Tracking
| Model | Prediction | Outcome | Accuracy |
|---|---|---|---|
| DNN | WIN | WIN/LOSS | Tracked per trade |
| CNN | WIN | WIN/LOSS | Tracked per trade |
| RNN | WIN | WIN/LOSS | Tracked per trade |
| Ensemble | Majority vote | WIN/LOSS | Primary metric |

**Retrain trigger:** Every 20 trades OR 7 days, whichever comes first.

---

## Optimization Area 8: Risk-Adjusted Expectancy

### Formula
```
Expectancy = (Win Rate × Average Win) - (Loss Rate × Average Loss)
E = (WR × W) - ((1-WR) × L)
```

### Target expectancy per setup grade
| Grade | Win Rate Target | RR Target | Expectancy Target |
|---|---|---|---|
| A+ | 62%+ | 1:2.5+ | +0.625R per trade |
| A | 55%+ | 1:2.0+ | +0.35R per trade |
| B | 48%+ | 1:1.5+ | +0.24R per trade |

**Any grade with expectancy < 0 over n=30+ trades → remove from live signal list**

### Tracking implementation
- Per-setup-grade P&L tracking in trade journal
- Weekly expectancy report generated automatically
- Alert if any grade's 20-trade rolling expectancy goes negative

---

## Optimization Area 9: False Positive Reduction

### False positive definition
A signal is a false positive if:
1. Score ≥ 13 but price never reaches the FVG zone (phantom signal)
2. FVG zone reached but CHoCH was fake (no actual structure break)
3. Signal fires but cancels before entry (volatility spike)

### Reduction strategies
1. **Entry confirmation timer:** FVG signal requires price to slow + form entry candle before triggering alert
2. **Minimum candle body:** Entry candle body must be ≥ 40% of its range (not a doji)
3. **Volatility gate:** Block signals during ATR spike > 2× 20-period average ATR
4. **Volume confirmation (NSE):** CHoCH candle should have above-average volume for that session

---

## Optimization Area 10: User-Facing Explanation Reports

### Problem
Most algo tools show signals but not reasoning. Users don't understand why → they distrust → they churn.

### Solution: CB6 Signal Intelligence Card

Every signal alert includes:
```
SIGNAL: NIFTY LONG | Grade: A+ | Score: 17/20

Why this setup:
✅ H4 Bias: Bullish (price above 23,800 key level)
✅ Session: London open first hour (highest quality)
✅ Sweep: Sell-side DOL swept at 23,282 (clean, 15pt sweep)
✅ CHoCH: Confirmed close above 23,320 at 07:23 UTC
✅ FVG: 23,295-23,310 zone identified, fresh (1st touch)
⚠️ Counter-note: 4H trend was ranging — counter-trend bias, 50% position size recommended

Entry zone: 23,295–23,310
Stop Loss: 23,267 (below sweep wick + 15pt buffer)
T1: 23,365 (0.618 retrace)
T2: 23,420 (1× measured move)
T3: 23,510 (1.5× measured move)
RR: 1:2.1 minimum

ML Consensus: WIN (DNN 71%, CNN 68%, RNN 59%)
[Shadow mode — informational only]

⚠️ FOR EDUCATIONAL PURPOSE ONLY. Not investment advice.
```

This format educates users AND builds trust in CB6's intelligence.

---

## Optimization Roadmap

| Priority | Action | Timeline | Impact |
|---|---|---|---|
| P1 | Complete 50-trade observation window (NSE) | Now → Jun 30 | Baseline data |
| P1 | GFT $5K Phase 1 pass | Now → target | Proof of concept |
| P2 | Add consecutive loss reduction logic | Week 2 | Drawdown reduction |
| P2 | Add pre-event blackout (1hr) | Week 2 | False positive reduction |
| P2 | FVG freshness filter | Week 3 | Signal quality |
| P3 | HTF alignment scoring (not binary) | Month 2 | Signal quality |
| P3 | Session quality multiplier | Month 2 | Execution quality |
| P4 | Per-setup-grade expectancy tracking | Month 2 | Performance insight |
| P4 | Signal intelligence card format | Month 2 | User trust |
| P5 | ML phase 2 (score influence) | After n=100 + 60% accuracy | ML integration |

---

*Report generated by CIPHER + SHADOW + SENTINEL agents*
*Next: Brand Manifesto → agent_reports/brand_manifesto.md*
