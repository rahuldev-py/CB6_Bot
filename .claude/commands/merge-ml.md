# /merge-ml — Review & Integrate ML System Updates

Audit the shadow ML system, review prediction accuracy, and check if it's ready to inform (not control) trade sizing.

## Steps to Execute

1. **Read ML status files:**
   - Search: `Glob("ml/**/*.json")` or `Glob("data/ml_*.json")`
   - Find model accuracy logs, prediction history

2. **Display ML system health:**
```
ML Shadow System — Status
──────────────────────────
NSE model      : DNN+CNN+RNN | Accuracy: XX% | Last retrain: YYYY-MM-DD
FTMO model     : DNN+CNN+RNN | Accuracy: XX% | Last retrain: YYYY-MM-DD
GFT model      : DNN+CNN+RNN | Accuracy: XX% | Last retrain: YYYY-MM-DD

Predictions vs actual (last 20 trades):
  Correct direction : XX / 20  (XX%)
  Shadow only       : YES — not touching orders
```

3. **Retrain trigger check:**
   - If last_retrain > 7 days ago: recommend `/ml_train`
   - If trades_since_retrain > 20: recommend `/ml_train`

4. **Accuracy threshold:**
   - If model accuracy < 55%: keep shadow-only, do NOT promote to position sizing
   - If model accuracy ≥ 60% for 30+ trades: document as candidate for A+ boost signal
   - NEVER allow ML to place, modify, or cancel orders directly

5. **Integration safety check:**
   - Grep `ml/` for any code that calls `place_order`, `close_trade`, `modify_sl` — flag if found
   - ML output must only be: prediction label + confidence score, written to log file

6. **Report:** Summary of accuracy, recommendation (keep shadow / promote to signal boost / retrain).

## Critical Rule
ML is SHADOW ONLY. It predicts. It never trades. Any code path connecting ML output directly to order execution must be removed immediately.
