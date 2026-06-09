import ast
import copy
from pathlib import Path

import pytest

from utils.hermes_close_adapter import (
    is_trade_durably_closed,
    notify_hermes_trade_closed,
)


ROOT = Path(__file__).resolve().parents[1]


def test_adapter_normalizes_trade(monkeypatch):
    captured = {}

    def fake_process_closed_trade(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "ml_engine.learning.feedback_loop.process_closed_trade",
        fake_process_closed_trade,
    )
    trade = {
        "trade_id": 42,
        "symbol": "NSE:NIFTYCE",
        "side": "BUY",
        "entry": "100.5",
        "current_sl": "90",
        "exit_price": "130",
        "pnl": "2950",
        "risk_amount": "1000",
        "status": "TARGET_HIT",
        "score": "12",
        "targets_hit": ("T1", "T2"),
        "session": "LONDON",
    }

    assert notify_hermes_trade_closed(
        trade,
        source="test_close",
        account="nse_paper_trader",
        market="NSE",
    )
    assert captured["market"] == "nse"
    assert captured["account"] == "nse_paper_trader"
    assert captured["notes"] == "source=test_close"
    assert captured["trade"] == {
        "id": "42",
        "symbol": "NSE:NIFTYCE",
        "direction": "BUY",
        "entry_price": 100.5,
        "stop_loss": 90.0,
        "exit_price": 130.0,
        "pnl_usd": 2950.0,
        "risk_usd": 1000.0,
        "entry_time": "",
        "exit_time": "",
        "exit_reason": "TARGET_HIT",
        "confluence": 12,
        "mss_type": "",
        "risk_mode": "normal",
        "targets_hit": ["T1", "T2"],
    }


def test_adapter_never_mutates_original_trade(monkeypatch):
    trade = {
        "id": "T-1",
        "symbol": "XAGUSD",
        "targets_hit": ["T1"],
        "nested": {"untouched": True},
    }
    before = copy.deepcopy(trade)

    def mutate_normalized_copy(**kwargs):
        kwargs["trade"]["targets_hit"].append("T2")
        kwargs["trade"]["symbol"] = "CHANGED"

    monkeypatch.setattr(
        "ml_engine.learning.feedback_loop.process_closed_trade",
        mutate_normalized_copy,
    )

    assert notify_hermes_trade_closed(trade, "test", "ftmo", "forex")
    assert trade == before


@pytest.mark.parametrize(
    "trade",
    [
        {"symbol": "XAGUSD"},
        {"id": "T-1"},
        {"id": "", "symbol": "XAGUSD"},
        None,
    ],
)
def test_adapter_missing_identity_returns_false(monkeypatch, trade):
    calls = []
    monkeypatch.setattr(
        "ml_engine.learning.feedback_loop.process_closed_trade",
        lambda **kwargs: calls.append(kwargs),
    )

    assert notify_hermes_trade_closed(trade, "test", "ftmo", "forex") is False
    assert calls == []


def test_adapter_hermes_exception_returns_false(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("Hermes unavailable")

    monkeypatch.setattr(
        "ml_engine.learning.feedback_loop.process_closed_trade",
        fail,
    )

    assert (
        notify_hermes_trade_closed(
            {"id": "T-1", "symbol": "XAGUSD"},
            "test",
            "ftmo",
            "forex",
        )
        is False
    )


def test_durable_close_readback_uses_full_trade_id():
    trade = {"id": "12345678-full-a", "symbol": "XAGUSD"}
    state = {
        "closed_trades": [
            {"id": "12345678-full-b", "symbol": "XAGUSD"},
            trade.copy(),
        ]
    }

    assert is_trade_durably_closed(lambda: state, trade) is True
    assert (
        is_trade_durably_closed(
            lambda: {"closed_trades": state["closed_trades"][:1]},
            trade,
        )
        is False
    )


def test_durable_close_readback_failure_is_false():
    def failed_readback():
        raise OSError("state unavailable")

    assert (
        is_trade_durably_closed(
            failed_readback,
            {"id": "T-1", "symbol": "XAGUSD"},
        )
        is False
    )


def _function_node(relative_path, function_name):
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )


def _named_call_lines(function_node, name):
    return [
        node.lineno
        for node in ast.walk(function_node)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == name
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == name
        )
    ]


@pytest.mark.parametrize(
    ("relative_path", "function_name", "save_name"),
    [
        ("trader/paper_trader.py", "close_paper_trade", "save_state"),
        ("trader/paper_trader.py", "close_paper_trade_by_id", "save_state"),
        (
            "forex_engine/prop_firms/ftmo/ftmo_state.py",
            "_update_trades_locked",
            "_save",
        ),
        (
            "forex_engine/prop_firms/ftmo/ftmo_state.py",
            "manual_exit_trade",
            "_save",
        ),
        (
            "forex_engine/prop_firms/gft/gft_5k_2step.py",
            "_check_exits",
            "_save",
        ),
        (
            "forex_engine/prop_firms/gft/gft_5k_2step.py",
            "manual_exit_trade",
            "_save",
        ),
    ],
)
def test_each_approved_close_path_has_one_post_save_notification(
    relative_path, function_name, save_name
):
    function_node = _function_node(relative_path, function_name)
    notify_lines = _named_call_lines(function_node, "notify_hermes_trade_closed")
    save_lines = _named_call_lines(function_node, save_name)

    assert len(notify_lines) == 1
    assert save_lines
    assert notify_lines[0] > max(save_lines)


def _forex_trade():
    return {
        "id": "T-1",
        "symbol": "XAGUSD",
        "direction": "BULLISH",
        "entry_price": 30.0,
        "stop_loss": 29.0,
        "current_sl": 29.0,
        "target1": 31.0,
        "target2": 32.0,
        "target3": 33.0,
        "lots": 0.3,
        "targets_hit": [],
        "pnl_usd": 0.0,
        "risk_usd": 100.0,
        "entry_time": "",
        "status": "OPEN",
    }


def _forex_state(trade):
    return {
        "open_trades": [trade],
        "closed_trades": [],
        "capital": 10000.0,
        "available_capital": 9900.0,
        "total_pnl": 0.0,
        "daily_pnl": 0.0,
        "daily_closed_pnl": 0.0,
        "daily_losses": 0,
        "best_day_pnl": 0.0,
        "peak_capital": 10000.0,
    }


def _persisted_forex_state(trade):
    state = _forex_state(trade)
    if trade.get("status") == "CLOSED":
        state["open_trades"] = []
        state["closed_trades"] = [trade]
    return state


def test_partial_ftmo_event_does_not_notify(monkeypatch):
    from forex_engine.prop_firms.ftmo import ftmo_state

    trade = _forex_trade()
    calls = []
    monkeypatch.setattr(ftmo_state, "load_state", lambda: _persisted_forex_state(trade))
    monkeypatch.setattr(ftmo_state, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    events = ftmo_state._update_trades_locked(31.0, "XAGUSD")

    assert any(event["type"] == "T1" for event in events)
    assert trade["status"] == "OPEN"
    assert calls == []


def test_full_ftmo_event_notifies_exactly_once(monkeypatch):
    from forex_engine.prop_firms.ftmo import ftmo_state

    trade = _forex_trade()
    calls = []
    monkeypatch.setattr(ftmo_state, "load_state", lambda: _persisted_forex_state(trade))
    monkeypatch.setattr(ftmo_state, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    events = ftmo_state._update_trades_locked(33.0, "XAGUSD")

    assert any(event["type"] == "T3" for event in events)
    assert trade["status"] == "CLOSED"
    assert len(calls) == 1


def test_manual_ftmo_event_notifies_exactly_once(monkeypatch):
    from forex_engine.prop_firms.ftmo import ftmo_state

    trade = _forex_trade()
    calls = []
    monkeypatch.setattr(ftmo_state, "load_state", lambda: _persisted_forex_state(trade))
    monkeypatch.setattr(ftmo_state, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    event = ftmo_state.manual_exit_trade("T-1", 30.5)

    assert event["type"] == "MANUAL"
    assert trade["status"] == "CLOSED"
    assert len(calls) == 1


def test_failed_ftmo_save_attempt_skips_hermes(monkeypatch):
    from forex_engine.prop_firms.ftmo import ftmo_state

    trade = _forex_trade()
    stale_trade = copy.deepcopy(trade)
    states = iter([_forex_state(trade), _forex_state(stale_trade)])
    calls = []
    monkeypatch.setattr(ftmo_state, "load_state", lambda: next(states))
    monkeypatch.setattr(ftmo_state, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    events = ftmo_state._update_trades_locked(33.0, "XAGUSD")

    assert any(event["type"] == "T3" for event in events)
    assert calls == []


def test_full_id_dedup_keeps_separate_ftmo_trades(monkeypatch):
    from forex_engine.prop_firms.ftmo import ftmo_state

    first = _forex_trade()
    first["id"] = "12345678-full-a"
    second = copy.deepcopy(first)
    second["id"] = "12345678-full-b"
    state = _forex_state(first)
    state["open_trades"].append(second)
    calls = []
    monkeypatch.setattr(ftmo_state, "load_state", lambda: state)
    monkeypatch.setattr(ftmo_state, "_save", lambda saved: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    events = ftmo_state._update_trades_locked(33.0, "XAGUSD")

    assert sum(event["type"] == "T3" for event in events) == 2
    assert [args[0]["id"] for args, _ in calls] == [
        "12345678-full-a",
        "12345678-full-b",
    ]


@pytest.mark.parametrize(
    "relative_path",
    [
        "forex_engine/prop_firms/ftmo/ftmo_state.py",
        "forex_engine/prop_firms/gft/gft_5k_2step.py",
    ],
)
def test_new_forex_trade_ids_are_not_uuid_prefixes(relative_path):
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))

    assert not any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "str"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "uuid4"
            for child in ast.walk(node.value)
        )
        for node in ast.walk(tree)
    )


def test_partial_gft_event_does_not_notify(monkeypatch):
    from forex_engine.prop_firms.gft import gft_5k_2step

    trade = _forex_trade()
    calls = []

    class Connector:
        @staticmethod
        def get_price(symbol):
            return 31.0

    monkeypatch.setattr(
        gft_5k_2step, "load_state", lambda: _persisted_forex_state(trade)
    )
    monkeypatch.setattr(gft_5k_2step, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    events = gft_5k_2step._check_exits(Connector(), "XAGUSD")

    assert any(event["type"] == "T1" for event in events)
    assert trade["status"] == "OPEN"
    assert calls == []


def test_full_gft_event_notifies_exactly_once(monkeypatch):
    from forex_engine.prop_firms.gft import gft_5k_2step

    trade = _forex_trade()
    calls = []

    class Connector:
        @staticmethod
        def get_price(symbol):
            return 33.0

    monkeypatch.setattr(
        gft_5k_2step, "load_state", lambda: _persisted_forex_state(trade)
    )
    monkeypatch.setattr(gft_5k_2step, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    events = gft_5k_2step._check_exits(Connector(), "XAGUSD")

    assert any(event["type"] == "T3" for event in events)
    assert trade["status"] == "CLOSED"
    assert len(calls) == 1


def test_manual_gft_event_notifies_exactly_once(monkeypatch):
    from forex_engine.prop_firms.gft import gft_5k_2step

    trade = _forex_trade()
    calls = []
    monkeypatch.setattr(
        gft_5k_2step, "load_state", lambda: _persisted_forex_state(trade)
    )
    monkeypatch.setattr(gft_5k_2step, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    event = gft_5k_2step.manual_exit_trade("T-1", 30.5)

    assert event["type"] == "MANUAL"
    assert trade["status"] == "CLOSED"
    assert len(calls) == 1


def test_failed_gft_save_attempt_skips_hermes(monkeypatch):
    from forex_engine.prop_firms.gft import gft_5k_2step

    trade = _forex_trade()
    stale_trade = copy.deepcopy(trade)
    states = iter([_forex_state(trade), _forex_state(stale_trade)])
    calls = []

    class Connector:
        @staticmethod
        def get_price(symbol):
            return 33.0

    monkeypatch.setattr(gft_5k_2step, "load_state", lambda: next(states))
    monkeypatch.setattr(gft_5k_2step, "_save", lambda state: None)
    monkeypatch.setattr(
        "utils.hermes_close_adapter.notify_hermes_trade_closed",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    events = gft_5k_2step._check_exits(Connector(), "XAGUSD")

    assert any(event["type"] == "T3" for event in events)
    assert calls == []
