# /prelaunch — Pre-Launch Safety Checklist

Run a full safety audit before starting either bot engine. Verify all critical settings are correct.

## Steps to Execute

1. **Read state files** (never launch with corrupted state):
   - `data/ftmo_25k/state.json` — check capital, mode, open_trades (must be empty)
   - `data/gft_5k/state.json` — check capital, phase_1_passed, trading_days_active, open_trades (must be empty)
   - `data/gft_1k_instant/state.json` — check capital, open_trades (must be empty)

2. **Verify FTMO config** (`forex_engine/prop_firms/ftmo/ftmo_config.py`):
   - FTMO is DEPRIORITIZED — runs as-is, no tuning. Just confirm no XAUUSD in active symbols and no open positions stuck.

3. **Verify GFT $5K config** (`forex_engine/prop_firms/gft/gft_config.py`):
   - risk_normal_pct = 0.50 | risk_reduced_pct = 0.25 | risk_max_pct = 0.75
   - enabled_symbols = ['XAGUSD', 'USOIL'] (XAUUSD must be in disabled_symbols)
   - kill_zone_windows_utc = [(7, 12), (16, 20)]

4. **Verify GFT poll speed** (`forex_engine/prop_firms/gft/gft_5k_2step.py`):
   - Search for `poll = 60 if self._paper else` → must be `15`, not 30 or 60

5. **Check .env** — these keys must all be present (non-empty):
   - `TELEGRAM_BOT_TOKEN` — NSE bot
   - `FOREX_TELEGRAM_TOKEN` — Forex bot
   - `CLIENT_ID` — Fyers OAuth client ID (NSE bot)
   - `ACCESS_TOKEN` — Fyers daily token (refreshed via auto_token.py)
   - `MT5_LOGIN` — Forex MT5 login

6. **Check no paper_mode=True** in any live engine file

7. **Check for duplicate processes** — warn if `main.py` or `forex_main.py` already running:
   ```powershell
   Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, CPU, StartTime
   ```

8. **Report PnL status:**
   - FTMO: current PnL vs $500 target (DEPRIORITIZED — informational only)
   - GFT $5K: current Phase 1 progress vs $400 target, trading days (need 3 min)
   - GFT $1K Instant: current PnL vs $30 daily DD limit

Report each step as ✅ PASS or ❌ FAIL with reason.
