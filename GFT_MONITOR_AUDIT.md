# GFT Monitor Loop Timing Audit
**Date:** 2026-05-30 | **File:** `forex_engine/prop_firms/gft/gft_5k_2step.py`

---

## Issue Found

`_monitor_loop()` had `time.sleep(30)` in two places:

```python
# BEFORE — both sleeps were 30s
def _monitor_loop(self):
    while self._running:
        if is_emergency_stop_active():
            ...
            time.sleep(30)   # ← emergency stop path: 30s
            continue
        for sym in _P['enabled_symbols']:
            ...
        time.sleep(30)       # ← normal path: 30s
```

**Impact:** SL/TP hits, drawdown checks, and emergency stop could go undetected for
up to 30 seconds. With GFT's $200/day loss limit and $500 total DD limit, a bad trade
can blow through internal guards in seconds on fast-moving markets like USOIL.

**Contradiction with spec:** CLAUDE.md explicitly states:
> "GFT poll speed: Must be 15s (not 30s/60s) for simultaneous FTMO+GFT entries"

The candle poll was already correct at 15s (`poll = 60 if self._paper else 15`),
but the exit monitor was running at half that speed.

---

## Fix Applied

Both `time.sleep(30)` calls changed to `time.sleep(15)`:

```python
# AFTER — 15s everywhere
def _monitor_loop(self):
    while self._running:
        if is_emergency_stop_active():
            logger.warning(
                "EMERGENCY_STOP.flag active — GFT 2-Step monitor cycle skipped"
            )
            time.sleep(15)   # ← was 30
            continue
        for sym in _P['enabled_symbols']:
            try:
                events = _check_exits(self._connector, sym)
                for ev in events:
                    self._handle_exit(sym, ev)
            except Exception as e:
                logger.error(f"GFT 2-Step monitor ({sym}): {e}")
        time.sleep(15)  # 15s matches GFT candle poll — ensures SL/TP/drawdown/emergency-stop checked every 15s
```

---

## Checks Verified Still Working After Fix

| Check | Code location | Status |
|-------|--------------|--------|
| SL hit detection | `_check_exits()` → called every 15s | ✅ |
| T1/T2/T3 target detection | `_check_exits()` → called every 15s | ✅ |
| MAE exit (85% SL) | `_check_exits()` → called every 15s | ✅ |
| Time exit (2hr no progress) | `_check_exits()` → called every 15s | ✅ |
| Emergency stop flag | `is_emergency_stop_active()` at loop top | ✅ |
| GFT daily loss guard | `_check_exits()` calls internal risk guards | ✅ |
| GFT total DD guard | Evaluated before every `_run()` candle | ✅ |
| MT5 close on SL/TP | `_handle_exit()` → `_connector.close_position()` | ✅ |
| BE trigger (SL→entry) | `_handle_exit()` → `_connector.modify_sl()` | ✅ |
| Heartbeat | Separate `_heartbeat_loop` thread — unaffected | ✅ |

---

## Thread Architecture (unchanged)

```
GFT2StepWorker.run()
├── Thread: GFT2Monitor    → _monitor_loop()    ← 15s cycle (fixed)
├── Thread: GFT2Heartbeat  → _heartbeat_loop()  ← 60s heartbeat write
└── Main:   candle polling → on_closed_candle() ← 15s poll in live mode
```

All three threads are daemon threads — they exit when the main process exits.
No thread coordination issues introduced by timing change.

---

## Status: ✅ FIXED — monitor loop now matches 15s candle poll cycle
