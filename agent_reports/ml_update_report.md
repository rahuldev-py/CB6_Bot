# SHADOW ML Report — 2026-06-08 23:30

## Retrain Decision: YES ⚡
**Deployment Status:** SHADOW TEST NEW MODEL
**Overfitting Risk:** MEDIUM
**Reason:** RNN NSE has high val_loss (0.822), CNN NSE and DNN NSE are near threshold for test_acc and val_loss

## Model Health
| Model | Grade | Acc | Prec | Val Loss | Age | Status |
|-------|-------|-----|------|----------|-----|--------|
| cnn_nse | B | 0.7584 | 0.9048 | 0.555972 | 12d | 🟢 |
| dnn_nse | B | 0.7383 | 0.9184 | 0.57245 | 12d | 🟢 |
| rnn_nse | A | 0.8 | ? | 0.821857 | 12d | 🟡 |

## Feature Improvements
- session_type
- direction_bias
- symbol_group
- H4_bias_aligned

## Specific Improvements
- London session feature
- BEARISH bias feature

## Deployment Protocol
```
Idea → Backtest → Shadow Test → Paper Test → Rahul Approval → Production
```

Current status: **SHADOW TEST NEW MODEL**

⚠️ ML models NEVER touch live execution. Shadow mode only.
