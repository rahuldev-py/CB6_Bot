# CB6 Futures Core — Symbol Ranking
**Generated:** 2026-05-31  
**Data:** 1h-bar backtests, 2024 (partial Jun–Dec), 2025 (full), 2026 (partial Jan–May)  
**Important:** All metrics are 1h-bar approximations with known biases (see research_audit.md).  
Treat these rankings as directional guidance, not execution-ready performance forecasts.

---

## Summary Table — All Years

| Symbol | Year | Trades | WR% | PF | Net PnL | Max DD | Expectancy | MFF Sim | Gate |
|---|---|---|---|---|---|---|---|---|---|
| **MES** | 2024 | 211 | 74.4% | 12.99 | $21,341 | $168 | $101 | PASS | PASS |
| **MES** | 2025 | 388 | 85.3% | 8.81 | $35,510 | $864 | $92 | PASS* | FAIL (DD) |
| **MES** | 2026 | 128 | 72.7% | 3.06 | $8,618 | $1,348 | $67 | PASS* | FAIL (DD) |
| **MNQ** | 2024 | 218 | 49.5% | 23.09 | $47,596 | $694 | $218 | PASS | PASS |
| **MNQ** | 2025 | 298 | 52.3% | 17.37 | $64,055 | $1,251 | $215 | PASS* | FAIL (DD) |
| **MNQ** | 2026 | 112 | 50.0% | 5.93 | $20,549 | $1,673 | $183 | PASS* | FAIL (DD) |
| **MGC** | 2024 | 187 | 84.0% | 10.78 | $22,965 | $694 | $123 | PASS | PASS |
| **MGC** | 2025 | 325 | 87.1% | 12.59 | $79,202 | $1,227 | $244 | PASS* | FAIL (DD) |
| **MGC** | 2026 | 131 | 80.2% | 9.63 | $58,365 | $3,471 | $446 | PASS* | FAIL (DD) |
| **MCL** | 2024 | 206 | 38.8% | 1.32 | $197 | $207 | $1 | FAIL | FAIL |
| **MCL** | 2025 | 377 | 38.5% | 1.28 | $290 | $260 | $1 | FAIL | FAIL |
| **MCL** | 2026 | 98 | 29.6% | 1.66 | $261 | $123 | $3 | FAIL | FAIL |

*PASS in MFF sim column is a simulation defect — drawdown check erroneously shows 0. See mff_rule_validation.md.

---

## Symbol Rankings

### Rank 1: MES (Micro E-mini S&P 500)

**Verdict: Best candidate for MFF eval — most consistent, lowest drawdown**

| Metric | 2024 | 2025 | 2026 | Assessment |
|---|---|---|---|---|
| Profit Factor | 12.99 | 8.81 | 3.06 | Declining trend. 2026 still positive. |
| Win Rate | 74.4% | 85.3% | 72.7% | Consistent. Inflated by 1h bias. |
| Max DD | $168 | $864 | $1,348 | Rising with higher volatility. |
| Expectancy | $101 | $92 | $67 | Positive across all years. |
| Best kill zone | NY_AFTERNOON (83%) | LONDON_OPEN (86%) | NY_AFTERNOON (66%) | Both zones work. |
| Worst kill zone | NY_OPEN (100% but 3 trades) | NY_LUNCH (75%) | LONDON_OPEN (73%) | No bad session. |

**Why MES ranks first:**  
2024 max drawdown of $168 is the lowest of all symbols and years tested. Even accounting for the simulation bias inflating the 2024 DD lower than reality, MES shows the most controlled drawdown profile. The PF trajectory is declining (12.99 → 8.81 → 3.06) which is concerning, but even 3.06 is well above the 1.5 minimum gate. MES is the least volatile instrument, making it most compatible with a tight $1,000 MFF drawdown limit.

**Risk factor:** 2026 partial-year DD of $1,348 exceeds the $1,000 MFF limit even on the equity-curve basis. This likely reflects the 2026 tariff/geopolitical volatility period (Feb–May 2026). The strategy needs news filter integration to manage these events.

---

### Rank 2: MGC (Micro Gold)

**Verdict: Highest expectancy, but drawdown risk in volatile periods**

| Metric | 2024 | 2025 | 2026 | Assessment |
|---|---|---|---|---|
| Profit Factor | 10.78 | 12.59 | 9.63 | Strong and consistent. |
| Win Rate | 84.0% | 87.1% | 80.2% | Highest of all symbols. |
| Max DD | $694 | $1,227 | $3,471 | Severe in 2026. |
| Expectancy | $123 | $244 | $446 | Rising — gold trending strongly in 2025-26. |
| Best kill zone | NY_OPEN (92%), NY_AFTERNOON (86-97%) | All sessions high | NY_AFTERNOON (97%) | Excellent across sessions. |

**Why MGC ranks second:**  
The win rate and expectancy are the best of the four symbols. ICT Silver Bullet with HTF bias works well on gold — the trend (strong bull run 2024–2026) likely helps the directional filter. However, the 2026 equity-curve DD of $3,471 is catastrophic — 3.5× the MFF drawdown limit. Gold experienced extreme volatility in early 2026, with large rollover gaps confirmed by the validator. This symbol should not be traded alone in a live MFF eval without a news blackout specifically for gold-moving events (Fed, geopolitical news).

**Note:** MGC daily bars have Panama-unadjusted rollover gaps up to 2.44% (1h: 0.98%). The 2026 drawdown number may be partially inflated by roll gap artifacts.

---

### Rank 3: MNQ (Micro E-mini Nasdaq-100)

**Verdict: Anomalous PF driven by extreme avg_win/avg_loss ratio — requires scrutiny**

| Metric | 2024 | 2025 | 2026 | Assessment |
|---|---|---|---|---|
| Profit Factor | 23.09 | 17.37 | 5.93 | Highest PF but most suspicious. |
| Win Rate | 49.5% | 52.3% | 50.0% | ~50% — realistic for a trend strategy. |
| Max DD | $694 | $1,251 | $1,673 | Above $1,000 in 2025 and 2026. |
| Expectancy | $218 | $215 | $183 | High and consistent. |
| Avg Win | $461 | $436 | $341 | Very large vs avg loss. |
| Avg Loss | $20 | $28 | $38 | Very small. |

**Anomaly analysis:**  
MNQ's PF of 23 at 49.5% WR is mathematically consistent but economically unusual. The avg_loss of $19.59 with avg_win of $460.66 implies an avg_win / avg_loss ratio of ~23.5. This ratio is driven by the 3-tick fallback stop-loss (Finding F-2 in audit). MNQ's $0.50 tick value means a 3-tick stop = $1.50 risk. After commission and slippage ($5.50), a "small loss" is $7, but the avg_loss is $19.59, suggesting stops are sometimes wider. The outsized avg_win comes from NQ trending strongly and the 3R target capturing large moves on 1h bars.

**Why MNQ ranks third (not second):**  
The 2025 DD of $1,251 and 2026 DD of $1,673 both exceed the MFF $1,000 limit. Unlike MGC's extreme 2026 volatility being potentially explainable by gold market conditions, MNQ's increasing drawdown trend is consistent across years. The 3-tick stop anomaly makes the MNQ numbers less trustworthy than MGC or MES.

---

### Rank 4: MCL (Micro WTI Crude Oil)

**Verdict: No edge with this strategy. Do not trade.**

| Metric | 2024 | 2025 | 2026 | Assessment |
|---|---|---|---|---|
| Profit Factor | 1.32 | 1.28 | 1.66 | Below 1.5 minimum. |
| Win Rate | 38.8% | 38.5% | 29.6% | Below 40%. Declining. |
| Max DD | $207 | $260 | $123 | Low DD but not because strategy is good. |
| Expectancy | $1 | $1 | $3 | Near zero. |
| Net PnL (3 years combined) | $648 | | | Barely covers 1 month of commissions. |

The ICT Silver Bullet does not have a detectable edge on crude oil. The low drawdown is not a sign of quality — it's a sign of near-random outcomes with low variance. Three years of flat performance across all sessions confirms this is not a tradeable setup. The net PnL over all three years combined ($197 + $290 + $261 = $748) would be consumed by a single month of live data costs.

**Why crude oil likely fails:**  
1. WTI crude has a different liquidity profile than equity index or gold futures. Liquidity pools and FVG sweeps behave differently in energy markets.
2. OPEC+ intervention creates non-technical gap events that disrupt ICT structure.
3. The kill zones (02:00–16:00 UTC) don't align well with crude oil's primary liquidity windows.

**Recommendation:** Remove MCL from Phase 1 trading list entirely. Replace with MYM (Micro Dow) or M2K (Micro Russell) for diversification if desired.

---

## Pass Rate Summary

| Symbol | Years with PF ≥ 1.5 | Years with DD ≤ $700 | Years MFF would pass (corrected) | Recommendation |
|---|---|---|---|---|
| MES | 3/3 | 1/3 (2024) | 1/3 (2024) | Primary symbol |
| MGC | 3/3 | 1/3 (2024) | 1/3 (2024) | Secondary — news filter required |
| MNQ | 3/3 | 1/3 (2024) | 1/3 (2024) | Tertiary — stop anomaly must be fixed |
| MCL | 1/3 | 3/3 | 0/3 | Removed from list |

---

## Kill-Zone Ranking by Symbol

Best to worst kill zone per symbol (based on win rate, minimum 20 trades):

| Symbol | Zone 1 (Best) | Zone 2 | Zone 3 (Worst) |
|---|---|---|---|
| MES | NY_AFTERNOON (75–83%) | LONDON_OPEN (69–86%) | NY_OPEN (variable, low sample) |
| MNQ | NY_AFTERNOON (68–86%) | LONDON_OPEN (35–49%) | NY_OPEN (0–17%) |
| MGC | All sessions 74–100% | — | NY_LUNCH variable |
| MCL | NY_AFTERNOON (29–65%) | All others < 37% | NY_LUNCH (0–9%) |

**MNQ finding:** LONDON_OPEN win rate (35–49%) is significantly lower than NY_AFTERNOON (68–86%). Running MNQ exclusively during NY_AFTERNOON and filtering LONDON_OPEN would substantially improve MNQ's profile. This single filter could bring MNQ from Rank 3 to near parity with MES.
