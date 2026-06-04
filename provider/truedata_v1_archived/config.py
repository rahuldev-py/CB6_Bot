"""
TrueData provider configuration.

Loads credentials and runtime settings from a .env file (or OS environment),
with sane defaults for sandbox vs live environments.

Environment variables expected:
    TRUEDATA_USER       — TrueData login username
    TRUEDATA_PASSWORD   — TrueData login password
    TRUEDATA_ENV        — "sandbox" or "live" (default: "sandbox")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WS_HOST = "push.truedata.in"
_WS_PORT_SANDBOX = 8082
_WS_PORT_LIVE = 8086
_REST_BASE = "https://api.truedata.in"
_HISTORY_BASE = "https://history.truedata.in"

_DEFAULT_TRIAL_SYMBOLS = [
    "NIFTY-I",
    "BANKNIFTY-I",
    "FINNIFTY-I",
    "MIDCPNIFTY-I",
]
_DEFAULT_OPTION_UNDERLYINGS = ["NIFTY", "BANKNIFTY"]


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class TrueDataConfig:
    """
    Immutable configuration for the TrueData provider.

    Never log the ``password`` field directly; use the ``masked_password``
    property instead.
    """

    user: str
    password: str
    env: str  # "sandbox" or "live"
    ws_host: str
    ws_port: int
    rest_base_url: str
    history_base_url: str
    trial_duration_minutes: int
    trial_symbols: list[str]
    option_underlyings: list[str]
    option_strike_range: int
    data_dir: Path
    log_dir: Path

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def ws_url(self) -> str:
        """Full WebSocket URL, e.g. ``wss://push.truedata.in:8082``."""
        return f"wss://{self.ws_host}:{self.ws_port}"

    @property
    def masked_password(self) -> str:
        """Password with everything after the first 4 chars replaced by ****."""
        if len(self.password) <= 4:
            return "****"
        return self.password[:4] + "****"

    @property
    def masked_user(self) -> str:
        """Username with everything after the first 4 chars replaced by ****."""
        if len(self.user) <= 4:
            return self.user[:4] + "****"
        return self.user[:4] + "****"

    def __repr__(self) -> str:  # never expose password in repr
        return (
            f"TrueDataConfig(user={self.masked_user!r}, env={self.env!r}, "
            f"ws_url={self.ws_url!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(
    env_path: str = ".env",
    override: Optional[dict] = None,
) -> TrueDataConfig:
    """
    Build a :class:`TrueDataConfig` from environment variables.

    Resolution order (highest priority first):

    1. ``override`` dict (for testing)
    2. OS environment (``os.environ``)
    3. ``.env`` file at ``env_path``

    Parameters
    ----------
    env_path:
        Path to the .env file.  Relative paths are resolved from the
        current working directory.
    override:
        Optional dict of key→value pairs that take precedence over
        everything else.  Used in unit tests.

    Returns
    -------
    TrueDataConfig
        A fully populated config object.

    Raises
    ------
    ValueError
        If ``TRUEDATA_USER`` or ``TRUEDATA_PASSWORD`` are not set.
    """
    # Load .env file values (does NOT override os.environ)
    env_file_values: dict[str, str | None] = {}
    env_file_path = Path(env_path)
    if env_file_path.exists():
        env_file_values = dotenv_values(env_file_path)
        logger.debug("Loaded .env from %s", env_file_path.resolve())
    else:
        logger.debug(".env not found at %s — relying on OS environment", env_file_path)

    def _get(key: str, default: str | None = None) -> str | None:
        """Resolve a config key with priority: override > os.environ > .env."""
        if override and key in override:
            return str(override[key])
        if key in os.environ:
            return os.environ[key]
        if key in env_file_values and env_file_values[key] is not None:
            return env_file_values[key]
        return default

    # Mandatory credentials
    user = _get("TRUEDATA_USER")
    password = _get("TRUEDATA_PASSWORD")

    if not user:
        raise ValueError(
            "TRUEDATA_USER is not set. "
            "Add it to .env or set the environment variable."
        )
    if not password:
        raise ValueError(
            "TRUEDATA_PASSWORD is not set. "
            "Add it to .env or set the environment variable."
        )

    env_name = (_get("TRUEDATA_ENV") or "sandbox").lower().strip()
    if env_name not in ("sandbox", "live"):
        logger.warning(
            "TRUEDATA_ENV='%s' is not recognised; defaulting to 'sandbox'", env_name
        )
        env_name = "sandbox"

    ws_port = _WS_PORT_LIVE if env_name == "live" else _WS_PORT_SANDBOX
    ws_port_override = _get("TRUEDATA_WS_PORT")
    if ws_port_override:
        try:
            ws_port = int(ws_port_override)
        except ValueError:
            logger.warning("TRUEDATA_WS_PORT='%s' is not a valid int; ignoring", ws_port_override)

    ws_host = _get("TRUEDATA_WS_HOST") or _WS_HOST
    rest_base = _get("TRUEDATA_REST_BASE_URL") or _REST_BASE
    history_base = _get("TRUEDATA_HISTORY_BASE_URL") or _HISTORY_BASE

    trial_duration = int(_get("TRUEDATA_TRIAL_DURATION_MINUTES") or "15")
    option_strike_range = int(_get("TRUEDATA_OPTION_STRIKE_RANGE") or "10")

    # Symbols can be comma-separated in env, or use defaults
    symbols_raw = _get("TRUEDATA_TRIAL_SYMBOLS")
    trial_symbols = (
        [s.strip() for s in symbols_raw.split(",") if s.strip()]
        if symbols_raw
        else list(_DEFAULT_TRIAL_SYMBOLS)
    )

    underlyings_raw = _get("TRUEDATA_OPTION_UNDERLYINGS")
    option_underlyings = (
        [s.strip() for s in underlyings_raw.split(",") if s.strip()]
        if underlyings_raw
        else list(_DEFAULT_OPTION_UNDERLYINGS)
    )

    # Directories
    project_root = Path(__file__).resolve().parents[2]  # c:\cb6_bot
    data_dir = Path(_get("TRUEDATA_DATA_DIR") or str(project_root / "data" / "truedata"))
    log_dir = Path(_get("TRUEDATA_LOG_DIR") or str(project_root / "logs" / "truedata"))

    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    cfg = TrueDataConfig(
        user=user,
        password=password,
        env=env_name,
        ws_host=ws_host,
        ws_port=ws_port,
        rest_base_url=rest_base.rstrip("/"),
        history_base_url=history_base.rstrip("/"),
        trial_duration_minutes=trial_duration,
        trial_symbols=trial_symbols,
        option_underlyings=option_underlyings,
        option_strike_range=option_strike_range,
        data_dir=data_dir,
        log_dir=log_dir,
    )

    logger.info(
        "TrueData config loaded: user=%s env=%s ws=%s",
        cfg.masked_user,
        cfg.env,
        cfg.ws_url,
    )
    return cfg
