# Fyers vs TrueData — Final Comparison Report
**Audit date:** 2026-06-01  
**Scope:** Data completeness, OI, bid/ask, latency, historical coverage, scanner differences  
**Method:** Code analysis + log evidence + architectural review

---

## Comparison Matrix

| Dimension | Fyers | TrueData | Winner |
|-----------|-------|----------|--------|
| Historical OHLCV quality | ✓ Good | ✓ Good | Tie |
| Historical OI | ✗ Not available | ✓ 100% (NIFTY/BANKNIFTY) | **TrueData** |
| Historical coverage limit | 100 days (REST) | **15 days (trial)** / unlimited (paid) | Fyers (trial), TrueData (paid) |
| Live tick LTP | ✓ WebSocket | ✓ WebSocket | Tie |
| Live OI streaming | Partial / indirect | ✓ Direct stream | **TrueData** |
| Live bid/ask | In WS payload (not cached) | In WS payload (not cached) | Tie (both inactive) |
| Historical request latency | 180–400 ms per request | ~50–100 ms estimated | **TrueData** |
| Rate limiting pressure | 5.5 req/sec enforced in code | No rate limit observed | **TrueData** |
| Chunking requirement | Yes (90-day chunks) | No | **TrueData** |
| API stability | Proven, 1+ year in CB6 | New (2 weeks of CB6 usage) | Fyers |
| Bar count reliability | High | FINNIFTY lower (74 vs 125+) | Fyers (FINNIFTY only) |
| NIFTY/BANKNIFTY bar quality | ✓ High | ✓ High (0 OHLC violations) | Tie |
| Session expiry handling | Token refresh via auto_token.py | State machine reconnect | Tie |
| Cost | Bundled with brokerage | Separate subscription (~₹2,500/mo) | Fyers |
| OI-based scanner gates | ✗ All inactive | ✓ All 4 OI gates active | **TrueData** |

---

## 1. Data Completeness

### Historical OHLCV

Both sources produce equivalent OHLCV bar quality for NIFTY and BANKNIFTY when data is present. The difference:

**Fyers:** Requires chunked fetching (90-day REST limit), rate-limited at 5.5 req/sec, 2-minute cache. On cache miss, a 3-symbol scan takes 3 × 1 API call × ~300ms = ~900ms just for bar data.

**TrueData:** Single call per symbol, no chunking, sub-100ms estimated. On cache miss, same 3-symbol scan = ~300ms.

FINNIFTY bar count discrepancy (74 TrueData vs expected 125+) is the one area where Fyers delivers more consistent data. However the 1m FINNIFTY coverage on TrueData is the known structural issue — the 3m data gap may be a sampling artifact of how TrueData constructs continuous futures.

### Assessment

For raw historical bar data, both sources are adequate. TrueData is faster. Fyers is more proven for FINNIFTY.

---

## 2. OI Availability

This is the **decisive differentiator**.

| Source | Historical OI | Live OI |
|--------|--------------|---------|
| Fyers REST | ✗ Not in response | ✗ Not in WS tick |
| TrueData REST | ✓ 100% for NIFTY/BANKNIFTY | ✓ In WS tick stream |

**Scanner impact without OI (Fyers path):**
- `score_dol_by_oi()` → returns 0.0, NO_OI_DATA → +0 to score
- `check_oi_entry_filter()` → returns True, NO_OI_PASS_THROUGH → always passes
- `get_oi_divergence_signal()` → returns None → no penalty

Every setup that clears DOL/MSS/FVG gets through all OI gates unconditionally on Fyers. There is no institutional positioning confirmation.

**Scanner impact with TrueData (NIFTY/BANKNIFTY):**
- Up to +3.5 score boost for confirmed institutional positioning at DOL
- Hard block when OI declining at FVG touch
- -1.5 score penalty when price/OI diverge over 8 bars

This is a structural edge that Fyers cannot provide, regardless of plan tier.

---

## 3. Bid/Ask Availability

| Source | Status |
|--------|--------|
| Fyers WS | `best_bid`, `best_ask` in tick payload, **not stored in CB6 cache** |
| TrueData WS | `best_bid`, `best_ask` in tick payload, **not stored in CB6 cache** |

Both sources provide bid/ask in the live WS tick. Neither is stored in the current `_tick_cache` implementation. The `check_bidask_filter()` gate is inactive for both. This is equal — a code fix (not a data provider fix) would activate it for either source.

---

## 4. Latency

### Historical bar fetch

| Scenario | Fyers | TrueData |
|----------|-------|----------|
| Cache hit (120s TTL) | ~0ms | ~0ms |
| Cache miss, 1 symbol | ~180–400ms | ~50–100ms estimated |
| Cache miss, 4 symbols | ~720ms–1,600ms | ~200–400ms estimated |
| Rate limit pressure | Yes (5.5/s enforced) | None observed |

The 3-min scanner fires every 3 minutes. With TrueData as primary, cache-miss latency is lower and there is no request queuing/throttling.

### Live tick latency

Both use WebSocket push — latency is network-dependent. TrueData's NSE feed is typically collocated or near-collocated with exchange. Exact latency will be measured after first live session.

---

## 5. Historical Coverage

| Source | Max lookback |
|--------|-------------|
| Fyers REST | ~100 days (intraday) |
| TrueData trial | **15 days** (hard limit) |
| TrueData paid | Configurable (1–5+ years depending on plan) |

**Critical point:** The 15-day trial limit is a trial-specific restriction. On the paid subscription the coverage should extend to 1+ year of intraday data. For backtesting purposes, TrueData paid would significantly exceed Fyers.

For the current live trading use case (scanner needs last 3–5 days of bars), both are adequate.

---

## 6. Scanner Output Differences

When TrueData is primary vs Fyers-only, the scanner produces different decisions:

### Same DOL/MSS/FVG setup — Score comparison

| Setup characteristic | Fyers-only score | TrueData score | Δ |
|---------------------|-----------------|----------------|---|
| Base (DOL+MSS+FVG) | 5 | 5 | 0 |
| CHoCH | +2 | +2 | 0 |
| FVG touch, displacement | +2 | +2 | 0 |
| UT aligned | +2 | +2 | 0 |
| RR ≥ 3 | +1 | +1 | 0 |
| OI spike at DOL (institutional) | 0 | **+1** | +1 |
| OI EQH/EQL cluster | 0 | **+2** | +2 |
| OI+sweep combo | 0 | **+1.5** | +1.5 |
| OI declining at entry | Pass-through | **Block** | Trade eliminated |
| OI divergence | No penalty | **-1.5** | -1.5 |

**Fyers path maximum for this example:** 5+2+2+2+1 = 12 (exactly at gate)  
**TrueData path with good OI:** 12+2+1.5 = 15.5 (confidently above gate)  
**TrueData path with divergence:** 12-1.5 = 10.5 (below gate → filtered)

The Fyers path would trade all three of these. TrueData filters the divergence setup (better selectivity) and boosts the confirmed setup (higher confidence).

---

## Recommendation

### Architecture: **Hybrid (TrueData Primary, Fyers Fallback)**

This is already the implemented architecture and is the correct one.

**Rationale:**

1. **TrueData PRIMARY for historical bars:** Faster, no chunking, OI included. Use for all scanner bar fetches.

2. **Fyers FALLBACK for bars:** Essential safety net. If TrueData session expires mid-session, the scanner degrades gracefully rather than stopping.

3. **TrueData PRIMARY for live ticks:** Lower latency, streaming OI available (not yet used from ticks).

4. **Fyers ALWAYS for option chain:** Fyers `fyers.optionchain()` is the data source for strikes, delta, and option OI. TrueData does not provide this in the current integration.

5. **Do not reject Fyers entirely:** Token-based authentication that refreshes daily via `auto_token.py` is proven and stable. It is the execution API for order placement.

### Should TrueData be purchased?

This is answered in the Final Verdict report. The data-quality answer: **yes, the OI data alone justifies the cost if CB6 is generating live trades** and the OI edge is validated over 90+ days.

---

## Final Answer

| Option | Verdict | Reason |
|--------|---------|--------|
| Keep Fyers only | ✗ REJECT | No OI in historical data — all OI gates inactive |
| Use Hybrid (TrueData primary + Fyers fallback) | **✓ CURRENT ARCHITECTURE — KEEP** | Best of both |
| Make TrueData sole source | ✗ REJECT | Fyers needed for order execution + option chain |
| Reject TrueData | ✗ REJECT | Eliminates the only OI data source in CB6 |

**Recommendation: Maintain hybrid architecture. Purchase TrueData subscription after FTMO/GFT profit extraction.**
