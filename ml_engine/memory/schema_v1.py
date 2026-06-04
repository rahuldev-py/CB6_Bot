from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4


SchemaVersion = Literal["1.0"]
Market = Literal["nse", "forex", "futures", "crypto"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SetupDNA:
    regime: str
    session: str
    sweep_type: str
    sweep_quality: float
    mss_quality: float
    bos_quality: float
    fvg_bucket: str
    htf_bias: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryTradeEventV1:
    schema_version: SchemaVersion = "1.0"
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at_utc: str = field(default_factory=utc_now_iso)
    market: Market = "forex"
    engine: str = ""
    symbol: str = ""
    session: str = ""
    direction: Literal["BUY", "SELL"] = "BUY"
    regime: str = "UNKNOWN"
    setup_dna: Dict[str, Any] = field(default_factory=dict)
    ml_score: Optional[float] = None
    result: Literal["WIN", "LOSS", "BREAKEVEN", "OPEN"] = "OPEN"
    rr_achieved: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryStoreSnapshotV1:
    schema_version: SchemaVersion = "1.0"
    market: Market = "forex"
    updated_at_utc: str = field(default_factory=utc_now_iso)
    stats: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

