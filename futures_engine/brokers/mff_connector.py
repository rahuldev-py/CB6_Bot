"""
CB6 Futures Core — MFF Broker Connector Placeholder
MyFundedFutures uses Rithmic/NinjaTrader for execution.
This stub will be wired to the live Rithmic API in Phase 6.

LIVE_AUTO IS DISABLED — connector is placeholder only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from futures_engine.brokers.base_connector import (
    AccountState, FuturesBrokerConnector, OrderRequest, OrderResult,
    OrderStatus, Position,
)

logger = logging.getLogger("cb6.futures.brokers.mff")

LIVE_AUTO_ENABLED = False  # NEVER change to True without explicit authorisation


class MFFConnector(FuturesBrokerConnector):
    """
    MyFundedFutures broker connector.
    Phase 6 placeholder — no live execution until enabled.
    """

    def __init__(
        self,
        account_id: str = "",
        username: str = "",
        password: str = "",
        host: str = "",
        port: int = 0,
        paper: bool = True,
    ):
        self._account_id = account_id
        self._username = username
        self._password = password
        self._host = host
        self._port = port
        self._paper = paper
        self._connected = False

        if not LIVE_AUTO_ENABLED:
            logger.info(
                "MFFConnector: LIVE_AUTO disabled — running in %s mode",
                "PAPER" if paper else "STUB"
            )

    @property
    def broker_name(self) -> str:
        return "MyFundedFutures (Rithmic)"

    def connect(self) -> bool:
        if not LIVE_AUTO_ENABLED:
            logger.info("MFF connect() called but LIVE_AUTO disabled — stub only")
            self._connected = False
            return False
        # Phase 6: implement Rithmic API handshake here
        raise NotImplementedError("MFF live connection not yet implemented — Phase 6")

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_account_state(self) -> AccountState:
        if not LIVE_AUTO_ENABLED:
            return AccountState(
                account_id=self._account_id or "MFF_STUB",
                equity=25000.0, cash=25000.0,
                unrealised_pnl=0.0, realised_pnl_today=0.0,
                open_positions=[],
                timestamp=datetime.now(timezone.utc),
            )
        raise NotImplementedError("Phase 6")

    def submit_order(self, order: OrderRequest) -> OrderResult:
        if not LIVE_AUTO_ENABLED:
            logger.warning("submit_order blocked — LIVE_AUTO disabled")
            return OrderResult(
                order_id="BLOCKED",
                client_id=order.client_id,
                symbol=order.symbol,
                direction=order.direction,
                contracts=order.contracts,
                fill_price=None,
                status=OrderStatus.REJECTED,
                timestamp=datetime.now(timezone.utc),
                message="LIVE_AUTO disabled — order blocked",
            )
        raise NotImplementedError("Phase 6")

    def cancel_order(self, order_id: str) -> bool:
        if not LIVE_AUTO_ENABLED:
            return False
        raise NotImplementedError("Phase 6")

    def get_open_positions(self) -> List[Position]:
        if not LIVE_AUTO_ENABLED:
            return []
        raise NotImplementedError("Phase 6")

    def close_position(self, symbol: str, contracts: Optional[int] = None) -> OrderResult:
        if not LIVE_AUTO_ENABLED:
            return OrderResult(
                order_id="BLOCKED", client_id="", symbol=symbol,
                direction="FLAT", contracts=0, fill_price=None,
                status=OrderStatus.REJECTED,
                timestamp=datetime.now(timezone.utc),
                message="LIVE_AUTO disabled",
            )
        raise NotImplementedError("Phase 6")
