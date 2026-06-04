from typing import Optional

from utils.logger import logger
from forex_engine.accounts.account_registry import (
    get_credentials,
    get_terminal_path,
    is_paper,
)


def build_gft_1k_instant_connector(paper: Optional[bool] = None):
    from forex_engine.mt5.mt5_connector import MT5Connector

    account_id = "GFT_1K_INSTANT"

    if paper is None:
        paper = is_paper(account_id)

    if paper:
        logger.info(f"[{account_id}] Paper mode - building paper MT5Connector")
        return MT5Connector(paper=True)

    terminal_path = get_terminal_path(account_id)
    credentials = get_credentials(account_id)

    if not terminal_path:
        _send_telegram_alert(
            "wrong_account_server_block",
            f"[{account_id}] live startup blocked: dedicated terminal path missing",
        )
        raise RuntimeError(
            f"[{account_id}] dedicated terminal path is required for live startup"
        )
    if not credentials:
        _send_telegram_alert(
            "wrong_account_server_block",
            f"[{account_id}] live startup blocked: credentials missing",
        )
        raise RuntimeError(f"[{account_id}] credentials missing")

    symbol_overrides = {
        "XAGUSD": "XAGUSD.x",
        "XAUUSD": "XAUUSD.x",
        "USOIL.cash": "WTI.x",
    }

    logger.info(
        f"[{account_id}] Building LIVE connector - terminal={terminal_path!r} "
        f"login={credentials['login']} server={credentials['server']}"
    )
    connector = MT5Connector(
        paper=False,
        credentials=credentials,
        terminal_path=terminal_path,
        symbol_overrides=symbol_overrides,
    )
    _validate_connected_server(account_id, credentials)
    _send_telegram_alert(
        "mt5_connected",
        f"[{account_id}] MT5 connected login={credentials['login']} server={credentials['server']}",
    )
    return connector


def _send_telegram_alert(alert_type: str, message: str) -> None:
    try:
        from forex_engine.gft_1k_instant.telegram_bot import send_alert
        send_alert(alert_type, message)
    except Exception:
        pass


def _validate_connected_server(account_id: str, credentials: dict) -> None:
    try:
        import MetaTrader5 as mt5
        import time as _time
    except ImportError:
        return

    expected_server = str(credentials.get("server", "") or "")
    # Retry up to 3 times: terminal may still be authenticating after initialize()
    connected_server = ""
    for attempt in range(3):
        info = mt5.account_info()
        if info:
            connected_server = str(getattr(info, "server", "") or "")
            if connected_server == expected_server or not connected_server:
                return
        if attempt < 2:
            _time.sleep(4)

    if not connected_server:
        return  # terminal hasn't reported server yet — let it trade; MT5Connector login guard already passed

    mt5.shutdown()
    _send_telegram_alert(
        "wrong_account_server_block",
        f"[{account_id}] SERVER MISMATCH: requested server={expected_server}, connected server={connected_server}",
    )
    raise RuntimeError(
        f"[{account_id}] SERVER MISMATCH: requested server={expected_server}, "
        f"connected server={connected_server}"
    )
