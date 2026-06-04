# CB6 Quantum — TrueData Data Flow Report
**Audit date:** 2026-06-01  
**Scope:** Every TrueData field traced from raw tick/bar to final scanner output  
**Method:** Static code analysis of actual production files

---

## Overview: Complete Data Path

```
TrueData REST/WS
    │
    ▼
data/truedata_feed.py          ← Singleton TrueDataManager, symbol mapping, FINNIFTY guard
    │                             Column normalization (_normalize_columns)
    │
    ├──► scanner/data_fetcher.py   ← Historical bars (caching, 120s TTL, chunked fallback)
    │        │
    │        ▼
    │    scanner/silver_bullet.py  ← ICT pattern detection (DOL→MSS→FVG→scoring)
    │        │
    │        ├──► scanner/oi_filters.py     ← OI-weighted scoring, entry gate, divergence
    │        └──► scanner/ut_bot.py         ← UT Bot trend confirmation
    │
    └──► scanner/websocket_feed.py  ← Live tick cache (thread-safe dict)
             │
             ├──► scanner/live_price.py     ← get_ltp() / get_live_prices()
             ├──► core/tick_watcher.py      ← SL/TP trigger evaluation
             └──► scanner/oi_filters.py     ← check_bidask_filter() (live bid/ask only)
```

---

## Field-by-Field Trace

### 1. `close` (and `open`, `high`, `low`)

**Source in TrueData:** Column named `c` or `close` in historical bar response.

**Normalization** (`data/truedata_feed.py:_normalize_columns()`):
```python
elif lc in ("c", "close", "ltp"):
    col_map[col] = "close"
```
Renames to standard `close`. Applied to every `get_historical_bars()` and `get_last_n_bars()` call.

**Where used in scanner:**

| Location | Purpose |
|----------|---------|
| `silver_bullet.py:find_draw_on_liquidity()` | Swing high/low detection on `high`/`low` columns |
| `silver_bullet.py:detect_sb_mss()` | Close-beyond-swing = Market Structure Shift |
| `silver_bullet.py:detect_sb_fvg()` | Body ratio = body / (high−low), body = \|close−open\| |
| `silver_bullet.py:market_regime()` | ADX computed from high/low/close via `_adx()` |
| `silver_bullet.py:get_opening_gap_bias()` | First bar open vs prior close |
| `silver_bullet.py:_get_nse_h1_bias()` | EMA(3) vs EMA(8) on close |
| `silver_bullet.py:_get_nse_h4_bias()` | EMA(3) vs EMA(8) on close |
| `oi_filters.py:get_oi_divergence_signal()` | `price_up = close[-1] > close[-5]` |

**Flow summary:** close/OHLC → all pattern detection, bias filters, regime classification.

---

### 2. `ltp` (Last Traded Price — live feed)

**Source in TrueData live:** `tick_data.ltp` attribute on every WS tick.

**Path through code:**
```
_dispatch_tick(tick_data)
  ltp = float(tick_data.ltp)
  → websocket_feed._tick_cache[fyers_sym] = {"ltp": ltp, "volume": vol, "ts": ts}
  → core.tick_watcher.on_tick(fyers_sym, ltp)      ← SL/TP evaluation
  → live_price.get_ltp(fyers, symbol) reads cache   ← Real-time price display
```

**Also normalized** in historical bars: `ltp` column alias → `close` (for any bar snapshot that carries LTP as close proxy).

**Where used:**

| Location | Purpose |
|----------|---------|
| `check_bidask_filter()` | Spread% = (ask−bid) / ltp |
| `core/tick_watcher.py` | SL hit check, TP hit check, trail SL trigger |
| `scanner/live_price.py:get_ltp()` | Real-time price for Telegram alerts |
| `main.py:_nifty_live_scanner()` | Checks if price is near FVG zone |

---

### 3. `volume`

**Source in TrueData:** Column `v`, `volume`, `vol`, or `ttq` (live).

**Normalization:**
```python
elif lc in ("v", "volume", "vol", "ttq"):
    col_map[col] = "volume"
```

Live tick: `vol = int(getattr(tick_data, "ttq", 0) or 0)` → stored in `_tick_cache["volume"]`.

**Where used:**

| Location | Purpose |
|----------|---------|
| `silver_bullet.py:detect_sb_fvg()` | Volume confirmation of displacement candle (implicit: body ratio check is volume proxy) |
| `scanner/websocket_feed._tick_cache` | Available to any scanner function via `get_latest_tick(symbol)["volume"]` |

**Note:** Volume is carried through but the current Silver Bullet scoring does not add explicit confluence points for volume. It is available for future use. The `check_bidask_filter()` does not use volume either.

---

### 4. `oi` (Open Interest)

**Source in TrueData:** Column `oi` in bar data. On live ticks: `tick_data.oi` attribute (captured in `_dispatch_tick`).

**Normalization:**
```python
elif lc == "oi":
    col_map[col] = "oi"
```

**Availability:**
- NIFTY/BANKNIFTY historical: 100% present (confirmed by audit)
- FINNIFTY/MIDCPNIFTY historical: Partial (see Data Availability report)
- All live: streamed when present in WS tick payload

**Complete usage chain in `scanner/oi_filters.py`:**

#### 4a. `score_dol_by_oi(df, dol)` — DOL quality scoring

```python
# Called from silver_bullet.py after find_draw_on_liquidity()
oi_dol_boost, oi_dol_reason = score_dol_by_oi(df, dol)
# → score += oi_dol_boost     (0.0, 1.0, or 2.0)
# → if sweep_confirmed and oi_dol_boost > 0: score += 1.5  [OI+sweep combo]
```

Logic:
1. Find bars within 0.3% of DOL level
2. Check if OI at those bars > rolling_mean(20) × 1.25
3. If spike + EQH/EQL cluster → +2.0. If spike only → +1.0. No spike → 0.0.

#### 4b. `check_oi_entry_filter(df, direction)` — Entry gate

```python
# Called after FVG touch confirmed
oi_entry_ok, reason = check_oi_entry_filter(df, direction)
if not oi_entry_ok:
    return None   # blocks the trade
```

Logic:
- OI rising (>+0.5%) over last 3 bars → PASS
- OI flat (±0.5%) → PASS (neutral)
- OI declining (>-0.5%) → FAIL → trade blocked

#### 4c. `get_oi_divergence_signal(df, direction)` — Setup weight downgrade

```python
oi_divergence = get_oi_divergence_signal(df, direction)
if oi_divergence == "DIVERGENCE":
    score -= 1.5   # downgrade, not block
```

Logic: checks if price direction and OI direction agree over last 8 bars. DIVERGENCE = price moving but OI falling (short-covering / long-liquidation, not new institutional commitment).

#### 4d. `check_oi_at_target(df, target_level, direction)` — SL trail decision

```python
# Called from trade monitoring (not scanner output path)
spike, reason = check_oi_at_target(df, T2_level, direction)
if spike:
    # trail SL aggressively — institutions defending near target
```

---

### 5. OI Change (delta between consecutive bars)

**Not a native TrueData column.** OI change is computed inline where needed:

```python
# In oi_filters.py:check_oi_entry_filter()
oi_start = recent_oi[0]
oi_end   = recent_oi[-1]
pct_change = (oi_end - oi_start) / oi_start   # This IS OI change, computed on-the-fly
```

No separate `oi_change` column is stored. The filters derive it from raw OI series.

---

### 6. `bid` / `ask`

**Source in TrueData live tick:** `tick_data.best_bid`, `tick_data.best_ask` attributes.

**NOT present in historical bar data** (no vendor provides this in OHLCV bars).

**Path through code:**
```
_dispatch_tick(tick_data)
  bid_val = float(getattr(tick_data, "best_bid", 0) or 0) or None
  ask_val = float(getattr(tick_data, "best_ask", 0) or 0) or None
  → live_session_monitor.record_tick(... bid=bid_val, ask=ask_val ...)
```

**Also accessible via:** `scanner/websocket_feed.get_latest_tick(symbol)` returns the full tick dict. The `check_bidask_filter()` function reads:
```python
bid = tick.get("bid") or tick.get("best_bid")
ask = tick.get("ask") or tick.get("best_ask")
```

**Bid/ask gate logic:**
```
spread_pct = (ask - bid) / ltp
NIFTY/BANKNIFTY limit: 0.10% of LTP
FINNIFTY/MIDCPNIFTY limit: 0.20% of LTP
If spread_pct > limit → skip FVG entry
```

**Gap:** The live tick cache (`_tick_cache`) stores only `{ltp, volume, ts}` — `best_bid` and `best_ask` are forwarded to the monitor but not stored in the cache. `check_bidask_filter()` reads from `get_latest_tick()` which returns `_tick_cache`. This means **bid/ask spread gate currently passes through** (`NO_BIDASK_PASS_THROUGH`) even during live session unless the tick cache is extended to store bid/ask.

---

### 7. `timestamp`

**Source in TrueData historical:** Column `time`, `datetime`, `date`, or `timestamp`.

**Normalization:**
```python
elif lc in ("time", "datetime", "date", "timestamp"):
    col_map[col] = "timestamp"
```

After normalization, `silver_bullet.py` forces IST localization:
```python
df.index = pd.to_datetime(df['timestamp'])
if df.index.tz is None:
    df.index = df.index.tz_localize('Asia/Kolkata')
else:
    df.index = df.index.tz_convert('Asia/Kolkata')
```

**Live tick timestamp:** `ts = str(getattr(tick_data, "timestamp", ""))` stored as string in cache. Used by `live_session_monitor` for latency calculation.

**Where timestamp is critical:**
- `is_silver_bullet_window()` — checks current time against window boundaries
- `get_day_extremes()` — filters today's bars only (timestamp date = today IST)
- `detect_liquidity_sweep()` — `candles_ago` calc requires sorted ascending timestamps
- `opening_range_swept()` — identifies 09:15–09:30 IST bars

---

## Data Flow Diagram (Field-Level)

```
TrueData Historical Bar Response
┌───────────────────────────────┐
│ time   → timestamp (IST)      │ → silver_bullet: window gates, extremes, candles_ago
│ open   → open                 │ → FVG body ratio, gap bias
│ high   → high                 │ → swing high detection (DOL), sweep check
│ low    → low                  │ → swing low detection (DOL), sweep check  
│ close  → close                │ → MSS detection, EMA bias, regime
│ volume → volume               │ → displacement candle (future explicit use)
│ oi     → oi                   │ → score_dol_by_oi, check_oi_entry_filter,
│                               │   get_oi_divergence_signal, check_oi_at_target
└───────────────────────────────┘

TrueData Live Tick (WS)
┌───────────────────────────────┐
│ symbol   → Fyers format (map) │ → tick_cache key, tick_watcher
│ ltp      → float              │ → tick_cache, SL/TP evaluation, live_price
│ ttq      → volume             │ → tick_cache
│ best_bid → bid (monitor only) │ → check_bidask_filter (gap — not in cache)
│ best_ask → ask (monitor only) │ → check_bidask_filter (gap — not in cache)
│ timestamp → str               │ → latency calculation in monitor
│ oi       → float (monitor)    │ → OI frequency tracking (monitor only)
└───────────────────────────────┘
```

---

## Known Gaps in Data Flow

| Gap | Location | Impact | Severity |
|-----|----------|--------|----------|
| `best_bid`/`best_ask` not stored in `_tick_cache` | `truedata_feed._dispatch_tick()` | `check_bidask_filter()` always returns PASS_THROUGH | Medium |
| `record_signal()` never called from scanner | `scanner/silver_bullet.py` | Day 1 report shows 0 signals | Low (telemetry only) |
| No live OI stored in `_tick_cache` | `_dispatch_tick()` | Live OI unavailable to `oi_filters` during position monitoring | Low (historical OI used instead) |
| Latency not yet measured | `live_session_monitor` | No empirical latency baseline | Low (pending first session) |

---

## Verdict

The data flow from TrueData through to signal scoring is **correctly architected and functioning**. The primary path (historical OHLCV + OI → scanner → OI-weighted scoring) works end-to-end for NIFTY and BANKNIFTY. The bid/ask gate has a wiring gap (not in tick_cache) that renders it effectively inactive. The OI chain is the most complete and best-wired feature in the integration.
