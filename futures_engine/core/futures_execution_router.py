"""
CB6 Futures Core — Execution Router
Broker-agnostic order routing layer.
Selects the registered connector and routes orders through it.
Signal generation has zero knowledge of which broker is attached.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from futures_engine.brokers.base_connector import (
    AccountState, FuturesBrokerConnector, OrderRequest, OrderResult,
    OrderStatus, OrderType,
)

logger = logging.getLogger("cb6.futures.execution_router")


class FuturesExecutionRouter:
    """
    Routes orders to the currently registered broker connector.
    In PAPER/BACKTEST modes the connector is None and orders are simulated.
    """

    def __init__(self, mode: str = "PAPER"):
        self._mode = mode           # PAPER | BACKTEST | MANUAL_MONITOR | SEMI_AUTO | LIVE_AUTO
        self._connector: Optional[FuturesBrokerConnector] = None
        self._paper_fill_count: int = 0
        self._order_log: list = []

    def register_connector(self, connector: FuturesBrokerConnector) -> None:
        if self._mode == "PAPER":
            logger.info("Paper mode — connector registered but not used for fills")
        self._connector = connector

    def set_mode(self, mode: str) -> None:
        allowed = {"OFF", "PAPER", "BACKTEST", "MANUAL_MONITOR", "SEMI_AUTO"}
        if mode not in allowed:
            raise ValueError(f"Mode '{mode}' not allowed. LIVE_AUTO is permanently disabled here.")
        self._mode = mode
        logger.info("ExecutionRouter mode → %s", mode)

    def submit(
        self,
        order: OrderRequest,
        paper_fill_price: Optional[float] = None,
    ) -> OrderResult:
        if self._mode in ("OFF", "BACKTEST", "MANUAL_MONITOR"):
            result = OrderResult(
                order_id=f"SIM_{self._paper_fill_count:04d}",
                client_id=order.client_id,
                symbol=order.symbol,
                direction=order.direction,
                contracts=order.contracts,
                fill_price=paper_fill_price,
                status=OrderStatus.FILLED if paper_fill_price else OrderStatus.PENDING,
                timestamp=datetime.now(timezone.utc),
                message=f"Simulated fill (mode={self._mode})",
            )
            self._paper_fill_count += 1
            self._log_order(order, result)
            return result

        if self._mode == "SEMI_AUTO":
            if not order.meta or not order.meta.get("approved"):
                return OrderResult(
                    order_id="PENDING_APPROVAL",
                    client_id=order.client_id,
                    symbol=order.symbol,
                    direction=order.direction,
                    contracts=order.contracts,
                    fill_price=None,
                    status=OrderStatus.PENDING,
                    timestamp=datetime.now(timezone.utc),
                    message="Awaiting manual approval (SEMI_AUTO mode)",
                )

        if self._mode == "PAPER":
            result = OrderResult(
                order_id=f"PAPER_{self._paper_fill_count:04d}",
                client_id=order.client_id,
                symbol=order.symbol,
                direction=order.direction,
                contracts=order.contracts,
                fill_price=paper_fill_price or order.limit_price,
                status=OrderStatus.FILLED,
                timestamp=datetime.now(timezone.utc),
                message="Paper fill",
            )
            self._paper_fill_count += 1
            self._log_order(order, result)
            return result

        # Should never reach here — LIVE_AUTO blocked at set_mode
        raise RuntimeError("LIVE_AUTO not enabled — use MFFConnector with Phase 6")

    def _log_order(self, order: OrderRequest, result: OrderResult) -> None:
        self._order_log.append({
            "timestamp": result.timestamp.isoformat(),
            "symbol": order.symbol,
            "direction": order.direction,
            "contracts": order.contracts,
            "order_type": order.order_type.value,
            "fill_price": result.fill_price,
            "status": result.status.value,
            "order_id": result.order_id,
        })

    def order_log(self) -> list:
        return list(self._order_log)
