# core/trade_triggers.py — Bridges paper-trader state to TickWatcher.
# When a trade opens: registers SL + target triggers.
# When a tick fires a trigger: calls the appropriate paper_trader handler.
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger
from core.tick_watcher import (
    get_watcher,
    TRIGGER_SL_LONG, TRIGGER_SL_SHORT,
    TRIGGER_TP_LONG, TRIGGER_TP_SHORT,
)


def _trigger_id(trade_id: str, kind: str, idx: int = 0) -> str:
    return f"{trade_id}:{kind}:{idx}"


def register_trade_triggers(trade: dict):
    """
    Register SL + targets (T1/T2/T3) for a paper trade.
    Returns count of triggers registered.
    Must be called every time a trade is opened or its SL changes.
    """
    direction = (trade.get('direction') or 'BUY').upper()
    symbol    = trade['symbol']
    trade_id  = trade['id']
    sl        = trade.get('current_sl') or trade.get('stop_loss')

    is_long = direction in ('BUY', 'BULLISH')
    sl_kind = TRIGGER_SL_LONG if is_long else TRIGGER_SL_SHORT
    tp_kind = TRIGGER_TP_LONG if is_long else TRIGGER_TP_SHORT

    cancel_trade_triggers(trade_id)   # clean slate before re-register
    w = get_watcher()
    n = 0

    # SL trigger
    if sl:
        if w.watch(_trigger_id(trade_id, 'SL'), symbol, sl_kind, sl,
                   _on_sl_hit, meta={'trade_id': trade_id}):
            n += 1

    # Target triggers — only register the ones not already hit
    targets_hit = set(trade.get('targets_hit', []))
    for i, key in enumerate(('target1', 'target2', 'target3'), start=1):
        if f'T{i}' in targets_hit:
            continue
        level = trade.get(key)
        if level:
            tid = _trigger_id(trade_id, f'T{i}')
            if w.watch(tid, symbol, tp_kind, level, _on_target_hit,
                       meta={'trade_id': trade_id, 'target_idx': i, 'target_price': level}):
                n += 1

    return n


def cancel_trade_triggers(trade_id: str) -> int:
    """Cancel all triggers for a trade. Used when trade closes."""
    w = get_watcher()
    n = 0
    for kind in ('SL', 'T1', 'T2', 'T3'):
        if w.cancel(_trigger_id(trade_id, kind)):
            n += 1
    return n


# ────────────────────────────────────────────────────────────────────
#   CALLBACKS — called from WebSocket thread
# ────────────────────────────────────────────────────────────────────
def _on_sl_hit(payload: dict):
    """Fired when LTP crosses SL. Closes the paper trade at SL level."""
    trade_id = payload['meta'].get('trade_id')
    if not trade_id:
        return
    logger.warning(f"WS-SL hit: {payload['symbol']} @ {payload['ltp']} (trade {trade_id})")
    try:
        from trader.paper_trader import close_paper_trade_by_id
        close_paper_trade_by_id(trade_id, exit_price=payload['level'],
                                 reason='SL_HIT_WS')
    except Exception as e:
        logger.error(f"WS-SL close failed: {e}")
    cancel_trade_triggers(trade_id)


def _on_target_hit(payload: dict):
    """Fired when LTP hits a target. Books partial / promotes SL."""
    trade_id    = payload['meta'].get('trade_id')
    target_idx  = payload['meta'].get('target_idx')
    if not trade_id or not target_idx:
        return
    logger.info(f"WS-T{target_idx} hit: {payload['symbol']} @ {payload['ltp']} "
                f"(trade {trade_id})")
    try:
        from trader.paper_trader import handle_target_hit_by_id
        handle_target_hit_by_id(trade_id, target_idx=target_idx,
                                 hit_price=payload['level'])
    except Exception as e:
        logger.error(f"WS-T{target_idx} handle failed: {e}")
