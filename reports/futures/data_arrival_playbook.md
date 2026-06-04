# CB6 Futures Core — Data Arrival Playbook
**Date:** 2026-06-01  
**Trigger:** MES_1m.csv and MGC_1m.csv arrive from NinjaTrader + Kinetick  

---

## Pre-Flight: Verify Export Format

Before running the pipeline, open both CSV files in a text editor and check the first 3 lines.

### Format A — WORKS (single combined Date column)
```
Date;Time;Open;High;Low;Close;Volume          ← if only Date column has datetime
20230103 093000;...;...;...;...;...;...
```
OR:
```
Date,Open,High,Low,Close,Volume
01/03/2023 09:30:00,4845.25,4850.00,...
```
If you see a single column with both date and time in it → **proceed**.

### Format B — WORKS
```
Date,Open,High,Low,Close,Volume
20230103 093000,...
```

### Format C — WILL FAIL (separate Date + Time columns)
```
Date,Time,Open,High,Low,Close,Volume
01/03/2023,09:30:00,4845.25,...
```
If you see two separate columns for date and time → **re-export from NinjaTrader** with "Export timestamp as single field" or combine manually.

**Fix for Format C:** In NinjaTrader Historical Data Manager export settings, choose a timestamp format that puts date and time in a single column. Alternatively, open in Excel and create a combined column `=A2&" "&B2` before saving.

---

## Step 1 — Place the Files

```powershell
# Create the directory if it doesn't exist
New-Item -ItemType Directory -Force "c:\cb6_bot\data\futures\historical"

# Copy files
Copy-Item "MES_1m.csv" "c:\cb6_bot\data\futures\historical\MES_1m.csv"
Copy-Item "MGC_1m.csv" "c:\cb6_bot\data\futures\historical\MGC_1m.csv"

# Verify sizes (should be >10 MB each for 2-year 1m data)
Get-Item "c:\cb6_bot\data\futures\historical\MES_1m.csv" | Select-Object Name, Length
Get-Item "c:\cb6_bot\data\futures\historical\MGC_1m.csv" | Select-Object Name, Length
```

Expected sizes: **50–150 MB each** for 2-year 1m data.  
If smaller than 10 MB, re-export — the data is incomplete.

---

## Step 2 — Validate Both Files

Run the validator on each file. Validator runtime: **< 2 minutes per file.**

```powershell
cd c:\cb6_bot

python -m futures_engine.research.futures_data_validator --symbol MES --timeframe 1m
python -m futures_engine.research.futures_data_validator --symbol MGC --timeframe 1m
```

### Interpreting Validator Output

**What you want to see:**
```
MES 1m: PASS
  Bars: 695,432  |  Dups: 0  |  Gaps: 0  |  OHLC: 0
  Range: 2023-01-02 → 2024-12-31
  ! [TIMEZONE] 0/50 sampled bars have no timezone info  ← OK if 0
  [INFO] Expected market closures (weekends/maintenance): 523 — normal
```

**Stop conditions — do NOT proceed to Step 3:**

| Error | Meaning | Fix |
|-------|---------|-----|
| `File not found` | File not placed correctly | Check path |
| `>10 rows failed to parse` | Wrong CSV format | Re-export (see Pre-Flight above) |
| `Empty timestamp` | Timestamp column missing | Wrong delimiter or column names |
| `Duplicate timestamp: N>100` | File has overlapping data | Re-export clean continuous contract |
| `OHLC violations: N>10` | Corrupt price data | Re-export |

**Warnings (proceed with caution):**

| Warning | Meaning | Action |
|---------|---------|--------|
| Intra-session gaps | Missing bars mid-session | Note. May miss some setups. |
| `Only N bars (min 5000)` | Too little data | Re-export longer range |
| Timezone warning | Some bars lack TZ info | Ensure export was UTC |

**FAIL = stop. Fix data. Re-run validator until PASS.**

---

## Step 3 — Run Research Runner

Once validator shows PASS for both symbols:

```powershell
cd c:\cb6_bot

# Run MES for all 3 years, save reports
python -m futures_engine.research.futures_research_runner --symbol MES --year 2023 --save-reports
python -m futures_engine.research.futures_research_runner --symbol MES --year 2024 --save-reports
python -m futures_engine.research.futures_research_runner --symbol MES --year 2025 --save-reports

# Run MGC for all 3 years, save reports
python -m futures_engine.research.futures_research_runner --symbol MGC --year 2023 --save-reports
python -m futures_engine.research.futures_research_runner --symbol MGC --year 2024 --save-reports
python -m futures_engine.research.futures_research_runner --symbol MGC --year 2025 --save-reports
```

**Expected runtime:** 5–15 minutes per symbol per year. Total: 30–90 minutes.

Alternatively, run all years at once:
```powershell
python -m futures_engine.research.futures_research_runner --symbol MES --save-reports
python -m futures_engine.research.futures_research_runner --symbol MGC --save-reports
```

### While It Runs — What to Watch For
- `INFO: No bars for MES ...` → data file is empty or wrong path
- `WARNING: MES_4h MISSING` → expected; 1m file will be used for HTF proxy (limitation)
- `INFO: Backtest done: 0 trades` → signal never fired; check timestamp/timezone of data

**If zero trades for all years:** Data loaded but no signals. Most likely cause: timestamps in local time instead of UTC, placing all bars outside the SB session windows (02:00-16:00 UTC). Confirm export was UTC.

---

## Step 4 — Run the Research Gate

```powershell
cd c:\cb6_bot

# Standard mode
python -m futures_engine.research.futures_research_gate

# Strict 1m mode (recommended — requires 1m data to issue BUY)
python -m futures_engine.research.futures_research_gate --require-1m
```

---

## Step 4 — Read the Gate Output

### Gate Output: BUY
```
OVERALL VERDICT: [✓] BUY  (confidence: HIGH)
► BUY MFF FLEX $25K ACCOUNT — research gate passed.
► Start with 1 MES micro, max $200/day internal stop.
```
→ **Proceed to buy MFF Flex $25K account ($57).** Start with 1 MES micro contract.

### Gate Output: CONDITIONAL
```
OVERALL VERDICT: [~] CONDITIONAL  (confidence: MEDIUM)
► DO NOT BUY YET — conditions below must be met first:
```
→ Read the blocking issues. Common reasons:
- Profit factor below 1.5 in one or more years
- MFF simulation passes only 1/3 years
- Max drawdown exceeds $700 in backtest

**Do not buy. Address each blocker and re-run.**

### Gate Output: DO_NOT_BUY
```
OVERALL VERDICT: [✗] DO_NOT_BUY  (confidence: LOW)
```
→ **Do not buy.** Review gate report in `reports/futures/research/gate_report_*.json`.

---

## Step 5 — Final Buy Decision

```
Validator PASS (both MES and MGC)
    AND
Gate output = BUY (confidence: HIGH)
    AND
Timeframe used = 1m (not 1h)
═══════════════════════════════════
→ BUY MFF Flex $25K ($57)
→ Trade MES first (equity index, most liquid)
→ Risk per trade: 0.5% = $125 on $25K account (1 micro contract)
→ Internal daily stop: $200 hard stop
→ Consistency rule: never make >$750 on any single day
```

If ANY condition above is not met → do NOT buy.

---

## Troubleshooting Quick Reference

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Validator: all rows parse error | Separate Date/Time columns | Re-export combined timestamp |
| Validator: timezone warning | Export in local time, not UTC | Re-export with UTC setting |
| Runner: 0 trades | Timestamps in wrong TZ | Verify UTC; bars should span 13:30-20:00 UTC |
| Runner: trades in only ETH | H4 bias using 1m data | Expected limitation; check if edge still present |
| Gate: CONDITIONAL (PF<1.5) | Strategy doesn't work on this symbol | Don't trade that symbol |
| Gate: CONDITIONAL (1h not 1m) | No 1m file found | Check file path and filename exactly |
