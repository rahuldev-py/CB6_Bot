# Case Study: Building CB6 Quantum
## A Multi-Market Trading Automation Infrastructure

---

## Overview

CB6 Quantum is a production algorithmic trading infrastructure I designed, built, and operate. It runs live across two separate markets simultaneously — Indian NSE index futures/options and Forex prop firm accounts via MetaTrader 5 — with real capital at risk.

This case study documents the engineering decisions, architecture, integrations, and lessons. It is written as proof of technical capability, not as financial advice or a signal service.

---

## The Problem

I needed a system that could:

1. Trade multiple markets with different instruments, sessions, and rules
2. Connect to two different broker ecosystems (Fyers for NSE, MT5 for Forex)
3. Enforce strict risk controls per account, automatically, without manual oversight
4. Run 24/7 without crashing, losing state, or missing market events
5. Be fully controllable from a mobile phone with no laptop required
6. Learn from its own trade history over time

No off-the-shelf solution covered all of this. Everything had to be built from scratch.

---

## Architecture

```
CB6 Quantum
├── NSE Engine (main.py)
│   ├── Data layer: TrueData (primary) / Fyers API (fallback)
│   ├── Signal scanner: CHoCH + BOS + FVG detection
│   ├── H4 bias filter (mandatory before entry)
│   ├── Order executor: Fyers API
│   └── Telegram control bot (30+ commands)
│
├── Forex Engine (forex_main.py)
│   ├── GFT $5K 2-Step engine (MT5)
│   ├── GFT $10K Instant engine (MT5)
│   ├── Risk guard: daily DD + total DD per account
│   ├── Kill zone filter: London / New York sessions only
│   └── Forex Telegram bot (17 commands)
│
├── ML System (ml/)
│   ├── DNN + CNN + RNN models (shadow mode)
│   ├── Auto-retrain: every 20 trades or 7 days
│   └── Pattern similarity scorer
│
└── Shared Core
    ├── Trade pattern database (SQLite FTS5)
    ├── State management (JSON per account)
    ├── Notification system (Telegram)
    └── Logging + error recovery
```

---

## Broker and API Integrations

### Fyers API (NSE)
- OAuth2 token flow with automated daily refresh
- Real-time market data via WebSocket
- Order placement, modification, cancellation
- Position and order book monitoring
- GTT (Good-Till-Triggered) stop-loss orders placed server-side after every fill

### MetaTrader 5 (Forex — GFT)
- MT5 Python library integration
- Two separate accounts with separate state tracking
- 15-second polling for position monitoring
- Order send, modify, close via Python
- Account info, margin level, balance monitoring

### TrueData (NSE Real-Time Feed)
- WebSocket connection with auto-reconnect
- OHLCV streaming for 4 indices
- Fallback to Fyers API on disconnect

---

## Data Pipeline

1. **Market data ingestion** — WebSocket streams from TrueData/Fyers normalised to a unified OHLCV format
2. **Multi-timeframe analysis** — H4 bias computed at session start, 15m/5m scanned every tick
3. **Signal detection** — CHoCH (Change of Character), BOS (Break of Structure), FVG (Fair Value Gap) identified in real time
4. **Pattern scoring** — each setup scored against historical winners via ML similarity model
5. **Trade storage** — every trade written to SQLite with 20+ metadata fields
6. **Backtesting pipeline** — 3+ years of historical data (Dukascopy for Forex, Fyers for NSE) processed through the same signal engine

---

## Risk Control System

This was the most critical component. One bad day on a prop firm account loses the challenge fee.

### Per-Account Guards (Forex)
```
Internal warning:    fires before official limit
Lot reduction:       50% size reduction at -70% of daily limit
Hard stop:           trading halted for the day at -85% of daily limit
```

### Per-Account Guards (NSE)
```
Risk per trade:     fixed % of account balance (not fixed rupees)
Daily limit:        tracked in state file, checked before every order
GTT stop-loss:      placed at broker level after every fill (server-side)
```

### State Persistence
- All counters stored in JSON state files
- Read-before-write guard on all state mutations
- Survives bot restart — daily counters not reset by crashes

---

## Telegram Control System

The entire system is controllable via Telegram from a phone. No SSH. No laptop.

**NSE Bot commands include:**
- `/nse_status` — engine health, today's trades, balance
- `/sb` — trigger Silver Bullet signal scan
- `/stop` / `/resume` — halt or resume trading
- `/risk` — view current risk settings
- `/ml_status` — ML model performance
- `/eventmode on` — switch to conservative mode during news events

**Forex Bot commands include:**
- `/gft_status` — both GFT accounts, P&L, open positions
- `/gft_pause` — halt all Forex trading
- `/daily_report` — auto-generated daily summary

All messages use Telegram's HTML parse mode for formatted, readable output.

---

## Backtesting Layer

- 258+ historical NSE setups validated and stored
- 3-year Forex data (XAUUSD, XAGUSD, USOIL) backtested
- SQLite database with FTS5 full-text search
- Pattern recognition: 209 unique setup combinations analysed
- Best-performing edge: 86.8% win rate on specific pattern + session combination (n=38)

---

## AI-Assisted Research Workflow

The ML system runs in shadow mode — it observes trades but never places or blocks orders.

- **DNN:** Classifies setup quality from price features
- **CNN:** Identifies chart pattern signatures from OHLCV sequences
- **RNN:** Captures time-series momentum context
- **Auto-retrain:** Every 20 trades or 7 days
- **Nudge proposals:** System suggests parameter adjustments; trader reviews before applying
- **Model registry:** Tracks all model versions, accuracy, and training history

Shadow mode was an intentional design choice. ML predictions inform the trader without overriding human judgment on live capital.

---

## Lessons Learned

1. **State management is everything.** Bots crash. The question is whether they recover correctly. JSON state files with read-before-write guards solved this.

2. **Risk controls must be in code, not discipline.** Human willpower fails under drawdown pressure. Automated hard stops don't.

3. **API reliability is the real problem.** Trading logic is 30% of the work. Data feed reliability, token refresh, reconnect logic, and error recovery is 70%.

4. **Build the alert system first.** If you can't see what the bot is doing in real time, you can't trust it with real money.

5. **Shadow ML before live ML.** Running predictions in shadow mode for 3+ months before trusting them with capital is not optional — it's necessary.

6. **Prop firm rules are a constraint engine.** Building to prop firm rules forced better risk discipline than I'd have applied voluntarily.

---

## What This Proves I Can Build for Clients

| Capability | Evidence |
|-----------|---------|
| Broker API integration | Fyers API (NSE), MT5 (Forex), TrueData — all live |
| Real-time data pipelines | WebSocket feeds with fallback and auto-reconnect |
| Order execution systems | Both Indian and Forex markets, live capital |
| Risk control automation | Multi-tier guards, state persistence, auto-halt |
| Telegram bot development | 30+ commands, HTML formatting, real-time alerts |
| SQLite / database design | FTS5 pattern DB with 1,272 trades indexed |
| ML integration | DNN/CNN/RNN shadow system, auto-retrain pipeline |
| Production deployment | Running 24/7 on VPS, auto-restart on crash |
| System architecture | Modular design, separate engines, shared core |

If you need any of these capabilities built for your project, I can do it. I've already done it with real money on the line.

---

*This case study describes engineering infrastructure only. Past performance of any trading system is not indicative of future results. No financial advice is expressed or implied.*
