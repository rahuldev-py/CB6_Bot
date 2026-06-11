from typing import Optional
from utils.logger import logger
from forex_engine.accounts.account_registry import get_credentials, get_terminal_path, is_paper


def _probe_gft_10k_symbols(terminal_path: str, credentials: dict) -> dict:
    """
    Probe GFT $10K terminal (GoatFunded-Server3) for correct broker symbol names.
    Server3 uses .x suffix for metals and WTI.x for oil.
    Falls back to known-good hardcoded map if probe fails.
    """
    KNOWN_GOOD = {
        "XAGUSD"    : "XAGUSD.x",
        "XAUUSD"    : "XAUUSD.x",
        "USOIL.cash": "WTI.x",
    }
    CANDIDATES = {
        "XAUUSD"    : ["XAUUSD.x",   "XAUUSD",   "GOLD"],
        "XAGUSD"    : ["XAGUSD.x",   "XAGUSD",   "SILVER"],
        "USOIL.cash": ["WTI.x",      "USOIL",    "WTI",   "OIL", "USOIL.cash"],
    }
    overrides = {}
    try:
        import MetaTrader5 as mt5
        ok = mt5.initialize(
            path=terminal_path, login=credentials["login"],
            password=credentials["password"], server=credentials["server"],
        )
        if not ok:
            logger.warning("[GFT_10K] symbol probe: MT5 init failed — using known-good Server3 map")
            return KNOWN_GOOD
        for canonical, candidates in CANDIDATES.items():
            found = next((s for s in candidates if mt5.symbol_info(s) is not None), None)
            if found and found != canonical:
                overrides[canonical] = found
                logger.info(f"[GFT_10K] symbol probe: {canonical} → {found}")
            elif not found:
                logger.warning(f"[GFT_10K] symbol probe: no match for {canonical}")
        # Do NOT call mt5.shutdown() here — same reason as GFT_1K adapter:
        # leaving the session alive lets MT5Connector._connect() reuse it.
    except Exception as e:
        logger.warning(f"[GFT_10K] symbol probe failed ({e}) — using known-good Server3 map")
        return KNOWN_GOOD
    return overrides


def build_gft_10k_connector(paper: Optional[bool] = None):
    from forex_engine.mt5.mt5_connector import MT5Connector

    account_id = "GFT_10K"

    if paper is None:
        paper = is_paper(account_id)

    if paper:
        logger.info(f"[{account_id}] Paper mode — building paper MT5Connector")
        return MT5Connector(paper=True)

    import os as _os

    # Resolve terminal path directly from env var — do NOT gate on os.path.isfile().
    # Same fix as GFT_1K: the pre-check intermittently fails on Windows during MT5 init.
    # mt5.initialize() is the real gatekeeper.
    _FALLBACK_PATH = "C:/CB6_MT5/MT5_GFT_10K/terminal64.exe"
    _env_val  = _os.getenv("GFT_10K_MT5_TERMINAL_PATH", "").strip()
    _raw_path = _env_val or _FALLBACK_PATH
    terminal_path = _raw_path.replace("/", _os.sep)

    logger.info(
        f"[{account_id}] terminal path resolved: {terminal_path!r} "
        f"(exists={_os.path.isfile(terminal_path)})"
    )

    credentials = get_credentials(account_id)
    if not credentials:
        raise RuntimeError(f"[{account_id}] credentials missing — check .env")

    # GoatFunded-Server3 uses .x suffix — probe to confirm exact names
    # (same server as 5K; WTI.x is oil, XAUUSD.x/XAGUSD.x for metals)
    symbol_overrides = _probe_gft_10k_symbols(terminal_path, credentials)

    logger.info(
        f"[{account_id}] Building LIVE connector — terminal={terminal_path!r} "
        f"login={credentials['login']} server={credentials['server']}"
    )
    connector = MT5Connector(
        paper=False,
        credentials=credentials,
        terminal_path=terminal_path,
        symbol_overrides=symbol_overrides,
    )
    return connector
