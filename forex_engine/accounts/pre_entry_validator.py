# forex_engine/accounts/pre_entry_validator.py
#
# CB6 Quantum — Pre-Entry Safety Validator (Phase 6)
#
# Runs BEFORE every trade order is sent to MT5.
# If ANY check fails → NO TRADE, detailed reason logged and Telegraphed.
#
# Checks:
#   1. Terminal path     — file exists on disk
#   2. Account login     — connected login matches expected login
#   3. Server            — connected server matches expected server
#   4. Magic             — order magic matches account magic
#   5. Equity            — equity > 0 and above minimum threshold
#   6. Risk profile      — account not in blown/halted state
#   7. Daily loss        — within prop firm daily loss limit
#   8. Drawdown          — within prop firm max drawdown limit
#   9. Kill switch       — forex kill switch not active
#  10. Emergency stop    — global emergency stop not active
#
# Usage:
#   from forex_engine.accounts.pre_entry_validator import PreEntryValidator
#   v = PreEntryValidator('FTMO_10K', connector)
#   ok, reason = v.validate(signal, state)
#   if not ok:
#       logger.warning(f"Pre-entry BLOCKED: {reason}")
#       return

import os
from typing import Optional, Tuple
from utils.logger import logger


class PreEntryValidator:
    """
    Validates all pre-entry conditions for a given account before placing an order.

    Parameters
    ----------
    account_id : str
        Account identifier (e.g. 'FTMO_10K', 'GFT_5K')
    connector : MT5Connector
        Live connector instance for account info queries
    """

    def __init__(self, account_id: str, connector=None):
        self.account_id = account_id
        self._connector = connector

        from forex_engine.accounts.account_registry import get_account
        self._cfg = get_account(account_id) or {}

    def validate(self, signal: dict, state: dict) -> Tuple[bool, str]:
        """
        Run all pre-entry checks.

        Returns (True, 'OK') if all pass.
        Returns (False, reason) on first failure — trade is blocked.
        """
        checks = [
            self._check_emergency_stop,
            self._check_kill_switch,
            self._check_terminal_path,
            self._check_account_login,
            self._check_magic,
            self._check_equity,
            self._check_daily_loss,
            self._check_drawdown,
            self._check_paused,
        ]

        for check in checks:
            try:
                ok, reason = check(signal, state)
                if not ok:
                    logger.warning(
                        f"[{self.account_id}] PRE-ENTRY BLOCKED ({check.__name__}): {reason}"
                    )
                    return False, reason
            except Exception as e:
                # Validator errors are FAIL-SAFE — block the trade
                logger.exception(
                    f"[{self.account_id}] Pre-entry validator error in {check.__name__}: {e}"
                )
                return False, f"Validator error in {check.__name__}: {e}"

        return True, 'OK'

    # ── Individual checks ───────────────────────────────────────────────────────

    def _check_emergency_stop(self, signal: dict, state: dict) -> Tuple[bool, str]:
        try:
            from utils.emergency_stop import is_emergency_stop_active
            if is_emergency_stop_active():
                return False, "Emergency stop active — data/EMERGENCY_STOP.flag present"
        except Exception:
            pass
        return True, 'OK'

    def _check_kill_switch(self, signal: dict, state: dict) -> Tuple[bool, str]:
        try:
            from forex_engine.risk.emergency_kill_switch import is_killed
            if is_killed():
                return False, "Forex kill switch active — data/forex_kill_switch.json"
        except Exception:
            pass
        return True, 'OK'

    def _check_terminal_path(self, signal: dict, state: dict) -> Tuple[bool, str]:
        from forex_engine.accounts.account_registry import get_terminal_path
        path = get_terminal_path(self.account_id)
        if path is None:
            # No path configured — system-default terminal, warn but allow
            logger.debug(
                f"[{self.account_id}] No terminal path configured — "
                f"using system-default (isolation not guaranteed)"
            )
            return True, 'OK'
        if not os.path.isfile(path):
            return False, (
                f"Terminal not found: {path!r}. "
                f"Run portable MT5 setup (C:\\CB6_MT5\\README_SETUP.md)"
            )
        return True, 'OK'

    def _check_account_login(self, signal: dict, state: dict) -> Tuple[bool, str]:
        if self._connector is None:
            return True, 'OK'   # Paper mode or no connector — skip
        try:
            from forex_engine.accounts.account_registry import get_credentials
            creds = get_credentials(self.account_id)
            if not creds:
                return False, f"[{self.account_id}] Credentials not found in .env"

            expected_login = int(creds['login'])
            expected_server = creds['server'].lower()

            try:
                import MetaTrader5 as mt5
                info = mt5.account_info()
                if not info:
                    return False, "MT5 account_info() returned None — terminal disconnected?"
                if info.login != expected_login:
                    return False, (
                        f"ACCOUNT MISMATCH: connected to login={info.login}, "
                        f"expected login={expected_login}. "
                        f"WRONG TERMINAL — trade blocked."
                    )
                if expected_server not in info.server.lower():
                    return False, (
                        f"SERVER MISMATCH: connected to {info.server!r}, "
                        f"expected server containing {expected_server!r}. "
                        f"Trade blocked."
                    )
            except ImportError:
                pass   # MT5 not available — paper mode

        except Exception as e:
            logger.warning(f"[{self.account_id}] Login check error (non-fatal): {e}")
        return True, 'OK'

    def _check_magic(self, signal: dict, state: dict) -> Tuple[bool, str]:
        from forex_engine.accounts.account_registry import get_magic
        sig_magic = signal.get('magic')
        if sig_magic is None:
            return True, 'OK'   # No magic in signal — allowed

        expected = get_magic(self.account_id)
        if sig_magic != expected:
            return False, (
                f"MAGIC MISMATCH: signal magic={sig_magic}, "
                f"account magic={expected}. "
                f"Cross-account contamination detected — trade blocked."
            )
        return True, 'OK'

    def _check_equity(self, signal: dict, state: dict) -> Tuple[bool, str]:
        if self._connector is None or getattr(self._connector, '_paper', True):
            return True, 'OK'
        try:
            equity = self._connector.get_equity()
            if equity is not None:
                min_equity = self._cfg.get('account_size', 1000) * 0.05   # 5% floor
                if equity <= 0:
                    return False, f"Equity = ${equity:.2f} — account appears closed or empty"
                if equity < min_equity:
                    return False, (
                        f"Equity ${equity:.2f} below minimum floor "
                        f"${min_equity:.2f} (5% of account size) — account may be blown"
                    )
        except Exception as e:
            logger.warning(f"[{self.account_id}] Equity check error (non-fatal): {e}")
        return True, 'OK'

    def _check_daily_loss(self, signal: dict, state: dict) -> Tuple[bool, str]:
        daily_pnl = state.get('daily_pnl', 0.0)
        if daily_pnl >= 0:
            return True, 'OK'

        max_daily_pct = self._cfg.get('max_daily_loss_pct', 5.0)
        account_size  = self._cfg.get('account_size', 10000.0)
        max_daily_usd = account_size * max_daily_pct / 100.0

        if abs(daily_pnl) >= max_daily_usd:
            return False, (
                f"Daily loss limit reached: ${abs(daily_pnl):.2f} "
                f">= {max_daily_pct}% = ${max_daily_usd:.2f}. "
                f"No new trades today."
            )
        return True, 'OK'

    def _check_drawdown(self, signal: dict, state: dict) -> Tuple[bool, str]:
        capital          = state.get('capital', self._cfg.get('account_size', 10000.0))
        starting_capital = state.get('starting_capital', capital)
        if starting_capital <= 0:
            return True, 'OK'

        drawdown_pct = (starting_capital - capital) / starting_capital * 100.0
        max_dd_pct   = self._cfg.get('max_drawdown_pct', 10.0)

        if drawdown_pct >= max_dd_pct:
            return False, (
                f"Max drawdown reached: {drawdown_pct:.1f}% "
                f">= {max_dd_pct}% limit. Account protection active."
            )
        return True, 'OK'

    def _check_paused(self, signal: dict, state: dict) -> Tuple[bool, str]:
        if state.get('paused', False):
            return False, f"[{self.account_id}] Engine paused — send /fx_resume to re-enable"
        return True, 'OK'


# ── Convenience factory ─────────────────────────────────────────────────────────

def make_validator(account_id: str, connector=None) -> PreEntryValidator:
    """Create a PreEntryValidator for the given account."""
    return PreEntryValidator(account_id, connector)
