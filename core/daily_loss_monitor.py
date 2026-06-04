# core/daily_loss_monitor.py — Active daily loss guard for live NSE trading.
#
# Runs as a background thread every 30s. When realised + unrealised PnL
# crosses the Rs 1,000 hard cap it:
#   1. Closes ALL open Fyers positions (market orders)
#   2. Cancels ALL pending orders
#   3. Sets data/NSE_EMERGENCY_STOP.flag (NSE-only — does NOT halt forex)
#   4. Sends a Telegram alert
#   5. Writes "daily_halt" to paper_state so can_enter() stays blocked
#      even after a restart within the same trading day.
#
# Survives: restart (flag file + state), reconnect (re-reads positions),
#           token refresh (uses fyers ref that main.py refreshes in place),
#           internet interruption (retries next poll cycle).

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

import os

from utils.logger import logger
from utils.emergency_stop import is_emergency_stop_active
from settings import MAX_DAILY_LOSS_ABS

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NSE_STOP_FLAG = os.path.join(_ROOT, 'data', 'NSE_EMERGENCY_STOP.flag')


def _set_nse_stop(reason: str) -> None:
    """Write NSE-specific stop flag — does NOT touch the forex EMERGENCY_STOP.flag."""
    try:
        os.makedirs(os.path.dirname(_NSE_STOP_FLAG), exist_ok=True)
        with open(_NSE_STOP_FLAG, 'w', encoding='utf-8') as f:
            f.write(reason)
    except OSError as e:
        logger.exception(f"CRITICAL: Could not write NSE_EMERGENCY_STOP flag — {e}")


def _nse_stop_active() -> bool:
    return os.path.exists(_NSE_STOP_FLAG)

_POLL_INTERVAL = 30        # seconds between PnL polls
_lock = threading.Lock()
_monitor_thread: Optional[threading.Thread] = None
_fyers_ref = None          # set via set_fyers(fyers) from main.py after init


def set_fyers(fyers) -> None:
    """Called by main.py after Fyers is initialised so monitor can place exits."""
    global _fyers_ref
    _fyers_ref = fyers


def _get_realised_loss_today() -> float:
    """Read today's realised loss from paper_state (absolute value, Rs)."""
    try:
        from trader.paper_trader import load_state
        from core.risk import daily_loss_used
        state = load_state()
        closed = state.get('closed_trades', [])
        today  = datetime.now().strftime('%Y-%m-%d')
        return daily_loss_used(closed, today)
    except Exception as e:
        logger.debug(f"DailyLossMonitor: realised PnL read error: {e}")
        return 0.0


def _get_bot_open_symbols() -> set:
    """Return the set of symbols the bot currently has open trades for."""
    try:
        from trader.paper_trader import load_state
        state = load_state()
        return {t.get('symbol', '') for t in state.get('open_trades', []) if t.get('symbol')}
    except Exception:
        return set()


def _get_unrealised_loss_fyers() -> float:
    """
    Query Fyers positions API for live unrealised PnL.
    Only counts positions the bot opened (cross-checked via paper_state open_trades).
    Manual trades placed outside the bot are excluded.
    Returns positive number = loss (abs value).
    """
    if _fyers_ref is None:
        return 0.0
    try:
        resp = _fyers_ref.positions()
        if not resp or resp.get('code') != 200:
            return 0.0
        positions = resp.get('netPositions', [])
        bot_symbols = _get_bot_open_symbols()
        if not bot_symbols:
            return 0.0
        pnl = sum(
            float(p.get('unrealizedProfit', p.get('pl', 0)) or 0)
            for p in positions
            if p.get('symbol', '') in bot_symbols
        )
        return abs(pnl) if pnl < 0 else 0.0
    except Exception as e:
        logger.debug(f"DailyLossMonitor: Fyers positions error: {e}")
        return 0.0


def _close_all_positions(fyers) -> tuple:
    """Close every open position via market order.

    Returns (attempted, succeeded) — counts are based on the Fyers response
    code, not just whether place_order() raised an exception.  Fyers can
    return a non-200 dict (e.g. IP whitelist rejection) without raising.
    """
    attempted  = 0
    succeeded  = 0
    failed_log = []
    try:
        resp = fyers.positions()
        if not resp or resp.get('code') != 200:
            return 0, 0
        positions = resp.get('netPositions', [])
        for pos in positions:
            symbol  = pos.get('symbol', '')
            net_qty = int(pos.get('netQty', 0))
            if net_qty == 0 or not symbol:
                continue
            attempted += 1
            side = -1 if net_qty > 0 else 1
            order_data = {
                "symbol"      : symbol,
                "qty"         : abs(net_qty),
                "type"        : 2,
                "side"        : side,
                "productType" : "INTRADAY",
                "limitPrice"  : 0,
                "stopPrice"   : 0,
                "validity"    : "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
                "orderTag"    : "CB6DAYLOSS",
            }
            try:
                result = fyers.place_order(order_data)
                # Fyers returns {'code': 200, 's': 'ok'} on success.
                # Any other response (e.g. IP whitelist -50) is a real failure.
                if isinstance(result, dict) and result.get('code') == 200:
                    succeeded += 1
                    logger.warning(
                        f"DailyLossMonitor: emergency close OK — "
                        f"{symbol} qty={abs(net_qty)}"
                    )
                else:
                    failed_log.append(f"{symbol}: {result}")
                    logger.error(
                        f"DailyLossMonitor: CLOSE FAILED — "
                        f"{symbol} qty={abs(net_qty)} → {result}"
                    )
            except Exception as oe:
                failed_log.append(f"{symbol}: {oe}")
                logger.error(f"DailyLossMonitor: close exception {symbol}: {oe}")
    except Exception as e:
        logger.error(f"DailyLossMonitor: _close_all_positions error: {e}")

    if failed_log:
        logger.critical(
            f"DailyLossMonitor: {len(failed_log)}/{attempted} CLOSE ORDER(S) FAILED — "
            f"positions may still be open! Details: {failed_log}. "
            f"Close manually via Fyers app or /force_resume + manual close."
        )
        try:
            from utils.telegram_alerts import send_message
            send_message(
                f"<b>⛔ EMERGENCY CLOSE FAILED</b>\n\n"
                f"<b>{len(failed_log)} of {attempted} position(s) could not be closed.</b>\n"
                f"Halt is still active — no new trades will be placed.\n\n"
                f"Failures:\n" + "\n".join(f"• {f}" for f in failed_log) + "\n\n"
                f"<b>Action required: close manually in Fyers app.</b>"
            )
        except Exception:
            pass

    return attempted, succeeded


def _cancel_all_pending_orders(fyers) -> int:
    """Cancel every pending/open order. Returns number cancelled."""
    cancelled = 0
    try:
        resp = fyers.orderbook()
        if not resp or resp.get('code') != 200:
            return 0
        for order in resp.get('orderBook', []):
            status = str(order.get('status', '')).upper()
            # 1=PENDING, 6=OPEN in Fyers; string variants also handled
            if status in ('1', '6', 'OPEN', 'PENDING', 'TRIGGER_PENDING'):
                oid = str(order.get('id', ''))
                if not oid:
                    continue
                try:
                    fyers.cancel_order({"id": oid})
                    logger.warning(f"DailyLossMonitor: cancelled order {oid}")
                    cancelled += 1
                except Exception as ce:
                    logger.error(f"DailyLossMonitor: cancel failed {oid}: {ce}")
    except Exception as e:
        logger.error(f"DailyLossMonitor: _cancel_all_pending_orders error: {e}")
    return cancelled


def _mark_daily_halt() -> None:
    """Write daily_halted flag into paper_state so it survives restart.

    Uses state_io primitives directly to avoid a broken transitive import
    (paper_trader → some dependency that no longer exists) causing the flag
    to silently not be written — which was the root cause of the monitor
    re-triggering on every restart (the halt was never persisted).
    """
    try:
        from utils.state_io import load_json_locked, save_json_locked
        import os as _os
        state_path = _os.path.join(_ROOT, 'data', 'paper_state.json')
        default = {
            'capital': 0.0, 'available_capital': 0.0,
            'open_trades': [], 'closed_trades': [],
            'daily_trades': 0, 'daily_losses': 0,
            'total_pnl': 0, 'date': datetime.now().strftime('%Y-%m-%d'),
        }
        state = load_json_locked(state_path, default)
        state['daily_halted']     = True
        state['daily_halt_reason'] = f"Daily loss cap Rs {MAX_DAILY_LOSS_ABS:.0f} hit"
        state['daily_halt_time']   = datetime.now().isoformat()
        save_json_locked(state_path, state)
        logger.info("DailyLossMonitor: daily_halted flag written to paper_state")
    except Exception as e:
        logger.error(f"DailyLossMonitor: _mark_daily_halt error: {e}")


def _send_halt_alert(loss: float) -> None:
    try:
        from utils.telegram_alerts import send_message
        send_message(
            f"<b>⛔ DAILY LOSS CAP HIT — TRADING HALTED</b>\n\n"
            f"Daily loss: <b>Rs {loss:.0f}</b> (cap: Rs {MAX_DAILY_LOSS_ABS:.0f})\n"
            f"Action: All positions closed, all pending orders cancelled.\n"
            f"Status: <b>LOCKED until next session</b>\n\n"
            f"Use /resume tomorrow morning or /force_resume if this is an error."
        )
    except Exception as e:
        logger.error(f"DailyLossMonitor: Telegram alert error: {e}")


def _execute_halt(loss: float) -> None:
    """Full halt sequence: close → cancel → flag → alert.

    Halt flag and state are written regardless of whether position closes
    succeed.  A failed close is logged at CRITICAL + Telegram so the user
    can act manually.  The system stays halted either way.
    """
    logger.critical(
        f"DailyLossMonitor: HALT TRIGGERED — loss=Rs {loss:.0f} >= cap=Rs {MAX_DAILY_LOSS_ABS:.0f}"
    )
    fyers = _fyers_ref
    if fyers:
        attempted, succeeded = _close_all_positions(fyers)
        orders_cancelled     = _cancel_all_pending_orders(fyers)
        if attempted == 0:
            logger.warning("DailyLossMonitor: no open positions to close")
        elif succeeded == attempted:
            logger.warning(
                f"DailyLossMonitor: closed {succeeded}/{attempted} positions, "
                f"cancelled {orders_cancelled} orders"
            )
        else:
            logger.error(
                f"DailyLossMonitor: only {succeeded}/{attempted} positions closed — "
                f"halt still active, manual action may be required"
            )
    # Write halt state AFTER close attempts so the state reflects actual outcome.
    # Halt is persisted regardless of close success — system stays blocked.
    _set_nse_stop(f"Daily loss cap Rs {MAX_DAILY_LOSS_ABS:.0f} hit")
    _mark_daily_halt()
    _send_halt_alert(loss)


def _is_halted_today() -> bool:
    """Return True if daily_halted flag is set in today's state.

    Uses state_io primitives directly — same approach as _mark_daily_halt —
    so a broken paper_trader import chain cannot cause this to silently
    return False and allow the monitor to retrigger.
    """
    try:
        from utils.state_io import load_json_locked
        import os as _os
        state_path = _os.path.join(_ROOT, 'data', 'paper_state.json')
        if not _os.path.exists(state_path):
            return False
        state = load_json_locked(state_path, {})
        return bool(state.get('daily_halted', False))
    except Exception:
        return False


def _monitor_loop() -> None:
    logger.info(f"DailyLossMonitor: started (cap=Rs {MAX_DAILY_LOSS_ABS:.0f}, poll={_POLL_INTERVAL}s)")
    while True:
        try:
            time.sleep(_POLL_INTERVAL)

            # Skip if already halted this session
            if _nse_stop_active() or is_emergency_stop_active() or _is_halted_today():
                continue

            realised   = _get_realised_loss_today()
            unrealised = _get_unrealised_loss_fyers()
            total_loss = realised + unrealised

            logger.debug(
                f"DailyLossMonitor: realised=Rs {realised:.0f} "
                f"unrealised=Rs {unrealised:.0f} total=Rs {total_loss:.0f}"
            )

            if total_loss >= MAX_DAILY_LOSS_ABS:
                with _lock:
                    # Re-check inside lock to prevent double-trigger
                    if not _nse_stop_active() and not is_emergency_stop_active() and not _is_halted_today():
                        _execute_halt(total_loss)

        except Exception as e:
            logger.error(f"DailyLossMonitor: loop error: {e}")


def start_daily_loss_monitor(fyers=None) -> None:
    """
    Start the background daily loss monitor thread.
    Call once from main.py after Fyers is initialised.
    If fyers is None, monitor runs but can only block new entries (no position close).
    """
    global _monitor_thread, _fyers_ref
    if fyers is not None:
        _fyers_ref = fyers
    if _monitor_thread and _monitor_thread.is_alive():
        logger.info("DailyLossMonitor: already running")
        return
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        daemon=True,
        name="DailyLossMonitor",
    )
    _monitor_thread.start()
    logger.info("DailyLossMonitor: thread started")
