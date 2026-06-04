from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


MARKETS = ("nse", "forex", "futures", "crypto")
MEMORY_ROOT = Path("memory")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _market_path(market: str) -> Path:
    m = str(market).strip().lower()
    if m not in MARKETS:
        raise ValueError(f"Unsupported market '{market}'. Expected one of: {', '.join(MARKETS)}")
    return MEMORY_ROOT / m / "memory_v1.json"


def _load_market_events_read_only(market: str) -> List[Dict[str, Any]]:
    path = _market_path(market)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    events = data.get("events", [])
    return events if isinstance(events, list) else []


def _is_closed_trade_event(ev: Dict[str, Any]) -> bool:
    meta = ev.get("metadata") or {}
    event_type = str(meta.get("event_type", "")).strip().lower()
    result = str(ev.get("result", "")).upper()
    return event_type == "trade_closed" or result in {"WIN", "LOSS", "BREAKEVEN"}


def _setup_key(ev: Dict[str, Any]) -> str:
    dna = ev.get("setup_dna") or {}
    parts = [
        f"regime={dna.get('regime', 'UNKNOWN')}",
        f"session={dna.get('session', 'UNKNOWN')}",
        f"sweep={dna.get('sweep_type', 'UNKNOWN')}",
        f"fvg={dna.get('fvg_bucket', 'UNKNOWN')}",
        f"htf={dna.get('htf_bias', 'UNKNOWN')}",
    ]
    return "|".join(parts)


def _win_loss_stats(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    wins = losses = be = 0
    for ev in rows:
        r = str(ev.get("result", "")).upper()
        if r == "WIN":
            wins += 1
        elif r == "LOSS":
            losses += 1
        elif r == "BREAKEVEN":
            be += 1
    decided = wins + losses
    wr = (wins / decided * 100.0) if decided > 0 else 0.0
    return {
        "wins": wins,
        "losses": losses,
        "breakeven": be,
        "sample_size": wins + losses + be,
        "win_rate": round(wr, 2),
    }


def _group_win_rate(events: List[Dict[str, Any]], field_getter, min_samples: int) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        k = str(field_getter(ev) or "UNKNOWN")
        buckets[k].append(ev)
    out = []
    for k, rows in buckets.items():
        stats = _win_loss_stats(rows)
        if stats["sample_size"] < min_samples:
            continue
        out.append({"key": k, **stats})
    out.sort(key=lambda x: (-x["win_rate"], -x["sample_size"], x["key"]))
    return out


def _group_win_rate_multi(
    events: List[Dict[str, Any]],
    field_getters: Tuple,
    labels: Tuple[str, ...],
    min_samples: int,
) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        key = tuple(str(fn(ev) or "UNKNOWN") for fn in field_getters)
        buckets[key].append(ev)
    out = []
    for k, rows in buckets.items():
        stats = _win_loss_stats(rows)
        if stats["sample_size"] < min_samples:
            continue
        row = {labels[i]: k[i] for i in range(len(labels))}
        row.update(stats)
        out.append(row)
    out.sort(key=lambda x: (-x["win_rate"], -x["sample_size"]))
    return out


def _best_worst_setup_from_events(
    events: List[Dict[str, Any]],
    *,
    min_samples: int,
    top_n: int,
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        grouped[_setup_key(ev)].append(ev)
    rows = []
    for key, vals in grouped.items():
        stats = _win_loss_stats(vals)
        if stats["sample_size"] < min_samples:
            continue
        rows.append({"setup_key": key, **stats})
    best = sorted(rows, key=lambda x: (-x["win_rate"], -x["sample_size"]))[:top_n]
    worst = sorted(rows, key=lambda x: (x["win_rate"], -x["sample_size"]))[:top_n]
    return {"best": best, "worst": worst}


def query_best_worst_setup_dna(
    market: str,
    *,
    min_samples: int = 20,
    top_n: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return _best_worst_setup_from_events(events, min_samples=min_samples, top_n=top_n)


def query_win_rate_by_market(*, min_samples: int = 20) -> List[Dict[str, Any]]:
    out = []
    for market in MARKETS:
        events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
        stats = _win_loss_stats(events)
        if stats["sample_size"] < min_samples:
            continue
        out.append({"market": market, **stats})
    out.sort(key=lambda x: (-x["win_rate"], -x["sample_size"], x["market"]))
    return out


def query_win_rate_by_symbol(market: str, *, min_samples: int = 20) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return _group_win_rate(events, lambda e: e.get("symbol"), min_samples)


def query_win_rate_by_session(market: str, *, min_samples: int = 20) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return _group_win_rate(events, lambda e: e.get("session"), min_samples)


def query_win_rate_by_regime(market: str, *, min_samples: int = 20) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return _group_win_rate(events, lambda e: (e.get("setup_dna") or {}).get("regime"), min_samples)


def query_average_rr_by_setup(
    market: str,
    *,
    min_samples: int = 20,
    top_n: int = 20,
) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        grouped[_setup_key(ev)].append(ev)
    out = []
    for key, vals in grouped.items():
        if len(vals) < min_samples:
            continue
        rr_vals = [_safe_float(v.get("rr_achieved"), None) for v in vals]
        rr_vals = [x for x in rr_vals if x is not None]
        avg_rr = round(sum(rr_vals) / len(rr_vals), 3) if rr_vals else 0.0
        stats = _win_loss_stats(vals)
        out.append({"setup_key": key, "avg_rr": avg_rr, **stats})
    out.sort(key=lambda x: (-x["avg_rr"], -x["sample_size"], -x["win_rate"]))
    return out[:top_n]


def _average_rr_by_setup_from_events(
    events: List[Dict[str, Any]],
    *,
    min_samples: int,
    top_n: int,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        grouped[_setup_key(ev)].append(ev)
    out = []
    for key, vals in grouped.items():
        if len(vals) < min_samples:
            continue
        rr_vals = [_safe_float(v.get("rr_achieved"), None) for v in vals]
        rr_vals = [x for x in rr_vals if x is not None]
        avg_rr = round(sum(rr_vals) / len(rr_vals), 3) if rr_vals else 0.0
        stats = _win_loss_stats(vals)
        out.append({"setup_key": key, "avg_rr": avg_rr, **stats})
    out.sort(key=lambda x: (-x["avg_rr"], -x["sample_size"], -x["win_rate"]))
    return out[:top_n]


def query_losing_pattern_clusters(
    market: str,
    *,
    min_samples: int = 20,
    min_loss_rate: float = 60.0,
    top_n: int = 20,
) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        grouped[_setup_key(ev)].append(ev)
    out = []
    for key, vals in grouped.items():
        stats = _win_loss_stats(vals)
        if stats["sample_size"] < min_samples:
            continue
        decided = stats["wins"] + stats["losses"]
        if decided <= 0:
            continue
        loss_rate = round(stats["losses"] / decided * 100.0, 2)
        if loss_rate < min_loss_rate:
            continue
        reason_counter = Counter(
            str((v.get("metadata") or {}).get("exit_reason", "UNKNOWN")) for v in vals
        )
        out.append(
            {
                "setup_key": key,
                "loss_rate": loss_rate,
                "top_exit_reasons": reason_counter.most_common(3),
                **stats,
            }
        )
    out.sort(key=lambda x: (-x["loss_rate"], -x["sample_size"]))
    return out[:top_n]


def _losing_clusters_from_events(
    events: List[Dict[str, Any]],
    *,
    min_samples: int,
    min_loss_rate: float,
    top_n: int,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        grouped[_setup_key(ev)].append(ev)
    out = []
    for key, vals in grouped.items():
        stats = _win_loss_stats(vals)
        if stats["sample_size"] < min_samples:
            continue
        decided = stats["wins"] + stats["losses"]
        if decided <= 0:
            continue
        loss_rate = round(stats["losses"] / decided * 100.0, 2)
        if loss_rate < min_loss_rate:
            continue
        reason_counter = Counter(
            str((v.get("metadata") or {}).get("exit_reason", "UNKNOWN")) for v in vals
        )
        out.append(
            {
                "setup_key": key,
                "loss_rate": loss_rate,
                "top_exit_reasons": reason_counter.most_common(3),
                **stats,
            }
        )
    out.sort(key=lambda x: (-x["loss_rate"], -x["sample_size"]))
    return out[:top_n]


def build_dashboard_ready_summary(
    market: str,
    *,
    min_samples: int = 20,
) -> Dict[str, Any]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return {
        "market": market,
        "totals": _win_loss_stats(events),
        "win_rate_by_symbol_top": _group_win_rate(events, lambda e: e.get("symbol"), min_samples)[:10],
        "win_rate_by_session_top": _group_win_rate(events, lambda e: e.get("session"), min_samples)[:10],
        "win_rate_by_regime_top": _group_win_rate(events, lambda e: (e.get("setup_dna") or {}).get("regime"), min_samples)[:10],
        "best_worst_setup_dna": _best_worst_setup_from_events(events, min_samples=min_samples, top_n=5),
        "avg_rr_by_setup_top": _average_rr_by_setup_from_events(events, min_samples=min_samples, top_n=10),
        "losing_clusters_top": _losing_clusters_from_events(events, min_samples=min_samples, min_loss_rate=60.0, top_n=10),
    }


def query_regime_performance_summary(market: str, *, min_samples: int = 20) -> Dict[str, Any]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return {
        "market": market,
        "totals": _win_loss_stats(events),
        "by_regime": _group_win_rate(events, lambda e: (e.get("setup_dna") or {}).get("regime"), min_samples),
    }


def query_regime_symbol_performance(market: str, *, min_samples: int = 20) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return _group_win_rate_multi(
        events,
        (
            lambda e: (e.get("setup_dna") or {}).get("regime"),
            lambda e: e.get("symbol"),
        ),
        ("regime", "symbol"),
        min_samples,
    )


def query_regime_session_performance(market: str, *, min_samples: int = 20) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    return _group_win_rate_multi(
        events,
        (
            lambda e: (e.get("setup_dna") or {}).get("regime"),
            lambda e: e.get("session"),
        ),
        ("regime", "session"),
        min_samples,
    )


def query_regime_setup_performance(
    market: str,
    *,
    min_samples: int = 20,
    top_n: int = 50,
) -> List[Dict[str, Any]]:
    events = [e for e in _load_market_events_read_only(market) if _is_closed_trade_event(e)]
    rows = _group_win_rate_multi(
        events,
        (
            lambda e: (e.get("setup_dna") or {}).get("regime"),
            lambda e: _setup_key(e),
        ),
        ("regime", "setup_key"),
        min_samples,
    )
    return rows[:top_n]
