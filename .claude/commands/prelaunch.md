# /prelaunch — Pre-Launch Safety Checklist

Run a full safety audit before starting either bot engine. Verify all critical settings are correct.

## Steps to Execute

1. **Read state files** (never launch with corrupted state):
   - `data/ftmo_10k/state.json` — check capital, mode, daily_pnl, best_day_pnl
   - `data/gft_5k/state.json` — check capital, phase_1_passed, trading_days_active

2. **Verify FTMO config** (`forex_engine/prop_firms/ftmo/ftmo_config.py`):
   - risk_per_trade_pct = 0.7 (sprint mode)
   - FTMO_ACTIVE_SYMBOLS = ['XAGUSD', 'USOIL', 'EURUSD']
   - XAUUSD must NOT be in active symbols

3. **Verify GFT config** (`forex_engine/prop_firms/gft/gft_config.py`):
   - risk_normal_pct = 0.25 | risk_reduced_pct = 0.12 | risk_max_pct = 0.30
   - enabled_symbols = ['XAGUSD', 'USOIL'] (XAUUSD in disabled_symbols)
   - kill_zone_windows_utc = [(7, 12), (16, 20)]

4. **Verify GFT poll speed** (`forex_engine/prop_firms/gft/gft_5k_2step.py`):
   - Search for `poll = 60 if self._paper else` → must be `15`, not 30 or 60

5. **Check .env** — TELEGRAM_BOT_TOKEN and FOREX_TELEGRAM_TOKEN must be set

6. **Check no paper_mode=True** in any live engine file

7. **Report PnL status:**
   - FTMO: current PnL vs $500 target, days remaining to ~June 6
   - GFT: current Phase 1 progress vs $400 target, trading days count

Report each step as ✅ PASS or ❌ FAIL with reason.
