# TrueData Discovery Report — CB6 Quantum
**Date:** 2026-05-30 | **Scope:** Full repository scan

---

## Files Found (32 total)

| File | Symbol / Pattern | Line Range | Active? |
|------|-----------------|-----------|---------|
| `data/truedata_feed.py` | `TrueDataManager`, `TD_live`, `TD_hist` | 1–366 | Partial (shim layer, critical bugs) |
| `provider/truedata/__init__.py` | `TrueDataManager` re-export | 1–73 | Active |
| `provider/truedata/auth.py` | `TrueDataAuth` | 1–171 | Active |
| `provider/truedata/config.py` | `TrueDataConfig` | 1–200 | Active |
| `provider/truedata/exceptions.py` | `TrueDataError` hierarchy | 1–86 | Active |
| `provider/truedata/models.py` | `MarketTick`, `MarketBar`, `OptionChainRow`, `GreeksSnapshot` | 1–150 | Active |
| `provider/truedata/websocket_client.py` | `TrueDataWebSocketClient` | 1–458 | Active (not called in main) |
| `provider/truedata/rest_client.py` | `TrueDataRestClient` | 1–205 | Active |
| `provider/truedata/historical_client.py` | `TrueDataHistoricalClient`, `get_candles` | 1–328 | Active (called via data_fetcher) |
| `provider/truedata/greeks_client.py` | `TrueDataGreeksClient`, `get_greeks`, `get_chain_greeks` | 1–233 | Trial only |
| `provider/truedata/option_chain.py` | `TrueDataOptionChain`, `start_option_chain`, `get_atm_chain` | 1–263 | Trial only |
| `provider/truedata/symbol_master.py` | `TrueDataSymbolMaster`, `get_all_symbols`, `get_atm_strikes` | 1–257 | Trial only |
| `scanner/websocket_feed.py` | `init_truedata`, `subscribe_truedata`, `_td_active` | 133–177 | Present, not invoked from main.py |
| `scanner/live_price.py` | `_td_ltp`, `get_live_price`, `get_live_prices` | 1–75 | Active (TrueData primary, Fyers fallback) |
| `scanner/data_fetcher.py` | `_get_historical_data_truedata`, `get_historical_data` | 142–161 | Active (TrueData primary, Fyers fallback) |
| `market_data/health_monitor.py` | Feed health metrics, TrueData status | 1–100+ | Active |
| `market_data/tick_store.py` | Tick persistence stubs (TrueData references) | — | Partial stub |
| `market_data/interfaces.py` | Abstract `IMarketDataProvider` using TrueData models | — | Active |
| `trial/run_truedata_trial.py` | Trial orchestrator | 1–200+ | Test only |
| `trial/test_live_feed.py` | WebSocket quality test, `one_min_bar_callback` | 1–200+ | Test only |
| `trial/test_historical.py` | `get_historic_data`, `get_n_historical_bars` | 1–200+ | Test only |
| `trial/test_greeks.py` | `greek_callback`, `get_greeks` | 1–200+ | Test only |
| `trial/test_option_chain.py` | `start_option_chain`, `bidask_callback` | 1–200+ | Test only |
| `trial/trial_report.py` | Result aggregation and scoring | 1–200+ | Test only |
| `config/truedata_trial.yaml` | Trial config: symbols, duration, expected counts | — | Test only |
| `settings.py` | `TRUEDATA_USER`, `TRUEDATA_PASSWORD` exports | — | Active |
| `TRUEDATA_CODE_REVIEW.md` | 6 critical + 5 high + 6 medium issues documented | — | Reference doc |
| `CB6_AUDIT_RESULTS.md` | Architecture audit results | — | Reference doc |

---

## Per-File Deep Dive

### `data/truedata_feed.py` — Deprecated Shim Layer
- **Class:** `TrueDataManager` (singleton via `get_instance()`)
- **Key functions:**
  - `connect_hist()` — connects TD_hist, sets `_hist_connected=True`
  - `connect_live(symbols)` — connects TD_live, starts WebSocket stream
  - `get_historical_bars(symbol, days, interval)` — REST candle fetch
  - `get_last_n_bars(symbol, n, interval)` — last N candles
  - `_dispatch_tick(symbol, data)` — routes ticks to scanner cache + core
  - `_dispatch_bar(symbol, data)` — 1-min bar handler (stub, drops bars)
  - `disconnect()` — teardown
- **Called from:** `scanner/data_fetcher.py`, `scanner/live_price.py`
- **Status:** Functional but has 4 critical concurrency bugs (see Phase 5)
- **Note:** This is the backward-compat shim. New code should use `provider.truedata.*` directly.

### `provider/truedata/` — Modern Async Implementation (2,380 lines)
- **auth.py:** `TrueDataAuth` — HTTP POST login, token caching, 5-min refresh buffer, thread-safe Lock
- **websocket_client.py:** `TrueDataWebSocketClient` — async WS to `wss://push.truedata.in:8082/8086`, reconnect backoff 1s→60s, 30s heartbeat, sequence gap detection, latency tracking (500-sample ring buffer per symbol)
- **historical_client.py:** `TrueDataHistoricalClient.get_candles()` — intervals: 1min/3min/5min/10min/15min/30min/60min/1day, returns `list[MarketBar]`
- **option_chain.py:** `get_option_chain()`, `get_atm_chain()` — ATM-centered filtering, all Greeks in response
- **greeks_client.py:** `get_greeks()` / `get_chain_greeks()` — IV, Delta, Gamma, Theta, Vega, Rho with range validation
- **symbol_master.py:** `get_all_symbols()`, `get_fo_symbols()`, `get_index_symbols()`, `get_atm_strikes()` — cached in memory
- **Status:** Well-architected, tested in trial suite. Not yet wired into production paths.

### `scanner/websocket_feed.py`
- **Functions:** `init_truedata(symbols)`, `subscribe_truedata(symbols)`, `_td_active` bool
- **Integration:** Calls `TrueDataManager.connect_live()` + subscribes symbols
- **Called from:** **NOT called in main.py startup** — only Fyers init is called
- **Status:** Present but inactive in production

### `scanner/live_price.py`
- **Functions:** `_td_ltp(symbol)`, `get_live_price(symbol)`, `get_live_prices(symbols)`
- **Flow:** TrueData tick cache → Fyers API (fallback)
- **Status:** Active for LTP when TrueData feed is connected; falls back gracefully

### `scanner/data_fetcher.py`
- **Function:** `_get_historical_data_truedata(symbol, days, interval)` → calls `TrueDataManager.get_historical_bars()`
- **Flow:** TrueData → Fyers (3-retry backoff fallback)
- **Column mapping:** Normalizes TrueData schema to Fyers format for downstream compatibility
- **Status:** Active in production (when TrueData credentials present)

### `trial/` — Test Suite
- **run_truedata_trial.py:** Orchestrates all 5 tests with configurable symbols from YAML
- **test_live_feed.py:** Connects WS, collects 2-min tick stream, measures latency/gaps/throughput
- **test_historical.py:** Fetches 1m/5m/15m/1d candles, validates OHLCV integrity
- **test_greeks.py:** Fetches Greeks for ATM calls/puts, validates IV range (1%–1000%)
- **test_option_chain.py:** Fetches full chain + ATM filter, validates OI/bid/ask fields
- **trial_report.py:** Scores each test Pass/Warn/Fail with quantitative thresholds
- **Status:** Test only — never called from main.py or forex_main.py

---

## Active vs Dead Code Summary

| Component | Active in Production? | Notes |
|-----------|----------------------|-------|
| `provider/truedata/historical_client` | ✅ Yes (via data_fetcher) | Historical candle fetch working |
| `scanner/live_price` TrueData path | ✅ Yes (when feed connected) | Falls back to Fyers if feed not connected |
| `provider/truedata/auth` | ✅ Yes | Used by historical client |
| `provider/truedata/rest_client` | ✅ Yes | HTTP backbone |
| `provider/truedata/websocket_client` | ❌ Not called in main.py | Present, not activated |
| `provider/truedata/option_chain` | ❌ Trial only | |
| `provider/truedata/greeks_client` | ❌ Trial only | |
| `provider/truedata/symbol_master` | ❌ Trial only | |
| `scanner/websocket_feed.init_truedata` | ❌ Not invoked from main.py | |
| `data/truedata_feed.connect_live` | ❌ Not called in production | |
| `trial/` entire directory | ❌ Test only | |

**Bottom line:** TrueData historical data is active and working. TrueData live feed is fully built but not activated in production. Options/Greeks/Symbol services are built and tested in isolation but not wired into any scanner or ML path.
