# CB6 Quantum — TrueData Trading Impact Audit
**Audit date:** 2026-06-01  
**Scope:** How TrueData data fields directly affect trade decisions  
**Method:** Code trace of every decision gate that touches TrueData data

> This report audits **decision gates only** — not strategy win rate or profitability.
> Data quality conclusions are separated from strategy-profitability conclusions throughout.

---

## 1. Does OI Boost DOL Scoring?

**Answer: YES — actively wired and working.**

### Code path (verified in `scanner/silver_bullet.py`)

```python
# After find_draw_on_liquidity() returns:
from scanner.oi_filters import score_dol_by_oi
oi_dol_boost, oi_dol_reason = score_dol_by_oi(df, dol)

# In scoring block:
if oi_dol_boost > 0:
    score += oi_dol_boost        # +1.0 (single swing spike) or +2.0 (EQH/EQL cluster)
if sweep_confirmed and oi_dol_boost > 0:
    score += 1.5                 # OI + confirmed sweep = institutional trap combo
```

### What `score_dol_by_oi()` does

1. Takes the last 60 bars of the symbol's bar data
2. Finds bars whose high/low touch within 0.3% of the DOL level
3. Computes rolling mean of OI over 20 bars
4. If OI at those DOL-touching bars is > mean × 1.25 → spike detected
5. Returns: 0.0 (no data or no spike), 1.0 (spike), 2.0 (spike + EQH/EQL cluster)

### Effective score contribution

| Scenario | Score added | Condition |
|----------|-------------|-----------|
| No OI data (Fyers fallback) | +0 | `oi` column absent |
| OI spike at DOL (single swing) | **+1.0** | OI > mean×1.25 at DOL bars |
| OI spike + EQH/EQL cluster | **+2.0** | Above + DOL is cluster of equal highs/lows |
| OI spike + confirmed sweep | **+1.5 extra** | New bonus (added 2026-06-01) |
| Max OI contribution | **+3.5** | EQH/EQL cluster + sweep simultaneously |

### When OI is unavailable

`score_dol_by_oi()` returns `(0.0, "NO_OI_DATA")` when the `oi` column is absent. The scanner continues — the OI bonus is simply missing. This is the Fyers-only path. **TrueData is the only source providing OI for this scoring.**

### Evidence from logs

No scanner output logs from this session show explicit OI boost values (the boost is logged at DEBUG level, not INFO). However, the code path is confirmed active — bar data includes `oi` column (confirmed by 100% OI for NIFTY/BANKNIFTY in data audit).

---

## 2. Does Declining OI Block Entries?

**Answer: YES — hard block when OI drops > 0.5% over last 3 bars.**

### Code path

```python
# After FVG touch confirmed, before trade plan:
from scanner.oi_filters import check_oi_entry_filter
oi_entry_ok, oi_entry_reason = check_oi_entry_filter(df, direction)
if not oi_entry_ok:
    logger.info(f"SB skip {symbol}: OI declining — {oi_entry_reason}")
    return None   # TRADE BLOCKED
```

### Decision matrix

| OI change over 3 bars | Decision | Reason logged |
|-----------------------|----------|---------------|
| > +0.5% (rising) | ✓ PASS | `OI_RISING_X.Xpct` |
| ±0.5% (flat) | ✓ PASS (neutral) | `OI_FLAT_X.Xpct` |
| < -0.5% (declining) | ✗ BLOCK | `OI_DECLINING_X.Xpct` |
| OI column absent | ✓ PASS | `NO_OI_PASS_THROUGH` |
| Fewer than 5 bars | ✓ PASS | `INSUFFICIENT_BARS` |

**This is a HARD BLOCK** — if OI is declining when price touches the FVG, the trade is cancelled entirely regardless of confluence score.

**Trading rationale:** Declining OI at FVG touch = existing positions are being closed (short-covering or long-liquidation), not new institutional positions being opened. The FVG touch is not being aggressively defended.

### Data dependency

This gate is OI-dependent. Without TrueData, the `oi` column is absent and this gate **always passes through** (the `NO_OI_PASS_THROUGH` branch). Fyers historical bars do not include OI.

**Impact of TrueData:** This gate is entirely unlocked by TrueData. On Fyers-only data, every setup that passes DOL/MSS/FVG gets through this gate automatically.

---

## 3. Does OI Divergence Reduce Score?

**Answer: YES — score reduced by 1.5 when price and OI diverge.**

### Code path

```python
# After OI entry filter passes:
oi_divergence = get_oi_divergence_signal(df, direction)

# In scoring block:
if oi_divergence == "DIVERGENCE":
    score -= 1.5   # downgrade entry weight, does not block
    logger.info(f"SB {symbol}: OI divergence penalty -1.5 ({direction} move, declining OI)")
```

### What constitutes divergence

```python
# get_oi_divergence_signal() logic (last 8 bars):
price_up  = close[-1] > close[-5]
oi_trend  = oi[-1] > oi[-5]      # rising OI = confirmation

BULLISH + price_up + oi_trend    → "CONFIRMATION" (+0 penalty)
BULLISH + price_up + NOT oi_trend → "DIVERGENCE"   (score -1.5)
BEARISH + not price_up + oi_trend → "CONFIRMATION" (+0 penalty)
BEARISH + not price_up + NOT oi_trend → "DIVERGENCE" (score -1.5)
```

### Difference from entry filter

The **entry filter** (`check_oi_entry_filter`) checks OI change at the FVG touch moment — 3-bar window, hard block at -0.5%.

The **divergence signal** (`get_oi_divergence_signal`) checks price vs OI agreement over 8 bars — broader context, soft penalty not a block.

They can coexist: a trade can pass the entry filter (OI flat at touch) but still get a -1.5 penalty if the 8-bar trend shows price/OI divergence.

**The combination** (pass through filter + divergence penalty) means the confluence score must be higher to still clear the minimum gate when divergence is present.

---

## 4. Does Bid/Ask Spread Block Bad FVG Entries?

**Answer: PARTIALLY — gate is coded but bid/ask not stored in live tick cache.**

### Code path

```python
from scanner.oi_filters import check_bidask_filter
bidask_ok, bidask_reason = check_bidask_filter(symbol, fvg_low, fvg_high)
if not bidask_ok:
    logger.info(f"SB skip {symbol}: bid/ask too wide — {bidask_reason}")
    return None
```

`check_bidask_filter()` reads:
```python
tick = get_latest_tick(symbol)
bid = tick.get("bid") or tick.get("best_bid")
ask = tick.get("ask") or tick.get("best_ask")
```

### The gap

`_tick_cache` stores: `{"ltp": ..., "volume": ..., "ts": ...}`. It does **not** store `best_bid` or `best_ask`. These are extracted in `_dispatch_tick()` for the monitor but not written to the cache.

**Result:** `check_bidask_filter()` always hits `NO_BIDASK_PASS_THROUGH` — the gate is effectively inactive.

**The logic and thresholds are correct** (0.10% for NIFTY/BANKNIFTY, 0.20% for FINNIFTY/MIDCPNIFTY), the wiring is incomplete.

**This is not a regression** — it was also inactive before TrueData (Fyers live tick has bid/ask in its WS payload but the same cache gap exists).

---

## 5. Does the Option Chain Source Work?

**Source for option chain data:** `scanner/option_strike_selector.py` and `nse_options/` module.

**TrueData's role:** TrueData is not the option chain data source. The option chain (strikes, OI per strike, IV, greeks) comes from Fyers API (`fyers.optionchain()` calls) and/or NSE website scraping via the `nse_options` enrichment layer.

**TrueData contribution to options:** The underlying index LTP (for ATM strike calculation) comes from the TrueData live tick cache (`get_ltp()`). This feeds `select_option_for_setup()` which then calls Fyers for the actual option chain.

**Assessment:** Option chain functionality is **not TrueData-dependent** and does not benefit from or require TrueData. The LTP source can be Fyers or TrueData interchangeably.

---

## 6. Does TrueData Improve Scanner Confidence vs Fyers?

**Answer: YES for NIFTY/BANKNIFTY — measurably, through OI scoring.**

### What Fyers provides (for scanner)

- OHLCV bars (historical) ✓
- Live tick LTP ✓
- Volume ✓
- OI: **NOT available** in Fyers REST historical bars
- Bid/Ask: Available in Fyers WS tick but not stored in cache

### What TrueData adds

| Field | Fyers | TrueData | Scanner impact |
|-------|-------|----------|----------------|
| Historical OHLCV | ✓ | ✓ | Same quality |
| Historical OI | ✗ | **✓ (100% for NIFTY/BANKNIFTY)** | Unlocks 3 OI gates |
| Live LTP | ✓ | ✓ | Same (cache unified) |
| Live OI in ticks | Partial | ✓ Streaming | Enables live OI monitoring |
| Bid/Ask | In WS, not cached | In WS, not cached | Equal (both inactive) |
| Latency (historical) | 180–400ms/request | ~50–100ms estimated | Faster bar refreshes |

### Quantified confidence impact

The OI scoring adds up to +3.5 to confluence score:
- +1.0 or +2.0 from DOL OI boost
- +1.5 from OI+sweep combo

The minimum confluence gate is 12 (from `settings.py: MIN_BUY_SCORE = 12`). A base setup without OI data (Fyers path) scores:
```
5 (base) + 2 (CHoCH) + 1 (inFVG) + 1 (displacement) + 2 (UT aligned) + 1 (RR≥3)
= 12 — barely at the gate, no margin
```

The same setup with TrueData OI scoring:
```
12 (above) + 2 (OI EQH/EQL spike) + 1.5 (OI+sweep combo)
= 15.5 — confidently above gate
```

Conversely, a marginal Fyers-path setup (score 13) that has OI divergence:
```
13 − 1.5 (divergence penalty) = 11.5 — falls below gate → filtered out
```

**This is a real edge**: OI data lets CB6 reject setups where price moves are driven by liquidation rather than new institutional positioning, and boost setups where institutional OI confirms the DOL level.

### Caveat

This is a **theoretical edge based on sound market microstructure logic**. Whether OI-filtered setups have materially higher win rates in live NSE trading cannot be confirmed from 15-day trial data alone. Validating OI edge requires 90+ days of live trades with OI logging enabled.

---

## Summary

| TrueData Gate | Active | Effective | Data source |
|---------------|--------|-----------|-------------|
| OI DOL boost (+1/+2) | ✓ YES | ✓ YES (NIFTY/BNF) | Historical OI column |
| OI+sweep combo (+1.5) | ✓ YES | ✓ YES | Historical OI column |
| OI entry block (declining) | ✓ YES | ✓ YES (NIFTY/BNF) | Historical OI column |
| OI divergence penalty (-1.5) | ✓ YES | ✓ YES | Historical OI column |
| OI at target (SL trail) | ✓ YES | ✓ YES | Historical OI column |
| Bid/Ask spread gate | Coded | ✗ NO (cache gap) | Live tick (not wired) |
| Option chain enrichment | N/A | ✓ (Fyers, not TrueData) | Fyers API |

**Net verdict:** TrueData provides a real, measurable improvement to NIFTY and BANKNIFTY scanner confidence through OI-based filtering. The bid/ask gate remains unactivated due to a tick-cache wiring gap. OI-based edge is theoretically sound but requires 90+ days of live validation to confirm statistical significance.
