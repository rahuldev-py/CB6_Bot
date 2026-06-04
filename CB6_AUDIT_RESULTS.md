# CB6 Quantum — Architecture Audit Results

**Date:** 2026-05-29  
**Based on:** `CB6_ARCHITECTURE_AUDIT.md`  
**Audit tools:** ruff, vulture, radon, autoflake, pycycle, import-linter  

---

## Actual Measurements (vs. Audit Estimates)

| Metric | Audit Estimate | Actual |
|--------|---------------|--------|
| Python files | 319 | **308** |
| Total LOC | 71,768 | **58,925** |
| Files >1000 LOC | estimated | **6** |
| Files >2000 LOC | estimated | **1** |
| BOM-encoded files | not detected | **10** (critical) |
| Root-level stray scripts | unknown | **17** |

---

## Applied Fixes

### ✅ 1. BOM Encoding Fixed (10 files)

All 10 files were silently unreadable by every audit tool (radon, mypy, autoflake).
UTF-8 BOM stripped and missing `#` comment prefixes restored:

| File | Fix |
|------|-----|
| `generate_excel_dashboard.py` | BOM stripped, broken header → proper docstring |
| `backtest/backtester.py` | BOM stripped, `# ` prefix restored |
| `backtest/nifty_strategy_backtest.py` | BOM stripped |
| `broker/web_token.py` | BOM stripped |
| `core/market_brain.py` | BOM stripped, `# ` prefix restored |
| `data/bot_memory.py` | BOM stripped |
| `data/trade_lessons.py` | BOM stripped |
| `journal/trade_journal.py` | BOM stripped |
| `scanner/expiry_calendar.py` | BOM stripped |
| `trader/paper_trader.py` | BOM stripped, `# ` prefix restored |

### ✅ 2. Dead Code Removed (vulture 80% confidence)

| File | Change |
|------|--------|
| `main.py` | Removed unused imports: `TIMEFRAMES`, `can_enter_trade` (×3 sites), `get_all_data`, `_fmt_sb` |
| `forex_engine/forex_worker.py` | Removed unused import: `is_in_session` |
| `forex_engine/mt5/mt5_order_manager.py` | Removed unused import: `describe_retcode` |
| `main.py:1206` | Removed dead parameters `tf_filter`, `instrument_filter` from `run_nifty_scan()` |
| `dashboard.py:1025–1462` | **Removed 438 lines of unreachable code** after `return` in `_build_crypto_tab()` |

### ✅ 3. Stray Scripts Relocated (17 files → `scripts/`)

All `_` prefixed debug/backtest scripts moved out of root into `scripts/`:

```
scripts/_analyze_backtest.py      scripts/_bt_1y_nifty_bnf.py
scripts/_bt_forex_mt5.py          scripts/_bt_fyers_365d.py
scripts/_bt_historical_5y.py      scripts/_check_accounts.py
scripts/_check_gft.py             scripts/_combined_report.py
scripts/_forex_scan_debug.py      scripts/_forex_scan_debug2.py
scripts/_health_check.py          scripts/_merge_forex_ml.py
scripts/_ml_verify_changes.py     scripts/_mt5_check.py
scripts/_pre_launch_check.py      scripts/_session_report.py
scripts/_watch_nifty.py
```

### ✅ 4. Canonical Schemas Created — `core/schemas.py`

Single source of truth for all data models shared across engines:

- `Candle` — normalized OHLCV bar with source tag
- `Signal` — strategy output with signal_id, rr_to_t2, risk_pts properties
- `RiskDecision` — go/no-go decision with reason + metrics
- `ExecutionIntent` — the only object a broker adapter may receive
- `TradeState` — full lifecycle with enforced state machine transitions
- Enums: `Direction`, `Market`, `Engine`, `TradeStatus`

### ✅ 5. Execution Guard Created — `core/execution_guard.py`

Single mandatory pre-execution gate. All 9 checks run in order:

1. Kill-switch active
2. Symbol permanently blocked (e.g. XAUUSD on GFT)
3. Symbol not in allowed list
4. Daily loss at or beyond hard limit
5. Max open trades reached
6. Max daily trades reached
7. Signal score below minimum
8. RR below minimum
9. Direction/SL sanity (LONG: SL must be < entry; SHORT: SL must be > entry)

Returns `RiskDecision(allowed=True/False, reason=...)` — never raises, never silently passes.

### ✅ 6. Account Router Created — `core/account_router.py`

Single source for all account/symbol routing. Eliminates hardcoded values across engines:

- `get_account(Engine.FTMO)` → `AccountProfile` with capital, limits, magic number, terminal path
- `get_symbol_config(Engine.GFT, "XAGUSD")` → pip size, lot limits, spread limits
- `is_symbol_allowed(Engine.GFT, "XAUUSD")` → False (permanently blocked)
- GFT blocked symbols: `{"XAUUSD"}` — enforced in router, not scattered across config files
- FTMO blocked symbols: `{"XAUUSD"}` — paused since May 22 disaster, persisted here

### ✅ 7. Import Layer Rules Created — `.importlinter`

Six dependency contracts enforced by `import-linter`:

| Contract | Forbidden |
|----------|-----------|
| `scanner` → | `trader`, `forex_engine.prop_firms`, `crypto_engine.crypto_worker` |
| `communications` → | `mt5_connector`, `live_trader`, `binance_adapter` (direct broker) |
| `backtest` → | `live_trader`, `mt5_connector`, `binance_adapter` |
| `ml`, `ml_engine` → | `live_trader`, `order_manager`, `mt5_connector`, `binance_adapter` |
| `core` → | `forex_engine`, `crypto_engine`, `scanner`, `trader`, `backtest`, `ml` |

Run check: `python -m lint-imports`

### ✅ 8. Audit Runner Created — `tools/audit.py`

One command to re-run all checks at any time:

```powershell
python tools/audit.py              # full audit
python tools/audit.py --bom        # BOM files only
python tools/audit.py --dead       # dead code (vulture)
python tools/audit.py --unused     # unused imports (autoflake)
python tools/audit.py --loc        # LOC + large files
python tools/audit.py --circular   # dangerous cross-layer imports
python tools/audit.py --complexity # rank D/E/F functions (radon)
python tools/audit.py --unreferenced  # modules with no known importer
```

---

## Remaining Issues (Not Applied — Require Larger Refactor)

### Critical — Execution paths not yet gated through ExecutionGuard

The following call sites place orders without going through `core/execution_guard.py`.
The schemas and guard are now created; wiring them in is the next step.

| File | Line | Issue |
|------|------|-------|
| `trader/live_trader.py` | 127 | `fyers.place_order()` — no risk gate |
| `trader/order_manager.py` | 173 | `fyers.place_order()` — no risk gate |
| `watch_nifty_long.py` | 165 | `open_paper_trade()` — no `can_enter()` check |
| `scripts/_watch_nifty.py` | — | same pattern |

**NSE `order_manager.py`** does call `open_paper_trade()` which uses `paper_trader` state, but `core.risk.can_enter()` is never invoked before the call.

### High — Risk checks scattered across 8+ files

`MAX_DAILY_LOSS_PCT` / `daily_loss` logic lives in:
- `settings.py` (definition)
- `dashboard.py` (display)
- `forex_bot.py` (repeated 6× in different functions)
- `forex_engine/prop_firms/ftmo/ftmo_state.py` (prop-firm enforcement)
- `forex_engine/prop_firms/gft/gft_5k_2step.py` (prop-firm enforcement)
- `core/risk.py` (pure function, correct — but not used by all engines)
- `strategy.py` (duplicate definition)

**Fix path:** All engines should call `ExecutionGuard.check()` which reads from `AccountProfile.max_daily_loss_abs`.

### High — Large files still above 1000 LOC

| File | LOC | Priority action |
|------|-----|-----------------|
| `dashboard.py` | **2,816** (after dead code removal) | Split: state_reader, charts, commands, layout |
| `forex_engine/forex_worker.py` | **1,658** | Split: worker_loop, signal_handler, session_guard |
| `scanner/silver_bullet.py` | **1,473** | Split: context, setup, entry, validation, models |
| `main.py` | **1,442** | Split: entry point, scan loop, report builder |
| `utils/bot_listener.py` | **1,325** | Split by command group |
| `communications/forex_bot.py` | **1,233** | Split: status, commands, alerts, state_display |

### High — Complexity hotspots (Radon rank D/E/F)

| Function | File | Rank | CC |
|----------|------|------|----|
| `_build_crypto_tab` | `dashboard.py` | **F** | 71 (was dead code — now removed) |
| `generate_dashboard` | `dashboard.py` | **F** | 69 |
| `_nifty_live_scanner` | `main.py` | **E** | 39 |
| `_build_journal` | `dashboard.py` | **D** | 27 |
| `run_silver_bullet_scan` | `main.py` | **D** | 25 |
| `send_eod_report` | `main.py` | **D** | 24 |

Target: all functions ≤ rank C (CC ≤ 10) for production code.

### Medium — Duplicate TrueData provider

Two TrueData integrations now coexist:

| Path | Type |
|------|------|
| `provider/truedata/` | Full production-quality package with Pydantic models, auth, WS, REST, options |
| `data/truedata_feed.py` | Simpler wrapper created in yesterday's session |

`data/truedata_feed.py` should be deprecated and all callers migrated to `provider/truedata/`.

### Medium — 12 files have maintainability index C (radon mi)

Rated C (approaching unmaintainable): `dashboard.py`, `main.py`, `communications/forex_bot.py`,
`crypto_engine/crypto_worker.py`, `forex_engine/forex_worker.py`,
`forex_engine/prop_firms/ftmo/ftmo_state.py`, `forex_engine/prop_firms/gft/gft_5k_2step.py`,
`scanner/silver_bullet.py`

---

## Pre-Live Checklist Status (from Audit §18)

### Data
- [x] Fyers + TrueData + Yahoo feed chain in place
- [ ] Missing candle detection
- [ ] Duplicate candle detection
- [x] Timezone normalization (IST conversions in data_fetcher.py)
- [ ] Data feed heartbeat

### Strategy
- [x] Signal score logged
- [x] Setup reason logged
- [ ] Signal schema validated (schemas.py created — not yet wired)
- [ ] Backtest/live parity tested

### Risk
- [x] Daily loss limit enforced in FTMO + GFT engines
- [x] Prop-firm best-day cap enforced (ftmo_state.py)
- [x] Kill-switch exists (`EMERGENCY_STOP.flag`)
- [x] ExecutionGuard created — **not yet wired to all order paths**
- [ ] Session guard enforced in NSE engine

### Execution
- [x] Broker response logged
- [ ] Order idempotency key (ExecutionIntent.idempotency_key defined — not yet used)
- [ ] Fill reconciliation
- [ ] No direct order placement outside broker adapter (3 unprotected paths remain)

### Account Isolation
- [x] Magic numbers assigned per account (GFT=62001, FTMO=62000)
- [x] AccountRouter created — centralizes all routing
- [x] XAUUSD blocked in GFT permanently
- [x] XAUUSD paused in FTMO

### Observability
- [ ] Heartbeat
- [ ] Exception counter
- [x] Telegram alerts
- [x] Audit logs (paper_state.json)
- [ ] Daily health summary (scripts exist but not scheduled)

---

## Next 7 Days (Priority Order)

1. **Wire `ExecutionGuard`** into `trader/order_manager.py` and `trader/live_trader.py`
2. **Wire `ExecutionGuard`** into `forex_engine/forex_worker.py` for both FTMO and GFT
3. **Deprecate `data/truedata_feed.py`** — migrate to `provider/truedata/`
4. **Add heartbeat** file touch in main.py and forex_main.py main loops
5. **Split `generate_dashboard()`** — it scores CC=69, highest remaining complexity

## Toolchain (run anytime)

```powershell
python tools/audit.py              # full audit
python -m lint-imports             # dependency layer violations
python -m vulture . --min-confidence 80
python -m radon cc . --min D --show-complexity
```
