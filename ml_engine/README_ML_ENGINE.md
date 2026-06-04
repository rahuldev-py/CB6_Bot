# CB6 QUANTUM — ML ENGINE

## Status: SHADOW MODE ONLY — NOT ACTIVE

All ML flags default to `false`. ML cannot trade, modify risk, block trades, or close trades.
Manual review gate (Step 11) must pass before any activation.

---

## Folder Structure

```
ml_engine/
├── config/
│   ├── ml_config.json          Master config — all ML flags
│   ├── feature_config.json     Feature groups and label definitions
│   └── model_registry.json     Trained model versions and metrics
│
├── data/
│   ├── nse/                    Raw NSE historical OHLCV data
│   ├── forex/                  Raw Forex historical OHLCV data
│   └── labeled/                Auto-labeled datasets (CSV + Parquet)
│
├── features/                   Feature engineering modules
│   ├── market_features.py
│   ├── ict_features.py
│   ├── silver_bullet_features.py
│   ├── risk_features.py
│   ├── session_features.py
│   ├── execution_features.py
│   ├── news_features.py
│   └── feature_pipeline.py
│
├── models/
│   ├── saved/                  Serialized trained models (.pt / .pkl)
│   ├── dnn_trade_scorer.py     DNN: win_prob, expected_r, trade_grade
│   ├── rnn_sequence_model.py   LSTM: continuation/reversal probability
│   └── cnn_chart_vision.py     CNN: chart image classifier (research only)
│
├── training/
│   ├── backtest_loader.py      Load backtest result data
│   ├── trade_history_loader.py Load closed trades from paper_state.json
│   ├── journal_loader.py       Load trade journal CSV
│   ├── live_market_loader.py   Read live candle samples (read-only)
│   ├── label_builder.py        Auto-label using CB6 rule detectors
│   ├── dataset_builder.py      Combine features + labels into dataset
│   ├── data_validator.py       Check for leakage, nulls, distribution
│   ├── train_dnn.py            DNN training + walk-forward validation
│   ├── train_rnn.py            RNN/LSTM training
│   ├── train_cnn.py            CNN training (research)
│   └── validation.py           Shared validation utilities
│
├── inference/
│   ├── predictor.py            Core ML inference (failsafe wrapper)
│   ├── shadow_predictor.py     Shadow-only prediction logger
│   ├── confidence_engine.py    Confidence bucketing (A+/A/B/C)
│   └── inference_router.py     Routes predictions by engine/model
│
├── monitoring/
│   ├── logs/                   Per-prediction shadow logs
│   ├── ml_logger.py            Structured prediction logger
│   ├── performance_tracker.py  Accuracy, calibration, expectancy tracking
│   ├── prediction_audit.py     Audit trail for all shadow predictions
│   └── drift_detector.py       Feature/prediction distribution drift
│
└── reports/
    ├── ml_report_generator.py  Full ML performance report
    └── model_scorecard.py      Per-model scorecard with readiness score
```

---

## Safety Rules (Permanent)

1. ML_ENABLED must be set to `true` in ml_config.json before any ML runs
2. ML_SHADOW_MODE must be `true` — ML observes and logs, never acts
3. ML_CAN_TRADE, ML_CAN_MODIFY_RISK, ML_CAN_BLOCK_TRADES, ML_CAN_CLOSE_TRADES all remain `false` until Step 11 gate passes
4. All inference calls are wrapped in try/except — on any error, CB6 proceeds without ML
5. ML never imports from or writes to: trader/, core/risk.py, core/market_brain.py, core/tick_watcher.py, core/trade_triggers.py

---

## Activation Gate (Step 11)

Before ML can do anything beyond shadow logging, ALL of the following must be true:

- [ ] 500+ historical/backtest trades scored
- [ ] 100+ live shadow predictions logged
- [ ] A+ bucket outperforms A bucket
- [ ] All confidence buckets have positive expectancy
- [ ] Zero ML crashes for 14 consecutive days
- [ ] Zero execution delay introduced by ML inference
- [ ] No live logic modified
- [ ] Manual human review completed

**Current status: NOT_READY**

---

## Build Steps

| Step | Description | Status |
|------|-------------|--------|
| 1 | Read architecture docs | DONE |
| 2 | Create folder structure + config | DONE |
| 3 | Data ingestion loaders | PENDING |
| 4 | Auto-labeling pipeline | PENDING |
| 5 | Feature pipeline | PENDING |
| 6 | Train DNN | PENDING |
| 7 | Train RNN/LSTM | PENDING |
| 8 | CNN chart vision (research) | PENDING |
| 9 | Shadow inference | PENDING |
| 10 | Monitoring + reports | PENDING |
| 11 | Manual review gate | PENDING |
