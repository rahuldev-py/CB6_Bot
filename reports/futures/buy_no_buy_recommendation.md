# CB6 Futures Core — Buy / No-Buy Recommendation
**Generated:** 2026-05-31  
**Decision scope:** MFF Flex $25K Flex Plan — $57 evaluation fee

---

## Final Answer

```
BUY_MFF_NOW = FALSE
```

---

## Reasons

### Reason 1: Backtest engine has a confirmed lookahead bias

The entry fill is recorded on bar[i] using data derived from bar[i]'s own price action. In live trading, bar[i] must close before the signal is generated, and the limit order fills at bar[i+1] or later. This produces a systematic 1-bar execution advantage that inflates win rates and PnL across all symbols and years. The magnitude of inflation is estimated at 15–40% on win rate and 30–60% on PnL, based on audit finding F-1.

This is not a guess. The code at `futures_backtest_engine.py` lines 168–221 confirms it.

The published win rates (74–87% for MES/MGC) are not live-trading estimates. They are optimistic artifacts. Real 1m execution win rates for this class of strategy typically run 45–60%.

### Reason 2: All data is 1h bars from proxy tickers, not 1m micro futures

Every backtest used ES=F (full E-mini) at 1-hour bars, not MES at 1-minute bars. The ICT Silver Bullet is a 1-minute strategy. On 1h bars, each silver bullet session contains only 1–3 bars, which is insufficient for reliable swing detection (the algorithm requires minimum 6 bars). The strategy fires on whatever structure it can detect, which is a loose approximation of the real signal.

No 1m data exists in the system. No 1m backtest has been run. The published metrics have never been tested on the intended execution timeframe.

### Reason 3: The MFF simulation has a known bug that makes all profitable years pass

The drawdown check in `_mff_simulation()` passes `peak_equity = starting_equity + total_pnl` and `current_equity = starting_equity + total_pnl` to the rule engine. This makes drawdown = 0, so the $1,000 MFF drawdown limit is never triggered.

The correct drawdown check, using actual equity-curve max drawdown from the reports, shows:

| Symbol/Year | Actual equity-curve DD | Would MFF eval pass? |
|---|---|---|
| MES 2024 | $168 | Yes |
| MES 2025 | $864 | Yes (within $1,000) |
| MES 2026 | $1,348 | **No — breaches $1,000** |
| MNQ 2025 | $1,251 | **No** |
| MGC 2025 | $1,227 | **No** |
| MGC 2026 | $3,471 | **No** |

Only MES 2024 and MES 2025 pass when the correct drawdown is applied. That is 2 out of 12 tested symbol-year combinations. The gate requires 2 of 3 years per symbol. MES passes 2/3 years on the drawdown criterion — but MES 2024 is only half a year of data, and MES 2025 is on inflated 1h-bar numbers.

### Reason 4: MCL fails comprehensively and was in the Phase 1 symbol list

MCL shows PF 1.28–1.66 and win rate 29–38% consistently across all three years and all four kill zones. This confirms the strategy has no edge on crude oil. Running MCL on a live MFF eval would consume drawdown budget without contributing to the profit target. MCL should be removed from Phase 1 before any live trading begins.

### Reason 5: The $57 is not the risk — the 30-day eval window is

The MFF free trial has a 30-day time limit. If the strategy enters live evaluation before these biases are corrected, the risk is not the $57 fee — it is 30 days of live trading on unvalidated parameters. The strategy could behave acceptably on 1m data, or it could show a real win rate of 45% with PF 1.8 and fail to reach the $1,500 target within 30 days. Without 1m data validation, there is no basis to estimate the probability of passing.

---

## What Would Change the Answer to TRUE

The answer changes to `BUY_MFF_NOW = TRUE` when all three conditions below are met:

### Condition 1: Fix the backtest engine's execution order

In `futures_backtest_engine.py`, shift trade entry to bar[i+1]:

```python
# Current (biased):
setups = signal_fn(window_m1, window_h4)
_open_trade(best, bar[i], ...)   # fills on bar[i] using bar[i]'s data

# Corrected:
pending_setup = best  # hold from bar[i]
# On bar[i+1]:
if pending_setup and bar[i+1].low <= pending_setup.entry:  # price returned to FVG
    _open_trade(pending_setup, bar[i+1], ...)
```

This also requires implementing FVG fill validation — the bar must actually trade to the entry price.

### Condition 2: Get and test 1m data for MES

Minimum: 12 months of MES 1m continuous contract data (Panama-adjusted).  
Source: Kinetick via NinjaTrader free account, or Rithmic data subscription.  
After import, re-run: `python -m futures_engine.research.futures_research_runner --year 2025`

### Condition 3: Gate passes on corrected code and 1m data

After conditions 1 and 2:
```
python -m futures_engine.research.futures_research_gate --require-1m
```

The gate must return `PASS` (not CONDITIONAL) for MES with:
- Trades ≥ 100 per year
- PF ≥ 1.5
- Max DD ≤ $700
- Expectancy > 0
- MFF simulation passes 2 of 3 years (with corrected drawdown logic)

If MES passes the corrected gate on 1m data, the $57 is justified.

---

## What the Evidence Does Support

Despite the `BUY_MFF_NOW = FALSE` verdict, the research is not negative. The data provides real signal:

1. **MCL has no edge** — confirmed reliably across three years. This is a valid finding that saves live capital.

2. **MES shows the most controlled drawdown** — the 2024 half-year DD of $168 is the best result in the entire dataset. Even after haircut for the 1h bias, MES is the right instrument to focus on.

3. **NY_AFTERNOON is consistently the best kill zone** for MES (75–83% WR across all years). If the 1m validation narrows signal count, restricting to NY_AFTERNOON first is the rational approach.

4. **The strategy architecture is complete and isolated** — no code needs to be rebuilt. The three fixes needed are targeted changes to the execution model and simulation, not redesigns.

5. **The 2026 drawdown increase is a data signal** — both MES and MGC show higher DDs in Jan–May 2026. This coincides with the market volatility period (tariffs, geopolitical uncertainty). The news guard and session filter in the infrastructure exist precisely to manage this. A properly integrated news blackout would have protected some of that drawdown.

---

## Recommended Sequence Before Reconsidering

```
Step 1: Fix backtest execution bias
        → futures_backtest_engine.py: shift fills to bar[i+1] with FVG fill validation

Step 2: Fix stop-loss fallback
        → futures_silver_bullet.py: reject setups with no sweep; don't use 3-tick default

Step 3: Fix MFF simulation drawdown check
        → futures_research_runner.py: pass report.max_drawdown into check_eval correctly

Step 4: Get MES 1m data
        → NinjaTrader Kinetick or Rithmic export, 2 years minimum

Step 5: Re-run gate in strict mode
        → python -m futures_engine.research.futures_research_gate --require-1m

Step 6: If gate returns PASS → BUY the $57 MFF Flex eval
        → Start with 1 MES micro, NY_AFTERNOON session only, max $200/day internal stop
```

---

## Decision Confidence

| Dimension | Confidence | Notes |
|---|---|---|
| MCL should not be traded | HIGH | Consistent failure across 3 years, 4 sessions |
| MES has a directional edge | MEDIUM | Signal exists; magnitude uncertain without 1m data |
| Current results can be trusted for live trading | LOW | Lookahead bias + 1h proxy data |
| $57 should be spent now | LOW | Premature — 2 code fixes + 1m data needed first |
| Strategy concept (ICT SB) is sound | MEDIUM | Established methodology; needs proper parameterisation |

**The correct holding position is: researching, not trading.**  
The infrastructure is built. The fixes are small. The data is obtainable.  
The $57 spend is 2–3 weeks away, not months.
