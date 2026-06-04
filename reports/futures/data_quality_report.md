# CB6 Futures Core — Data Quality Report
**Generated:** 2026-05-31 22:02 UTC
**Data directory:** `data/futures/historical`

---

## Summary Table

| Symbol | TF   |    Bars | Date Range                          |  Dups |  Gaps |  OHLC | Status |
|-------|------|--------|------------------------------------|------|------|------|--------|
| MES    | 1m   |       0 |                                     |     0 |     0 |     0 | — MISSING |
| MES    | 1h   |  11,227 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MES    | 4h   |   3,047 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MNQ    | 1m   |       0 |                                     |     0 |     0 |     0 | — MISSING |
| MNQ    | 1h   |  11,225 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MNQ    | 4h   |   3,044 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MGC    | 1m   |       0 |                                     |     0 |     0 |     0 | — MISSING |
| MGC    | 1h   |  11,262 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MGC    | 4h   |   3,054 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MCL    | 1m   |       0 |                                     |     0 |     0 |     0 | — MISSING |
| MCL    | 1h   |  11,090 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MCL    | 4h   |   3,025 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MGC    | 1m   |       0 |                                     |     0 |     0 |     0 | — MISSING |
| MGC    | 1h   |  11,262 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |
| MGC    | 4h   |   3,054 | 2024-06-10 → 2026-05-29             |     0 |     0 |     0 | ✓ PASS |

---

## Per-Symbol Detail

### MES [1m]

**File not found.** No data available for this symbol/timeframe combination.

> **Action required:** Export 1m data from TradingView, NinjaTrader Kinetick, or Rithmic and import using:
> ```
> python -m futures_engine.research.futures_data_downloader \
>   --symbol MES --source csv \
>   --file MES_1m.csv --timeframe 1m
> ```

### MES [1h]

- **Bars:** 11,227
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 8
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L364: Intra-session gap: 2024-07-03 03:00:00+00:00 → 2024-07-03 13:30:00+00:00 (630 min — possible missing bars)
- `GAP` L3106: Intra-session gap: 2024-12-24 04:00:00+00:00 → 2024-12-24 14:30:00+00:00 (630 min — possible missing bars)
- `GAP` L3321: Intra-session gap: 2025-01-09 14:00:00+00:00 → 2025-01-09 23:00:00+00:00 (540 min — possible missing bars)
- `GAP` L5475: Intra-session gap: 2025-05-26 16:00:00+00:00 → 2025-05-26 22:00:00+00:00 (360 min — possible missing bars)
- `GAP` L5885: Intra-session gap: 2025-06-19 16:00:00+00:00 → 2025-06-19 22:00:00+00:00 (360 min — possible missing bars)

### MES [4h]

- **Bars:** 3,047
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 3
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L100: Intra-session gap: 2024-07-03 00:00:00+00:00 → 2024-07-03 12:00:00+00:00 (720 min — possible missing bars)
- `GAP` L1657: Intra-session gap: 2025-07-03 00:00:00+00:00 → 2025-07-03 12:00:00+00:00 (720 min — possible missing bars)
- `GAP` L2062: Intra-session gap: 2025-10-06 20:00:00+00:00 → 2025-10-07 08:00:00+00:00 (720 min — possible missing bars)

### MNQ [1m]

**File not found.** No data available for this symbol/timeframe combination.

> **Action required:** Export 1m data from TradingView, NinjaTrader Kinetick, or Rithmic and import using:
> ```
> python -m futures_engine.research.futures_data_downloader \
>   --symbol MNQ --source csv \
>   --file MNQ_1m.csv --timeframe 1m
> ```

### MNQ [1h]

- **Bars:** 11,225
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 8
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L363: Intra-session gap: 2024-07-03 03:00:00+00:00 → 2024-07-03 13:30:00+00:00 (630 min — possible missing bars)
- `GAP` L3102: Intra-session gap: 2024-12-24 04:00:00+00:00 → 2024-12-24 14:30:00+00:00 (630 min — possible missing bars)
- `GAP` L3317: Intra-session gap: 2025-01-09 14:00:00+00:00 → 2025-01-09 23:00:00+00:00 (540 min — possible missing bars)
- `GAP` L5470: Intra-session gap: 2025-05-26 16:00:00+00:00 → 2025-05-26 22:00:00+00:00 (360 min — possible missing bars)
- `GAP` L5880: Intra-session gap: 2025-06-19 16:00:00+00:00 → 2025-06-19 22:00:00+00:00 (360 min — possible missing bars)

### MNQ [4h]

- **Bars:** 3,044
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 3
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L99: Intra-session gap: 2024-07-03 00:00:00+00:00 → 2024-07-03 12:00:00+00:00 (720 min — possible missing bars)
- `GAP` L1654: Intra-session gap: 2025-07-03 00:00:00+00:00 → 2025-07-03 12:00:00+00:00 (720 min — possible missing bars)
- `GAP` L2059: Intra-session gap: 2025-10-06 20:00:00+00:00 → 2025-10-07 08:00:00+00:00 (720 min — possible missing bars)

### MGC [1m]

**File not found.** No data available for this symbol/timeframe combination.

> **Action required:** Export 1m data from TradingView, NinjaTrader Kinetick, or Rithmic and import using:
> ```
> python -m futures_engine.research.futures_data_downloader \
>   --symbol MGC --source csv \
>   --file MGC_1m.csv --timeframe 1m
> ```

### MGC [1h]

- **Bars:** 11,262
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 6
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L370: Intra-session gap: 2024-07-03 03:00:00+00:00 → 2024-07-03 13:30:00+00:00 (630 min — possible missing bars)
- `GAP` L3121: Intra-session gap: 2024-12-24 04:00:00+00:00 → 2024-12-24 14:30:00+00:00 (630 min — possible missing bars)
- `GAP` L6129: Intra-session gap: 2025-07-03 03:00:00+00:00 → 2025-07-03 13:30:00+00:00 (630 min — possible missing bars)
- `GAP` L7627: Intra-session gap: 2025-10-06 20:00:00+00:00 → 2025-10-07 07:00:00+00:00 (660 min — possible missing bars)
- `GAP` L8876: Intra-session gap: 2025-12-24 04:00:00+00:00 → 2025-12-24 14:30:00+00:00 (630 min — possible missing bars)

### MGC [4h]

- **Bars:** 3,054
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 2
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L101: Intra-session gap: 2024-07-03 00:00:00+00:00 → 2024-07-03 12:00:00+00:00 (720 min — possible missing bars)
- `GAP` L1663: Intra-session gap: 2025-07-03 00:00:00+00:00 → 2025-07-03 12:00:00+00:00 (720 min — possible missing bars)

### MCL [1m]

**File not found.** No data available for this symbol/timeframe combination.

> **Action required:** Export 1m data from TradingView, NinjaTrader Kinetick, or Rithmic and import using:
> ```
> python -m futures_engine.research.futures_data_downloader \
>   --symbol MCL --source csv \
>   --file MCL_1m.csv --timeframe 1m
> ```

### MCL [1h]

- **Bars:** 11,090
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 17
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L178: Intra-session gap: 2024-06-20 18:00:00+00:00 → 2024-06-21 07:00:00+00:00 (780 min — possible missing bars)
- `GAP` L357: Intra-session gap: 2024-07-03 03:00:00+00:00 → 2024-07-03 13:30:00+00:00 (630 min — possible missing bars)
- `GAP` L1107: Intra-session gap: 2024-08-20 18:00:00+00:00 → 2024-08-21 07:00:00+00:00 (780 min — possible missing bars)
- `GAP` L2101: Intra-session gap: 2024-10-22 18:00:00+00:00 → 2024-10-23 07:00:00+00:00 (780 min — possible missing bars)
- `GAP` L2569: Intra-session gap: 2024-11-20 19:00:00+00:00 → 2024-11-21 08:00:00+00:00 (780 min — possible missing bars)

### MCL [4h]

- **Bars:** 3,025
- **Range:** 2024-06-10T04:00:00+00:00 → 2026-05-29T20:00:00+00:00
- **Duplicates:** 0
- **Large gaps (errors):** 0
- **Session gaps (warnings):** 14
- **OHLC violations:** 0
- **Status:** ✓ PASS

**Warnings (first 5):**
- `GAP` L49: Intra-session gap: 2024-06-20 16:00:00+00:00 → 2024-06-21 04:00:00+00:00 (720 min — possible missing bars)
- `GAP` L99: Intra-session gap: 2024-07-03 00:00:00+00:00 → 2024-07-03 12:00:00+00:00 (720 min — possible missing bars)
- `GAP` L304: Intra-session gap: 2024-08-20 16:00:00+00:00 → 2024-08-21 04:00:00+00:00 (720 min — possible missing bars)
- `GAP` L575: Intra-session gap: 2024-10-22 16:00:00+00:00 → 2024-10-23 04:00:00+00:00 (720 min — possible missing bars)
- `GAP` L703: Intra-session gap: 2024-11-20 16:00:00+00:00 → 2024-11-21 08:00:00+00:00 (960 min — possible missing bars)

---

## 1m Data Readiness for Corrected Backtests

- **MES 1m:** **MISSING** — must be imported before production backtesting
- **MGC 1m:** **MISSING** — must be imported before production backtesting
- **MNQ 1m:** **MISSING** — must be imported before production backtesting

---

## Import Instructions

### Option 1 — NinjaTrader + Kinetick (Recommended, Free)
```
1. Download NinjaTrader 8 (free): ninjatrader.com
2. Register for Kinetick data feed (free with NinjaTrader account)
3. Connect Kinetick → Tools → Historical Data → Request Data
4. Symbol: MES 09-25 (continuous back-adjust: @MES#)
5. Export to CSV: right-click → Export → CSV
6. Import:
   python -m futures_engine.research.futures_data_downloader \
     --symbol MES --source csv --file MES_1m.csv --timeframe 1m
```

### Option 2 — TradingView Pro+ Export
```
1. Open MES1! chart on TradingView, 1 minute timeframe
2. Click Export → Download → CSV
3. Limited to 20,000 bars (~2 weeks) — insufficient for full backtest
4. Useful for spot-checking signal quality only
```

After import, validate with:
```
python -m futures_engine.research.futures_data_validator --symbol MES --timeframe 1m
```
