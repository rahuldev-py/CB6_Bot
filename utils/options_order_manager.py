# utils/options_order_manager.py
# NSE Index Options Order Manager — auto-entry + premium monitoring + auto-exit.
#
# Design:
#   • 1 lot per trade, always ATM strike, market order
#   • Entry via Fyers place_order (INTRADAY)
#   • SL trigger : -40% of entry premium
#   • T1 trigger : +80%  → exit if standard; move SL to break-even if A+ (score>=15)
#   • T2 trigger : +150% → exit all remaining
#   • Force-exit at 15:10 IST if still open
#   • Telegram alert on entry + every exit

import threading
import time
import logging
from datetime import datetime, date
from typing import Optional

import pytz

from core.execution_guard import execute_guarded_order
from scanner.options_strike_selector import get_option_ltp
from utils.telegram_alerts import send_message

logger = logging.getLogger(__name__)
IST = pytz.timezone('Asia/Kolkata')

# Premium % targets (relative to entry LTP)
SL_PCT   = -0.40   # -40 %
T1_PCT   =  0.80   # +80 %
T2_PCT   =  1.50   # +150 %
APLUS_SCORE_THRESHOLD = 15  # score >= 15 → hold to T2 instead of exiting at T1

POLL_INTERVAL_SEC = 20        # check premium every 20 s
FORCE_EXIT_IST    = (15, 10)  # HH, MM — force close before 15:15 expiry


class OptionsPosition:
    """Tracks a single live options position."""

    def __init__(
        self, symbol: str, index: str, strike: int, option_type: str,
        expiry: date, lot_size: int, entry_premium: float, order_id: str,
        setup_score: int, direction: str, signal_info: dict,
    ):
        self.symbol        = symbol
        self.index         = index
        self.strike        = strike
        self.option_type   = option_type
        self.expiry        = expiry
        self.lot_size      = lot_size
        self.entry_premium = entry_premium
        self.order_id      = order_id
        self.setup_score   = setup_score
        self.direction     = direction
        self.signal_info   = signal_info    # raw SB setup dict for reference

        self.sl_premium    = round(entry_premium * (1 + SL_PCT), 2)
        self.t1_premium    = round(entry_premium * (1 + T1_PCT), 2)
        self.t2_premium    = round(entry_premium * (1 + T2_PCT), 2)

        self.t1_hit        = False
        self.be_sl_active  = False          # break-even SL after T1 for A+ setups
        self.closed        = False
        self.exit_reason   = None
        self.exit_premium  = None
        self.pnl_rs        = None

    def is_aplus(self) -> bool:
        return self.setup_score >= APLUS_SCORE_THRESHOLD

    def lot_cost(self) -> float:
        return self.entry_premium * self.lot_size

    def pnl(self, current_premium: float) -> float:
        return (current_premium - self.entry_premium) * self.lot_size


class OptionsOrderManager:
    """
    Manages live NSE options positions.
    Thread-safe: one background thread per active position monitors the premium.
    """

    def __init__(self, fyers_getter):
        """
        fyers_getter: callable that returns a live Fyers client instance.
        Using a getter avoids holding a stale reference across token refreshes.
        """
        self._fyers_getter  = fyers_getter
        self._positions: dict[str, OptionsPosition] = {}  # keyed by symbol
        self._lock = threading.Lock()

    @property
    def _fyers(self):
        return self._fyers_getter()

    # ── Entry ─────────────────────────────────────────────────────────────────

    def enter(self, option_info: dict, setup: dict) -> Optional[OptionsPosition]:
        """
        Place a 1-lot market buy for the given ATM option.

        option_info : dict from select_atm_option()
        setup       : raw Silver Bullet setup dict (for score, direction, signal details)

        Returns the OptionsPosition on success, None on failure.
        """
        sym     = option_info['symbol']
        index   = option_info['index']
        strike  = option_info['strike']
        otype   = option_info['option_type']
        expiry  = option_info['expiry']
        lot     = option_info['lot_size']
        spot    = option_info['spot']

        _raw = setup.get('confluence', setup.get('score', 0))
        score = _raw.get('score', 0) if isinstance(_raw, dict) else int(_raw or 0)
        direction = setup.get('direction', 'BULLISH')

        # Prevent duplicate positions on the same symbol
        with self._lock:
            if sym in self._positions and not self._positions[sym].closed:
                logger.warning(f"[OPTIONS] Already have open position on {sym} — skipping entry")
                return None

        # Fetch LTP to confirm the option is liquid
        ltp = get_option_ltp(self._fyers, sym)
        if not ltp or ltp < 1.0:
            logger.warning(f"[OPTIONS] LTP fetch failed or too low for {sym} (ltp={ltp}) — skipping")
            return None

        order_data = {
            "symbol"     : sym,
            "qty"        : lot,
            "type"       : 2,           # market order
            "side"       : 1,           # BUY
            "productType": "INTRADAY",
            "validity"   : "DAY",
            "limitPrice" : 0,
            "stopPrice"  : 0,
            "offlineOrder": False,
        }

        try:
            result = execute_guarded_order(
                self._fyers.place_order, order_data,
                symbol=sym, intent="ENTRY",
            )
        except Exception as e:
            logger.error(f"[OPTIONS ENTRY FAILED] {sym}: {e}")
            self._send_alert(
                f"❌ <b>OPTIONS ENTRY FAILED</b>\n"
                f"Symbol: <code>{sym}</code>\nError: {e}"
            )
            return None

        # Confirm order accepted (Fyers returns {"s": "ok", "id": "..."} on success)
        if not result or result.get('s') != 'ok':
            logger.error(f"[OPTIONS ENTRY REJECTED] {sym}: {result}")
            return None

        order_id = result.get('id', 'UNKNOWN')

        pos = OptionsPosition(
            symbol=sym, index=index, strike=strike, option_type=otype,
            expiry=expiry, lot_size=lot, entry_premium=ltp, order_id=order_id,
            setup_score=score, direction=direction, signal_info=setup,
        )

        with self._lock:
            self._positions[sym] = pos

        logger.info(
            f"[OPTIONS ENTRY] {sym} entry_prem={ltp} lot={lot} "
            f"SL={pos.sl_premium} T1={pos.t1_premium} T2={pos.t2_premium} "
            f"score={score} A+={pos.is_aplus()}"
        )

        # Telegram entry card
        self._send_entry_alert(pos, spot, setup)

        # Start monitoring thread
        t = threading.Thread(target=self._monitor_loop, args=(sym,), daemon=True)
        t.start()

        return pos

    # ── Monitor loop ─────────────────────────────────────────────────────────

    def _monitor_loop(self, sym: str):
        """Background thread: poll LTP every POLL_INTERVAL_SEC and apply SL/target logic."""
        while True:
            time.sleep(POLL_INTERVAL_SEC)

            with self._lock:
                pos = self._positions.get(sym)
                if pos is None or pos.closed:
                    break

            # Force-exit check (15:10 IST)
            now_ist = datetime.now(IST)
            if (now_ist.hour, now_ist.minute) >= FORCE_EXIT_IST:
                self._close_position(sym, reason="FORCE_EXIT_EOD")
                break

            ltp = get_option_ltp(self._fyers, sym)
            if ltp is None:
                logger.debug(f"[OPTIONS MONITOR] {sym}: LTP unavailable, retrying")
                continue

            with self._lock:
                pos = self._positions.get(sym)
                if pos is None or pos.closed:
                    break

            # SL check
            if pos.be_sl_active:
                # After T1 hit for A+ setup: SL at break-even (entry premium)
                if ltp <= pos.entry_premium:
                    self._close_position(sym, reason="BE_SL")
                    break
            else:
                if ltp <= pos.sl_premium:
                    self._close_position(sym, reason="SL_HIT")
                    break

            # T1 check
            if not pos.t1_hit and ltp >= pos.t1_premium:
                if pos.is_aplus():
                    # A+ setup: move SL to break-even, hold for T2
                    with self._lock:
                        pos.t1_hit = True
                        pos.be_sl_active = True
                    logger.info(f"[OPTIONS T1] {sym} A+ setup — moving SL to BE, holding for T2")
                    self._send_alert(
                        f"🎯 <b>OPTIONS T1 HIT</b> — A+ Setup — Holding for T2\n"
                        f"<code>{sym}</code>\n"
                        f"Current: ₹{ltp:.2f} | SL moved to BE ₹{pos.entry_premium:.2f}\n"
                        f"Next target T2: ₹{pos.t2_premium:.2f} (+150%)"
                    )
                    continue
                else:
                    # Standard setup: exit at T1
                    self._close_position(sym, reason="T1_HIT")
                    break

            # T2 check
            if pos.t1_hit and ltp >= pos.t2_premium:
                self._close_position(sym, reason="T2_HIT")
                break

    # ── Exit ─────────────────────────────────────────────────────────────────

    def _close_position(self, sym: str, reason: str):
        """Place a market sell to close the position and send exit alert."""
        with self._lock:
            pos = self._positions.get(sym)
            if pos is None or pos.closed:
                return
            pos.closed     = True
            pos.exit_reason = reason

        logger.info(f"[OPTIONS EXIT] {sym} reason={reason}")

        # Fetch final LTP for P&L calculation
        ltp = get_option_ltp(self._fyers, sym) or pos.entry_premium

        order_data = {
            "symbol"     : sym,
            "qty"        : pos.lot_size,
            "type"       : 2,           # market order
            "side"       : -1,          # SELL
            "productType": "INTRADAY",
            "validity"   : "DAY",
            "limitPrice" : 0,
            "stopPrice"  : 0,
            "offlineOrder": False,
        }

        try:
            result = execute_guarded_order(
                self._fyers.place_order, order_data,
                symbol=sym, intent=f"CLOSE_{reason}",
            )
            if not result or result.get('s') != 'ok':
                logger.error(f"[OPTIONS EXIT FAILED] {sym}: {result}")
        except Exception as e:
            logger.error(f"[OPTIONS EXIT EXCEPTION] {sym}: {e}")

        with self._lock:
            pos.exit_premium = ltp
            pos.pnl_rs       = pos.pnl(ltp)

        self._send_exit_alert(pos)

    def force_exit_all(self):
        """Manual kill — close all open positions immediately."""
        with self._lock:
            symbols = [s for s, p in self._positions.items() if not p.closed]
        for sym in symbols:
            self._close_position(sym, reason="MANUAL_KILL")

    def open_positions(self) -> list[OptionsPosition]:
        with self._lock:
            return [p for p in self._positions.values() if not p.closed]

    # ── Alerts ────────────────────────────────────────────────────────────────

    def _send_entry_alert(self, pos: OptionsPosition, spot: float, setup: dict):
        mss   = setup.get('mss_type', 'MSS')
        setup_score = pos.setup_score
        aplus = "⭐ A+" if pos.is_aplus() else ""
        plan  = "Hold to T2 (+150%)" if pos.is_aplus() else "Exit at T1 (+80%)"

        msg = (
            f"🟢 <b>OPTIONS ENTRY</b> {aplus}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Index  :</b> {pos.index}\n"
            f"<b>Option :</b> {pos.option_type} {pos.strike}\n"
            f"<b>Symbol :</b> <code>{pos.symbol}</code>\n"
            f"<b>Expiry :</b> {pos.expiry.strftime('%d %b %Y')}\n"
            f"<b>Spot   :</b> ₹{spot:,.2f}\n"
            f"<b>Prem   :</b> ₹{pos.entry_premium:.2f}\n"
            f"<b>Lot    :</b> {pos.lot_size} units  |  Cost ₹{pos.lot_cost():,.0f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>SL     :</b> ₹{pos.sl_premium:.2f}  (-40%)\n"
            f"<b>T1     :</b> ₹{pos.t1_premium:.2f}  (+80%)\n"
            f"<b>T2     :</b> ₹{pos.t2_premium:.2f}  (+150%)\n"
            f"<b>Plan   :</b> {plan}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Setup  :</b> {mss} | Score {setup_score}"
        )
        self._send_alert(msg)

    def _send_exit_alert(self, pos: OptionsPosition):
        pnl   = pos.pnl_rs or 0.0
        emoji = "🟢" if pnl >= 0 else "🔴"
        pct   = ((pos.exit_premium / pos.entry_premium) - 1) * 100 if pos.entry_premium else 0

        reason_label = {
            'SL_HIT'        : '🔴 Stop Loss Hit (-40%)',
            'BE_SL'         : '🟡 Break-Even Stop',
            'T1_HIT'        : '✅ T1 Hit (+80%)',
            'T2_HIT'        : '🏆 T2 Hit (+150%)',
            'FORCE_EXIT_EOD': '🕐 EOD Force Close',
            'MANUAL_KILL'   : '⛔ Manual Kill',
        }.get(pos.exit_reason, pos.exit_reason)

        msg = (
            f"{emoji} <b>OPTIONS EXIT — {reason_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Symbol :</b> <code>{pos.symbol}</code>\n"
            f"<b>Entry  :</b> ₹{pos.entry_premium:.2f}\n"
            f"<b>Exit   :</b> ₹{pos.exit_premium:.2f}  ({pct:+.1f}%)\n"
            f"<b>P&L    :</b> {emoji} ₹{pnl:+,.0f}\n"
            f"<b>Lot    :</b> {pos.lot_size}"
        )
        self._send_alert(msg)

    def _send_alert(self, msg: str):
        try:
            send_message(msg, parse_mode='HTML')
        except Exception as e:
            logger.error(f"[OPTIONS ALERT ERROR] {e}")
