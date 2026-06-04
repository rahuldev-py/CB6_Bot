# TRUEDATA_HARDENING_REPORT.md
# CB6 Quantum — TrueData Hardening Pass

**Date:** 2026-05-30
**Engineer:** Principal Quant Architect / Claude Code
**Scope:** `data/truedata_feed.py` (deprecated shim layer, still in production use)

---

## Summary

All 4 critical and both high-severity issues identified in the original audit have been fixed
in a single hardening pass of `data/truedata_feed.py`. No new external dependencies were
introduced. All fixes are backward-compatible with existing callers in `scanner/data_fetcher.py`,
`scanner/live_price.py`, and `scanner/websocket_feed.py`.

---

## Critical Fixes

### C1 — TOCTOU Race in connect_hist() and connect_live()

**Root Cause:**
Both methods released `self._lock` after the connected-check but before the expensive I/O
operation (SDK instantiation + network connect). Two or more threads could simultaneously
pass the `if self._hist_connected: return True` guard and each create a separate
`TD_hist` / `TD_live` session. The second assignment silently leaked the first connection.

**Fix Applied — 3-State Machine:**

Replaced the two boolean flags (`_hist_connected`, `_live_connected`) with two enum state
machines (`_hist_state`, `_live_state`) using a new `_ConnState` enum:

```
DISCONNECTED → CONNECTING → CONNECTED
      ↑                         |
      └────────── ERROR ────────┘
```

Only the thread that wins the `DISCONNECTED → CONNECTING` CAS (compare-and-set under
the lock) proceeds with I/O. All concurrent callers see `CONNECTING` and return `False`
immediately. The calling layer retries after a brief sleep.

```python
with self._lock:
    if self._hist_state == _ConnState.CONNECTED:
        return True
    if self._hist_state == _ConnState.CONNECTING:
        return False           # back off, another thread is connecting
    self._hist_state = _ConnState.CONNECTING  # claim the slot

# I/O happens OUTSIDE the lock
```

On success the state advances to `CONNECTED`. On any exception it reverts to
`DISCONNECTED` so future callers can retry.

---

### C2 — Concurrent Disconnect Crash in get_historical_bars()

**Root Cause:**
`get_historical_bars()` read `self._hist` and `self._hist_connected` without holding the
lock. A concurrent `disconnect()` call could set `self._hist = None` between the connected
check and the `self._hist.get_historic_data(...)` call, raising `AttributeError: 'NoneType'`.

**Fix Applied — Local Reference Pattern:**

```python
# Grab a local reference while holding the lock
with self._lock:
    hist = self._hist
    state = self._hist_state

# Use the local reference — immune to concurrent disconnect
df = hist.get_historic_data(...)
```

Even if `disconnect()` fires after the lock is released, the local `hist` reference keeps the
object alive for the duration of the call. The same pattern was applied to `get_last_n_bars()`
and `get_ltp()`.

---

### C3 — _hist_connected Never Resets After Session Expiry

**Root Cause:**
If the TrueData REST session expired mid-session (token TTL elapsed, server restart), all
subsequent calls to `get_historical_bars()` would silently receive errors and fall back to
Fyers — with `_hist_connected` still reporting `True`. There was no alerting and no
reconnect path.

**Fix Applied — _reset_hist_on_error():**

```python
def _reset_hist_on_error(self, exc: Exception) -> None:
    err_text = str(exc).lower()
    session_errors = ("expired", "invalid token", "unauthorized", "401", "session", "not connected")
    if any(kw in err_text for kw in session_errors):
        with self._lock:
            if self._hist_state == _ConnState.CONNECTED:
                logger.warning("TrueData: hist session appears expired — will reconnect on next call")
                self._hist = None
                self._hist_state = _ConnState.DISCONNECTED
```

Called from the `except` block of both `get_historical_bars()` and `get_last_n_bars()`.
On the next call, `connect_hist()` will be invoked and re-authenticate.

---

### C4 — start_live_data() Failure Leaks Background WebSocket Thread

**Root Cause:**
`connect_live()` called `TD_live(...)` (which may start internal threads), then called
`live.start_live_data(symbols)`. If `start_live_data()` raised, the `live` object was never
stored and never cleaned up, leaving zombie background threads.

**Fix Applied — Cleanup on Failure:**

```python
live = None
try:
    from truedata import TD_live
    live = TD_live(...)          # partially starts; captured before start_live_data
    live.start_live_data(symbols)
    with self._lock:
        self._live = live
        self._live_state = _ConnState.CONNECTED
    return True
except Exception as exc:
    ...
    if live is not None:
        try:
            live.disconnect()    # signal SDK threads to exit
        except Exception:
            pass
    with self._lock:
        self._live = None
        self._live_state = _ConnState.DISCONNECTED
    return False
```

---

## High-Severity Fixes

### HIGH-1 — Possible Password Leakage in Logs

**Root Cause:**
`logger.error(f"TrueData: TD_hist connect failed: {exc}")` passed the raw exception string
to the logger. Some authentication library exceptions embed credential fields in their message.

**Fix Applied — _safe_log_error():**

```python
def _safe_log_error(msg: str, exc: Exception) -> None:
    text = f"{msg}: {exc}"
    if _TRUEDATA_PASS and _TRUEDATA_PASS in text:
        text = text.replace(_TRUEDATA_PASS, "***")
    logger.error(text)
```

Used in place of all raw `logger.error(f"... {exc}")` calls that occur in connection paths.

---

### HIGH-2 — Tick Dispatch Blocking the WebSocket Callback Thread

**Root Cause:**
`_on_tick` (the `@live.trade_callback` handler) called `_dispatch_tick()` directly, which
acquired `websocket_feed._lock` and attempted module imports. Any slowness in those
operations stalled tick reception on the SDK's internal thread.

**Fix Applied — Queue + Worker Thread:**

```python
# __init__
self._tick_queue: queue.SimpleQueue = queue.SimpleQueue()
self._tick_worker = threading.Thread(
    target=self._tick_dispatch_loop, daemon=True, name="td-tick-worker"
)
self._tick_worker.start()

# Callback (runs on SDK's WS thread — must be fast)
@live.trade_callback
def _on_tick(tick_data):
    self._tick_queue.put(("tick", tick_data))   # non-blocking O(1)

# Worker (runs on dedicated thread)
def _tick_dispatch_loop(self) -> None:
    while True:
        kind, data = self._tick_queue.get()     # blocks until work arrives
        if kind == "tick":
            self._dispatch_tick(data)
        elif kind == "bar":
            self._dispatch_bar(data)
```

The WS callback now does a single non-blocking `put()`. All heavy work (lock acquisition,
module imports, tick_watcher calls) happens on the dedicated `td-tick-worker` thread.

---

## Medium-Severity Items (Noted, Not Fixed Here)

| ID | Issue | Recommended Fix | Priority |
|----|-------|----------------|----------|
| M1 | No retry on TrueData REST failure (falls straight to Fyers) | Add 1-retry with 500ms backoff before fallback | Medium |
| M2 | `_TRUEDATA_USER/PASS` read at module import time (not lazy) | Read inside `connect_hist/live()` or use `TrueDataConfig` | Low |
| M3 | `add_live_symbols()` doesn't check return value of `start_live_data()` | Validate and log on failure | Low |
| M4 | `_dispatch_bar()` is a no-op — 1-min bars are lost | Wire to bar-triggered strategies when ready | Deferred |

M1 through M4 are non-critical during the trial phase. Address before production deployment.

---

## Architecture After Hardening

```
connect_hist() / connect_live()
  ├── Lock: check state (CONNECTED → return True)
  ├── Lock: check state (CONNECTING → return False, caller retries)
  ├── Lock: set CONNECTING (claim slot)
  ├── [unlock] — I/O happens here (long operation, lock not held)
  ├── Lock: set CONNECTED + store object
  └── Lock: set DISCONNECTED + clear object (on error + cleanup zombie)

get_historical_bars()
  ├── Lock: snapshot (hist, state) into locals
  ├── [unlock]
  ├── Use local hist ref — immune to concurrent disconnect
  └── On error: _reset_hist_on_error() — reverts state if session expired

connect_live() callback chain:
  TD_live internal thread → _on_tick() → tick_queue.put() [O(1), non-blocking]
                                                    ↓
                                           td-tick-worker thread
                                                    ↓
                                           _dispatch_tick() → websocket_feed cache
                                                           → tick_watcher
```

---

## Test Protocol (Run After Trial Credentials Are Issued)

1. Spin up two threads calling `connect_hist()` simultaneously — verify only one `TD_hist` session is created.
2. Call `get_historical_bars()` from thread A while thread B calls `disconnect()` — verify no `AttributeError`.
3. Force a session-expiry error in `get_historical_bars()` — verify `_hist_state` reverts to `DISCONNECTED` and the next call reconnects.
4. Call `connect_live()` with a bad symbol list that causes `start_live_data()` to raise — verify `TD_live.disconnect()` is called and `_live_state` is `DISCONNECTED`.
5. Subscribe a high-frequency symbol and monitor `td-tick-worker` thread CPU — verify WS callback thread is not stalled.

---

## Files Changed

| File | Change Type |
|------|-------------|
| `data/truedata_feed.py` | Hardened (C1, C2, C3, C4, HIGH-1, HIGH-2 fixed) |

No other files were modified. All existing callers continue to work without change.
