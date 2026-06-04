# core/prop_firm_risk_controller.py
#
# PropFirmRiskController — Intercepts every order request with prop-firm rules.
#
# Guards enforced (in order):
#   1. Daily max-loss limit          (default 4% of starting daily balance)
#   2. Trailing max-drawdown guard   (0.5% buffer before breach level)
#   3. News blackout windows         (hard-coded macro events + dynamic monitor)
#
# Usage:
#   controller = PropFirmRiskController(config)
#   ok, reason = controller.can_trade(account_state)
#   if not ok:
#       logger.warning(reason)
#       return
#   # ... place order

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from utils.logger import logger


class PropFirmRiskController:
    """
    Intercept layer for ALL prop-firm order requests.

    Parameters (all in `config` dict)
    ──────────────────────────────────
    daily_loss_limit_pct    : float  — max realised+unrealised loss per day
                                       as fraction of starting_balance (default 0.04)
    trailing_dd_limit_pct   : float  — prop-firm max-drawdown rule (default 0.10 = 10%)
    trailing_dd_buffer_pct  : float  — stop new entries this many % before breach
                                       (default 0.005 = 0.5%)
    news_blackout_minutes   : int    — block entries this many minutes around
                                       a high-impact event (default 30)
    account_name            : str    — human label for Telegram alerts
    """

    def __init__(self, config: dict):
        self.daily_loss_limit_pct  = float(config.get('daily_loss_limit_pct',  0.04))
        self.trailing_dd_limit_pct = float(config.get('trailing_dd_limit_pct', 0.10))
        self.trailing_dd_buffer    = float(config.get('trailing_dd_buffer_pct', 0.005))
        self.news_blackout_minutes = int(config.get('news_blackout_minutes',    30))
        self.account_name          = str(config.get('account_name', 'PropFirm'))

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def can_trade(self, state: dict) -> tuple[bool, str]:
        """
        Master gate — runs all three checks in sequence.

        `state` must contain:
            starting_balance  : float  — balance at start of trading day
            current_equity    : float  — live equity (realised + unrealised)
            peak_equity       : float  — highest equity seen since account opened
            daily_pnl         : float  — today's realised P&L (negative = loss)
            unrealised_pnl    : float  — current open-trade P&L (0 if flat)
        """
        ok, reason = self.check_daily_loss(state)
        if not ok:
            return False, reason

        ok, reason = self.check_trailing_drawdown(state)
        if not ok:
            return False, reason

        ok, reason = self.check_news_blackout()
        if not ok:
            return False, reason

        return True, 'all_guards_pass'

    # ─────────────────────────────────────────────────────────────────────────
    # Guard 1: Daily max-loss
    # ─────────────────────────────────────────────────────────────────────────

    def check_daily_loss(self, state: dict) -> tuple[bool, str]:
        """
        Block if (|daily_pnl| + |unrealised_pnl|) >= daily_loss_limit_pct × starting_balance.

        Checks COMBINED realised + unrealised so a large open loss doesn't sneak
        past the guard before it's booked.
        """
        starting = float(state.get('starting_balance', 0))
        if starting <= 0:
            return True, 'starting_balance_unknown'

        daily_real    = float(state.get('daily_pnl',       0))
        daily_unreal  = float(state.get('unrealised_pnl',  0))
        total_loss    = -(daily_real + daily_unreal)   # positive when losing

        limit = starting * self.daily_loss_limit_pct
        if total_loss >= limit:
            pct = total_loss / starting * 100
            msg = (
                f"{self.account_name} DAILY LOSS BREACH: "
                f"loss ${total_loss:.2f} ({pct:.2f}%) >= "
                f"limit ${limit:.2f} ({self.daily_loss_limit_pct*100:.1f}%). "
                f"No new entries today."
            )
            logger.warning(msg)
            return False, msg

        # Warning at 75% of daily limit
        if total_loss >= limit * 0.75:
            pct = total_loss / starting * 100
            logger.info(
                f"{self.account_name} DAILY LOSS WARNING: "
                f"${total_loss:.2f} ({pct:.2f}%) — "
                f"approaching {self.daily_loss_limit_pct*100:.1f}% daily limit"
            )

        return True, 'daily_loss_ok'

    # ─────────────────────────────────────────────────────────────────────────
    # Guard 2: Trailing max-drawdown
    # ─────────────────────────────────────────────────────────────────────────

    def check_trailing_drawdown(self, state: dict) -> tuple[bool, str]:
        """
        Block if current_equity is within trailing_dd_buffer of the breach level.

        Breach level = peak_equity × (1 - trailing_dd_limit_pct)
        Buffer level = breach_level + (peak_equity × trailing_dd_buffer)

        Example (FTMO-style 10% trailing DD, 0.5% buffer):
            peak_equity  = $11,000
            breach_level = $9,900   (10% below peak)
            buffer_level = $9,955   (0.5% above breach)
            → if equity < $9,955: HALT
        """
        peak    = float(state.get('peak_equity', 0))
        current = float(state.get('current_equity', 0))

        if peak <= 0 or current <= 0:
            return True, 'equity_unknown'

        breach_level = peak * (1 - self.trailing_dd_limit_pct)
        buffer_level = breach_level + peak * self.trailing_dd_buffer

        if current <= breach_level:
            msg = (
                f"{self.account_name} DRAWDOWN BREACH: "
                f"equity ${current:.2f} <= breach ${breach_level:.2f} "
                f"(peak ${peak:.2f}, limit {self.trailing_dd_limit_pct*100:.1f}%). "
                f"Account locked — contact support."
            )
            logger.error(msg)
            return False, msg

        if current <= buffer_level:
            remaining = current - breach_level
            msg = (
                f"{self.account_name} DRAWDOWN BUFFER: "
                f"equity ${current:.2f} within ${remaining:.2f} of breach "
                f"${breach_level:.2f}. "
                f"No new entries until equity recovers."
            )
            logger.warning(msg)
            return False, msg

        # Informational: warn at 70% of drawdown consumed
        dd_consumed = (peak - current) / (peak * self.trailing_dd_limit_pct)
        if dd_consumed >= 0.70:
            logger.info(
                f"{self.account_name} DRAWDOWN NOTE: "
                f"{dd_consumed:.0%} of max drawdown consumed "
                f"(${peak-current:.2f} of ${peak*self.trailing_dd_limit_pct:.2f})"
            )

        return True, 'drawdown_ok'

    # ─────────────────────────────────────────────────────────────────────────
    # Guard 3: News blackout
    # ─────────────────────────────────────────────────────────────────────────

    # Hard-coded recurring high-impact events (UTC hour ranges).
    # Format: (month, day, utc_start, utc_end, description)
    # Update this list before each major event.
    _SCHEDULED_EVENTS: list[tuple] = [
        # ── Recurring monthly / quarterly events ─────────────────────────────
        # US Non-Farm Payrolls: first Friday of each month, 12:30 UTC
        # US CPI: ~12th of each month, 12:30 UTC
        # FOMC: 8× per year, decision at 18:00 UTC
        # RBI MPC: ~6× per year, decision at 10:00 IST = 04:30 UTC
        # These are populated dynamically — see _dynamic_blackout() below.
    ]

    # Manually-set one-off events: list of (date_str, utc_start_h, utc_end_h, label)
    # Add entries here before high-impact days; clear after.
    MANUAL_EVENTS: list[tuple] = [
        # Example: ('2026-06-05', 12, 14, 'US NFP June 2026')
    ]

    def check_news_blackout(self) -> tuple[bool, str]:
        """
        Block trading within ±news_blackout_minutes of any scheduled event.
        Checks:
          1. Manual one-off events (MANUAL_EVENTS list above)
          2. Dynamic detection via data/forex_news_monitor (if available)
        """
        now_utc = datetime.now(timezone.utc)

        # Manual events
        date_str = now_utc.strftime('%Y-%m-%d')
        h        = now_utc.hour
        m        = now_utc.minute
        now_min  = h * 60 + m
        buf      = self.news_blackout_minutes

        for event_date, start_h, end_h, label in self.MANUAL_EVENTS:
            if event_date != date_str:
                continue
            event_start_min = start_h * 60
            event_end_min   = end_h   * 60
            window_start    = event_start_min - buf
            window_end      = event_end_min   + buf
            if window_start <= now_min <= window_end:
                msg = (
                    f"{self.account_name} NEWS BLACKOUT: "
                    f"'{label}' at {start_h:02d}:00-{end_h:02d}:00 UTC "
                    f"(±{buf}min buffer). No new entries."
                )
                logger.info(msg)
                return False, msg

        # Dynamic detection via news monitor
        try:
            from data.forex_news_monitor import is_news_blackout
            if is_news_blackout():
                msg = (
                    f"{self.account_name} NEWS BLACKOUT: "
                    f"High-impact event detected by news monitor. "
                    f"No new entries."
                )
                logger.info(msg)
                return False, msg
        except Exception:
            pass   # monitor unavailable — fail open

        return True, 'no_news_blackout'

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience summary for Telegram alerts
    # ─────────────────────────────────────────────────────────────────────────

    def status_summary(self, state: dict) -> str:
        """Return a Telegram-ready multi-line risk status string."""
        starting = float(state.get('starting_balance', 1))
        current  = float(state.get('current_equity',   starting))
        peak     = float(state.get('peak_equity',       current))
        daily    = float(state.get('daily_pnl',         0))
        unreal   = float(state.get('unrealised_pnl',    0))
        total_loss = max(0, -(daily + unreal))

        daily_limit   = starting * self.daily_loss_limit_pct
        breach_level  = peak * (1 - self.trailing_dd_limit_pct)
        buffer_level  = breach_level + peak * self.trailing_dd_buffer
        dd_to_buffer  = current - buffer_level

        # Traffic-light symbols
        dl_pct  = total_loss / daily_limit if daily_limit else 0
        dl_icon = '🔴' if dl_pct >= 1.0 else '🟡' if dl_pct >= 0.75 else '🟢'
        dd_icon = '🔴' if current <= breach_level else '🟡' if current <= buffer_level else '🟢'

        return (
            f"<b>Risk Status — {self.account_name}</b>\n\n"
            f"{dl_icon} Daily loss  : ${total_loss:.2f} / ${daily_limit:.2f} "
            f"({dl_pct*100:.1f}% used)\n"
            f"{dd_icon} Drawdown    : ${peak-current:.2f} from peak ${peak:.2f}\n"
            f"   Breach @ ${breach_level:.2f} | Buffer @ ${buffer_level:.2f}\n"
            f"   Headroom: ${dd_to_buffer:.2f} to buffer\n"
            f"Equity now  : ${current:.2f}"
        )


# ── Pre-built instances for FTMO and GFT ────────────────────────────────────

def make_ftmo_controller() -> PropFirmRiskController:
    """FTMO Free Trial / Standard Challenge risk profile."""
    return PropFirmRiskController({
        'daily_loss_limit_pct'  : 0.03,    # 3% daily loss limit
        'trailing_dd_limit_pct' : 0.10,    # 10% max drawdown
        'trailing_dd_buffer_pct': 0.005,   # stop 0.5% before breach
        'news_blackout_minutes' : 30,
        'account_name'          : 'FTMO',
    })


def make_gft_controller() -> PropFirmRiskController:
    """GFT 2-Step GOAT risk profile."""
    return PropFirmRiskController({
        'daily_loss_limit_pct'  : 0.04,    # 4% daily loss limit
        'trailing_dd_limit_pct' : 0.10,    # 10% max drawdown
        'trailing_dd_buffer_pct': 0.005,
        'news_blackout_minutes' : 30,
        'account_name'          : 'GFT',
    })


def make_nse_futures_controller() -> PropFirmRiskController:
    """NSE Futures prop-firm profile (generic — configure per firm)."""
    return PropFirmRiskController({
        'daily_loss_limit_pct'  : 0.04,    # 4% daily loss limit
        'trailing_dd_limit_pct' : 0.10,
        'trailing_dd_buffer_pct': 0.005,
        'news_blackout_minutes' : 30,
        'account_name'          : 'NSE_Prop',
    })
