"""
Pydantic v2 data models for TrueData provider.

All timestamps are stored as timezone-aware datetime objects in IST
(Asia/Kolkata).  The ``provider`` field is set to ``"truedata"`` by
default so downstream code can identify the data source.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


class MarketTick(BaseModel):
    """A single real-time tick received from TrueData WebSocket."""

    symbol: str = Field(..., description="TrueData symbol string, e.g. 'NIFTY-I'")
    exchange: str = Field(default="NSE", description="Exchange identifier")
    timestamp: datetime = Field(..., description="Tick timestamp in IST")
    ltp: float = Field(..., description="Last traded price")
    open: Optional[float] = Field(default=None, description="Day open price")
    high: Optional[float] = Field(default=None, description="Day high price")
    low: Optional[float] = Field(default=None, description="Day low price")
    close: Optional[float] = Field(default=None, description="Previous close price")
    volume: Optional[int] = Field(default=None, description="Cumulative day volume")
    oi: Optional[int] = Field(default=None, description="Open interest")
    bid: Optional[float] = Field(default=None, description="Best bid price")
    ask: Optional[float] = Field(default=None, description="Best ask price")
    bid_qty: Optional[int] = Field(default=None, description="Bid quantity")
    ask_qty: Optional[int] = Field(default=None, description="Ask quantity")
    seq: Optional[int] = Field(default=None, description="Sequence number for gap detection")
    provider: Literal["truedata"] = Field(default="truedata")
    raw: Optional[dict[str, Any]] = Field(default=None, description="Raw message dict")

    model_config = {"arbitrary_types_allowed": True}


class MarketBar(BaseModel):
    """An OHLCV bar (candle) from TrueData historical or live bar feed."""

    symbol: str = Field(..., description="TrueData symbol string")
    exchange: str = Field(default="NSE")
    timestamp: datetime = Field(..., description="Bar open time in IST")
    bar_time: Optional[datetime] = Field(default=None, description="Bar time from API (may equal timestamp)")
    interval: str = Field(..., description="Bar interval, e.g. '1min', '5min', '1day'")
    open: float = Field(..., description="Bar open price")
    high: float = Field(..., description="Bar high price")
    low: float = Field(..., description="Bar low price")
    close: float = Field(..., description="Bar close price")
    volume: int = Field(default=0, description="Bar volume")
    oi: Optional[int] = Field(default=None, description="Open interest at bar close")
    provider: Literal["truedata"] = Field(default="truedata")

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


class OptionChainRow(BaseModel):
    """One row of an option chain snapshot."""

    symbol: str = Field(..., description="Full option symbol, e.g. 'NIFTY24000CE'")
    underlying: str = Field(..., description="Underlying index, e.g. 'NIFTY'")
    strike: float = Field(..., description="Strike price")
    option_type: Literal["CE", "PE"] = Field(..., description="Call or Put")
    expiry: str = Field(..., description="Expiry date string, e.g. '2026-05-29'")
    ltp: Optional[float] = Field(default=None, description="Last traded price")
    bid: Optional[float] = Field(default=None)
    ask: Optional[float] = Field(default=None)
    oi: Optional[int] = Field(default=None, description="Open interest")
    oi_change: Optional[int] = Field(default=None, description="Change in OI from previous session")
    volume: Optional[int] = Field(default=None)
    iv: Optional[float] = Field(default=None, description="Implied volatility (percentage)")
    delta: Optional[float] = Field(default=None)
    gamma: Optional[float] = Field(default=None)
    theta: Optional[float] = Field(default=None)
    vega: Optional[float] = Field(default=None)
    rho: Optional[float] = Field(default=None)
    provider: Literal["truedata"] = Field(default="truedata")
    timestamp: Optional[datetime] = Field(default=None, description="Snapshot time in IST")

    model_config = {"arbitrary_types_allowed": True}


class GreeksSnapshot(BaseModel):
    """Greeks for a single option contract."""

    symbol: str = Field(..., description="Full option symbol")
    underlying: str = Field(..., description="Underlying index")
    strike: float = Field(...)
    option_type: Literal["CE", "PE"] = Field(...)
    expiry: str = Field(...)
    iv: Optional[float] = Field(default=None, description="Implied volatility")
    delta: Optional[float] = Field(default=None)
    gamma: Optional[float] = Field(default=None)
    theta: Optional[float] = Field(default=None)
    vega: Optional[float] = Field(default=None)
    rho: Optional[float] = Field(default=None)
    timestamp: datetime = Field(..., description="Snapshot time in IST")
    provider: Literal["truedata"] = Field(default="truedata")

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Symbol master
# ---------------------------------------------------------------------------


class SymbolInfo(BaseModel):
    """Metadata for a tradeable instrument from TrueData symbol master."""

    symbol: str = Field(..., description="TrueData symbol string")
    exchange: str = Field(default="NSE")
    segment: Optional[str] = Field(default=None, description="Segment, e.g. 'nse_fo', 'nse_cm'")
    lot_size: Optional[int] = Field(default=None)
    tick_size: Optional[float] = Field(default=None)
    expiry: Optional[str] = Field(default=None, description="Expiry date string or None for spot")
    strike: Optional[float] = Field(default=None)
    option_type: Optional[Literal["CE", "PE"]] = Field(default=None)
    underlying: Optional[str] = Field(default=None)
    is_index: bool = Field(default=False)
    is_futures: bool = Field(default=False)
    is_options: bool = Field(default=False)

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Health and diagnostics
# ---------------------------------------------------------------------------


class ProviderHealth(BaseModel):
    """Real-time health snapshot of a market data provider connection."""

    provider: str = Field(..., description="Provider name, e.g. 'truedata'")
    connected: bool = Field(default=False)
    last_heartbeat: Optional[datetime] = Field(default=None, description="Last heartbeat time in IST")
    last_tick_time: Optional[datetime] = Field(default=None, description="Last tick received in IST")
    reconnect_count: int = Field(default=0, description="Total reconnect attempts since start")
    error_count: int = Field(default=0, description="Total errors encountered")
    latency_ms: Optional[float] = Field(default=None, description="Most recent tick latency in ms")
    status: str = Field(default="disconnected", description="Human-readable status string")

    model_config = {"arbitrary_types_allowed": True}


class FeedLatencyStats(BaseModel):
    """Latency and quality statistics for a single symbol's tick feed."""

    symbol: str = Field(...)
    count: int = Field(default=0, description="Total ticks received")
    mean_ms: float = Field(default=0.0, description="Mean latency in milliseconds")
    min_ms: float = Field(default=0.0)
    max_ms: float = Field(default=0.0)
    p50_ms: float = Field(default=0.0, description="Median latency")
    p95_ms: float = Field(default=0.0, description="95th percentile latency")
    p99_ms: float = Field(default=0.0, description="99th percentile latency")
    missing_ticks: int = Field(default=0, description="Detected sequence gaps")
    duplicate_ticks: int = Field(default=0, description="Detected duplicate sequences")

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Trial / testing
# ---------------------------------------------------------------------------


class TrialResult(BaseModel):
    """Result of a single trial test module."""

    test_name: str = Field(..., description="Human-readable test name")
    passed: bool = Field(default=False)
    score: int = Field(default=0, description="Score contribution (0 to max for this test)")
    details: dict[str, Any] = Field(default_factory=dict, description="Structured test metrics")
    errors: list[str] = Field(default_factory=list, description="Error messages encountered")
    started_at: Optional[datetime] = Field(default=None)
    ended_at: Optional[datetime] = Field(default=None)
    duration_s: float = Field(default=0.0, description="Test duration in seconds")

    model_config = {"arbitrary_types_allowed": True}
