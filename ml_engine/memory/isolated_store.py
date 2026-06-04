from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.state_io import load_json_locked, save_json_locked

from ml_engine.memory.regime_contract import normalize_regime
from ml_engine.memory.schema_v1 import (
    MemoryStoreSnapshotV1,
    MemoryTradeEventV1,
    SetupDNA,
    utc_now_iso,
)


class IsolatedMemoryStoreV1:
    """
    Per-market memory store.

    Shared code path, separate market files to prevent cross-market contamination.
    """

    def __init__(self, market: str, root_dir: str = "memory") -> None:
        self.market = market.strip().lower()
        self.root = Path(root_dir)
        self.path = self.root / self.market / "memory_v1.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _default_snapshot(self) -> Dict[str, Any]:
        return MemoryStoreSnapshotV1(
            market=self.market, stats={"trade_count": 0, "last_event_id": None}, events=[]
        ).to_dict()

    def load_snapshot(self) -> Dict[str, Any]:
        return load_json_locked(str(self.path), self._default_snapshot())

    def save_snapshot(self, snapshot: Dict[str, Any]) -> None:
        snapshot["updated_at_utc"] = utc_now_iso()
        save_json_locked(str(self.path), snapshot)

    def append_trade_event(
        self,
        *,
        engine: str,
        symbol: str,
        session: str,
        direction: str,
        regime: str,
        setup_dna: SetupDNA,
        ml_score: Optional[float] = None,
        result: str = "OPEN",
        rr_achieved: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot = self.load_snapshot()
        event = MemoryTradeEventV1(
            market=self.market,  # type: ignore[arg-type]
            engine=engine,
            symbol=symbol,
            session=session,
            direction=str(direction).upper(),  # type: ignore[arg-type]
            regime=normalize_regime(regime),
            setup_dna=setup_dna.to_dict(),
            ml_score=ml_score,
            result=str(result).upper(),  # type: ignore[arg-type]
            rr_achieved=rr_achieved,
            metadata=metadata or {},
        ).to_dict()
        snapshot.setdefault("events", []).append(event)
        stats = snapshot.setdefault("stats", {})
        stats["trade_count"] = int(stats.get("trade_count", 0)) + 1
        stats["last_event_id"] = event["event_id"]
        self.save_snapshot(snapshot)
        return event

    def query_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        snapshot = self.load_snapshot()
        events = snapshot.get("events", [])
        if not isinstance(events, list):
            return []
        return events[-max(1, int(limit)) :]
