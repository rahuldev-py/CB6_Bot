# core/schemas.py — Canonical data models for CB6 Quantum
#
# Single source of truth for all signal, candle, trade, risk, and execution
# objects shared across NSE, Forex, and Crypto engines.
#
# Usage:
#   from core.schemas import Candle, Signal, RiskDecision, ExecutionIntent, TradeState

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── Enumerations ─────────────────────────────────────────────────────────────

class Direction(str, Enum):
    LONG  = "LONG"
    SHORT = "SHORT"

class Market(str, Enum):
    NSE    = "NSE"
    FOREX  = "FOREX"
    CRYPTO = "CRYPTO"

class TradeStatus(str, Enum):
    CREATED    = "CREATED"
    VALIDATED  = "VALIDATED"
    ARMED      = "ARMED"
    SENT       = "SENT"
    FILLED     = "FILLED"
    MANAGING   = "MANAGING"
    CLOSED     = "CLOSED"
    BLOCKED    = "BLOCKED"
    FAILED     = "FAILED"

class Engine(str, Enum):
    NSE_PAPER   = "NSE_PAPER"
    NSE_LIVE    = "NSE_LIVE"
    FTMO        = "FTMO"
    GFT         = "GFT"
    CRYPTO_PAPER = "CRYPTO_PAPER"
    CRYPTO_LIVE  = "CRYPTO_LIVE"


# ── Core market data ──────────────────────────────────────────────────────────

@dataclass
class Candle:
    """Normalized OHLCV bar. Timezone: IST for NSE, UTC for Forex."""
    symbol:    str
    timestamp: datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int
    timeframe: str        # "1", "5", "15", "60", "D" (minutes or D/W)
    market:    Market
    source:    str = "unknown"   # "fyers", "truedata", "mt5", "binance"


# ── Signal ────────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    """
    Strategy output. Immutable after creation — any modification
    creates a new Signal with a new signal_id.
    """
    signal_id:  str           # unique UUID or timestamp-based ID
    symbol:     str
    market:     Market
    engine:     Engine
    direction:  Direction
    strategy:   str           # "ICT_SILVER_BULLET", "ICT_FOREX_SWEEP", etc.
    entry:      float
    stop_loss:  float
    target1:    float
    target2:    float
    target3:    Optional[float]
    timeframe:  str
    score:      float         # confidence score (0-100)
    timestamp:  datetime      # when the signal was generated
    window:     Optional[str] # e.g. "Morning Silver Bullet"
    notes:      str = ""

    @property
    def risk_pts(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def rr_to_t2(self) -> float:
        if self.risk_pts <= 0:
            return 0.0
        return abs(self.target2 - self.entry) / self.risk_pts


# ── Risk decision ─────────────────────────────────────────────────────────────

@dataclass
class RiskDecision:
    """
    Output of core.execution_guard — go/no-go plus reason.
    Always present before an ExecutionIntent is created.
    """
    allowed:     bool
    reason:      str
    signal_id:   str
    engine:      Engine
    daily_loss:  float = 0.0
    daily_limit: float = 0.0
    open_trades: int   = 0
    checked_at:  Optional[datetime] = None


# ── Execution intent ──────────────────────────────────────────────────────────

@dataclass
class ExecutionIntent:
    """
    The only object that may be passed to a broker adapter.
    Created by core.execution_guard after RiskDecision.allowed == True.
    """
    intent_id:      str
    signal:         Signal
    risk_decision:  RiskDecision
    engine:         Engine
    account_id:     str
    quantity:       float       # lots (Forex) or shares/contracts (NSE)
    idempotency_key: str        # prevents duplicate order placement
    created_at:     datetime = field(default_factory=datetime.utcnow)
    broker_meta:    dict = field(default_factory=dict)  # broker-specific params


# ── Trade state ───────────────────────────────────────────────────────────────

@dataclass
class TradeState:
    """
    Full lifecycle state of a single trade.
    Transitions: CREATED → VALIDATED → ARMED → SENT → FILLED →
                 MANAGING → CLOSED | FAILED | BLOCKED
    """
    trade_id:       str
    intent_id:      str
    signal_id:      str
    engine:         Engine
    account_id:     str
    symbol:         str
    market:         Market
    direction:      Direction
    status:         TradeStatus
    entry_price:    Optional[float] = None
    current_sl:     Optional[float] = None
    target1:        Optional[float] = None
    target2:        Optional[float] = None
    target3:        Optional[float] = None
    quantity:       float = 0.0
    pnl:            float = 0.0
    targets_hit:    list  = field(default_factory=list)
    open_time:      Optional[datetime] = None
    close_time:     Optional[datetime] = None
    broker_order_id: Optional[str] = None
    notes:          str = ""

    def transition(self, new_status: TradeStatus, note: str = "") -> None:
        """Enforce valid status transitions."""
        _ALLOWED: dict[TradeStatus, set[TradeStatus]] = {
            TradeStatus.CREATED:   {TradeStatus.VALIDATED, TradeStatus.BLOCKED},
            TradeStatus.VALIDATED: {TradeStatus.ARMED, TradeStatus.BLOCKED},
            TradeStatus.ARMED:     {TradeStatus.SENT, TradeStatus.BLOCKED},
            TradeStatus.SENT:      {TradeStatus.FILLED, TradeStatus.FAILED},
            TradeStatus.FILLED:    {TradeStatus.MANAGING, TradeStatus.FAILED},
            TradeStatus.MANAGING:  {TradeStatus.CLOSED, TradeStatus.FAILED},
        }
        allowed_next = _ALLOWED.get(self.status, set())
        if new_status not in allowed_next:
            raise ValueError(
                f"Invalid transition {self.status} → {new_status} for trade {self.trade_id}"
            )
        self.status = new_status
        if note:
            self.notes = f"{self.notes} | {note}".strip(" |")
