# CB6 Experience Engine Answer

Hello Rahul. I am CB6 Experience Engine: trade memory, backtest memory, shadow learning, NSE knowledge, and Forex knowledge speaking as one research brain.

I will not touch execution. I will not modify SL, TP, lot sizing, risk, or live code. This is memory and research only.

## SECTION 1 - Executive Summary
To reach 80-85% WR with PF >= 2.25, CB6 must become more selective, not more aggressive.
The strongest current evidence says: trade fewer, cleaner NSE setups, especially the 13:30 window and short/displacement contexts.
- `13_30_window` | market=nse | N=101 | WR=82.18% | PF=13.6611 | AvgR=2.256 | MaxDD=2.00R | rank=A+ | confidence=98/100 | filters={'session': '13:30'}
Confidence: 88/100 for the direction of the recommendation; 70/100 for live transfer because live sample is still small.

## SECTION 2 - NSE Findings
- `combo_score_gate=8_direction=short_fvg_displacement=True` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'fvg_displacement': True}
- `combo_score_gate=8_direction=short_ob_present=False` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False}
- `combo_score_gate=8_direction=short_ob_present=False_fvg_displacement=True` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=12_direction=short` | market=nse | N=159 | WR=74.21% | PF=10.8088 | AvgR=2.529 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'short'}
- `combo_score_gate=12_direction=short_ob_present=True` | market=nse | N=159 | WR=74.21% | PF=10.8088 | AvgR=2.529 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'short', 'ob_present': True}

## SECTION 3 - Forex Findings
- `combo_score_gate=10_direction=short_mss_type=BOS_fvg_displacement=True` | market=forex | N=78 | WR=74.36% | PF=6.845 | AvgR=1.499 | MaxDD=4.33R | rank=B | confidence=76/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'fvg_displacement': True}
- `combo_score_gate=10_direction=short_mss_type=BOS_ob_present=False_fvg_displacement=True` | market=forex | N=76 | WR=73.68% | PF=6.557 | AvgR=1.462 | MaxDD=4.33R | rank=B | confidence=76/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=10_direction=short_mss_type=BOS` | market=forex | N=81 | WR=72.84% | PF=6.5212 | AvgR=1.431 | MaxDD=4.33R | rank=B | confidence=76/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS'}
- `combo_score_gate=8_direction=short_mss_type=BOS_fvg_displacement=True` | market=forex | N=82 | WR=73.17% | PF=6.4955 | AvgR=1.474 | MaxDD=4.33R | rank=B | confidence=76/100 | filters={'score_gate': 8, 'direction': 'short', 'mss_type': 'BOS', 'fvg_displacement': True}
- `combo_score_gate=10_direction=short_mss_type=BOS_ob_present=False` | market=forex | N=79 | WR=72.15% | PF=6.2469 | AvgR=1.395 | MaxDD=4.33R | rank=B | confidence=76/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'ob_present': False}

## SECTION 4 - Long vs Short
SHORT edge is stronger in current memory.
- `combo_score_gate=8_direction=short_fvg_displacement=True` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'fvg_displacement': True}
- `combo_score_gate=8_direction=short_ob_present=False` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False}
- `combo_score_gate=8_direction=short_ob_present=False_fvg_displacement=True` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=12_direction=short_ob_present=True` | market=combined | N=161 | WR=74.53% | PF=10.9493 | AvgR=2.534 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'short', 'ob_present': True}
- `combo_score_gate=12_direction=short` | market=nse | N=159 | WR=74.21% | PF=10.8088 | AvgR=2.529 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'short'}
LONG edge exists but is weaker and includes several low-WR CHoCH pockets.
- `combo_score_gate=8_direction=long_fvg_displacement=True` | market=nse | N=128 | WR=72.66% | PF=7.9434 | AvgR=1.899 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'long', 'fvg_displacement': True}
- `combo_score_gate=8_direction=long_ob_present=False` | market=nse | N=128 | WR=72.66% | PF=7.9434 | AvgR=1.899 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'long', 'ob_present': False}
- `combo_score_gate=8_direction=long_ob_present=False_fvg_displacement=True` | market=nse | N=128 | WR=72.66% | PF=7.9434 | AvgR=1.899 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'long', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=12_direction=long_regime=TRENDING` | market=nse | N=107 | WR=72.90% | PF=7.7259 | AvgR=1.823 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'long', 'regime': 'TRENDING'}
- `combo_score_gate=12_direction=long_regime=TRENDING_ob_present=True` | market=nse | N=107 | WR=72.90% | PF=7.7259 | AvgR=1.823 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'long', 'regime': 'TRENDING', 'ob_present': True}
Recommendation confidence: 86/100 for favoring shorts until live data disproves it.

## SECTION 5 - Best Setup Combination
Best robust target combination:
- `13_30_window` | market=nse | N=101 | WR=82.18% | PF=13.6611 | AvgR=2.256 | MaxDD=2.00R | rank=A+ | confidence=98/100 | filters={'session': '13:30'}
Best PF robust combination:
- `13_30_window` | market=nse | N=101 | WR=82.18% | PF=13.6611 | AvgR=2.256 | MaxDD=2.00R | rank=A+ | confidence=98/100 | filters={'session': '13:30'}
Best WR robust combination:
- `13_30_window` | market=nse | N=101 | WR=82.18% | PF=13.6611 | AvgR=2.256 | MaxDD=2.00R | rank=A+ | confidence=98/100 | filters={'session': '13:30'}

## SECTION 6 - Worst Setup Combination
These destroy WR relative to the target and should be reviewed as skip candidates:
- `combo_score_gate=8_direction=long_mss_type=BOS_ob_present=False` | market=forex | N=74 | WR=51.35% | PF=2.4616 | AvgR=0.708 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 8, 'direction': 'long', 'mss_type': 'BOS', 'ob_present': False}
- `combo_score_gate=8_direction=long_mss_type=BOS_ob_present=False` | market=combined | N=74 | WR=51.35% | PF=2.4616 | AvgR=0.708 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 8, 'direction': 'long', 'mss_type': 'BOS', 'ob_present': False}
- `combo_score_gate=8_direction=long_mss_type=BOS_ob_present=False_fvg_displacement=True` | market=forex | N=66 | WR=51.52% | PF=2.4263 | AvgR=0.692 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 8, 'direction': 'long', 'mss_type': 'BOS', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=10_direction=long_mss_type=BOS_ob_present=False_fvg_displacement=True` | market=forex | N=66 | WR=51.52% | PF=2.4263 | AvgR=0.692 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 10, 'direction': 'long', 'mss_type': 'BOS', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=8_direction=long_mss_type=BOS_ob_present=False_fvg_displacement=True` | market=combined | N=66 | WR=51.52% | PF=2.4263 | AvgR=0.692 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 8, 'direction': 'long', 'mss_type': 'BOS', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=10_direction=long_mss_type=BOS_ob_present=False_fvg_displacement=True` | market=combined | N=66 | WR=51.52% | PF=2.4263 | AvgR=0.692 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 10, 'direction': 'long', 'mss_type': 'BOS', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=8_direction=long_mss_type=BOS` | market=forex | N=76 | WR=52.63% | PF=2.629 | AvgR=0.768 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 8, 'direction': 'long', 'mss_type': 'BOS'}
- `combo_score_gate=10_direction=long_mss_type=BOS_ob_present=False` | market=forex | N=72 | WR=52.78% | PF=2.607 | AvgR=0.756 | MaxDD=4.33R | rank=REJECT | confidence=65/100 | filters={'score_gate': 10, 'direction': 'long', 'mss_type': 'BOS', 'ob_present': False}

## SECTION 7 - Entry Improvements
Best entry model: Silver Bullet FVG retest inside the 13:30 NSE window, with displacement preferred.
MSS/structure: BOS and short continuation contexts are cleaner than long CHoCH pockets in this memory.
- `combo_score_gate=10_direction=short_mss_type=BOS_ob_present=True` | market=combined | N=103 | WR=76.70% | PF=9.9938 | AvgR=2.096 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'ob_present': True}
- `combo_score_gate=10_mss_type=BOS_ob_present=True` | market=combined | N=178 | WR=76.97% | PF=9.9424 | AvgR=2.060 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 10, 'mss_type': 'BOS', 'ob_present': True}
- `combo_score_gate=10_direction=short_mss_type=BOS` | market=nse | N=101 | WR=76.24% | PF=9.7538 | AvgR=2.080 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS'}
- `combo_score_gate=10_direction=short_mss_type=BOS_ob_present=True` | market=nse | N=101 | WR=76.24% | PF=9.7538 | AvgR=2.080 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'ob_present': True}
- `combo_score_gate=10_mss_type=BOS` | market=nse | N=174 | WR=76.44% | PF=9.6556 | AvgR=2.039 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 10, 'mss_type': 'BOS'}
FVG quality: displacement-only is preferred when sample size is sufficient.
- `combo_score_gate=8_direction=short_fvg_displacement=True` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'fvg_displacement': True}
- `combo_score_gate=8_direction=short_ob_present=False_fvg_displacement=True` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=8_fvg_displacement=True` | market=nse | N=261 | WR=75.86% | PF=9.4149 | AvgR=2.031 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'fvg_displacement': True}
- `combo_score_gate=8_ob_present=False_fvg_displacement=True` | market=nse | N=261 | WR=75.86% | PF=9.4149 | AvgR=2.031 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'ob_present': False, 'fvg_displacement': True}
- `fvg_displacement_only` | market=nse | N=330 | WR=74.85% | PF=8.8686 | AvgR=1.979 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'fvg_displacement': True}
OB overlap: current OB results are mixed. Tiny Forex OB samples are not acceptable for live decisions.
- `combo_score_gate=8_direction=short_ob_present=False` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False}
- `combo_score_gate=8_direction=short_ob_present=False_fvg_displacement=True` | market=nse | N=133 | WR=78.95% | PF=11.2543 | AvgR=2.159 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False, 'fvg_displacement': True}
- `combo_score_gate=12_direction=short_ob_present=True` | market=combined | N=161 | WR=74.53% | PF=10.9493 | AvgR=2.534 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'short', 'ob_present': True}
- `combo_score_gate=12_direction=short_ob_present=True` | market=nse | N=159 | WR=74.21% | PF=10.8088 | AvgR=2.529 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'score_gate': 12, 'direction': 'short', 'ob_present': True}
- `combo_score_gate=10_direction=short_mss_type=BOS_ob_present=True` | market=combined | N=103 | WR=76.70% | PF=9.9938 | AvgR=2.096 | MaxDD=3.00R | rank=A | confidence=93/100 | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'ob_present': True}

## SECTION 8 - Exit Improvements
Exit tests are proxy-only because full candle path, MFE, MAE, partial exit events, and BE events are missing.
Best current evidence: do not assume tighter TP improves PF; the logged CB6 exit model already performs strongly in best filters.
- `exit_model_ob_sl` | market=nse | N=718 | WR=71.87% | PF=7.9811 | AvgR=2.062 | MaxDD=4.20R | rank=A | confidence=93/100 | filters={'exit_model': 'ob_sl'}
- `exit_model_base` | market=nse | N=718 | WR=71.87% | PF=7.9811 | AvgR=1.964 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'exit_model': 'base'}
- `exit_model_be_at_1r` | market=nse | N=718 | WR=71.87% | PF=7.9811 | AvgR=1.964 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'exit_model': 'be_at_1r'}
- `exit_model_structure_sl` | market=nse | N=718 | WR=71.87% | PF=7.9811 | AvgR=1.964 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'exit_model': 'structure_sl'}
- `exit_model_fixed_sl` | market=nse | N=718 | WR=71.87% | PF=7.9811 | AvgR=1.866 | MaxDD=3.80R | rank=A | confidence=93/100 | filters={'exit_model': 'fixed_sl'}
- `exit_model_fvg_sl` | market=nse | N=718 | WR=71.87% | PF=7.9811 | AvgR=1.768 | MaxDD=3.60R | rank=A | confidence=93/100 | filters={'exit_model': 'fvg_sl'}
- `exit_model_partial_1r` | market=nse | N=718 | WR=71.87% | PF=6.1645 | AvgR=1.453 | MaxDD=4.65R | rank=A | confidence=93/100 | filters={'exit_model': 'partial_1r'}
- `exit_model_tp_3r` | market=nse | N=718 | WR=71.87% | PF=5.7068 | AvgR=1.324 | MaxDD=4.00R | rank=A | confidence=93/100 | filters={'exit_model': 'tp_3r'}

## SECTION 9 - Risk Improvements
Risk should stay controlled by selectivity and daily stops, not by increasing lot size.
Skip low-quality contexts rather than widening SL.
Keep MAE/time exits under review only after MFE/MAE data is stored.
Recommendation confidence: 90/100.

## SECTION 10 - Future Learning Requirements
Missing data preventing stronger answers:
- MFE
- MAE
- partial exit path
- trailing path
- candle-by-candle post-entry behavior
- re-entry attempts
- break-even trigger/hit events
- H1/H4 bias logged on every historical and live trade
- premium/discount at entry
- EQH/EQL sweep type and sweep depth
- real news blackout labels

## SECTION 11 - Recommended Memory Fields
Add/keep these future live fields:
- direction, entry_reason, exit_reason, sl_reason, tp_reason, trailing_sl_used
- trend_bias, h4_bias, h1_bias, CHoCH, BOS, MSS
- fvg_size, fvg_quality, ob_overlap, liquidity_sweep_type, score
- ML confidence, result, R multiple, MFE, MAE, should_repeat

## SECTION 12 - Probability of Reaching Targets
- 80% WR: HIGH for filtered NSE (2 robust candidates)
- 85% WR: LOW (0 robust candidates)
- PF 2.25: HIGH (445 robust candidates)
- PF 3+: HIGH (430 robust candidates)
- PF 5+: MEDIUM/HIGH (335 robust candidates)

## SECTION 13 - Safe Live Recommendations
Do not change live code automatically.
Manual review candidate #1: NSE 13:30 window priority.
Manual review candidate #2: short/displacement setups as a higher-quality bucket.
Manual review candidate #3: skip weak long CHoCH pockets unless other context is exceptional.
Keep ML shadow-only.
Run this query after every 25-50 new closed trades.

Teach you how to become better: stop asking CB6 to trade more. Ask CB6 to trade cleaner. Your edge is not in pressing every setup; it is in refusing the 30-40% that look valid but historically pay you poorly.
