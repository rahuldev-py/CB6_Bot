# CB6 ML Internet Context (Read-Only)

This module adds **read-only** internet context to ML shadow inference.

## Added

- `ml_engine/inference/internet_context.py`
  - Google News RSS headlines
  - Yahoo Finance RSS headlines
  - Macro calendar feed
  - JSONL context logging (`ml_engine/logs/market_context.jsonl`)

- `route_with_market_context(...)` in `ml_engine/inference/inference_router.py`
  - Calls existing `route(...)` first
  - Appends internet context for analysis/logging only
  - Never changes execution behavior

## Safety Guarantees

- No broker API calls
- No order placement
- No SL/TP/lot/risk mutation
- No trade blocking/closing
- If internet fetch fails, returns safely with `read_only=true` status and empty sources

## Usage

```python
from ml_engine.inference.inference_router import route_with_market_context

result = route_with_market_context(signal_dict)
```

Use this output for research/shadow logs only.
