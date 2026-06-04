# /ftmo — FTMO Account Status & Analysis

Pull a full snapshot of the FTMO free trial account and deadline urgency.

## Steps to Execute

1. **Read** `data/ftmo_10k/state.json`

2. **Display structured status:**

```
FTMO Free Trial $10K — Status Report
──────────────────────────────────────
Capital        : $X,XXX.XX  (start: $10,000)
Total PnL      : $XXX.XX
Target         : $500 (5%)  →  Progress: $XXX / $500  (XX.X%)
Still needed   : $XXX.XX
Deadline       : ~June 6, 2026

Daily PnL      : $XX.XX  (limit: -$300)
Best day PnL   : $XX.XX  (cap: $250)

Closed trades  : X total | X wins | X losses | WR: XX%
Open positions : X

Mode           : free_trial
Risk/trade     : 0.7% = $70
```

3. **Deadline urgency calculation:**
   - Today = 2026-05-27, deadline ~June 6 = 8 trading days
   - At $70/trade and current WR, estimate days needed to hit $500 target
   - Show daily run-rate needed: `$remaining ÷ days_left = $/day needed`

4. **Risk alerts:**
   - If daily_pnl < -$200 → warn approaching -$300 limit
   - If best_day_pnl ≥ $200 → warn approaching best-day cap ($250 → blocked for day)
   - If total loss > -$800 → CRITICAL: approaching $1,000 max drawdown

5. **XAUUSD reminder:** Confirm XAUUSD is NOT in active symbols (paused after May 22 disaster).

6. **Read** `forex_engine/prop_firms/ftmo/ftmo_config.py` to confirm risk_per_trade_pct = 0.7 and active symbols list.
