from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from utils.state_io import load_json_locked, save_json_locked

MARKETS = ("nse", "forex", "futures", "crypto")
ROOT = Path("replays")
BUCKETS = ("winners", "losers", "breakeven")


def _index_path(market: str) -> Path:
    return ROOT / market / "replay_index.json"


def _scan_replay_files(market: str) -> List[Path]:
    base = ROOT / market
    files: List[Path] = []
    for bucket in BUCKETS:
        bdir = base / bucket
        if bdir.exists():
            files.extend(sorted(bdir.glob("*.json")))
    return files


def _load_json_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_index_entries_from_files(market: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for fp in _scan_replay_files(market):
        data = _load_json_file(fp)
        if not data:
            continue
        trade_id = str(data.get("trade_id") or fp.stem)
        entries.append(
            {
                "trade_id": trade_id,
                "market": market,
                "symbol": data.get("symbol"),
                "direction": data.get("direction"),
                "session": data.get("session"),
                "regime": data.get("regime"),
                "result": data.get("result"),
                "rr_achieved": data.get("rr_achieved"),
                "entry_time": data.get("entry_time"),
                "exit_time": data.get("exit_time"),
                "path": os.path.relpath(fp.as_posix(), start=Path(".").as_posix()).replace("\\", "/"),
            }
        )
    return entries


def rebuild_replay_index(market: str, *, apply: bool = False) -> Dict[str, Any]:
    entries = _build_index_entries_from_files(market)
    index_payload = {
        "schema_version": "1.0",
        "market": market,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    if apply:
        p = _index_path(market)
        p.parent.mkdir(parents=True, exist_ok=True)
        save_json_locked(str(p), index_payload)
    return {"market": market, "entries": len(entries), "applied": bool(apply)}


def validate_replay_index(market: str) -> Dict[str, Any]:
    p = _index_path(market)
    idx = load_json_locked(str(p), {"entries": []}) if p.exists() else {"entries": []}
    entries = idx.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    duplicate_ids = []
    seen = set()
    for e in entries:
        tid = str(e.get("trade_id", ""))
        if tid in seen:
            duplicate_ids.append(tid)
        seen.add(tid)
    missing_paths = []
    for e in entries:
        rel = e.get("path")
        if not rel:
            missing_paths.append({"trade_id": e.get("trade_id"), "reason": "missing_path"})
            continue
        if not Path(rel).exists():
            missing_paths.append({"trade_id": e.get("trade_id"), "path": rel})
    return {
        "market": market,
        "index_entries": len(entries),
        "duplicate_trade_ids": sorted(set(duplicate_ids)),
        "missing_files_from_index": missing_paths,
        "valid": len(duplicate_ids) == 0 and len(missing_paths) == 0,
    }


def detect_orphan_replay_files(market: str) -> List[str]:
    p = _index_path(market)
    idx = load_json_locked(str(p), {"entries": []}) if p.exists() else {"entries": []}
    entries = idx.get("entries", [])
    indexed_paths = set()
    if isinstance(entries, list):
        for e in entries:
            rel = e.get("path")
            if rel:
                indexed_paths.add(Path(rel).as_posix())
    fs_paths = [f.as_posix() for f in _scan_replay_files(market)]
    return sorted([x for x in fs_paths if x not in indexed_paths])


def detect_missing_replay_files_from_index(market: str) -> List[Dict[str, Any]]:
    p = _index_path(market)
    idx = load_json_locked(str(p), {"entries": []}) if p.exists() else {"entries": []}
    entries = idx.get("entries", [])
    out = []
    if not isinstance(entries, list):
        return out
    for e in entries:
        rel = e.get("path")
        if not rel:
            out.append({"trade_id": e.get("trade_id"), "reason": "missing_path"})
            continue
        if not Path(rel).exists():
            out.append({"trade_id": e.get("trade_id"), "path": rel})
    return out


def trim_index_entries(
    market: str,
    *,
    max_entries: int,
    apply: bool = False,
) -> Dict[str, Any]:
    p = _index_path(market)
    idx = load_json_locked(str(p), {"entries": []}) if p.exists() else {"entries": []}
    entries = idx.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    before = len(entries)
    if before <= max_entries:
        return {"market": market, "before": before, "after": before, "trimmed": 0, "applied": bool(apply)}

    def _key(e: Dict[str, Any]) -> str:
        return str(e.get("exit_time") or e.get("entry_time") or "")

    entries_sorted = sorted(entries, key=_key, reverse=True)
    kept = entries_sorted[:max_entries]
    trimmed = before - len(kept)
    if apply:
        idx["entries"] = kept
        save_json_locked(str(p), idx)
    return {"market": market, "before": before, "after": len(kept), "trimmed": trimmed, "applied": bool(apply)}


def delete_orphan_files(
    market: str,
    *,
    apply: bool = False,
) -> Dict[str, Any]:
    orphans = detect_orphan_replay_files(market)
    deleted = 0
    if apply:
        for f in orphans:
            try:
                Path(f).unlink(missing_ok=True)
                deleted += 1
            except Exception:
                pass
    return {"market": market, "orphans": len(orphans), "deleted": deleted, "applied": bool(apply)}

