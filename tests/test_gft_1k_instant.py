import importlib
import json
import os
import sys
import types

import pytest


def test_launcher_disabled_does_not_start_subprocess(monkeypatch):
    import forex_engine.forex_main as launcher

    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setenv("CB6_GFT_1K_INSTANT_ENABLED", "false")

    assert launcher.start_gft_1k_instant_worker() is None
    assert calls == []


def test_launcher_failure_is_nonfatal_and_uses_1k_error_log(tmp_path, monkeypatch):
    import forex_engine.forex_main as launcher

    def fail_popen(*args, **kwargs):
        raise RuntimeError("bad 1k terminal")

    env = os.environ.copy()
    env["CB6_GFT_1K_INSTANT_ENABLED"] = "true"
    env["CB6_GFT_1K_INSTANT_STRICT_STARTUP"] = "false"
    env["CB6_GFT_1K_INSTANT_STATE_DIR"] = str(tmp_path).replace("\\", "/")

    monkeypatch.setattr(launcher.subprocess, "Popen", fail_popen)
    assert launcher.start_gft_1k_instant_worker(env=env) is None

    error_log = tmp_path / "startup_error.log"
    assert error_log.exists()
    assert "bad 1k terminal" in error_log.read_text(encoding="utf-8")


def test_launcher_starts_1k_with_isolated_module_and_namespace(monkeypatch):
    import forex_engine.forex_main as launcher

    class FakeProc:
        pid = 100061

    seen = {}

    def fake_popen(cmd, cwd, env):
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        seen["env"] = env
        return FakeProc()

    env = os.environ.copy()
    env["CB6_GFT_1K_INSTANT_ENABLED"] = "true"
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    proc = launcher.start_gft_1k_instant_worker(env=env)

    assert proc.pid == 100061
    assert seen["cmd"][2] == "forex_engine.gft_1k_instant.monitor"
    assert seen["cmd"][-1] == "GFT_1K_INSTANT"


def test_account_registry_has_distinct_1k_boundaries():
    from forex_engine.accounts.account_registry import get_account, get_magic, get_state_dir

    ftmo = get_account("FTMO_10K")
    gft_5k = get_account("GFT_5K")
    gft_1k = get_account("GFT_1K_INSTANT")

    assert gft_1k["terminal_env"] == "GFT_1K_MT5_TERMINAL_PATH"
    assert gft_1k["login_env"] == "GFT_1K_MT5_LOGIN"
    assert gft_1k["server_env"] == "GFT_1K_MT5_SERVER"
    assert get_magic("GFT_1K_INSTANT") == 100061
    assert get_magic("GFT_1K_INSTANT") not in {
        ftmo["magic"],
        gft_5k["magic"],
    }
    assert "gft_1k_instant" in get_state_dir("GFT_1K_INSTANT").replace("\\", "/")
    assert get_state_dir("GFT_1K_INSTANT") not in {
        get_state_dir("FTMO_10K"),
        get_state_dir("GFT_5K"),
    }


def test_1k_config_defaults_and_live_gate(monkeypatch):
    monkeypatch.setenv("CB6_GFT_1K_INSTANT_ENABLED", "false")
    monkeypatch.setenv("CB6_GFT_1K_INSTANT_LIVE_EXECUTION", "true")

    import forex_engine.gft_1k_instant.config as cfg
    cfg = importlib.reload(cfg)

    assert cfg.GFT_1K_INSTANT_PROFILE["max_lot"] == 0.01
    assert cfg.GFT_1K_INSTANT_PROFILE["max_risk_usd"] == 2.50
    assert cfg.GFT_1K_INSTANT_PROFILE["magic"] == 100061
    assert cfg.live_execution_enabled() is False

    monkeypatch.setenv("CB6_GFT_1K_INSTANT_ENABLED", "true")
    monkeypatch.setenv("CB6_GFT_1K_INSTANT_LIVE_EXECUTION", "true")
    assert cfg.live_execution_enabled() is True


def test_1k_risk_blocks_missing_sl_tp_low_rr_and_big_lot():
    from forex_engine.gft_1k_instant.risk import validate_entry

    state = {"open_trades": [], "capital": 1000.0, "daily_snapshot": 1000.0}
    base_setup = {
        "entry_signal": {
            "entry": 10.0,
            "stop_loss": 9.0,
            "target2": 12.0,
            "rr_ratio": 2.0,
        }
    }

    assert validate_entry(base_setup, 0.01, 2.50, state) == (True, "OK")
    assert validate_entry({"entry_signal": {"target2": 12, "rr_ratio": 2}}, 0.01, 1, state)[0] is False
    assert validate_entry({"entry_signal": {"stop_loss": 9, "rr_ratio": 2}}, 0.01, 1, state)[0] is False
    assert validate_entry({"entry_signal": {"stop_loss": 9, "target2": 12, "rr_ratio": 1.4}}, 0.01, 1, state)[0] is False
    assert validate_entry(base_setup, 0.02, 2.50, state)[0] is False


def test_1k_adapter_requires_dedicated_terminal_for_live(monkeypatch):
    from forex_engine.accounts.gft_1k_instant_adapter import build_gft_1k_instant_connector

    monkeypatch.setattr(
        "forex_engine.accounts.gft_1k_instant_adapter.get_terminal_path",
        lambda account_id: None,
    )
    monkeypatch.setattr(
        "forex_engine.accounts.gft_1k_instant_adapter.get_credentials",
        lambda account_id: {
            "login": 1,
            "password": "x",
            "server": "GoatFunded-Server",
        },
    )

    with pytest.raises(RuntimeError, match="dedicated terminal path"):
        build_gft_1k_instant_connector(paper=False)


def test_1k_adapter_rejects_wrong_server(monkeypatch):
    import forex_engine.mt5.mt5_connector as mt5_connector
    from forex_engine.accounts.gft_1k_instant_adapter import build_gft_1k_instant_connector

    class FakeConnector:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_mt5 = types.SimpleNamespace(
        account_info=lambda: types.SimpleNamespace(server="Wrong-Server"),
        shutdown=lambda: None,
    )

    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(mt5_connector, "MT5Connector", FakeConnector)
    monkeypatch.setattr(
        "forex_engine.accounts.gft_1k_instant_adapter.get_terminal_path",
        lambda account_id: r"C:\CB6_MT5\MT5_GFT_1K\terminal64.exe",
    )
    monkeypatch.setattr(
        "forex_engine.accounts.gft_1k_instant_adapter.get_credentials",
        lambda account_id: {
            "login": 1,
            "password": "x",
            "server": "GoatFunded-Server",
        },
    )

    with pytest.raises(RuntimeError, match="SERVER MISMATCH"):
        build_gft_1k_instant_connector(paper=False)


def test_1k_modules_do_not_import_ftmo_or_gft_5k_state():
    module_names = [
        "forex_engine/gft_1k_instant/config.py",
        "forex_engine/gft_1k_instant/state.py",
        "forex_engine/gft_1k_instant/risk.py",
        "forex_engine/gft_1k_instant/monitor.py",
        "forex_engine/gft_1k_instant/telegram_bot.py",
    ]
    for module_name in module_names:
        source = open(module_name, encoding="utf-8").read()
        assert "forex_engine.prop_firms.ftmo" not in source
        assert "forex_engine.prop_firms.gft.gft_phase_tracker" not in source
        assert "communications.forex_bot" not in source
        assert "communications.gft_bot" not in source
        assert "TELEGRAM_BOT_TOKEN_FTMO" not in source
        assert "TELEGRAM_BOT_TOKEN_GFT" not in source
        assert "data/ftmo_10k" not in source
        assert "data/gft_5k" not in source


def _reload_telegram_with_tmp_state(monkeypatch, tmp_path):
    import forex_engine.gft_1k_instant.config as cfg
    state_dir = tmp_path / "gft_1k_instant"
    monkeypatch.setenv("CB6_GFT_1K_INSTANT_STATE_DIR", str(state_dir).replace("\\", "/"))
    importlib.reload(cfg)

    import forex_engine.gft_1k_instant.state as state_module
    importlib.reload(state_module)

    import forex_engine.gft_1k_instant.telegram_bot as telegram_bot
    importlib.reload(telegram_bot)
    return telegram_bot, state_module


def test_telegram_disabled_by_default(monkeypatch, tmp_path):
    telegram_bot, _ = _reload_telegram_with_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv("GFT_1K_TELEGRAM_ENABLED", raising=False)

    assert telegram_bot.is_enabled() is False
    assert telegram_bot.start_background_listener() is False


def test_wrong_admin_id_cannot_control_bot(monkeypatch, tmp_path):
    telegram_bot, state_module = _reload_telegram_with_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv("GFT_1K_TELEGRAM_ADMIN_IDS", "111")
    sent = []
    monkeypatch.setattr(telegram_bot, "_safe_send", lambda text, parse_mode="HTML": sent.append(text))

    result = telegram_bot.handle_update(
        {
            "message": {
                "text": "/lock",
                "chat": {"id": 999},
                "from": {"id": 222},
            }
        }
    )

    assert result is None
    assert sent == []
    assert state_module.load_lock_state()["locked"] is False


def test_lock_updates_only_gft_1k_lock_state(monkeypatch, tmp_path):
    telegram_bot, state_module = _reload_telegram_with_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv("GFT_1K_TELEGRAM_ADMIN_IDS", "8483421900")
    monkeypatch.setattr(telegram_bot, "_safe_send", lambda text, parse_mode="HTML": True)

    telegram_bot.handle_update(
        {
            "message": {
                "text": "/lock",
                "chat": {"id": 8483421900},
                "from": {"id": 8483421900},
            }
        }
    )

    assert state_module.load_lock_state()["locked"] is True
    assert "gft_1k_instant" in state_module.LOCK_STATE_FILE.replace("\\", "/")
    assert "ftmo_10k" not in state_module.LOCK_STATE_FILE.replace("\\", "/")
    assert "gft_5k" not in state_module.LOCK_STATE_FILE.replace("\\", "/")


def test_unlock_updates_only_gft_1k_lock_state_and_audit(monkeypatch, tmp_path):
    telegram_bot, state_module = _reload_telegram_with_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv("GFT_1K_TELEGRAM_ADMIN_IDS", "8483421900")
    monkeypatch.setattr(telegram_bot, "_safe_send", lambda text, parse_mode="HTML": True)
    state_module.save_lock_state({"locked": True, "dry_run": True})

    telegram_bot.handle_update(
        {
            "message": {
                "text": "/unlock",
                "chat": {"id": 8483421900},
                "from": {"id": 8483421900},
            }
        }
    )

    assert state_module.load_lock_state()["locked"] is False
    audit_lines = [
        json.loads(line)
        for line in open(state_module.TELEGRAM_AUDIT_FILE, encoding="utf-8")
    ]
    assert any(row["event"] == "unlock" for row in audit_lines)
    assert "gft_1k_instant" in state_module.TELEGRAM_AUDIT_FILE.replace("\\", "/")


def test_alerts_go_only_to_gft_1k_chat_id(monkeypatch, tmp_path):
    telegram_bot, _ = _reload_telegram_with_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv("GFT_1K_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("GFT_1K_TELEGRAM_BOT_TOKEN", "123456:SECRET")
    monkeypatch.setenv("GFT_1K_TELEGRAM_CHAT_ID", "8483421900")

    calls = []
    monkeypatch.setattr(
        telegram_bot,
        "send_message",
        lambda token, chat_id, text, parse_mode, logger: calls.append(
            {
                "token": token,
                "chat_id": chat_id,
                "text": text,
            }
        ) or True,
    )

    assert telegram_bot.send_alert("startup", "hello") is True
    assert calls == [
        {
            "token": "123456:SECRET",
            "chat_id": "8483421900",
            "text": "<b>GFT 1K Instant - Startup</b>\nhello",
        }
    ]


def test_no_secrets_are_logged_on_start(monkeypatch, tmp_path):
    telegram_bot, _ = _reload_telegram_with_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv("GFT_1K_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("GFT_1K_TELEGRAM_BOT_TOKEN", "1234567890:VERY_SECRET_TOKEN")
    monkeypatch.setenv("GFT_1K_TELEGRAM_CHAT_ID", "8483421900")
    monkeypatch.setenv("GFT_1K_TELEGRAM_ADMIN_IDS", "8483421900")
    logged = []
    monkeypatch.setattr(telegram_bot.logger, "info", lambda msg: logged.append(msg))
    monkeypatch.setattr(telegram_bot, "_get_updates", lambda: [])
    monkeypatch.setattr(telegram_bot.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        telegram_bot.start_listening()

    joined = "\n".join(logged)
    assert "VERY_SECRET_TOKEN" not in joined
    assert "1234567890:VERY_SECRET_TOKEN" not in joined
    assert "12345678***" in joined
