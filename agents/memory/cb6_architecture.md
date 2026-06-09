# CB6 Quantum — Full Account Architecture (Agent Memory)
# Last updated: 2026-06-05

## What CB6 Is
Algorithmic trading bot — NSE Indian markets + Forex prop firm accounts.
Public brand: CB6 Quantum. Owner: Rahul (zzu4309@gmail.com).
All 3 active accounts are REAL MONEY with real P&L.

---

## ACTIVE ACCOUNTS — Priority Order

### 1. GFT $5K 2-Step GOAT ⭐ PRIMARY
- **Capital:** $5,013.62 | **PnL:** +$13.62 (updated 2026-06-05)
- **Phase 1 target:** +$400 (8%) — need +$386.38 more | 2/3 trading days done
- **Phase 2 target:** +$300 (6%) after Phase 1 passes
- **Goal:** Pass both phases → funded $5K master account → withdraw profits → CB6 infrastructure
- **Daily loss limit:** $200 (4%) | **Max total loss:** $500 (10%) = blown
- **Internal guards:** Warn $100 | Reduce 50% at $140 | Hard stop $170/day
- **Risk/trade:** 0.25% = $12.50 normal | 0.30% = $15 A+
- **Symbols:** XAGUSD + USOIL ONLY (XAUUSD PERMANENTLY DISABLED)
- **Kill zones:** London 07-12 UTC | NY 16-20 UTC
- **State:** data/gft_5k/state.json

### 2. GFT $1K Instant ⭐ SECONDARY
- **Capital:** $1,004.07 | **PnL:** +$4.07 (updated 2026-06-05)
- **Goal:** Trade live, withdraw profits freely — real funded account
- **Daily DD limit:** $30 (3%) | **Max DD:** $60 (6%)
- **Internal guards:** Warn $25 | Hard stop $30/day
- **Risk/trade:** $2.50 max | Max lot: 0.01
- **Symbols:** XAGUSD + USOIL ONLY (XAUUSD PERMANENTLY DISABLED)
- **State:** data/gft_1k_instant/state.json

### 3. NSE Fyers — Real Demat ⭐ THIRD
- **Capital:** ₹26,000 REAL MONEY
- **Instruments:** NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY — options + futures ONLY
- **Strategy:** ICT Silver Bullet — CHoCH + BOS + FVG sweep
- **Windows:** 10:00-11:00 IST | 13:00-14:00 IST | 15:00-15:30 IST
- **Data source:** TrueData (primary, trial expires 2026-06-09) → Fyers API fallback
- **State:** data/trade_journal.csv (exits now tracked — fixed 2026-06-05)
- **Goal:** Hit ≥56% WR validated → unlock brokera.in SaaS launch
- **Validated trade 2026-06-05:** NIFTY LONG 23321 CE → +Rs689, R=1.27

### 4. FTMO $10K — DEPRIORITIZED
- **Capital:** ~$9,804 | Code runs as-is, NO new engineering effort
- **State:** data/ftmo_10k/state.json
- **Note:** Best-day cap $250 enforced in ftmo_state.py — do NOT remove

---

## Strategy Rules

### NSE (ICT Silver Bullet)
- Index futures + options ONLY — NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY
- Windows: 10-11, 13-14, 15-15:30 IST
- Required: CHoCH + BOS + FVG sweep (all 3)
- SL: Sweep wick extreme + 10-15pt buffer (NEVER tight 5pt)
- H4 bias check MANDATORY before entry
- **Counter-trend LONG valid at 50% size** when: DOL swept + OB ≥15min + BOS/CHoCH + FVG + kill zone all confirmed
- **CHoCH preferred over BOS** for LONG entries (73–77% of backtested winners used CHoCH)
- **OB duration ≥45min = +1 confluence point** (institutional patience = higher conviction)

### Forex (ICT — GFT accounts)
- Kill zones: London 07-12 UTC | NY 16-20 UTC
- Entry pattern: Sweep DOL → OB accumulation → CHoCH/BOS → FVG fill
- H4 bias MANDATORY — counter-trend only at 50% size when all 5 steps confirmed
- A+ similarity scorer: ≥55% → 1.25× | ≥70% → 1.5× | ≥85% → 2×
- XAUUSD: PERMANENTLY DISABLED on ALL GFT accounts
- **OB duration ≥45min = +1 confluence point** (validated 2026-06-05)

### DOL_SWEEP_OB_BOS_FVG Template — VALIDATED 2026-06-05
Validated on 258 LONG trades across NSE + Forex:
- NSE 55 LONG trades: 61.8% WR | Avg R 1.78
- Forex 203 LONG trades: 60.1% WR | Avg R 1.17
- Combined: 60.5% WR — STRONG EDGE confirmed

4 mandatory features (100% of backtest winners):
1. Sweep confirmed — DOL hunted
2. BOS or CHoCH — structure shift
3. FVG present — entry zone
4. Kill zone — London/NY or Silver Bullet window

---

## Key Files
```
forex_main.py                          — Forex bot (GFT + FTMO)
main.py                                — NSE bot entry
auto_token.py                          — NSE launcher (refreshes Fyers token)
data/trade_journal.csv                 — NSE live trade log (exits NOW tracked)
data/gft_5k/state.json                 — GFT 5K state ($5,013.62)
data/gft_1k_instant/state.json         — GFT 1K state ($1,004.07)
data/ftmo_10k/state.json               — FTMO state (deprioritized)
data/ml/nse/trades.jsonl               — NSE ML data (17 entries, 1 outcome)
data/ml/forex/gft_trades.jsonl         — GFT ML data
forex_engine/scanner/setup_scorer.py   — A+ similarity scorer (updated 2026-06-05)
scanner/silver_bullet.py               — NSE scanner (OB duration added)
forex_engine/scanner/signal_scanner.py — Forex scanner (OB duration added)
ml/nse_collector.py                    — ML collector (ob_duration_mins added)
manual_trade_log.py                    — Log missed trades to ML manually
template_matcher.py                    — Score backtest trades vs template
agents/memory/                         — THIS directory (agent working memory)
agent_reports/                         — Validated reports and concepts
```

---

## Bugs Fixed Today (2026-06-05)
1. RC2: FVG equilibrium used 60-bar lookback (3hr) → now uses today's session range
2. RC4: NSE exits never logged to CSV → fixed in close_paper_trade()
3. RC4b: entry_time undefined bug in log_exit() → fixed
4. NSE exit tracking: log_exit() now called on every trade close
5. ML outcomes: record_outcome() wired correctly, today's trade manually seeded

## Tools Added Today
- `manual_trade_log.py` — log any manually taken trade to ML + CSV + agents
- `template_matcher.py` — score backtest data against DOL template
- `check_ml_state.py` — audit ML JSONL files and trainer state
- `generate_pdf.py` — generate PDF flowcharts from Mermaid
- `send_*.py` scripts — send files/reports to Telegram

---

## $1M Roadmap (Updated 2026-06-05)
1. **Now:** GFT $5K Phase 1 (+$386 needed) + GFT $1K growing
2. **Month 1-2:** GFT $5K funded master account → withdraw profits for CB6 infra
3. **Month 3-4:** NSE WR ≥56% validated | Scale GFT accounts
4. **Month 5+:** brokera.in SaaS launches | Scale to paying users
