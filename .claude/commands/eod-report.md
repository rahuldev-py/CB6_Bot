# /eod-report — End-of-Day Report

Generate and send the full CB6 Quantum end-of-day report immediately.
Covers all active accounts, pattern DB quality, Hermes nudges, ML status,
and GFT Phase 1 progress. Saves a .txt file and sends it to both Telegram bots.

**Auto-triggers (no manual action needed):**
- 15:30 IST daily — after NSE market close (via `main.py` schedule_daily)
- 20:00 UTC daily — after GFT NY kill zone close (via `forex_engine/forex_main.py` EOD scheduler)

## Manual usage
```
/eod-report
/eod-report manual    # same as above
```

## Steps

### 1. Run the generator
```python
from utils.eod_report import generate_and_send
fpath = generate_and_send(trigger='MANUAL')
print(f"Report saved: {fpath}")
```

### 2. Confirm delivery
Verify both Telegram bots received the document:
- NSE bot (TELEGRAM_BOT_TOKEN) — index futures account summary
- Forex bot (FOREX_TELEGRAM_TOKEN) — GFT $5K + GFT $1K + FTMO summary

### 3. Review the report sections
The generated .txt contains:
- **NSE Fyers** — trades today, WR, realized PnL
- **GFT $5K** — Phase 1 progress bar, daily PnL vs $170 hard stop
- **GFT $1K Instant** — daily PnL vs $30 limit
- **FTMO $25K** — informational only, no effort
- **Pattern DB** — today's setup quality: WR, avg R, H4 alignment %, best pattern
- **Hermes Nudges** — pending parameter proposals (needs /parameter-optimizer to approve)
- **ML Shadow** — model accuracy per market, training timestamp
- **Tomorrow's Watch List** — key DOL levels to fill in via /market-analyst

### 4. If report fails
Check `logs/cb6_<date>.log` for the error. Common causes:
- State file corrupted — run /prelaunch to diagnose
- Telegram token missing — check .env for FOREX_TELEGRAM_TOKEN and CB6_ADMIN_USER_ID
- Pattern DB empty — no trades yet; DB initialises on first trade close

## Output location
`c:\cb6_bot\reports\eod_YYYYMMDD_<trigger>.txt`
