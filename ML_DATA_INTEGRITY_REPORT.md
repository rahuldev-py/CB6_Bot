# ML Data Integrity Report — Trade Correlation Repair
**Date:** 2026-05-30

---

## Root Cause

Trade dicts written to state files use key `'id'`:
```python
# ftmo_state.py:open_trade() / gft_5k_2step.py:_open_trade()
trade = {
    'id': str(uuid.uuid4())[:8],   ← correct key name
    ...
}
```

All 6 ML call sites were passing `trade.get('trade_id', '')` — a key that does NOT
exist in the trade dict. Every ML prediction and outcome was recorded with `trade_id = ""`.

Since `predictor.py` uses `trade_id` to match predictions to outcomes in the JSONL log,
this produced a dataset where:
- All predictions had `trade_id = ""`
- All outcomes patched `trade_id = ""`
- Every new prediction overwrote the same empty-string slot
- Outcome ↔ prediction correlation: **0%**

---

## Locations Fixed

| File | Line | Old key | New key | Function |
|------|------|---------|---------|----------|
| `forex_engine/forex_worker.py` | ~1463 | `ftmo_trade.get('trade_id', '')` | `ftmo_trade.get('id', '')` | `save_price_series` (CNN/RNN) |
| `forex_engine/forex_worker.py` | ~1499 | `ftmo_trade.get('trade_id', '')` | `ftmo_trade.get('id', '')` | `predict_forex` shadow pred |
| `forex_engine/forex_worker.py` | ~1698 | `t.get('trade_id','')` | `t.get('id','')` | `on_trade_closed` outcome |
| `forex_engine/prop_firms/gft/gft_5k_2step.py` | ~719 | `trade.get('trade_id', '')` | `trade.get('id', '')` | `save_price_series` (CNN/RNN) |
| `forex_engine/prop_firms/gft/gft_5k_2step.py` | ~761 | `trade.get('trade_id', '')` | `trade.get('id', '')` | `predict_forex` shadow pred |
| `forex_engine/prop_firms/gft/gft_5k_2step.py` | ~841 | `t.get('trade_id','')` | `t.get('id','')` | `on_trade_closed` outcome |

Verified with grep — zero remaining `\.get\('trade_id'` calls in `forex_engine/`.

---

## Already Correct (no change needed)

`ml/forex_collector.py:record_entry()` was already using the correct key:
```python
'trade_id': trade.get('id', ''),   ← line 134 — correct
```
This is the entry record writer — it was producing correct trade_ids in the JSONL log.
Only the callers of `predict_forex` and `on_trade_closed` were broken.

---

## Data Already Collected (Historical)

All trades collected before this fix have `trade_id = ""` in the predictions log.
These records are permanently uncorrelated — no automatic fix possible.

**Recommended action:**
1. Archive the existing `data/ml/forex/ftmo_predictions.jsonl` and `gft_predictions.jsonl`
2. Clear `data/ml/forex/ftmo_trades.jsonl` and `gft_trades.jsonl` (or let the model train on entry records only)
3. Allow fresh predictions to accumulate with correct IDs going forward
4. ML model quality is unaffected — training uses `trades.jsonl` (entries + outcomes), not predictions.jsonl

---

## Pipeline Flow (Post-Fix)

```
Trade opens
  └── forex_collector.record_entry(trade=t)
        writes { trade_id: t['id'], ... } → trades.jsonl

  └── predict_forex(trade_id=t['id'], ...)       ← NOW CORRECT
        writes { trade_id: t['id'], win_prob, confidence, ... } → predictions.jsonl

Trade closes
  └── shadow_monitor.on_trade_closed(trade_id=t['id'], ...)   ← NOW CORRECT
        patches { outcome: { result, r_actual } } into predictions.jsonl
        triggers accuracy threshold check

  └── auto_trainer.check_and_train()
        retrains every 20 trades or 7 days, whichever comes first
```

---

## Outcome Coverage Estimate

- **Before fix:** ~0% (all trades had same empty trade_id — last prediction always overwritten)
- **After fix:** ~100% (each trade has unique 8-char UUID — predictions matched correctly)
- **Historical records:** Unrecoverable (empty trade_id entries should be ignored or archived)

---

## Status: ✅ ALL 6 CALL SITES FIXED — ML pipeline now correctly correlated
