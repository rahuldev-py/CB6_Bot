# Prop Firm Hard Rules — Never Violate

These rules are enforced by the prop firms and breach = account blown = start over.

## FTMO Free Trial ($10,000)
| Rule | Value | Note |
|------|-------|------|
| Profit target | $500 (5%) | Must hit to pass |
| Daily loss limit | $300 (3%) | Hard stop — day ends |
| Best day cap | $250 (2.5%) | Cannot profit more in 1 day |
| Max drawdown | $1,000 (10%) | Total loss from peak |
| Min trading days | None (free trial) | — |
| Deadline | ~June 6, 2026 | ~8 trading days remaining |

## GFT $5K 2-Step GOAT
| Rule | Value | Note |
|------|-------|------|
| Phase 1 target | $400 (8%) | Need + 3 trading days |
| Phase 2 target | $300 (6%) | After Phase 1 passes |
| Daily loss limit | $200 (4%) | Hard stop — day ends |
| Max total loss | $500 (10%) | Breach = blown |
| Min trading days/phase | 3 | Calendar days with trades |
| Disabled symbols | XAUUSD | Permanent — no exceptions |

## Internal Guards (fire BEFORE official limits)
| Guard | GFT | FTMO |
|-------|-----|------|
| Warning | -$100/day | -$200/day |
| Reduce lots 50% | -$140/day | -$250/day (best day cap) |
| Hard stop today | -$170/day | No entry after best day cap |
| Total warning | -$250 | — |
| Total reduce | -$350 | — |
| Total halt | -$430 | — |

## Enforced in Code
- `ftmo_state.py` — best_day_pnl check, daily loss check
- `gft_5k_2step.py` — internal_daily_hard_stop, internal_total_hard_stop
- `gft_config.py` — all values defined here, referenced by engine

**Never remove these guards from code. Never increase them to be more aggressive than official limits.**
