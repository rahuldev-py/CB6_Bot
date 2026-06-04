# TRUEDATA_PURCHASE_DECISION.md
# CB6 Quantum — Trial Verification Framework & Purchase Decision Criteria

**Date:** 2026-05-30
**Engineer:** Principal Quant Architect / Claude Code

> Trial credentials have not yet been issued by TrueData.
> This document defines exactly what must be verified during the trial to make
> a confident, data-driven purchase decision.

---

## Verification Scorecard

Each item is scored Pass / Fail / Partial.
A purchase recommendation requires ≥ 90% Pass on MUST PASS items and no Fail on any CRITICAL item.

---

## Section 1 — Authentication & Session Management

| # | Test | Must Pass | Score |
|---|------|-----------|-------|
| A1 | Login with trial credentials returns a token within 5 seconds | YES | — |
| A2 | Token is valid for ≥ 8 hours (or stated expiry is accurate) | YES | — |
| A3 | Auto-refresh works before expiry (test by reducing buffer to 10 min) | YES | — |
| A4 | Session survives a 30-minute idle period with no requests | YES | — |
| A5 | Re-login after deliberate logout returns a valid token | YES | — |
| A6 | Invalid credentials return clear error (not a silent timeout) | NO | — |

---

## Section 2 — Historical Data Quality

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| H1 | Fetch NIFTY-I 15-min bars for last 30 days | YES | Returns ≥ 1,800 bars (6.5h × 30d ÷ 15min) | — |
| H2 | Fetch BANKNIFTY-I 5-min bars for last 30 days | YES | Returns ≥ 3,900 bars | — |
| H3 | Fetch EOD bars for last 365 days | YES | Returns ≥ 240 bars (trading days) | — |
| H4 | Bar OHLC values match Fyers API for same symbol/date (within 0.1%) | YES | Sample 5 random bars | — |
| H5 | No duplicate timestamps in returned data | YES | 0 duplicates | — |
| H6 | Timestamps are in IST (not UTC) or consistently offset-aware | YES | Offset matches known IST session | — |
| H7 | Gap count on a normal trading day is ≤ 2 bars per hour | YES | Count gaps during full session | — |
| H8 | Data available for all 4 indices: NIFTY-I, BANKNIFTY-I, FINNIFTY-I, MIDCPNIFTY-I | YES | All 4 return data | — |
| H9 | Continuous futures handles rollover correctly (close price same day both months) | YES | Test on last rollover date | — |
| H10 | Fetch speed: 30-day 15-min request completes in < 5 seconds | NO | Acceptable up to 10s with caching | — |

---

## Section 3 — Connection Stability (WebSocket)

Run during a market session (09:15–15:30 IST). Subscribe to: NIFTY-I, BANKNIFTY-I.

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| W1 | WebSocket connects within 5 seconds | YES | TCP + handshake < 5s | — |
| W2 | Receive first tick within 30 seconds of market open | YES | First tick latency < 30s | — |
| W3 | 30-minute continuous session with no unexpected disconnect | YES | 0 connection drops | — |
| W4 | Tick missing rate < 1% over 30-minute session | YES | Sequence gaps / total ticks < 1% | — |
| W5 | Reconnect after deliberate network drop restores feed in < 60 seconds | YES | Test by disabling NIC for 5s | — |
| W6 | Average tick latency (exchange time vs receive time) < 500ms | YES | p95 latency < 500ms | — |
| W7 | Peak latency < 2 seconds during high-volatility (open/close) | YES | During 09:15–09:30 window | — |
| W8 | Heartbeat mechanism keeps connection alive over 10-minute quiet period | YES | No disconnect during low-tick interval | — |
| W9 | Unsubscribe and re-subscribe without reconnecting | NO | Nice to have for OI rotation | — |
| W10 | Feed includes OI field in tick message | NO | Important for Phase 5 but not blocking | — |

---

## Section 4 — Tick Quality

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| T1 | LTP in tick matches Fyers LTP within 2 ticks (1 point) | YES | Spot-check 10 ticks | — |
| T2 | Sequence numbers are present and monotonically increasing | NO | Required for gap detection | — |
| T3 | Tick timestamps reflect exchange time, not arrival time | YES | Should match NSE feed time | — |
| T4 | Bid/Ask fields present in tick message | NO | Required for Phase 3/5 | — |
| T5 | Volume (TTQ) matches Fyers within 5% at any given moment | YES | Spot-check at 10:00, 14:00 | — |

---

## Section 5 — Historical Data Accuracy

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| H11 | Historical data for today matches live tick accumulation at session end | YES | OHLC within 0.1% | — |
| H12 | No weekend/holiday bars present (data skips non-trading days) | YES | Check a known holiday | — |
| H13 | Intraday bars align to NSE session (no pre-market bars before 09:15) | YES | First bar at 09:15 | — |

---

## Section 6 — Option Chain (if plan includes it)

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| O1 | Fetch NIFTY option chain for current expiry | YES | Returns CE+PE for ≥ 20 strikes | — |
| O2 | Chain fetch latency < 2 seconds | YES | Measure 5 calls, take p95 | — |
| O3 | ATM CE LTP matches live tick within 2 ticks | YES | Spot-check at any time | — |
| O4 | OI values present and non-zero for liquid strikes | YES | ATM ±3 strikes have OI > 50,000 | — |
| O5 | Chain available for weekly and monthly expiry | NO | Nice to have | — |
| O6 | Next-week expiry chain is available before current expiry closes | NO | Important for rollover | — |

---

## Section 7 — Greeks (if plan includes it)

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| G1 | Greeks API returns data for a specific option symbol | YES | Non-null response | — |
| G2 | IV is in range 5%–300% for liquid strikes | YES | ATM IV 5–150% typical | — |
| G3 | Delta for ATM CE is 0.45–0.55 | YES | During mid-session | — |
| G4 | Delta for ATM PE is -0.55 to -0.45 | YES | During mid-session | — |
| G5 | Greeks update frequency is ≤ 60 seconds | NO | If batch-updated, note limitation | — |
| G6 | Greeks available for same expiry as option chain | YES | Must be same scope | — |

---

## Section 8 — Symbol Master

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| S1 | Symbol master download completes within 30 seconds | YES | File format parseable | — |
| S2 | NIFTY weekly option symbols present with correct expiry dates | YES | Both CE and PE | — |
| S3 | Strike step sizes correct (NIFTY 50pt, BANKNIFTY 100pt) | YES | Verify from master | — |
| S4 | Continuous futures symbols (NIFTY-I, BANKNIFTY-I) present | YES | Required for historical | — |

---

## Section 9 — Session Limits

| # | Test | Must Pass | Pass Criteria | Score |
|---|------|-----------|--------------|-------|
| L1 | REST API calls do not hit rate limit at 10 req/sec | YES | Run 50 calls in 5 seconds | — |
| L2 | WebSocket allows ≥ 10 concurrent symbol subscriptions | YES | Subscribe NIFTY-I + 9 option strikes | — |
| L3 | No hard session limit (kicks user after N hours) | YES | Test 4-hour session | — |
| L4 | Concurrent connections allowed (trial + verify simultaneously) | NO | Good to know | — |

---

## Scoring Rubric

| Score | Meaning |
|-------|---------|
| **FULL PASS** | ≥ 90% Must Pass items pass AND 0 CRITICAL failures | → Proceed to purchase |
| **CONDITIONAL PASS** | 75–90% Must Pass items pass AND 0 CRITICAL failures | → Purchase with noted limitations |
| **HOLD** | 50–75% Must Pass pass OR 1 CRITICAL failure | → Negotiate or wait for fix |
| **NO PURCHASE** | < 50% Must Pass pass OR 2+ CRITICAL failures | → Seek alternative provider |

**CRITICAL items** (any single failure = HOLD or NO PURCHASE):
- A1 (Authentication works)
- H1, H4 (Historical data present and accurate)
- W3, W4 (WebSocket stable, < 1% tick loss)
- T1 (LTP accuracy within 1 point)

---

## Cost Justification Threshold

Purchase is justified if:
1. Trial score: FULL PASS or CONDITIONAL PASS.
2. TrueData reduces Fyers API dependency (rate limit pressure removed).
3. Live tick latency is demonstrably lower than Fyers WebSocket (measured in trial).
4. Option chain is included in plan AND latency < 2s (unlocks OI-based entry confirmation).

Purchase is NOT justified (keep Fyers only) if:
- Historical data accuracy is poor (H4 fails).
- WebSocket tick loss > 1% under normal conditions (W4 fails).
- Monthly cost exceeds prop firm daily profit target ($25/day for GFT, $50/day for FTMO).

---

## Alternatives to Consider (If TrueData Trial Fails)

| Alternative | Strength | Weakness |
|-------------|----------|---------|
| Fyers (current) | Already working, free with brokerage | Rate limits, 100-day history cap |
| NSE real-time paid feed (user planned) | Direct NSE, most accurate | Setup complexity, cost |
| Upstox API | Good NSE coverage | Different API format, migration cost |
| Angel One SmartAPI | Free tier available | Lower reliability, less historical |
