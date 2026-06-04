# CB6 Futures Core — Final Validation Checklist
**Generated:** 2026-05-31  
**Purpose:** Gate conditions that must all pass before BUY_MFF_NOW = TRUE

---

## Current Status

```
BUY_MFF_NOW = FALSE
```

**Reason:** No 1m futures data has been validated. Architecture, risk engine, rule engine, and research pipeline are complete.

---

## Checklist

### Phase 1 — Data Acquisition

```
□ 1. Obtain MES 1m continuous data (Jan 2024 – Dec 2025 minimum)
      Source: NinjaTrader + Kinetick (free) or Norgate ($276/year)
      File: data/futures/historical/MES_1m.csv
      Expected: ~640,000 bars (2 years × ~320K bars/year)

□ 2. Obtain MGC 1m continuous data (Jan 2024 – Dec 2025 minimum)
      Source: Same as MES
      File: data/futures/historical/MGC_1m.csv
      Expected: ~640,000 bars
```

---

### Phase 2 — Data Validation

Run these commands. Both must return PASS.

```
□ 3. MES 1m validation PASS
      Command:
        python -m futures_engine.research.futures_data_validator \
               --symbol MES --timeframe 1m
      Pass criteria:
        - "MES 1m: PASS" in output
        - Bars >= 100,000
        - Errors = 0
        - Date range covers >= 12 months

□ 4. MGC 1m validation PASS
      Command:
        python -m futures_engine.research.futures_data_validator \
               --symbol MGC --timeframe 1m
      Pass criteria: same as above
```

If validation fails: check `reports/futures/data_quality_report.md` for specific errors and see `import_readiness.md` for troubleshooting.

---

### Phase 3 — Backtest Execution

Run for both symbols, both years. All must complete without error.

```
□ 5. MES 2024 backtest complete
      Command:
        python -m futures_engine.research.futures_research_runner \
               --year 2024 --symbol MES --save-reports
      Pass criteria:
        - Completes without Python exception
        - Log shows "Backtest done: N trades" where N > 0
        - JSON saved to reports/futures/research/

□ 6. MES 2025 backtest complete
      Command:
        python -m futures_engine.research.futures_research_runner \
               --year 2025 --symbol MES --save-reports
      Pass criteria: same as above

□ 7. MGC 2024 backtest complete
      Command:
        python -m futures_engine.research.futures_research_runner \
               --year 2024 --symbol MGC --save-reports
      Pass criteria: same as above

□ 8. MGC 2025 backtest complete
      Command:
        python -m futures_engine.research.futures_research_runner \
               --year 2025 --symbol MGC --save-reports
      Pass criteria: same as above
```

---

### Phase 4 — Research Gate

```
□ 9. Research gate passes for MES
      Command:
        python -m futures_engine.research.futures_research_gate \
               --symbol MES --require-1m
      Pass criteria:
        - Gate verdict: PASS (not CONDITIONAL, not FAIL)
        - Trades >= 100 per year
        - Profit factor >= 1.5
        - Max EOD drawdown <= $700
        - Expectancy > $0
        - MFF simulation passes >= 2/3 years

□ 10. Research gate passes for MGC (optional but recommended)
       Command:
         python -m futures_engine.research.futures_research_gate \
                --symbol MGC --require-1m
       Pass criteria: same as MES
```

---

### Phase 5 — MFF Rule Verification

```
□ 11. MFF drawdown simulation within limits
       Verify from gate output or research JSON:
         max_eod_drawdown < $1,000 (at least 2 of 3 test years)

□ 12. MFF consistency rule passes
       Verify: no single day > 50% of cumulative profit
       (Checked automatically by research gate — violations listed under "violations" key)

□ 13. MFF minimum trading days met
       Verify: trading_days >= 2 per year in simulation
       (Always passes if trades > 0)
```

---

### Phase 6 — Final Decision

```
□ 14. All checkboxes 1–13 checked
       If yes: BUY_MFF_NOW = TRUE
       If any fail: BUY_MFF_NOW = FALSE
```

---

## Gate Thresholds Reference

| Criterion | Minimum | Source |
|---|---|---|
| Trades per year | 100 | `futures_research_gate.py` `GATE.min_trades` |
| Profit factor | 1.5 | `GATE.min_profit_factor` |
| Max EOD drawdown | $700 | `GATE.max_drawdown_usd` |
| Expectancy | > $0 | `GATE.min_expectancy` |
| MFF sim passes | ≥ 2/3 years | `GATE.min_mff_pass_years` |

These thresholds are stricter than MFF's own rules ($1,000 DD limit) to preserve a $300 safety margin.

---

## Quick Run — Full Pipeline

Once data files are in place, the complete pipeline runs in approximately 5–10 minutes:

```powershell
# Validate both files
python -m futures_engine.research.futures_data_validator --symbol MES --timeframe 1m
python -m futures_engine.research.futures_data_validator --symbol MGC --timeframe 1m

# Run backtests (4 runs)
python -m futures_engine.research.futures_research_runner --year 2024 --symbol MES --save-reports
python -m futures_engine.research.futures_research_runner --year 2025 --symbol MES --save-reports
python -m futures_engine.research.futures_research_runner --year 2024 --symbol MGC --save-reports
python -m futures_engine.research.futures_research_runner --year 2025 --symbol MGC --save-reports

# Gate decision
python -m futures_engine.research.futures_research_gate --require-1m
```

The gate prints a single final line: `PASS`, `CONDITIONAL`, or `DO_NOT_BUY`.  
`PASS` = `BUY_MFF_NOW = TRUE`.  
Everything else = `BUY_MFF_NOW = FALSE`.

---

## If The Gate Returns CONDITIONAL

CONDITIONAL means some but not all criteria pass. Specific actions:

| Failing criterion | Action |
|---|---|
| Trades < 100 | Expand date range to 3 years; consider adding MNQ to test set |
| PF < 1.5 | Review session filter; consider restricting to NY_AFTERNOON only |
| Max DD > $700 | Apply stricter internal daily stop ($150 instead of $200) and re-run |
| Expectancy ≤ 0 | Strategy does not have edge at current parameters — do not buy |
| MFF sim fails all 3 years | Do not buy — strategy cannot pass the eval |

---

## What Happens If Gate Passes

```
1. Purchase MFF Flex $25K Flex Plan ($57)
2. Set mode to SEMI_AUTO:
   python futures_main.py --mode semi_auto
3. Symbol: MES only (Phase 1)
4. Kill zone: NY_AFTERNOON only (14:00-16:00 UTC)
5. Max 1 micro contract per trade
6. Internal daily hard stop: $200 (fires at 20% of MFF $1,000 limit)
7. Consecutive loss halt: after 2 losses, stop for the day
8. Monitor via: python futures_main.py --status
9. Target: $1,500 profit with EOD DD < $1,000 and trading days ≥ 2
```

---

## Architecture Ready Status

| Component | Ready? |
|---|---|
| Signal scanner (F-2 fixed — sweep required) | ✓ |
| Backtest engine (F-1 fixed — next-bar fill) | ✓ |
| MFF rule engine (F-3 fixed — EOD drawdown) | ✓ |
| Data validator (Wave 5 — all formats) | ✓ |
| Data importer (column alias handling) | ✓ |
| Research runner | ✓ |
| Research gate | ✓ |
| MFF state machine | ✓ |
| Risk guard / kill-switches | ✓ |
| Semi-auto approval queue | ✓ |
| 1m data | **✗ MISSING** |

**The single remaining action: obtain and drop 1m data files.**
