# forex_engine/accounts/mt5_session_manager.py
#
# CB6 Quantum — MT5 Session Manager
#
# Manages the lifecycle of a single MT5 terminal process:
#   - Launch terminal (if not already running)
#   - Connect Python MT5 library to the terminal via path
#   - Heartbeat thread (60s ping to detect connection loss)
#   - Auto-reconnect with back-off
#   - Clean shutdown
#
# Each engine (FTMO/GFT) creates its own MT5SessionManager instance,
# pointing at its own terminal path. There is ZERO cross-account sharing.
#
# Usage:
#   mgr = MT5SessionManager('FTMO_10K')
#   if mgr.connect():
#       # engine runs
#       mgr.start_heartbeat()
#   mgr.shutdown()

import os
import subprocess
import threading
import time
from typing import Optional

from utils.logger import logger
from forex_engine.accounts.account_registry import (
    get_account, get_terminal_path, get_credentials, is_paper
)

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False

_HEARTBEAT_SECS  = 60
_RECONNECT_WAITS = [5, 15, 30, 60, 120]   # back-off seconds


class MT5SessionManager:
    """
    Owns the MT5 terminal connection for one account.

    Parameters
    ----------
    account_id : str
        Must match a key in config/mt5_accounts.json (e.g. 'FTMO_10K')
    """

    def __init__(self, account_id: str):
        self.account_id = account_id
        self._account   = get_account(account_id)
        if not self._account:
            raise ValueError(f"Unknown account_id: {account_id!r}")

        self._terminal_path = get_terminal_path(account_id)
        self._credentials   = get_credentials(account_id)
        self._paper         = is_paper(account_id)

        self._connected      = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat  = threading.Event()
        self._last_heartbeat  = 0.0
        self._reconnect_count = 0

    # ── Terminal Process ────────────────────────────────────────────────────────

    def launch_terminal(self, wait_secs: int = 8) -> bool:
        """
        Launch the terminal64.exe process if a path is configured.
        Safe to call even if terminal is already running — MT5 de-dupes.

        Returns True if terminal was found and launched (or already running).
        """
        if not self._terminal_path:
            logger.warning(
                f"[{self.account_id}] No terminal path configured — "
                f"skipping launch. See C:\\CB6_MT5\\README_SETUP.md"
            )
            return False

        if not os.path.isfile(self._terminal_path):
            logger.error(
                f"[{self.account_id}] terminal64.exe not found at {self._terminal_path!r}. "
                f"Run Phase 1 setup first."
            )
            return False

        logger.info(
            f"[{self.account_id}] Launching terminal: {self._terminal_path}"
        )
        try:
            subprocess.Popen(
                [self._terminal_path, '/portable'],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            logger.info(
                f"[{self.account_id}] Terminal launched — waiting {wait_secs}s for login..."
            )
            time.sleep(wait_secs)
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] Terminal launch failed: {e}")
            return False

    # ── Connection ──────────────────────────────────────────────────────────────

    def connect(self, launch_if_missing: bool = True) -> bool:
        """
        Initialize the MT5 Python library connection to THIS account's terminal.

        If terminal_path is set, passes it to mt5.initialize() so this Python
        process connects to the correct isolated terminal — never to another
        account's terminal.

        Returns True on success.
        """
        if self._paper:
            logger.info(f"[{self.account_id}] Paper mode — MT5 connection skipped")
            self._connected = True
            return True

        if not _MT5_AVAILABLE:
            logger.warning(f"[{self.account_id}] MetaTrader5 package not installed")
            return False

        creds = self._credentials or {}
        login    = int(creds.get('login', 0))
        password = creds.get('password', '')
        server   = creds.get('server', '')

        if not login or not password or not server:
            logger.error(f"[{self.account_id}] Missing MT5 credentials")
            return False

        # Optionally launch terminal first
        if launch_if_missing and self._terminal_path:
            if not _is_terminal_running(self._terminal_path):
                self.launch_terminal()

        # ── The Critical Fix ────────────────────────────────────────────────────
        # Pass path= so mt5.initialize() connects to THIS account's terminal,
        # not whichever terminal happened to be open last.
        # Without path= both FTMO and GFT connect to the same terminal →
        # the account-switch triggers "Algo Trading OFF" on the other session.
        # ────────────────────────────────────────────────────────────────────────
        try:
            if self._terminal_path and os.path.isfile(self._terminal_path):
                ok = mt5.initialize(
                    path     = self._terminal_path,
                    login    = login,
                    password = password,
                    server   = server,
                )
            else:
                # Fallback — no path, system-default terminal
                logger.warning(
                    f"[{self.account_id}] No terminal path — using system-default. "
                    f"Algo Trading isolation NOT guaranteed."
                )
                ok = mt5.initialize(login=login, password=password, server=server)

            if not ok:
                err = mt5.last_error()
                logger.error(f"[{self.account_id}] mt5.initialize failed: {err}")
                return False

            info = mt5.account_info()
            if not info:
                logger.error(f"[{self.account_id}] mt5.account_info() returned None after connect")
                return False

            # ── Account contamination guard ─────────────────────────────────────
            expected_login = login
            actual_login   = info.login
            if actual_login != expected_login:
                logger.error(
                    f"[{self.account_id}] ACCOUNT MISMATCH — "
                    f"expected login={expected_login}, got login={actual_login}. "
                    f"Refusing to trade. Check terminal path configuration."
                )
                mt5.shutdown()
                return False

            self._connected = True
            self._last_heartbeat = time.time()
            logger.info(
                f"[{self.account_id}] Connected — login={actual_login} "
                f"balance=${info.balance:.2f} server={info.server}"
            )
            return True

        except Exception as e:
            logger.exception(f"[{self.account_id}] Connection error: {e}")
            return False

    def disconnect(self) -> None:
        """Shutdown MT5 connection for this account."""
        self._stop_heartbeat.set()
        self._connected = False
        if not self._paper and _MT5_AVAILABLE:
            try:
                mt5.shutdown()
                logger.info(f"[{self.account_id}] MT5 disconnected")
            except Exception as e:
                logger.warning(f"[{self.account_id}] MT5 shutdown error: {e}")

    def is_connected(self) -> bool:
        if self._paper:
            return True
        if not _MT5_AVAILABLE:
            return False
        try:
            return mt5.terminal_info() is not None
        except Exception:
            return False

    # ── Heartbeat ───────────────────────────────────────────────────────────────

    def start_heartbeat(self) -> None:
        """Start background thread that pings terminal every 60s and reconnects on loss."""
        if self._paper:
            return
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(
            target  = self._heartbeat_loop,
            daemon  = True,
            name    = f"HB_{self.account_id}",
        )
        self._heartbeat_thread.start()
        logger.info(f"[{self.account_id}] Heartbeat started (every {_HEARTBEAT_SECS}s)")

    def _heartbeat_loop(self) -> None:
        attempt = 0
        while not self._stop_heartbeat.wait(timeout=_HEARTBEAT_SECS):
            try:
                if self.is_connected():
                    info = mt5.account_info() if _MT5_AVAILABLE else None
                    if info:
                        self._last_heartbeat = time.time()
                        self._reconnect_count = 0
                        logger.debug(
                            f"[{self.account_id}] HB OK — "
                            f"equity=${info.equity:.2f} margin_free=${info.margin_free:.2f}"
                        )
                        continue

                # Connection lost
                wait = _RECONNECT_WAITS[min(attempt, len(_RECONNECT_WAITS) - 1)]
                logger.warning(
                    f"[{self.account_id}] Heartbeat lost — reconnecting in {wait}s "
                    f"(attempt {attempt + 1})"
                )
                time.sleep(wait)
                if self.connect(launch_if_missing=True):
                    logger.info(f"[{self.account_id}] Reconnected via heartbeat")
                    attempt = 0
                    self._reconnect_count += 1
                else:
                    attempt += 1
                    logger.error(
                        f"[{self.account_id}] Reconnect failed (attempt {attempt})"
                    )

            except Exception as e:
                logger.exception(
                    f"[{self.account_id}] Heartbeat error: {e}"
                )

    def heartbeat_age_secs(self) -> float:
        """Seconds since last successful heartbeat. 0 in paper mode."""
        if self._paper:
            return 0.0
        return time.time() - self._last_heartbeat

    def shutdown(self) -> None:
        """Stop heartbeat and disconnect cleanly."""
        self._stop_heartbeat.set()
        self.disconnect()

    # ── Account info helpers ────────────────────────────────────────────────────

    def get_equity(self) -> Optional[float]:
        if self._paper or not _MT5_AVAILABLE:
            return None
        try:
            info = mt5.account_info()
            return float(info.equity) if info else None
        except Exception as e:
            logger.error(f"[{self.account_id}] get_equity: {e}")
            return None

    def get_balance(self) -> Optional[float]:
        if self._paper or not _MT5_AVAILABLE:
            return None
        try:
            info = mt5.account_info()
            return float(info.balance) if info else None
        except Exception as e:
            logger.error(f"[{self.account_id}] get_balance: {e}")
            return None

    def get_account_info(self) -> Optional[dict]:
        if self._paper or not _MT5_AVAILABLE:
            return None
        try:
            info = mt5.account_info()
            if not info:
                return None
            return {
                'login'       : info.login,
                'balance'     : info.balance,
                'equity'      : info.equity,
                'margin'      : info.margin,
                'margin_free' : info.margin_free,
                'server'      : info.server,
                'currency'    : info.currency,
                'leverage'    : info.leverage,
            }
        except Exception as e:
            logger.error(f"[{self.account_id}] get_account_info: {e}")
            return None

    def get_status(self) -> dict:
        """Return a status dict suitable for dashboard display."""
        hb_age = self.heartbeat_age_secs()
        return {
            'account_id'   : self.account_id,
            'label'        : self._account.get('label', self.account_id),
            'paper'        : self._paper,
            'connected'    : self.is_connected(),
            'terminal_path': self._terminal_path or 'not configured',
            'terminal_ok'  : os.path.isfile(self._terminal_path) if self._terminal_path else False,
            'hb_age_secs'  : round(hb_age, 1),
            'hb_stale'     : hb_age > 180,          # 3 min = stale
            'reconnects'   : self._reconnect_count,
        }

    def __repr__(self) -> str:
        state = 'PAPER' if self._paper else ('LIVE' if self._connected else 'DISCONNECTED')
        return f"<MT5SessionManager {self.account_id} [{state}]>"


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _is_terminal_running(terminal_path: str) -> bool:
    """Heuristic: check if a terminal64.exe process with this path is running."""
    try:
        import psutil
        exe_name = os.path.basename(terminal_path).lower()
        for proc in psutil.process_iter(['name', 'exe']):
            try:
                if proc.info['name'].lower() == exe_name:
                    if terminal_path.lower() in (proc.info.get('exe') or '').lower():
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False
    except ImportError:
        # psutil not available — assume not running
        return False
