# Portfolio — Rahul (rahuldev-py)

> All projects below are based on production systems I designed and built. Code is proprietary; architecture and outcomes are shared here as engineering proof-of-work.

---

## 1. Multi-Market Trading Infrastructure

**Problem:**
Building a unified system to trade across Indian equity index markets (NSE) and Forex prop firm accounts (MT5) simultaneously, with separate risk rules per account, separate data feeds, and a single control interface.

**Technical Solution:**
- Modular Python architecture with separate engines per market (NSE engine, GFT engine)
- Shared core utilities: logging, error handling, state management
- Per-account state files (JSON) with read-before-write guards
- Single Telegram bot control panel managing both markets with 30+ commands
- Real-time data from two separate sources (Fyers API + MT5 native feed)
- Startup health checks that gate trading on connectivity, balance, and session time

**Stack:**
Python · MT5 Python API · Fyers API · Telegram Bot API · SQLite · JSON state files · asyncio · threading

**Result:**
Live multi-market system running simultaneously on NSE and two MT5 prop firm accounts. Handles market-open spikes, connection drops, and API failures without human intervention.

---

## 2. MT5 Prop Firm Risk Control System

**Problem:**
Prop firm challenges have hard daily loss limits and max drawdown rules. One bad day can blow the account and lose the challenge fee. The trading engine needed automated enforcement — not relying on the trader to stop manually.

**Technical Solution:**
- Per-account internal guards (warn → reduce lots → hard stop) that fire *before* the official limits
- Real-time PnL polling (every 15 seconds) via MT5 API
- Tiered response: warning Telegram alert → 50% lot reduction → full trading halt for the day
- State persisted across restarts so a crash doesn't reset the daily counter
- Separate guards for daily loss and total drawdown

**Stack:**
Python · MetaTrader5 library · JSON state management · Telegram alerts · threading

**Result:**
Zero account violations across multiple prop firm challenge cycles. System caught and responded to drawdown events automatically with no manual intervention required.

---

## 3. Broker API Data Pipeline — Fyers + NSE

**Problem:**
Indian NSE market data is fragmented. TrueData (primary feed), Fyers API (fallback), and historical sources all have different formats, connection patterns, and failure modes. The trading engine needed a unified, reliable feed.

**Technical Solution:**
- Abstracted data layer with primary/fallback switching logic
- TrueData WebSocket handler with auto-reconnect
- Fyers REST API fallback with token auto-refresh
- Data normalisation layer: same OHLCV format regardless of source
- Startup diagnostic that confirms data feed is live before allowing trades
- Token refresh automation (no manual re-auth at market open)

**Stack:**
Python · Fyers API v3 · TrueData API · WebSockets · pandas · asyncio

**Result:**
Uninterrupted data flow across full market sessions. Auto-failover to Fyers when TrueData drops. Token refresh runs automatically — no daily manual login.

---

## 4. Telegram Trading Control Panel

**Problem:**
A live trading bot needs real-time monitoring and control without SSH access or code changes. The trader needs to see status, pause trading, change risk, and get alerts — all from a phone.

**Technical Solution:**
- 30+ Telegram bot commands covering: engine status, trade history, risk controls, manual overrides, ML status, account balances
- HTML-formatted messages with live P&L, open positions, and system health
- Role-based access (only authorised user ID can send commands)
- Separate bots for NSE and Forex engines with independent command sets
- Alert system: trade entries, SL hits, target hits, drawdown warnings, system errors

**Stack:**
Python · python-telegram-bot · asyncio · threading · HTML parse mode

**Result:**
Full trading system control from a mobile phone. No laptop required during market hours. Alerts fire within 2 seconds of any significant event.

---

## 5. Backtesting and Pattern Recognition Database

**Problem:**
Manual trade review doesn't scale. After 100+ trades, patterns are invisible. The system needed to store, query, and score historical setups to identify what actually works.

**Technical Solution:**
- SQLite database storing every trade with 20+ fields: entry pattern, confluence score, session, bias, setup type, outcome
- FTS5 full-text search for fast pattern lookups
- Similarity scorer: compares new live setups against historical winners (DNN-based)
- Auto-backfill from broker trade history
- Query interface via Telegram: `/pattern_search NIFTY LONG CHoCH+FVG`
- Generates statistical reports: win rate by setup, by session, by confluence level

**Stack:**
Python · SQLite (FTS5) · pandas · scikit-learn · Telegram Bot API

**Result:**
1,272 trades indexed. Best edge identified: specific pattern + session combination → 86.8% win rate (n=38). Reports generated on demand in < 3 seconds.

---

## 6. AI-Assisted Trade Learning Loop

**Problem:**
The bot takes trades but doesn't learn from them. Every loss repeats because there's no feedback mechanism between trade outcomes and future decisions.

**Technical Solution:**
- Shadow ML system (DNN + CNN + RNN) trained on historical trade data
- Runs in shadow mode — predictions logged but never used to block or place orders
- Auto-retrains every 20 trades or 7 days (whichever comes first)
- Nudge proposals: system suggests parameter adjustments based on recent performance
- Model registry tracking model versions, training dates, accuracy metrics
- Telegram commands: `/ml_status`, `/ml_train` to view and trigger retraining

**Stack:**
Python · TensorFlow/Keras · scikit-learn · SQLite · pandas · Telegram Bot API

**Result:**
Shadow system running across NSE and Forex trade data. Pattern recognition accuracy improving over time. Nudge proposals reviewed by trader before any live parameter change — safety by design.
