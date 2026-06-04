# /gft — GFT Account Status & Analysis

Pull a full snapshot of the GFT $5K 2-Step GOAT account.

## Steps to Execute

1. **Read** `data/gft_5k/state.json`

2. **Display structured status:**

```
GFT $5K 2-Step GOAT — Status Report
──────────────────────────────────────
Capital        : $X,XXX.XX  (start: $5,000)
Total PnL      : $XXX.XX
Phase 1 target : $400  →  Progress: $XX.XX / $400  (X.X%)
Phase 1 passed : Yes/No
Phase 2 passed : Yes/No
Trading days   : X / 3 minimum
Daily PnL      : $XX.XX  (limit: -$200)

Closed trades  : X total | X wins | X losses | WR: XX%
Open positions : X

Risk mode      : normal (0.25% / $12.50) | reduced (0.12% / $6) | A+ (0.30% / $15)
```

3. **Phase countdown:**
   - If Phase 1 not passed: show $ remaining to $400 target + trading days needed
   - If Phase 1 passed, Phase 2 not passed: show $ remaining to $300 target
   - If both passed: congratulate + remind to check GFT dashboard for funded account

4. **Risk check:**
   - If daily_pnl < -$100 → warn approaching internal warning level (-$100)
   - If daily_pnl < -$140 → warn risk cut mode active (-$140)
   - If daily_pnl < -$170 → ALERT hard stop threshold approaching (-$170, limit -$200)

5. **Read** `forex_engine/prop_firms/gft/gft_config.py` to confirm current config matches expected values (symbols, kill zones, risk pct).
