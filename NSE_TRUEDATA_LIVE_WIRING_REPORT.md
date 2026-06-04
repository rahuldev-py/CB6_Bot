# NSE TrueData Live Feed — Wiring Report
**Date:** 2026-06-02  
**Change:** Wire `init_truedata()` into `main.py` startup  
**Status:** COMPLETE — syntax verified

---

## What Was Done

### 1. Feature flag added to `.env`
```
ENABLE_TRUEDATA_LIVE=true
```
Set to `false` to revert to Fyers quotes without restarting or editing code.

### 2. Startup block injected into `main.py`
Inserted **after** Yahoo feed start, **before** `send_test_alert()`:

**Location:** Between `start_nse_yahoo_feed()` and `send_test_alert()` call.

**Block behaviour:**
1. Reads `ENABLE_TRUEDATA_LIVE` from `.env` via `dotenv_values`
2. If `true`: calls `init_truedata(['NIFTY-I', 'BANKNIFTY-I', 'FINNIFTY-I', 'MIDCPNIFTY-I'])`
3. If TrueData WS starts → launches `TD-TickVerify` background thread
4. If TrueData WS fails → logs `TRUEDATA_WS_FAILED_FALLBACK_ACTIVE`, continues
5. If flag is `false` → logs `TRUEDATA_LIVE_ENABLED=false`, Fyers quotes used

### 3. Tick verification thread (`TD-TickVerify`)
- Waits for market to open, then waits 60 s for first ticks
- Every 5 minutes checks `websocket_feed._tick_cache` for all 4 symbols
- Logs `TRUEDATA_TICK_OK` or `TRUEDATA_TICK_MISSING` per symbol
- Non-fatal: missing ticks are logged but Fyers quotes covers silently

### 4. Trial expiry warning
- Fires via Telegram once at startup
- If trial expires within 7 days: `WARNING: TrueData trial expires in N day(s)`
- If trial already lapsed: `WARNING: TrueData trial EXPIRED. Full fallback active.`
- **Trial expiry date: 2026-06-09** (7 days from wiring)

---

## Startup Log Sequence (expected)

```
NSE Yahoo price feed started (background)
TRUEDATA_LIVE_ENABLED — wiring TrueData WebSocket live feed
TRUEDATA_WS_STARTED — live ticks active: ['NIFTY-I', 'BANKNIFTY-I', 'FINNIFTY-I', 'MIDCPNIFTY-I']
[Telegram] WARNING: TrueData trial expires in 7 day(s) ...
```

Then at 09:15 IST + 60 s:
```
TRUEDATA_TICK_OK  NIFTY-I          ltp=24350.25
TRUEDATA_TICK_OK  BANKNIFTY-I      ltp=52100.50
TRUEDATA_TICK_OK  FINNIFTY-I       ltp=23800.75
TRUEDATA_TICK_OK  MIDCPNIFTY-I     ltp=11200.00
```

If WS fails at startup:
```
TRUEDATA_WS_FAILED_FALLBACK_ACTIVE — <error> — Fyers quotes + Yahoo remain as live LTP sources
```

---

## Data Source Stack After This Change

```
Live LTP (entry price check):
  1. TrueData live WS tick cache  ← NEW primary (init_truedata() now called)
  2. Fyers quotes API (REST)       ← fallback, unchanged
  3. Yahoo Finance (60s stale)     ← emergency fallback, unchanged
  4. Last candle close             ← last resort, logs warning

Historical candles (scanner structure):
  1. TrueData REST API             ← primary, unchanged (was already working)
  2. Fyers history API             ← fallback, unchanged
```

---

## What Was NOT Changed

- `scanner/` — no changes
- `scanner/live_price.py` — no changes (already falls through TD → Fyers → Yahoo)
- `scanner/data_fetcher.py` — no changes
- `scanner/websocket_feed.py` — no changes (init_truedata was already implemented)
- `data/truedata_feed.py` — no changes
- Risk/strategy/execution logic — untouched
- Fyers quotes API fallback — untouched
- Yahoo feed — untouched

---

## How to Disable (Emergency Rollback)

Edit `.env`:
```
ENABLE_TRUEDATA_LIVE=false
```
Restart `main.py`. No code change needed. Fyers quotes becomes primary LTP source again.

---

## Trial Expiry — Action Required

| Date | Event |
|---|---|
| **2026-06-09** | TrueData trial expires |
| Before 2026-06-09 | Upgrade Trial119 to paid plan at truedata.in |
| If lapsed | Set `ENABLE_TRUEDATA_LIVE=false` in `.env` until renewed |
| After renewal | Set `ENABLE_TRUEDATA_LIVE=true`, update credentials in `.env` if changed |

CB6 will **automatically fall back** to Fyers historical + Fyers quotes if TrueData lapsed — but the Telegram warning fires at every startup to remind you.
