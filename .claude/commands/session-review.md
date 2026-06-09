# /session-review — End of Session Synthesis

Run at end of each trading session (after 15:30 IST for NSE, after 20:00 UTC for GFT NY close). Synthesizes the full session into a performance snapshot and learning extract.

## Steps

### 1. Session P&L Summary
For each active account, compute today's closed trade stats:
- Total trades | Wins | Losses | Win rate
- Total PnL ($) | Best trade | Worst trade
- Current risk mode (normal/reduced/halted)

### 2. Setup Quality Review
For each trade taken today:
- Was the setup A+ / A / B grade?
- Was H4 bias aligned?
- Was entry in correct session kill zone?
- FVG body% — was it above threshold?

Classify each as: SHOULD HAVE TAKEN | CORRECTLY TAKEN | CORRECTLY SKIPPED | INCORRECTLY SKIPPED

### 3. Missed Opportunity Scan
Grep today's logs for skipped setups:
```
grep "SB skip\|FOREX.*skip" logs/cb6_$(date +%Y%m%d).log
```
For each skipped setup: was the skip reason valid? Did price eventually move in that direction? If we missed a valid A+ trade, note why the scanner rejected it.

### 4. Regime Accuracy Check
Compare today's morning brief H4 bias vs actual price action:
- Brief said NIFTY BEARISH → did NIFTY fall? Yes/No
- Update `memory/daily_brief_accuracy.md` with today's entry

### 5. GFT Phase Progress
GFT $5K only:
- Phase 1: need +$535.63 more from today's baseline | trading days used: X/3 minimum
- At today's pace, estimated days to phase completion
- If daily P&L negative: how much of internal guard buffer remains ($170 hard stop)

### 6. Tomorrow's Watch List
Based on today's price action, list key levels for tomorrow:
- NIFTY: buy-side DOL at [X], sell-side DOL at [Y]
- XAGUSD + USOIL: current structure (what sweep is building)

### 7. Send to Telegram
Send the session summary to both bots. Format:
```
📊 SESSION REVIEW — [date]
NSE: [trades] trades | [WR]% WR | [PnL]
GFT: [trades] trades | [WR]% WR | [PnL]
Phase progress: $[remaining] to Phase 1 target
Tomorrow: [key levels to watch]
```
