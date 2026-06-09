# /trade-debrief — Post-Trade Learning Loop

Run after every closed trade (win or loss). Extracts what happened, why, and updates the pattern memory. This is CB6's learning loop — the bridge between trade outcome and future scanner behavior.

## Steps

### 1. Load the Closed Trade
Read the most recent closed trade from:
- GFT $5K: `data/gft_5k/state.json` → `closed_trades[-1]`
- GFT $1K: `data/gft_1k_instant/state.json` → `closed_trades[-1]`
- NSE: `data/trade_journal.csv` → last row

Extract: symbol, direction, entry, SL, targets_hit, pnl_usd, exit_reason, confluence score, mss_type, entry_time

### 2. Classify the Setup
Map the trade to one of the validated setup templates:
- `DOL_SWEEP_OB_BOS_FVG` — sweep → OB → BOS → FVG entry (primary template)
- `CHOCH_ONLY` — CHoCH without full chain
- `SWEEP_NO_FVG` — sweep + MSS but weak FVG (this is why we skip)
- `COUNTER_H4` — trade against H4 bias (requires 50% size rule)

### 3. Verdict
- WIN: What made it work? (FVG size, session, H4 alignment, confluence score)
- LOSS: What failed? (SL too tight? Wrong session? Weak FVG body%? Counter-H4?)
- Identify the single root cause — one sentence

### 4. Update Pattern Memory
Write to `ml_engine/memory/trade_pattern_db.sqlite`:
```
symbol | direction | session | h4_bias | setup_type | confluence | fvg_body_pct | outcome | pnl_r | timestamp
```
This builds the searchable trade database for future `/pattern-search` queries.

### 5. Score Drift Check
After every 5 trades on same symbol:
- Calculate live WR for that symbol vs backtest benchmark
- If live WR < backtest WR by >15%: flag to Telegram "XAGUSD live WR drifting — review"
- If live WR > backtest WR by >10%: note it as a positive signal

### 6. Scorer Weight Nudge
If we have ≥10 trades on this setup type:
- Calculate: does higher confluence score correlate with better outcomes?
- If yes: note in `memory/scorer_insights.md`
- If a specific filter (e.g. FVG body% > 50%) consistently predicts wins: flag for scanner update

### 7. Memory Update
Write a one-line entry to `C:/Users/Rahul/.claude/projects/c--cb6-bot/memory/` if something non-obvious was learned. Only write if genuinely surprising — not just "we lost."

## Self-Improvement Note
This skill itself improves when: the root cause identified in Step 3 consistently appears across multiple losing trades. Update the skip criteria in the scanner when you see ≥3 losses with the same root cause.
