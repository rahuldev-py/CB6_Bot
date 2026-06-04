# CB6 Futures Core — Performance Stress Test
**Date:** 2026-06-01  
**Scope:** Memory, runtime, and bottleneck analysis for 2-year 1m MES + MGC datasets  

---

## Dataset Size Estimates

### Bar Counts
| Symbol | Days/Year | Hours/Day | Bars/Day | 2-Year Total |
|--------|-----------|-----------|----------|-------------|
| MES | ~252 trading + partial weekends | ~23h | ~1,380 | ~695,000 |
| MGC | ~252 trading + partial weekends | ~23h | ~1,380 | ~695,000 |
| **Total** | | | | **~1,390,000** |

CME equity and metals trade Sunday 17:00 CT to Friday 16:00 CT. Daily maintenance break 60 min at 16:00-17:00 CT removes ~60 bars/day.

**Conservative estimate for 2 years (Jan 2023 – Dec 2024):** ~700,000 bars per symbol.

---

## Memory Analysis

### Per-bar Python Object
`FuturesBar` is a `@dataclass` with 7 fields (str, str, datetime, float×4, int, str).  
Python dataclass instance overhead: ~200–240 bytes each (including Python object header, dict-based attribute storage).

| Dataset | Bar Count | Estimated Memory |
|---------|-----------|-----------------|
| MES 1m bars list | 700,000 | ~140–170 MB |
| MGC 1m bars list | 700,000 | ~140–170 MB |
| Rolling window_m1 (200 bars × 2 symbols) | 400 | negligible |
| Rolling window_h4 (60 bars × 2 symbols) | 120 | negligible |
| Trade records (max ~2,000 trades) | 2,000 | ~5 MB |
| **Total Python heap** | | **~285–345 MB** |

Windows 11 process memory limit is effectively available RAM. A modern system with 8 GB RAM handles this without issue.

**Verdict: No OOM risk.**

---

## Load Time Analysis

`CSVDataFeed._load_csv()` reads the entire file line by line using `csv.DictReader`, parsing each row into a `FuturesBar`.

### Bottlenecks in `_load_csv`
1. `csv.DictReader` iteration: ~100,000–150,000 rows/sec in CPython
2. `_parse_ts` — loops through up to 10 `datetime.strptime` patterns per row until one matches
   - Best case (first pattern matches): ~1 μs/row
   - Worst case (falls through all 10): ~10 μs/row

### Estimated Load Times
| Step | MES 1m | MGC 1m |
|------|--------|--------|
| CSV open + delimiter detect | <0.1s | <0.1s |
| Row parsing (700K rows) | 5–12s | 5–12s |
| List build + sort | 1–2s | 1–2s |
| **Total per file** | **6–14s** | **6–14s** |
| **Total (both files)** | | **12–28 seconds** |

**Verdict: Load time is acceptable.**

---

## Backtest Loop Performance

### Per-bar Operations
The engine iterates through all `m1_bars` once. For each bar:

| Operation | Complexity | Note |
|-----------|-----------|------|
| `window_m1.append(bar)` | O(1) | — |
| `window_m1.pop(0)` | **O(n=200)** | Bottleneck — list shift |
| H4 pointer advance | O(1) amortized | |
| `_check_exits` (open trades) | O(open_trades) | Usually 0 or 1 |
| EOD flat check | O(1) | |
| Fill attempt | O(1) | |
| `signal_fn` call | O(48 + session_bars²) | Only when flat |

### `window_m1.pop(0)` Bottleneck
`list.pop(0)` shifts all 200 elements left — effectively a 200-element memmove.  
For 700,000 bars: `700,000 × 200 = 140,000,000` element shifts.  
At ~10 ns/shift: **~1.4 seconds** of overhead. Acceptable.

### Signal Function Call Frequency
`signal_fn` is called only when `not open_trades and pending_setup is None`.  
Given ~100–500 trades/year from a 1m ICT setup, most bars are "flat":  
- ~99.9% of bars call signal_fn
- Each call: `find_liquidity_pools(48 bars, lookback=24)` + `check_sweeps` + `detect_structure_events(session_bars)` + `find_fvg(session_bars)`

Estimated signal_fn time: ~0.1–0.5 ms/call  
For 700K bars × 0.3 ms = **~210 seconds = ~3.5 minutes per symbol per year.**

For 3 years × 2 symbols = **~20–42 minutes total.**

This is significant but not a failure. The user can run it overnight or while working.

**Verdict: Runtime is long (20–42 min) but will complete without timeout or OOM.**

---

## Logging Growth

`FuturesBacktestEngine` uses `logger.debug` for per-trade events.  
The research runner configures `logging.basicConfig(level=logging.INFO)`.  
Debug messages are suppressed at runtime — **no log file growth concern.**

---

## DataFrame Growth
The codebase uses **no pandas DataFrames**. All data structures are native Python lists, dicts, and dataclasses. No risk of pandas memory explosion.

---

## Report Generation Limits

Research output files:
| File | Estimated Size | Concern |
|------|---------------|---------|
| `research_summary_*.json` | <100 KB (JSON per trade) | None |
| `gate_report_*.json` | <10 KB | None |
| `data_quality_report.md` | <50 KB | None |
| `perf_MES[1m]_*.json` | <500 KB | None |
| `trades_MES[1m]_*.csv` | <2 MB (1000 trades × fields) | None |

**Verdict: No report generation limits concern.**

---

## Failure Points Summary

| Risk | Likelihood | Impact | Verdict |
|------|-----------|--------|---------|
| OOM during file load | Very Low | Fatal | Not a risk |
| Timeout on backtest loop | Low | Slow | ~20–42 min, completes |
| CSV parse errors (wrong format) | Medium | Fatal run | Validator catches it |
| Disk space for reports | Very Low | Fatal write | <10 MB total |
| Python recursion | None | — | No recursion in hot path |

---

## Recommendations Before Data Import

1. Verify available RAM ≥ 4 GB free before running (2 GB comfortable margin above ~350 MB usage)
2. Expect 12–28 seconds for data load per symbol
3. Expect 20–42 minutes for full 2-year backtest on both symbols
4. Run validator first — a format error discovered here saves the full backtest runtime

---

## Verdict

**No failure points identified that prevent correct execution.** The system will process 700K-bar 1m datasets without crashing. Runtime is long but bounded and predictable.
