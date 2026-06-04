# Execution Reconciliation Report — CB6 Quantum
**Date:** 2026-05-30 | **Scope:** NSE / FTMO / GFT ticket mapping, fills, duplicate prevention

---

## Architecture Overview

CB6 runs three fully isolated execution engines:

| Engine | Broker | State File | Dedup Mechanism |
|--------|--------|-----------|-----------------|
| NSE | Fyers API | `data/nse_state.json` | `_live_alerted` + `_sb_daily_taken` sets |
| FTMO | MT5 (FTMO terminal) | `data/ftmo_10k/state.json` | `ForexWorker._dedup` dict + `1 trade at a time` gate |
| GFT | MT5 (GFT terminal) | `data/gft_5k/state.json` | `DuplicateGuard._seen` dict + `1 trade at a time` gate |

**Critical isolation:** FTMO and GFT run in **separate Python subprocesses** (not threads).
The MT5 C-extension state is process-global — running both accounts in one process would
cause terminal hijacking. `forex_main.py --profile ALL` correctly uses `subprocess.Popen`.

---

## Ticket Mapping

### FTMO

Order of operations after a signal fires:
1. `ftmo_open_trade(setup, lots)` → writes trade to state with `ticket=0` initially
2. `adapter.place_market_order()` → returns MT5 ticket number
3. `ftmo_update_ticket(trade_id, ticket)` → patches ticket into state
4. `ftmo_update_fill(trade_id, fill_price, sl, t1, t2, t3)` → patches actual fill price

If step 2 returns None (order failed), `ftmo_rollback(trade_id)` removes the state record
and decrements `daily_trades`. The state never shows a "ghost" open trade.

**Gap:** Between step 1 and step 3, the trade exists with `ticket=0`.
If the bot crashes in this window, the state shows an open trade that MT5 has no position for.
On restart, the engine would try to monitor a `ticket=0` trade indefinitely.

**Recommendation:** Add a startup reconciliation check: for any open trade with `ticket=0`
older than 60 seconds, verify with MT5 whether a position exists. If not, rollback.

### GFT

Identical pattern via `_update_ticket()` and `_update_fill()` in `gft_5k_2step.py`.
Same gap and same recommendation applies.

---

## Order State Consistency

### State file vs MT5 terminal

State files are the **source of truth** for CB6. MT5 terminal is the execution layer.
They can diverge if:
- MT5 order is placed but state write crashes (extremely rare — atomic writes)
- MT5 position closes server-side (SL hit) but bot was disconnected at that moment

**Existing sync mechanism:** The monitor loop (`_monitor_loop`) polls MT5 prices every 15s
and evaluates SL/TP logic. When a SL is hit, the bot closes via `close_position()` AND
updates state. If MT5 already closed server-side, `close_position()` fails gracefully
(ticket not found), but the state write still happens.

**Gap:** No explicit position reconciliation on startup. If the bot was offline when
a position was server-side closed by SL, the state will still show it as OPEN until
the next monitor tick detects the price-based SL condition.

---

## Fill Price Accuracy

Both FTMO and GFT use actual MT5 fill prices via `get_order_fill()`:
```python
fill_result = self._adapter.get_order_fill(ticket)
if fill_result:
    fill_px, sl, t1, t2, t3 = fill_result
    ftmo_update_fill(trade_id, fill_px, sl, t1, t2, t3, risk_usd)
```

**Result:** All P&L calculations use actual fill price, not signal price. Slippage is
captured correctly.

---

## Partial Fill Handling

FTMO and GFT both use 3-target partial exit logic:
- T1: close 1/3 of position, SL → break-even
- T2: close 1/3 of position
- T3: close remaining 1/3

`_remaining_lots()` in both engines calculates remaining lots based on `targets_hit` count.
Each partial close updates `state['capital']` and `state['daily_pnl']` incrementally.

**Gap:** If `close_position()` for T1 succeeds on MT5 but the state write fails
(extremely unlikely with atomic writes), the position size in state diverges from MT5.
No reconciliation logic exists for this scenario.

---

## Duplicate Execution Prevention

### FTMO (`forex_worker.py`)
```python
# _dedup is a per-symbol set of (date, direction, fvg_key)
dedup_k = (today, setup['direction'], fvg_key)
if dedup_k in self._dedup[symbol]:
    logger.info(f"FOREX {symbol}: dedup — already traded this zone today")
    continue
```

Additionally: `can_open_trade()` returns False if `len(open_trades) > 0`
(1 trade at a time rule). This is a hard state-level gate that prevents any second
entry regardless of dedup state.

**Limitation:** `_dedup` is in-memory. On process restart, all dedup history is cleared.
A restarted process could re-enter the same zone on the same day.

### GFT (`gft_5k_2step.py + DuplicateGuard`)
```python
# DuplicateGuard is in-memory, keyed by (date, direction, fvg_low_rounded)
if self._dedup.is_duplicate(symbol, direction, fvg_low):
    logger.debug(f"GFT 2-Step {symbol}: zone already traded today — skip")
    return
```

Same limitation: in-memory, clears on restart.

**Recommendation:** Persist the dedup set to a small JSON file on each `mark_seen()` call,
loaded on startup. This would prevent duplicate zone entries after process restart.

### NSE (`main.py`)
```python
# _live_alerted and _sb_daily_taken are module-level sets
# Also noted: race condition if two scanners run concurrently
```

See audit note: threading lock is missing on `_live_alerted`. This is a separate
Medium-priority issue not addressed in this hardening pass.

---

## Cross-Broker Isolation

| Check | Status |
|-------|--------|
| FTMO and GFT use separate Python processes | ✅ Correct |
| FTMO and GFT use separate MT5 terminals (terminal_path) | ⚠️ Depends on .env MT5_TERMINAL_FTMO / GFT config |
| FTMO and GFT use separate state files | ✅ Correct |
| FTMO and GFT use separate Telegram bots | ✅ Correct |
| NSE and Forex bots have no shared state | ✅ Correct |
| Account contamination guard in MT5Connector | ✅ Implemented — verifies login matches |

---

## Findings Summary

| Issue | Severity | Implemented? |
|-------|---------|--------------|
| FTMO/GFT separate processes for MT5 isolation | Required | ✅ Yes |
| Account contamination guard (login mismatch abort) | Critical | ✅ Yes |
| 1-trade-at-a-time gate | Critical | ✅ Yes |
| Atomic state writes (fsync + os.replace) | Critical | ✅ Yes |
| Rollback on MT5 order failure | Critical | ✅ Yes |
| In-memory dedup clears on restart | Medium | ❌ Not addressed |
| Startup reconciliation for ticket=0 trades | Medium | ❌ Not addressed |
| Server-side close not detected if offline | Medium | ❌ Not addressed |
| NSE _live_alerted threading lock | Medium | ❌ Not addressed in this pass |
