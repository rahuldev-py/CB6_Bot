# TRUEDATA_WS_REVIEW.md
# CB6 Quantum — WebSocket Architecture Review

**Date:** 2026-05-30
**Engineer:** Principal Quant Architect / Claude Code
**Scope:** `data/truedata_feed.py` (shim), `provider/truedata/websocket_client.py` (modern async client)

---

## Executive Summary

CB6 Quantum has **two parallel WebSocket implementations** for TrueData:

| Layer | File | Status | Architecture |
|-------|------|--------|--------------|
| **Shim (legacy)** | `data/truedata_feed.py` | Active in production | Sync, `truedata` SDK wrapping `TD_live` |
| **Modern** | `provider/truedata/websocket_client.py` | Built, not wired | Async, direct WebSocket via `websockets` library |

The shim is used today. The modern client is trial-ready but not connected to CB6's scanner or tick-watcher.

---

## Part 1 — Shim Layer (data/truedata_feed.py)

### Before Hardening: Was Processing on the WebSocket Thread?

**YES.** The `@live.trade_callback` decorator registerd `_dispatch_tick()` as a synchronous
callback executed directly on the `TD_live` SDK's internal WebSocket receive thread.

`_dispatch_tick()` performed:
- A module import (`from scanner import websocket_feed`)
- A lock acquisition (`with websocket_feed._lock`)
- A second module import (`from core.tick_watcher import get_watcher`)
- A virtual method call (`get_watcher().on_tick(sym, ltp)`)

Any latency in these operations (GIL contention, lock congestion, slow imports) would cause
the SDK's receive buffer to back up, resulting in dropped ticks.

### After Hardening: Queue Architecture

**Fixed.** The callback now does a single O(1) non-blocking `queue.SimpleQueue.put()`:

```
TD_live internal WS thread
        │
        ▼  (O(1) non-blocking put)
  ┌─────────────┐
  │  tick_queue │   queue.SimpleQueue — unbounded, thread-safe
  │  (FIFO)     │
  └─────────────┘
        │
        ▼  (blocking get — dedicated thread)
  td-tick-worker thread
        │
        ├──▶ websocket_feed._tick_cache update (lock)
        └──▶ core.tick_watcher.on_tick()
```

**Can ticks be dropped?**

With `queue.SimpleQueue` (unbounded) and a dedicated worker thread, ticks are NOT dropped
unless the process runs out of memory. The queue will grow if the worker falls behind;
memory pressure is the only failure mode. For CB6's 4-symbol subscription (NIFTY, BANKNIFTY,
FINNIFTY, MIDCPNIFTY), tick throughput is low enough that this is not a practical concern.

**Can slow callbacks block the feed?**

**No.** After the hardening fix, the WS callback thread is only responsible for enqueue.
Slow `tick_watcher` callbacks only delay the worker thread's processing, not tick reception.

---

## Part 2 — Modern Async Client (provider/truedata/websocket_client.py)

### Architecture

```
TrueDataWebSocketClient.connect()
        │
        ├── _recv_task  (asyncio.Task: td_recv)
        │       │
        │       ├── recv() — blocks on WS frame
        │       ├── _handle_message()
        │       │       ├── _dispatch_tick()  ← calls on_tick callback
        │       │       └── _dispatch_bar()   ← calls on_bar callback
        │       └── auto-reconnect with exponential backoff (1s → 60s)
        │
        └── _heartbeat_task (asyncio.Task: td_heartbeat)
                └── 30s ping {"method": "heartbeat"}
```

### Is Processing on the WebSocket Thread?

**Partially.** The modern client is pure async. Both `_dispatch_tick()` and `_dispatch_bar()`
are called directly from `_recv_loop()` (the same asyncio coroutine that reads from the socket).

This means:
- A slow `on_tick` callback that does CPU work will delay the next `recv()`.
- An `on_tick` callback that blocks (e.g., acquires a threading lock) will stall the entire
  event loop if called from a sync context bridged into async.

### Can Ticks Be Dropped?

**Potentially, yes.** If `on_tick` is slow, `recv()` is delayed. The WebSocket server
may disconnect idle clients or fill the TCP receive buffer, causing the OS to drop frames
before they reach the application. This depends on TrueData's server timeout behavior —
unknown until trial verification.

### Can Slow Callbacks Block Feed?

**YES** — in the current design. The docstring says callbacks "should be non-blocking (or
wrapped in `asyncio.create_task`)" but this is advisory, not enforced.

### Recommended Refactor for Production

Wrap the callback invocation in `asyncio.create_task()` to decouple from the recv loop:

```python
# Current (blocks recv loop if on_tick is slow):
if self.on_tick:
    self.on_tick(tick)

# Recommended (non-blocking):
if self.on_tick:
    asyncio.create_task(_safe_callback(self.on_tick, tick))

async def _safe_callback(fn, arg):
    try:
        result = fn(arg)
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.warning("on_tick callback raised: %s", exc)
```

For the 4-symbol CB6 use case this is not urgent, but is required for production scale
(e.g., when subscribing to 50+ option strikes for OI monitoring).

---

## Part 3 — Current vs. Active Feed

| Feed | Active Today | Architecture | Thread Safety | Tick Drop Risk |
|------|-------------|--------------|---------------|----------------|
| Fyers WebSocket (`scanner/websocket_feed.py`) | YES (primary) | Sync callback → `_tick_cache` | Lock around cache | Low |
| TrueData Shim (`data/truedata_feed.py`) | NO (code exists, not wired in `main.py`) | Queue + worker thread (post-hardening) | Queue + lock | Very low |
| TrueData Modern (`provider/truedata/websocket_client.py`) | NO (trial-ready) | Async recv loop, callbacks inline | asyncio.Lock | Medium (needs `create_task` fix) |

### Why init_truedata() Is Not Called from main.py

`scanner/websocket_feed.py` exposes `init_truedata()` but `main.py` only calls `init()` (Fyers).
This is intentional — TrueData live feed requires trial credential verification before
activation. The wiring is complete; enabling requires one line change in `main.py`:

```python
# Current (Fyers only):
websocket_feed.init(access_token, client_id)

# After TrueData trial passes:
if os.getenv("TRUEDATA_USER"):
    websocket_feed.init_truedata(symbols)
else:
    websocket_feed.init(access_token, client_id)
```

---

## Findings Summary

| # | Finding | Severity | Fixed? |
|---|---------|----------|--------|
| WS-1 | Shim dispatched ticks on SDK callback thread | HIGH | YES (queue + worker) |
| WS-2 | Modern async client calls on_tick inline in recv loop | MEDIUM | NO (deferred — use create_task) |
| WS-3 | init_truedata() never called from main.py | INFO | By design (pending trial) |
| WS-4 | Fyers WS is sync; TrueData WS is async — mixing them requires bridge | MEDIUM | N/A (not mixed yet) |
| WS-5 | No backpressure signal from tick queue to WS layer | LOW | Acceptable for 4 symbols |

---

## Recommendations

**Before Trial:**
1. Shim layer is now safe. The queue architecture is production-grade for 4 symbols.
2. Note WS-2 (async client inline dispatch) in trial test plan — observe if on_tick latency
   impacts tick reception under load.

**After Trial (if purchase decision is YES):**
1. Migrate from shim (`data/truedata_feed.py`) to modern client (`provider/truedata/websocket_client.py`).
2. Apply `asyncio.create_task()` wrapper to decouple on_tick from recv loop.
3. Wire `init_truedata()` into `main.py` behind an env-var gate.
4. Decommission Fyers WebSocket as fallback (or keep as secondary for redundancy).
