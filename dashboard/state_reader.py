# dashboard/state_reader.py — State-loading helpers for CB6 dashboard
#
# These functions read JSON state files and return plain dicts.
# They are pure I/O — no HTML, no computation, no rendering.
# Importable from both dashboard.py and external test code.

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(__file__))


def _json_load(path: str, default: Any = None) -> Any:
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def load_nse_state() -> dict:
    """Load NSE paper-trade state from data/paper_state.json."""
    path = os.path.join(_ROOT, "data", "paper_state.json")
    return _json_load(path, {
        'capital': 200000, 'available_capital': 200000,
        'open_trades': [], 'closed_trades': [],
        'daily_losses': 0, 'daily_trades': 0,
        'total_pnl': 0, 'paused': False,
        'date': datetime.now().strftime('%Y-%m-%d'),
    })


def load_ftmo_state() -> dict:
    """Load FTMO account state."""
    path = os.path.join(_ROOT, "data", "ftmo_10k", "state.json")
    return _json_load(path, {})


def load_gft_state() -> dict:
    """Load GFT account state."""
    path = os.path.join(_ROOT, "data", "gft_5k", "state.json")
    return _json_load(path, {})


def load_crypto_state() -> dict:
    """Load crypto engine state."""
    path = os.path.join(_ROOT, "data", "crypto_state.json")
    return _json_load(path, {
        'capital': 0, 'available_capital': 0,
        'open_trades': [], 'closed_trades': [],
        'daily_pnl': 0, 'paused': False,
    })


def load_market_context() -> dict:
    """Load FII/DII context from data/market_context.json."""
    path = os.path.join(_ROOT, "data", "market_context.json")
    return _json_load(path, {
        'fii_net': None, 'dii_net': None, 'fii_bias': 'NEUTRAL',
        'vix': None, 'sgx_nifty': None,
    })


def load_watchlist_data() -> list:
    """Load watchlist entries from data/watchlist.json."""
    path = os.path.join(_ROOT, "data", "watchlist.json")
    return _json_load(path, [])
