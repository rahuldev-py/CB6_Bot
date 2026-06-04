# NSE Execution Trace Patch — CB6 Quantum
**Date:** 2026-06-01  
**Scope:** Add structured execution-trace logging to every gate in the NSE order path  
**Constraint:** No strategy logic changed, no thresholds changed, no guards bypassed

---

## Purpose

The current log output allows auditing *that* a setup was skipped but not always *why exactly* within each sub-function. When a setup DOES reach order placement and is rejected, there is no Telegram alert — the trader must check logs manually.

This patch adds:
1. A `_trace()` helper that emits a structured INFO log line with all required fields
2. One `_trace()` call at each of 9 named execution stages
3. A Telegram alert when an order is rejected by the broker (the silent failure identified in the audit)

---

## Trace Events and Locations

| Event | Trigger condition | File:Function | Line |
|-------|-------------------|---------------|------|
| `SIGNAL_CREATED` | `scan_silver_bullet()` returns a non-None setup | `main.py:_nifty_live_scanner()` | After setup returned |
| `SIGNAL_VALIDATED` | Setup passes score gate + pattern library gate | `main.py:_nifty_live_scanner()` | After `should_trade` check |
| `RISK_APPROVED` | `_apply_live_entry()` confirms LTP in FVG | `main.py:_nifty_live_scanner()` | After setup_live is not None |
| `ROUTER_APPROVED` | `route_trade()` returns a routing decision | `main.py:_nifty_live_scanner()` | After routing computed |
| `ORDER_BUILD_STARTED` | `place_futures_trade()` / `place_silver_bullet_trade()` called | `main.py:_nifty_live_scanner()` | Before order_manager call |
| `ORDER_SENT` | `fyers.place_order()` called inside order_manager | `trader/order_manager.py:_fyers_order()` | Before API call |
| `ORDER_ACCEPTED` | Fyers API returns success response | `trader/order_manager.py:_fyers_order()` | On success response |
| `ORDER_REJECTED` | Fyers API returns error response | `trader/order_manager.py:_fyers_order()` | On error response + Telegram alert |
| `EXECUTION_BLOCKED` | Any gate in the chain blocks the setup | Every skip point in scanner/main | At each `continue` |

---

## Patch Implementation

### Part 1 — `_trace()` helper in `main.py`

Added near the top of the file (after imports), available to all scanner functions:

```python
def _trace(event: str, symbol: str, **kwargs) -> None:
    """
    Emit a structured execution-trace log line.
    Fields: event, symbol + any keyword args (direction, score, reason, mode, tf, ts).
    All trace lines are tagged [TRACE] for easy grep.
    """
    import pytz as _ptz
    ts = datetime.now(_ptz.timezone("Asia/Kolkata")).strftime("%H:%M:%S IST")
    parts = [f"[TRACE] {event} | sym={symbol} | ts={ts}"]
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    logger.info(" | ".join(parts))
```

### Part 2 — `ORDER_REJECTED` Telegram alert in `order_manager.py`

The only place where a live order failure must reach the trader immediately. No structural change — just adds a Telegram call inside the existing error-handling path.

```python
# In _fyers_order(), after logging the rejection:
if err_code == -50 or "algo" in err_msg.lower():
    try:
        from utils.telegram_alerts import send_message
        send_message(
            f"🚨 <b>ORDER REJECTED (code {err_code})</b>\n"
            f"Symbol: {symbol}\n"
            f"Reason: {err_msg}\n"
            f"Fix: Enable Algo Trading on Fyers app PBM0J0M29C-100"
        )
    except Exception:
        pass
```

---

## Files Changed

| File | Change | Lines affected |
|------|--------|---------------|
| `main.py` | Add `_trace()` helper + 7 trace call sites | +40 lines |
| `trader/order_manager.py` | Add Telegram alert on ORDER_REJECTED (code -50 / algo error) | +12 lines |

---

## Expected Log Output After Patch

When a setup is found and successfully placed:
```
[TRACE] SIGNAL_CREATED | sym=NSE:BANKNIFTY26JUNFUT | ts=10:32:15 IST | dir=BEARISH | score=14.5 | tf=3min | mode=LEGACY
[TRACE] SIGNAL_VALIDATED | sym=NSE:BANKNIFTY26JUNFUT | ts=10:32:15 IST | dir=BEARISH | score=14.5 | should_trade=True | decision=CONFIRMED_56PCT
[TRACE] RISK_APPROVED | sym=NSE:BANKNIFTY26JUNFUT | ts=10:32:16 IST | ltp=55180.0 | fvg_low=55140.0 | fvg_high=55210.0
[TRACE] ROUTER_APPROVED | sym=NSE:BANKNIFTY26JUNFUT | ts=10:32:16 IST | route=FUTURES | reason=margin_ok
[TRACE] ORDER_BUILD_STARTED | sym=NSE:BANKNIFTY26JUNFUT | ts=10:32:16 IST | dir=BEARISH | entry=55170.0 | sl=55250.0 | t2=54910.0
[TRACE] ORDER_SENT | sym=NSE:BANKNIFTY26JUNFUT | ts=10:32:16 IST | qty=30 | order_type=LIMIT | price=55170.0
[TRACE] ORDER_ACCEPTED | sym=NSE:BANKNIFTY26JUNFUT | ts=10:32:17 IST | order_id=119305001234567
```

When broker rejects:
```
[TRACE] ORDER_SENT | sym=NSE:FINNIFTY26JUNFUT | ts=15:07:25 IST | qty=60 | order_type=LIMIT | price=25870.0
ERROR | Order rejected: {'code': -50, 'message': 'Algo orders not allowed...'}
[TRACE] ORDER_REJECTED | sym=NSE:FINNIFTY26JUNFUT | ts=15:07:25 IST | code=-50 | reason=Algo_orders_not_allowed | mode=LEGACY
```
Telegram alert also fires immediately on ORDER_REJECTED.

When a gate blocks a setup mid-chain:
```
[TRACE] SIGNAL_CREATED | sym=NSE:NIFTY26JUNFUT | ts=13:45:20 IST | dir=BULLISH | score=11.0 | tf=3min | mode=LEGACY
[TRACE] EXECUTION_BLOCKED | sym=NSE:NIFTY26JUNFUT | ts=13:45:20 IST | gate=SCORE | reason=score_11.0_lt_gate_12 | mode=LEGACY
```

---

## Rollback

All trace calls are inside `try/except Exception: pass` blocks. Removing the trace calls restores the exact prior behaviour. The ORDER_REJECTED Telegram alert is also in a try/except — its removal is the only change that affects user-visible output.

To fully rollback: remove the `_trace()` function and its 7 call sites from `main.py`, and remove the Telegram alert block from `order_manager.py:_fyers_order()`.
