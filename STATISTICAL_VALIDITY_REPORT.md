# STATISTICAL VALIDITY REPORT
> Generated: 2026-05-30 22:49:51
> Total signals observed: 35
> Data period: 9 trading days (trial limit)

---

## ⚠️ Critical Context

This backtest covers **9 trading days** of TrueData trial data.
The Silver Bullet scanner fires **1-9 setups per index/timeframe combination**.
These numbers are **not sufficient to draw strategy conclusions**.

The purpose of this validation is **data quality assessment**, not strategy validation.

---

## Sample Size Adequacy by Combination

| Combination | n | Win Rate | 95% CI | Adequacy |
|-------------|---|----------|--------|----------|
| BANKNIFTY_1min | 7 | 85.7% | 48.7–97.4% | **INSUFFICIENT** |
| BANKNIFTY_3min | 1 | 100.0% | N/A | **NO_DATA** |
| BANKNIFTY_5min | 1 | 100.0% | N/A | **NO_DATA** |
| FINNIFTY_1min | 0 | — | N/A | **NO_DATA** |
| FINNIFTY_3min | 0 | — | N/A | **NO_DATA** |
| FINNIFTY_5min | 1 | 100.0% | N/A | **NO_DATA** |
| MIDCPNIFTY_1min | 6 | 66.7% | 30.0–90.3% | **INSUFFICIENT** |
| MIDCPNIFTY_3min | 4 | 75.0% | N/A | **INSUFFICIENT** |
| MIDCPNIFTY_5min | 3 | 66.7% | N/A | **INSUFFICIENT** |
| NIFTY_1min | 7 | 100.0% | 64.6–100% | **INSUFFICIENT** |
| NIFTY_3min | 4 | 100.0% | N/A | **INSUFFICIENT** |
| NIFTY_5min | 1 | 100.0% | N/A | **NO_DATA** |


**Adequacy legend:**
- ADEQUATE: ≥30 trades (minimal for WR estimate)
- MARGINAL: 10–29 trades (directional indication only)
- INSUFFICIENT: 3–9 trades (noise range — any WR is meaningless)
- NO_DATA: 0–2 trades (nothing measurable)

---

## What Can Be Concluded

- TrueData connection reliability during data fetch
- Data format correctness (columns, types, timezone)
- OI availability on all bars (Fyers cannot provide this)
- Scanner import compatibility — existing code runs without modification
- Zero OHLC violations in fetched data
- Market-hours gap rate (plausible vs exchange reality)

---

## What Cannot Be Concluded

- Strategy win rate — 35 total setups is statistically insufficient (need ≥200)
- Whether current parameters are optimal
- Whether OI filters improve or degrade performance
- Long-term reliability — only 9 trading days observed
- Drawdown properties — max drawdown from <30 trades is noise
- FINNIFTY conclusions — 2 trading days (Wednesdays only)

---

## Minimum Requirements for Valid Conclusions

- **for_win_rate_significance:** ≥200 trades per combination (≈3 months paid data)
- **for_oi_filter_impact:** A/B test: ≥50 trades with and ≥50 without OI filter
- **for_drawdown_analysis:** ≥100 consecutive trades on same TF/index

---

## Statistical Note on Win Rates

With 1-9 setups per combination, any observed win rate is within the binomial noise band. E.g. 3/3 wins = 100% WR, but 95% CI is [29%, 100%]. These numbers prove nothing about strategy edge.

**Example:**
- Observed: 3 wins from 3 trades → 100% WR → Wilson 95% CI: [29%, 100%]
- Observed: 2 wins from 3 trades → 67% WR → Wilson 95% CI: [9%, 99%]
- Both are statistically indistinguishable from a 50% coin flip.

---

## Path to Statistical Validity

| Step | Action | Timeline |
|------|--------|----------|
| 1 | Purchase TrueData standard plan | Before trial expiry 2026-06-09 |
| 2 | Re-run validation with 90-day data | Week after purchase |
| 3 | Run A/B test: OI filter on/off | Month 2 |
| 4 | Analyse by index × TF × regime | Month 3 |
| 5 | Statistical report with CIs | End of Month 3 |

Only after step 4 can strategy-level conclusions be drawn.

---

## Verdict on This Run

**Data quality:** HIGH CONFIDENCE — structural properties measurable with 1 day of data.
**Strategy performance:** NO CONFIDENCE — 29 total trades across 12 combinations.
**OI utility:** PROMISING but UNVERIFIED — data present, sample too small.
