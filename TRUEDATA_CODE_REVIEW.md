# TrueData Integration — Code Review

**Reviewer:** Claude Code  
**Date:** 2026-05-29  
**Files reviewed:**  
- `data/truedata_feed.py` (new)  
- `scanner/data_fetcher.py` (modified)  
- `scanner/live_price.py` (modified)  
- `scanner/websocket_feed.py` (modified)

---

## Summary

The integration is functional in the happy path. The symbol/timeframe mapping is correct, the singleton pattern is sound, and the Fyers fallback chain works. However there are four critical issues that can produce silent data corruption or crashes under concurrent load — the most likely scenario for a live trading bot — plus several high-priority holes around reconnection and rate limiting.

---

## Critical Issues

### C1 — TOCTOU race in `connect_hist()` creates duplicate REST sessions

**File:** `data/truedata_feed.py:105–123`

```python
def connect_hist(self) -> bool:
    with self._lock:
        if self._hist_connected:   # ← lock released here
            return True            #
    # ← gap: another thread can pass this check simultaneously
    try:
        hist = TD_hist(...)        # ← two threads both create a session
        with self._lock:
            self._hist = hist      # ← second thread silently overwrites first
            self._hist_connected = True
```

The lock is released after the early-return check. Two threads that both see `_hist_connected = False` simultaneously will each call `TD_hist(...)`, creating two concurrent auth sessions with TrueData. The second assignment silently discards the first session without closing it. A leaked open session against a prop-firm API is a reliability risk.

**Same defect exists in `connect_live()` at line 216.**

**Fix:** Hold the lock for the entire connect sequence, or use a dedicated connecting-state flag:

```python
def connect_hist(self) -> bool:
    with self._lock:
        if self._hist_connected:
            return True
        if self._connecting_hist:   # guard against concurrent entry
            return False
        self._connecting_hist = True
    try:
        hist = TD_hist(...)
        with self._lock:
            self._hist = hist
            self._hist_connected = True
        return True
    except Exception as exc:
        logger.error(f"TrueData: TD_hist connect failed: {exc}")
        return False
    finally:
        with self._lock:
            self._connecting_hist = False
```

---

### C2 — `get_historical_bars()` reads `self._hist` without lock — crash on concurrent disconnect

**File:** `data/truedata_feed.py:132–178`

```python
def get_historical_bars(self, td_symbol, bar_size, days=30):
    if not self._hist_connected:   # ← read without lock
        if not self.connect_hist():
            return None
    try:
        df = self._hist.get_historic_data(...)  # ← self._hist read without lock
```

`disconnect()` runs under `self._lock` and sets `self._hist = None`. If `disconnect()` executes between line 132 and line 136, `self._hist` becomes `None` and line 136 raises `AttributeError: 'NoneType' object has no attribute 'get_historic_data'`. This exception is caught and logged, but the log message will be misleading ("TrueData get_historical_bars... NoneType...") and the Fyers fallback fires unnecessarily.

**Same defect in `get_last_n_bars()` at line 186.**

**Fix:** Read `self._hist` under lock and keep a local reference:

```python
def get_historical_bars(self, td_symbol, bar_size, days=30):
    with self._lock:
        hist = self._hist
        connected = self._hist_connected
    if not connected or hist is None:
        if not self.connect_hist():
            return None
        with self._lock:
            hist = self._hist
    try:
        df = hist.get_historic_data(...)
```

---

### C3 — Session expiry not detected — `_hist_connected` stuck True on dead connection

**File:** `data/truedata_feed.py:105–178`

Once `connect_hist()` succeeds and `_hist_connected = True`, it is never set back to False on a REST failure. If the TrueData session token expires mid-day (TrueData sessions are time-limited), all subsequent `get_historic_data()` calls will raise exceptions, be caught, and return None. The code then falls through to Fyers — silently. The manager never attempts to re-authenticate.

From the user's perspective the bot is running on TrueData (it shows `TD_hist connected` in logs once) but is actually using Fyers for every call after expiry, with zero visibility.

**Fix:** On `get_historic_data()` raising an auth/session exception, clear `_hist_connected` and set `self._hist = None` so the next call will re-authenticate:

```python
except Exception as exc:
    err_str = str(exc).lower()
    if any(kw in err_str for kw in ("auth", "token", "expired", "unauthorized", "401")):
        with self._lock:
            self._hist_connected = False
            self._hist = None
        logger.warning(f"TrueData: session expired, will re-auth on next call")
    logger.error(f"TrueData get_historical_bars({td_symbol}): {exc}")
    return None
```

---

### C4 — WebSocket session leaks if `start_live_data()` raises

**File:** `data/truedata_feed.py:222–245`

```python
live = TD_live(...)            # WS thread starts internally

@live.trade_callback
def _on_tick(tick_data): ...

live.start_live_data(symbols)  # ← if this raises...

with self._lock:               # ← ...this block is never reached
    self._live = live          # ← manager loses the reference
    self._live_connected = True
```

If `start_live_data()` raises (e.g. bad symbol list), the `live` object has already started its internal WebSocket thread. The reference is then lost from the local scope. The WebSocket thread is a daemon thread so it dies with the process, but until then the TrueData server has an open connection for which there is no manager reference, no way to call `disconnect()`, and the callbacks still fire into a zombie state.

**Fix:** Assign `self._live` before `start_live_data()`, under the lock, and clean up on error:

```python
with self._lock:
    self._live = live

try:
    live.start_live_data(symbols)
    with self._lock:
        self._live_connected = True
except Exception as exc:
    with self._lock:
        self._live = None
    try:
        live.disconnect()
    except Exception:
        pass
    raise
```

---

## High Issues

### H1 — No rate limiting on TrueData REST calls

**File:** `scanner/data_fetcher.py:142–161`

`_get_historical_data_truedata()` calls `get_historical_bars()` which calls `TD_hist.get_historic_data()` with no rate limiting. The Fyers path has careful throttling (`MIN_INTERVAL = 0.18s`, ~5.5 req/sec). `get_all_data()` calls `get_historical_data()` in a tight loop over potentially 200+ symbols. TrueData's REST API will receive up to 200 unanticipated concurrent requests. TrueData's rate limits are not documented in the provided files but almost certainly exist.

**Fix:** Add a TrueData-specific throttle in `_get_historical_data_truedata()`, or share the existing `_throttle()` only when TrueData is the source (since TrueData limits will differ from Fyers limits). At minimum, add a 200ms inter-call delay for TrueData:

```python
_TD_RATE_LOCK = threading.Lock()
_TD_LAST_CALL = [0.0]
_TD_MIN_INTERVAL = 0.25  # 4 req/sec conservative

def _throttle_td():
    with _TD_RATE_LOCK:
        now = time.monotonic()
        wait = _TD_MIN_INTERVAL - (now - _TD_LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _TD_LAST_CALL[0] = time.monotonic()
```

---

### H2 — No retry logic in TrueData historical fetch

**File:** `data/truedata_feed.py:135–178`

A single transient error (TCP reset, 502, brief network dropout) fails the entire TrueData call immediately, logs an error, and forces Fyers fallback. The Fyers path has 3 retries with exponential backoff (`backoff = 1.0`, `2 ** attempt` scaling). TrueData gets zero retries.

During market open, transient network errors are common. Every blip silently falls back to Fyers and produces a `logger.error()` that looks like a real failure.

**Fix:** Wrap `self._hist.get_historic_data()` in a retry loop matching the Fyers approach:

```python
for attempt in range(3):
    try:
        df = self._hist.get_historic_data(...)
        break
    except Exception as exc:
        if attempt == 2:
            raise
        time.sleep(0.5 * (2 ** attempt))
```

---

### H3 — Credentials accessible at module level

**File:** `data/truedata_feed.py:20–21`

```python
_TRUEDATA_USER = os.getenv("TRUEDATA_USER", "")
_TRUEDATA_PASS = os.getenv("TRUEDATA_PASSWORD", "")
```

These module-level constants are readable from any code in the same process:

```python
import data.truedata_feed as tf
print(tf._TRUEDATA_PASS)   # "rahul1449" — no access control
```

The single underscore prefix signals "private by convention" but provides no enforcement. Any third-party library, injected code, or debug console can read the plaintext password.

**Fix:** Load credentials lazily inside the methods that use them, directly from `os.getenv()`. Never store them as module-level state. This also fixes the import-time timing issue (see L4):

```python
def connect_hist(self) -> bool:
    user = os.getenv("TRUEDATA_USER", "")
    pwd  = os.getenv("TRUEDATA_PASSWORD", "")
    if not user or not pwd:
        ...
```

---

### H4 — `_dispatch_tick()` runs on WebSocket thread and can block on `on_tick()`

**File:** `data/truedata_feed.py:275–297`

`_dispatch_tick()` is called directly from the TrueData WebSocket receive thread (via the `@live.trade_callback` decorator). It then calls `get_watcher().on_tick(sym, ltp)`, which executes all registered triggers synchronously. If any trigger is slow (database write, Telegram message, complex calculation), the WebSocket receive loop is blocked and TrueData ticks queue up in the socket buffer.

Under sustained load (NIFTY open, high tick rate), this causes tick drops. The data quality of the live feed degrades silently — no log, no alert.

**Fix:** Dispatch to a dedicated processing queue with a worker thread:

```python
import queue
_tick_queue: queue.Queue = queue.Queue(maxsize=10000)

@live.trade_callback
def _on_tick(tick_data):
    try:
        _tick_queue.put_nowait(tick_data.to_dict())
    except queue.Full:
        logger.warning("TrueData: tick queue full, dropping tick")

# In a separate worker thread started at connect_live():
def _tick_worker():
    while True:
        tick = _tick_queue.get()
        self._dispatch_tick_dict(tick)
```

---

### H5 — Auth exception may log credentials

**File:** `data/truedata_feed.py:121–122`

```python
except Exception as exc:
    logger.error(f"TrueData: TD_hist connect failed: {exc}")
```

The exception raised by `TD_hist(username, password, ...)` on an auth failure may include the username and/or a partial token in its message, depending on the library version. The `truedata` package (v7.0.1) uses `requests` internally. HTTP library exceptions often include the request URL, which for an OAuth flow can include credentials in the query string.

Check by running: `TD_hist("wrong_user", "wrong_pass")` and inspecting the exception message.

**Fix:** Sanitize the exception message before logging, or catch specific exception types and log a generic message:

```python
except Exception as exc:
    safe = str(exc).replace(_TRUEDATA_PASS, "***").replace(_TRUEDATA_USER, "***")
    logger.error(f"TrueData: TD_hist connect failed: {safe}")
```

---

## Medium Issues

### M1 — No `atexit` cleanup — WebSocket orphaned on crash or `Ctrl+C`

**File:** `data/truedata_feed.py` (missing)

`TrueDataManager.disconnect()` is never called automatically. If the bot is killed with `Ctrl+C`, crashes, or is restarted, the TrueData WebSocket session remains open on the server side until it times out. Repeated restarts during development will exhaust the server's concurrent session quota.

**Fix:** Register a shutdown hook at module level:

```python
import atexit
atexit.register(lambda: _td.disconnect())
```

---

### M2 — `days` not validated — malformed duration string sent to TrueData

**File:** `data/truedata_feed.py:138`

```python
df = self._hist.get_historic_data(td_symbol, duration=f"{days} D", ...)
```

If `days=0` or `days=-5` (e.g. due to a caller bug), TrueData receives `"0 D"` or `"-5 D"`. The library may return an empty DataFrame, raise, or return unexpected data. There is no guard.

**Fix:** Assert or clamp at the entry point:

```python
if days <= 0:
    logger.error(f"TrueData: invalid days={days} for {td_symbol}")
    return None
```

---

### M3 — Empty `symbols` list not validated in `connect_live()`

**File:** `data/truedata_feed.py:211`

```python
def connect_live(self, symbols: list[str]) -> bool:
    ...
    live.start_live_data(symbols)
```

Calling `start_live_data([])` has undefined behavior per the TrueData docs. It may succeed silently (connected but no data), raise, or produce a confusing error. There is no guard.

**Fix:** Add a check before connecting:

```python
if not symbols:
    logger.warning("TrueData: connect_live called with empty symbol list")
    return False
```

---

### M4 — `logger.info()` per historical fetch spams logs during full scan

**File:** `data/truedata_feed.py:173` and `data_fetcher.py:157`

Both `get_historical_bars()` and `_get_historical_data_truedata()` emit `logger.info()` on every successful fetch. During a `get_all_data()` scan over 200 symbols, this emits 400 INFO lines in rapid succession, drowning out meaningful log events (signals, trades, errors).

**Fix:** Downgrade the per-symbol fetch log to `logger.debug()`. Keep `logger.info()` only for connection events and errors.

---

### M5 — `get_historical_bars()` not safe for use from async context

**File:** `data/truedata_feed.py:125–178`

`TD_hist.get_historic_data()` is a synchronous blocking HTTP call (uses `requests` internally). `data/financial_data_core.py` is fully async and uses `asyncio.to_thread()` to isolate blocking calls. If any future code calls `get_historical_bars()` from an async coroutine without `asyncio.to_thread()`, the entire event loop blocks for the duration of the HTTP call (typically 1–5 seconds).

**Fix:** Document this clearly at the function level, or provide an async wrapper:

```python
async def get_historical_bars_async(self, td_symbol, bar_size, days=30):
    return await asyncio.to_thread(self.get_historical_bars, td_symbol, bar_size, days)
```

---

### M6 — `_initialized` check in `__init__` is not thread-safe

**File:** `data/truedata_feed.py:93–101`

```python
def __init__(self) -> None:
    if self._initialized:   # ← read without lock
        return
    self._initialized = True
    ...
    self._lock = threading.Lock()
```

Two threads calling `TrueDataManager()` simultaneously both get the same instance (protected by `_class_lock` in `__new__`), but `__init__` runs again on each call. If two threads both pass `if self._initialized` before either sets it to True, `__init__` runs twice and `self._lock` is replaced with a new Lock mid-construction — any thread that already holds the old lock's reference is now on a dangling object.

In practice, `TrueDataManager()` is only called at module import time (`_td = TrueDataManager()`) so concurrent `__init__` calls are unlikely. But the `_class_lock` should cover `__init__` too, or `_initialized` should be set atomically.

**Fix:** Check-and-set `_initialized` while holding `_class_lock`:

```python
def __init__(self) -> None:
    with TrueDataManager._class_lock:
        if self._initialized:
            return
        self._initialized = True
        self._live = None
        ...
        self._lock = threading.Lock()
```

---

## Low Issues

### L1 — `_td_active` in `websocket_feed.py` is an unprotected module global

**File:** `scanner/websocket_feed.py:133–134, 144, 161`

```python
_td_active = False
...
global _td_active
if ok:
    _td_active = True
```

This is a bare write behind a `global` declaration with no lock. While CPython's GIL makes a single boolean assignment effectively atomic, `is_truedata_active()` and `subscribe_truedata()` read it without any synchronization. This is inconsistent with the rest of the codebase which uses locks for shared state, and will be a latent bug if ever run under a different Python implementation (PyPy, Jython).

**Fix:** Protect with the existing `_lock`, or use `threading.Event`.

---

### L2 — `_on_bar` stub silently discards 1-min bars — diagnostic data lost

**File:** `data/truedata_feed.py:299–301`

```python
def _dispatch_bar(self, bar_data) -> None:
    """1-min bar received — available for future bar-triggered strategies."""
    pass
```

1-min bars are being received by the WebSocket feed, consuming network and CPU, and silently discarded. There is no log entry. If the subscription is accidentally receiving bars when it shouldn't (e.g. wrong subscription flag), there is no way to detect this.

**Fix:** Add at minimum a debug-level counter:

```python
def _dispatch_bar(self, bar_data) -> None:
    logger.debug(f"TrueData 1min bar: {getattr(bar_data, 'symbol', '?')} @ {getattr(bar_data, 'close', '?')}")
```

---

### L3 — Bare `except: pass` in `_dispatch_tick()` inner blocks — silent failures

**File:** `data/truedata_feed.py:283–294`

```python
try:
    from scanner import websocket_feed
    with websocket_feed._lock:
        websocket_feed._tick_cache[sym] = ...
except Exception:
    pass   # ← completely silent
```

If `websocket_feed` is importable but `_lock` has been replaced (see M6) or `_tick_cache` is unexpectedly None, this fails silently on every tick. In a high-frequency tick stream this could mean thousands of silent failures per second.

**Fix:** Replace bare `pass` with `logger.debug(...)` so failures are detectable:

```python
except Exception as exc:
    logger.debug(f"TrueData tick cache update failed: {exc}")
```

---

### L4 — Credentials read at import time — `load_dotenv()` ordering dependency

**File:** `data/truedata_feed.py:20–21`

```python
_TRUEDATA_USER = os.getenv("TRUEDATA_USER", "")
_TRUEDATA_PASS = os.getenv("TRUEDATA_PASSWORD", "")
```

These are evaluated when the module is first imported. If anything imports `data.truedata_feed` before `settings.py` calls `load_dotenv()`, both will be empty strings. The error manifests much later when `connect_hist()` logs "credentials missing" — with no indication that import ordering was the root cause.

`settings.py` is imported early in `main.py` and calls `load_dotenv()`, so the current ordering is safe. But this is a fragile implicit dependency: any test or utility that imports `data.truedata_feed` directly without first importing `settings` will silently get empty credentials.

**Fix:** Load lazily inside the methods (also fixes H3). If module-level storage is preferred, add an assertion:

```python
# At end of module, after lazy load point in connect_hist/connect_live:
# No module-level reads of env vars — done lazily in connect_* methods
```

---

### L5 — Forward-reference string annotations unnecessary for Python 3.10+

**File:** `data/truedata_feed.py:125, 211, 260, 340, 345`

```python
def get_historical_bars(self, ...) -> "pd.DataFrame | None":
def get_ltp(self, td_symbol: str) -> "float | None":
```

The project requires Python ≥ 3.10. PEP 604 union types (`X | Y`) are valid natively in 3.10+. String-quoting the return type (`"pd.DataFrame | None"`) is only needed for forward references or Python < 3.10. These are not forward references — `pd` and `float` are both in scope.

**Fix:** Remove the quotes:

```python
def get_historical_bars(self, ...) -> pd.DataFrame | None:
def get_ltp(self, td_symbol: str) -> float | None:
```

---

### L6 — `fyers_to_td_symbol()` silently passes through unknown symbols

**File:** `data/truedata_feed.py:51–63`

```python
def fyers_to_td_symbol(fyers_sym: str) -> str:
    td = _FYERS_TO_TD.get(fyers_sym)
    if td:
        return td
    if ":" in fyers_sym:
        sym = fyers_sym.split(":", 1)[1]
        ...
        return sym
    return fyers_sym   # ← returns input unchanged if no rule matches
```

If an unknown Fyers symbol is passed (e.g. `NSE:MIDCPNIFTY-FUT` when only `NSE:MIDCPNIFTY-FUT` is in the map — actually it is, but consider any new symbol), the function returns the Fyers string unchanged. TrueData then receives `NSE:MIDCPNIFTY-FUT` verbatim, which is not a valid TrueData symbol, and returns an error or empty DataFrame.

The error surfaces as "TrueData: empty response" with no indication that a symbol mapping is missing.

**Fix:** Log a warning when the fallback path is taken:

```python
logger.debug(f"TrueData: no symbol mapping for '{fyers_sym}', using fallback '{sym}'")
```

---

### L7 — `is_active()` for Fyers WS tests object existence, not connectivity

**File:** `scanner/websocket_feed.py:127–128` (pre-existing, surfaced by review)

```python
def is_active():
    return _ws_client is not None
```

This returns `True` if `init()` was ever called successfully, even if the WebSocket has since disconnected, errored, or is in a reconnecting state. Callers that check `is_active()` before subscribing may proceed with a broken connection.

This is pre-existing code, but the parallel `is_truedata_active()` (line 160) has the same problem — it returns the `_td_active` flag set at connect time, not actual current connectivity.

---

## Severity Summary

| ID | Category | Severity | File | Line(s) |
|----|----------|----------|------|---------|
| C1 | Thread Safety / Data Race | **Critical** | `truedata_feed.py` | 105–123, 216–238 |
| C2 | Data Race / Crash | **Critical** | `truedata_feed.py` | 132–136, 183–186 |
| C3 | Reconnection Logic | **Critical** | `truedata_feed.py` | 105–178 |
| C4 | Resource Leak | **Critical** | `truedata_feed.py` | 222–245 |
| H1 | Rate Limiting | **High** | `data_fetcher.py` | 142–161 |
| H2 | Retry Logic | **High** | `truedata_feed.py` | 135–178 |
| H3 | Security | **High** | `truedata_feed.py` | 20–21 |
| H4 | Event Loop Blocking | **High** | `truedata_feed.py` | 275–297 |
| H5 | Security / Logging | **High** | `truedata_feed.py` | 121–122 |
| M1 | Resource Cleanup | Medium | `truedata_feed.py` | (missing) |
| M2 | Exception Handling / Validation | Medium | `truedata_feed.py` | 138 |
| M3 | Exception Handling / Validation | Medium | `truedata_feed.py` | 211 |
| M4 | Logging Quality | Medium | `truedata_feed.py` | 173, `data_fetcher.py` 157 |
| M5 | Async Correctness | Medium | `truedata_feed.py` | 125–178 |
| M6 | Thread Safety | Medium | `truedata_feed.py` | 93–101 |
| L1 | Data Race | Low | `websocket_feed.py` | 133–161 |
| L2 | Logging Quality | Low | `truedata_feed.py` | 299–301 |
| L3 | Exception Handling | Low | `truedata_feed.py` | 283–294 |
| L4 | Authentication | Low | `truedata_feed.py` | 20–21 |
| L5 | Type Safety | Low | `truedata_feed.py` | 125, 211, 260 |
| L6 | Logging Quality | Low | `truedata_feed.py` | 51–63 |
| L7 | Reconnection Logic | Low | `websocket_feed.py` | 127–128, 160 |

---

## Fix Priority Order

1. **C1** — Lock the entire connect sequence (prevents duplicate sessions)
2. **C2** — Snapshot `self._hist` under lock before use (prevents AttributeError crash)
3. **C4** — Assign `self._live` before `start_live_data()` (prevents WS resource leak)
4. **H3 + L4** — Move credential reads to inside methods (fixes both security and import ordering)
5. **H4** — Add tick dispatch queue (prevents tick drops under load)
6. **C3** — Reset `_hist_connected` on auth-class errors (enables re-auth after expiry)
7. **H1** — Add TrueData-specific rate limiter in `_get_historical_data_truedata()`
8. **H2** — Add 3-attempt retry with backoff in `get_historical_bars()`
9. **H5** — Sanitize `exc` before logging in `connect_hist()` / `connect_live()`
10. **M1** — Add `atexit.register(lambda: _td.disconnect())`
11. Remaining Medium and Low items in any order
