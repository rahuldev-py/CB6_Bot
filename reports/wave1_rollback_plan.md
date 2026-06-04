# CB6 Quantum — Wave 1 Safety Guards: Rollback Plan

**Date applied:** 2026-05-24  
**Scope:** 9 files modified, 2 new files created  
**Test coverage:** 16/16 passing (tests/test_wave1_safety_guards.py)

---

## Hard Rollback (revert everything)

CB6 is not in a git repository. Use file-level backup copies created before the session:

```powershell
# 1. Stop all engines immediately
# NSE:
taskkill /F /IM python.exe /T
# Or send Telegram: /stop

# 2. Restore from the pre-session snapshot
# (Located in CB6_PRE_LIVE_RESUME_20260523/ — the last-known-good state)
Copy-Item "C:\cb6_bot\CB6_PRE_LIVE_RESUME_20260523\*" "C:\cb6_bot\" -Recurse -Force

# 3. Re-verify syntax of restored files
python -m py_compile trader/paper_trader.py
python -m py_compile forex_engine/forex_worker.py
python -m py_compile utils/state_io.py
```

---

## Surgical Per-File Rollback

If only one requirement is causing issues, revert only that file:

### REQ-1 — utils/state_io.py (state_lock CM)
Risk: low — only additive. Existing `file_lock`, `load_json_locked`, `save_json_locked` unchanged.  
Rollback: Remove lines between `# ── Atomic read-modify-write context manager` and `# ── Convenience helpers`.

### REQ-2 — trader/paper_trader.py (RLock + state guards)
Symptom to watch: Deadlock (bot freezes, all threads hang on lock acquisition).  
Rollback steps:
1. Change `_state_lock = threading.RLock()` back to `threading.Lock()`
2. Remove `_state_lock.acquire()` / `_state_lock.release()` pairs from:
   - `open_paper_trade()`
   - `update_paper_trades()`
   - `close_paper_trade_by_id()`
   - `handle_target_hit_by_id()`
   - `register_option_strike()`
3. Restore `save_state()` to use `with _state_lock:` internally

### REQ-2 — forex_engine/forex_worker.py (_entry_lock in monitor)
Symptom: Monitor loop blocks for >5s per cycle (MT5 order in progress).  
Rollback: Change `with self._entry_lock:` back to `if True:` in `_monitor_loop()`.

### REQ-3 — Emergency stop wiring
Symptom: None expected. Very low risk (just an `if os.path.exists()` check).  
Rollback: Remove the 4-line `if is_emergency_stop_active():` blocks from:
- `main.py` `_trade_monitor()`
- `forex_engine/forex_worker.py` `_monitor_loop()`
- `forex_engine/prop_firms/gft/gft_5k_2step.py` `_monitor_loop()` and `_run()`

### REQ-4 — Daily loss enforcement (paper_trader.can_take_trade)
Symptom: Bot correctly blocks at 2% daily loss (desired behavior — unlikely to revert).  
Rollback: Remove the 8-line `# REQ-4` block from `can_take_trade()`. Revert import to remove `MAX_DAILY_LOSS_PCT`.

### REQ-4 — FTMO daily reset in forex_worker._run_scan
Symptom: None expected.  
Rollback: Revert `with self._entry_lock:` block to remove `ftmo_reset_daily` call and restore `fresh_daily = fresh.get('daily_pnl', 0) if fresh_date == today else 0.0`.

### REQ-5 — Silent exception logging
Symptom: More log noise (desired behavior — unlikely to revert).  
Rollback: Change `logger.exception(...)` / `logger.warning(..., exc_info=True)` back to `pass` in affected files.

### REQ-6 — Live MT5 balance for lot sizing
Symptom: If MT5 `get_equity()` is slow, it adds latency to every scan (max +2s).  
Rollback: Remove the 10-line `# REQ-6` block from both `forex_worker._run_scan()` and `gft_5k_2step._run()`. Restore `calc_lot_size(symbol, state.get('capital', ...), ...)`.

---

## Anomaly Detection Checklist

Run these checks after deploying Wave 1 to confirm stability:

```powershell
# 1. Confirm all tests still pass
python -m pytest tests/test_wave1_safety_guards.py -v

# 2. Confirm no lock-related errors in logs (first 30 min after deploy)
Select-String -Path "logs\cb6_$(Get-Date -f yyyyMMdd).log" -Pattern "TimeoutError|state_lock|deadlock" | Select-Object -Last 20

# 3. Confirm emergency stop flag is NOT present at startup
Test-Path "data\EMERGENCY_STOP.flag"   # should return False

# 4. Confirm daily loss guard fires correctly in paper mode
# Send /portfolio via Telegram — check "Available: Rs X" matches expectations

# 5. Confirm FTMO lot sizes are using live MT5 equity (look for log line):
Select-String -Path "logs\cb6_$(Get-Date -f yyyyMMdd).log" -Pattern "lot sizing on live MT5 equity"
```

---

## Files Changed — Wave 1

| File | Change | Requirement |
|---|---|---|
| `utils/state_io.py` | Added `state_lock()` CM, added `fsync` to `save_json_locked` | REQ-1 |
| `utils/emergency_stop.py` | **NEW FILE** — shared emergency stop utility | REQ-3 |
| `trader/paper_trader.py` | RLock, acquire/finally in 5 functions, daily loss cap, import fix | REQ-2, REQ-4 |
| `forex_engine/forex_worker.py` | Emergency stop in monitor, _entry_lock in monitor, daily reset, live MT5 balance | REQ-2, REQ-3, REQ-4, REQ-6 |
| `forex_engine/prop_firms/gft/gft_5k_2step.py` | Emergency stop in scan+monitor, live MT5 balance, heartbeat logging | REQ-2, REQ-3, REQ-5, REQ-6 |
| `forex_engine/prop_firms/ftmo/ftmo_state.py` | Exported `reset_daily_if_needed` and `save_state` as public aliases | REQ-4 |
| `forex_engine/risk/emergency_kill_switch.py` | Added `logger.warning(..., exc_info=True)` to silent excepts | REQ-5 |
| `crypto_engine/crypto_paper_trader.py` | Added `os.fsync` to save_state, changed `logger.error` to `logger.exception` | REQ-2, REQ-5 |
| `data/bot_memory.py` | Changed raw `open()` to `load_json_locked`/`save_json_locked` | REQ-5 |
| `main.py` | Added emergency stop check to `_trade_monitor()` | REQ-3 |
| `tests/test_wave1_safety_guards.py` | **NEW FILE** — 16-test verification suite | Verification |
