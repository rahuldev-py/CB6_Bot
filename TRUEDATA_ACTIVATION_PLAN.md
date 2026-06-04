# TRUEDATA_ACTIVATION_PLAN.md
# CB6 Quantum — Feature Activation Review & Recommended Order

**Date:** 2026-05-30
**Engineer:** Principal Quant Architect / Claude Code

> **IMPORTANT:** No feature in this plan should be activated automatically.
> All activation gates require successful trial verification first.
> Trial credentials are still pending from TrueData.

---

## Overview

TrueData provides 8 distinct capability sets. Five are already partially integrated.
This document evaluates each for activation readiness, then provides a recommended
activation sequence that minimizes risk to live FTMO and GFT prop firm accounts.

---

## Feature Evaluation

### 1. Historical Data (REST)

**Current State:** Active in production via `data/truedata_feed.py` + Fyers fallback.

**Benefits:**
- NSE index futures data without Fyers API rate-limit pressure.
- Higher quality continuous futures data (NIFTY-I rollover handling).
- Faster scanner startup (no Fyers chunking for large history windows).

**Risks:**
- Column name differences may break normalization (mitigated by `_normalize_columns()`).
- Continuous futures symbol `NIFTY-I` behavior on rollover day is unknown until trial.

**Dependencies:**
- `TRUEDATA_USER` / `TRUEDATA_PASSWORD` env vars set.
- Trial verification of bar format, column names, historical depth.

**Recommended Activation:** ALREADY CONDITIONALLY ACTIVE.
Verify column names and rollover behavior in trial. No code change needed.

---

### 2. Live WebSocket Feed

**Current State:** Code complete (`init_truedata()` in `scanner/websocket_feed.py`).
Not called from `main.py`. Fyers WebSocket is the active feed.

**Benefits:**
- Lower latency than Fyers WebSocket (TrueData co-located with NSE exchange).
- Tick sequence numbers enable gap detection.
- Built-in OI updates per-tick (if included in tick message).
- Bid/Ask available per-tick for spread-aware entry.

**Risks:**
- Unknown connection stability until trial (30-minute stability test required).
- Reconnect behavior under network interruption — untested.
- Symbol format may differ from documentation.
- Switching primary feed during market hours could cause missed signals.

**Dependencies:**
- Trial: 30-minute connectivity test with gap count < 1%.
- Trial: Reconnect test (simulate network drop; verify auto-recovery).
- Trial: Confirm symbol subscription format.
- Code change: One-line gate in `main.py` behind `TRUEDATA_USER` env var.

**Recommended Activation Order:** PHASE 2 (after historical verified).

**Activation Change Required:**
```python
# main.py — add after Fyers auth:
import os
from scanner import websocket_feed
if os.getenv("TRUEDATA_USER"):
    websocket_feed.init_truedata(active_symbols)
else:
    websocket_feed.init(access_token, client_id)
```

---

### 3. Option Chain

**Current State:** Integrated in `provider/truedata/option_chain.py`. Not wired to signal engine.

**Benefits:**
- Real-time CE/PE OI → detect max pain, OI walls, directional bias.
- ATM detection for entry-point confirmation.
- Spread (bid/ask) filtering to avoid illiquid strikes.
- Eliminate manual strike lookup from Telegram commands.

**Risks:**
- API call latency unknown — if >1s per call it will delay signal generation.
- Plan tier may not include option chain (verify with TrueData sales).
- Adding option chain to signal engine increases complexity; bugs could block trades.

**Dependencies:**
- Plan includes option chain access.
- Trial: Measure full chain fetch time for NIFTY (100+ strikes).
- Trial: Confirm expiry format and CE/PE structure.
- Engineering: Wire `get_atm_chain()` into NSE signal engine (estimated 4-6h).

**Recommended Activation Order:** PHASE 3 (after live feed verified).

---

### 4. Greeks

**Current State:** Integrated in `provider/truedata/greeks_client.py`. Not wired to scanner.

**Benefits:**
- IV for premium assessment on option entries.
- Delta for hedge sizing in multi-leg setups.
- Theta awareness for time-decay trade management.
- IV rank / IV percentile calculations (to be added from Greeks data).

**Risks:**
- Greeks may be delayed (end-of-minute update) rather than real-time.
- Plan tier exclusion is a common TrueData restriction.
- IV accuracy at market open/close is typically poor (wide bid/ask → bad IV).

**Dependencies:**
- Plan includes Greeks.
- Trial: Validate IV range (should be 5–150% for liquid NSE options).
- Trial: Validate Delta range (CE [0,1], PE [-1,0]).
- Engineering: Wire into signal engine for IV-based entry filters (estimated 3-4h).

**Recommended Activation Order:** PHASE 4 (after option chain verified).

---

### 5. OI Streaming

**Current State:** OI field present in `MarketTick` model. No consumer code.

**Benefits:**
- Real-time OI buildup/unwind detection at key levels.
- OI + price action confirmation for Silver Bullet entries.
- Detect trapped positions (large OI at a level that breaks → fast move).

**Risks:**
- OI may be batch-updated (every 3-5 minutes at NSE) not truly real-time.
- If stale, OI-based signals fire on outdated data — no better than end-of-day OI.
- Adds complexity to already-working signal engine.

**Dependencies:**
- Trial: Confirm OI update frequency (tick-level vs batch).
- Trial: Confirm OI field name in live tick message.
- Engineering: Write OI consumer in scanner (estimated 6-8h including testing).

**Recommended Activation Order:** PHASE 5 (after Greeks, if OI is confirmed real-time).

---

### 6. Bid/Ask Spread Filters

**Current State:** Bid/Ask parsed from tick messages in `MarketTick`. No filter code.

**Benefits:**
- Avoid entering options with >2% spread (execution slippage risk).
- Use bid/ask midpoint for more accurate LTP than last-trade price.
- Spread widening detection as early signal of liquidity concern.

**Risks:**
- Minor: Spread-based filters may block valid entries in thin pre-market windows.
- If bid/ask not in TrueData tick stream (documentation ambiguous), feature is unavailable.

**Dependencies:**
- Trial: Confirm bid/ask presence in tick messages.
- Engineering: Add spread filter to option entry logic (estimated 2-3h).

**Recommended Activation Order:** PHASE 3 (alongside option chain — same trial checkpoint).

---

## Recommended Activation Sequence

```
PHASE 1 — NOW (No Trial Required)
─────────────────────────────────
✓  Historical REST (already active — just verify in trial)
✓  All 4 critical hardening fixes applied (data/truedata_feed.py)

PHASE 2 — After Trial: Connectivity (Day 1-2 of trial)
─────────────────────────────────────────────────────────
[ ] Verify historical column format → confirm TrueData as primary
[ ] 30-min WebSocket stability test → measure tick gap rate
[ ] If gap rate < 1%: wire init_truedata() into main.py behind env gate

PHASE 3 — After Trial: Market Data Quality (Day 2-3 of trial)
───────────────────────────────────────────────────────────────
[ ] Verify option chain latency and format
[ ] Confirm bid/ask in tick stream
[ ] Wire option chain to ATM strike finder (standalone, not in signal path yet)
[ ] Add bid/ask spread filter to option entry check

PHASE 4 — After Trial: Greeks & OI (Day 3-5 of trial)
───────────────────────────────────────────────────────
[ ] Validate Greek ranges with 10-strike sample
[ ] Confirm OI update frequency
[ ] If OI is real-time: wire OI consumer (entry confirmation)
[ ] If OI is batch: note limitation, do not use for intraday signals

PHASE 5 — Production (Only After Full Trial Pass)
─────────────────────────────────────────────────
[ ] Set TrueData as primary for all data paths
[ ] Degrade Fyers WebSocket to explicit fallback (keep running, lower priority)
[ ] Wire Greeks to IV filter for option entries
[ ] Wire OI to Silver Bullet confirmation (if real-time)
[ ] Daily symbol master refresh on startup
```

---

## Do NOT Activate (Scope Exclusions)

| Feature | Reason |
|---------|--------|
| Crypto / Binance via TrueData | Shelved until prop firm phase complete |
| Forex symbols via TrueData | Forex uses MT5 direct for XAGUSD/USOIL/EURUSD — TrueData is NSE-only |
| Brokera.in SaaS feed | Do not build until NSE live WR ≥ 56% and GFT funded |

---

## Estimated Engineering Hours (Post-Trial)

| Feature | Estimated Hours |
|---------|----------------|
| Wire init_truedata() into main.py | 0.5h |
| Wire option chain to ATM finder (standalone) | 4h |
| Wire bid/ask spread filter to option entry | 2h |
| Wire OI consumer (if real-time confirmed) | 6h |
| Wire IV filter to option signal engine | 3h |
| Daily symbol master refresh | 1h |
| Decommission Fyers WS to fallback role | 2h |
| **Total** | **~18.5h** |

All estimates assume trial verification is complete and APIs behave as documented.
Add 50% buffer for undocumented behavior discovered during integration.
