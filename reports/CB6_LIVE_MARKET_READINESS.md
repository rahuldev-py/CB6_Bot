# CB6 Quantum — Live Market Readiness Report
**Date:** 2026-06-01  
**Scope:** Pre-session verification before Monday live NSE open (09:15 IST)  
**Method:** Code audit + log analysis + system state review

---

## Checklist — System Preconditions

### 1. Fyers Token Fresh

| Check | Method | Status |
|-------|--------|--------|
| Token issued today (IST) | `is_token_fresh()` in main.py checks JWT `iat` claim | ✓ Confirmed from logs: token OK |
| OAuth refresh available | `python auto_token.py` → refreshes token then auto-launches | ✓ Implemented |
| 30-min refresh buffer | main.py refuses to start scanner if token expires within 30 min | ✓ Coded |

**Action before session:** Run `python auto_token.py` after 08:45 IST. Do not run main.py directly.

---

### 2. TrueData Connection

| Check | Expected | Evidence |
|-------|----------|----------|
| Historical API connected | `TrueData: historical connection established` in log | ✓ Confirmed 2026-06-01 |
| NIFTY bars fetchable | `TrueData: 127 bars fetched for NIFTY*` | ✓ Confirmed |
| BANKNIFTY bars fetchable | `TrueData: 125 bars fetched for BANKNIFTY*` | ✓ Confirmed |
| FINNIFTY bars fetchable | `TrueData: 74 bars fetched for FINNIFTY*` | ✓ Confirmed (lower count — expected) |
| MIDCPNIFTY bars fetchable | `TrueData: 126 bars fetched for MIDCPNIFTY*` | ✓ Confirmed |
| 1m FINNIFTY block active | `_guard_finnifty_1m()` present in truedata_feed.py | ✓ Confirmed |

**Action:** If any symbol returns 0 bars, check credentials in .env (`TRUEDATA_USER`, `TRUEDATA_PASSWORD`, `TRUEDATA_WS_PORT=8086`).

---

### 3. Live WebSocket — Tick Arrival

| Check | How to verify | Status |
|-------|--------------|--------|
| NIFTY live ticks arriving | `get_ltp("NSE:NIFTY50-FUT")` returns a float | Requires market hours |
| BANKNIFTY live ticks arriving | `get_ltp("NSE:NIFTYBANK-FUT")` returns a float | Requires market hours |
| Tick cache updating | `websocket_feed._tick_cache` entries have fresh `ts` | Requires market hours |
| No reconnect loop | Reconnect count stays at 0 in logs | Monitor during session |

**Note:** Live ticks cannot be verified before 09:15 IST when NSE opens. Check within the first 2 minutes of market open.

**Quick verification command (run during session):**
```python
from scanner.websocket_feed import get_latest_tick
print(get_latest_tick("NSE:NIFTY50-FUT"))
# Expected: {'ltp': 24xxx.x, 'volume': xxx, 'ts': '2026-06-02 09:16:...'}
```

---

### 4. 3-Minute Candle Formation

| Check | Status |
|-------|--------|
| Scanner uses 3-min bars (`tf='3'` passed to `scan_silver_bullet`) | ✓ Confirmed in `run_silver_bullet_scan()` main.py |
| TrueData bar_size `"3min"` confirmed working | ✓ 127 bars returned |
| Cache TTL 120s — will refresh once per 2 min during scanning | ✓ Coded |
| `get_historical_data(fyers, symbol, '3', days=3)` used in scanner loop | ✓ Confirmed |

**Risk:** During the first 3-min bar after open (09:15–09:18), bar data will include one partial bar. Scanner dedup logic (`_sb_daily_taken`) prevents double-trading the same zone.

---

### 5. OI Updating

| Check | Status |
|-------|--------|
| `oi` column present in NIFTY/BANKNIFTY bar data | ✓ 100% confirmed |
| `score_dol_by_oi()` called after DOL detection | ✓ Confirmed in silver_bullet.py |
| `check_oi_entry_filter()` called at FVG touch | ✓ Confirmed |
| Live OI in tick stream | Available — not yet used post-signal (monitor only) |

**Note:** OI gates operate on historical bar OI, not the live tick OI. This means OI is updated each time a new bar is fetched (every 2 minutes due to cache TTL). This is sufficient resolution for 3-min bar analysis.

---

### 6. Bid/Ask Updating

| Check | Status |
|-------|--------|
| TrueData WS sends `best_bid`, `best_ask` | ✓ Coded in _dispatch_tick() |
| Bid/ask stored in `_tick_cache` | ✗ **NOT STORED** — cache gap |
| `check_bidask_filter()` active | ✗ Always PASS_THROUGH due to above gap |

**Impact for Monday session:** The bid/ask gate will not function. All FVG entries that pass other gates will pass this gate unconditionally. This is the same behaviour as before TrueData. It is a known gap, not a regression.

---

### 7. Symbol Mismatch Check

| Potential mismatch | Status |
|-------------------|--------|
| Monthly expiry symbol (NIFTY26JUNFUT) correctly generated | ✓ `get_active_futures()` uses today's month |
| Fyers → TrueData symbol mapping | ✓ `_FYERS_TO_TD` verified for all 4 indices |
| TrueData → Fyers reverse mapping for tick dispatch | ✓ `_TD_TO_FYERS` verified |
| BANKNIFTY alias (both `NSE:BANKNIFTY-FUT` and `NSE:NIFTYBANK-FUT`) | ✓ Both map to `BANKNIFTY-I` |

**Watch for:** On expiry rollover dates (last Thursday of month), `get_active_futures()` must generate the next month's symbol. June expiry = `NIFTY26JUNFUT`. Next rollover = last Thursday of June 2026.

---

### 8. Reconnect Loop Check

| Check | How to detect | Mitigation |
|-------|--------------|------------|
| TrueData hist reconnect loop | Multiple `"TrueData: historical connect failed"` lines in log | State machine blocks CONNECTING→CONNECTING reentry |
| TrueData live WS silent drop | No ticks arriving, no error logged | **No watchdog** — manual check required |
| Fyers fallback activated | `"NSE:NIFTY26JUNFUT: TrueData missing/empty — Fyers fallback"` in log | Expected, not alarming |

**Risk:** Silent WS drop. If TrueData live feed drops without exception, CB6 reads stale `_tick_cache` values. The historical bar path (used by scanner) still works via REST. Only real-time tick-dependent features (SL/TP triggers) would be affected.

---

### 9. Scanner Exception Check

Common scanner exceptions to watch for on startup:

| Exception | Cause | Fix |
|-----------|-------|-----|
| `AttributeError: 'NoneType' has no attribute 'scan_silver_bullet'` | fyers_instance is None | Ensure auto_token.py completes before scanner starts |
| `KeyError: 'NIFTY' not in futures` | `get_active_futures()` returned incomplete dict | Check if June expiry contracts are listed by Fyers |
| `TrueData get_historical_bars failed: session expired` | 15-day trial ended | Renew subscription or fall back to Fyers |
| `ValueError: not enough bars: got 28 need 30` | Early morning, few bars available | Scanner skips symbol, retries next cycle |

---

### 10. MIDCPNIFTY Forward-Fill

| Check | Status |
|-------|--------|
| `forward_fill_midcpnifty()` implemented | ✓ In TrueDataManager |
| Called during scanning | ✗ **NOT YET CALLED** — needs wiring in scanner loop |

The forward-fill method exists but is not called from anywhere in the production scanning loop. To activate it, add to the 3-min scanner loop:

```python
# In main.py _nifty_live_scanner() or run_silver_bullet_scan():
try:
    from data.truedata_feed import get_manager
    get_manager().forward_fill_midcpnifty()
except Exception:
    pass
```

This is a do-not-implement note for audit purposes. The gap handler is ready; the call site just needs wiring.

---

## Go / No-Go Summary

| Category | Status | Risk if ignored |
|----------|--------|----------------|
| Fyers token | ✓ GO | Session crash at start |
| TrueData historical bars (NIFTY/BANKNIFTY) | ✓ GO | — |
| TrueData historical bars (FINNIFTY) | ⚠ CAUTION | Sparse bars → phantom patterns |
| TrueData historical bars (MIDCPNIFTY) | ⚠ CAUTION | 87 historical gaps |
| 3m candle formation | ✓ GO | — |
| OI gates (NIFTY/BANKNIFTY) | ✓ GO | — |
| Bid/ask gate | ✗ INACTIVE | Entry on wide-spread FVG possible |
| Symbol mapping | ✓ GO | — |
| Reconnect watchdog | ✗ MISSING | Silent drop undetected |
| MIDCPNIFTY forward-fill call site | ✗ NOT WIRED | Not called — gap not actively patched |
| Scanner exceptions | ✓ GRACEFUL | Symbols skipped, not crashed |

**Overall: GO with awareness of 3 known gaps (bid/ask gate, WS watchdog, MIDCPNIFTY forward-fill call site).**

---

## Pre-Session Checklist (Run Sequence)

```powershell
# 1. Before 08:45 IST — run token refresh
python auto_token.py     # refreshes Fyers token + auto-launches main.py

# 2. At 09:00 IST — confirm system running
Get-Content logs\cb6_20260602.log -Tail 20
# Look for: "TrueData: historical connection established"
# Look for: "NSE scanner: running"
# No "EMERGENCY_STOP.flag" lines

# 3. At 09:17 IST (2 minutes after open) — confirm live ticks
# In Python REPL (if accessible):
from scanner.websocket_feed import get_latest_tick
tick = get_latest_tick("NSE:NIFTY50-FUT")
print(tick)   # should have ltp and ts from today

# 4. At 10:05 IST — first SB window check
# Look in log for: "Silver Bullet: [NIFTY/BANKNIFTY/...] ..."
# OR: "SB dedup: already alerted..."
# OR: "no setup on ..."

# 5. After 11:05 IST — generate Day 1 report
python reports/generate_day1_report.py
```
