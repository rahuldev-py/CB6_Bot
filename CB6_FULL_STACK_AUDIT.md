# CB6 FULL STACK AUDIT
> Generated: 2026-05-30 22:08:54
> Scope: Post-TrueData activation audit

---

## Summary

| Category | Finding | Severity |
|----------|---------|----------|
| Dead code | `provider/truedata/` (11 files, custom HTTP client) | LOW — superseded by official library |
| Deprecated shim | Old `data/truedata_feed.py` fully replaced | ✅ Fixed |
| Auth endpoint bug | Was `api.truedata.in/users/login` | ✅ Fixed |
| Import bug | `from truedata import TD_hist` (wrong package) | ✅ Fixed |
| Bar size format | `"5 mins"` → `"5min"` | ✅ Fixed |
| .env loading | `os.getenv()` on Windows | ✅ Fixed |
| Trial days cap | Hardcoded 30d (trial only allows 15d) | ✅ Fixed (15d; increase for paid) |

---

## 1. Data Layer

### `data/truedata_feed.py` ✅ (rewritten)
- Now uses official `truedata_ws` library
- Auth, LZ4 decompression, reconnection handled by library
- Same public interface preserved (no scanner changes needed)

### `provider/truedata/` ⚠️ (11 files, can be archived)
- Custom HTTP client with wrong auth endpoint
- `TrueDataHistoricalClient` uses `getAllData` (404)
- `TrueDataAuth` posts to `api.truedata.in/users/login` (wrong)
- Recommend: archive to `provider/truedata_v1_archived/`
- Not a runtime risk (not imported by active code paths)

### `scanner/data_fetcher.py` ✅ (unchanged)
- TrueData primary path calls `data.truedata_feed` correctly
- Fyers fallback still functional
- 2-minute cache prevents redundant fetches

### `scanner/live_price.py` ✅
- Already calls `data.truedata_feed.get_ltp()` then Fyers fallback
- No changes needed

### `scanner/websocket_feed.py` ✅
- `init_truedata()` calls `TrueDataManager.connect_live()`
- Correctly dispatches ticks to `_tick_cache` and `tick_watcher`

---

## 2. Scanner Engine

### `scanner/silver_bullet.py` ✅
- Receives standard DataFrame (timestamp, open, high, low, close, volume)
- TrueData adds OI column — scanner ignores unknown columns safely
- No changes needed

### `scanner/index_futures.py` ✅
- Static symbol definitions — not data-source dependent

### `core/tick_watcher.py`
- Receives on_tick(symbol, ltp) from TrueDataManager._dispatch_tick
- TrueData symbol format (NIFTY-I) vs scanner symbol format may need mapping
- **Recommendation:** Verify tick_watcher symbol keys match what scanner expects

---

## 3. Risk Engine

### `forex_engine/prop_firms/ftmo/ftmo_state.py` ✅
- FTMO best-day cap ($250) enforced — not data dependent

### `forex_engine/prop_firms/gft/gft_5k_2step.py` ✅
- GFT guards intact — not NSE data dependent

---

## 4. ML System

### `ml/` (Shadow mode) ✅
- DNN/CNN/RNN models read same DataFrames via scanner
- TrueData provides OI — new feature for ML (currently unused)
- **Opportunity:** Wire `oi` column into ML feature vector (future enhancement)

---

## 5. Backtest Engine

### `backtest/backtester.py` ✅
- Calls `scanner.data_fetcher.get_historical_data()` → routes to TrueData
- Trial limit (15 days) means backtest window reduced; paid plan = full history

---

## 6. Dashboard

### `dashboard/`
- Market data display should use TrueData live data
- **Check:** Ensure dashboard's live price widget reads from `truedata_feed.get_ltp()`

---

## 7. Technical Debt

| Item | File | Priority |
|------|------|----------|
| Archive old provider | `provider/truedata/` | LOW (not blocking) |
| Wire OI to ML features | `ml/feature_builder.py` | MEDIUM (future uplift) |
| Tick symbol mapping | `core/tick_watcher.py` | MEDIUM (verify format) |
| Rate limiter tuning | `scanner/data_fetcher.py` | LOW (Fyers-only concern) |
| Increase days cap | `data/truedata_feed.py:get_historical_bars()` | MEDIUM (post-purchase) |

---

## 8. Single Points of Failure

| SPOF | Mitigation |
|------|-----------|
| TrueData service down | ✅ Fyers automatic fallback |
| TrueData auth expiry | ✅ Library auto-refreshes token |
| WS disconnect | ✅ Library has heartbeat + auto-reconnect |
| Fyers token expiry | ⚠️ `auto_token.py` handles refresh |

---

## 9. Data Flow Map (Post-Activation)

```
NSE Scanner
    ↓
scanner/data_fetcher.get_historical_data(fyers, symbol, tf, days)
    ↓ cache miss
    ├─ TrueData (PRIMARY)
    │   └─ data/truedata_feed.TrueDataManager
    │       └─ truedata_ws.TD.get_historic_data()
    │           └─ history.truedata.in/getbars (Bearer + LZ4)
    │
    └─ Fyers (FALLBACK — only if TrueData fails/unavailable)
        └─ fyers.history() with 90-day chunking
```

---

## Verdict

**Stack health: GOOD**. TrueData is correctly wired as primary. No scanner or strategy changes were needed. Two medium-priority items (OI→ML, tick symbol mapping) can be addressed post-purchase.
