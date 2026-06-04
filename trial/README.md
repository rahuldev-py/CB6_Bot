# TrueData Integration Trial — CB6 Quantum

## Overview

This trial suite evaluates TrueData as a market data provider for CB6 Quantum.
It scores the integration across 9 dimensions (0–100 points) and produces a
**CB6 Fit Score** to guide the go/no-go decision.

## Setup

1. Add credentials to `.env` (project root):

```env
TRUEDATA_USER=your_username
TRUEDATA_PASSWORD=your_password
TRUEDATA_ENV=sandbox        # or "live"
```

2. Install dependencies (already in requirements.txt):

```powershell
pip install httpx websockets pydantic python-dotenv pytz numpy
```

## Running the Trial

```powershell
# Full trial (15 min live feed + historical + options + greeks)
python trial/run_truedata_trial.py

# Sandbox, 5 minute live feed only
python trial/run_truedata_trial.py --env sandbox --duration 5 --skip-historical --skip-options --skip-greeks

# Skip live feed (historical + options + greeks only)
python trial/run_truedata_trial.py --skip-live

# Live environment, 30 min feed
python trial/run_truedata_trial.py --env live --duration 30
```

## Output

All reports are saved to `data/truedata/reports/`:

| File | Description |
|------|-------------|
| `trial_summary.json` | Full machine-readable results |
| `trial_report.md` | Human-readable report |
| `fit_score.md` | Compact score card |
| `latency_stats.csv` | Per-symbol latency stats |
| `missing_data.csv` | Gap/duplicate counts per interval |

Tick CSVs: `data/truedata/trial_ticks/`
Candle CSVs: `data/truedata/trial_candles/`
Option chain: `data/truedata/trial_option_chain/`
Greeks CSV: `data/truedata/trial_greeks/`

## Fit Score Dimensions

| Dimension | Weight | What It Measures |
|-----------|--------|-----------------|
| Auth Stability | 10 | Login success, token caching, refresh |
| WebSocket Stability | 20 | All 4 symbols streaming, no disconnects |
| Tick Quality | 15 | Sequence gaps, duplicate ticks |
| Candle Quality | 15 | Bar gaps, duplicates during market hours |
| Historical Availability | 10 | 1m/3m/5m/15m intervals all returning data |
| Option Chain Quality | 10 | OI present, bid/ask non-zero, ATM correct |
| Greeks Availability | 10 | IV non-null, delta in range, gamma/theta valid |
| Latency p95 | 5 | p95 < 500ms = full 5 pts |
| Error Handling | 3 | All tests ran gracefully despite any failures |
| Integration Complexity | 2 | TrueData has clean REST+WS API = 2 pts |

## Score Interpretation

| Score | Label | Recommendation |
|-------|-------|---------------|
| 85–100 | Excellent | Proceed immediately |
| 70–84 | Good | Integrate with monitoring |
| 55–69 | Acceptable | Integrate with caution, longer trial |
| 40–54 | Marginal | Fix issues before integrating |
| 0–39 | Poor | Do not integrate, contact TrueData |

## File Structure

```
trial/
├── __init__.py
├── README.md                    ← This file
├── run_truedata_trial.py        ← Main orchestrator
├── test_live_feed.py            ← WebSocket quality test
├── test_historical.py           ← Historical candle test
├── test_option_chain.py         ← Option chain test
├── test_greeks.py               ← Greeks test
└── trial_report.py              ← Report generator

provider/truedata/
├── __init__.py
├── exceptions.py
├── models.py
├── config.py
├── auth.py
├── rest_client.py
├── historical_client.py
├── symbol_master.py
├── option_chain.py
├── greeks_client.py
└── websocket_client.py

market_data/
├── __init__.py
├── interfaces.py
├── normalizer.py
├── event_bus.py
├── candle_builder.py
├── tick_store.py
└── health_monitor.py
```
