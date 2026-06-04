"""
CB6 Futures Core — Rithmic Connector Placeholder
Phase 6 / future broker integration.
LIVE_AUTO disabled.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from futures_engine.brokers.base_connector import (
    AccountState, FuturesBrokerConnector, OrderRequest, OrderResult, Position,
)

logger = logging.getLogger("cb6.futures.brokers.rithmic")
LIVE_AUTO_ENABLED = False


class RithmicConnector(FuturesBrokerConnector):
    """Rithmic R|API+ connector — Phase 6 placeholder."""

    def __init__(self, system_name: str = "", username: str = "", password: str = ""):
        self._system_name = system_name
        self._username = username
        self._password = password
        self._connected = False

    @property
    def broker_name(self) -> str:
        return "Rithmic R|API+"

    def connect(self) -> bool:
        raise NotImplementedError("Rithmic connector — Phase 6")

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_account_state(self) -> AccountState:
        raise NotImplementedError("Phase 6")

    def submit_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError("Phase 6")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("Phase 6")

    def get_open_positions(self) -> List[Position]:
        raise NotImplementedError("Phase 6")

    def close_position(self, symbol: str, contracts: Optional[int] = None) -> OrderResult:
        raise NotImplementedError("Phase 6")
