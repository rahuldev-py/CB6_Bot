# NSE Trading Rules — CB6 Quantum

## Absolute Constraints
- **Index futures + options ONLY** — NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY
- **Zero equity/stock trades** — ever, under any circumstance
- **No crypto** — shelved until prop firm phase complete

## ICT Silver Bullet Strategy
- **Windows:** 10:00-11:00 IST | 13:00-14:00 IST | 15:00-15:30 IST
- **Required signals:** CHoCH + BOS + FVG sweep (all three, not just one)
- **SL placement:** Sweep wick extreme + 10-15pt buffer (never tight 5pt)
- **HTF check:** Mandatory H4 bias before entry — no counter-trend trades

## Index Configuration
All 4 indices are active (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY):
- Lot sizes: From CSV + `index_futures.py` (refresh when SEBI revises)
- Instruments: Index futures for intraday, options for directional plays

## Data Source
- Current: Fyers API (live feed)
- NSE real-time paid feed: Coming soon (user will add)
- Historical: `data/nse_eod.py` kept as reference for data shape

## Win Rate Gate for SaaS
- Current live WR must reach ≥56% (validated over 3+ months) before:
  - Building brokera.in SaaS platform
  - Launching commercial offering
  - Adding more users

## Telegram Commands (NSE Bot)
29 commands registered. Key operational ones:
- `/sb` — trigger Silver Bullet scan
- `/scan` — full index scan
- `/nse_status` — engine health + today's trades
- `/stop` / `/resume` — trading control
- `/eventmode on|off` — crisis filter
