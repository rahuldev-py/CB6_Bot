# CB6 Futures Core — Import Readiness
**Generated:** 2026-05-31  
**Purpose:** Exact requirements for MES_1m.csv and MGC_1m.csv import

---

## Required Files

| File | Destination | Min bars | Min date range |
|---|---|---|---|
| `MES_1m.csv` | `data/futures/historical/MES_1m.csv` | 100,000+ | Jan 2024 – Dec 2025 (2 full years) |
| `MGC_1m.csv` | `data/futures/historical/MGC_1m.csv` | 100,000+ | Jan 2024 – Dec 2025 |

A full trading year of MES 1m data has approximately:
- 252 trading days × 23h × 60 min = ~348,000 bars per year
- After maintenance breaks and holidays: ~320,000 bars per year

---

## Column Specification

CB6 natively reads all of the following formats — no pre-processing required:

### Format A — CB6 Native (output of `save_bars()` / `futures_data_downloader`)
```
timestamp,contract,open,high,low,close,volume
2024-01-02T00:01:00+00:00,MESH24,4769.00,4782.75,4750.00,4765.25,12345
```

### Format B — TradingView Export
```
time,open,high,low,close,Volume
1704067260,4769.0,4782.75,4750.0,4765.25,12345
```
- `time` column: Unix timestamp (seconds, UTC) ✓
- `Volume` (capital V): normalised to lowercase ✓

### Format C — NinjaTrader 8 / Kinetick Export (Recommended)
```
Date,Time,Open,High,Low,Close,Volume
01/02/2024,00:01:00,4769.00,4782.75,4750.00,4765.25,12345
```
- Date format: `MM/DD/YYYY` ✓
- Time: `HH:MM:SS` UTC ✓

### Format D — NinjaTrader Semicolon Export
```
Date;Time;Open;High;Low;Close;Volume;
20240102;000100;4769.00;4782.75;4750.00;4765.25;12345;
```
- Semicolons auto-detected ✓
- `YYYYMMDD` date and `HHMMSS` time combined ✓

### Format E — Sierra Chart / Rithmic
```
Date,Time,Open,High,Low,Close,TotalVolume
2024-01-02,00:01:00,4769.00,4782.75,4750.00,4765.25,12345
```
- `TotalVolume` normalised to `volume` ✓
- ISO date format ✓

---

## Critical Timezone Requirement

**ALL TIMESTAMPS MUST BE UTC.**

This is the single most common import failure. Data exported in local time (US Eastern, US Central) will cause:
- Session filters to shift by 5–6 hours
- Kill-zone detection to trigger at wrong times
- Signal generation outside intended windows

**Verification:** After import, check the first timestamp in the file. If MES opens at 17:00 CT (Chicago), the correct UTC timestamp is:
- Winter (CT = UTC-6): `23:00 UTC` on the calendar date
- Summer (CT = UTC-5): `22:00 UTC` on the calendar date

If you see the first bar at `17:00 UTC`, your timestamps are in CT and must be converted before import.

---

## How to Export in UTC

### NinjaTrader 8 + Kinetick (Recommended)
```
1. Tools → Options → General → Time zone: UTC
2. Historical Data Manager → right-click symbol → Export to CSV
3. Confirm "Export time zone: UTC" in the export dialog
```

### TradingView
```
TradingView always exports Unix timestamps (UTC seconds) — no conversion needed.
Check: first column header is "time", values are large integers (e.g., 1704067200)
```

### Sierra Chart
```
File → Export Data → Time Zone: UTC
Format: Comma-delimited CSV
```

---

## Contract Handling

MES continuous contract data may be presented as:
- **Single continuous series** (all bars in one file, no contract column) — PREFERRED
- **Per-expiry files** (MESH25.csv, MESM25.csv, etc.) — must be concatenated

For a single continuous file: `contract` column will be absent or empty — this is fine. The `ContractManager` assigns the correct front-month contract code internally based on bar timestamps.

**Panama adjustment:** If your data source applies backward price adjustment (Norgate, TickData), use it. The rollover gaps in unadjusted data (0.1–0.3% for ES/MES) are small enough not to invalidate the strategy test, but adjusted data produces cleaner equity curves.

---

## Drop-In Procedure (Zero-Touch)

```powershell
# Step 1: Copy your CSV files to the data directory
Copy-Item "C:\Downloads\MES_1m.csv" "data\futures\historical\MES_1m.csv"
Copy-Item "C:\Downloads\MGC_1m.csv" "data\futures\historical\MGC_1m.csv"

# Step 2: Validate (must show PASS before proceeding)
python -m futures_engine.research.futures_data_validator --symbol MES --timeframe 1m
python -m futures_engine.research.futures_data_validator --symbol MGC --timeframe 1m

# Step 3: Run full pipeline (only if both validations pass)
python -m futures_engine.research.futures_research_runner --year 2025 --symbol MES --save-reports
python -m futures_engine.research.futures_research_runner --year 2025 --symbol MGC --save-reports
python -m futures_engine.research.futures_research_runner --year 2024 --symbol MES --save-reports
python -m futures_engine.research.futures_research_runner --year 2024 --symbol MGC --save-reports

# Step 4: Run gate decision
python -m futures_engine.research.futures_research_gate --require-1m
```

If the gate returns PASS: `BUY_MFF_NOW = TRUE`.  
If the gate returns FAIL or CONDITIONAL: `BUY_MFF_NOW = FALSE`.

---

## Recommended Data Source

**NinjaTrader 8 + Kinetick** is the recommended free source:

1. Download NinjaTrader 8: [ninjatrader.com/trading-software/download](https://ninjatrader.com)
2. Create a free account (no brokerage required for data)
3. Connect Kinetick data feed: Connection → Configure → Add → Kinetick
4. Tools → Historical Data Manager
5. Select: Instrument = `MES 09-25` (or `@MES#` for continuous)
6. Resolution: 1 Minute
7. Date range: 2023-01-01 to today
8. Download → Export → CSV → UTC timezone

For MGC (Micro Gold):
- Same process, Instrument = `MGC 09-25` (or `@MGC#`)

**Alternative paid sources:**
- Norgate Data ($276/year) — Panama-adjusted, institutional quality
- TickData Suite — tick-level with custom aggregation
- Rithmic — requires brokerage account

---

## Expected Output After Successful Import

After a clean MES 1m import, the validator should report:
```
MES 1m: PASS
  Bars: ~320,000  |  Dups: 0  |  Gaps: 0  |  OHLC: 0
  Range: 2024-01-02 → 2025-12-31
  ! [GAP] Expected market closures: ~11,000 — normal (weekends + maintenance + holidays)
```

Session gap warnings for half-day sessions (Jul 3, Dec 24 early close) are normal.
