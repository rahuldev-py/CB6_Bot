"""
CB6 Futures Core — MFF Flex Account Connector
Thin adapter that wires MFFConnector to the MFF Flex 25K state machine.
Passes account data from broker into MFFFlexState.
LIVE_AUTO permanently disabled — placeholder for Phase 6.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from futures_engine.brokers.mff_connector import MFFConnector
from futures_engine.mff_flex_25k.mff_flex_state import MFFFlexState

logger = logging.getLogger("cb6.futures.mff_flex.connector")

LIVE_AUTO_ENABLED = False  # Do NOT change — Phase 6 only


class MFFFlexConnector:
    """
    Bridges the MFF broker API with the local MFF Flex state.
    In PAPER/BACKTEST modes the broker is never called.
    """

    def __init__(
        self,
        state: MFFFlexState,
        account_id: str = "",
        username: str = "",
        password: str = "",
        paper: bool = True,
    ):
        self._state = state
        self._broker = MFFConnector(
            account_id=account_id,
            username=username,
            password=password,
            paper=paper,
        )
        self._paper = paper

    def sync_account(self) -> dict:
        """
        Sync equity from broker → local state.
        In PAPER mode returns local state without calling broker.
        """
        if self._paper or not LIVE_AUTO_ENABLED:
            return self._state.snapshot()

        # Phase 6: pull real account state
        account = self._broker.get_account_state()
        logger.info(
            "Broker sync: equity=%.2f unrealised=%.2f",
            account.equity, account.unrealised_pnl
        )
        return {
            "equity": account.equity,
            "unrealised_pnl": account.unrealised_pnl,
            "realised_pnl_today": account.realised_pnl_today,
            "open_positions": len(account.open_positions),
            "timestamp": account.timestamp.isoformat(),
        }

    def is_live(self) -> bool:
        return LIVE_AUTO_ENABLED and not self._paper

    def broker_name(self) -> str:
        return self._broker.broker_name
