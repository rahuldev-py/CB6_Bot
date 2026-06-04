# FINNIFTY — TrueData Backtest Report
> Generated: 2026-05-30 22:49:51
> Strategy: CB6 Quantum ICT Silver Bullet (unchanged)
> Data: TrueData trial — 9 trading days (2026-05-18 to 2026-05-29)
> Note: Sample is too small for statistical conclusions — see STATISTICAL_VALIDITY_REPORT.md

---

## Results by Timeframe

| TF | Setups | W/L | Win Rate | Total R | Avg R | PF | T3/SL | Avg Hold | Max DD |
|----|--------|-----|----------|---------|-------|-----|-------|----------|--------|
| 1min | 0 | — | — | — | — | — | — | — | — |
| 3min | 0 | — | — | — | — | — | — | — | — |
| 5min | 1 | 1/0 | **100.0%** | +2.50R | +2.500R | 250.00 | 0/1 | 3.0 | -0.00R |

---

## Data Coverage

| TF | Bars | Expected (9d) | Real Coverage% | OI Present | Notes |
|----|------|--------------|---------------|------------|-------|
| 1min | 799 | ~3,375 | ~23.7% | ✅ | Low-volume instrument — expected |
| 3min | 562 | ~1,125 | ~50.0% | ✅ | Reasonable — aggregation helps |
| 5min | 459 | ~675 | ~68.0% | ✅ | Best resolution for this index |

> **FINNIFTY-I 1min coverage is ~24% of expected bars.** This is normal behaviour for this
> low-volume continuous futures contract — TrueData only emits bars where actual trades occur.
> Use FINNIFTY at 5min or 3min resolution. The 1min scanner returned 0 setups because the
> context window is too sparse to form reliable DOL/MSS/FVG patterns.

### Trade Log — 5min

| Date | Time | Dir | Result | P&L | Risk | Score | OI Δ% |
|------|------|-----|--------|-----|------|-------|-------|
| 2026-05-21 | 13:35 | BULL | SL | +2.50R | 11pts | 15 | +0.00% |

---

## Interpretation

> ⚠️ **Small sample. Do not trade based on these numbers.**
> These results are from 1 total setups.
> Minimum for statistical significance: ≥200 per timeframe.
> Purpose of this run: verify TrueData scanner compatibility, not strategy validation.