# CB6 Learning Update Summary - Role Model Setups

## Safety

This update is memory improvement only.

- Live execution unchanged.
- Risk unchanged.
- SL/TP unchanged.
- Lot sizing unchanged.
- ML trading not enabled.
- Recommendations are shadow/logging only.

## Role Models Saved

### 1. NSE_1330_A_PLUS

- Market: NSE
- Session: 13:30
- N: 101
- WR: 82.18%
- PF: 13.66
- Avg R: 2.256
- Max DD: 2R
- Rank: A+
- Confidence: 98/100

### 2. NSE_SHORT_DISPLACED_FVG_A

- Market: NSE
- Direction: SHORT
- FVG: displaced
- N: 133
- WR: 78.95%
- PF: 11.25
- Avg R: 2.159
- Rank: A
- Confidence: 93/100

### 3. FOREX_SHORT_BOS_DISPLACED_FVG_SCORE10_B

- Market: Forex
- Direction: SHORT
- Structure: BOS
- FVG: displaced
- Score: >= 10
- N: 78
- WR: 74.36%
- PF: 6.84
- Avg R: 1.499
- Rank: B
- Confidence: 76/100

## Filters Improved In Memory

- Prefer NSE 13:30 setups.
- Prefer displaced FVG setups.
- Prefer SHORT setups when structure agrees.
- Skip no-displacement FVGs.
- Skip CHOPPY regime.
- Require OB overlap or a higher score.
- Treat Forex as lower confidence until more samples arrive.
- Reject tiny-sample perfect results.
- Store role-model mismatches.
- Compare actual outcome against role-model expectation after every trade.

## Future Live Comparison

Every future live setup should be compared against role models and logged with:

- similarity_score 0-100
- matched_role_model
- missing_conditions
- quality_grade
- warning_tags
- should_observe
- should_skip recommendation
- learning_note

This is not a live gate yet.

## What CB6 Should Skip More Aggressively

- Weak long CHoCH pockets.
- Forex long BOS without OB.
- No-displacement FVG.
- CHOPPY regime.
- Low score setups, especially below 12 without role-model similarity.
- Tiny sample filters.
- Setups outside best windows unless structure is excellent.

## Data Still Missing

- MFE
- MAE
- partial exit path
- trailing path
- candle-by-candle post-entry behavior
- re-entry attempts
- BE trigger and BE stop events
- H4/H1 bias on every trade
- premium/discount at entry
- EQH/EQL sweep type and depth
- real news blackout labels

## What Future Live Trades Must Log

- role_model_similarity
- matched_role_model
- missing_role_model_conditions
- warning_tags
- should_repeat
- MFE
- MAE
- BE event
- partial exit event
- trailing SL path
- post-entry candle path summary

## Final Learning Note

CB6 should learn from its best historical self, not its average self. The role-model layer teaches future live review to ask: "How close was this trade to the cleanest 13:30 NSE template, the strongest NSE short/displaced-FVG template, or the acceptable Forex short/BOS template?"

