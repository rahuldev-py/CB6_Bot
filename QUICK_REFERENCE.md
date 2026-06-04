# CB6 QUANTUM — Quick Reference Guide

## 🎯 WHO TAKES TRADES? (Execution Path)

```
Signal Generator (Scanner) 
    ↓ (Entry, SL, T1-T3)
Market Brain (Context filter)
    ↓ (Bias + mode)
Risk Module (Gates)
    ↓ (can_enter decision)
Paper Trader (Position mgmt)
    ↓ (Opens trade in state)
TickWatcher (Real-time monitor)
    ↓ (Fires SL/TP triggers)
Trade Triggers (Execution callbacks)
    ↓ (Closes at levels)
Journal (State persistence)
```

---

## 📊 CORE LOGIC FLOW

### 1. **SIGNAL LOGIC** (Who generates trades?)
```
File: scanner/silver_bullet.py
Entry Condition:
  ├─ Between 10-11 AM OR 1:30-2:30 PM IST
  ├─ Silver Bullet window active
  ├─ MSS (Market Structure Shift) detected
  ├─ FVG (Fair Value Gap) formed
  ├─ Price touches FVG edge
  └─ Entry Signal generated
  
Parameters:
  ├─ Entry: Price at FVG touch
  ├─ SL: Edge of FVG (tightest)
  ├─ T1: Entry + 1×Risk (1:1 R:R)
  ├─ T2: Entry + 2×Risk (2:1 R:R)
  └─ T3: Entry + 3×Risk (3:1 R:R)
```

### 2. **RISK-REWARD LOGIC** (Position sizing)
```
File: core/risk.py & trader/paper_trader.py

Position Size Formula:
  Qty = (Capital × Risk%) / (Entry - SL)
  
Where:
  ├─ Capital = Available cash
  ├─ Risk% = 1.0% per trade (configurable)
  ├─ Entry = Detected entry price
  └─ SL = Stop-loss price
  
Example:
  ├─ Capital: Rs 2,00,000
  ├─ Risk: 1% = Rs 2,000
  ├─ Entry: 1500
  ├─ SL: 1450
  ├─ Risk per share: 50
  └─ Qty = 2000 / 50 = 40 shares

Constraints:
  ├─ Qty × Entry × Margin% ≤ Available Capital
  ├─ Qty must be whole lot size (50 for options, 1 for equity)
  └─ Qty must be ≥ 1 lot (else skip trade)
```

### 3. **EXECUTION LOGIC** (Who actually enters?)
```
File: trader/paper_trader.py → open_paper_trade()

Before Entry:
  ├─ MarketBrain context check (bias, mode)
  ├─ Daily risk gates:
  │  ├─ daily_trades < 8
  │  ├─ daily_losses < Rs 25,000
  │  └─ cumulative_loss < 3% of capital
  ├─ Symbol not already open
  └─ Qty ≥ 1 lot

Entry Action:
  ├─ Create trade object with ID: SYMBOL-DATE-TIME
  ├─ Save to paper_state.json['open_trades']
  ├─ Register SL/TP triggers with TickWatcher
  ├─ Subscribe to symbol on WebSocket
  ├─ Send Telegram alert: "🟢 BUY NIFTYIT @ 1500"
  └─ Update daily_trades++

Trade State:
  ├─ Status: OPEN
  ├─ Entry time: timestamp
  ├─ Capital locked: qty × entry × margin%
  ├─ P&L: Unrealized (updated per tick)
  └─ Triggers: SL, T1, T2, T3 (armed)
```

### 4. **EXIT LOGIC** (Real-time monitoring)
```
File: core/trade_triggers.py (callbacks)

Trigger Registered:
  ├─ watch_trigger(id='NIFTYIT-SL', symbol='NIFTYIT', level=1450, kind=TRIGGER_SL_LONG)
  ├─ watch_trigger(id='NIFTYIT-T1', symbol='NIFTYIT', level=1550, kind=TRIGGER_TP_LONG)
  ├─ watch_trigger(id='NIFTYIT-T2', symbol='NIFTYIT', level=1600, kind=TRIGGER_TP_LONG)
  └─ watch_trigger(id='NIFTYIT-T3', symbol='NIFTYIT', level=1650, kind=TRIGGER_TP_LONG)

On WebSocket Tick (e.g., NIFTYIT @ 1450.00):
  ├─ TickWatcher.on_tick('NIFTYIT', 1450.00)
  ├─ Check all triggers for NIFTYIT
  ├─ Found: SL trigger matches (1450.00 >= 1450.00)
  ├─ Fire callback: _on_sl_hit({symbol, level, meta})
  └─ Execution: close_paper_trade_by_id(..., exit_price=1450.00)

Exit Outcome:
  ├─ Status: CLOSED
  ├─ Exit time: timestamp
  ├─ Exit price: SL level (1450)
  ├─ P&L: (Exit - Entry) × Qty = (1450 - 1500) × 50 = -2,500
  ├─ Reason: SL_HIT
  └─ Move to closed_trades[], update daily_losses
```

---

## 🚦 DECISION GATES (in order)

```
Gate 1: MARKET HOURS
  ├─ Is market open? (9:15 AM - 3:30 PM IST)
  └─ FAIL → Skip trade until market opens

Gate 2: SILVER BULLET WINDOW
  ├─ Is time 10-11 AM OR 1:30-2:30 PM?
  └─ FAIL → Wait for next window

Gate 3: SIGNAL DETECTED
  ├─ FVG + MSS pattern found?
  └─ FAIL → No entry, wait for next scan

Gate 4: MARKET BRAIN
  ├─ Is session in SIT_OUT mode? (consecutive losses > 3)
  ├─ Is confidence < 5? (weak signal)
  └─ FAIL → Skip entry (DEFENSIVE mode active)

Gate 5: RISK GATES
  ├─ Daily trades < MAX_TRADES_PER_DAY (8)?
  ├─ Daily losses < MAX_LOSS_PER_DAY (Rs 25k)?
  ├─ Cumulative DD < 3% of capital?
  └─ FAIL → Stop trading for remainder of day

Gate 6: SYMBOL CHECK
  ├─ Symbol not already open?
  └─ FAIL → Skip (avoid duplicate positions)

Gate 7: POSITION SIZING
  ├─ Calculate Qty = (Capital × Risk%) / (Entry - SL)
  ├─ Qty ≥ 1 lot?
  ├─ Qty × Entry × Margin% ≤ Available Capital?
  └─ FAIL → Insufficient capital, skip

Gate 8: ALL PASSED
  └─ ✅ ENTER TRADE
```

---

## 📈 RISK-REWARD CALCULATION

```
Setup:
  Entry: 1500
  SL: 1450 (20 pts below = 50-30)
  Risk per share: 1500 - 1450 = 50 pts

Targets (at 1:1, 2:1, 3:1 R:R):
  T1: 1500 + 50 = 1550 (1:1 — break even + 1 risk)
  T2: 1500 + 100 = 1600 (2:1 — 2× the risk)
  T3: 1500 + 150 = 1650 (3:1 — 3× the risk)

Position Sizing:
  Risk per trade: 1% of Rs 2,00,000 = Rs 2,000
  Qty = Rs 2,000 / 50 = 40 shares

Outcomes:
  SL Hit @ 1450: Loss = (1450-1500) × 40 = -Rs 2,000 (-1R)
  T1 Hit @ 1550: Profit = (1550-1500) × 40 = +Rs 2,000 (+1R)
  T2 Hit @ 1600: Profit = (1600-1500) × 40 = +Rs 4,000 (+2R)
  T3 Hit @ 1650: Profit = (1650-1500) × 40 = +Rs 6,000 (+3R)

Expected Value (if 60% win rate, avg winner = 2R, avg loser = 1R):
  EV = (0.60 × 2R) - (0.40 × 1R) = 1.2R - 0.4R = +0.8R per trade
```

---

## 🔌 STATE & PERSISTENCE

```
File: data/paper_state.json

Structure:
{
  "capital": 200000,                    // Base capital
  "available_capital": 187500,          // Capital - locked positions
  "open_trades": [
    {
      "id": "NIFTYIT-20260523-1030",
      "symbol": "NSE:NIFTYIT-EQ",
      "direction": "BUY",
      "entry_price": 1500.00,
      "quantity": 40,
      "stop_loss": 1450.00,
      "target1": 1550.00,
      "target2": 1600.00,
      "target3": 1650.00,
      "current_sl": 1450.00,             // Can be trailed
      "status": "OPEN",
      "entry_time": "2026-05-23 10:30:00",
      "ltp": 1545.50,
      "unrealized_pnl": 1820.00,         // (LTP - Entry) × Qty
      "capital_used": 12500.00,          // Qty × Entry × Margin%
      "targets_hit": [],                 // T1, T2, T3 marked here
      "instrument_type": "EQUITY"
    }
  ],
  "closed_trades": [
    {
      "id": "NIFTYIT-20260523-0945",
      "symbol": "NSE:NIFTYIT-EQ",
      "direction": "BUY",
      "entry_price": 1480.00,
      "exit_price": 1530.00,
      "quantity": 35,
      "pnl": 1750.00,                    // Realized P&L
      "exit_time": "2026-05-23 10:15:00",
      "exit_reason": "TARGET_1_HIT",
      "r_multiple": 2.8                  // Profit / Risk per share
    }
  ],
  "daily_trades": 3,                     // Trades entered today
  "daily_losses": 8500.00,               // Sum of losses today
  "total_pnl": 12750.00,                 // P&L this session
  "date": "2026-05-23"
}
```

---

## 📊 KEY METRICS TRACKED

```
Per Trade:
  ├─ Entry price & time
  ├─ Exit price & time
  ├─ Quantity
  ├─ Risk per share (entry - sl)
  ├─ P&L (exit - entry) × qty
  ├─ R-Multiple: P&L / (Risk × Qty)
  ├─ % Return: P&L / Capital Used
  └─ Hold time

Daily:
  ├─ Trades entered
  ├─ Wins & losses
  ├─ Win rate: Wins / (Wins + Losses)
  ├─ Gross P&L
  ├─ Max drawdown
  ├─ Consecutive losses
  ├─ Largest win & loss
  ├─ Profit factor: Total wins / Total losses
  ├─ Average R-multiple
  └─ Expectancy: WR×AvgWin - LR×AvgLoss

Session:
  ├─ Capital start
  ├─ Capital end
  ├─ Total return %
  ├─ Sharpe ratio
  ├─ Max adverse excursion
  └─ Best/worst trade
```

---

## 🎛️ COMMAND INTERFACE (via Telegram)

```
Command              Function
────────────────────────────────────────────────────────
/start              Welcome message
/ask <query>        Claude AI market analysis
/scan               Run equity scanner immediately
/scan_nifty         Run NIFTY/BANKNIFTY scan
/stats              Today's P&L & metrics
/brain              Current market context (bias, confidence)
/portfolio          List all open positions
/close <trade_id>   Manually close a position
/reset              Force daily reset (EOD)
/help               Command reference
```

---

## 🔄 CORE COMPONENTS & OWNERSHIP

| Component | File | Owns | Responsibility |
|-----------|------|------|-----------------|
| **Scanner** | scanner/silver_bullet.py | Signals | Entry detection (FVG+MSS) |
| **Brain** | core/market_brain.py | Context | Session bias & trade mode |
| **Risk** | core/risk.py | Gates | Qty sizing & trade-gating |
| **Trader** | trader/paper_trader.py | State | Position lifecycle mgmt |
| **Watcher** | core/tick_watcher.py | Triggers | Real-time price monitoring |
| **Triggers** | core/trade_triggers.py | Callbacks | SL/TP firing & exit |
| **Journal** | core/metrics.py | Analytics | P&L tracking & stats |
| **Orchestrator** | orchestrator.py | Lifecycle | Engine startup/restart |

---

## 🚀 STARTUP SEQUENCE

```
1. User: python orchestrator.py
   ├─ Reads .env (API keys, tokens)
   ├─ Launches main.py (NSE engine)
   ├─ Launches forex_main.py (FOREX engine)
   └─ Sends Telegram startup alert

2. main.py (NSE engine):
   ├─ Loads paper_state.json
   ├─ Authenticates with Fyers API
   ├─ Initializes WebSocket tick feed
   ├─ Re-arms triggers for open trades
   ├─ Starts scan loop (5-15 min interval)
   └─ Background: Telegram listener

3. Scan loop (while market open):
   ├─ Check Silver Bullet window
   ├─ Fetch 15-min candles for all symbols
   ├─ Detect FVG+MSS patterns
   ├─ If signal: risk gates → sizing → entry
   └─ Sleep 5-15 minutes, repeat

4. WebSocket thread (background):
   ├─ Receives live ticks
   ├─ Updates _tick_cache
   ├─ Evaluates all triggers
   ├─ Fires SL/TP callbacks
   └─ Continuous (no sleep)

5. Telegram listener (background):
   ├─ Polls for /commands
   ├─ Executes scans, stats, closes, resets
   └─ Sends responses
```

---

## 🎯 TYPICAL TRADE LIFECYCLE (Timestamps)

```
10:30:00  → Signal detected: NIFTYIT FVG @ 1500
10:30:05  → Risk gates passed ✅
10:30:10  → Qty calculated: 40 shares
10:30:15  → Trade entered, triggers armed
10:30:20  → Telegram alert: "🟢 BUY NIFTYIT @ 1500"
10:35:00  → LTP: 1512 (unrealized +480)
10:45:00  → LTP: 1545 (unrealized +1800)
10:55:00  → Tick: NIFTYIT @ 1550.00
10:55:02  → T1 trigger fires! Partial close at 1550
10:55:05  → Telegram: "✅ T1 HIT @ 1550 | +Rs 2000"
11:05:00  → Tick: NIFTYIT @ 1600.00
11:05:02  → T2 trigger fires! Partial close at 1600
11:05:05  → Telegram: "✅ T2 HIT @ 1600 | +Rs 4000"
11:25:00  → Tick: NIFTYIT @ 1430.00
11:25:02  → SL trigger fires! Full close at 1450
11:25:05  → Telegram: "🔴 SL HIT @ 1450 | -Rs 2000"
11:25:10  → Trade CLOSED, P&L logged, metrics updated
```

---

## 💡 CRITICAL SUCCESS FACTORS

1. **Tight SL**: SL must be at FVG edge (minimum loss)
2. **1:1 Minimum R:R**: T1 at least breakeven + risk
3. **Qty Discipline**: Risk only 1% per trade, scale with capital
4. **Real-Time Execution**: WebSocket ticks = fast SL/TP firing
5. **Daily Risk Cap**: Stop trading if losses > 3% daily
6. **Session Context**: MarketBrain shifts to DEFENSIVE on consecutive losses
7. **State Persistence**: All trades saved; restart-safe
8. **Telegram Alerts**: Real-time feedback on every trade event

---

## 🔐 SAFETY MECHANISMS

```
Level 1: Pre-Entry Gates
  ├─ Market hours check
  ├─ Window timing validation
  ├─ Risk gate evaluation
  └─ Daily limit checks

Level 2: Position Size Cap
  ├─ Qty × Entry × Margin% ≤ Available Capital
  └─ Qty ≥ 1 lot (no fractional lots)

Level 3: Real-Time SL
  ├─ TickWatcher monitors continuously
  ├─ SL fires immediately on price touch
  └─ No manual intervention needed

Level 4: Daily Risk Cap
  ├─ Stop new trades if cumulative loss > 3%
  ├─ MarketBrain shifts to DEFENSIVE mode
  └─ Requires manual reset next day

Level 5: Kill Switch
  ├─ Create data/kill_all.flag to stop all engines
  ├─ Optional token-based authentication
  └─ Graceful shutdown with state save
```

---

*Last Updated: May 23, 2026*  
*CB6 Quantum v1.0 Architecture Summary*
