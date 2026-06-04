"""
Abstract base classes (interfaces) for market data providers.

These interfaces define the contract that any market data provider
(TrueData, Fyers, Yahoo, etc.) must implement.  CB6 Quantum core code
should depend only on these interfaces, not on concrete implementations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Optional

from provider.truedata.models import (
    GreeksSnapshot,
    MarketBar,
    MarketTick,
    OptionChainRow,
    ProviderHealth,
    SymbolInfo,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Live data
# ---------------------------------------------------------------------------


class IMarketDataProvider(ABC):
    """
    Interface for real-time (streaming) market data providers.

    Implementors manage a persistent connection (WebSocket, etc.) and
    deliver ticks/bars via callbacks or an internal queue.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish the data feed connection."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close the data feed connection gracefully."""
        ...

    @abstractmethod
    def subscribe(self, symbols: list[str]) -> None:
        """
        Subscribe to real-time updates for the given symbols.

        Parameters
        ----------
        symbols:
            List of provider-specific symbol strings.
        """
        ...

    @abstractmethod
    def unsubscribe(self, symbols: list[str]) -> None:
        """
        Remove subscriptions for the given symbols.

        Parameters
        ----------
        symbols:
            List of symbol strings to unsubscribe.
        """
        ...

    @abstractmethod
    def get_health(self) -> ProviderHealth:
        """
        Return a health snapshot of the current connection.

        Returns
        -------
        ProviderHealth
        """
        ...


# ---------------------------------------------------------------------------
# Historical data
# ---------------------------------------------------------------------------


class IHistoricalDataProvider(ABC):
    """
    Interface for historical OHLCV and tick data retrieval.
    """

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        interval: str,
        from_dt: datetime | date,
        to_dt: datetime | date,
    ) -> list[MarketBar]:
        """
        Fetch OHLCV candles for a symbol over a time range.

        Parameters
        ----------
        symbol:
            Provider-specific symbol string.
        interval:
            Bar interval, e.g. ``"1min"``, ``"5min"``, ``"1day"``.
        from_dt:
            Start of the range (inclusive).
        to_dt:
            End of the range (inclusive).

        Returns
        -------
        list[MarketBar]
            Sorted, validated list of bars.
        """
        ...

    @abstractmethod
    def get_ticks(
        self,
        symbol: str,
        date_: date | datetime | str,
    ) -> list[MarketTick]:
        """
        Fetch all tick records for a symbol on a trading day.

        Parameters
        ----------
        symbol:
            Provider-specific symbol string.
        date_:
            Trading date.

        Returns
        -------
        list[MarketTick]
        """
        ...


# ---------------------------------------------------------------------------
# Option chain
# ---------------------------------------------------------------------------


class IOptionChainProvider(ABC):
    """
    Interface for option chain data providers.
    """

    @abstractmethod
    def get_chain(
        self,
        underlying: str,
        expiry: Optional[str] = None,
    ) -> list[OptionChainRow]:
        """
        Fetch the option chain for an underlying.

        Parameters
        ----------
        underlying:
            Index name, e.g. ``"NIFTY"``.
        expiry:
            Optional expiry filter (``"YYYY-MM-DD"``).  None = nearest.

        Returns
        -------
        list[OptionChainRow]
        """
        ...

    @abstractmethod
    def get_atm_chain(
        self,
        underlying: str,
        spot: float,
        n_strikes: int,
        expiry: Optional[str] = None,
    ) -> list[OptionChainRow]:
        """
        Return the option chain filtered to ATM ± n_strikes.

        Parameters
        ----------
        underlying:
            Index name.
        spot:
            Current spot price for ATM detection.
        n_strikes:
            Number of strikes on each side of ATM.
        expiry:
            Optional expiry filter.

        Returns
        -------
        list[OptionChainRow]
        """
        ...


# ---------------------------------------------------------------------------
# Greeks
# ---------------------------------------------------------------------------


class IGreeksProvider(ABC):
    """
    Interface for option Greeks data providers.
    """

    @abstractmethod
    def get_greeks(self, symbol: str) -> GreeksSnapshot:
        """
        Fetch Greeks for a single option symbol.

        Parameters
        ----------
        symbol:
            Full option symbol string.

        Returns
        -------
        GreeksSnapshot
        """
        ...

    @abstractmethod
    def get_chain_greeks(
        self,
        underlying: str,
        strikes: list[str],
    ) -> list[GreeksSnapshot]:
        """
        Fetch Greeks for multiple option symbols.

        Parameters
        ----------
        underlying:
            Index name (for logging / organisation).
        strikes:
            List of full option symbol strings.

        Returns
        -------
        list[GreeksSnapshot]
        """
        ...


# ---------------------------------------------------------------------------
# Symbol master
# ---------------------------------------------------------------------------


class ISymbolMasterProvider(ABC):
    """
    Interface for symbol master / instrument catalogue providers.
    """

    @abstractmethod
    def get_symbols(self) -> list[SymbolInfo]:
        """
        Return all available symbols.

        Returns
        -------
        list[SymbolInfo]
        """
        ...

    @abstractmethod
    def find_symbol(self, name: str) -> Optional[SymbolInfo]:
        """
        Look up a symbol by name (case-insensitive).

        Parameters
        ----------
        name:
            Symbol string to search for.

        Returns
        -------
        SymbolInfo or None
        """
        ...
