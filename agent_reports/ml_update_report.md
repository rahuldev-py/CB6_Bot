# SHADOW ML Report — 2026-06-12 23:53

## Retrain Decision: YES ⚡
**Deployment Status:** SHADOW TEST NEW MODEL
**Overfitting Risk:** MEDIUM
**Reason:** All models have low accuracy and high val_loss, with none meeting target metrics

## Model Health
| Model | Grade | Acc | Prec | Val Loss | Age | Status |
|-------|-------|-----|------|----------|-----|--------|
| dnn_trade_scorer_nse | D | 0.5298 | 0.6872 | 0.3073 | 1d | 🔴 |
| dnn_trade_scorer_forex | D | 0.537 | 0.6988 | 0.2678 | 1d | 🔴 |
| rnn_sequence_nse | D | 0.537 | 0.6988 | 0.2539 | 1d | 🔴 |

## Feature Improvements
- session_type
- direction_bias
- symbol_group
- H4_bias_aligned

## Specific Improvements
- Add session_type feature
- Add direction_bias feature
- Add symbol_group feature
- Add H4_bias_aligned binary feature

## Deployment Protocol
```
Idea → Backtest → Shadow Test → Paper Test → Rahul Approval → Production
```

Current status: **SHADOW TEST NEW MODEL**

⚠️ ML models NEVER touch live execution. Shadow mode only.
