"""
CB6 Quantum market data layer.

Provides:
- Abstract interfaces for all market data provider types
- TrueData concrete implementations
- Data normalizer
- Event bus
- Candle builder
- Tick store
- Health monitor

Usage::

    from market_data import (
        IMarketDataProvider,
        IHistoricalDataProvider,
        IOptionChainProvider,
        IGreeksProvider,
        ISymbolMasterProvider,
        EventBus,
        EventType,
        CandleBuilder,
        TickStore,
        HealthMonitor,
        normalize_tick,
        normalize_bar,
        normalize_option_chain_row,
        normalize_timestamp,
    )
"""

from .candle_builder import CandleBuilder, SUPPORTED_INTERVALS
from .event_bus import EventBus, EventType
from .health_monitor import HealthMonitor
from .interfaces import (
    IGreeksProvider,
    IHistoricalDataProvider,
    IMarketDataProvider,
    IOptionChainProvider,
    ISymbolMasterProvider,
)
from .normalizer import (
    normalize_bar,
    normalize_option_chain_row,
    normalize_symbol_info,
    normalize_tick,
    normalize_timestamp,
)
from .tick_store import TickStore

__all__ = [
    # Interfaces
    "IMarketDataProvider",
    "IHistoricalDataProvider",
    "IOptionChainProvider",
    "IGreeksProvider",
    "ISymbolMasterProvider",
    # Utilities
    "EventBus",
    "EventType",
    "CandleBuilder",
    "SUPPORTED_INTERVALS",
    "TickStore",
    "HealthMonitor",
    # Normalizer
    "normalize_tick",
    "normalize_bar",
    "normalize_option_chain_row",
    "normalize_symbol_info",
    "normalize_timestamp",
]
