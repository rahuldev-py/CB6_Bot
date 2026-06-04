# CB6 Quantum — Claude Code Project Instructions

## Project Identity
- **Brand name:** CB6 Quantum (never "CB6_Bot" in public-facing text)
- **Owner:** Rahul (zzu4309@gmail.com)
- **Purpose:** Algorithmic trading bot — NSE Indian markets + Forex prop firm accounts
- **Platform:** Windows 11, Python, MT5, Fyers API, Telegram

---

## Absolute Rules (NEVER violate)

1. **No equity/stock trades** — Index futures + options ONLY (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY). Zero exceptions.
2. **Never set `paper_mode=True`** in any live config or state file. Paper mode is for testing only.
3. **Never manually edit state.json files** without reading them first and confirming the edit is safe.
4. **Never enable XAUUSD on GFT** — permanently disabled. GFT symbols: XAGUSD + USOIL only.
5. **Never build the SaaS/brokera.in commercial platform** until NSE live win rate ≥ 56% validated + GFT funded account profitable.
6. **Never skip the best-day PnL cap check** on FTMO — $250/day hard cap enforced in code.
7. **Always use `encoding='utf-8'`** when opening Python files for AST parsing on Windows.

---

## Current Account Status (as of 2026-06-04)

### FTMO Free Trial ($10,000)
- **Capital:** $9,891.91 | **PnL:** -$108.09
- **Target:** +$500 (5%) → need **+$608 more**
- **Deadline:** ~June 6, 2026 (~2 trading days)
- **Daily loss limit:** $300 (3%) | **Best-day cap:** $250
- **Risk/trade:** 0.7% = $70/trade (sprint mode)
- **Active symbols:** XAGUSD, USOIL, EURUSD (XAUUSD paused after 3-loss disaster May 22)
- **State file:** `data/ftmo_10k/state.json`
- **Config:** `forex_engine/prop_firms/ftmo/ftmo_config.py`

### GFT $5K 2-Step GOAT (ONE account, not three)
- **Capital:** $4,967.00 | **PnL:** -$33.00
- **Phase 1 target:** +$400 (8%) → need **+$433.00 more** + 3 trading days
- **Phase 2 target:** +$300 (6%) after Phase 1 passes
- **Goal:** Funded real $5K → scale to $10K → fund CB6 Quantum infrastructure
- **Daily loss limit:** $200 (4%) | **Max total loss:** $500 (10%)
- **Risk/trade:** 0.25% = $12.50 normal | 0.12% = $6 reduced | 0.30% = $15 A+
- **Active symbols:** XAGUSD + USOIL (XAUUSD PERMANENTLY DISABLED)
- **Kill zones:** London 07-12 UTC | NY 16-20 UTC
- **State file:** `data/gft_5k/state.json`
- **Config:** `forex_engine/prop_firms/gft/gft_config.py`

---

## Key File Map

```
c:\cb6_bot\
├── CLAUDE.md                          ← THIS FILE
├── main.py                            ← NSE bot entry point
├── forex_main.py                      ← Forex bot entry point
├── set_bot_commands.py                ← Register Telegram menus (run after command changes)
│
├── forex_engine/
│   ├── forex_worker.py                ← Core forex signal engine (both accounts)
│   ├── forex_instruments.py           ← Symbol configs, FTMO active symbols
│   ├── prop_firms/
│   │   ├── ftmo/
│   │   │   ├── ftmo_config.py         ← FTMO challenge parameters
│   │   │   ├── ftmo_state.py          ← State machine + best-day cap enforcement
│   │   │   └── ftmo_10k.py            ← FTMO account engine
│   │   └── gft/
│   │       ├── gft_config.py          ← GFT challenge parameters
│   │       └── gft_5k_2step.py        ← GFT account engine (poll=15s live)
│
├── communications/
│   ├── forex_bot.py                   ← Forex Telegram bot (HTML menus)
│   └── telegram_bot.py                ← NSE Telegram bot
│
├── utils/
│   ├── bot_listener.py                ← NSE bot command handler (HTML /start)
│   └── ...
│
├── data/
│   ├── ftmo_10k/state.json            ← FTMO live state (read before editing)
│   └── gft_5k/state.json             ← GFT live state (read before editing)
│
├── ml/
│   └── ...                            ← DNN+CNN+RNN shadow ML system (shadow only, never touches orders)
│
└── .claude/
    ├── settings.json                  ← Permissions + hooks
    ├── commands/                      ← Slash commands
    ├── agents/                        ← Specialized subagents
    ├── hooks/                         ← PreToolUse/PostToolUse hooks
    └── rules/                         ← Additional rule files
```

---

## NSE Strategy Rules (ICT Silver Bullet)

- **Markets:** NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY — futures + options ONLY
- **No equity/stocks ever**
- **Windows:** 10:00-11:00 IST, 13:00-14:00 IST, 15:00-15:30 IST (Silver Bullet windows)
- **Signal:** CHoCH + BOS + FVG sweep combo
- **SL rule:** Sweep wick extreme + 10-15pt buffer (not tight 5pt SL)
- **HTF check:** Mandatory H4 bias check before entry
- **Lot sizes:** From CSV + `index_futures.py` (refresh when SEBI revises)
- **Data source:** Fyers API (paid NSE real-time feed coming)

## Forex Strategy Rules (ICT-based)

- **Kill zones:** London 07-12 UTC | NY 16-20 UTC
- **Entry pattern:** Sweep DOL → CHoCH → FVG fill
- **A+ setup:** ≥55% similarity score → 1.25× lots | ≥70% → 1.5× | ≥85% → 2×
- **XAUUSD:** Paused on FTMO (3-loss disaster May 22 vs H4 uptrend), PERMANENTLY DISABLED on GFT
- **GFT:** XAGUSD + USOIL only
- **FTMO:** XAGUSD, USOIL, EURUSD
- **H4 bias filter:** Required before any trade entry
- **News blackout:** No entries within 30 min of high-impact news

---

## ML System Notes

- **Shadow mode only** — predictions logged, NEVER used to place or block orders
- **Models:** DNN + CNN + RNN (one set per market: NSE, FTMO, GFT)
- **Auto-retrain:** Every 20 trades or 7 days (whichever comes first)
- **Commands:** `/ml_status`, `/ml_train` on both Telegram bots
- **Location:** `ml/` directory

---

## How to Run

```powershell
# NSE bot
python main.py

# Forex bot (FTMO + GFT)
python forex_main.py

# Register Telegram command menus (run once after any command change)
python set_bot_commands.py

# Syntax check a Python file (always use utf-8 encoding)
python -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('OK')"
```

---

## Telegram Bots

| Bot | Token env var | Purpose |
|-----|--------------|---------|
| NSE Bot | `TELEGRAM_BOT_TOKEN` | Indian markets trading control |
| Forex Bot | `FOREX_TELEGRAM_TOKEN` | FTMO + GFT prop firm control |

- Both use HTML `parse_mode='HTML'` for formatted messages
- Menus registered via `setMyCommands` API
- NSE bot: 29 commands | Forex bot: 17 commands

---

## Brand / Commercial Roadmap

- Public name: **CB6 Quantum** (not CB6_Bot)
- SaaS platform (brokera.in): **DO NOT BUILD** until:
  1. NSE live win rate ≥ 56% (validated over 3+ months)
  2. GFT funded account profitable
  3. Infrastructure funded from prop firm profits
- Crypto engine: Shelved until above conditions met

---

## Common Pitfalls

- **Windows encoding:** Always `encoding='utf-8'` when reading Python files
- **State files:** Always read before any edit; never corrupt JSON structure
- **GFT poll speed:** Must be 15s (not 30s/60s) for simultaneous FTMO+GFT entries
- **GFT kill zones:** Must be `[(7,12),(16,20)]` — NOT the old narrow `[(8,9),(15,16),(19,20)]`
- **FTMO best day cap:** $250 hard cap — already coded in `ftmo_state.py`, don't remove
- **SL buffer:** Always sweep wick extreme + 10-15pt for NSE, never tight 5pt
