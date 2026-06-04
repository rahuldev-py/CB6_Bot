# /launch — Launch Sequence for Both Bots

Guided startup for NSE bot + Forex bot with safety verification.

## Steps to Execute

1. **Run /prelaunch first** (mental checklist — read `CLAUDE.md` section "Common Pitfalls")

2. **Verify .env exists and has required keys:**
   - Read `.env` (or check `os.environ`)
   - Required: `TELEGRAM_BOT_TOKEN`, `FOREX_TELEGRAM_TOKEN`, `FYERS_API_KEY` (NSE), `MT5_LOGIN` (Forex)
   - Do NOT print token values — just confirm each key is present (non-empty)

3. **Check for running processes:**
   ```powershell
   Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, CPU, StartTime
   ```
   If `main.py` or `forex_main.py` already running → warn before starting again

4. **State file final check:**
   - Read `data/ftmo_10k/state.json` → confirm `mode` = "free_trial", no open positions stuck
   - Read `data/gft_5k/state.json` → confirm no stuck open positions

5. **Launch instructions** (manual — do not run automatically):
   ```powershell
   # Terminal 1 — NSE Bot
   cd c:\cb6_bot
   python main.py

   # Terminal 2 — Forex Bot (FTMO + GFT)
   cd c:\cb6_bot
   python forex_main.py
   ```

6. **Post-launch verification (30 seconds after start):**
   - Check Telegram NSE bot for startup message
   - Check Telegram Forex bot for startup message
   - Both should show: engine initialized, connection confirmed, next kill zone time

7. **If either bot fails to start:**
   - Check Python traceback for import errors
   - Common fix: `pip install -r requirements.txt`
   - Check MT5 terminal is running and logged in (for Forex bot)
