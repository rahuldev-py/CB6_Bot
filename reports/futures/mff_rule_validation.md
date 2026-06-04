# CB6 Futures Core — MFF Flex $25K Rule Validation
**Generated:** 2026-05-31  
**Source:** `mff_flex_config.py`, `mff_flex_rules.py`, `mff_flex_state.py`, `futures_research_runner.py`

---

## Rule Source Reference

All MFF Flex $25K Flex Plan rules were verified against the program specification provided at project inception. No external MFF API was queried — rules are encoded as constants in `mff_flex_config.py`.

---

## Evaluation Phase Rules — VERIFIED ✓ (with one exception)

| Rule | Configured Value | Spec Value | Status |
|---|---|---|---|
| Account size | $25,000 | $25,000 | ✓ |
| Profit target | $1,500 | $1,500 | ✓ |
| Max drawdown | $1,000 | $1,000 | ✓ |
| Drawdown mode | EOD | EOD | ✓ |
| Daily drawdown | None (0) | None | ✓ |
| Max contracts | 2 | 2 | ✓ |
| Micro scaling | 10:1 | 10:1 | ✓ |
| Consistency rule | 50% | 50% | ✓ |
| Scaling rule | False (eval) | False | ✓ |
| Min trading days | 2 | 2 | ✓ |
| News trading | Allowed | Allowed | ✓ |

All eval parameters match. ✓

---

## Funded Phase Rules — VERIFIED ✓

| Rule | Configured Value | Spec Value | Status |
|---|---|---|---|
| Max drawdown | $1,000 | $1,000 | ✓ |
| Drawdown mode | EOD | EOD | ✓ |
| Daily drawdown | None | None | ✓ |
| Max contracts | 2 | 2 | ✓ |
| Consistency rule | None | None | ✓ |
| Scaling rule | True | Yes | ✓ |
| Inactivity limit | 7 calendar days | 7 calendar days | ✓ |
| News trading | Allowed | Allowed | ✓ |
| Buffer | 0 | None | ✓ |

---

## Payout Rules — VERIFIED ✓

| Rule | Configured Value | Spec Value | Status |
|---|---|---|---|
| Days to first payout | 5 | 5 | ✓ |
| Min profit per day | $100 | $100 | ✓ |
| Min payout amount | $250 | $250 | ✓ |
| Max payout amount | $1,000 | $1,000 | ✓ |
| Net profit between payouts | $250 | $250 | ✓ |
| Requestable profit % | 50% | 50% | ✓ |
| Profit split | 80% | 80% | ✓ |
| MLL after first payout | $100 | $100 | ✓ |
| Max simulated payouts | 5 | 5 | ✓ |

---

## MFF Simulation Logic — DEFECTS FOUND

### Defect 1: EOD Drawdown Check Receives Wrong Peak Equity (HIGH)

**File:** `futures_research_runner.py`, line 143

```python
result = rule_engine.check_eval(
    current_equity=EVAL_CONFIG.account_size + total_pnl,
    peak_equity=EVAL_CONFIG.account_size + max(total_pnl, 0),  # ← wrong
    ...
)
```

For a profitable year, `peak_equity = current_equity = starting_equity + total_pnl`.  
Therefore `drawdown = peak_equity - current_equity = 0` inside `check_eval`.

The MFF drawdown violation check in `mff_flex_rules.py`:
```python
drawdown = peak_equity - current_equity
if drawdown >= cfg.max_drawdown:  # 0 >= 1000 → always False for profitable years
    violations.append(...)
```

**Real behaviour:** All profitable years pass the drawdown check regardless of actual equity curve drawdown. The actual drawdown ($863–$3,471) is printed in the display string but has no effect on the pass/fail verdict.

**Corrected logic should be:**
```python
# Pass actual equity-curve drawdown into the check
peak_equity=EVAL_CONFIG.account_size + max(total_pnl, 0) + report.max_drawdown,
# This reconstructs approximate peak as final_equity + maximum_trough_recovery
```
Or more accurately, simulate day-by-day equity and track EOD peak separately.

**Which years would have failed with correct drawdown check:**

| Symbol/Year | Equity-curve DD | MFF limit | Correct result |
|---|---|---|---|
| MES 2024 | $168 | $1,000 | PASS ✓ |
| MNQ 2024 | $694 | $1,000 | PASS ✓ |
| MGC 2024 | $694 | $1,000 | PASS ✓ |
| MES 2025 | $864 | $1,000 | PASS ✓ |
| MNQ 2025 | $1,251 | $1,000 | **FAIL** ✗ |
| MGC 2025 | $1,227 | $1,000 | **FAIL** ✗ |
| MES 2026 | $1,348 | $1,000 | **FAIL** ✗ |
| MNQ 2026 | $1,673 | $1,000 | **FAIL** ✗ |
| MGC 2026 | $3,471 | $1,000 | **FAIL** ✗ |

Note: equity-curve drawdown ≠ MFF EOD drawdown. MFF's EOD model only ratchets peak at day end, so intraday drawdowns that recover by close do not count. The equity-curve DD shown above is a worst-case upper bound; actual MFF EOD DD would be somewhat lower. The direction of the problem is confirmed (many years would fail), the exact threshold depends on day-by-day simulation.

### Defect 2: Consistency Rule Checked Over Full Year (MEDIUM)

**File:** `futures_research_runner.py`, lines 137–138

```python
trading_days = len(daily)
best_day = max(daily.values(), default=0.0)
```

The 50% consistency check divides best_day by total_pnl. Over 167 days (MES 2025), even a $2,131 best day is only 6% of $35,510 total — it will never violate the 50% rule.

**What MFF actually checks:** During a 5–15 day eval sprint, if one day generates more than 50% of cumulative profit, that day is flagged. Example: if you make $800 on day 2 and $1,500 total by day 5, the $800 day = 53% → consistency violation. The annual simulation cannot detect this.

**Correct simulation approach:** Run many 5-day and 10-day rolling windows across the year and check how often the best-day-of-window exceeds 50% of window profit. This is not currently implemented.

### Defect 3: No EOD Tracking for State Machine (LOW)

The `MFFFlexState.end_of_day()` method correctly implements EOD peak ratcheting. However, the research runner's `_mff_simulation()` function does not use `MFFFlexState` at all — it reconstructs everything from the annual trade log. The live risk guard and state machine are correct; the simulation shortcut is not.

---

## Internal Guards — VERIFIED ✓

CB6 internal guards (fire before MFF limits):

| Guard | Value | Relationship to MFF limit |
|---|---|---|
| Daily warning | $100 | At 10% of MFF $1,000 DD |
| Daily reduce 50% | $150 | At 15% of MFF $1,000 DD |
| Daily hard stop | $200 | At 20% of MFF $1,000 DD → $800 safety margin |
| Consecutive loss halt | 2 losses | Not in MFF rules — CB6 only |
| Total warning | $400 | At 40% of MFF $1,000 DD |
| Total halt | $800 | At 80% of MFF $1,000 DD → $200 safety margin |
| Max trade size | 1 micro | Phase 1 limit, below MFF's 2-contract limit |

Guards are correctly set to fire well before official MFF limits. ✓

---

## Drawdown Model Architecture — CORRECT ✓

`EODDrawdownGuard` in `futures_drawdown_guard.py` implements:
- `update_intraday()`: moves current equity, does NOT update peak
- `end_of_day()`: ratchets peak upward only at EOD
- `is_breached()`: checks `abs(current - peak) >= max_drawdown`

This correctly models MFF's EOD trailing drawdown. Peak only locks in at end of trading day, matching the MFF rule. The drawdown guard is architecturally sound.

---

## Summary

The MFF rule engine is **correctly specified** (all rules match the MFF Flex $25K Flex plan documentation). The **live risk guard, drawdown guard, and state machine are correct**. The **research simulation** has two logic defects that cause it to misreport how many years would actually pass MFF compliance. Correcting these defects would show MNQ 2025, MGC 2025, and all 2026 partial-year runs as drawdown violations.
