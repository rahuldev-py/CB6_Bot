from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from reports.daily_report import generate_daily_report, render_markdown

DAY = "2026-06-12"


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _build_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    trades = [
        {
            "id": "win",
            "symbol": "XAGUSD",
            "exit_time": f"{DAY} 10:00:00",
            "pnl_usd": 20,
            "risk_usd": 10,
            "mss_type": "BOS",
            "slippage": 0.02,
        },
        {
            "id": "loss",
            "symbol": "USOIL",
            "exit_time": f"{DAY} 11:00:00",
            "pnl_usd": -10,
            "risk_usd": 10,
            "mss_type": "CHOCH",
            "slippage": 0.04,
            "slippage_exceeded": True,
        },
    ]
    for rel in (
        "data/paper_state.json",
        "data/gft_1k_instant/state.json",
        "data/gft_5k/state.json",
        "data/gft_10k/state.json",
    ):
        _write_json(root / rel, {"closed_trades": trades})

    audit = root / "data/audit" / f"orders_{DAY}.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        "\n".join(
            [
                json.dumps({"event": "DB_WRITE_FAILURE", "account": "GFT_5K"}),
                json.dumps(
                    {
                        "event": "POSITION_RECONCILE_MISMATCH",
                        "phantom_count": 1,
                        "ghost_count": 2,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    db = root / "data/cb6_trades.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE oi_snapshots "
            "(symbol TEXT, ts TEXT, pcr_oi REAL, option_bias TEXT, source TEXT)"
        )
        conn.execute(
            "INSERT INTO oi_snapshots VALUES (?, ?, ?, ?, ?)",
            ("NIFTY", f"{DAY} 15:20:00", 1.2, "BULLISH", "fyers"),
        )
        conn.commit()
    finally:
        conn.close()

    hermes = root / "ml_engine/memory/trade_pattern_db.sqlite"
    hermes.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(hermes)
    try:
        conn.execute("CREATE TABLE trades (recorded_at TEXT)")
        conn.execute("INSERT INTO trades VALUES (?)", (f"{DAY} 12:00:00",))
        conn.commit()
    finally:
        conn.close()
    return root


def test_generates_json_markdown_and_telegram(tmp_path):
    root = _build_root(tmp_path)
    result = generate_daily_report(DAY, root=root, output_dir=root / "out")
    report = result["report"]

    assert Path(result["paths"]["json"]).exists()
    assert Path(result["paths"]["markdown"]).exists()
    assert report["accounts"]["GFT_5K"]["trade_count"] == 2
    assert report["accounts"]["GFT_5K"]["pnl"] == 10.0
    assert report["accounts"]["GFT_5K"]["r_multiple"] == 1.0
    assert report["accounts"]["GFT_5K"]["win_rate"] == 50.0
    assert report["accounts"]["GFT_5K"]["loss_count"] == 1
    assert report["accounts"]["GFT_5K"]["max_drawdown"] == 10.0
    assert report["ops"]["missed_db_writes"]["count"] == 1
    assert report["ops"]["broker_reconciliation_mismatches"]["count"] == 3
    assert "CB6 DAILY REPORT" in result["telegram"]
    assert "NSE OI/PCR" in render_markdown(report)


def test_missing_db_does_not_crash_and_reports_na(tmp_path):
    root = _build_root(tmp_path)
    (root / "data/cb6_trades.db").unlink()

    report = generate_daily_report(DAY, root=root, write_files=False)["report"]

    assert report["nse_oi_pcr_context"]["status"] == "N/A"
    assert any("OI/PCR database unavailable" in item for item in report["warnings"])


def test_empty_data_is_valid_zero_summary(tmp_path):
    root = _build_root(tmp_path)
    for rel in (
        "data/paper_state.json",
        "data/gft_1k_instant/state.json",
        "data/gft_5k/state.json",
        "data/gft_10k/state.json",
    ):
        _write_json(root / rel, {"closed_trades": []})

    report = generate_daily_report(DAY, root=root, write_files=False)["report"]

    assert report["accounts"]["NSE"]["trade_count"] == 0
    assert report["accounts"]["GFT_10K"]["pnl"] == 0.0
    assert report["accounts"]["GFT_1K"]["best_setup"] == "N/A"


def test_malformed_rows_are_skipped_with_warning(tmp_path):
    root = _build_root(tmp_path)
    _write_json(
        root / "data/gft_5k/state.json",
        {
            "closed_trades": [
                "bad row",
                {"exit_time": f"{DAY} 10:00:00", "pnl_usd": "bad", "risk_usd": "bad"},
            ]
        },
    )
    audit = root / "data/audit" / f"orders_{DAY}.jsonl"
    audit.write_text("{bad json}\n" + json.dumps({"event": "DB_WRITE_FAILURE"}), encoding="utf-8")

    report = generate_daily_report(DAY, root=root, write_files=False)["report"]

    assert report["accounts"]["GFT_5K"]["trade_count"] == 1
    assert report["accounts"]["GFT_5K"]["pnl"] == 0.0
    assert report["ops"]["missed_db_writes"]["count"] == 1
    assert any("malformed" in item for item in report["warnings"])


def test_missing_states_show_na(tmp_path):
    root = tmp_path / "empty"
    report = generate_daily_report(DAY, root=root, write_files=False)["report"]
    assert report["accounts"]["NSE"]["trade_count"] == "N/A"
    assert report["accounts"]["GFT_1K"]["pnl"] == "N/A"
