# utils/secure_config.py
#
# Secure configuration reader for CB6 Quantum.
#
# SECURITY PROTOCOL
# ─────────────────
# ALL credentials (API keys, passwords, tokens, WebSocket endpoints) MUST be
# supplied via environment variables or a .env file that is NEVER committed to
# source control.  This module enforces that contract:
#
#   1. Reads from process environment (os.environ) first.
#   2. Falls back to .env / .env.local loaded at startup (python-dotenv).
#   3. Raises ConfigError (not silently returns None) if a REQUIRED secret is absent.
#   4. Never logs secret values — only their presence/absence.
#
# .env.example (included in repo) shows required variable NAMES with blank values.
# .env         (git-ignored)     holds the actual values on each deployment machine.
#
# Usage:
#   from utils.secure_config import SecureConfig
#   cfg = SecureConfig()
#   token = cfg.require('TELEGRAM_BOT_TOKEN')       # raises if missing
#   key   = cfg.get('OPTIONAL_KEY', default='')     # returns default if missing

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


class ConfigError(RuntimeError):
    """Raised when a required secret is absent from the environment."""


class SecureConfig:
    """
    Unified credential accessor.  Loads .env once on construction; subsequent
    calls are pure os.environ lookups (no disk I/O, safe to call in hot paths).
    """

    _loaded: bool = False   # class-level flag — load .env only once per process

    def __init__(self, env_file: Optional[str] = None):
        if not SecureConfig._loaded:
            self._load_dotenv(env_file)
            SecureConfig._loaded = True

    # ── Public helpers ────────────────────────────────────────────────────────

    def require(self, key: str) -> str:
        """
        Return the value of environment variable `key`.
        Raises ConfigError if the variable is absent or blank.
        Never logs the value itself.
        """
        val = os.environ.get(key, '').strip()
        if not val:
            raise ConfigError(
                f"Required secret '{key}' is not set.\n"
                f"  • Add it to your .env file, or\n"
                f"  • Export it as an environment variable before starting the bot.\n"
                f"  See .env.example for the full list of required variables."
            )
        return val

    def get(self, key: str, default: str = '') -> str:
        """Return env var value or `default` if absent/blank. Never raises."""
        return os.environ.get(key, '').strip() or default

    def get_int(self, key: str, default: int = 0) -> int:
        """Return int env var or default."""
        val = self.get(key, '')
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Return float env var or default."""
        val = self.get(key, '')
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Return bool env var (truthy: '1', 'true', 'yes', 'on')."""
        val = self.get(key, '').lower()
        if val in ('1', 'true', 'yes', 'on'):
            return True
        if val in ('0', 'false', 'no', 'off'):
            return False
        return default

    def audit(self, required_keys: list[str]) -> dict:
        """
        Check which required keys are present vs missing.
        Returns {'present': [...], 'missing': [...]}.
        Safe to log — never includes values.
        """
        present = [k for k in required_keys if os.environ.get(k, '').strip()]
        missing = [k for k in required_keys if k not in present]
        return {'present': present, 'missing': missing}

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _load_dotenv(env_file: Optional[str] = None) -> None:
        """
        Load .env into os.environ using python-dotenv.
        Search order:
          1. Explicit `env_file` path if provided.
          2. .env in the project root (parent of utils/).
          3. .env.local in the project root (overrides .env — useful for dev).
        Does NOT override variables already set in the process environment.
        """
        try:
            from dotenv import load_dotenv
        except ImportError:
            # python-dotenv not installed — rely on shell-level env vars only
            return

        root = Path(__file__).resolve().parent.parent   # c:\cb6_bot

        if env_file:
            load_dotenv(env_file, override=False)
            return

        # Load base .env first, then .env.local (if present) for overrides
        base_env   = root / '.env'
        local_env  = root / '.env.local'

        if base_env.exists():
            load_dotenv(base_env, override=False)
        if local_env.exists():
            # .env.local CAN override .env (developer machine customisations)
            load_dotenv(local_env, override=True)


# ── Module-level singleton (import and use directly) ─────────────────────────

_config = SecureConfig()


def require(key: str) -> str:
    """Module-level shorthand for SecureConfig().require(key)."""
    return _config.require(key)


def get(key: str, default: str = '') -> str:
    """Module-level shorthand for SecureConfig().get(key, default)."""
    return _config.get(key, default)


# ── REQUIRED VARIABLES REGISTRY ──────────────────────────────────────────────
# Used by startup health-check to fail fast rather than silently missing creds.

REQUIRED_NSE_VARS = [
    'FYERS_CLIENT_ID',
    'FYERS_SECRET_KEY',
    'FYERS_REDIRECT_URI',
    'TELEGRAM_BOT_TOKEN',
    'TELEGRAM_CHAT_ID',
]

REQUIRED_FOREX_VARS = [
    'MT5_FTMO_LOGIN',
    'MT5_FTMO_PASSWORD',
    'MT5_FTMO_SERVER',
    'MT5_GFT_LOGIN',
    'MT5_GFT_PASSWORD',
    'MT5_GFT_SERVER',
    'FOREX_TELEGRAM_TOKEN',
    'CB6_ADMIN_USER_ID',
]

ALL_REQUIRED_VARS = REQUIRED_NSE_VARS + REQUIRED_FOREX_VARS


def startup_audit() -> None:
    """
    Run at process start. Logs which required secrets are present vs missing.
    Does NOT raise — allows partial startup (e.g. NSE-only run without Forex creds).
    Raises ConfigError only if ALL required vars are missing (complete misconfiguration).
    """
    result = _config.audit(ALL_REQUIRED_VARS)
    if result['present']:
        import logging
        logging.getLogger(__name__).info(
            f"SecureConfig: {len(result['present'])}/{len(ALL_REQUIRED_VARS)} "
            f"required secrets loaded. "
            + (f"Missing: {result['missing']}" if result['missing'] else "All present ✅")
        )
    if not result['present']:
        raise ConfigError(
            "STARTUP ABORT: No required environment variables are set.\n"
            "Copy .env.example to .env and fill in your credentials.\n"
            "Never commit .env to source control."
        )
