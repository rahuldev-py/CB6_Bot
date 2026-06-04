# CB6 Futures Core — MFF Flex Readiness Audit
**Date:** 2026-06-01  
**Scope:** Verify all MFF Flex $25K parameters (Eval + Funded + Payout) match the implementation  

---

## MFF Flex $25K — Evaluation Phase

### Target Parameters
| Rule | MFF Spec | Code (`mff_flex_config.py`) | Match |
|------|---------|----------------------------|-------|
| Account size | $25,000 | `account_size = 25_000.0` | ✅ |
| Profit target | $1,500 (6%) | `profit_target = 1_500.0` | ✅ |
| Max drawdown | $1,000 (4%) EOD | `max_drawdown = 1_000.0`, `drawdown_mode = "EOD"` | ✅ |
| Daily drawdown | None | `daily_drawdown = 0.0` | ✅ |
| Consistency rule | No day > 50% of total profit | `consistency_rule_pct = 0.50` | ✅ |
| Min trading days | 2 | `min_trading_days = 2` | ✅ |
| Max contracts | 2 standard / 20 micro | `max_contracts = 2`, `micro_scaling_ratio = 10` | ✅ |
| News trading | Allowed | `news_trading_allowed = True` | ✅ |
| Scaling during eval | Not permitted | `scaling_rule = False` | ✅ |

**All eval parameters correctly coded. PASS ✅**

---

### Drawdown Model Verification

MFF Flex uses **EOD trailing drawdown**: the drawdown high-water mark ratchets up only at the close of each trading day (not intraday). This is enforced in two places:

**1. `_compute_eod_drawdown` in `futures_research_runner.py`:**
```python
equity = starting_equity
peak = starting_equity
for date_str in sorted(daily.keys()):
    equity += daily[date_str]
    if equity > peak:
        peak = equity    # EOD only
    dd = peak - equity
    if dd > max_dd: max_dd = dd
```
Correctly simulates EOD trailing. Starting equity $25,000 matches `EVAL_CONFIG.account_size`. ✅

**2. `MFFFlexRuleEngine.check_eval` in `mff_flex_rules.py`:**
```python
drawdown = peak_equity - current_equity
if drawdown >= cfg.max_drawdown:
    violations.append(...)
```
The `peak_equity` passed in is the EOD-ratcheted peak from step 1. ✅

### Consistency Rule Verification
```python
if total_pnl > 0 and best_day_pnl > 0:
    best_day_share = best_day_pnl / total_pnl
    if best_day_share > cfg.consistency_rule_pct:  # 0.50
        violations.append(RuleViolation(rule="CONSISTENCY", ...))
```
Guard condition `total_pnl > 0 and best_day_pnl > 0` is correct — consistency rule only applies when there is net profit to compare against. ✅

---

## MFF Flex $25K — Funded Phase

| Rule | MFF Spec | Code | Match |
|------|---------|------|-------|
| Max drawdown | $1,000 EOD | `max_drawdown = 1_000.0`, `drawdown_mode = "EOD"` | ✅ |
| Daily drawdown | None | `daily_drawdown = 0.0` | ✅ |
| Max contracts | 2 | `max_contracts = 2` | ✅ |
| Consistency rule | None in funded | `consistency_rule = False` | ✅ |
| Scaling | Permitted | `scaling_rule = True` | ✅ |
| Inactivity | 7 days | `inactivity_days = 7` | ✅ |
| News trading | Allowed | `news_trading_allowed = True` | ✅ |

**All funded parameters correctly coded. PASS ✅**

---

## MFF Flex $25K — Payout Rules

| Rule | MFF Spec | Code | Match |
|------|---------|------|-------|
| Min trading days to first payout | 5 | `days_to_first_payout = 5` | ✅ |
| Min payout amount | $250 | `min_payout_amount = 250.0` | ✅ |
| Max payout amount | $1,000 | `max_payout_amount = 1_000.0` | ✅ |
| Net profit between payouts | $250 | `net_profit_between_payouts = 250.0` | ✅ |
| Requestable profit % | 50% | `requestable_profit_pct = 0.50` | ✅ |
| Trader profit split | 80% | `profit_split = 0.80` | ✅ |
| Max simulated payouts | 5 | `max_simulated_payouts = 5` | ✅ |

**Note on `min_profit_per_day = $100.0`:** The payout config requires `≥$100 profit on every active trading day`. This rule's application in the backtest is informational only (generates a warning, not a violation that blocks payout). The gate does not use payout eligibility to make the buy/no-buy decision — it uses only eval criteria. This parameter does not affect gate output.

**All payout parameters coded. PASS ✅**

---

## CB6 Internal Guards (on top of MFF limits)

```python
class MFFFlexInternalGuards:
    daily_warning_usd: float   = 100.0   # Warn
    daily_reduce_usd: float    = 150.0   # Halve lots
    daily_hard_stop_usd: float = 200.0   # No new trades

    total_warning_usd: float   = 400.0   # Warn
    total_reduce_usd: float    = 600.0   # Reduce
    total_halt_usd: float      = 800.0   # Halt (vs MFF $1000 limit)

    max_consecutive_losses: int = 2
    default_risk_pct: float    = 0.005   # 0.5% per trade
    max_trade_contracts: int   = 1       # Start with 1 micro
```

These internal guards fire BEFORE MFF official limits:
- Daily hard stop at $200 vs MFF daily limit of N/A (no daily limit) → conservative
- Total halt at $800 vs MFF $1,000 max drawdown → $200 buffer
- Consecutive loss halt at 2 → prevents runaway losses

**These guards exist in config but are enforced only in live trading, not in the backtest.** The backtest uses `GUARDS_CONFIG.default_risk_pct` for position sizing but does not apply the daily/total halt triggers. This is correct — the backtest simulates the strategy's mathematical edge, not the live risk management overlays.

**Verdict: Guards correctly defined and appropriately scoped. PASS ✅**

---

## Permitted Symbols

### MFF Flex Permitted for CB6 Phase 1
```python
PHASE1_SYMBOLS = ["MES", "MNQ", "MGC", "MCL"]
```

| Symbol | MFF Permitted | In Registry |
|--------|--------------|-------------|
| MES (Micro E-mini S&P 500) | ✅ | `mff_permitted=True` |
| MGC (Micro Gold) | ✅ | `mff_permitted=True` |
| MNQ (Micro Nasdaq) | ✅ | `mff_permitted=True` |
| MCL (Micro Crude Oil) | ✅ | `mff_permitted=True` |

**Both target symbols (MES, MGC) are MFF permitted. PASS ✅**

---

## MFF Rule Engine Integration with Research Gate

The gate calls:
```python
result = rule_engine.check_eval(
    current_equity=final_eq,
    peak_equity=peak_eq,
    daily_pnl=0.0,
    total_pnl=total_pnl,
    trading_days=trading_days,
    best_day_pnl=best_day,
)
```

Then:
```python
would_pass = (
    total_pnl >= EVAL_CONFIG.profit_target and
    trading_days >= EVAL_CONFIG.min_trading_days and
    dd_within_limit and
    result.passed
)
```

The gate checks all four conditions independently. A year "would_pass" only if ALL four hold simultaneously. This is correct — all eval criteria must be satisfied at the same time.

**Verdict: MFF rule integration is correct. PASS ✅**

---

## Final MFF Readiness Verdict

| Component | Status |
|-----------|--------|
| Eval parameters | ✅ Correct |
| EOD drawdown model | ✅ Correct |
| Consistency rule | ✅ Correct |
| Funded parameters | ✅ Correct |
| Payout logic | ✅ Correct |
| Internal guards | ✅ Correctly scoped |
| Symbol permissions | ✅ MES and MGC both permitted |
| Gate-engine integration | ✅ Correct |

**The MFF Flex $25K rule implementation is accurate and complete. The system will correctly simulate whether a backtest year would pass the MFF Flex evaluation.**
