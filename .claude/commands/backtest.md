# /backtest — Run or Analyze a Backtest

Run or review backtest results for NSE or Forex strategies.

## Usage
```
/backtest XAGUSD          # Analyze latest XAGUSD backtest results
/backtest USOIL           # Analyze USOIL results
/backtest NIFTY           # NSE NIFTY backtest summary
/backtest all             # Summary across all symbols
```

## Steps to Execute

### If analyzing existing results:

1. Search for backtest result files: `Glob("data/backtest_*.json")` or `Glob("data/*backtest*")`
2. Read the most recent result file
3. Display:
```
Backtest Results — [SYMBOL]
────────────────────────────
Period         : YYYY-MM-DD to YYYY-MM-DD
Total trades   : X
Win rate       : XX.X%
Avg R          : X.XX
Total R        : XXX.XX
Max drawdown   : X.X%
Profit factor  : X.XX
Best trade     : +X.XX R
Worst trade    : -X.XX R
```
4. Compare against live performance in state.json (if applicable)
5. Flag if live WR diverges >10% from backtest WR

### Key reference data (Dukascopy 3yr backtest — validated May 2026):
- XAUUSD: 58.6% WR | +1,805R total (FTMO paused, GFT permanently disabled)
- XAGUSD: 15% WR | -160R total ❌ (GFT only — watch carefully)
- USOIL: 34% WR | -139R total ❌ (GFT only — watch carefully)
- NIFTY: Target ≥56% WR before SaaS launch

### If running a new backtest:
1. Check which backtest script exists: `Glob("**/*backtest*.py")`
2. Confirm it reads from Dukascopy data in `data/` directory
3. Run with appropriate symbol argument
4. Save results to `data/backtest_{symbol}_{date}.json`
