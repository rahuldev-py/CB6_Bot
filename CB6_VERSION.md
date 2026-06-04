# CB6 Quantum — Version Tags

## CB6_TRUEDATA_PRIMARY_V1
**Tagged:** 2026-05-30  
**State:** TrueData activated as primary NSE data source  

### What this tag represents
- `data/truedata_feed.py` rewritten to use official `truedata_ws` library
- `.env` updated: `Trial119` / `rahul119` / port 8086
- All 6 integration phases complete
- 9 reports generated (TRUEDATA_TRIAL_RESULTS.md through CB6_TRUEDATA_DECISION.md)
- `CB6_TRUEDATA_IMPLEMENTATION_FINAL.md` written as permanent record
- `provider/truedata/` archived to `provider/truedata_v1_archived/`
- Zero scanner / strategy / ML changes

### Files changed in this version
- `data/truedata_feed.py` — full rewrite
- `.env` — credentials updated

### Files unchanged
- All scanner, strategy, risk, ML, backtest, Telegram files

### Verified
- 16/16 historical tests: NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY × 1m/3m/5m/15m
- Fallback to Fyers: structural, always active
- Live WS: connected, subscriptions working

### Pending (post this tag)
- Live tick symbol mapping verification (market hours)
- Full trading session WS stability test
- OI-based DOL detection
- OI entry / exit filters
- Bid/Ask FVG validation
- Option intelligence layer
