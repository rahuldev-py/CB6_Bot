import os

import pandas as pd


def _base_rows():
    rows = []
    price = 100.0
    for i in range(30):
        rows.append(
            {
                "time": f"2026-05-29 08:{i:02d}:00",
                "open": price,
                "high": price + 0.8,
                "low": price - 0.8,
                "close": price + 0.1,
                "volume": 100 + i,
            }
        )
        price += 0.02

    # Clean swing high for buy-side liquidity.
    rows[10]["high"] = 105.0
    rows[10]["close"] = 101.0
    rows[9]["high"] = 102.0
    rows[11]["high"] = 102.0

    # Clean swing low for sell-side liquidity.
    rows[14]["low"] = 95.0
    rows[14]["close"] = 100.5
    rows[13]["low"] = 98.0
    rows[15]["low"] = 98.0
    return rows


def test_high_sweep_marks_level_swept_and_scores(tmp_path, monkeypatch):
    from forex_engine.scanner import liquidity_sweep as ls

    monkeypatch.setattr(ls, "SWEEP_LOG_FILE", str(tmp_path / "sweep_events.jsonl"))
    ls._LEVEL_STATE.clear()
    ls._LOGGED_SWEEPS.clear()

    rows = _base_rows()
    rows.extend(
        [
            {
                "time": "2026-05-29 08:30:00",
                "open": 104.8,
                "high": 106.0,
                "low": 103.8,
                "close": 104.7,
                "volume": 260,
            },
            {
                "time": "2026-05-29 08:31:00",
                "open": 104.6,
                "high": 104.8,
                "low": 102.8,
                "close": 103.0,
                "volume": 230,
            },
        ]
    )
    df = pd.DataFrame(rows)

    sweep = ls.detect_sweep(df, symbol="XAUUSD", timeframe="15m", lookback=40, sweep_window=8)

    assert sweep is not None
    assert sweep["sweep_type"] == "HIGH_SWEEP"
    assert sweep["direction"] == "BEARISH"
    assert sweep["level_state"] == ls.STATE_SWEPT
    assert sweep["wick_ratio"] >= 0.12
    assert 0 <= sweep["confidence"] <= 100
    assert os.path.exists(ls.SWEEP_LOG_FILE)


def test_true_breakout_violates_buy_side_and_blocks_sweep(tmp_path, monkeypatch):
    from forex_engine.scanner import liquidity_sweep as ls

    monkeypatch.setattr(ls, "SWEEP_LOG_FILE", str(tmp_path / "sweep_events.jsonl"))
    ls._LEVEL_STATE.clear()
    ls._LOGGED_SWEEPS.clear()

    rows = _base_rows()
    rows.extend(
        [
            {
                "time": "2026-05-29 08:30:00",
                "open": 105.2,
                "high": 106.0,
                "low": 104.9,
                "close": 105.5,
                "volume": 220,
            },
            {
                "time": "2026-05-29 08:31:00",
                "open": 105.4,
                "high": 106.4,
                "low": 105.1,
                "close": 105.8,
                "volume": 240,
            },
        ]
    )
    df = pd.DataFrame(rows)

    state = ls.analyze_liquidity_state(df, symbol="XAUUSD", timeframe="15m", lookback=40, sweep_window=8)
    sweep = ls.detect_sweep(df, symbol="XAUUSD", timeframe="15m", lookback=40, sweep_window=8)

    assert sweep is None
    assert any(level["side"] == ls.BUY_SIDE for level in state["violated_levels"])
