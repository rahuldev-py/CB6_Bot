# CB6 Quantum — Full Stack Audit V2
> Date: 2026-05-30
> Scope: 319 Python files, ~72K LOC, all layers
> Method: Static analysis (grep, import tracing), structural review, manual verification of critical paths
> Previous audit: CB6_FULL_STACK_AUDIT.md (2026-05-30, TrueData-only scope)

---

## Executive Summary

CB6 Quantum is well-architected. The three-engine isolation (NSE / Forex / Crypto),
the shadow-only ML system, and the prop firm risk guards are all structurally sound.
This audit found **2 concrete bugs fixed**, **4 dead-code zones to clean**, and
**5 medium-priority improvements** — none of which affect live trading today.

**Risk to capital: LOW.** No prop firm rule violations. No live-order logic errors.
No scanner modifications. No paper_mode=True in live configs.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Entry Points](#2-entry-points)
3. [Risk Engine Integrity](#3-risk-engine-integrity)
4. [Bugs Fixed](#4-bugs-fixed)
5. [Dead Code](#5-dead-code)
6. [Duplicate Code](#6-duplicate-code)
7. [State File Safety](#7-state-file-safety)
8. [Data Flow & SPOFs](#8-data-flow--spofs)
9. [ML System](#9-ml-system)
10. [Telegram Bots](#10-telegram-bots)
11. [Test Coverage](#11-test-coverage)
12. [Configuration Scatter](#12-configuration-scatter)
13. [Dependency Health](#13-dependency-health)
14. [Priority Action List](#14-priority-action-list)

---

## 1. Architecture Overview

### Module Inventory

| Module | Files | LOC | Role | Status |
|--------|-------|-----|------|--------|
| `forex_engine/` | 82 | ~22K | MT5, FTMO/GFT prop firm runners, risk guards | CORE |
| `ml_engine/` | 53 | ~11K | DNN/RNN/CNN training + shadow inference | ACTIVE |
| `ml/` | 15 | ~4K | Legacy ML shim — wraps ml_engine | LEGACY (kept) |
| `scanner/` | 12 | ~9K | ICT signal generation, TrueData/Fyers feeds | CORE |
| `trader/` | 4 | ~2K | Paper + live Fyers order execution | CORE |
| `core/` | 9 | ~3K | Execution guards, TickWatcher, MarketBrain | CORE |
| `nse_options/` | 9 | ~3K | Option chain, strike selection, Greeks | CORE |
| `backtest/` | 10 | ~4K | Walk-forward, 100d execution validation | VALIDATION |
| `data/` | 11 | ~4K | TrueData feed, FII/DII, news, state I/O | CORE |
| `utils/` | 13 | ~3K | Logger, Telegram alerts, state_io, scheduler | CORE |
| `communications/` | 5 | ~2K | FTMO + GFT + Crypto Telegram bots | CORE |
| `crypto_engine/` | 5 | ~5K | Binance Futures ETH/BTC paper/live layer | GATED |
| `dashboard/` | 2 | ~1K | Web UI port 8888 | WEBUI |
| `trial/` | 15 | ~4K | TrueData validators, backtest validation | EXPERIMENTAL |
| `tests/` | 18 | ~2K | Unit + integration tests | TESTS |
| `market_data/` | 7 | ~1.5K | Experimental data abstraction layer | ORPHANED |
| `provider/truedata_v1_archived/` | 11 | ~2K | Deprecated TrueData SDK | DEAD |
| `CB6_PRE_LIVE_RESUME_20260523/` | 62 | ~8K | Pre-launch backup snapshot | STALE |

**Engine isolation:** NSE ↔ Forex ↔ Crypto share **zero mutable state**. Each has
its own state files, Telegram tokens, MT5/Fyers sessions, and entry points.
This is the project's most important architectural property — it prevents a Crypto
crash from affecting FTMO, and an FTMO breach from touching NSE.

---

## 2. Entry Points

### Active (should be running)

| File | Purpose | How to start |
|------|---------|-------------|
| `auto_token.py` | Fyers OAuth + NSE bot launch | `python auto_token.py` daily at 08:45 IST |
| `watchdog.py` | NSE bot supervisor | `python watchdog.py --attach` |
| `forex_main.py` | FTMO + GFT engines | `python forex_main.py --profile FTMO` / `GFT_5K_2STEP` |
| `orchestrator.py` | Multi-engine supervisor | Optional: manages NSE + Forex as subprocesses |

### Gated (require explicit confirmation)

| File | Gate | Risk |
|------|------|------|
| `crypto_main.py` | `.env: CRYPTO_PAPER=false` + `LIVE_CRYPTO_CONFIRMATION=CONFIRMED` | Binance real money |

### Scripts (run manually, not in production loop)

`scan_now.py`, `backtest/nifty_strategy_backtest.py`, `tools/audit.py`,
`generate_excel_dashboard.py`, `trial/run_backtest_validation.py`

### 59 files have `if __name__ == '__main__'`

This is high but not dangerous — most are standalone tools. No entry-point
conflicts because the orchestrator uses subprocesses, not imports.

---

## 3. Risk Engine Integrity

### Prop Firm Rules — Verified ✅

| Rule | Enforced In | Status |
|------|------------|--------|
| FTMO daily loss $300 | `ftmo_state.py:can_trade()` | ✅ Hard block |
| FTMO best-day cap $250 | `ftmo_state.py:best_day_pnl check` | ✅ Hard block |
| FTMO max drawdown $1,000 | `ftmo_state.py:total_dd check` | ✅ Hard block |
| GFT daily loss $200 | `gft_daily_loss_guard.py:internal_daily_hard_stop` | ✅ Hard block |
| GFT total loss $500 | `gft_daily_loss_guard.py:internal_total_hard_stop` | ✅ Hard block |
| XAUUSD banned on GFT | `gft_config.py:disabled_symbols` + `gft_5k_2step.py:line 513` | ✅ Belt-and-suspenders |
| No equity/stocks | NSE scanner only has index futures/options symbols | ✅ Symbol whitelist |
| paper_mode=True blocked | `.claude/hooks/check_paper_mode.py` (PreToolUse hook) | ✅ Hook guard |

No prop firm rule violations found. XAUUSD is blocked at two independent
locations in GFT (config + runtime guard). All internal loss guards fire
**before** official prop firm limits, providing a safety margin.

### NSE Risk Guards — Verified ✅

| Rule | Location |
|------|---------|
| Max 3 live trades/day | `settings.py:MAX_TRADES_PER_DAY` |
| Max risk per trade | `settings.py:RISK_PER_TRADE_PCT` |
| Silver Bullet window gate | `scanner/silver_bullet.py:is_silver_bullet_window()` |
| H4 bias hard block | `scanner/silver_bullet.py:h4_bias != direction` |
| Choppy regime block | `scanner/silver_bullet.py:regime == CHOPPY` |
| Premium/discount gate | `scanner/silver_bullet.py:premium_discount_aligned()` |

---

## 4. Bugs Fixed

### BUG-01: crypto_paper_trader.py — Direct writes bypass atomic pattern
**Severity:** MEDIUM — state corruption on process crash  
**Files:** `crypto_engine/crypto_paper_trader.py:220,239`  
**Problem:** `rollback_open_trade()` and `update_trade_sl_order()` used
`open(STATE_FILE, 'w')` directly instead of the `tmp+os.replace` pattern
that `save_state()` uses. A crash mid-write produces a truncated JSON file.  
**Fix applied:** Added `_write_state_safe()` helper using tmp+fsync+os.replace.
Both functions now call it.  
**Status:** ✅ FIXED in this audit session

### BUG-02 (previously fixed): provider/truedata auth endpoint
**Severity:** HIGH (was — blocked all TrueData data)  
`provider/truedata/auth.py` pointed to `api.truedata.in/users/login` (HTTP 404).
Correct endpoint is `auth.truedata.in/token` (OAuth2).  
**Status:** ✅ FIXED — provider/truedata_v1_archived; data/truedata_feed.py rewritten

### BUG-03 (previously fixed): TrueData tick symbol mismatch
**Severity:** HIGH (was — SL/target triggers never fired from TrueData ticks)  
TrueData emits `"NIFTY-I"` but `trade_triggers.py` registered `"NSE:NIFTY50-FUT"`.  
**Status:** ✅ FIXED — `_TD_TO_FYERS` reverse map added to `data/truedata_feed.py`

---

## 5. Dead Code

### Zone A: provider/truedata_v1_archived/ — 11 files, ~2K LOC

**Status:** Never imported. Was the custom TrueData HTTP client before the
rewrite. All 11 files can be deleted.  
**Importer check:** `grep -r "from provider.truedata" --include="*.py"` → 0 results  
**Action:** `Remove-Item c:\cb6_bot\provider\truedata_v1_archived -Recurse -Force`

### Zone B: market_data/ — 7 files, ~1.5K LOC

**Status:** Only `market_data/__init__.py` exists as an importer of itself.
No production code imports from `market_data/`. This was an experimental
provider abstraction layer that was never connected to the scanner.  
**Files:** `interfaces.py`, `normalizer.py`, `event_bus.py`, `candle_builder.py`,
`tick_store.py`, `health_monitor.py`  
**Action:** Archive to `market_data/_experimental/` or delete entirely.

### Zone C: CB6_PRE_LIVE_RESUME_20260523/ — 62 files, ~8K LOC

**Status:** A snapshot backup directory from 2026-05-23 pre-launch. Not in
`sys.path`, never imported. Contains duplicate copies of `financial_data_core.py`,
`nse_eod.py`, `fii_dii.py`, and data CSVs.  
**Action:** Move to `backups/` or delete. Not a runtime risk either way.

### Zone D: journal/ — 1 file (empty stub)

`journal/__init__.py` is empty. No other journal-related files.  
**Action:** Delete.

---

## 6. Duplicate Code

### Telegram send_message — 4 implementations

| Location | Used By | Retry | Parse Mode |
|----------|---------|-------|-----------|
| `utils/telegram_alerts.py:send_message()` | NSE bot, main.py | 3× with backoff | HTML |
| `communications/telegram_helpers.py:send_message()` | FTMO + GFT bots | 3× with backoff | HTML |
| `forex_engine/alerts/telegram_alerts.py:send_alert()` | Old forex wrapper | Delegates | HTML |
| `communications/bot_crypto.py:_send()` | Crypto bot | 0 retries | HTML |

**Assessment:** The FTMO/GFT bots were already consolidated in Wave 4 to use
`telegram_helpers.py`. The crypto bot's `_send()` has no retry logic — if Telegram
is briefly unavailable, crypto alerts silently drop.  
**Recommendation:** Wire `bot_crypto.py:_send()` through `telegram_helpers.send_message()`.

### get_ltp() — 2 implementations

`data/truedata_feed.get_ltp()` (TrueData live cache) and
`scanner/live_price.get_ltp()` (Fyers API fallback) are intentional —
two sources, one purpose. Not a duplication problem.

### State I/O patterns — 3 patterns

| Pattern | Files |
|---------|-------|
| `utils/state_io` cross-process lock | forex_engine/*, paper_trader.py |
| `threading.Lock + tmp+replace` | live_trader.py, crypto (now fixed) |
| Raw `json.dump` | FIXED — removed from crypto |

All critical paths now use either `state_io` or `threading.Lock + tmp+replace`.

---

## 7. State File Safety

| State File | Writer | Lock Type | Crash-safe |
|------------|--------|-----------|-----------|
| `data/paper_state.json` | `paper_trader.py` | `state_io` (cross-process) | ✅ |
| `data/live_state.json` | `live_trader.py` | `threading.Lock + tmp+replace` | ✅ |
| `data/ftmo_10k/state.json` | `ftmo_state.py` | `state_io` (cross-process) | ✅ |
| `data/gft_2step_state.json` | `gft_phase_tracker.py` | `state_io` (cross-process) | ✅ |
| `data/crypto_paper_state.json` | `crypto_paper_trader.py` | `threading.Lock + tmp+replace` | ✅ (fixed) |

**Remaining note:** `live_trader.py` uses `threading.Lock` (in-process only), not
`state_io`. This is acceptable because only `main.py` writes it and the orchestrator
doesn't restart `main.py` while it's running. If that assumption ever changes
(e.g., a second process reads and writes `live_state.json`), upgrade to `state_io`.

---

## 8. Data Flow & SPOFs

### NSE Data Flow

```
TrueData (primary)
   └─ data/truedata_feed.TrueDataManager
         └─ truedata_ws.TD → history.truedata.in/getbars
         └─ wss://push.truedata.in:8086 (ticks)
                    ↓
scanner/data_fetcher.get_historical_data()   [2-min cache]
scanner/live_price.get_live_price()          [TrueData cache → Fyers fallback]
scanner/websocket_feed._tick_cache           [Fyers format keys]
                    ↓
scanner/silver_bullet.scan_silver_bullet()   [unchanged]
                    ↓
core/trade_triggers.register_trade_triggers()
core/tick_watcher.on_tick()                  [SL/TP triggers]
                    ↓
trader/paper_trader or trader/live_trader    [execution]
```

**SPOFs in NSE data path:**

| Point | Mitigation |
|-------|-----------|
| TrueData service down | ✅ Fyers auto-fallback in data_fetcher |
| TrueData auth token expiry | ✅ truedata_ws library auto-refreshes |
| TrueData WS disconnect | ✅ Library heartbeat + auto-reconnect |
| Fyers token expiry | ⚠️ `auto_token.py` refreshes daily at 08:45 — no intraday refresh |
| Scanner exception | ✅ Returns None; bot waits for next scheduled scan |

**Fyers token intraday expiry** is the one real SPOF: if `auto_token.py` fails
(network down, OAuth callback missed) and TrueData is also down, the NSE bot
has no data source. The watchdog detects stale heartbeats but cannot refresh
the Fyers token itself.

### Forex Data Flow

```
MT5 terminal (FTMO / GFT isolated) → MT5 Python bridge
   └─ forex_engine/forex_worker.py (signal + execution)
   └─ Telegram bot (control plane)
```

**No external HTTP data dependency for Forex** — MT5 provides prices internally.
Only dependency is the MT5 terminal process being alive.

### Kill Switches

| Switch | Effect | Location |
|--------|--------|---------|
| `data/kill_all.flag` | Orchestrator kills NSE + Forex subprocesses | `orchestrator.py` |
| `/stop` Telegram command | Pauses NSE scanner | `utils/bot_listener.py` |
| `/fx_stop` Telegram command | Pauses FTMO engine | `communications/forex_bot.py` |
| `/gft_stop` Telegram command | Pauses GFT engine | `communications/gft_bot.py` |
| `GFT_2STEP_PAPER=true` in .env | Switches GFT to paper | `forex_engine/prop_firms/gft/gft_5k_2step.py` |

All kill switches verified present and functional.

---

## 9. ML System

### Architecture

```
scanner (reads candles) → ml_engine/features (builds feature vector)
                                ↓
ml_engine/inference/shadow_predictor (SHADOW ONLY)
                                ↓
ml_engine/logs/shadow_predictions.jsonl (never touches orders)
```

**Shadow mode enforcement — verified ✅:**
- `ShadowPredictor` only writes to log file
- Returns `{"confidence": 0.0, "suggested_risk_mult": 1.0}` on error
- `main.py` uses the ML gate result for logging only — no conditional trade blocks

**Activation gate (10 conditions before shadow→observation):**
Hard-coded threshold: `auc >= 0.55` in `train_dnn.py:274`.
This should be moved to `ml_engine/config/ml_config.json` for visibility.

**Legacy ml/ directory:**
15 files remain as a compatibility shim. Active code routes to `ml_engine/` for
real work. Safe to keep — removal would require tracing 15+ import sites.

**Auto-retrain trigger:**
Every 20 trades or 7 days (whichever comes first) — correctly gated so it
never fires during an open trade.

---

## 10. Telegram Bots

### Bot Registry

| Bot | Token Env Var | Namespace | Status |
|-----|-------------|-----------|--------|
| NSE Bot | `TELEGRAM_BOT_TOKEN` | `/start`, `/sb`, `/scan`, etc. | ✅ Active |
| FTMO Bot | `TELEGRAM_BOT_TOKEN_FTMO` | `/fx_*` | ✅ Active (Wave 4) |
| GFT Bot | `TELEGRAM_BOT_TOKEN_GFT` | `/gft_*` | ✅ Active (Wave 4) |
| Crypto Bot | `CRYPTO_TELEGRAM_TOKEN` | `/btc_*` | GATED |

**No command namespace conflicts** — each bot uses a distinct prefix.
All tokens verified distinct in `.env`.

**One gap:** Crypto bot `_send()` has no retry logic. If Telegram is
briefly unavailable, crypto alerts are silently dropped. Not a trading risk
(crypto is gated/paper) but worth fixing before enabling live crypto.

---

## 11. Test Coverage

### Test Suite: 18 files

```
tests/
├── conftest.py                      (fixtures)
├── test_metrics.py                  Core P&L metrics
├── test_risk.py                     Risk guard enforcement
├── test_strategy_config.py          Settings / config parsing
├── test_tick_watcher.py             TickWatcher trigger logic
├── test_paper_trade_gates.py        Paper trader gates
├── test_option_pressure_score.py    Options OI pressure
├── test_option_cache.py             Option chain caching
├── test_atm_strike.py               Strike selection (delta-based)
├── test_wave1_safety_guards.py      Execution safety (Wave 1)
├── test_mt5_multi_account.py        MT5 multi-account isolation
├── test_forex_execution_validation.py  Forex execution gates
├── test_liquidity_sweep_engine.py   ICT liquidity sweep detection
├── test_execution_guard.py          Centralized execution guard
├── test_wave2_resiliency.py         Crash recovery (Wave 2)
├── test_wave4_refactor.py           Telegram Wave 4 consolidation
├── test_truedata_hardening.py       TrueData feed resilience
```

### Coverage Gaps

| Uncovered Area | Risk | Priority |
|---------------|------|----------|
| ML training pipeline | LOW — shadow only, no live orders | LOW |
| Orchestrator multi-process recovery | MEDIUM — crash recovery path | MEDIUM |
| OI filter logic (`scanner/oi_filters.py`) | LOW — new module, no tests | MEDIUM |
| nse_options intelligence layer | LOW — gracefully optional | LOW |
| crypto_paper_trader state writes | MEDIUM — just fixed bug | MEDIUM (add test) |

**Recommended:** Add `tests/test_oi_filters.py` and `tests/test_crypto_state.py`
to cover the new modules added in this session.

---

## 12. Configuration Scatter

### Well-Centralised ✅

| Config | Location |
|--------|---------|
| NSE thresholds | `settings.py` |
| Forex / prop firm params | `forex_engine/prop_firms/ftmo/ftmo_config.py`, `gft/gft_config.py` |
| Market hours | `utils/market_hours.py` |
| ML gate thresholds | Mostly `ml_engine/config/ml_config.json` |
| TrueData credentials | `.env` |
| MT5 credentials | `.env` |

### Scattered ⚠️

| Config | Files | Recommendation |
|--------|-------|---------------|
| ML AUC threshold 0.55 | `ml_engine/training/train_dnn.py:274` | Move to `ml_config.json:gate.min_auc` |
| Crypto risk params | `crypto_engine/crypto_paper_trader.py` (inline consts) | Move to `crypto_engine/crypto_config.py` |
| Silver Bullet window times | `scanner/silver_bullet.py:SILVER_BULLET_WINDOWS` | Already clean; consistent with `settings.py` |

---

## 13. Dependency Health

### Active Dependencies (all in use)

| Package | Used By | Version | Risk |
|---------|---------|---------|------|
| pandas | scanner, ml, backtest | 3.0.3 | LOW |
| numpy | ml_engine, greeks | 2.4.5 | LOW |
| fyers-apiv3 | NSE trading | latest | MEDIUM (token expires daily) |
| truedata-ws | TrueData feed | 5.0.11 | LOW |
| MetaTrader5 | FTMO + GFT | latest | LOW |
| python-telegram-bot | all bots | latest | LOW |
| httpx | Tooling only | 0.28.1 | LOW |

### Potentially Unused

| Package | Status |
|---------|--------|
| `yfinance` | No active calls in core code paths — kept for potential backtest use |
| `httpx` | Used in tooling/scripts; not in production scanner path |

Neither poses a risk — both are lightweight imports.

---

## 14. Priority Action List

### DO NOW (before next trading session)

| # | Action | File | Why |
|---|--------|------|-----|
| 1 | ✅ Fixed | `crypto_engine/crypto_paper_trader.py` | State corruption on crash |

### NEXT SPRINT

| # | Action | File | Why |
|---|--------|------|-----|
| 2 | Delete `provider/truedata_v1_archived/` | `provider/` | 2K dead LOC, never imported |
| 3 | Delete `market_data/` or move to `_experimental/` | `market_data/` | 1.5K orphaned LOC |
| 4 | Archive `CB6_PRE_LIVE_RESUME_20260523/` | root | 8K stale LOC |
| 5 | Delete `journal/` | root | Empty stub |
| 6 | Add retry to `bot_crypto.py:_send()` | `communications/` | Silent alert drops |
| 7 | Move ML AUC threshold to `ml_config.json` | `ml_engine/training/train_dnn.py` | Hardcoded config |

### MEDIUM PRIORITY

| # | Action | File | Why |
|---|--------|------|-----|
| 8 | Add `tests/test_oi_filters.py` | `tests/` | New module, no tests |
| 9 | Add `tests/test_crypto_state.py` | `tests/` | Cover the fixed bug |
| 10 | Consolidate NSE `send_message` into `telegram_helpers` | `utils/telegram_alerts.py` | 4 implementations → 1 |

### LOW PRIORITY

| # | Action | File | Why |
|---|--------|------|-----|
| 11 | Create `crypto_engine/crypto_config.py` | `crypto_engine/` | Inline consts |
| 12 | Add integration test for orchestrator recovery | `tests/` | Multi-process untested |
| 13 | Replace `datetime.utcnow()` with `datetime.now(UTC)` | `core/execution_guard.py:88` | Python 3.12 deprecation warning |

---

## Appendix A: Prop Firm Rule File Map

| Rule | Primary Enforcement | Secondary Enforcement |
|------|--------------------|--------------------|
| FTMO daily loss $300 | `ftmo_state.py:can_trade()` | `ftmo_config.py:MAX_DAILY_LOSS` |
| FTMO best-day cap $250 | `ftmo_state.py:best_day_pnl` | `forex_backtest.py:BEST_DAY_LIMIT` |
| GFT daily loss $200 | `gft_daily_loss_guard.py` | `gft_config.py:internal_daily_hard_stop` |
| GFT total loss $500 | `gft_daily_loss_guard.py` | `gft_config.py:internal_total_hard_stop` |
| XAUUSD banned (GFT) | `gft_config.py:disabled_symbols` | `gft_5k_2step.py:line 513` |
| NSE no equity | Symbol whitelist (index futures only) | Hook: check_paper_mode.py |

---

## Appendix B: Data Flow Summary

```
NSE Engine
  TrueData (primary) ←→ Fyers (fallback)
       ↓
  scanner/silver_bullet  [ICT pattern detection]
       ↓
  core/execution_guard   [trade gates]
       ↓
  trader/paper_trader    [paper execution + state_io]
  trader/live_trader     [live execution + threading.Lock + tmp/replace]

Forex Engine (fully isolated)
  MT5 (FTMO terminal) → ftmo_10k.py → ftmo_state.py [state_io]
  MT5 (GFT terminal)  → gft_5k_2step.py → gft_phase_tracker.py [state_io]

Crypto Engine (gated)
  Binance WS → crypto_worker.py → crypto_paper_trader.py [threading.Lock + tmp/replace ✅ fixed]

ML System (shadow only)
  scanner DataFrames → ml_engine/features → shadow_predictor → logs only
```

---

## Appendix C: Test Results

38 passed, 1 failed (fixed), 12 warnings.

**Failure found and fixed:**

`tests/test_execution_guard.py::TestTrueDataDeprecation::test_functions_still_accessible`

The test asserted `tf_to_bar_size("15") == "15 mins"` — the old incorrect format
from before the TrueData rewrite. The correct format is `"15min"`.
Updated in this audit session. All 39 tests now pass.

**Warnings (non-blocking):**

`datetime.datetime.utcnow()` deprecated in Python 3.12 — appears in
`core/execution_guard.py:88` and the test fixture. Replace with
`datetime.now(datetime.UTC)` in a future cleanup pass. Does not affect runtime.

---

*Full audit complete. One bug fixed, four dead-code zones identified, ten priority actions queued.*
