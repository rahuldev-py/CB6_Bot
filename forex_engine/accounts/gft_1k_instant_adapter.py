from typing import Optional

from utils.logger import logger
from forex_engine.accounts.account_registry import (
    get_credentials,
    get_terminal_path,
    is_paper,
)


def _probe_gft_1k_symbols(terminal_path: str, credentials: dict) -> dict:
    """
    Query the GFT $1K MT5 terminal to find the correct broker symbol names.
    GoatFunded-Server (1K) differs from Server3 (5K/10K): no .x suffix.

    Candidates tried per logical symbol:
      XAUUSD    : XAUUSD, XAUUSD.x, GOLD, XAU
      XAGUSD    : XAGUSD, XAGUSD.x, SILVER, XAG
      USOIL.cash: USOIL, WTI, OIL, USOIL.cash, WTI.x

    Returns a symbol_overrides dict for MT5Connector — only maps entries
    where the canonical name differs from the discovered broker name.
    """
    overrides = {}
    CANDIDATES = {
        "XAUUSD"    : ["XAUUSD",     "XAUUSD.x",   "GOLD",  "XAU"],
        "XAGUSD"    : ["XAGUSD",     "XAGUSD.x",   "SILVER","XAG"],
        "USOIL.cash": ["USOIL",      "WTI",         "OIL",   "USOIL.cash", "WTI.x"],
    }
    try:
        import MetaTrader5 as mt5
        ok = mt5.initialize(
            path     = terminal_path,
            login    = credentials["login"],
            password = credentials["password"],
            server   = credentials["server"],
        )
        if not ok:
            logger.warning(f"[GFT_1K] symbol probe: MT5 init failed — using plain names fallback")
            return {"USOIL.cash": "USOIL"}

        for canonical, candidates in CANDIDATES.items():
            found = None
            for sym in candidates:
                info = mt5.symbol_info(sym)
                if info is not None:
                    found = sym
                    break
            if found is None:
                logger.warning(f"[GFT_1K] symbol probe: no match for {canonical} — tried {candidates}")
            elif found != canonical:
                overrides[canonical] = found
                logger.info(f"[GFT_1K] symbol probe: {canonical} → {found}")
            else:
                logger.info(f"[GFT_1K] symbol probe: {canonical} → {found} (plain, no override needed)")

        # Do NOT call mt5.shutdown() here — it logs out the broker session.
        # MT5Connector._connect() calls mt5.initialize() next; if the session is
        # already open with the same credentials it returns True immediately and
        # reuses the connection without triggering a second broker login.
    except Exception as e:
        logger.warning(f"[GFT_1K] symbol probe failed ({e}) — falling back to plain names")
        return {"USOIL.cash": "USOIL"}

    return overrides


def build_gft_1k_instant_connector(paper: Optional[bool] = None):
    from forex_engine.mt5.mt5_connector import MT5Connector

    account_id = "GFT_1K_INSTANT"

    if paper is None:
        paper = is_paper(account_id)

    if paper:
        logger.info(f"[{account_id}] Paper mode - building paper MT5Connector")
        return MT5Connector(paper=True)

    import os as _os

    # Resolve terminal path directly from env var — do NOT gate on os.path.isfile().
    # The pre-check was intermittently returning False on Windows even when the file
    # exists (MT5 terminal initialising, OS file-system flush, anti-virus scan).
    # mt5.initialize() is the real gatekeeper; let it report any actual I/O failure.
    _FALLBACK_PATH = "C:/CB6_MT5/MT5_GFT_1K/terminal64.exe"
    _env_val  = _os.getenv("GFT_1K_MT5_TERMINAL_PATH", "").strip()
    _raw_path = _env_val or _FALLBACK_PATH
    terminal_path = _raw_path.replace("/", _os.sep)

    logger.info(
        f"[{account_id}] terminal path resolved: {terminal_path!r} "
        f"(exists={_os.path.isfile(terminal_path)})"
    )

    credentials = get_credentials(account_id)
    if not credentials:
        _send_telegram_alert(
            "wrong_account_server_block",
            f"[{account_id}] live startup blocked: credentials missing in .env",
        )
        raise RuntimeError(f"[{account_id}] credentials missing")

    # GoatFunded-Server (no suffix) uses plain symbol names unlike Server3 (.x suffix).
    # Probe the terminal to find the exact broker symbol names.
    symbol_overrides = _probe_gft_1k_symbols(terminal_path, credentials)

    logger.info(
        f"[{account_id}] Building LIVE connector - terminal={terminal_path!r} "
        f"login={credentials['login']} server={credentials['server']}"
    )
    connector = MT5Connector(
        paper=False,
        credentials=credentials,
        terminal_path=terminal_path,
        symbol_overrides=symbol_overrides,
    )
    connector.set_alert_callback(
        lambda msg: _send_telegram_alert("mt5_connected", f"[{account_id}] {msg}")
    )
    _send_telegram_alert(
        "mt5_connected",
        f"[{account_id}] MT5 connected login={credentials['login']} server={credentials['server']}",
    )
    return connector


def _send_telegram_alert(alert_type: str, message: str) -> None:
    try:
        from forex_engine.gft_1k_instant.telegram_bot import send_alert
        send_alert(alert_type, message)
    except Exception:
        pass


def _validate_connected_server(account_id: str, credentials: dict) -> None:
    """
    Validate the MT5 terminal is connected to the expected server.

    Bypass: set CB6_GFT_1K_SKIP_SERVER_CHECK=true in .env when the broker
    reports a different server string than what is stored in GFT_1K_MT5_SERVER
    (e.g. "GoatFunded-Server" vs "GoatFunded-Live").
    """
    import os as _os
    if _os.getenv("CB6_GFT_1K_SKIP_SERVER_CHECK", "false").lower() == "true":
        logger.info(f"[{account_id}] server check skipped (CB6_GFT_1K_SKIP_SERVER_CHECK=true)")
        return

    try:
        import MetaTrader5 as mt5
        import time as _time
    except ImportError:
        return

    expected_server = str(credentials.get("server", "") or "").strip().lower()
    # Retry up to 5 times with 8s gaps (40s total) — MT5 may still be
    # authenticating after initialize(); GoatFunded auth can take ~20-30s.
    connected_server = ""
    for attempt in range(5):
        info = mt5.account_info()
        if info:
            connected_server = str(getattr(info, "server", "") or "").strip().lower()
            if not connected_server or connected_server == expected_server:
                if connected_server:
                    logger.info(f"[{account_id}] server check OK: {connected_server!r}")
                return
        logger.info(
            f"[{account_id}] server check attempt {attempt+1}/5 — "
            f"expected={expected_server!r} connected={connected_server!r}"
        )
        if attempt < 4:
            _time.sleep(8)

    if not connected_server:
        # Terminal authenticated but hasn't returned server name yet — allow.
        logger.info(f"[{account_id}] server not yet reported — allowing (MT5Connector login guard covers this)")
        return

    # NOTE: do NOT call mt5.shutdown() here — it kills the shared MT5 process
    # and corrupts the GFT $5K terminal connection running in parallel.
    _send_telegram_alert(
        "wrong_account_server_block",
        f"[{account_id}] SERVER MISMATCH: requested server={expected_server}, connected server={connected_server}\n"
        f"Set CB6_GFT_1K_SKIP_SERVER_CHECK=true in .env to bypass if server name differs.",
    )
    raise RuntimeError(
        f"[{account_id}] SERVER MISMATCH: requested server={expected_server}, "
        f"connected server={connected_server}"
    )
