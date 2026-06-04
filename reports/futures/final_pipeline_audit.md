# CB6 Futures Core — Final Pipeline Audit
**Date:** 2026-06-01  
**Auditor role:** Principal Quantitative Researcher / Futures Systems Architect  
**Scope:** Full 1m CSV pipeline readiness for MES_1m.csv and MGC_1m.csv  

---

## Files Audited

| File | Purpose |
|------|---------|
| `futures_engine/core/futures_data_feed.py` | CSV loading, timestamp parsing, delimiter detection |
| `futures_engine/research/futures_data_validator.py` | Pre-backtest data quality checks |
| `futures_engine/research/futures_research_runner.py` | Backtest execution + MFF simulation |
| `futures_engine/research/futures_research_gate.py` | Binary buy/no-buy decision |
| `futures_engine/core/futures_backtest_engine.py` | Bar-by-bar simulation engine |
| `futures_engine/core/futures_silver_bullet.py` | ICT signal detection |
| `futures_engine/core/futures_market_structure.py` | CHoCH/BOS detection |
| `futures_engine/core/futures_liquidity.py` | BSL/SSL sweep detection |
| `futures_engine/mff_flex_25k/mff_flex_config.py` | MFF rule parameters |
| `futures_engine/mff_flex_25k/mff_flex_rules.py` | MFF rule engine |
| `futures_engine/core/futures_symbol_registry.py` | MES/MGC contract specs |

---

## 1. CSV Loading (`futures_data_feed.py`)

### Delimiter Detection
```python
delimiter = ";" if sample.count(";") > sample.count(",") else ","
```
**PASS.** Reads first 4096 bytes and counts delimiters. NinjaTrader uses semicolons; the code detects this correctly.

### BOM Stripping
```python
r = {k.lower().strip().lstrip("﻿"): v.strip() for k, v in row.items() if k}
```
**PASS.** UTF-8 BOM stripped from all column names.

### Timestamp Parsing — Single Column
Format list includes `"%Y%m%d %H%M%S"` (NinjaTrader YYYYMMDD HHMMSS), `"%m/%d/%Y %H:%M:%S"` (US date format), and ISO-8601 variants.  
**PASS** for single-column exports.

### Timestamp Parsing — Separate Date + Time Columns (CRITICAL BUG)

```python
# Line 127-133 in futures_data_feed.py:
ts_raw = (r.get("timestamp") or r.get("time") or r.get("date") or "")

# NinjaTrader: separate Date (YYYYMMDD) + Time (HHMMSS) columns
if not ts_raw and "date" in r:
    ts_raw = r["date"].strip()
    if "time" in r and r["time"].strip():
        ts_raw = ts_raw + " " + r["time"].strip()
```

**BUG.** When NinjaTrader exports with separate `Date` and `Time` columns:
- `r.get("time")` intercepts the time-only value (e.g. `"093000"`) before the combination block runs
- `ts_raw = "093000"` — truthy, so the `if not ts_raw` block is **skipped entirely**
- `_parse_ts("093000")` fails all format patterns → `ValueError`
- Row silently skipped
- **Net effect: all rows in the file fail to parse → empty dataset**

The same bug exists identically in `futures_data_validator.py` lines 146-153.

**Impact:** If NinjaTrader exports with separate Date/Time columns, both validator and research runner produce zero bars. The validator will flag this as `>10 parse errors` with an ERROR result — **the user will see FAIL and be told to fix the data format.**

**Workaround without code change:** Ensure NinjaTrader exports the Date column as a combined datetime (e.g. `20230103 093000` in a single `Date` field, not two separate fields). The playbook must include this export setting.

### Timezone Handling
```python
if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
```
**PASS.** All naive timestamps are assumed UTC. NinjaTrader Kinetick exports in UTC when configured correctly. If user exports in local time, all bars will be timestamped wrong — but the validator checks for timezone consistency and warns.

### Contract Column
```python
contract=r.get("contract", ""),
```
**PASS.** Optional. NinjaTrader exports do not have a contract column — defaults to empty string. Backtest engine fills `current_contract` from `ContractManager`.

---

## 2. Data Validator (`futures_data_validator.py`)

### Gap Detection Thresholds (1m)
```python
"1m": (90, 1500),  # warn >90 min, error >25h
```
**PASS.** CME's 60-minute daily maintenance break (21:00-22:00 UTC) does not trigger errors. Weekend gaps (~49 hours) trigger warnings (not errors) correctly.

### OHLC Sanity
Checks high ≥ low, open within high/low, close within high/low.  
**PASS.** Standard validation.

### Minimum Bar Count
```python
min_required = {"1m": 5000, ...}
```
**PASS.** A 2-year 1m dataset will have ~700,000 bars — well above 5,000.

### Holiday Gap Classification
Imports `CME_HOLIDAYS` from `futures_session_manager.py`. Walks all dates between two bars and classifies gaps as holiday gaps.  
**PASS.** Holiday set covers 2024-2026.

### Validator Output to Research Runner
The validator writes a markdown report to `reports/futures/data_quality_report.md`. The research runner reads directly from CSV files (not from the report). The validator is a standalone check tool.  
**PASS.** Validator FAIL does not block the runner — user must interpret the output and decide to fix.

---

## 3. Research Runner (`futures_research_runner.py`)

### Timeframe Fallback (HIGH SEVERITY)

```python
def _best_available_timeframe(symbol: str, data_dir: str, preferred: str = "1m") -> str:
    for tf in [preferred, "1m", "5m", "15m", "1h", "4h", "1d"]:
        path = os.path.join(data_dir, f"{symbol.upper()}_{tf}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 200:
            return tf
    return preferred
```

When called for HTF (preferred="4h") with only `MES_1m.csv` on disk:
- Checks `MES_4h.csv` → not found
- Checks `MES_1m.csv` → **FOUND**
- Returns `"1m"`

**Result:** Both primary and HTF use 1m bars. The `get_htf_bias(window_h4_of_1m_bars)` call returns a 1-minute bias, not H4 bias. The ICT strategy's mandatory H4 alignment filter is effectively bypassed — replaced with same-timeframe confirmation.

**Impact on research integrity:** Trades that would be filtered by a genuine H4 bearish bias (while 1m is bullish) may be taken. Win rate and profit factor on 1m data will NOT reflect the strategy as designed.

**Impact on gate decision:** Gate checks `timeframe_used == "1m"` for data quality — it will report `has_1m_data = True` and potentially output `PASS`. But the H4 filter is not operative. Results are not directly comparable to the strategy described in the ICT rules.

This is a pre-existing limitation (same applies when running on 1h data). It is not a new failure. A separate `MES_4h.csv` derived from the same Kinetick source would resolve this.

### MFF Eval Simulation
```python
max_eod_dd, peak_eq, final_eq = _compute_eod_drawdown(daily, EVAL_CONFIG.account_size)
```
**PASS.** EOD trailing drawdown correctly modelled. Peak ratchets up only at end of day. ✅

### Data Inventory Check
Runner checks for both `MES_1m.csv` and `MES_4h.csv`. Missing files are logged as warnings (not fatal). Runner continues with available data.  
**PASS** — graceful degradation.

---

## 4. Research Gate (`futures_research_gate.py`)

### Threshold Values
| Gate Criterion | Value | Assessment |
|---------------|-------|------------|
| Min trades | 100/year | Appropriate |
| Min profit factor | 1.5 | Conservative |
| Max drawdown | $700 | $300 buffer vs MFF $1000 |
| Min expectancy | >0 | Appropriate |
| MFF pass years | ≥2/3 | Appropriate |

**PASS.** Gate thresholds are well-calibrated.

### 1m Data Flag
```python
has_1m = all(y.timeframe_used == "1m" for y in year_checks)
```
Gate correctly distinguishes 1h vs 1m results.  
**PASS.** A 1m-based PASS is flagged with `confidence: HIGH`. A 1h-based PASS is `confidence: MEDIUM`.

### `--require-1m` Flag
```python
if require_1m and not has_1m:
    reasons.append("1m data required...")
```
**PASS.** Strict mode correctly enforced.

### Gate Output → Buy Decision
```python
buy_now = len(passing) >= 1 and has_any_1m
buy_verd = "BUY" if buy_now else "CONDITIONAL"
```
**PASS.** BUY only possible when 1m data is present AND at least one symbol passes all criteria.

---

## Summary Table

| Check | Status | Severity |
|-------|--------|----------|
| Delimiter detection (semicolons) | PASS | — |
| BOM stripping | PASS | — |
| Single-column combined timestamp | PASS | — |
| Separate Date+Time columns | **BUG** | HIGH |
| Timezone assumption (UTC) | PASS | — |
| Gap detection (maintenance break) | PASS | — |
| OHLC sanity | PASS | — |
| Holiday gap classification | PASS | — |
| HTF uses 1m proxy when no 4h file | **LIMITATION** | HIGH |
| MFF EOD drawdown simulation | PASS | — |
| Gate threshold calibration | PASS | — |
| Gate 1m/1h distinction | PASS | — |

---

## Can 2-year 1m MES and MGC datasets be processed without failure?

**YES** — provided NinjaTrader exports the Date field as a combined datetime (single column), not split into separate Date + Time columns.

If the export uses separate Date + Time columns, the validator will fail with `>10 parse errors`, the runner will produce 0 bars, and the gate will output `DO_NOT_BUY`. This is a **recoverable** situation — re-export with combined timestamp format.

The user MUST verify the NinjaTrader export format before running the pipeline. The data arrival playbook includes this check.
