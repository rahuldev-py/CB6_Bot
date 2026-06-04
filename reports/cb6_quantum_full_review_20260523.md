# CB6 Quantum Full Review - 2026-05-23

## Code Fixes Applied

- `forex_engine/forex_main.py --profile ALL` now launches FTMO and GFT as separate subprocesses instead of threads. This avoids MT5 singleton/session collision between accounts.
- NSE and Forex Telegram command listeners now validate authorized chat IDs before parsing commands and apply a short per-command cooldown.
- `orchestrator.py` kill flag can now be protected with `ORCHESTRATOR_KILL_TOKEN`; when set, `data/kill_all.flag` must contain the token.
- NSE option paper trades now persist `product_type`, so NRML/MIS handling survives state reloads and overnight cleanup.

## NSE Option Target Audit

- `trader/order_manager.py` converts underlying ICT levels into option-premium SL/T1/T2/T3 before calling `open_paper_trade`.
- `trader/paper_trader.py` monitors the option symbol itself and treats CE/PE as long premium for P&L and target logic.
- `core/trade_triggers.py` registers WebSocket triggers on the paper trade symbol, which is the option contract for option trades.

Conclusion: current T1/T2/T3 hit logic is premium-based for options, not underlying-price based.

## Strategy Logic Map

Scanner:
- Market data fetch: 5m NSE index futures, 15m forex/commodities.
- Liquidity sweep detection.
- Draw on liquidity.
- MSS/CHoCH/BOS structure shift.
- FVG detection and entry-zone validation.
- Optional order block, UT Bot, H1/H4 bias, displacement, and A+ similarity scoring.

Filters:
- Market/session windows.
- Rollover/news blocks for forex.
- Symbol allowlist and disabled-symbol guard.
- Spread/slippage/liquidity checks.
- Score gate and pattern-library confidence.
- H1/H4 directional alignment.
- Fresh sweep and in-FVG hard gates on forex/GFT.

Guards:
- Daily loss, max loss, profit cap, best-day rule, risk reduction mode.
- Anti-hedge and anti-HFT guards for GFT.
- Duplicate trade and repeated FVG-zone guards.
- Telegram stop/pause flags.

Execution:
- NSE options: select CE/PE strike, refresh premium, validate volume/spread/book, size by premium spend, open paper/live order.
- FTMO/GFT: calculate MT5 lots from account risk and SL distance, place MT5 order, adjust SL/targets after fill when needed.

Monitor:
- SL, T1/T2/T3, BE trigger, MAE exit, time exit, rollover danger checks.
- State update, Telegram alert, journal write, lesson capture.

## FTMO vs GFT Rules

FTMO:
- Account: 10K free trial profile.
- Risk: 0.7% base in current config.
- Daily guard: internal 3% stop.
- Total DD: trailing/end-of-day style guard in FTMO state.
- Best-day rule tracked.
- Sessions: London 07-12 UTC, NY 16-20 UTC.
- Symbols: XAGUSD, USOIL, EURUSD active; XAUUSD paused.

GFT 5K 2-Step:
- Account: 5K profile.
- Risk: 0.2% normal, 0.1% reduced, 0.25% A+ max.
- Daily official loss: 4%.
- Total official loss: 10% static floor.
- Anti-HFT minimum hold/trade-spacing guards.
- Sessions: 08-09, 15-16, 19-20 UTC.
- Symbols: XAGUSD, USOIL only; XAUUSD disabled.

## Parameter Review

- GFT 0.2% / 0.1%: keep until live spread/slippage sample is available. Increase only after 20+ live GFT fills show stable spread and no HFT/fingerprint flags.
- FTMO 0.7%: aggressive sprint risk. Safe only because daily loss guard and best-day rule exist; reduce to 0.5% if two consecutive SL/MAE exits occur in one day.
- XAGUSD/USOIL score 11: reasonable with hard sweep + in-FVG gates. Watch false positives around off-peak KZ; off-peak already adds +1.
- Sweep <=15 candles: acceptable as a secondary score input, but hard sweep confirmation at <=15 is loose. Consider tightening to <=10 after review of missed May 13 setups.
- MAE 85%: useful prop-firm damage control, but it may cut trades just before normal SL liquidity sweep. Keep for GFT; consider 90% for FTMO after sample.
- Time exit 2h: sensible for 15m Silver Bullet. For GFT, keep to avoid dead holds; for FTMO, review if many later T2/T3 wins are being cut.
- A+ similarity scorer: currently boosts lots, not a hard block. It is helping only if boosted trades keep positive expectancy; monitor separately.

## Open External Actions

- Rotate the exposed GFT password in the broker portal.
- Move `.env` secrets into Windows Credential Manager or an encrypted vault.
- Confirm broker-side IP whitelist support for MT5 accounts.
- GFT 10K second-worker prep still needs account-specific rules, credentials, state file name, heartbeat, and whether it should run alongside the 5K account.
- VPS/Task Scheduler setup requires Windows host access decisions.
- Log rotation and daily `data/*.json` backups are still recommended infra tasks.
