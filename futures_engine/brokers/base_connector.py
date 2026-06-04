"""
CB6 Futures Core — Base Broker Connector
Abstract interface all broker connectors must implement.
No broker-specific logic inside signal generation or risk layers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    STOP   = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    PENDING   = "PENDING"
    FILLED    = "FILLED"
    PARTIAL   = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"


@dataclass
class OrderRequest:
    symbol: str
    contract: str          # e.g. "MESH25"
    direction: str         # "LONG" | "SHORT"
    order_type: OrderType
    contracts: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    client_id: str = ""
    meta: dict = None


@dataclass
class OrderResult:
    order_id: str
    client_id: str
    symbol: str
    direction: str
    contracts: int
    fill_price: Optional[float]
    status: OrderStatus
    timestamp: datetime
    message: str = ""


@dataclass
class Position:
    symbol: str
    contract: str
    direction: str
    contracts: int
    avg_entry: float
    unrealised_pnl: float
    open_since: datetime


@dataclass
class AccountState:
    account_id: str
    equity: float
    cash: float
    unrealised_pnl: float
    realised_pnl_today: float
    open_positions: List[Position]
    timestamp: datetime


class FuturesBrokerConnector(ABC):
    """All broker connectors implement this interface."""

    @property
    @abstractmethod
    def broker_name(self) -> str: ...

    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_account_state(self) -> AccountState: ...

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_open_positions(self) -> List[Position]: ...

    @abstractmethod
    def close_position(self, symbol: str, contracts: Optional[int] = None) -> OrderResult: ...
