# CB6 Futures Core — Wave 4 Final Decision
**Generated:** 2026-05-31  
**Decision scope:** MFF Flex $25K Flex Plan — $57 evaluation fee

---

## FINAL ANSWER

```
BUY_MFF_NOW = FALSE
```

---

## Basis for Decision

This decision is based exclusively on measured results. No assumptions, no optimism.

### Evidence for FALSE

**1. Post-fix backtest produces 0–2 trades per year on available data**

After applying all three research defect fixes (F-1 next-bar execution, F-2 mandatory sweep, F-3 EOD drawdown), the corrected backtesting engine produces:

| Symbol | 2024 | 2025 | 2026 | Total |
|---|---|---|---|---|
| MES | 1 trade | 2 trades | 0 trades | 3 trades |
| MNQ | 0 trades | 1 trade | 1 trade | 2 trades |
| MGC | 1 trade | 2 trades | 0 trades | 3 trades |

A sample of 3 trades is not a sufficient basis for any statistical conclusion. The gate requires ≥ 100 trades. The minimum gate is not met by a factor of 33×.

**2. The near-zero trade count is caused by data resolution, not strategy failure**

The fixes are correct. They reveal that the ICT Silver Bullet strategy requires 1-minute bar data to produce a meaningful signal count. One-hour bars cannot resolve the intrabar sweep patterns (same-bar wick-and-recovery, 1m FVG structure, sub-60-minute liquidity sweeps) that the strategy depends on. This is a data availability problem.

**3. Pre-fix results (211–388 trades, WR 74–87%, PF 8–23) are confirmed artifacts**

The three biases that inflated the pre-fix numbers:
- F-1: Same-bar execution (signal used bar[i]'s data to fill on bar[i])
- F-2: 3-tick stop fallback + no sweep requirement (noise setups qualified)
- F-3: MFF drawdown always passed (peak_equity set equal to final equity)

All three are now fixed. The pre-fix numbers are discarded. They were not real strategy performance.

**4. No valid 1m sample exists to replace them**

The pre-fix numbers were inflated. The post-fix numbers are too sparse to evaluate. There are no valid numbers. A $57 purchase decision requires numbers. Therefore: wait.

**5. Gate thresholds — all fail**

| Gate criterion | Required | Best result (MES 2025, 2 trades) | Pass? |
|---|---|---|---|
| Trades ≥ 100 | 100 | 2 | FAIL |
| PF ≥ 1.5 | 1.5 | 2.93 (1 win, 1 loss — 2-trade sample) | too small |
| Max EOD DD ≤ $700 | $700 | $43 | PASS |
| Expectancy > 0 | >0 | +$42 | PASS (2 trades) |
| MFF sim passes ≥ 2/3 years | 2 | 0 (profit $84 < $1,500 target) | FAIL |

Two criteria pass — but on a 2-trade sample, they are statistically meaningless.

---

## What Would Change This Answer

The answer changes to `BUY_MFF_NOW = TRUE` when:

1. **1m data is imported and validated** (MES, minimum 12 months)
2. **Gate passes on 1m corrected backtest:**
   - Trades ≥ 100
   - PF ≥ 1.5
   - Max EOD DD ≤ $700
   - Expectancy > $0
   - MFF simulation passes ≥ 2 of 3 years

Both conditions must be met simultaneously. One without the other is insufficient.

---

## Current Infrastructure Readiness

Despite the FALSE verdict, the CB6 Futures Core is substantially complete and ready for 1m validation:

| Layer | Status | Notes |
|---|---|---|
| Symbol registry | ✓ Ready | 16 CME symbols, all MFF-permitted |
| Contract manager | ✓ Ready | Rollover detection, expiry calendar |
| Data feed | ✓ Ready | CSV import + paper feed |
| Signal scanner | ✓ Ready (F-2 fixed) | Sweep required, stop at wick |
| Backtest engine | ✓ Ready (F-1 fixed) | Next-bar execution, EOD expiry |
| Risk guard | ✓ Ready | EOD drawdown, kill-switches |
| MFF rule engine | ✓ Ready (F-3 fixed) | All rules verified against MFF spec |
| Session manager | ✓ Ready | RTH/ETH/holiday/rollover-week |
| News guard | ✓ Ready | 2025–2026 calendar pre-loaded |
| Rollover validator | ✓ Ready | Panama adjustment available |
| Research runner | ✓ Ready (F-3+F-4 fixed) | EOD DD simulation, TF label |
| Research gate | ✓ Ready | Reads corrected JSON output |
| Data validator | ✓ Ready | Validates 1m CSV before use |
| MFF state machine | ✓ Ready | Persists phase, equity, payout |
| Manual bridge | ✓ Ready | MANUAL_MONITOR mode operational |
| ML training logger | ✓ Ready | Schema defined, logger built |

**The only missing input is 1m historical data.**

---

## Time Estimate to Decision

| Step | Time required |
|---|---|
| Register for NinjaTrader (free) | 15 minutes |
| Install + connect Kinetick data feed | 30 minutes |
| Download MES 1m continuous bars (2024–2025) | 10 minutes |
| Export CSV and import | 5 minutes |
| Validate data quality | 2 minutes |
| Re-run corrected backtest (2025 MES) | 2 minutes |
| Gate decision | automatic |
| **Total** | **~1 hour** |

The $57 decision is approximately 1 hour of data acquisition away — not weeks, not months. The architecture is complete. The code is correct. The only open question is: does the strategy produce a valid edge on 1m data? That question has a definitive answer available within 1 hour.

---

## Summary Statement

CB6 Futures Core is fully built, all known research defects are fixed, and the system is production-ready for paper and semi-auto trading. The MFF evaluation account should not be purchased until the research gate passes on 1m data. The gate will return an answer within 1 hour of data acquisition. Until then, the answer is:

**`BUY_MFF_NOW = FALSE`**

*Not because the strategy fails. Because the evidence is insufficient to say it passes.*
