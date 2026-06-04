from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import CB6_REPLAY_V1_ENABLED
from utils.state_io import load_json_locked, save_json_locked


SCHEMA_VERSION = "1.0"
MARKETS = {"nse", "forex", "futures", "crypto"}
REPLAY_ROOT = Path("replays")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_result_bucket(result: str) -> str:
    r = str(result or "").upper()
    if r == "WIN":
        return "winners"
    if r == "LOSS":
        return "losers"
    return "breakeven"


def _market_dir(market: str) -> Path:
    m = str(market).strip().lower()
    if m not in MARKETS:
        raise ValueError(f"Unsupported market '{market}'")
    return REPLAY_ROOT / m


def _index_path(market: str) -> Path:
    return _market_dir(market) / "replay_index.json"


def _default_index(market: str) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "updated_at_utc": _utc_now(),
        "entries": [],
    }


def _safe_trade_id(payload: Dict[str, Any]) -> str:
    for key in ("trade_id", "id", "journal_id"):
        v = payload.get(key)
        if v:
            return str(v)
    return f"replay_{int(datetime.now(timezone.utc).timestamp() * 1000)}"


def build_replay_payload(
    *,
    market: str,
    trade: Dict[str, Any],
    result: str,
    rr_achieved: Optional[float],
    setup_dna: Optional[Dict[str, Any]] = None,
    bars_before_entry: Optional[List[Dict[str, Any]]] = None,
    entry_bar: Optional[Dict[str, Any]] = None,
    bars_after_entry: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trade_id": _safe_trade_id(trade),
        "market": str(market).lower(),
        "symbol": str(trade.get("symbol") or trade.get("underlying") or "UNKNOWN"),
        "direction": str(trade.get("direction") or "UNKNOWN"),
        "session": str(trade.get("session") or trade.get("window") or "UNKNOWN"),
        "regime": str(trade.get("regime") or trade.get("market_regime") or "UNKNOWN"),
        "setup_dna": setup_dna or {},
        "entry_time": str(trade.get("entry_time") or ""),
        "exit_time": str(trade.get("exit_time") or ""),
        "result": str(result).upper(),
        "rr_achieved": rr_achieved,
        "bars_before_entry": bars_before_entry or [],
        "entry_bar": entry_bar or {},
        "bars_after_entry": bars_after_entry or [],
        "metadata": metadata or {},
        "created_at_utc": _utc_now(),
    }


def write_replay_atomic(market: str, replay_payload: Dict[str, Any]) -> str:
    market = str(market).lower()
    market_base = _market_dir(market)
    market_base.mkdir(parents=True, exist_ok=True)

    result_bucket = _safe_result_bucket(replay_payload.get("result", "BREAKEVEN"))
    replay_dir = market_base / result_bucket
    replay_dir.mkdir(parents=True, exist_ok=True)

    trade_id = str(replay_payload.get("trade_id") or _safe_trade_id(replay_payload))
    replay_path = replay_dir / f"{trade_id}.json"
    save_json_locked(str(replay_path), replay_payload)

    index_fp = _index_path(market)
    index = load_json_locked(str(index_fp), _default_index(market))
    entries = index.setdefault("entries", [])
    entries = [e for e in entries if str(e.get("trade_id")) != trade_id]
    entries.append(
        {
            "trade_id": trade_id,
            "market": market,
            "symbol": replay_payload.get("symbol"),
            "direction": replay_payload.get("direction"),
            "session": replay_payload.get("session"),
            "regime": replay_payload.get("regime"),
            "result": replay_payload.get("result"),
            "rr_achieved": replay_payload.get("rr_achieved"),
            "entry_time": replay_payload.get("entry_time"),
            "exit_time": replay_payload.get("exit_time"),
            "path": os.path.relpath(replay_path.as_posix(), start=Path(".").as_posix()).replace("\\", "/"),
        }
    )
    index["entries"] = entries
    index["updated_at_utc"] = _utc_now()
    save_json_locked(str(index_fp), index)
    return str(replay_path)


def archive_trade_replay_safe(
    *,
    market: str,
    trade: Dict[str, Any],
    result: str,
    rr_achieved: Optional[float],
    setup_dna: Optional[Dict[str, Any]] = None,
    bars_before_entry: Optional[List[Dict[str, Any]]] = None,
    entry_bar: Optional[Dict[str, Any]] = None,
    bars_after_entry: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not CB6_REPLAY_V1_ENABLED:
        return None
    try:
        payload = build_replay_payload(
            market=market,
            trade=trade,
            result=result,
            rr_achieved=rr_achieved,
            setup_dna=setup_dna,
            bars_before_entry=bars_before_entry,
            entry_bar=entry_bar,
            bars_after_entry=bars_after_entry,
            metadata=metadata,
        )
        return write_replay_atomic(market, payload)
    except Exception:
        return None

