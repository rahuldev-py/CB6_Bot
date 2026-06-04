# TRUEDATA_TRIAL_READINESS.md
# CB6 Quantum — Trial Readiness Audit

**Date:** 2026-05-30
**Engineer:** Principal Quant Architect / Claude Code
**Status:** TRIAL CREDENTIALS NOT YET ISSUED

---

## Critical Context

> **Trial login credentials have NOT been provided by TrueData.**
> Everything in this audit is based on documentation, SDK references, API specifications,
> and Postman collections only.
>
> **NO feature is verified** until tested against an actual TrueData trial account.
> "Integrated" does NOT mean "working." Do not purchase without trial verification.

---

## Feature Classification

### Legend

| Status | Meaning |
|--------|---------|
| `Documented Only` | Feature exists in TrueData docs/Postman but NO code written |
| `Integrated` | Code written based on docs; NOT tested against live endpoint |
| `Trial Verified` | Tested with actual trial credentials; confirmed working |
| `Production Verified` | Running in live production CB6 session |

---

## Feature Matrix

### 1. Authentication

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| Login (user/password → token) | `Integrated` | `provider/truedata/auth.py:52` | POST `/users/login`, 15s timeout |
| Token caching (in-memory) | `Integrated` | `provider/truedata/auth.py:130` | Thread-safe via Lock |
| Auto-refresh (5-min buffer) | `Integrated` | `provider/truedata/auth.py:173` | Relogins before expiry |
| 8-hour default TTL fallback | `Integrated` | `provider/truedata/auth.py:179` | When no expiry in response |
| Logout (token clear) | `Integrated` | `provider/truedata/auth.py:148` | In-memory only; no API endpoint |

**Trial Verification Required:**
- [ ] Actual token format — is field `"token"` or `"access_token"`? Both handled but need confirmation.
- [ ] Actual token TTL — docs say 8h; verify expiry field name (`"expires"` vs `"expiry"`).
- [ ] Sandbox vs production base URL — `config.py` switches via `TRUEDATA_ENV=sandbox`.
- [ ] Whether 401 on bad credentials returns JSON or HTML error page.

---

### 2. Historical Data

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| OHLCV candles (1min–1day) | `Integrated` | `provider/truedata/historical_client.py:60` | Via `get_historic_data()` SDK call |
| Last-N bars | `Integrated` | `data/truedata_feed.py:get_last_n_bars()` | Via `get_n_historical_bars()` SDK call |
| Tick-level historical data | `Integrated` | `provider/truedata/historical_client.py:130` | Day-level tick dump |
| Column normalization | `Integrated` | `data/truedata_feed.py:_normalize_columns()` | Maps TrueData cols → CB6 schema |
| Gap detection | `Integrated` | `provider/truedata/historical_client.py` | Alerts on >50% gap ratio |
| Deduplication | `Integrated` | `provider/truedata/historical_client.py` | By timestamp |
| Fyers fallback on failure | `Production Verified` | `scanner/data_fetcher.py:164` | Works today (Fyers path) |

**Trial Verification Required:**
- [ ] Actual column names returned by `get_historic_data()` — does normalization map correctly?
- [ ] Whether `duration` format is `"30 D"` or `"30d"` or `"30 days"`.
- [ ] Bar sizes — verify all of: `"1 min"`, `"3 mins"`, `"5 mins"`, `"15 mins"`, `"60 mins"`, `"eod"`.
- [ ] Historical depth — how many days back is available? (Docs claim 5+ years for EOD, 90 days intraday.)
- [ ] Weekends / holidays — are gaps present or does the API skip them cleanly?
- [ ] Index symbols — does `"NIFTY-I"` resolve to continuous futures correctly?
- [ ] Response on empty range — does it return empty DataFrame or raise?

---

### 3. Tick Feed (Live WebSocket)

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| WebSocket connection (wss://) | `Integrated` | `provider/truedata/websocket_client.py:97` | `wss://push.truedata.in:8082/8086` |
| Token-based auth on subscribe | `Integrated` | `provider/truedata/websocket_client.py:407` | Token sent in sub message |
| Auto-reconnect (backoff 1s→60s) | `Integrated` | `provider/truedata/websocket_client.py:252` | Exponential backoff |
| Heartbeat (30s ping) | `Integrated` | `provider/truedata/websocket_client.py:309` | `{"method": "heartbeat"}` |
| Sequence gap detection | `Integrated` | `provider/truedata/websocket_client.py:378` | Per-symbol seq tracking |
| Latency tracking | `Integrated` | `provider/truedata/websocket_client.py:366` | Exchange ts vs receive time |
| LTP access (shim) | `Integrated` | `data/truedata_feed.py:get_ltp()` | Via `live.live_data.get(symbol)` |
| Tick → CB6 cache dispatch | `Integrated` | `data/truedata_feed.py:_dispatch_tick()` | Queue + worker thread post-hardening |
| Live feed wired to main.py | `Documented Only` | `scanner/websocket_feed.py:136` | init_truedata() exists but not called |

**Trial Verification Required:**
- [ ] Actual WebSocket URL — `wss://push.truedata.in:8082/8086` — verify port and path.
- [ ] Message format — is `method` field `"tick"` / `"quote"` / something else?
- [ ] Whether `seq` field is present for gap detection.
- [ ] Heartbeat acknowledgment — does server send heartbeat back?
- [ ] Reconnect behavior — what happens if client disappears mid-session?
- [ ] Symbol subscription format — plain `"NIFTY-I"` or exchange-prefixed `"NSE:NIFTY-I"`?
- [ ] Missing tick rate under normal market conditions — critical for scanner reliability.

---

### 4. Bid/Ask Spread Data

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| Bid/Ask in tick message | `Integrated` | `provider/truedata/websocket_client.py:511` | Parsed if present (`bid`, `ask`, `bid_qty`, `ask_qty`) |
| Bid/Ask in MarketTick model | `Integrated` | `provider/truedata/models.py` | Optional fields |
| Bid/Ask consumed by scanner | `Documented Only` | — | No scanner code uses bid/ask yet |

**Trial Verification Required:**
- [ ] Whether bid/ask fields appear in live tick messages.
- [ ] Field names — `"bid"` / `"ask"` or `"best_bid_price"` / `"best_ask_price"`?

---

### 5. Open Interest (OI)

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| OI in live tick | `Integrated` | `provider/truedata/websocket_client.py:509` | Parsed if present (`oi`, `open_interest`) |
| OI in option chain | `Integrated` | `provider/truedata/option_chain.py` | `OptionChainRow.oi`, `oi_change` |
| OI consumed by scanner/ML | `Documented Only` | — | No consumer code yet |

**Trial Verification Required:**
- [ ] Whether OI is in live tick stream or only available via option chain REST call.
- [ ] OI update frequency — per-tick or per-minute?

---

### 6. Option Chain

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| Full chain for underlying | `Integrated` | `provider/truedata/option_chain.py:get_option_chain()` | By underlying + optional expiry |
| ATM-filtered chain | `Integrated` | `provider/truedata/option_chain.py:get_atm_chain()` | ±n_strikes from spot |
| ATM strike detection | `Integrated` | `provider/truedata/option_chain.py:detect_atm()` | CE preferred |
| Consumed by scanner | `Documented Only` | — | Not wired to CB6 signal engine |

**Trial Verification Required:**
- [ ] API endpoint path — `/option-chain/{underlying}` or different structure?
- [ ] Expiry format — `"YYYY-MM-DD"` or `"DD-MMM-YYYY"` (e.g., `"29-MAY-2025"`)?
- [ ] Whether CE and PE appear in the same response or require separate calls.
- [ ] Speed — how quickly does full NIFTY chain (100+ strikes × 2 types) return?

---

### 7. Greeks

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| Greeks for one symbol | `Integrated` | `provider/truedata/greeks_client.py:get_greeks()` | IV, Delta, Gamma, Theta, Vega, Rho |
| Batch Greeks for chain | `Integrated` | `provider/truedata/greeks_client.py:get_chain_greeks()` | Per-symbol error isolation |
| Greeks validation | `Integrated` | `provider/truedata/greeks_client.py:validate_greeks()` | Range checks per field |
| Greeks consumed by scanner | `Documented Only` | — | Not wired to signal engine |

**Trial Verification Required:**
- [ ] Whether TrueData's plan tier includes Greeks (some plans exclude them).
- [ ] IV field name and unit — percentage (e.g., `22.5` = 22.5%) or decimal (0.225)?
- [ ] Delta sign convention for PE — negative or positive?
- [ ] Update frequency — real-time or delayed?

---

### 8. Symbol Master

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| Full symbol master download | `Integrated` | `provider/truedata/symbol_master.py:refresh()` | Cached in memory |
| Filter by segment / index | `Integrated` | `provider/truedata/symbol_master.py` | `get_fo_symbols()`, `get_index_symbols()` |
| Expiry lookup | `Integrated` | `provider/truedata/symbol_master.py` | `get_option_strikes(underlying, expiry)` |
| ATM strike finder | `Integrated` | `provider/truedata/symbol_master.py:get_atm_strikes()` | ±n_strikes from spot |
| Daily refresh schedule | `Documented Only` | — | No scheduled refresh wired |

**Trial Verification Required:**
- [ ] Download URL and authentication method for symbol master.
- [ ] File format — CSV, JSON, or binary?
- [ ] Whether symbol master updates intraday (e.g., on expiry day).
- [ ] TrueData symbol format for option strikes — e.g., `"NIFTY30MAY2425000CE"` or different?

---

### 9. ATM Strike Finder

| Sub-Feature | Status | File | Notes |
|-------------|--------|------|-------|
| Find ATM from symbol master | `Integrated` | `provider/truedata/symbol_master.py:get_atm_strikes()` | Uses spot price vs strike list |
| Find ATM from option chain | `Integrated` | `provider/truedata/option_chain.py:detect_atm()` | CE preferred |
| Wired to signal engine | `Documented Only` | — | Not connected |

**Trial Verification Required:**
- [ ] Strike step size — 50pt for NIFTY, 100pt for BANKNIFTY — does symbol master confirm?
- [ ] Whether weekly vs monthly expiry disambiguation works.

---

## Pre-Trial Checklist

Before the first trial session:

- [ ] `.env` file updated with trial credentials: `TRUEDATA_USER=`, `TRUEDATA_PASSWORD=`
- [ ] `TRUEDATA_ENV=sandbox` set if trial uses sandbox endpoint
- [ ] `trial/config/truedata_trial.yaml` updated with correct underlying symbols and dates
- [ ] `trial/run_truedata_trial.py` reviewed and ready to execute
- [ ] Network connectivity verified to `push.truedata.in:8082` (WebSocket port)
- [ ] Network connectivity verified to REST base URL

---

## Trial Execution Order

Execute in this order to build confidence progressively:

1. **Authentication** — Login, verify token format, test refresh.
2. **Historical Data** — Fetch NIFTY 5-min bars for last 30 days. Verify column names, bar count, gaps.
3. **Symbol Master** — Download and parse. Confirm option symbol format.
4. **Tick Feed** — Subscribe to NIFTY-I. Measure latency, check for gaps, run 30-minute stability test.
5. **Option Chain** — Fetch NIFTY chain. Count strikes, verify CE+PE, test ATM detection.
6. **Greeks** — Fetch Greeks for 10 strikes. Validate IV/Delta ranges.
7. **Bid/Ask** — Confirm bid/ask presence in tick stream.
8. **OI** — Confirm OI in tick stream and/or option chain.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Token field name differs from docs | Medium | Low | Auth handles `"token"` + `"access_token"` both |
| WebSocket URL / port incorrect | Medium | High | Verify in trial before any production use |
| Historical column names differ | High | Medium | Normalization layer handles most variations |
| Greeks not included in plan tier | Medium | Medium | Confirm plan scope with TrueData sales |
| Option chain slow (>2s per call) | Medium | Medium | Cache chain; refresh every 30s not per-signal |
| Symbol master format undocumented | Low | Medium | Inspect file format in trial session |
