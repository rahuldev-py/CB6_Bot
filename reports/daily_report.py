"""Production-safe, read-only CB6 Quantum daily report generator.

Inputs are never modified. The only writes are the requested JSON and Markdown
report artifacts under ``reports/`` (or an explicitly supplied output folder).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger("cb6.daily_report")

ROOT = Path(__file__).resolve().parents[1]
NA = "N/A"

ACCOUNT_SOURCES = {
    "NSE": "data/paper_state.json",
    "GFT_1K": "data/gft_1k_instant/state.json",
    "GFT_5K": "data/gft_5k/state.json",
    "GFT_10K": "data/gft_10k/state.json",
}

HEARTBEAT_ENGINES = (
    "nse_engine",
    "gft_5k",
    "gft_1k_instant",
    "gft_10k",
    "telegram_nse",
    "telegram_gft",
    "db_writer",
    "data_feed",
)


def generate_daily_report(
    report_date: str | date | None = None,
    root: str | Path | None = None,
    output_dir: str | Path | None = None,
    write_files: bool = True,
    heartbeat_stale_after: int = 180,
) -> dict[str, Any]:
    """Build the structured report and optionally write JSON + Markdown files."""
    base = Path(root) if root is not None else ROOT
    day = _date_string(report_date)
    warnings: list[str] = []

    accounts = {
        account: _account_report(base / rel_path, account, day, warnings)
        for account, rel_path in ACCOUNT_SOURCES.items()
    }
    audit = _audit_summary(base, day, warnings)
    report = {
        "report_date": day,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "accounts": accounts,
        "ops": {
            "missed_db_writes": audit["missed_db_writes"],
            "heartbeat_failures": _heartbeat_summary(
                base, heartbeat_stale_after, warnings
            ),
            "broker_reconciliation_mismatches": audit["reconciliation"],
        },
        "nse_oi_pcr_context": _oi_pcr_summary(base, day, warnings),
        "slippage_summary": _slippage_summary(base, day, accounts, warnings),
        "hermes_pattern_updates": _hermes_summary(base, day, warnings),
        "warnings": warnings,
    }
    report["telegram_summary"] = render_telegram(report)

    paths: dict[str, str] = {}
    if write_files:
        target = Path(output_dir) if output_dir is not None else base / "reports"
        target.mkdir(parents=True, exist_ok=True)
        stem = f"daily_report_{day.replace('-', '')}"
        json_path = target / f"{stem}.json"
        markdown_path = target / f"{stem}.md"
        json_path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        paths = {"json": str(json_path), "markdown": str(markdown_path)}

    return {"report": report, "paths": paths, "telegram": report["telegram_summary"]}


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a complete human-readable Markdown report."""
    lines = [
        f"# CB6 Quantum Daily Report - {report['report_date']}",
        "",
        f"Generated UTC: {report['generated_at_utc']}",
        "",
        "## Account Performance",
        "",
        "| Account | Trades | PnL | R Multiple | Win Rate | Losses | Max Drawdown | Best Setup | Worst Setup |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for name, item in report["accounts"].items():
        lines.append(
            f"| {name} | {_fmt(item['trade_count'])} | {_fmt(item['pnl'])} | "
            f"{_fmt(item['r_multiple'])} | {_pct(item['win_rate'])} | "
            f"{_fmt(item['loss_count'])} | {_fmt(item['max_drawdown'])} | "
            f"{_fmt(item['best_setup'])} | {_fmt(item['worst_setup'])} |"
        )

    ops = report["ops"]
    lines += [
        "",
        "## Operations",
        "",
        f"- Missed DB writes: {_fmt(ops['missed_db_writes']['count'])}",
        f"- Heartbeat failures: {_fmt(ops['heartbeat_failures']['count'])}",
        f"- Broker reconciliation mismatches: {_fmt(ops['broker_reconciliation_mismatches']['count'])}",
        "",
        "## Execution And Context",
        "",
        f"- Slippage: {_summary_text(report['slippage_summary'])}",
        f"- NSE OI/PCR: {_summary_text(report['nse_oi_pcr_context'])}",
        f"- Hermes pattern updates: {_summary_text(report['hermes_pattern_updates'])}",
    ]
    if report["warnings"]:
        lines += ["", "## Warnings", ""]
        lines.extend(f"- {warning}" for warning in report["warnings"])
    return "\n".join(lines) + "\n"


def render_telegram(report: Mapping[str, Any]) -> str:
    """Render a compact Telegram-safe plain-text summary."""
    lines = [f"CB6 DAILY REPORT - {report['report_date']}"]
    for name, item in report["accounts"].items():
        lines.append(
            f"{name}: trades={_fmt(item['trade_count'])} "
            f"PnL={_fmt(item['pnl'])} R={_fmt(item['r_multiple'])} "
            f"WR={_pct(item['win_rate'])} DD={_fmt(item['max_drawdown'])}"
        )
    ops = report["ops"]
    lines += [
        f"DB write misses: {_fmt(ops['missed_db_writes']['count'])}",
        f"Heartbeat failures: {_fmt(ops['heartbeat_failures']['count'])}",
        f"Reconciliation mismatches: {_fmt(ops['broker_reconciliation_mismatches']['count'])}",
        f"Slippage: {_summary_text(report['slippage_summary'])}",
        f"NSE OI/PCR: {_summary_text(report['nse_oi_pcr_context'])}",
        f"Hermes updates: {_summary_text(report['hermes_pattern_updates'])}",
    ]
    return "\n".join(lines)


def _account_report(
    state_path: Path, account: str, day: str, warnings: list[str]
) -> dict[str, Any]:
    state = _load_json(state_path, warnings, f"{account} state")
    if state is None:
        return _empty_account()

    rows = state.get("closed_trades", [])
    if not isinstance(rows, list):
        _warn(warnings, f"{account} closed_trades is malformed")
        return _empty_account()

    trades = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _warn(warnings, f"{account} skipped malformed trade row {index}")
            continue
        if _trade_day(row) == day:
            trades.append(row)

    pnl_values = [_pnl(row) for row in trades]
    r_values = [_r_multiple(row) for row in trades]
    wins = sum(value > 0 for value in pnl_values)
    losses = sum(value < 0 for value in pnl_values)
    setups = _setup_breakdown(trades)
    return {
        "trade_count": len(trades),
        "pnl": round(sum(pnl_values), 2),
        "r_multiple": round(sum(r_values), 2),
        "win_rate": round(100.0 * wins / len(trades), 1) if trades else 0.0,
        "loss_count": losses,
        "max_drawdown": _max_drawdown(pnl_values),
        "best_setup": setups["best"],
        "worst_setup": setups["worst"],
        "trades": [_trade_view(row) for row in trades],
    }


def _empty_account() -> dict[str, Any]:
    return {
        "trade_count": NA,
        "pnl": NA,
        "r_multiple": NA,
        "win_rate": NA,
        "loss_count": NA,
        "max_drawdown": NA,
        "best_setup": NA,
        "worst_setup": NA,
        "trades": [],
    }


def _audit_summary(base: Path, day: str, warnings: list[str]) -> dict[str, Any]:
    path = base / "data" / "audit" / f"orders_{day}.jsonl"
    rows = _read_jsonl(path, warnings, "daily audit log")
    if rows is None:
        unavailable = {"count": NA, "events": []}
        return {"missed_db_writes": unavailable, "reconciliation": unavailable.copy()}

    db_rows = [row for row in rows if row.get("event") == "DB_WRITE_FAILURE"]
    reconcile_rows = [
        row
        for row in rows
        if row.get("event")
        in {"POSITION_RECONCILE_MISMATCH", "POSITION_RECONCILE_FETCH_FAILED"}
    ]
    mismatch_count = sum(
        _safe_int(row.get("phantom_count")) + _safe_int(row.get("ghost_count"))
        if row.get("event") == "POSITION_RECONCILE_MISMATCH"
        else 1
        for row in reconcile_rows
    )
    return {
        "missed_db_writes": {"count": len(db_rows), "events": db_rows},
        "reconciliation": {"count": mismatch_count, "events": reconcile_rows},
    }


def _heartbeat_summary(
    base: Path, stale_after: int, warnings: list[str]
) -> dict[str, Any]:
    now = int(datetime.now(timezone.utc).timestamp())
    failures = []
    for engine in HEARTBEAT_ENGINES:
        payload = _load_json(
            base / "data" / "heartbeat" / f"{engine}.json",
            warnings,
            f"{engine} heartbeat",
        )
        if payload is None:
            failures.append({"engine": engine, "status": "NO_HEARTBEAT", "age_secs": NA})
            continue
        ts = _safe_int(payload.get("ts"))
        age = max(0, now - ts) if ts else NA
        status = str(payload.get("status", "unknown"))
        if not ts or (isinstance(age, int) and age > stale_after) or status.lower() != "ok":
            failures.append({"engine": engine, "status": status, "age_secs": age})
    return {"count": len(failures), "failures": failures, "stale_after_secs": stale_after}


def _oi_pcr_summary(base: Path, day: str, warnings: list[str]) -> dict[str, Any]:
    db_path = base / "data" / "cb6_trades.db"
    if not db_path.exists():
        _warn(warnings, f"NSE OI/PCR database unavailable: {db_path}")
        return {"status": NA, "summary": NA, "snapshots": []}
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        rows = conn.execute(
            """
            SELECT symbol, ts, pcr_oi, option_bias, source
            FROM oi_snapshots
            WHERE substr(ts, 1, 10) = ?
            ORDER BY ts DESC
            """,
            (day,),
        ).fetchall()
    except Exception as exc:
        _warn(warnings, f"NSE OI/PCR unavailable: {exc}")
        return {"status": NA, "summary": NA, "snapshots": []}
    finally:
        if conn is not None:
            conn.close()
    if not rows:
        _warn(warnings, f"NSE OI/PCR has no snapshots for {day}")
        return {"status": NA, "summary": NA, "snapshots": []}
    latest: dict[str, tuple] = {}
    for row in rows:
        latest.setdefault(str(row[0]), row)
    snapshots = [
        {"symbol": row[0], "ts": row[1], "pcr_oi": row[2], "bias": row[3], "source": row[4]}
        for row in latest.values()
    ]
    summary = "; ".join(
        f"{row['symbol']} PCR={_fmt(row['pcr_oi'])} {row['bias'] or NA}"
        for row in snapshots
    )
    return {"status": "available", "summary": summary, "snapshots": snapshots}


def _slippage_summary(
    base: Path,
    day: str,
    accounts: Mapping[str, Mapping[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    values = []
    exceeded = 0
    for account in accounts.values():
        for trade in account.get("trades", []):
            value = _safe_float(trade.get("slippage"), None)
            if value is not None:
                values.append(abs(value))
                exceeded += bool(trade.get("slippage_exceeded"))

    log_dir = base / "forex_engine" / "logs" / "slippage"
    pattern = re.compile(r"slip=([-+]?\d+(?:\.\d+)?)")
    if log_dir.exists():
        for path in log_dir.glob("*_slippage.log"):
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith(day):
                        match = pattern.search(line)
                        if match:
                            values.append(abs(float(match.group(1))))
                            exceeded += 1
            except OSError as exc:
                _warn(warnings, f"Could not read slippage log {path}: {exc}")
    if not values:
        _warn(warnings, f"Slippage data unavailable for {day}")
        return {"status": NA, "summary": NA, "count": NA, "average": NA, "max": NA, "exceeded": NA}
    summary = f"{len(values)} fills, avg={sum(values)/len(values):.5f}, max={max(values):.5f}, exceeded={exceeded}"
    return {
        "status": "available",
        "summary": summary,
        "count": len(values),
        "average": round(sum(values) / len(values), 5),
        "max": round(max(values), 5),
        "exceeded": exceeded,
    }


def _hermes_summary(base: Path, day: str, warnings: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "available", "recorded_trades": 0, "nudges": 0}
    db_path = base / "ml_engine" / "memory" / "trade_pattern_db.sqlite"
    if not db_path.exists():
        _warn(warnings, f"Hermes pattern database unavailable: {db_path}")
        result["status"] = NA
        result["recorded_trades"] = NA
    else:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            result["recorded_trades"] = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE substr(recorded_at, 1, 10) = ?",
                (day,),
            ).fetchone()[0]
        except Exception as exc:
            _warn(warnings, f"Hermes pattern updates unavailable: {exc}")
            result["status"] = NA
            result["recorded_trades"] = NA
        finally:
            if conn is not None:
                conn.close()

    nudges = _read_jsonl(
        base / "ml_engine" / "learning" / "nudge_proposals.jsonl",
        warnings,
        "Hermes nudge proposals",
        missing_is_warning=False,
    )
    result["nudges"] = (
        sum(str(row.get("ts", "")).startswith(day) for row in nudges)
        if nudges is not None
        else NA
    )
    result["summary"] = (
        f"recorded trades={_fmt(result['recorded_trades'])}, nudges={_fmt(result['nudges'])}"
    )
    return result


def _read_jsonl(
    path: Path,
    warnings: list[str],
    label: str,
    missing_is_warning: bool = True,
) -> list[dict[str, Any]] | None:
    if not path.exists():
        if missing_is_warning:
            _warn(warnings, f"{label} unavailable: {path}")
        return None
    rows = []
    try:
        for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
                else:
                    _warn(warnings, f"{label} skipped malformed row {index}")
            except (json.JSONDecodeError, TypeError):
                _warn(warnings, f"{label} skipped malformed row {index}")
    except OSError as exc:
        _warn(warnings, f"{label} unavailable: {exc}")
        return None
    return rows


def _load_json(path: Path, warnings: list[str], label: str) -> dict[str, Any] | None:
    if not path.exists():
        _warn(warnings, f"{label} unavailable: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict):
            return value
        _warn(warnings, f"{label} is malformed")
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        _warn(warnings, f"{label} unavailable: {exc}")
    return None


def _trade_view(row: Mapping[str, Any]) -> dict[str, Any]:
    entry = _safe_float(_first(row, "entry_price", "entry"), None)
    fill = _safe_float(_first(row, "fill_price", "executed_price"), None)
    slippage = _safe_float(row.get("slippage"), None)
    if slippage is None and entry is not None and fill is not None:
        slippage = abs(fill - entry)
    return {
        "id": _first(row, "id", "trade_id", "ticket", default=NA),
        "symbol": row.get("symbol", NA),
        "setup": _setup_name(row),
        "pnl": _pnl(row),
        "r_multiple": _r_multiple(row),
        "slippage": slippage,
        "slippage_exceeded": bool(row.get("slippage_exceeded", row.get("high_slippage", False))),
    }


def _trade_day(row: Mapping[str, Any]) -> str:
    return str(_first(row, "exit_time", "close_time", "date", default=""))[:10]


def _pnl(row: Mapping[str, Any]) -> float:
    return _safe_float(_first(row, "pnl_usd", "pnl", "realized_pnl"), 0.0) or 0.0


def _r_multiple(row: Mapping[str, Any]) -> float:
    explicit = _safe_float(_first(row, "r_multiple", "pnl_r", "actual_rrr"), None)
    if explicit is not None:
        return explicit
    risk = _safe_float(_first(row, "risk_usd", "risk_amount"), 0.0) or 0.0
    return _pnl(row) / risk if risk else 0.0


def _setup_name(row: Mapping[str, Any]) -> str:
    return str(_first(row, "setup_type", "mss_type", "entry_reason", "window", default=NA))


def _setup_breakdown(trades: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    totals: dict[str, float] = {}
    for trade in trades:
        setup = _setup_name(trade)
        totals[setup] = totals.get(setup, 0.0) + _pnl(trade)
    if not totals:
        return {"best": NA, "worst": NA}
    return {
        "best": max(totals, key=totals.get),
        "worst": min(totals, key=totals.get),
    }


def _max_drawdown(pnl_values: Iterable[float]) -> float:
    equity = peak = max_dd = 0.0
    for value in pnl_values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _date_string(value: str | date | None) -> str:
    if value is None:
        return datetime.now().astimezone().date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()


def _first(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value) if value not in (None, "", "nan") else default
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt(value: Any) -> str:
    if value == NA or value is None:
        return NA
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _pct(value: Any) -> str:
    return NA if value == NA else f"{_fmt(value)}%"


def _summary_text(value: Mapping[str, Any]) -> str:
    return str(value.get("summary", value.get("status", NA)))


def _warn(warnings: list[str], message: str) -> None:
    warnings.append(message)
    logger.warning(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the CB6 Quantum daily report")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD format")
    parser.add_argument("--root", help="Repository root override")
    parser.add_argument("--output-dir", help="Output directory override")
    args = parser.parse_args()
    result = generate_daily_report(args.date, args.root, args.output_dir)
    print(result["paths"]["markdown"])
    print(result["paths"]["json"])
    print()
    print(result["telegram"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
