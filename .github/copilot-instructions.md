# CB6 Quantum — AI Chat Instructions

## Project Identity
- **Name:** CB6 Quantum (never "CB6_Bot")
- **Purpose:** Algorithmic trading bot — NSE Indian markets + GFT Forex prop firm accounts
- **Platform:** Windows 11, Python 3.13, MT5, Fyers API, Telegram
- **Owner:** Rahul

---

## Absolute Rules — Never Violate

1. **No equity/stock trades** — Index futures + options ONLY (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY).
2. **Never set `paper_mode=True`** in any live config or state file.
3. **Never edit state.json files** without reading them first.
4. **Never enable XAUUSD on GFT** — permanently disabled. GFT active symbols: XAGUSD + USOIL only.
5. **FTMO is deprioritized** — runs as-is, zero new engineering effort.
6. **Always use `encoding='utf-8'`** when opening Python files on Windows.
7. **Never build brokera.in SaaS** until NSE live WR ≥ 56% validated + GFT master account profitable.

---

## Account Priority Order

> ALL THREE active accounts are REAL funded with real money — treat them equally seriously.
> GFT $5K → GFT $1K Instant → NSE Fyers ₹26K. FTMO is deprioritized.

### 1. GFT $5K 2-Step GOAT — PRIMARY
- Real prop firm account. Pass Phase 1 (+$400/8%) then Phase 2 (+$300/6%) → unlock master $5K account → withdraw profits for CB6 infrastructure.
- Daily DD limit: $200 | Max DD: $500 | Min 3 trading days/phase
- Internal guards: warn $100/day | reduce 50% at $140 | hard stop $170
- Risk: 0.25% normal | 0.12% reduced | 0.30% A+
- State: `data/gft_5k/state.json` | Config: `forex_engine/prop_firms/gft/gft_config.py`

### 2. GFT $1K Instant Live — SECONDARY
- Real funded account, withdrawal open immediately.
- Daily DD limit: $30 | Max DD: $60
- Risk: 0.25% = $2.50/trade | Max lot: 0.01
- State: `data/gft_1k_instant/state.json` | Config: `forex_engine/gft_1k_instant/config.py`
- Enable with: `CB6_GFT_1K_INSTANT_ENABLED=true` + `CB6_GFT_1K_INSTANT_LIVE_EXECUTION=true`

### 3. NSE Engine — Fyers Live ₹26,000 REAL MONEY
- Real Fyers account balance ₹26,000 — NOT paper, NOT simulation.
- Index options + futures on NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY ONLY. No equity/stocks ever.
- ICT Silver Bullet strategy: CHoCH + BOS + FVG sweep (all three required).
- Windows: 10:00-11:00 IST | 13:00-14:00 IST | 15:00-15:30 IST
- SL: sweep wick extreme + 10-15pt buffer (never tight 5pt)
- H4 bias check mandatory before every entry
- Data: TrueData primary → Fyers API fallback
- Launch: `python auto_token.py` (refreshes Fyers token, then launches bot)

### 4. FTMO ($10K free trial) — DEPRIORITIZED
- Runs as-is. Do not suggest improvements, fixes, or new features for FTMO.
- State: `data/ftmo_10k/state.json`

---

## Key File Map

```
c:\cb6_bot\
├── main.py                            ← NSE bot entry
├── forex_main.py                      ← Forex bot entry
├── forex_engine/
│   ├── forex_worker.py                ← Core forex signal engine
│   ├── forex_instruments.py           ← Symbol configs
│   ├── prop_firms/gft/
│   │   ├── gft_config.py              ← GFT $5K parameters
│   │   └── gft_5k_2step.py            ← GFT $5K engine (poll=15s)
│   └── gft_1k_instant/
│       ├── config.py                  ← GFT $1K Instant parameters
│       ├── risk.py                    ← $1K risk guards
│       ├── state.py                   ← $1K state management
│       └── monitor.py                 ← $1K monitor
├── data/
│   ├── gft_5k/state.json             ← GFT $5K live state
│   ├── gft_1k_instant/state.json     ← GFT $1K live state
│   └── ftmo_10k/state.json           ← FTMO state (deprioritized)
└── ml/                                ← DNN+CNN+RNN shadow system (never touches orders)
```

---

## Forex Strategy (ICT-based)

- Kill zones: London 07-12 UTC | NY 16-20 UTC
- Entry pattern: Sweep DOL → CHoCH → FVG fill
- A+ boost: ≥55% sim → 1.25× lots | ≥70% → 1.5× | ≥85% → 2×
- H4 bias filter mandatory before entry
- News blackout: no entries within 30 min of high-impact news
- XAUUSD permanently disabled on ALL GFT accounts

## NSE Strategy (ICT Silver Bullet)

- Signals: CHoCH + BOS + FVG sweep (all three required)
- SL: sweep wick extreme + 10-15pt buffer — never tight 5pt
- HTF check: mandatory H4 bias before entry

---

## ML System

- Shadow mode only — predictions logged, never touch orders
- Models: DNN + CNN + RNN per market
- Auto-retrain: every 20 trades or 7 days
- Priority: train on GFT trade data first

---

## Commercial Roadmap

- GFT $5K master account profits + GFT $1K Instant profits = CB6 infrastructure budget (VPS, data feeds, licenses)
- SaaS (brokera.in): DO NOT build until NSE WR ≥ 56% validated + GFT master profitable
