# forex_engine/forex_paper_trader.py
# Backward-compat shim — canonical implementation is in:
#   forex_engine/prop_firms/ftmo/ftmo_state.py
from forex_engine.prop_firms.ftmo.ftmo_state import (
    load_state,
    open_trade,
    rollback_trade,
    update_trade_ticket,
    update_trade_fill_price,
    update_trades,
    manual_exit_trade,
    get_summary,
    get_risk_mode,
    can_open_trade,
    compute_best_day_stats,
)

__all__ = [
    'load_state', 'open_trade', 'rollback_trade',
    'update_trade_ticket', 'update_trade_fill_price',
    'update_trades', 'manual_exit_trade', 'get_summary',
    'get_risk_mode', 'can_open_trade', 'compute_best_day_stats',
]
