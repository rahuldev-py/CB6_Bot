"""
CB6 dependency and broker runtime safety check.

Read-only diagnostics only:
- No trades
- No order placement imports/calls
- No package installs/upgrades
- No ML config changes

Usage:
    python dependency_safety_check.py
    python dependency_safety_check.py --skip-websocket
    python dependency_safety_check.py --json reports/dependency_safety_latest.json
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as metadata
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent
DEFAULT_FYERS_SYMBOL = "NSE:NIFTY50-INDEX"
DEFAULT_MT5_SYMBOLS = ("XAGUSD", "XAUUSD", "USOIL", "EURUSD")


def _load_env() -> dict[str, str]:
    env = dict(dotenv_values(ROOT / ".env"))
    for key, value in env.items():
        if value is not None and key not in os.environ:
            os.environ[key] = value
    return {k: v or "" for k, v in env.items()}


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def _result(name: str, status: str, detail: str = "", data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "data": data or {},
    }


def _version(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except Exception:
        return "not-installed"


def check_package_versions() -> dict[str, Any]:
    packages = [
        "fyers-apiv3",
        "truedata-ws",
        "MetaTrader5",
        "requests",
        "aiohttp",
        "websocket-client",
        "setuptools",
        "aws-lambda-powertools",
        "lz4",
        "torch",
        "scikit-learn",
    ]
    versions = {pkg: _version(pkg) for pkg in packages}

    conflicts: list[str] = []
    try:
        import subprocess

        completed = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            conflicts = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            conflicts += [line.strip() for line in completed.stderr.splitlines() if line.strip()]
    except Exception as exc:
        conflicts = [f"pip check failed: {exc}"]

    status = "PASS" if not conflicts else "WARN"
    detail = "No dependency conflicts reported" if not conflicts else f"{len(conflicts)} conflict(s) reported"
    return _result("dependency_versions", status, detail, {"versions": versions, "conflicts": conflicts})


def check_imports() -> dict[str, Any]:
    modules = {
        "fyers_apiv3": "Fyers SDK",
        "truedata_ws": "TrueData SDK",
        "MetaTrader5": "MT5 SDK",
        "websocket": "websocket-client",
        "requests": "requests",
        "aiohttp": "aiohttp",
    }
    loaded = {}
    failures = {}
    for module_name in modules:
        try:
            importlib.import_module(module_name)
            loaded[module_name] = True
        except Exception as exc:
            loaded[module_name] = False
            failures[module_name] = repr(exc)

    status = "PASS" if not failures else "FAIL"
    detail = "All broker/runtime modules import" if not failures else f"{len(failures)} import failure(s)"
    return _result("broker_runtime_imports", status, detail, {"loaded": loaded, "failures": failures})


def _make_fyers(env: dict[str, str]):
    from fyers_apiv3 import fyersModel

    client_id = env.get("CLIENT_ID") or os.getenv("CLIENT_ID", "")
    token = env.get("ACCESS_TOKEN") or os.getenv("ACCESS_TOKEN", "")
    if ":" in token:
        token = token.split(":", 1)[1]
    if not client_id or not token:
        raise RuntimeError("CLIENT_ID or ACCESS_TOKEN missing in .env")
    return fyersModel.FyersModel(
        client_id=client_id,
        token=token,
        is_async=False,
        log_path=str(ROOT / "logs" / ""),
    )


def check_fyers_login(env: dict[str, str]) -> dict[str, Any]:
    try:
        fyers = _make_fyers(env)
        profile = fyers.get_profile()
        ok = profile.get("code") == 200 or profile.get("s") == "ok"
        data = profile.get("data") if isinstance(profile.get("data"), dict) else {}
        safe_profile = {
            "code": profile.get("code"),
            "s": profile.get("s"),
            "name": data.get("name"),
            "fy_id": _mask(str(data.get("fy_id", ""))),
        }
        return _result(
            "fyers_login",
            "PASS" if ok else "FAIL",
            "Fyers profile accepted token" if ok else f"Fyers profile failed: {profile}",
            safe_profile,
        )
    except Exception as exc:
        return _result("fyers_login", "FAIL", repr(exc))


def check_fyers_quote(env: dict[str, str], symbol: str = DEFAULT_FYERS_SYMBOL) -> dict[str, Any]:
    try:
        fyers = _make_fyers(env)
        resp = fyers.quotes({"symbols": symbol})
        ok = resp.get("code") == 200 or resp.get("s") == "ok"
        ltp = None
        if resp.get("d"):
            ltp = resp["d"][0].get("v", {}).get("lp")
        return _result(
            "fyers_quote_fetch",
            "PASS" if ok and ltp is not None else "FAIL",
            f"{symbol} LTP={ltp}" if ok else f"Quote failed: {resp}",
            {"symbol": symbol, "ltp": ltp, "code": resp.get("code"), "s": resp.get("s")},
        )
    except Exception as exc:
        return _result("fyers_quote_fetch", "FAIL", repr(exc), {"symbol": symbol})


def check_fyers_websocket(env: dict[str, str], symbol: str = DEFAULT_FYERS_SYMBOL, timeout_sec: int = 12) -> dict[str, Any]:
    try:
        from fyers_apiv3.FyersWebsocket import data_ws

        client_id = env.get("CLIENT_ID") or os.getenv("CLIENT_ID", "")
        token = env.get("ACCESS_TOKEN") or os.getenv("ACCESS_TOKEN", "")
        if not client_id or not token:
            return _result("fyers_websocket", "SKIP", "CLIENT_ID or ACCESS_TOKEN missing")
        token_str = token if ":" in token else f"{client_id}:{token}"

        events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        client_holder: dict[str, Any] = {}

        def on_message(message):
            events.put(("message", message))

        def on_error(error):
            events.put(("error", str(error)))

        def on_close(reason):
            events.put(("close", str(reason)))

        def on_connect():
            events.put(("connect", "connected"))
            try:
                client_holder["client"].subscribe(symbols=[symbol], data_type="SymbolUpdate")
                events.put(("subscribe", symbol))
            except Exception as exc:
                events.put(("error", f"subscribe failed: {exc}"))

        client = data_ws.FyersDataSocket(
            access_token=token_str,
            log_path=str(ROOT / "logs"),
            litemode=True,
            write_to_file=False,
            reconnect=False,
            on_connect=on_connect,
            on_close=on_close,
            on_error=on_error,
            on_message=on_message,
        )
        client_holder["client"] = client
        thread = threading.Thread(target=client.connect, daemon=True, name="CB6DependencyFyersWS")
        thread.start()

        seen: list[tuple[str, Any]] = []
        deadline = time.time() + timeout_sec
        connected = False
        subscribed = False
        tick = None
        error = None
        while time.time() < deadline:
            try:
                event, payload = events.get(timeout=0.5)
                seen.append((event, payload))
                if event == "connect":
                    connected = True
                elif event == "subscribe":
                    subscribed = True
                elif event == "message":
                    tick = payload
                    break
                elif event == "error":
                    error = payload
                    break
            except queue.Empty:
                continue

        try:
            client.close_connection()
        except Exception:
            pass

        if error:
            return _result("fyers_websocket", "FAIL", str(error), {"symbol": symbol, "events": seen[-5:]})
        if tick:
            return _result("fyers_websocket", "PASS", "Connected, subscribed, and received a tick", {"symbol": symbol, "tick": tick})
        if connected and subscribed:
            return _result(
                "fyers_websocket",
                "WARN",
                "Connected and subscribed, but no tick arrived before timeout. If market is closed, this is expected.",
                {"symbol": symbol, "events": seen[-5:]},
            )
        if connected:
            return _result("fyers_websocket", "WARN", "Connected, but subscribe/tick not confirmed before timeout", {"symbol": symbol, "events": seen[-5:]})
        return _result("fyers_websocket", "FAIL", "No websocket connect event before timeout", {"symbol": symbol, "events": seen[-5:]})
    except Exception as exc:
        return _result("fyers_websocket", "FAIL", repr(exc), {"symbol": symbol})


def check_truedata(env: dict[str, str], timeout_sec: int = 15) -> dict[str, Any]:
    user = env.get("TRUEDATA_USER") or os.getenv("TRUEDATA_USER", "")
    password = env.get("TRUEDATA_PASSWORD") or os.getenv("TRUEDATA_PASSWORD", "")
    if not user or not password:
        return _result("truedata_connection", "SKIP", "TRUEDATA_USER/TRUEDATA_PASSWORD not configured")

    try:
        from truedata_ws.websocket.TD import TD
    except Exception as exc:
        return _result("truedata_connection", "FAIL", f"TrueData import failed: {exc}")

    events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
    holder: dict[str, Any] = {}

    def worker():
        try:
            # Constructor connects. Keep this as live data only; no subscriptions are started here.
            td = TD(user, password, historical_api=False, full_feed=False)
            holder["td"] = td
            events.put(("created", "TD object created"))
            live = getattr(td, "live_websocket", None)
            sub_type = getattr(live, "subscription_type", "") if live is not None else ""
            events.put(("subscription_type", sub_type))
        except Exception as exc:
            events.put(("error", repr(exc)))

    thread = threading.Thread(target=worker, daemon=True, name="CB6DependencyTrueData")
    thread.start()
    thread.join(timeout=timeout_sec)

    try:
        if holder.get("td") is not None:
            holder["td"].disconnect()
    except Exception:
        pass

    seen = []
    while not events.empty():
        seen.append(events.get())

    errors = [payload for event, payload in seen if event == "error"]
    if errors:
        return _result("truedata_connection", "FAIL", str(errors[-1]), {"events": seen})
    if thread.is_alive():
        return _result("truedata_connection", "WARN", "TrueData connect did not complete before timeout", {"events": seen})
    if seen:
        return _result("truedata_connection", "PASS", "TrueData client created/connect returned", {"events": seen})
    return _result("truedata_connection", "WARN", "TrueData check ended without events")


def check_mt5(env: dict[str, str]) -> dict[str, Any]:
    try:
        import MetaTrader5 as mt5
    except Exception as exc:
        return _result("mt5_connection", "FAIL", f"MetaTrader5 import failed: {exc}")

    login = env.get("MT5_LOGIN") or os.getenv("MT5_LOGIN", "")
    password = env.get("MT5_PASSWORD") or os.getenv("MT5_PASSWORD", "")
    server = env.get("MT5_SERVER") or os.getenv("MT5_SERVER", "")
    if not login or not password or not server:
        return _result("mt5_connection", "SKIP", "MT5_LOGIN/MT5_PASSWORD/MT5_SERVER not configured")

    try:
        ok = mt5.initialize(login=int(login), password=password, server=server)
        if not ok:
            return _result("mt5_connection", "FAIL", f"mt5.initialize failed: {mt5.last_error()}")
        info = mt5.account_info()
        terminal = mt5.terminal_info()
        tick_data = {}
        for symbol in DEFAULT_MT5_SYMBOLS:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                tick_data[symbol] = {"bid": tick.bid, "ask": tick.ask}
                break
        return _result(
            "mt5_connection",
            "PASS" if info else "FAIL",
            "MT5 account_info returned" if info else "MT5 initialized but account_info is empty",
            {
                "login": getattr(info, "login", None),
                "server": getattr(info, "server", None),
                "balance": getattr(info, "balance", None),
                "terminal_connected": getattr(terminal, "connected", None),
                "sample_tick": tick_data,
            },
        )
    except Exception as exc:
        return _result("mt5_connection", "FAIL", repr(exc))
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def check_engine_imports() -> list[dict[str, Any]]:
    checks = []
    for name, module_name in [
        ("nse_engine_import", "main"),
        ("forex_entry_import", "forex_engine.forex_main"),
        ("forex_worker_import", "forex_engine.forex_worker"),
    ]:
        try:
            importlib.import_module(module_name)
            checks.append(_result(name, "PASS", f"Imported {module_name} without starting engine"))
        except Exception as exc:
            checks.append(_result(name, "FAIL", f"{module_name} import failed: {repr(exc)}"))
    return checks


def recommend_env_split() -> dict[str, Any]:
    return {
        "cb6_live_env": {
            "purpose": "Live trading runtime only: Fyers / TrueData / MT5 / execution",
            "rules": [
                "Use broker-compatible pinned versions.",
                "Do not install torch/scikit-learn here.",
                "Run NSE/Forex live engines only from this env.",
            ],
            "known_pins_from_current_conflicts": {
                "fyers-apiv3": "3.1.12",
                "aiohttp": "3.9.3",
                "aws-lambda-powertools": "1.25.5",
                "requests": "2.31.0",
                "setuptools": "68.0.0",
                "websocket-client": "1.6.1",
                "truedata-ws": "5.0.11",
                "lz4": "3.1.3",
                "MetaTrader5": "5.0.5735",
            },
        },
        "cb6_ml_env": {
            "purpose": "ML training/research only: Torch / sklearn / feature building",
            "rules": [
                "No broker login required.",
                "No live engine starts.",
                "Can install torch/scikit-learn without risking broker SDK pins.",
            ],
            "suggested_core": {
                "torch": "installed in current env as 2.12.0+cpu",
                "scikit-learn": "installed in current env as 1.8.0",
                "pandas": "match project data pipeline",
                "numpy": "match torch/sklearn compatibility",
            },
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    env = _load_env()
    results: list[dict[str, Any]] = []

    results.append(check_package_versions())
    results.append(check_imports())
    results.append(check_fyers_login(env))
    results.append(check_fyers_quote(env, args.fyers_symbol))
    if args.skip_websocket:
        results.append(_result("fyers_websocket", "SKIP", "Skipped by --skip-websocket"))
    else:
        results.append(check_fyers_websocket(env, args.fyers_symbol, args.websocket_timeout))
    results.append(check_truedata(env, args.truedata_timeout))
    results.append(check_mt5(env))
    results.extend(check_engine_imports())

    summary = {
        "generated_at": datetime.now().isoformat(),
        "python": sys.version,
        "root": str(ROOT),
        "checks": results,
        "environment_recommendation": recommend_env_split(),
    }
    return summary


def print_report(report: dict[str, Any]) -> None:
    print("\nCB6 Dependency Safety Check")
    print("=" * 72)
    for item in report["checks"]:
        print(f"[{item['status']:<4}] {item['name']}: {item['detail']}")
        if item["name"] == "dependency_versions" and item["data"].get("conflicts"):
            for conflict in item["data"]["conflicts"]:
                print(f"       - {conflict}")
    print("=" * 72)
    hard_fail = [x for x in report["checks"] if x["status"] == "FAIL"]
    warn = [x for x in report["checks"] if x["status"] == "WARN"]
    print(f"PASS={sum(x['status'] == 'PASS' for x in report['checks'])}  WARN={len(warn)}  FAIL={len(hard_fail)}  SKIP={sum(x['status'] == 'SKIP' for x in report['checks'])}")
    print("\nRecommended split:")
    print("  cb6_live_env -> Fyers / TrueData / MT5 / execution with broker-compatible pins")
    print("  cb6_ml_env   -> Torch / sklearn / ML training only")
    print("Live NSE/Forex should run from cb6_live_env after broker checks pass.")


def main() -> int:
    parser = argparse.ArgumentParser(description="CB6 broker dependency safety check")
    parser.add_argument("--fyers-symbol", default=DEFAULT_FYERS_SYMBOL)
    parser.add_argument("--websocket-timeout", type=int, default=12)
    parser.add_argument("--truedata-timeout", type=int, default=15)
    parser.add_argument("--skip-websocket", action="store_true")
    parser.add_argument("--json", metavar="PATH", help="Optional report JSON path")
    args = parser.parse_args()

    report = run(args)
    print_report(report)

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved JSON report: {path}")

    return 1 if any(x["status"] == "FAIL" for x in report["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
