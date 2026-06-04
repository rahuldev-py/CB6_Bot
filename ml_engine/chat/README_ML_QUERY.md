# CB6 ML Research Query

Offline-only research layer for asking:

- How can CB6 reach 80-85% win rate?
- How can CB6 reach profit factor 2.25+?
- Which filters improve or weaken CB6?
- Which market, direction, session, entry, exit, SL, TP, and trailing logic is strongest?

## Safety

This folder does not start NSE, Forex, MT5, Fyers, TrueData, Telegram, or broker execution.

It reads historical/labeled CSV/JSON data and writes advisory memory under:

```text
ml_engine/memory/
```

No live filters are changed automatically.

## Run

```powershell
cd c:\cb6_bot
python -m ml_engine.chat.ml_research_query
```

Optional:

```powershell
python -m ml_engine.chat.ml_research_query --question "Which filters improve PF above 2.25?"
```

## Outputs

- `nse_backtest_learning.json`
- `forex_backtest_learning.json`
- `combined_backtest_learning.json`
- `best_filters.json`
- `rejected_filters.json`
- `long_short_edge.json`
- `entry_exit_sl_tp_edge.json`
- `future_live_learning_schema.json`
- `ml_learning_summary.md`
- `all_experiment_results.csv`

## Caveats

Some experiments are proxy-only because current historical rows do not yet include every field:

- Real H1/H4 bias is incomplete unless logged per historical trade.
- News filter labels are unavailable.
- TP/SL/trailing variants use R-multiple transformations, not full post-entry candle path simulation.
- MFE/MAE learning needs future live trade logging.

