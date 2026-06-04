from __future__ import annotations

from typing import Any, Dict, Optional

from ml_engine.memory.replay_archive import archive_trade_replay_safe
from ml_engine.memory.shadow_logger import build_setup_dna


def archive_closed_trade_shadow(
    market: str,
    engine: str,
    trade: Dict[str, Any],
    *,
    result: str,
    rr_achieved: Optional[float],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        dna = build_setup_dna(trade).to_dict()
    except Exception:
        dna = {}
    try:
        archive_trade_replay_safe(
            market=market,
            trade=trade,
            result=result,
            rr_achieved=rr_achieved,
            setup_dna=dna,
            bars_before_entry=(trade.get("bars_before_entry") if isinstance(trade, dict) else None) or [],
            entry_bar=(trade.get("entry_bar") if isinstance(trade, dict) else None) or {},
            bars_after_entry=(trade.get("bars_after_entry") if isinstance(trade, dict) else None) or [],
            metadata={"engine": engine, **(metadata or {})},
        )
    except Exception:
        pass

