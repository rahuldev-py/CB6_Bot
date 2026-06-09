# ATLAS Engineering Standup — 2026-06-09 09:10

## 🟡 YELLOW — Codebase has syntax errors and TODOs in files like silver_bullet.py, signal_scanner.py, and gft_5k_2step.py, and signal logic issues in forex_instruments.py

## Priority Tasks for FORGE
- **[URGENT]** `forex_engine/forex_instruments.py` — Verify XAUUSD is blocked for GFT in forex_instruments.py → FORGE
- **[HIGH]** `scanner/silver_bullet.py` — Fix Bug 4 in silver_bullet.py → SHADOW
- **[HIGH]** `forex_engine/scanner/signal_scanner.py` — Fix Bug 2 in signal_scanner.py → CIPHER
- **[MEDIUM]** `forex_engine/forex_instruments.py` — Update GFT kill zones in forex_instruments.py → FORGE

## Optimization Opportunities
- Optimize lot size calculation in forex_instruments.py

## TODOs Found (3 total)
- `scanner/silver_bullet.py:493`
- `forex_engine/scanner/signal_scanner.py:462`
- `forex_engine/prop_firms/gft/gft_5k_2step.py:658`

## Standup
The codebase has syntax errors and TODOs in several files, including silver_bullet.py, signal_scanner.py, and gft_5k_2step.py. There are also signal logic issues in forex_instruments.py that need to be addressed. The top priority tasks include verifying XAUUSD is blocked for GFT, fixing Bug 4 in silver_bullet.py, and fixing Bug 2 in signal_scanner.py. Additionally, the GFT kill zones need to be updated in forex_instruments.py.
