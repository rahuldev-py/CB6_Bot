# Disaster Recovery Report — CB6 Quantum
**Date:** 2026-05-30 | **Scope:** VPS reboot, MT5 disconnect, internet outage, JSON corruption

---

## Scenario 1: VPS Reboot / Process Kill

### What happens to state

State files are written atomically via `fsync + os.replace` (see `utils/state_io.py`).
The write sequence is:
1. Write to `.tmp` file
2. `f.flush()` + `os.fsync()` — forces OS to flush to disk
3. `os.replace(tmp, path)` — atomic rename, never leaves partial file

**Result:** State is either the pre-reboot version OR the fully-committed new version.
There is no partial-write corruption scenario from a crash.

### What happens to open trades

On restart, `load_state()` reads the last committed state. If a trade was open at time
of crash:
- It will still be in `open_trades` list
- The engine will resume monitoring it on next candle callback
- **Risk:** SL/TP may have been hit during the downtime window and not recorded

### Recommended recovery procedure

1. After restart, check MT5 terminal for actual open positions
2. Compare against `data/ftmo_10k/state.json` `open_trades`
3. If MT5 shows trade closed but state shows OPEN: run `/ftmo_exit` Telegram command to sync
4. If MT5 shows trade open but state shows CLOSED: investigate — this indicates a state write happened but MT5 order failed (rollback should have cleaned this up)

### Bot auto-restart

`forex_main.py --profile ALL` monitors child processes and restarts them with
exponential backoff (5→15→30→60→120→300s, max 10 restarts) after this hardening.

Use `watchdog.py` for the NSE bot: `python watchdog.py --attach`

---

## Scenario 2: MT5 Disconnect (internet blip, broker server restart)

### Detection

`MT5Connector.ensure_connected()` is called before every order placement:
```python
def ensure_connected(self, max_retries=3) -> bool:
    if self.is_connected(): return True
    for attempt in range(1, max_retries + 1):
        # shutdown → 2s sleep → reconnect
        time.sleep(10 * attempt)  # 10s, 20s, 30s between attempts
```

### What happens during disconnect

- Candle poll: `start_polling()` loop — if `get_price()` returns None, the candle is skipped
- Monitor loop: `_check_exits()` — if price fetch fails, exception is caught and logged, loop continues
- New entries: `ensure_connected()` aborts entry attempt if MT5 not available

### Gap identified

There is no Telegram alert when MT5 reconnect fails after all attempts. The engine
continues running but silently cannot trade. Operator may not know.

**Recommendation:** Add alert in `ensure_connected()` final failure path:
```python
if not success:
    _send("🔴 MT5 RECONNECT FAILED — engine running without live orders")
```

### Open position during disconnect

If MT5 disconnects while a trade is open, SL/TP is held by the broker's server
(stop orders are server-side). The position will close server-side even if the
bot is disconnected. On reconnect, the bot reconciles state vs MT5 positions.

---

## Scenario 3: Internet Outage

Functionally identical to MT5 disconnect above. Additionally:

- Yahoo Finance candles (paper mode): will fail with `requests.exceptions.ConnectionError`
  — caught in `start_polling()`, retried each 15s poll cycle
- Telegram messages: will fail silently (exception caught in `_send()`)
- State files: unaffected — local disk only

---

## Scenario 4: JSON State Corruption

### When can it happen

Theoretically: OS power loss between write and fsync. In practice: the `fsync` call
makes this extremely rare on modern hardware with write caching disabled.

More likely: manual edit that introduces invalid JSON.

### Detection

`load_state()` in both FTMO and GFT engines:
```python
try:
    state = load_json_locked(STATE_FILE, _DEFAULT_STATE.copy())
except Exception:
    return _DEFAULT_STATE.copy()  # fall back to defaults
```

`state_io.load_json_locked()` also catches `json.JSONDecodeError`:
```python
except (json.JSONDecodeError, OSError, ValueError):
    data = default.copy()  # seed from default, overwrite bad data on next save
```

### Recovery sequence

1. `load_state()` reads corrupt file → falls back to `_DEFAULT_STATE`
2. First trade attempt calls `can_open_trade()` → runs against default state (capital=$10,000)
3. **Risk:** Daily PnL counters reset to 0 → daily loss guard thinks today is clean

**Mitigation:** The `data/` directory is in `.gitignore`. Automated backups:

```python
# utils/state_io.py provides backup_json_dir()
from utils.state_io import backup_json_dir
backup_json_dir('data/ftmo_10k', backup_root='data/backups')
```

**Recommendation:** Add a daily backup cron that calls `backup_json_dir` for both
FTMO and GFT state directories.

---

## Summary Matrix

| Scenario | Data Safe? | Bot Resumes? | Operator Alert? | Gap |
|----------|-----------|--------------|-----------------|-----|
| VPS reboot | ✅ Yes (atomic writes) | ✅ Yes (backoff restart) | ✅ Telegram on restart | SL/TP missed during downtime |
| MT5 disconnect | ✅ Yes | ✅ Yes (ensure_connected) | ❌ No alert on final failure | No Telegram on persistent disconnect |
| Internet outage | ✅ Yes | ✅ Yes (retry each 15s) | ❌ Partial | Same as MT5 disconnect |
| JSON corruption | ⚠️ Fallback to defaults | ✅ Yes but with reset state | ❌ No alert | Daily PnL reset to 0 on fallback |

---

## Recommended Improvements (not yet implemented)

1. **MT5 persistent disconnect alert** — Telegram message when `ensure_connected()` fails all retries
2. **State corruption alert** — Telegram message when `load_state()` falls back to defaults
3. **Daily state backup** — scheduled `backup_json_dir` call (cron or on daily reset)
4. **Open position reconciliation on startup** — query MT5 for actual positions and cross-check state on boot
