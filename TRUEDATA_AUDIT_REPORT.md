# TrueData Integration Audit Report — CB6 Quantum
**Date:** 2026-05-30 | **Auditor:** Senior Anthropic Engineering Review
**Repository:** `c:\cb6_bot\` | **TrueData SDK:** Python (async + REST)

---

## Summary Table

| Feature | Status | CB6 Usage | Production Ready |
|---------|--------|-----------|-----------------|
| Authentication | ✅ Fully Integrated | Active (historical path) | ✅ Yes |
| WebSocket Live Feed | ✅ Fully Built | **Not activated in main.py** | ❌ No (4 critical races) |
| Tick Data | ✅ Fully Built | Not persisted | ❌ No (memory-only, blocking) |
| Historical — 1m/3m/5m | ✅ Fully Integrated | Active via data_fetcher | ✅ Yes (no retry gap) |
| Historical — 15m/30m/60m/EOD | ✅ Fully Integrated | Active via data_fetcher | ✅ Yes |
| Option Chain (REST) | ✅ Fully Integrated | Trial only | ✅ Yes (not wired to scanner) |
| Greeks (REST) | ✅ Fully Integrated | Trial only | ✅ Yes (not wired to scanner) |
| OI Intelligence | ⚠️ Partially Present | Not consumed | ⚠️ Data available, no consumer |
| Bid / Ask | ⚠️ Partially Present | Not consumed | ⚠️ Data present, LTP-only used |
| Symbol Master | ✅ Fully Integrated | Trial only | ✅ Yes |
| Expiry List | ⚠️ Derived only | Not consumed | ⚠️ No dedicated API endpoint |
| Corporate Actions | ❌ Not Present | — | — |
| News Feed | ❌ Not Present | — | — |

---

## Integrated Features

### 1. Authentication (`provider/truedata/auth.py`)
- HTTP POST login to TrueData API
- Token caching with 5-minute pre-expiry refresh buffer
- Thread-safe via `threading.Lock`
- Auto-logout on disconnect
- Credentials loaded from `.env` via `settings.py`
- Token stored in memory only (never written to disk)
- **Quality:** Production-grade implementation

### 2. Historical Candles (`provider/truedata/historical_client.py` + `scanner/data_fetcher.py`)
- Intervals: 1min, 3min, 5min, 10min, 15min, 30min, 60min, 1day
- Called by `data_fetcher._get_historical_data_truedata()` on every scanner bar request
- Returns normalized DataFrame matching Fyers schema
- Automatic fallback to Fyers on any TrueData failure (3-retry with backoff on Fyers side)
- Rate limited to 10 req/sec by REST client
- **Quality:** Working in production. Gap: no retry on TrueData side before falling back.

### 3. Live Price Feed (`scanner/live_price.py`)
- `get_live_price(symbol)` — TrueData tick cache primary, Fyers API fallback
- `get_live_prices(symbols)` — batch LTP across both sources
- Consistent symbol normalization (NIFTY-I, BANKNIFTY-I formats)
- **Quality:** Working when feed is connected. Feed not currently activated.

### 4. WebSocket Client (`provider/truedata/websocket_client.py`)
- Async WS to `wss://push.truedata.in:8082` (sandbox) or `:8086` (live)
- Exponential reconnect backoff: 1s → 2s → 4s … → 60s cap
- 30-second heartbeat monitoring
- Sequence gap detection per-symbol (alerts on missing ticks)
- Per-symbol latency tracking with 500-sample ring buffer
- Callbacks: `on_tick`, `on_bar`, `on_connect`, `on_disconnect`, `on_error`
- **Quality:** Modern async design. Not called from main.py.

### 5. Option Chain (`provider/truedata/option_chain.py`)
- `get_option_chain(underlying, expiry=None)` → list of rows per strike × (CE/PE)
- `get_atm_chain(underlying, spot, n_strikes, expiry)` → ATM-centered filtered list
- Fields: symbol, strike, option_type, ltp, bid, ask, oi, oi_change, volume, iv, greeks
- Underlyings tested: NIFTY, BANKNIFTY, FINNIFTY
- **Quality:** Trial suite passes. Not wired into CB6 scanner.

### 6. Greeks (`provider/truedata/greeks_client.py`)
- `get_greeks(symbol)` → GreeksSnapshot (IV, Delta, Gamma, Theta, Vega, Rho)
- `get_chain_greeks(underlying, strikes)` → list with per-symbol error isolation
- Input validation: IV range check (1%–1000%), Delta range (−1 to +1)
- **Quality:** Trial suite passes. Not wired into CB6 scanner or ML.

### 7. Symbol Master (`provider/truedata/symbol_master.py`)
- `get_all_symbols()`, `get_fo_symbols()`, `get_index_symbols()`
- `find_symbol(name)`, `get_option_strikes(underlying, expiry)`, `get_atm_strikes()`
- Results cached in memory after first fetch
- **Quality:** Trial suite passes. Not wired into scanner.

### 8. Trial Test Suite (`trial/`)
- 5 comprehensive tests covering: live feed, historical, Greeks, option chain, symbol master
- Configurable via `config/truedata_trial.yaml`
- Scoring: Pass/Warn/Fail with quantitative thresholds
- **Quality:** Good coverage. Not run in CI/CD.

---

## Partially Integrated Features

### Live WebSocket Feed
- **Built:** Yes — `scanner/websocket_feed.init_truedata()` calls `TrueDataManager.connect_live()`
- **Gap:** `init_truedata()` is never called from `main.py`. Only `init_fyers()` (Fyers WebSocket) is called at startup.
- **Impact:** TrueData live ticks are never received during a production trading session.
- **Fix:** Add `init_truedata(symbols)` call in `main.py` startup, after existing `init_fyers()`.

### OI Intelligence
- **Built:** `OptionChainRow.oi` and `oi_change` fields populated by option chain API
- **Gap:** No code in scanner/ML/dashboard queries these fields. OI data arrives and is discarded.
- **Impact:** CB6 cannot use OI buildup/washout signals for trade confirmation.

### Bid/Ask Spread
- **Built:** `MarketTick.bid`, `ask`, `bid_qty`, `ask_qty` fields populated
- **Gap:** `scanner/live_price.py` only reads `ltp`. Bid/ask never checked for spread validation before entry.
- **Impact:** CB6 may enter during wide-spread conditions; no execution quality control.

### Tick Data
- **Built:** Tick ingestion from WS → `_dispatch_tick()` → `websocket_feed._tick_cache`
- **Gap 1:** In-memory cache only. Lost on restart. No tick persistence to disk.
- **Gap 2:** `_dispatch_tick()` runs synchronously on WS receive thread — slow evaluation blocks incoming ticks.
- **Gap 3:** `_dispatch_bar()` (1-min bars from TrueData) is a stub that silently drops every bar.

---

## Missing Features (Available in TrueData, Not in CB6)

| Feature | TrueData Support | CB6 Value | Complexity | Recommendation |
|---------|-----------------|-----------|------------|----------------|
| **Live Greeks Streaming** | WebSocket callbacks | HIGH — real-time IV crush detection before expiry | Medium | Priority 2 — add after live feed activated |
| **Expiry Calendar API** | Symbol master derived | MEDIUM — reliable expiry dates for option chain queries | Easy | Priority 2 — 1-day task |
| **Index Component List** | `get_index_symbols()` | MEDIUM — detect rebalancing events | Easy | Priority 3 |
| **Order Book Depth (L2)** | WebSocket feed | LOW — ICT strategy doesn't use depth | Hard | Priority 4 — ignore |
| **Trade Prints** | `trade_callback` | LOW — not used by scanner | Medium | Priority 4 — ignore |
| **Tick History Export** | REST endpoint | MEDIUM — ultra-granular backtesting | Medium | Priority 3 |
| **Corporate Actions** | REST endpoint | MEDIUM — affects backtesting accuracy (splits) | Easy | Priority 3 |
| **News Feed** | REST endpoint | LOW — CB6 uses Yahoo Finance already | Medium | Priority 4 |
| **OI Streaming** | WebSocket | HIGH — real-time OI buildup signals | Medium | Priority 2 |
| **Bid/Ask Spread Monitoring** | WebSocket tick | HIGH — entry execution quality | Easy | Priority 1 — data already arriving |

---

## Remove Candidates

| Item | Reason | Action |
|------|--------|--------|
| `data/truedata_feed.py` — `_dispatch_bar()` stub | Subscribes to 1-min bars but silently drops every one. Wasted subscription. | Remove bar subscription OR implement actual handler |
| `market_data/tick_store.py` stub references | Tick persistence stubs that reference TrueData but contain no logic | Implement or remove |
| `TRUEDATA_CODE_REVIEW.md` (if stale) | 18 documented issues — if fixed, archive. If not fixed, keep as reference. | Keep until issues resolved |
| Bare `except: pass` blocks in `data/truedata_feed.py:291–302` | Silent failure on every tick hides real errors. Thousands of swallowed exceptions/sec at open. | Replace with `except Exception as e: logger.debug(...)` |

---

## Phase 5 Production Readiness

### Reliability

#### CRITICAL — Race Conditions (4 found in `data/truedata_feed.py`)

**C1 — TOCTOU in `connect_hist()` / `connect_live()`**
```python
# data/truedata_feed.py:113–131
# Lock released after early-return check.
# Two threads can both see _hist_connected=False → both create sessions.
# First session silently leaked; second overwrites it.
```
**Fix:** Hold lock through entire connect sequence.

**C2 — Concurrent read of `self._hist` without lock**
```python
# data/truedata_feed.py:132–178
# Reads _hist_connected (unlocked), then calls _hist.get_historic_data().
# If disconnect() fires between those two lines: _hist = None → AttributeError.
# Silently falls back to Fyers every time.
```
**Fix:** Hold read lock across the check + call pair.

**C3 — Session expiry not detected**
```python
# data/truedata_feed.py:105–178
# _hist_connected = True is never reset on auth failure.
# Token expiry mid-day → all historical calls silently fall to Fyers.
# No operator visibility.
```
**Fix:** Catch auth errors in `get_historical_bars()`; set `_hist_connected = False` and send Telegram alert.

**C4 — WebSocket session leak on `start_live_data()` error**
```python
# data/truedata_feed.py:231–253
# If start_live_data() raises, self._live loses its reference.
# Internal WS thread keeps running with no manager → zombie connection.
```
**Fix:** Wrap in try/except; call disconnect on exception path.

#### HIGH — Blocking Tick Dispatch (H4)
- `_dispatch_tick()` calls `on_tick()` synchronously on WS receive thread
- Slow ICT evaluation blocks incoming ticks during market open bursts
- **Fix:** Push ticks to a `queue.Queue`; dedicated worker thread pulls and evaluates

#### HIGH — No Retry on TrueData Historical (H2)
- Single transient error (5xx, network blip) fails immediately to Fyers fallback
- Fyers gets 3 retries with backoff; TrueData gets 0
- **Fix:** Add 2-retry loop with 0.5s backoff before falling through to Fyers

#### HIGH — Credential Exposure (H3, H5)
- `_TRUEDATA_USER`, `_TRUEDATA_PASS` accessible as module globals (single underscore)
- Exception from `TD_hist()` init may include credentials in request URL → leaked in logs
- **Fix:** Use `__TRUEDATA_USER` (double underscore) + redact credentials from exception strings

#### MEDIUM — No atexit Cleanup (M1)
- `TrueDataManager.disconnect()` never called automatically on process exit
- WebSocket stays open on TrueData servers until timeout; repeated restarts exhaust quota
- **Fix:** Register `atexit.register(get_instance().disconnect)`

### Performance

| Metric | Status | Notes |
|--------|--------|-------|
| Tick throughput | ⚠️ At risk | Synchronous dispatch blocks WS thread |
| Historical latency | ✅ OK | Rate-limited to 10 req/sec |
| Memory usage | ✅ OK | 500-sample ring buffer per symbol capped |
| Queue depth | ❌ No queue | No tick queue → drop risk under load |

### Security

| Item | Status | Risk |
|------|--------|------|
| Credentials in `.env` | ✅ Protected | `.env` in `.gitignore` |
| Token in memory only | ✅ OK | Never written to disk |
| Module-level globals `_TRUEDATA_USER/PASS` | ⚠️ Exposed | Any module in process can read |
| Auth exception may log credentials | ⚠️ Risk | Fix: redact before logging |
| TrueData creds not rotated | ⚠️ Action needed | See `SECRET_ROTATION_CHECKLIST.md` |

### Monitoring

| Item | Status |
|------|--------|
| `market_data/health_monitor.py` | ✅ Feed health tracking present |
| Auth failure detection | ❌ Silent (see C3 above) |
| Sequence gap alerts | ✅ Per-symbol gap detection in WS client |
| Latency tracking | ✅ 500-sample ring buffer |
| Telegram on disconnect | ❌ Not wired to TrueData WS |
| Feed quality metrics | ⚠️ Partial (trial suite only, not in production) |

---

## Recommended Next Steps

### Priority 1 — Must Fix Before Activating Live Feed

1. **Fix C1-C4 race conditions** in `data/truedata_feed.py`
   - Hold lock through entire connect/disconnect sequences
   - Detect auth expiry; reset `_hist_connected` on failure
   - *Effort: 4 hours*

2. **Wire bid/ask spread check into entry validation**
   - Data already arriving via tick. Add spread % check in `_nifty_live_scanner` before firing trade.
   - *Effort: 2 hours*

3. **Add atexit cleanup**
   - `atexit.register(TrueDataManager.get_instance().disconnect)`
   - *Effort: 30 minutes*

4. **Move tick dispatch to dedicated worker queue**
   - `queue.Queue` + worker thread; WS thread just enqueues
   - *Effort: 3 hours*

5. **Credential hardening**
   - Double-underscore globals; redact from exception messages
   - *Effort: 1 hour*

6. **Activate live feed in `main.py`**
   - Add `init_truedata(active_symbols)` in startup, after Fyers init
   - Add Telegram alert on TrueData WS disconnect (mirror MT5 alert pattern already done)
   - *Effort: 1 hour*

### Priority 2 — Should Add

7. **OI monitoring in scanner** — TrueData option chain fetched once per candle; use `oi_change` as confluence point for ICT setups
   - *Effort: 6 hours*

8. **Live IV monitoring** — Fetch ATM Greeks at SB window open; filter entries when IV > 2× 30-day avg
   - *Effort: 8 hours*

9. **Add retry to TrueData historical fetch** before falling to Fyers
   - *Effort: 1 hour*

10. **Expiry calendar helper** — Wrap `symbol_master.get_option_strikes()` into a simple `get_expiry_list(underlying)` function
    - *Effort: 2 hours*

### Priority 3 — Optional

11. **Tick persistence** — Write tick stream to daily Parquet files for backtesting
12. **Index component tracking** — Alert on rebalancing events
13. **Corporate actions detection** — Flag symbols with pending splits to avoid dirty backtests

### Priority 4 — Ignore

14. **Order book depth (L2)** — ICT strategy doesn't use L2 data
15. **Trade prints** — Not used by scanner
16. **News feed** — Yahoo Finance already wired; duplication
17. **MIDCPNIFTY option chain** — Low liquidity; trial config excludes it

---

## Final Scores

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| **Integration Coverage** | 62/100 | Historical + auth + live_price wired; live feed + options/Greeks/OI not activated |
| **Reliability** | 28/100 | 4 critical race conditions unresolved in production shim layer |
| **Performance** | 45/100 | Synchronous tick dispatch on WS thread; no queue; REST rate limiting present |
| **Security** | 55/100 | `.env` protected; module globals expose credentials; auth exception log risk |
| **Production Readiness** | 42/100 | Historical data production-ready; live feed has blockers; options/Greeks trial-only |

---

## Overall Verdict

**Can TrueData become the primary NSE market-data backbone for CB6 Quantum?**

### ✅ CONDITIONAL YES

**Reasoning:**

The TrueData integration is 90% built and architecturally sound. The `provider/truedata/` package is modern, async-first, and well-tested. Historical data is already running in production as the primary source with Fyers fallback. The option chain, Greeks, and symbol master are fully implemented and passing trial tests.

The blocker is not architecture — it is 4 unresolved critical race conditions in `data/truedata_feed.py` (the backward-compat shim layer), plus the live WebSocket feed never being activated in `main.py`.

**What works today (no changes needed):**
- Historical candle data (1m through EOD) — TrueData is already primary
- Live LTP via `scanner/live_price.py` — works when feed is connected

**Conditions to flip to full primary:**
1. Fix 4 critical race conditions — `~4 hours`
2. Move tick dispatch off WS thread — `~3 hours`
3. Add `init_truedata()` call in `main.py` startup — `~1 hour`
4. Wire bid/ask spread into entry guard — `~2 hours`
5. Add credential hardening — `~1 hour`

**Total estimated effort: ~11 hours to unlock full live feed.**

Once those 5 items are done, TrueData provides a materially better data backbone than Fyers for NSE:
- Lower latency (WebSocket push vs Fyers poll)
- Native OI data (Fyers requires separate API call)
- Built-in Greeks (Fyers requires option chain separately)
- Sequence gap detection (Fyers has none)
- ATM strike helper (built-in vs manual calculation)

**Recommendation:** Fix Priority 1 items in the next session, then activate live feed. Run side-by-side with Fyers for 1 week before making TrueData the sole source.

---

*Audit completed 2026-05-30 | CB6 Quantum Hardening*
