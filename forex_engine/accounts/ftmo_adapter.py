# forex_engine/accounts/ftmo_adapter.py
#
# CB6 Quantum — FTMO Account Adapter
#
# Wraps MT5Connector with FTMO-specific terminal path + credentials.
# Drop-in replacement for the generic MT5Connector in forex_worker.py.
#
# Usage:
#   from forex_engine.accounts.ftmo_adapter import build_ftmo_connector
#   connector = build_ftmo_connector(paper=False)

from typing import Optional
from utils.logger import logger
from forex_engine.accounts.account_registry import (
    get_terminal_path, get_credentials, is_paper
)


def build_ftmo_connector(paper: Optional[bool] = None):
    """
    Build an MT5Connector pre-configured for the FTMO_10K account.

    - Loads terminal path from account registry (env var or config fallback)
    - Loads credentials from .env via account registry
    - Passes path= to mt5.initialize() → connects to FTMO terminal ONLY

    Returns an MT5Connector instance. Never raises — returns paper=True fallback
    on credential/path errors so the engine can still start in paper mode.
    """
    from forex_engine.mt5.mt5_connector import MT5Connector

    account_id = 'FTMO_10K'

    if paper is None:
        paper = is_paper(account_id)

    if paper:
        logger.info(f"[{account_id}] Paper mode — building paper MT5Connector")
        return MT5Connector(paper=True)

    terminal_path = get_terminal_path(account_id)
    credentials   = get_credentials(account_id)

    if not credentials:
        logger.error(
            f"[{account_id}] Cannot build LIVE connector — credentials missing. "
            f"Falling back to paper mode."
        )
        return MT5Connector(paper=True)

    logger.info(
        f"[{account_id}] Building LIVE connector — terminal={terminal_path!r} "
        f"login={credentials['login']} server={credentials['server']}"
    )
    return MT5Connector(
        paper         = False,
        credentials   = credentials,
        terminal_path = terminal_path,
    )
