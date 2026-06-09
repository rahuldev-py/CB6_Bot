# Prop Firm Hard Rules — Never Violate

> **PRIORITY: GFT $5K 2-Step → GFT $1K Instant → GFT $10K Instant → NSE → FTMO (deprioritized)**
> All 3 GFT accounts are REAL funded with withdrawal rights. FTMO runs as-is, no active effort.

## GFT $5K 2-Step GOAT ⭐ PRIMARY
| Rule | Value | Note |
|------|-------|------|
| Phase 1 target | $400 (8%) | Need + 3 trading days min |
| Phase 2 target | $300 (6%) | After Phase 1 passes |
| Daily loss limit | $200 (4%) | Hard stop — day ends |
| Max total loss | $500 (10%) | Breach = blown |
| Min trading days/phase | 3 | Calendar days with trades |
| Active symbols | XAUUSD + XAGUSD + USOIL | H4 bias mandatory before Gold entry |
| XAUUSD max lot | 0.05 | SL $5→0.05L · SL $10→0.02L · SL $15→0.01L |
| Risk per trade (normal) | 0.50% = ~$25/trade | Phase 1 growth mode — intentional |
| Risk per trade (reduced) | 0.25% = ~$12/trade | After -$140/day guard fires |
| Risk per trade (A+ max) | 0.75% = ~$37/trade | A+ setups with sim boost only |
| Max daily loss (6 trades×normal) | ~$146 | $24 under $170 hard stop — safe |

## GFT $1K Instant Live ⭐ SECONDARY
| Rule | Value | Note |
|------|-------|------|
| Account size | $1,000 | Real funded, withdrawals open |
| Daily DD limit | $30 (3%) | Hard stop — day ends |
| Max DD limit | $60 (6%) | Breach = blown |
| Risk per trade | 0.25% = $2.50 | Max lot 0.01 |
| Active symbols | XAUUSD + XAGUSD + USOIL | XAUUSD viable only when SL ≤ $2.50 distance |
| XAUUSD max lot | 0.01 | Engine auto-skips if calc lots < 0.01 min |

## GFT $10K Instant ⭐ TERTIARY
| Rule | Value | Note |
|------|-------|------|
| Account size | $10,000 | Real funded |
| Daily DD limit | $500 (5%) | Hard stop — day ends |
| Max DD limit | $1,000 (10%) | Breach = blown |
| Risk per trade | 0.50% = $50 | |
| Active symbols | XAUUSD + XAGUSD + USOIL | H4 bias mandatory before Gold entry |
| XAUUSD max lot | 0.10 | SL $5→0.10L · SL $10→0.05L · SL $15→0.03L |
| MT5 login | 514294187 | Server: GoatFunded-Server3 |

## Internal Guards — GFT $5K (fire BEFORE official limits)
| Guard | Value |
|-------|-------|
| Warning | -$100/day |
| Reduce lots 50% | -$140/day |
| Hard stop today | -$170/day |
| Total warning | -$250 |
| Total reduce | -$350 |
| Total halt | -$430 |

## Internal Guards — GFT $1K Instant
| Guard | Value |
|-------|-------|
| Warning | -$25/day |
| Hard stop today | -$30/day (= official limit) |

## Internal Guards — GFT $10K Instant
| Guard | Value |
|-------|-------|
| Daily DD danger | -$400/day |
| Daily DD hard stop | -$500/day |
| Total DD danger | -$900 |
| Total DD halt | -$1,000 |

## XAUUSD Gold — Re-enabled 2026-06-09
| Account | Max lot | Typical lots at SL $10 | Notes |
|---------|---------|----------------------|-------|
| $10K | 0.10 | 0.05 | A+ → 0.075 (capped 0.10) |
| $5K | 0.05 | 0.02 | A+ → 0.037 (capped 0.05) |
| $1K | 0.01 | skip (SL too wide) | Only enters when SL ≤ $2.50 |

*H4 bias filter mandatory before all Gold entries. Was disabled after May 22 SELL disaster (trading SELL vs H4 uptrend). H4 filter now enforced in code — root cause addressed.*

## FTMO Free Trial ($10,000) — DEPRIORITIZED
| Rule | Value | Note |
|------|-------|------|
| Best day cap | $250 (2.5%) | Enforced in ftmo_state.py — do not remove |
| Daily loss limit | $300 (3%) | Enforced in ftmo_state.py — do not remove |

*FTMO runs as-is. No new features, debugging, or tuning effort.*

## Enforced in Code
- `gft_5k_2step.py` — internal_daily_hard_stop, internal_total_hard_stop
- `gft_config.py` — GFT $5K values + max_lot_per_symbol
- `forex_engine/gft_1k_instant/risk.py` — GFT $1K Instant guards
- `forex_engine/gft_1k_instant/config.py` — GFT $1K values + max_lot_per_symbol
- `forex_engine/gft_10k/config.py` — GFT $10K values + max_lot_per_symbol
- `forex_engine/trade/lot_calculator.py:cap_lots_for_account()` — per-symbol lot cap enforcement

**Never remove guards from code. Never increase them beyond official limits.**
