"""
NSE Yahoo Price Feed — background thread polling Yahoo Finance for index spot prices.

Used as a fallback price source when Fyers API fails to return a live quote.
Starts automatically in the background when start_nse_yahoo_feed() is called.

Usage:
    from data.nse_yahoo_feed import start_nse_yahoo_feed, get_yahoo_nse_price

    start_nse_yahoo_feed()          # call once at NSE engine startup

    price = get_yahoo_nse_price('NIFTY')              # → 24350.5 or None
    price = get_yahoo_nse_price('NSE:NIFTY25MAYFUT')  # Fyers symbol also accepted
"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional

import yfinance as yf

from utils.logger import logger

# ── Symbol maps ─────────────────────────────────────────────────────────────────

_YAHOO_SYMBOLS: dict[str, str] = {
    'NIFTY'      : '^NSEI',
    'BANKNIFTY'  : '^NSEBANK',
    'MIDCPNIFTY' : '^NSMIDCP',
}

# Yahoo coverage for FINNIFTY is inconsistent and frequently returns 404/delisted.
# Keep it out of batch polling to avoid noisy startup/runtime errors.
_YAHOO_UNSUPPORTED = {'FINNIFTY'}

_ALIASES: dict[str, str] = {
    'NIFTY50': 'NIFTY',
}

# Fyers futures symbol pattern: NSE:NIFTY25MAYFUT → NIFTY
_FYERS_RE = re.compile(
    r'(?:NSE:)?'
    r'(NIFTY50?|BANKNIFTY|FINNIFTY|MIDCPNIFTY)',
    re.IGNORECASE,
)

# ── In-memory price cache ────────────────────────────────────────────────────────

_prices: dict[str, float] = {}   # {index_name: last_known_price}
_lock   = threading.Lock()
_started = False

POLL_INTERVAL = 60   # seconds between Yahoo fetches


def _extract_index(symbol: str) -> Optional[str]:
    """Map any form of symbol to a canonical index name (NIFTY, BANKNIFTY, ...)."""
    sym = symbol.upper().strip()
    m = _FYERS_RE.search(sym)
    if m:
        raw = m.group(1).upper()
        return _ALIASES.get(raw, raw)
    for key in sorted(_YAHOO_SYMBOLS, key=len, reverse=True):
        if key in sym:
            return key
    for alias, canonical in _ALIASES.items():
        if alias in sym:
            return canonical
    return None


def _fetch_all() -> dict[str, float]:
    """Fetch latest prices for all NSE indices from Yahoo using per-ticker history.
    Uses yf.Ticker().history() instead of yf.download() — more reliable for NSE symbols.
    """
    result: dict[str, float] = {}
    for index_name, yahoo_sym in _YAHOO_SYMBOLS.items():
        try:
            ticker = yf.Ticker(yahoo_sym)
            hist   = ticker.history(period="1d", interval="5m")
            if not hist.empty:
                price = float(hist["Close"].dropna().iloc[-1])
                if price > 0:
                    result[index_name] = round(price, 2)
        except Exception as exc:
            logger.debug(f"NSE Yahoo fetch failed for {yahoo_sym}: {exc}")
    return result


def _poll_loop():
    """Background polling loop — runs forever until process exits."""
    logger.info("NSE Yahoo feed started (polling every %ds)", POLL_INTERVAL)
    while True:
        try:
            fresh = _fetch_all()
            if fresh:
                with _lock:
                    _prices.update(fresh)
                logger.debug(
                    "NSE Yahoo prices: %s",
                    "  ".join(f"{k}={v}" for k, v in fresh.items()),
                )
        except Exception as exc:
            logger.warning(f"NSE Yahoo poll error: {exc}")
        time.sleep(POLL_INTERVAL)


def start_nse_yahoo_feed() -> None:
    """Start the background Yahoo price feed (idempotent — safe to call multiple times)."""
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_poll_loop, daemon=True, name='NSEYahooFeed')
    t.start()


def get_yahoo_nse_price(symbol: str) -> Optional[float]:
    """
    Return the latest Yahoo-sourced spot price for an NSE index.

    Accepts:
        'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'
        'NSE:NIFTY25MAYFUT' or any Fyers futures symbol
        'NIFTY50' (alias for NIFTY)

    Returns None if the symbol is unrecognised or the feed has not yet polled.
    """
    index = _extract_index(symbol)
    if not index:
        return None
    if index in _YAHOO_UNSUPPORTED:
        return None
    with _lock:
        return _prices.get(index)
