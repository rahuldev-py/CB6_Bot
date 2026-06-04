# FTMO Best Day Protection — Hardening Report
**Date:** 2026-05-30 | **File:** `forex_engine/prop_firms/ftmo/ftmo_state.py`

---

## Background

FTMO's Best Day Rule: No single trading day may generate more than 50% of the
profit target ($500 × 50% = $250 max profit per day).

FTMO evaluates this on **account equity** — not just realized/closed trades.
An open position with +$280 unrealized profit already puts you past the $250 cap,
even if no trade has closed yet.

---

## Gap Before Fix

`can_open_trade()` only checked `best_day_pnl` (updated only on trade close):

```python
# BEFORE — only closed trade PnL checked
best_day_pnl = state.get('best_day_pnl', 0.0)
if best_day_pnl >= best_day_limit:
    return False, f"FTMO Best Day Rule hit (${best_day_pnl:.2f} of ${best_day_limit:.2f} max)"
```

### Attack scenario:
1. Trade A closes at +$200 (below $250 cap — OK)
2. Trade B is open with +$80 unrealized (today's equity = +$280 → OVER CAP)
3. `best_day_pnl` = $200 (only closed P&L) → cap check passes → Trade C allowed to open
4. FTMO sees equity = $280+ → Best Day Rule triggered → account flagged

---

## Fix Applied

`forex_engine/prop_firms/ftmo/ftmo_state.py` — `can_open_trade()`:

```python
profit_target  = starting * rules['profit_target_pct'] / 100
best_day_limit = profit_target * rules['best_day_rule_pct'] / 100

# Guard 1: closed P&L (updated on each trade close)
best_day_pnl = state.get('best_day_pnl', 0.0)
if best_day_pnl >= best_day_limit:
    return False, f"FTMO Best Day Rule hit (${best_day_pnl:.2f} of ${best_day_limit:.2f} max)"

# Guard 2: equity PnL — realized + floating (updated as price moves)
# FTMO evaluates best-day on account equity, not just closed trades.
daily_equity_pnl = state.get('daily_pnl', 0.0)
if daily_equity_pnl >= best_day_limit:
    return False, (
        f"FTMO Best Day Rule (equity) — today ${daily_equity_pnl:.2f} "
        f"≥ ${best_day_limit:.2f} cap (realized + floating)"
    )
```

---

## How `daily_pnl` vs `best_day_pnl` Differ

| Field | Updated when | Includes unrealized? |
|-------|-------------|----------------------|
| `best_day_pnl` | Each time `daily_pnl` exceeds the previous best (on close) | No — closed only |
| `daily_pnl` | Every trade open/partial/close + floating P&L tick | Yes — equity basis |

The second guard uses `daily_pnl`, which is the running equity PnL for today.

---

## Cap Calculation Verification

| FTMO Mode | Profit Target | best_day_rule_pct | Cap |
|-----------|--------------|-------------------|-----|
| Free Trial ($10K) | $500 (5%) | 50% | **$250** |
| Challenge ($10K)  | $1000 (10%) | 50% | **$500** |

`best_day_limit = profit_target * best_day_rule_pct / 100`
= $500 × 50 / 100 = **$250** ✅

---

## Daily Reset

Both `best_day_pnl` and `daily_pnl` are reset to 0.0 in `_reset_daily_if_needed()`:
```python
state['daily_pnl']    = 0.0
state['best_day_pnl'] = 0.0
```
Reset fires on first call each calendar day before any trade gate check.

---

## Status: ✅ HARDENED — both realized and floating P&L now guarded
