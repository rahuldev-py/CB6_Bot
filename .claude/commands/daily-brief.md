# /daily-brief — Morning Market Briefing

Runs every morning before trading. Synthesizes account health, market regime, and today's bias into a single actionable brief. Sends summary to Telegram.

## Steps

### 1. Account Health (read state files)
- `data/gft_5k/state.json` — capital, daily_pnl, phase progress, risk_mode
- `data/gft_1k_instant/state.json` — capital, daily_pnl
- `data/ftmo_25k/state.json` — capital (informational only, deprioritized)
- Calculate: GFT $5K phase target remaining, trading days used vs minimum 3

### 2. Risk Mode Check
Evaluate internal guards for each account before any trade today:
- GFT $5K: warn if total_pnl < -$250 | reduce if < -$350 | halt if < -$430
- GFT $1K: halt if daily_pnl < -$25
- If either account is in reduced/halt mode — say so clearly at the top

### 3. H4 Bias Assessment (NSE)
Read recent NIFTY/BANKNIFTY 4H data via Fyers. Determine:
- BULLISH / BEARISH / RANGING for each index
- Key levels: recent swing high (buy-side DOL), recent swing low (sell-side DOL)
- Output: "Today bias NIFTY = BEARISH. Watch sell-side sweep of [level] for SHORT entries"

### 4. Forex Session Check (GFT)
- Current UTC time vs kill zones: London 07-12 UTC | NY 16-20 UTC
- Which session is open / next session starts in X hours
- XAGUSD + USOIL current structure (from last scanner log entries)

### 5. News Blackout Check
- Any high-impact news today (CPI/NFP/FOMC)? If yes — exact blackout windows
- Source: `data/news_cache.json` or last news monitor log

### 6. Pattern Memory Query
Search `ml_engine/memory/trade_pattern_db.sqlite` for:
- Similar market conditions in past (same H4 bias + same session)
- Win rate for those conditions: "In 12 past similar sessions: 8W/4L = 67% WR"

### 7. Today's Game Plan
Output a 3-line action plan:
```
NIFTY: [BULLISH/BEARISH/RANGING] — watch [key level] for [LONG/SHORT] sweep entry
GFT: [session status] — [setup to watch if any]
Risk: Normal / Reduced / HALT [reason if not normal]
```

### 8. Send to Telegram
Send the brief to both NSE and GFT Telegram bots using existing bot infrastructure.

## Self-Improvement Note
After each session: if today's brief was accurate (trade triggered = correctly predicted direction), note it. If wrong — update the H4 bias criteria in this skill. Track accuracy in `memory/daily_brief_accuracy.md`.
