# CB6 ML Learning Summary

## Executive Summary
Can CB6 realistically reach 80-85% WR? Yes, but only under stricter filters and only where sample confidence is acceptable.
The safest path is not higher risk. It is fewer trades: stricter score gates, direction/session filtering, displacement-only FVGs, and skipping weak/noisy regimes.
Profit factor improves when poor-context trades are removed; it is not solved by widening TP alone.

## NSE Findings
- 13_30_window | N=101 | WR=82.18% | PF=13.66 | AvgR=2.2564 | DD=2.0R | confidence=HIGH | filters={'session': '13:30'}
- combo_score_gate=8_direction=short_fvg_displacement=True | N=133 | WR=78.95% | PF=11.25 | AvgR=2.1588 | DD=3.0R | confidence=HIGH | filters={'score_gate': 8, 'direction': 'short', 'fvg_displacement': True}
- combo_score_gate=8_direction=short_ob_present=False | N=133 | WR=78.95% | PF=11.25 | AvgR=2.1588 | DD=3.0R | confidence=HIGH | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False}
- combo_score_gate=8_direction=short_ob_present=False_fvg_displacement=True | N=133 | WR=78.95% | PF=11.25 | AvgR=2.1588 | DD=3.0R | confidence=HIGH | filters={'score_gate': 8, 'direction': 'short', 'ob_present': False, 'fvg_displacement': True}
- combo_score_gate=12_direction=short | N=159 | WR=74.21% | PF=10.81 | AvgR=2.5293 | DD=4.0R | confidence=HIGH | filters={'score_gate': 12, 'direction': 'short'}

## Forex Findings
- ob_present | N=4 | WR=100.0% | PF=inf | AvgR=2.94 | DD=0.0R | confidence=LOW CONFIDENCE | filters={'ob': True}
- score_gate_14 | N=2 | WR=100.0% | PF=inf | AvgR=2.88 | DD=0.0R | confidence=LOW CONFIDENCE | filters={'score_gate': 14}
- combo_score_gate=10_direction=short_mss_type=BOS_fvg_displacement=True | N=78 | WR=74.36% | PF=6.84 | AvgR=1.4987 | DD=4.33R | confidence=ACCEPTABLE | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'fvg_displacement': True}
- combo_score_gate=12_direction=short_fvg_displacement=True | N=14 | WR=71.43% | PF=6.78 | AvgR=1.65 | DD=1.0R | confidence=LOW CONFIDENCE | filters={'score_gate': 12, 'direction': 'short', 'fvg_displacement': True}
- combo_score_gate=10_direction=short_mss_type=BOS_ob_present=False_fvg_displacement=True | N=76 | WR=73.68% | PF=6.56 | AvgR=1.4624 | DD=4.33R | confidence=ACCEPTABLE | filters={'score_gate': 10, 'direction': 'short', 'mss_type': 'BOS', 'ob_present': False, 'fvg_displacement': True}

## Combined Market Comparison
- Better current research market: NSE
- 13_30_window | N=101 | WR=82.18% | PF=13.66 | AvgR=2.2564 | DD=2.0R | confidence=HIGH | filters={'session': '13:30'}
- combo_score_gate=12_direction=short_ob_present=True | N=161 | WR=74.53% | PF=10.95 | AvgR=2.5337 | DD=4.0R | confidence=HIGH | filters={'score_gate': 12, 'direction': 'short', 'ob_present': True}
- combo_score_gate=12_direction=short | N=175 | WR=73.71% | PF=10.24 | AvgR=2.4297 | DD=4.0R | confidence=HIGH | filters={'score_gate': 12, 'direction': 'short'}

## Best Setup Combination
- 13_30_window | N=101 | WR=82.18% | PF=13.66 | AvgR=2.2564 | DD=2.0R | confidence=HIGH | filters={'session': '13:30'}
- 13_30_window | N=101 | WR=82.18% | PF=13.66 | AvgR=2.2564 | DD=2.0R | confidence=HIGH | filters={'session': '13:30'}

## Worst Setup Combination
- combo_score_gate=8_direction=long_mss_type=CHOCH | N=106 | WR=59.43% | PF=4.03 | AvgR=1.2281 | DD=3.75R | confidence=HIGH | filters={'score_gate': 8, 'direction': 'long', 'mss_type': 'CHOCH'}
- combo_score_gate=8_direction=long_mss_type=CHOCH_ob_present=True | N=106 | WR=59.43% | PF=4.03 | AvgR=1.2281 | DD=3.75R | confidence=HIGH | filters={'score_gate': 8, 'direction': 'long', 'mss_type': 'CHOCH', 'ob_present': True}
- combo_score_gate=8_direction=long_regime=TRENDING_mss_type=CHOCH | N=69 | WR=59.42% | PF=3.84 | AvgR=1.1528 | DD=5.0R | confidence=ACCEPTABLE | filters={'score_gate': 8, 'direction': 'long', 'regime': 'TRENDING', 'mss_type': 'CHOCH'}
- combo_score_gate=8_direction=long_regime=TRENDING_mss_type=CHOCH_ob_present=True | N=69 | WR=59.42% | PF=3.84 | AvgR=1.1528 | DD=5.0R | confidence=ACCEPTABLE | filters={'score_gate': 8, 'direction': 'long', 'regime': 'TRENDING', 'mss_type': 'CHOCH', 'ob_present': True}
- h4_aligned_only | N=0 | WR=0.0% | PF=0.00 | AvgR=0.0 | DD=0.0R | confidence=LOW CONFIDENCE | filters={'h4': 'aligned'}
- h1_h4_aligned | N=0 | WR=0.0% | PF=0.00 | AvgR=0.0 | DD=0.0R | confidence=LOW CONFIDENCE | filters={'h1': 'aligned', 'h4': 'aligned'}
- no_ob_score_ge_15 | N=0 | WR=0.0% | PF=0.00 | AvgR=0.0 | DD=0.0R | confidence=LOW CONFIDENCE | filters={'ob': False, 'score_gate': 15}
- news_filter_unavailable | N=0 | WR=0.0% | PF=0.00 | AvgR=0.0 | DD=0.0R | confidence=LOW CONFIDENCE | filters={'news_filter': 'unavailable'}
- combo_score_gate=8_direction=long_ob_present=False | N=131 | WR=59.54% | PF=3.56 | AvgR=0.994 | DD=3.0R | confidence=HIGH | filters={'score_gate': 8, 'direction': 'long', 'ob_present': False}
- combo_score_gate=10_direction=long_ob_present=False | N=127 | WR=59.84% | PF=3.58 | AvgR=0.9938 | DD=3.0R | confidence=HIGH | filters={'score_gate': 10, 'direction': 'long', 'ob_present': False}

## Recommended Live Rule Changes
- Do not apply automatically.
- Manually review the best_filters.json candidates first.
- Prefer displacement-only FVGs, higher score gates, no choppy regime, and direction/session filters supported by sample size.
- Treat H1/H4 alignment results as incomplete unless historical rows include h1_bias/h4_bias.

## Backtest Caveats
- This run uses existing labeled/backtest rows, not a fresh candle-path simulation for every SL/TP/trailing variant.
- TP/SL/trailing experiments are R-multiple proxies until MFE/MAE and post-entry candle paths are stored.
- News filter is unavailable in historical labels and is marked as unavailable.
- H1/H4 bias is not consistently logged in the existing labeled dataset.
- Any result with N < 30 is LOW CONFIDENCE; N >= 50 is acceptable; N >= 100 is preferred.

## Future Live Learning Schema
```json
{
  "trade_id": "string",
  "market": "NSE|FOREX",
  "symbol": "string",
  "direction": "long|short",
  "entry_reason": "string",
  "exit_reason": "string",
  "sl_reason": "structure|fvg|ob|fixed|atr|manual",
  "tp_reason": "liquidity_pool|fixed_r|session_extreme|manual",
  "trailing_sl_used": "none|breakeven_1r|trail_after_1_5r|structure_trail",
  "trend_bias": "bullish|bearish|ranging",
  "h4_bias": "bullish|bearish|ranging",
  "h1_bias": "bullish|bearish|ranging",
  "choch": "bool",
  "bos": "bool",
  "mss": "bool",
  "fvg_size": "float",
  "fvg_quality": "none|weak|strong",
  "ob_overlap": "bool",
  "liquidity_sweep_type": "BSL|SSL|EQH|EQL|session|none",
  "score": "float",
  "ml_confidence": "float",
  "result": "win|loss|breakeven",
  "r_multiple": "float",
  "max_favorable_excursion": "float",
  "max_adverse_excursion": "float",
  "should_repeat": "bool"
}
```

## ML Memory Files Saved
- nse_backtest_learning: ml_engine\memory\nse_backtest_learning.json
- forex_backtest_learning: ml_engine\memory\forex_backtest_learning.json
- combined_backtest_learning: ml_engine\memory\combined_backtest_learning.json
- best_filters: ml_engine\memory\best_filters.json
- rejected_filters: ml_engine\memory\rejected_filters.json
- long_short_edge: ml_engine\memory\long_short_edge.json
- entry_exit_sl_tp_edge: ml_engine\memory\entry_exit_sl_tp_edge.json
- all_experiment_results_csv: ml_engine\memory\all_experiment_results.csv

## Next Safe Step
Run this research after each new batch of closed trades. Do not change live filters until a human approves a specific rule change and the sample size is acceptable.