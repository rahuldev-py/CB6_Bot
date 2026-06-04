# scanner/websocket_feed.py — Real-time tick feed (TrueData primary, Fyers fallback)
#
# TrueData WebSocket is the primary live feed when credentials are configured.
# Fyers WebSocket is used as fallback (or when TrueData is not available).
#
# Usage:
#   init_truedata(symbols)         → start TrueData live feed (preferred)
#   init(access_token, client_id)  → start Fyers WS (fallback)
#   subscribe(symbols)             → add symbols to active feed
#   get_latest_tick(symbol)        → {'ltp', 'volume', 'ts'}
import os
import sys
import threading
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger

_ws_client     = None
_subscriptions = set()
_tick_cache    = {}     # {symbol: {'ltp': float, 'volume': int, 'ts': str}}
_lock          = threading.Lock()


def get_latest_tick(symbol):
    """Thread-safe read of the most recent tick for a symbol."""
    with _lock:
        return _tick_cache.get(symbol, {}).copy()


def _on_message(message):
    """
    Fyers tick handler. Updates _tick_cache for fast read AND
    feeds the global TickWatcher so registered triggers can fire.
    """
    try:
        sym = message.get('symbol')
        ltp = message.get('ltp') or message.get('last_price')
        vol = message.get('vol_traded_today', 0)
        ts  = message.get('exch_feed_time', '')
        if sym and ltp is not None:
            ltp_f = float(ltp)
            with _lock:
                _tick_cache[sym] = {
                    'ltp'   : ltp_f,
                    'volume': int(vol),
                    'ts'    : ts,
                }
            # Push to tick watcher for trigger evaluation
            try:
                from core.tick_watcher import get_watcher
                get_watcher().on_tick(sym, ltp_f)
            except Exception as e:
                logger.debug(f"Tick watcher dispatch failed: {e}")
    except Exception as e:
        logger.debug(f"WS tick parse error: {e}")


def _on_error(err):
    logger.error(f"Fyers WS error: {err}")


def _on_close(reason):
    logger.warning(f"Fyers WS closed: {reason}")


def _on_open():
    logger.info("Fyers WS connected")
    # Re-subscribe after reconnect
    if _ws_client and _subscriptions:
        try:
            _ws_client.subscribe(symbols=list(_subscriptions), data_type="SymbolUpdate")
        except Exception as e:
            logger.error(f"Re-subscribe failed: {e}")


def init(access_token, client_id):
    """Create and start the WS client. Token format: 'CLIENT_ID:JWT'."""
    global _ws_client
    try:
        from fyers_apiv3.FyersWebsocket import data_ws
        token_str = f"{client_id}:{access_token}" if ':' not in access_token else access_token
        _ws_client = data_ws.FyersDataSocket(
            access_token = token_str,
            log_path     = os.path.join(os.getcwd(), "logs"),
            litemode     = False,
            write_to_file= False,
            reconnect    = True,
            on_connect   = _on_open,
            on_close     = _on_close,
            on_error     = _on_error,
            on_message   = _on_message,
        )
        threading.Thread(target=_ws_client.connect, daemon=True).start()
        return True
    except Exception as e:
        logger.error(f"WS init error: {e}")
        return False


def subscribe(symbols):
    """Add symbols to the live subscription set."""
    if not _ws_client:
        return False
    try:
        new = [s for s in symbols if s not in _subscriptions]
        if new:
            _ws_client.subscribe(symbols=new, data_type="SymbolUpdate")
            _subscriptions.update(new)
        return True
    except Exception as e:
        logger.error(f"WS subscribe error: {e}")
        return False


def unsubscribe(symbols):
    if not _ws_client:
        return False
    try:
        _ws_client.unsubscribe(symbols=symbols)
        for s in symbols:
            _subscriptions.discard(s)
        return True
    except Exception as e:
        logger.error(f"WS unsubscribe error: {e}")
        return False


def is_active():
    return _ws_client is not None


# ── TrueData WebSocket feed (primary) ────────────────────────────────────────

_td_active = False


def init_truedata(symbols: list) -> bool:
    """
    Start TrueData WebSocket live feed and subscribe to symbols.
    Ticks are dispatched into _tick_cache (same dict used by get_latest_tick)
    and forwarded to core.tick_watcher — identical to the Fyers path.

    Returns True on success. Falls back gracefully if credentials are missing.
    """
    global _td_active
    try:
        from data.truedata_feed import get_manager, fyers_to_td_symbol
        td = get_manager()
        # Map Fyers symbols to TrueData format
        td_symbols = [fyers_to_td_symbol(s) for s in symbols]
        ok = td.connect_live(td_symbols)
        if ok:
            _td_active = True
            logger.info(f"TrueData WS: live feed active ({len(td_symbols)} symbols)")
        return ok
    except Exception as exc:
        logger.error(f"TrueData WS init error: {exc}")
        return False


def is_truedata_active() -> bool:
    """Return True if TrueData WebSocket feed is running."""
    return _td_active


def subscribe_truedata(symbols: list) -> bool:
    """Add symbols to the TrueData live feed after initial connect."""
    if not _td_active:
        return False
    try:
        from data.truedata_feed import get_manager, fyers_to_td_symbol
        td = get_manager()
        td_symbols = [fyers_to_td_symbol(s) for s in symbols]
        td.add_live_symbols(td_symbols)
        return True
    except Exception as exc:
        logger.error(f"TrueData subscribe error: {exc}")
        return False
