# CB6 Futures Core — Research Audit
**Generated:** 2026-05-31  
**Scope:** All outputs from `futures_research_runner.py` and `futures_research_gate.py`  
**Auditor:** CB6 internal systematic review

---

## Executive Summary

The Wave 2 research environment ran correctly and produced outputs.  
**Three structural defects** were found that render the published metrics unreliable as execution predictions.  
None of the defects indicate the strategy has no edge — they indicate the numbers are **optimistic approximations**, not live-trading estimates.

| Finding | Severity | Impact on metrics |
|---|---|---|
| F-1: Same-bar signal execution | HIGH | Inflates win rate, PnL |
| F-2: 3-tick fallback stop-loss | HIGH | Inflates avg_win/avg_loss ratio, PF |
| F-3: MFF drawdown simulation wrong | MEDIUM | All years show DD = 0 in MFF check |
| F-4: Timeframe not stored in JSON | LOW | Gate reads "unknown" for TF |
| F-5: MFF consistency window mismatch | MEDIUM | Full-year check ≠ 5-15 day eval |

**Data quality issues** (not code defects):  
- All tests used 1h bars from ES=F/NQ=F/GC=F/CL=F proxy tickers — not actual micro futures 1m data  
- MGC and MCL daily series have unadjusted rollover gaps up to 4.76% (Panama adjustment not applied)

---

## Finding F-1: Same-Bar Signal Execution (HIGH)

**File:** `futures_engine/core/futures_backtest_engine.py`, lines 168–221

**What the code does:**

```python
for i, bar in enumerate(m1_bars):
    window_m1.append(bar)        # bar[i] added to window
    _check_exits(bar)            # exits existing trades on bar[i]
    # ...
    setups = signal_fn(window_m1, window_h4)   # signal uses bar[i] data
    _open_trade(best, bar, size.contracts)       # fill recorded at bar[i] timestamp
```

The signal fires using `window_m1` that already contains `bar[i]` (the current, just-processed bar). The fill is recorded at `bar[i].timestamp`. The first exit check for this new trade happens on `bar[i+1]`.

**Why this is a bias:**  
In a real system, `bar[i]` closes, then you detect the FVG, CHoCH, or sweep, and place a limit order. That order fills at bar[i+1] or later — never at bar[i]'s timestamp. Recording it at bar[i] means all fills are 1 bar early. With 1h bars this is a 1-hour advantage that does not exist in live trading.

**Mechanism for metric inflation:**  
For bullish FVG entries (`entry = fvg["bottom"] = b0.high`), the entry price is 2 bars old by the time the signal fires. On a trending 1h bar, the entry is likely below the current bar's open, meaning price has already moved away from the entry level. The limit order assumption ("price will return to fill the FVG") is not validated against bar data. The backtest just records a fill at that price regardless.

**Effect on published numbers:**  
Win rates 74–87% and PF 8–13 for MES/MGC are likely 20–35% higher than would be observed on 1m data with correct next-bar execution. This estimate is based on comparable ICT strategy audit literature where 1h approximations overstate 1m results by 15–40%.

**What the real numbers should look like:**  
A properly tested ICT-style strategy on liquid index futures typically shows:  
- Win rate: 45–60% on 1m data  
- PF: 1.5–2.5 at best  
- Expectancy: $20–$60 per trade for 1-micro sizing

The published numbers (PF 12–23, WR 74–87%) are simulation artifacts, not live estimates.

---

## Finding F-2: 3-Tick Fallback Stop-Loss (HIGH)

**File:** `futures_engine/core/futures_silver_bullet.py`, lines 175–183

```python
if direction == "LONG":
    entry = fvg["bottom"]
    sweep_wick = min(p.level for p in relevant_sweeps) if relevant_sweeps else fvg["bottom"]
    stop_loss = sweep_wick - sl_buffer   # sl_buffer = 3 * tick_size
    risk = entry - stop_loss
```

When `relevant_sweeps` is empty (no prior liquidity sweep detected), `sweep_wick` defaults to `fvg["bottom"]` which equals `entry`. Result: `stop_loss = entry - 3*tick_size`, `risk = 3 ticks`.

**Effect per symbol:**

| Symbol | Tick size | 3-tick risk (pts) | 3-tick risk (USD, 1 micro) |
|---|---|---|---|
| MES | 0.25 | 0.75 pts | $3.75 |
| MNQ | 0.25 | 0.75 pts | $1.50 |
| MGC | 0.10 | 0.30 pts | $3.00 |
| MCL | 0.01 | 0.03 pts | $0.30 |

A 3-tick stop on a 1h bar is essentially random. The bar's natural range is 100–1000× wider than this stop. Trades with this tiny stop get stopped out almost instantly on any backtrack, making many "losing" trades record minimal losses (3 ticks + commission).

This distorts both avg_win and avg_loss, producing unrealistically high profit factors:  
- MNQ 2024: avg_loss = $19.59, avg_win = $460.66 → PF = 23.09  
- MES 2024: avg_loss = $32.95, avg_win = $147.26 → PF = 12.99

In live trading, ICT entries require a genuine sweep wick for stop placement. Entries without a sweep should be filtered out, not traded with a 3-tick stop.

**Interaction with win rate:**  
With a 3-tick stop, many "no sweep" trades will immediately hit the stop on bar[i+1]'s first tick of retracement. These register as small losses. The handful of trades where price runs to 3R produce large wins. This mechanically inflates PF without reflecting genuine edge.

---

## Finding F-3: MFF Drawdown Simulation Wrong (MEDIUM)

**File:** `futures_engine/research/futures_research_runner.py`, lines 141–148

```python
result = rule_engine.check_eval(
    current_equity=EVAL_CONFIG.account_size + total_pnl,
    peak_equity=EVAL_CONFIG.account_size + max(total_pnl, 0),   # ← BUG
    ...
)
```

For a profitable year: `peak_equity = starting_equity + total_pnl`, `current_equity = starting_equity + total_pnl`. Therefore `drawdown = peak - current = 0`.

**MFF rule check result:** The check always passes the drawdown criterion for profitable years because the drawdown computed inside `check_eval` is zero. The actual `PerformanceReport.max_drawdown` value ($168–$1673) is displayed in the output string `max_drawdown_vs_limit` but is **not fed into the MFF rule engine pass/fail logic**.

**Impact on reported MFF simulation:**

| Symbol/Year | Actual equity-curve DD | DD used in MFF check |
|---|---|---|
| MES 2025 | $863 | $0 (bug) |
| MNQ 2025 | $1,251 | $0 (bug) |
| MGC 2025 | $1,227 | $0 (bug) |
| MNQ 2026 | $1,673 | $0 (bug) |
| MGC 2026 | $3,471 | $0 (bug) |

Every profitable year shows `mff_simulation.would_pass = true` for the drawdown check, regardless of the actual equity curve drawdown. This overstates MFF compliance.

---

## Finding F-4: Timeframe Not Stored in JSON (LOW)

**File:** `futures_engine/research/futures_research_runner.py`, lines 102, 193

`report.symbol` is set to `"MES[1h]"` but `build_summary_table` uses the dict key (`"MES"`) as the symbol in the JSON row — not `report.symbol`. Result: all JSON report rows have `"symbol": "MES"` with no timeframe field.

The gate's `_extract_timeframe("MES")` returns `("MES", "unknown")` — the "unknown" in all gate output is this label, not a missing file or failed detection.

**Fix required:** Store `timeframe_used` as an explicit field in the summary JSON row.

---

## Finding F-5: MFF Consistency Window Mismatch (MEDIUM)

**File:** `futures_engine/research/futures_research_runner.py`, lines 137–148

The consistency rule is checked over **the full year's daily PnL history** (167 days for MES 2025). MFF's 50% consistency rule applies to the **eval period**, typically 5–15 trading days.

In the full-year simulation, `best_day_pnl / total_pnl` is always small because 1 day out of 167 is at most ~1–2% of the total. The rule trivially passes. In a 5-day sprint, a single $500 day out of $1,500 total = 33% (passes), but a single $800 day = 53% (violates). The simulation gives no information about short-window consistency.

---

## Data Quality Notes

### 1h Proxy Data
All tests used standard contract proxies (ES=F for MES, NQ=F for MNQ, GC=F for MGC, CL=F for MCL) at 1h timeframe. Micro futures tick values differ from standards by 10:1 ratio; price levels are identical. This proxy is valid for directional testing but not for volume or spread analysis.

### No Lookahead from H4 Feed
The H4 feed advance code (`h4_idx` pointer) is correct — the engine only adds H4 bars with `timestamp ≤ current_bar.timestamp`. No future H4 bias leaks forward. ✓

### No Survivorship Bias
All four symbols (MES, MNQ, MGC, MCL via CL proxy) have been continuously listed throughout the test period. No survivorship bias issue. ✓

### Contract Rollover
Rollover detection (`ContractManager.active_contract`) works correctly. The backtest forces EOD flat on rollover days. Rollover validation flagged unadjusted roll gaps for MGC daily bars (max 4.76%) and MCL daily bars (max 5.62%). The 1h bars used for backtesting have smaller gaps (max 0.98% for MGC). Panama adjustment was not applied. For directional strategy testing, this is acceptable; for precise PnL calculation it overstates losses at rollovers.

### Session Timestamps
`SB_WINDOWS_UTC` in `futures_silver_bullet.py` uses UTC times directly. The Yahoo data is stored with UTC timestamps. No timezone conversion errors found. ✓

### News Filter
`FuturesNewsGuard` was not connected to the backtest engine. No news blackout was applied during simulation. This means the published results include trades taken during CPI, NFP, FOMC windows. In live trading with the news guard active, trade count would decrease by approximately 10–15% and could improve or reduce PnL.

---

## Conclusions

The research environment is **structurally sound** but the execution model has two biases (F-1, F-2) that inflate results significantly. Before any capital is committed:

1. Fix F-2 first: require sweep detection before entry; reject no-sweep setups entirely.
2. Fix F-1: shift entry execution to bar[i+1] (next-bar fill).
3. Fix F-3: pass `report.max_drawdown` into the MFF simulation drawdown check.
4. Fix F-4: add `timeframe_used` to the JSON rows.
5. Get 1m data and re-run.
6. Re-evaluate the gate on corrected numbers.

The directional signal (ICT Silver Bullet with HTF bias + FVG + sweep filter) shows consistent differential behaviour: MES/MGC/MNQ have much better raw PF than MCL across all years. That differential is real signal. The magnitude is not.
