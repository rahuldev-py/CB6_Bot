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
4. **XAUUSD is now enabled on all active GFT accounts** — XAUUSD + XAGUSD + USOIL active on GFT $5K and GFT $10K. H4 bias filter mandatory before any Gold entry. Per-account max lots: $10K→0.10, $5K→0.05. (GFT $1K blown 2026-06-13 — disabled.)
5. **Never build the SaaS/brokera.in commercial platform** until NSE live win rate ≥ 56% validated + GFT master account profitable.
6. **FTMO is deprioritized** — do not waste engineering effort on FTMO. Keep existing code running as-is, no new features or debugging effort for FTMO.
7. **Always use `encoding='utf-8'`** when opening Python files for AST parsing on Windows.

---

## Current Account Status (as of 2026-06-05)

> **PRIORITY ORDER: GFT $5K 2-Step → GFT $10K Instant → NSE Fyers → (FTMO last, deprioritized)**
> ACTIVE accounts: GFT $5K 2-Step + GFT $10K Instant (MT5 prop firm) + NSE Fyers ₹26,000 (Indian markets).
> GFT $1K Instant (login 314983765) BLOWN 2026-06-13 — engine disabled. Re-add when new $1K purchased.
> FTMO free trial runs as-is — no new engineering effort.

### GFT $5K 2-Step GOAT ⭐ PRIMARY
- **Capital:** $4,864.37 | **PnL:** -$135.63
- **Phase 1 target:** +$400 (8%) → need **+$535.63 more** + min 3 trading days (have 2)
- **Phase 2 target:** +$300 (6%) after Phase 1 passes → unlocks master $5K account
- **Goal:** Master $5K → withdraw profits → fund CB6 Quantum infrastructure
- **Daily loss limit:** $200 (4%) | **Max total loss:** $500 (10%)
- **Internal guards:** Warn $100 | Reduce 50% at $140 | Hard stop at $170
- **Risk/trade:** 0.50% = $24.32 normal | 0.25% = $12.16 reduced | 0.75% = $36.48 A+ (Phase 1 growth mode — intentional 2× conservative baseline)
- **Active symbols:** XAUUSD + XAGUSD + USOIL (H4 bias mandatory before Gold entry)
- **Kill zones:** London 07-12 UTC | NY 16-20 UTC
- **State file:** `data/gft_5k/state.json`
- **Config:** `forex_engine/prop_firms/gft/gft_config.py`

### GFT $1K Instant — ❌ BLOWN 2026-06-13 (DISABLED)
- **Status:** Account 314983765 failed — max drawdown hit. Engine fully disabled.
- **Re-enable:** When new $1K Instant account purchased → update .env credentials + set `CB6_GFT_1K_INSTANT_ENABLED=true`
- **Code preserved:** `forex_engine/gft_1k_instant/` kept intact for easy re-activation

### NSE Engine — Fyers Live Account ⭐ REAL MONEY
- **Balance:** ₹26,000 real (Fyers broker, not paper)
- **Markets:** NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY — index futures + options ONLY
- **Strategy:** ICT Silver Bullet — CHoCH + BOS + FVG sweep combo
- **Windows:** 10:00-11:00 IST | 13:00-14:00 IST | 15:00-15:30 IST
- **SL rule:** Sweep wick extreme + 10-15pt buffer
- **H4 bias:** Mandatory before entry
- **Data source:** TrueData (primary, trial until Jun 9) → Fyers API fallback
- **Entry:** `python auto_token.py` → refreshes token → auto-launches NSE bot
- **Journal:** `data/trade_journal.csv`

### FTMO Free Trial ($10,000) — DEPRIORITIZED, DO NOT FOCUS
- **Capital:** $9,804.91 | **PnL:** -$195.09 | **Deadline:** ~June 6, 2026
- Keep code running as-is. No new features, debugging, or analysis effort.
- **State file:** `data/ftmo_10k/state.json`

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
│   ├── gft_5k/state.json             ← GFT $5K live state (read before editing)
│   ├── gft_1k_instant/state.json     ← GFT $1K Instant live state (read before editing)
│   └── ftmo_10k/state.json           ← FTMO state (deprioritized, read-only)
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
- **XAUUSD:** Re-enabled on all GFT accounts as of 2026-06-09. H4 bias filter mandatory before every Gold entry. Per-account max lots: $10K→0.10, $5K→0.05, $1K→0.01 (engine auto-skips $1K if SL too wide).
- **GFT $5K + GFT $1K Instant:** XAUUSD + XAGUSD + USOIL active
- **FTMO (deprioritized):** XAGUSD, USOIL, EURUSD — runs as-is, no active tuning
- **H4 bias filter:** Required before any trade entry
- **News blackout:** No entries within 30 min of high-impact news

---

## ML System Notes

- **Shadow mode only** — predictions logged, NEVER used to place or block orders
- **Models:** DNN + CNN + RNN (one set per market: NSE, GFT)
- **Priority:** Train/optimize ML on GFT $5K and GFT $1K Instant trade data first
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
- **GFT poll speed:** Must be 15s (not 30s/60s)
- **GFT kill zones:** Must be `[(7,12),(16,20)]` — NOT the old narrow `[(8,9),(15,16),(19,20)]`
- **GFT $1K Instant:** DISABLED (account blown). To re-enable: set `CB6_GFT_1K_INSTANT_ENABLED=true` + `CB6_GFT_1K_INSTANT_LIVE_EXECUTION=true` + update login/password/server in .env
- **XAUUSD on GFT:** Re-enabled 2026-06-09 with H4 bias filter. Max lots enforced per account. Never disable the H4 filter.
- **SL buffer:** Always sweep wick extreme + 10-15pt for NSE, never tight 5pt
