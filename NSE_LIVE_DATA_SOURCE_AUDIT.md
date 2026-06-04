# NSE Live Data Source Audit
**Date:** 2026-06-02  
**Auditor:** CB6 Quantum — Claude Code  
**Evidence:** `logs/cb6_20260601.log`, `logs/cb6_20260602.log`, source trace of `main.py → scanner → live_price → data_fetcher → websocket_feed → truedata_feed`

---

## Verdict Summary

| Layer | Current Source | Expected Source | Gap? |
|---|---|---|---|
| Historical candles (scanner structure) | **TrueData REST** ✅ | TrueData REST | None |
| Live LTP (entry price check) | **Fyers quotes API** ⚠️ | TrueData live WS | Yes — live feed never started |
| Tick feed (WS triggers / SL/TP) | **None — WS off** ❌ | TrueData WS | Yes — `init_truedata()` never called |
| Yahoo feed | **Running (60s poll)** ℹ️ | Fallback only | Harmless — never actually used |

---

## Q1 — Is live LTP coming from Yahoo, Fyers, or TrueData?

**Answer: Fyers quotes API (REST)**

Call chain when a signal fires:
```
main.py _apply_live_entry()
  → scanner.live_price.get_live_price(fyers, symbol)
      → _td_ltp(symbol)              # tries TrueData live cache
          → data.truedata_feed.get_ltp(symbol)
          → returns None             # live feed not connected → cache empty
      → fyers.quotes({"symbols": symbol})   ← ACTUAL LTP SOURCE
          → returns LTP via REST HTTP call
```

Log evidence (Jun 1 — no "Yahoo fallback" entries found):
- Zero instances of `"Live price {symbol}: ... (Yahoo fallback)"` in any log
- Fyers quotes API succeeding silently on every entry check

---

## Q2 — Is the scanner using Yahoo, Fyers, or TrueData prices?

**Two distinct operations — different answers for each:**

### Candle bars (structure detection: FVG/MSS/DOL)
**TrueData REST API** — confirmed active.

Log evidence from Jun 1 at 09:15 IST (first scan cycle):
```
Connected successfully to TrueData Historical Data Service...
TrueData: historical connection established
NSE:NIFTY26JUNFUT:      TrueData 127 candles (3min)
NSE:BANKNIFTY26JUNFUT:  TrueData 125 candles (3min)
NSE:FINNIFTY26JUNFUT:   TrueData  55 candles (3min)
NSE:MIDCPNIFTY26JUNFUT: TrueData 122 candles (3min)
```
Fyers fallback was **never triggered** — TrueData delivered all bars.

### Entry price (LTP at signal time)
**Fyers quotes API** — see Q1.

### Yahoo
**Never reaches the scanner.** Yahoo's `_prices` dict is only read inside `_apply_live_entry()` as Fallback 2, only if Fyers quotes returns nothing. This has not happened.

---

## Q3 — Is TrueData live feed connected?

**No.**

Evidence:
- Zero occurrences of `"TrueData WS: live feed active"` in any log file
- Zero occurrences of `"TrueData: live connection"` anywhere
- `websocket_feed._td_active` is always `False` at runtime

---

## Q4 — Is `init_truedata()` actually called?

**No — it is never called from `main.py`.**

`init_truedata()` is defined in `scanner/websocket_feed.py:136`.  
Grep result across the entire codebase:
```
trial/run_full_report.py:622   — reference in a report comment only
scanner/websocket_feed.py:136  — the definition itself
scanner/websocket_feed.py:7    — docstring comment
```
`main.py` startup sequence (line 1637–1658) only calls `ws_init` (Fyers WebSocket), and only if `STRATEGY.enable_websocket = True`. That flag is currently **False** (`WebSocket feed: OFF` in logs). `init_truedata()` has no caller in the live path.

---

## Q5 — Is TrueData writing into `_tick_cache`?

**No.**

`_tick_cache` in `scanner/websocket_feed.py` is populated by two paths:
1. `_on_message()` — the Fyers WS tick handler (WS is off, so this never fires)
2. `TrueDataManager._tick_dispatch_loop()` — dispatches ticks into `_tick_cache` via `fyers_to_td_symbol()` remapping

Path 2 requires `connect_live()` to be called first, which requires `init_truedata()` to be called. Since that never happens, the dispatch loop has nothing to process. `_tick_cache` is empty for all NSE symbols.

---

## Q6 — Is the scanner reading TrueData live values?

**No.**

`scanner/live_price.get_live_price()` calls `_td_ltp()` first, which calls `data.truedata_feed.get_ltp(symbol)`. That function reads from `TrueDataManager._sym_to_req` + the live TD object. Since `connect_live()` was never called, `_sym_to_req` is empty and the function returns `None` immediately. Scanner falls through to Fyers quotes.

---

## Q7 — Is Yahoo overriding TrueData values?

**No.**

Yahoo and TrueData operate on completely separate caches:
- Yahoo stores prices in `nse_yahoo_feed._prices` dict (in-memory, thread-local to that module)
- TrueData live would store ticks in `websocket_feed._tick_cache`

Yahoo is only accessed via `get_yahoo_nse_price()`, which is called in `_apply_live_entry()` **only** as Fallback 2 after both TrueData-live and Fyers quotes return `None`. Since Fyers quotes is succeeding, Yahoo's `_prices` is populated (polling every 60s) but **never read** during normal operation.

There is no code path where Yahoo writes into `_tick_cache` or overrides TrueData.

---

## Q8 — Is any code path still polling Yahoo every 60 seconds?

**Yes — `nse_yahoo_feed._poll_loop()` is always running.**

Started at bot launch via `start_nse_yahoo_feed()` (main.py:1595–1596). Background daemon thread, polls `^NSEI`, `^NSEBANK`, `^NSMIDCP` every 60 seconds via `yfinance.download()`. Results stored in module-level `_prices` dict.

**Impact:** Negligible. It runs but the data it collects sits unused because Fyers quotes never fails. It does add a minor network call every 60s and a yfinance import overhead.

**Note:** FINNIFTY is intentionally excluded from Yahoo polling (`_YAHOO_UNSUPPORTED = {'FINNIFTY'}`) because Yahoo's coverage is inconsistent.

---

## Q9 — Which exact source generated the latest NIFTY LTP?

**Fyers quotes API** — `fyers.quotes({"symbols": "NSE:NIFTY26JUNFUT"})` via REST HTTP.

No TrueData tick for NIFTY has ever been dispatched into `_tick_cache`. No Yahoo price was needed.

---

## Q10 — Which exact source generated the latest BANKNIFTY LTP?

**Fyers quotes API** — `fyers.quotes({"symbols": "NSE:BANKNIFTY26JUNFUT"})` via REST HTTP.

Same reasoning as Q9.

---

## Final Answers

```
Current Live Source (LTP at entry):
  Fyers quotes API (REST HTTP) — per-call, ~200-500ms round trip
  TrueData live WS = never started (init_truedata() not called)
  Yahoo = running in background but never actually read

Current Historical Source (candle bars for structure):
  TrueData REST API — confirmed delivering bars for all 4 indices
  Fyers historical = fallback, never triggered
  Max lookback: 15 days (trial cap enforced in truedata_feed.py)

Current Scanner Source (what scan_silver_bullet() receives):
  Candle bars:  TrueData REST
  Entry LTP:    Fyers quotes API

Current Fallback Source:
  Candle bars:  Fyers history API (if TrueData REST fails)
  LTP:          Yahoo Finance (if Fyers quotes fails — has never happened)
  LTP last:     Last closed candle close (if Yahoo also fails)

Recommended Source:
  Candle bars:  TrueData REST ← already correct, no change needed
  LTP:          TrueData live WebSocket ← NOT wired in yet
  Fix needed:   Call init_truedata(list(_LIVE_INSTRUMENTS.keys())) at startup
                in main.py alongside start_nse_yahoo_feed()
                This gives sub-second real-time ticks instead of REST round-trips
                and removes dependency on Fyers quotes API for live price
  Yahoo:        Keep as Fallback 2 (harmless, no change needed)
  Trial expiry: TrueData trial (Trial119/rahul119) expires 2026-06-09
                Must upgrade to paid plan before then or historical feed breaks
```

---

## Action Items

| Priority | Action | File | Impact |
|---|---|---|---|
| HIGH | Call `init_truedata(symbols)` at startup | `main.py` ~line 1596 | LTP switches from Fyers REST → TrueData WS ticks |
| HIGH | Upgrade TrueData trial before 2026-06-09 | Account/billing | Both REST + WS will break if trial lapses |
| LOW | Remove or gate Yahoo 60s poll thread | `main.py:1595` | Minor — saves network overhead, Yahoo never used |
