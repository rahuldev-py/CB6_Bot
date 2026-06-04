# CB6 TRUEDATA BACKTEST — FINAL DECISION
> Generated: 2026-05-30 22:49:51
> Period: 9 trading days (TrueData trial)
> Total signals: 35 across 12 combinations

---

## Score Summary

| Dimension | Score | Max | Notes |
|-----------|-------|-----|-------|
| Data Quality | 17 | 20 | OHLC=0 violations, Gaps=208, Coverage=98.9% |
| Historical Feed | 16 | 20 | 15-day cap (trial). Paid plan = 365+ days. |
| OI Value | 17 | 20 | OI confirmed on all bars. Predictive value unmeasured (9-day sample). |
| Backtest Consistency | 14 | 20 | 35 setups fired without errors — scanner runs cleanly on TrueData. |
| Reliability | 14 | 20 | Trial: 1 concurrent WS. Auth: OAuth2 verified. Reconnect: verified. 9-day uptime: stable. |
| **TOTAL** | **78** | **100** | |

---

## Verdict

### **B) Hybrid Historical** — TrueData live/OI, Fyers for deep history

TrueData is superior for live data and OI. Until paid plan provides 90+ days, use Fyers for deep historical lookback in the scanner.

---

## Historical Coverage Limitations

| Limitation | Impact | Resolution |
|------------|--------|------------|
| 15-day bar data (trial) | Cannot validate strategy edge | Purchase standard plan |
| ~9 trading days of signals | All WRs statistically noise | Need ≥90 days |
| 1 concurrent WS (trial) | Cannot run live + backtest simultaneously | Paid plan: multi-session |
| FINNIFTY: 2 Wednesdays | Least data of any index | Wednesday-only, always low |
| No Fyers comparison (token expired) | Cannot quantify signal differences | Re-run with fresh token |

---

## Data Quality Verdict

**PASS.** Zero OHLC violations. Gaps are within acceptable range for NSE data.
OI present on all bars. Coverage 98.9% of expected market-hours bars.

This is the **primary conclusion** this run can validly support:
TrueData historical data quality is high enough for CB6 production use.

---

## Signal Consistency Verdict

**COMPATIBLE.** The CB6 scanner (`scan_silver_bullet`) runs on TrueData DataFrames
without modification. All 12 combinations produced valid output or correctly
returned None when no setup was present. Zero import errors, zero exceptions.

---

## OI Verdict

**STRUCTURALLY READY.** OI data is present, correctly typed, and consumed by
`oi_filters.py`. Predictive value cannot be measured from 9 days. Requires
paid subscription + 90-day dataset to validate.

---

## Recommended Next Steps

| Priority | Action | Rationale |
|----------|--------|-----------|
| 1 | Purchase before 2026-06-09 | Trial expires — Fyers fallback activates |
| 2 | Remove 15-day cap in `data/truedata_feed.py` | `days=min(days, 15)` → `days=days` |
| 3 | Re-run this validation with 90-day data | Get statistically meaningful backtest |
| 4 | Refresh Fyers token and re-run Step 5 | Complete the side-by-side comparison |
| 5 | Monitor one full live session (09:15-15:30) | Validate WS stability under real conditions |
| 6 | A/B test OI filters at 50+ trades | Measure filter contribution to edge |

---

## What Changes After Purchase

One line in `data/truedata_feed.py` line ~113:
```python
# BEFORE (trial)
start_dt = end_dt - timedelta(days=min(days, 15))
# AFTER (paid)
start_dt = end_dt - timedelta(days=days)
```

Everything else is already production-ready.

---

> **This document reflects evidence from 9 trading days only.**
> Strategy performance conclusions require ≥200 trades per combination.
> Data quality and integration conclusions are valid at any sample size.
