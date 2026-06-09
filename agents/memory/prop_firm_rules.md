# CB6 Quantum — All Account Rules (SENTINEL Bible)
# Last updated: 2026-06-05

## PRIORITY ORDER
1. GFT $5K 2-Step (primary — pass phases → master account → CB6 infra)
2. GFT $1K Instant (secondary — real funded, withdraw freely)
3. NSE Fyers (real ₹26K — index options/futures only)
4. FTMO (deprioritized — runs as-is, no new engineering)

---

## 1. GFT $5K 2-Step GOAT ⭐ PRIMARY
| Rule | Value | Status |
|------|-------|--------|
| Current capital | $5,013.62 | Updated 2026-06-05 |
| Phase 1 target | +$400 (8%) | Need +$386.38 more |
| Phase 2 target | +$300 (6%) | After Phase 1 passes |
| Trading days done | 2 of 3 minimum | Need 1 more |
| Daily loss limit | $200 (4%) | Hard stop |
| Max total loss | $500 (10%) | = BLOWN |
| XAUUSD | PERMANENTLY DISABLED | No exceptions |
| Kill zones | London 07-12 UTC, NY 16-20 UTC | |

Internal guards (fire BEFORE official limits):
| Guard | Trigger |
|-------|---------|
| Warning | -$100/day |
| Reduce lots 50% | -$140/day |
| Hard stop today | -$170/day |
| Total warning | -$250 total |
| Total reduce | -$350 total |
| Total HALT | -$430 total |

---

## 2. GFT $1K Instant ⭐ SECONDARY
| Rule | Value | Status |
|------|-------|--------|
| Current capital | $1,004.07 | Updated 2026-06-05 |
| Daily DD limit | $30 (3%) | Hard stop |
| Max DD limit | $60 (6%) | = BLOWN |
| Risk/trade | $2.50 max | 0.25% of $1K |
| Max lot size | 0.01 | Hard limit |
| Magic number | 100061 | MT5 identifier |
| XAUUSD | PERMANENTLY DISABLED | No exceptions |

Internal guards:
| Guard | Trigger |
|-------|---------|
| Warning | -$25/day |
| Hard stop today | -$30/day |

---

## 3. NSE Fyers — Real Demat (₹26K)
| Rule | Value |
|------|-------|
| Instruments | Index futures + options ONLY |
| Allowed | NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY |
| BANNED | All equity/stocks, crypto |
| Strategy windows | 10-11 IST, 13-14 IST, 15-15:30 IST |
| H4 bias | Mandatory before entry |
| SL rule | Sweep wick + 10-15pt buffer |

---

## 4. FTMO $10K — DEPRIORITIZED
| Rule | Value |
|------|-------|
| Current capital | ~$9,804 |
| Best-day cap | $250 — hard coded in ftmo_state.py, do NOT remove |
| Daily loss limit | $300 |
| Status | Runs as-is. No new engineering effort. |

---

## ICT Strategy Rules (All Accounts)

### Validated Template: DOL_SWEEP_OB_BOS_FVG (2026-06-05)
5-step sequence mandatory for any entry:
1. DOL identified (swing high/low with stop cluster)
2. DOL swept (price wicks through, closes back = fake out)
3. OB forms (consolidation after sweep, ≥3 candles tight range)
4. BOS or CHoCH fires (large displacement candle)
5. FVG entry (limit at FVG bottom/top)

Backtest validation — 258 LONG trades:
- NSE: 61.8% WR | Avg R 1.78
- Forex: 60.1% WR | Avg R 1.17

### Counter-Trend Rule (UPDATED 2026-06-05)
**Previous rule:** No counter-trend entries ever.
**Updated rule:** Counter-trend VALID at 50% size when ALL 5 confirmed:
1. DOL fully swept
2. OB ≥15min accumulation
3. BOS/CHoCH displacement ≥3× avg body
4. FVG present
5. Entry at FVG (not chasing)

Validated: NIFTY LONG 2026-06-05, H4 BEARISH, +Rs689, R=1.27, 50% size applied.

### OB Duration Rule (NEW 2026-06-05)
- OB < 15min: weak, skip or reduce size
- OB 15-45min: standard quality
- OB ≥ 45min: +1 confluence point (strong institutional loading)
- OB ≥ 90min: +2 confluence points (institutional patience, rare)

### CHoCH vs BOS for LONG Entries
- CHoCH fires in 73-77% of backtest LONG winners
- BOS fires in 23-27% of backtest LONG winners
- **Prefer CHoCH for LONG entries when both available**

---

## SENTINEL Checklist — Run on Every Code Change
- [ ] XAUUSD not re-enabled on GFT (5K or 1K) — PERMANENT ban
- [ ] FTMO best_day_cap $250 still in ftmo_state.py — do not touch
- [ ] GFT daily loss limits unchanged ($200 for 5K, $30 for 1K)
- [ ] NSE: no equity/stock symbols added (index only)
- [ ] paper_mode=True not in any live config
- [ ] H4 bias check not bypassed (counter-trend allowed at 50% size only)
- [ ] State JSON files not directly corrupted by code
- [ ] GFT $1K max lot still 0.01
- [ ] GFT $1K risk/trade still $2.50 max
- [ ] ob_duration_mins tracked in all scanner setup dicts
- [ ] OB duration bonus does not exceed +2pts in scoring
- [ ] Counter-trend 50% size rule enforced when H4 opposes direction

## Critical Fixes Applied 2026-06-05
- RC2: Equilibrium calc fixed (session anchor, not 60-bar rolling)
- RC4: Exit tracking fixed in paper_trader.py + log_exit bug fixed
- OB duration added to NSE + Forex scanners and ML collector
- Template validated on 258 backtest LONG trades — 60.5% WR confirmed
