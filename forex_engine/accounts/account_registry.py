# forex_engine/accounts/account_registry.py
#
# CB6 Quantum — Account Registry
#
# Loads config/mt5_accounts.json, resolves credentials from .env,
# migrates legacy state files to isolated directories on first run.
#
# Usage:
#   from forex_engine.accounts.account_registry import get_account, get_terminal_path, migrate_state
#   cfg = get_account('FTMO_10K')
#   path = get_terminal_path('FTMO_10K')

import json
import os
import shutil
from typing import Optional

from utils.logger import logger

# ── Paths ───────────────────────────────────────────────────────────────────────
_ROOT          = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REGISTRY_FILE = os.path.join(_ROOT, 'config', 'mt5_accounts.json')

# ── Cache ───────────────────────────────────────────────────────────────────────
_registry: Optional[dict] = None


def _load() -> dict:
    global _registry
    if _registry is not None:
        return _registry
    try:
        with open(_REGISTRY_FILE, encoding='utf-8') as f:
            data = json.load(f)
        _registry = {k: v for k, v in data.items() if not k.startswith('_')}
        return _registry
    except FileNotFoundError:
        logger.error(f"Account registry not found: {_REGISTRY_FILE}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Account registry JSON error: {e}")
        return {}


def get_account(account_id: str) -> Optional[dict]:
    """Return the full account config dict for account_id, or None if not found."""
    return _load().get(account_id)


def get_all_accounts() -> dict:
    """Return all account configs keyed by account_id."""
    return _load()


def get_enabled_accounts() -> dict:
    """Return only accounts where enabled=true."""
    return {k: v for k, v in _load().items() if v.get('enabled', False)}


def get_terminal_path(account_id: str) -> Optional[str]:
    """
    Return the resolved terminal path for account_id.

    Resolution order:
      1. Environment variable named in config['terminal_env']
         (e.g. MT5_TERMINAL_FTMO → C:/CB6_MT5/MT5_FTMO_10K/terminal64.exe)
      2. config['terminal'] hard-coded path
      3. None (falls back to system-default MT5 terminal in mt5.initialize)

    Returns None if terminal file does not exist — caller should log a warning
    and let mt5.initialize() use its default.
    """
    cfg = get_account(account_id)
    if not cfg:
        return None

    # 1. Check env var override
    env_key = cfg.get('terminal_env', '')
    if env_key:
        env_val = os.getenv(env_key, '').strip()
        if env_val:
            path = env_val.replace('/', os.sep)
            if os.path.isfile(path):
                return path
            logger.warning(
                f"[{account_id}] terminal_env {env_key}={env_val!r} "
                f"— file not found, falling back to config path"
            )

    # 2. Check config hard-coded path
    cfg_path = cfg.get('terminal', '').replace('/', os.sep)
    if cfg_path:
        if os.path.isfile(cfg_path):
            return cfg_path
        logger.warning(
            f"[{account_id}] config terminal {cfg_path!r} "
            f"— file not found. Terminals not yet installed? "
            f"See C:\\CB6_MT5\\README_SETUP.md"
        )

    # 3. No valid path — will use system default
    logger.info(
        f"[{account_id}] no terminal path resolved — "
        f"mt5.initialize() will use system-default terminal"
    )
    return None


def get_credentials(account_id: str) -> Optional[dict]:
    """
    Return {'login': int, 'password': str, 'server': str} resolved from .env.
    Returns None if any credential is missing.
    """
    cfg = get_account(account_id)
    if not cfg:
        return None

    login_str = os.getenv(cfg.get('login_env', ''), '').strip()
    password  = os.getenv(cfg.get('password_env', ''), '').strip()
    server    = os.getenv(cfg.get('server_env', ''), '').strip()

    if not login_str or not password or not server:
        missing = [k for k, v in [('login', login_str), ('password', password), ('server', server)] if not v]
        logger.warning(f"[{account_id}] missing credentials: {missing}")
        return None

    try:
        return {'login': int(login_str), 'password': password, 'server': server}
    except ValueError:
        logger.error(f"[{account_id}] login is not an integer: {login_str!r}")
        return None


def get_state_dir(account_id: str) -> str:
    """Return absolute path to the isolated state directory for account_id."""
    cfg = get_account(account_id)
    rel = cfg.get('state_dir', f'data/{account_id.lower()}') if cfg else f'data/{account_id.lower()}'
    return os.path.join(_ROOT, rel.replace('/', os.sep))


def migrate_state(account_id: str) -> bool:
    """
    One-time migration: copy legacy state file to isolated state_dir if:
      - legacy file exists
      - state_dir/state.json does NOT yet exist

    Returns True if migration ran, False if skipped (already migrated or no legacy file).
    """
    cfg = get_account(account_id)
    if not cfg:
        return False

    legacy_rel = cfg.get('legacy_state', '')
    if not legacy_rel:
        return False

    legacy_path = os.path.join(_ROOT, legacy_rel.replace('/', os.sep))
    state_dir   = get_state_dir(account_id)
    new_path    = os.path.join(state_dir, 'state.json')

    # Already migrated
    if os.path.exists(new_path):
        return False

    # Nothing to migrate
    if not os.path.exists(legacy_path):
        return False

    os.makedirs(state_dir, exist_ok=True)
    shutil.copy2(legacy_path, new_path)
    logger.info(
        f"[{account_id}] State migrated: {legacy_path} → {new_path}"
    )
    return True


def migrate_all() -> None:
    """Run state migration for all enabled accounts."""
    for account_id in get_enabled_accounts():
        try:
            migrated = migrate_state(account_id)
            if migrated:
                logger.info(f"[{account_id}] Legacy state migrated to isolated directory.")
        except Exception as e:
            logger.warning(f"[{account_id}] State migration failed (non-fatal): {e}")


def is_paper(account_id: str) -> bool:
    """Return True if account is in paper mode (from env var)."""
    if account_id == 'GFT_1K_INSTANT':
        enabled = os.getenv('CB6_GFT_1K_INSTANT_ENABLED', 'false').lower() == 'true'
        live = os.getenv('CB6_GFT_1K_INSTANT_LIVE_EXECUTION', 'false').lower() == 'true'
        return not (enabled and live)

    cfg = get_account(account_id)
    if not cfg:
        return True
    env_key = cfg.get('paper_env', '')
    if not env_key:
        return True
    return os.getenv(env_key, 'true').lower() == 'true'


def get_magic(account_id: str) -> int:
    """Return the magic number for account_id."""
    cfg = get_account(account_id)
    return int(cfg.get('magic', 0)) if cfg else 0


def status_summary() -> dict:
    """
    Return a dict keyed by account_id with basic status info.
    Used by dashboard and /fx_terminals Telegram command.
    """
    result = {}
    for acc_id, cfg in _load().items():
        terminal_path = get_terminal_path(acc_id)
        result[acc_id] = {
            'label'         : cfg.get('label', acc_id),
            'enabled'       : cfg.get('enabled', False),
            'paper'         : is_paper(acc_id),
            'terminal_path' : terminal_path,
            'terminal_found': os.path.isfile(terminal_path) if terminal_path else False,
            'magic'         : cfg.get('magic', 0),
            'account_size'  : cfg.get('account_size', 0),
            'risk_profile'  : cfg.get('risk_profile', ''),
        }
    return result
