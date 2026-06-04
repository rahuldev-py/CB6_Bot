# FYERS VS TRUEDATA
> Generated: 2026-05-30 22:08:54
> Sample: NIFTY-I / NSE:NIFTY50-FUT — 5min — last 5 days

---

## Side-by-Side Comparison

| Metric | Fyers | TrueData | Winner |
|--------|-------|----------|--------|
| Bars returned | 0 | 228 | TrueData |
| OI data | ❌ | ✅ | TrueData |
| Missing values | -1 | 0 | TrueData |
| Duplicate timestamps | -1 | 0 | Equal |
| Bid/Ask | ❌ | ✅ | TrueData |
| Tick streaming | ❌ Limited | ✅ | TrueData |
| Historical depth | 100 days (intraday) | 15 days (trial) / 365+ (paid) | Fyers (trial) / TrueData (paid) |
| Cost | Included in API | Separate paid subscription | Fyers |

---

## Timestamp Overlap Analysis

| Metric | Value |
|--------|-------|
| Common bars | N/A |
| TrueData-only bars | N/A |
| Fyers-only bars | N/A |
| Max close price diff | N/A pts |
| Avg close price diff | N/A pts |

---

## Signal Quality Impact

| Aspect | Fyers | TrueData |
|--------|-------|----------|
| DOL detection (swing highs/lows) | ✅ | ✅ |
| FVG detection | ✅ | ✅ |
| CHoCH / BOS | ✅ | ✅ |
| OI-based POI filtering | ❌ Not possible | ✅ Enabled |
| Volume profile | ❌ Basic | ✅ Accurate tick vol |
| Institutional flow signals | ❌ | ✅ (with Greeks add-on) |

---

## Backtest Signal Differences

> Note: With aligned timestamps and matching OHLCV, signal generation is identical.
> The key advantage of TrueData is the **OI column** and **bid/ask** — these can be used
> for future signal filters but do not change existing ICT logic.

---

## Verdict

**TrueData is superior to Fyers for CB6's ICT strategy** because:
1. OI data enables position-aware DOL detection
2. Tick streaming enables more precise entry timing
3. Bid/ask spread confirms or filters FVG fills
4. Option chain + Greeks enables options flow analysis (future)

Fyers remains a reliable **fallback** for historical data continuity.
