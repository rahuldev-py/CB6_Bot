"""
CB6 Futures Core — MFF Flex Manual Trade Bridge
Inherits from core ManualTradeBridge with MFF-specific storage path
and symbol validation.
"""
from __future__ import annotations

import logging
from typing import Optional

from futures_engine.core.futures_manual_bridge import ManualTradeBridge, ManualTrade
from futures_engine.core.futures_symbol_registry import get_symbol, assert_mff_permitted
from futures_engine.mff_flex_25k.mff_flex_state import MFFFlexState

logger = logging.getLogger("cb6.futures.mff_flex.manual_bridge")


class MFFFlexManualBridge(ManualTradeBridge):
    """
    MFF Flex specialization of the manual bridge.
    Validates that logged symbols are MFF-permitted.
    Stores data under data/futures/mff_flex_25k/manual_trades/.
    """

    def __init__(self, state: MFFFlexState):
        super().__init__(storage_dir="data/futures/mff_flex_25k/manual_trades")
        self._state = state

    def log_entry(
        self,
        symbol: str,
        contract: str,
        direction: str,
        entry_price: float,
        contracts: int,
        point_value: Optional[float] = None,
        source: str = "MANUAL",
        notes: str = "",
    ) -> ManualTrade:
        # Validate symbol is MFF-permitted
        try:
            assert_mff_permitted(symbol)
        except ValueError as e:
            logger.error("MFF symbol validation: %s", e)
            raise

        sym_info = get_symbol(symbol)
        pv = point_value or sym_info.point_value

        trade = super().log_entry(
            symbol=symbol,
            contract=contract,
            direction=direction,
            entry_price=entry_price,
            contracts=contracts,
            point_value=pv,
            source=source,
            notes=notes,
        )

        # Record into MFF state for risk tracking
        if source != "MANUAL":
            pass  # CB6 trades recorded by runner; manual trades logged separately

        return trade

    def log_exit(
        self,
        trade_id: str,
        exit_price: float,
        notes: str = "",
    ) -> Optional[ManualTrade]:
        trade = super().log_exit(trade_id, exit_price, notes)
        if trade and trade.pnl is not None:
            # Update state PnL for manual trades too
            self._state.record_trade(trade.pnl)
            logger.info(
                "Manual trade %s closed: pnl=$%.2f | state equity=$%.2f",
                trade_id, trade.pnl, self._state.current_equity
            )
        return trade
