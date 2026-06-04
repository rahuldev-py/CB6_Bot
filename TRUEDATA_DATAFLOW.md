# TRUEDATA_DATAFLOW.md
# CB6 Quantum — TrueData Data Architecture & Flow

**Date:** 2026-05-30
**Engineer:** Principal Quant Architect / Claude Code

---

## 1. Current Data Flow (Production Today)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CB6 Quantum — Current Market Data Architecture                             │
│  (TrueData historical active; TrueData live NOT yet wired)                  │
└─────────────────────────────────────────────────────────────────────────────┘

   HISTORICAL DATA PATH
   ─────────────────────
   scanner/data_fetcher.py::get_historical_data()
           │
           ├─1─ _cache_get()  ──── HIT ──▶  Return cached DataFrame (TTL 120s)
           │
           └─ MISS ──▶
                   │
                   ├─2─ _get_historical_data_truedata()
                   │           │
                   │           ├── data/truedata_feed.py::TrueDataManager
                   │           │           │
                   │           │           ├── connect_hist()  [if not CONNECTED]
                   │           │           │       └── truedata.TD_hist(user, pass)
                   │           │           │
                   │           │           └── get_historical_bars(td_symbol, bar_size, days)
                   │           │                   └── TD_hist.get_historic_data(...)
                   │           │
                   │           ├── Symbol mapping: fyers_to_td_symbol()
                   │           │   e.g. NSE:NIFTY50-FUT → NIFTY-I
                   │           │
                   │           └── Timeframe mapping: tf_to_bar_size()
                   │               e.g. "15" → "15 mins"
                   │
                   │   SUCCESS (len > 20 bars)
                   ├───────────────────────────▶  _cache_put()  →  Return df
                   │
                   │   FAILURE (None / empty / error)
                   └─3─ Fyers Fallback
                               │
                               └── fyers.history(symbol, resolution, date_range)
                                       ├── 3 retries with exponential backoff
                                       └── _cache_put()  →  Return df


   LIVE PRICE PATH (LTP)
   ─────────────────────
   scanner/live_price.py::get_live_price()
           │
           ├─1─ _td_ltp(symbol)
           │           │
           │           └── TrueDataManager.get_ltp(td_symbol)
           │                   └── TD_live.live_data.get(symbol).ltp  [if CONNECTED]
           │                                                            [returns None today — feed not started]
           │
           └─2─ Fyers Fallback (for all symbols today)
                       └── fyers.quotes({"symbols": symbol})


   LIVE TICK FEED
   ─────────────
   Fyers WebSocket (ACTIVE)
           │
           ├── scanner/websocket_feed.py::init() — started in main.py
           ├── _on_message() → _tick_cache[symbol] update
           └── core.tick_watcher.on_tick()

   TrueData WebSocket (NOT ACTIVE)
           │
           └── scanner/websocket_feed.py::init_truedata()
                   [function exists but NOT called from main.py]


   DOWNSTREAM CONSUMERS
   ─────────────────────
   Historical DataFrame ──▶ scanner/  (ICT Silver Bullet signal engine)
                        ──▶ ml/       (DNN+CNN+RNN shadow models, training)
                        ──▶ dashboard/ (chart data)
                        ──▶ risk/      (macro bias, ATR calculation)

   Live Tick ──▶ core/tick_watcher.py  (trigger evaluation)
             ──▶ scanner/live_price.py (get_live_price calls)
             ──▶ communications/telegram_bot.py (live P&L display)
```

---

## 2. Future Data Flow (After Trial Verification + Activation)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CB6 Quantum — Target Market Data Architecture                              │
│  (TrueData as PRIMARY for all paths; Fyers as FALLBACK)                     │
└─────────────────────────────────────────────────────────────────────────────┘

   HISTORICAL DATA PATH (unchanged — already working)
   ─────────────────────
   scanner/data_fetcher.py
           │
           ├── TrueData PRIMARY  (TTL-cached, 2-min refresh)
           └── Fyers FALLBACK    (on TrueData failure)


   LIVE PRICE PATH (upgrade: TrueData fills from live tick cache)
   ─────────────────────
   scanner/live_price.py::get_live_price()
           │
           ├─1─ TrueData tick cache (sub-second staleness)
           │       └── available after init_truedata() is wired
           │
           └─2─ Fyers quotes() (fallback only)


   LIVE TICK FEED (upgrade: TrueData primary)
   ─────────────
   TrueData WebSocket (PRIMARY)
           │
           ├── data/truedata_feed.py::connect_live()
           ├── @TD_live.trade_callback → tick_queue.put() [O(1)]
           ├── td-tick-worker thread
           │       ├── _tick_cache[symbol] update
           │       └── core.tick_watcher.on_tick()
           └── Subscribed: NIFTY-I, BANKNIFTY-I, FINNIFTY-I, MIDCPNIFTY-I
                         + option strikes for active setups

   Fyers WebSocket (FALLBACK)
           └── Activated only if TrueData feed fails to connect


   OPTION CHAIN / GREEKS / OI (new capability)
   ─────────────────────────
   On Silver Bullet setup detection:
           │
           ├── provider/truedata/option_chain.py::get_atm_chain()
           │       ├── Fetch ATM ±5 strikes for active underlying
           │       ├── OI, OI_change, LTP, Bid, Ask
           │       └── Filter: OI > threshold → liquidity check
           │
           └── provider/truedata/greeks_client.py::get_greeks()
                   ├── IV for entry/exit decision (high IV → prefer selling)
                   ├── Delta for hedge sizing
                   └── Theta for time decay awareness


   SYMBOL MASTER (new capability)
   ─────────────
   On startup / expiry rollover:
           │
           └── provider/truedata/symbol_master.py::refresh()
                   ├── Load all F&O symbols
                   ├── Cache strikes for NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY
                   └── Auto-detect ATM strike for active setups
```

---

## 3. Risk Points

| Risk Point | Location | Type | Mitigation |
|-----------|---------|------|-----------|
| TrueData REST down (holiday/maintenance) | `data/truedata_feed.py` | Single Point of Failure for historical | Fyers fallback in `data_fetcher.py` |
| TrueData WebSocket disconnects mid-session | `provider/truedata/websocket_client.py` | Stale tick cache | Auto-reconnect with backoff; Fyers fallback for LTP |
| TrueData session expiry during market hours | `data/truedata_feed.py` | Silent fallback to Fyers | Fixed by C3 — reset + reconnect |
| Option chain fetch latency >2s | `provider/truedata/option_chain.py` | Signal delay | Cache chain for 30s; refresh async |
| Symbol mapping gap (unmapped Fyers symbol) | `data/truedata_feed.py:fyers_to_td_symbol()` | Wrong symbol → empty data | Fallback to Fyers path |
| Tick queue unbounded growth | `data/truedata_feed.py:_tick_queue` | Memory (under sustained high throughput) | Not a risk for 4 symbols; monitor if expanded |
| No bid/ask in scanner filters | `scanner/` | Reduced entry quality | Low priority — add after OI/Greeks |

---

## 4. Single Points of Failure

### Current Architecture SPOFs

| SPOF | Impact | Mitigation Status |
|------|--------|------------------|
| Fyers API authentication failure | No historical data and no LTP | No mitigation — Fyers is the fallback itself |
| TrueData TD_hist session | Historical data falls to Fyers | Mitigated by Fyers fallback |
| main.py / auto_token.py crash | Full bot down | Watchdog (`watchdog.py --attach`) |
| Telegram bot token invalid | No remote control | Bot reconnects on next message |

### Future Architecture SPOFs (after TrueData activation)

| SPOF | Impact | Mitigation |
|------|--------|-----------|
| TrueData live feed down | Fall back to Fyers WebSocket | Wire Fyers as explicit backup |
| Both TrueData + Fyers live down | No live prices | Alert via Telegram; pause trading |
| TrueData REST rate limit hit | Slow historical fetches | REST client already rate-limits; add circuit breaker |

---

## 5. Data Quality Risks (Specific to Trial Verification)

These are unknown until trial and could affect trading decisions:

| Data Quality Issue | How to Detect | Impact |
|-------------------|--------------|--------|
| Historical data gaps on expiry days | Compare bar count vs expected | Missed signals on expiry |
| Tick timestamp drift (server clock vs exchange) | Latency tracking in WS client | Incorrect candle assignment |
| Stale OI data (batch-updated, not real-time) | Timestamp on OI field | OI-based signals fire on stale data |
| Greek IV spiking at open/close | IV validation ranges in greeks_client.py | Bad signal generation |
| Wrong continuous futures rollover (NIFTY-I) | Compare close vs Fyers on rollover day | Incorrect HTF bias |

---

## 6. Module Dependency Map

```
main.py
  └── scanner/websocket_feed.py::init()          ← Fyers WS (active)
      scanner/websocket_feed.py::init_truedata()  ← TrueData WS (inactive)

scanner/data_fetcher.py
  └── data/truedata_feed.py::TrueDataManager      ← shim (active, historical)
      ├── provider/truedata/auth.py                ← (not used by shim)
      └── provider/truedata/historical_client.py   ← (not used by shim, used in trial)

scanner/live_price.py
  └── data/truedata_feed.py::get_ltp()            ← shim (active, live LTP)

trial/run_truedata_trial.py
  └── provider/truedata/*                          ← modern clients (trial only)

ml/
  └── scanner/data_fetcher.py::get_historical_data()  ← shared path (TrueData → Fyers)

forex_engine/  dashboard/  risk/  execution/
  └── No direct TrueData dependency (NSE-only data layer)
```
