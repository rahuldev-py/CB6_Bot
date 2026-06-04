"""
ml_engine/training/live_market_loader.py

Reads live market candle samples from CB6's existing data layer.
Used for VALIDATION ONLY — to check that feature distributions from
training data match what the live scanner sees today.

Rules:
  - Read-only. Never modifies scanner state.
  - Never imports from trader/, core/risk.py, core/trade_triggers.py,
    core/tick_watcher.py, or any execution path.
  - Falls back to Yahoo Finance (yfinance) when Fyers is unavailable.
  - All data returned as plain DataFrames — no side effects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("cb6.ml.live_market_loader")

# NSE symbols in Fyers format → Yahoo Finance equivalent
# FINNIFTY (Nifty Financial Services) has no reliable Yahoo ticker — excluded.
_NSE_YAHOO_MAP = {
    "NSE:NIFTY50-INDEX"   : "^NSEI",
    "NSE:NIFTYBANK-INDEX" : "^NSEBANK",
    "NSE:MIDCPNIFTY-INDEX": "^NSMIDCP",
    "NIFTY"               : "^NSEI",
    "BANKNIFTY"           : "^NSEBANK",
    "MIDCPNIFTY"          : "^NSMIDCP",
}

# Symbols with no reliable Yahoo Finance equivalent — return None silently.
_YAHOO_UNSUPPORTED = {"FINNIFTY", "NSE:FINNIFTY-INDEX"}

# Forex symbols → Yahoo Finance equivalent
_FOREX_YAHOO_MAP = {
    "XAUUSD" : "GC=F",
    "XAGUSD" : "SI=F",
    "USOIL"  : "CL=F",
    "GBPUSD" : "GBPUSD=X",
    "EURUSD" : "EURUSD=X",
    "USDJPY" : "JPY=X",
}


def _to_yahoo_symbol(symbol: str) -> Optional[str]:
    """Return Yahoo Finance ticker, or None if the symbol has no Yahoo equivalent."""
    s = symbol.upper().strip()
    if s in _YAHOO_UNSUPPORTED:
        return None
    return _NSE_YAHOO_MAP.get(s) or _FOREX_YAHOO_MAP.get(s) or s


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower().strip() for c in df.columns]
    rename = {
        "open" : "open", "high": "high", "low": "low",
        "close": "close", "volume": "volume",
        "adj close": "close", "dividends": None, "stock splits": None,
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns and v})
    drop = [c for c in ["dividends", "stock splits", "capital gains"] if c in df.columns]
    df = df.drop(columns=drop, errors="ignore")
    df = df[["open", "high", "low", "close", "volume"] if all(
        c in df.columns for c in ["open", "high", "low", "close", "volume"]
    ) else [c for c in ["open", "high", "low", "close"] if c in df.columns]]
    return df


def load_from_yahoo(
    symbol: str,
    interval: str = "15m",
    days: int = 5,
) -> Optional[pd.DataFrame]:
    """
    Pull recent OHLCV candles from Yahoo Finance for validation.

    Parameters
    ----------
    symbol   : CB6 or Yahoo symbol (auto-converted).
    interval : '1m' | '5m' | '15m' | '1h' | '1d'
    days     : Number of calendar days to fetch (max 60 for intraday).

    Returns
    -------
    DataFrame with columns [open, high, low, close, volume] and
    DatetimeIndex in IST (Asia/Kolkata), or None on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — pip install yfinance")
        return None

    yahoo_sym = _to_yahoo_symbol(symbol)
    if yahoo_sym is None:
        logger.debug(f"Yahoo: {symbol} has no supported ticker — skipping")
        return None

    end   = datetime.utcnow()
    start = end - timedelta(days=days)

    try:
        ticker = yf.Ticker(yahoo_sym)
        df = ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
            auto_adjust=True,
        )
    except Exception as e:
        logger.error(f"Yahoo fetch failed for {yahoo_sym}: {e}")
        return None

    if df is None or df.empty:
        logger.warning(f"No data returned for {yahoo_sym}")
        return None

    df = _normalise_columns(df)

    if df.index.tzinfo is not None:
        import pytz
        df.index = df.index.tz_convert(pytz.timezone("Asia/Kolkata")).tz_localize(None)

    logger.info(f"Yahoo [{yahoo_sym}] {interval}: {len(df)} candles ({days}d)")
    return df


def load_from_fyers(
    fyers,
    symbol: str,
    resolution: str = "15",
    days: int = 5,
) -> Optional[pd.DataFrame]:
    """
    Pull recent candles from Fyers API (read-only historical endpoint).
    fyers must be an authenticated FyersModel instance passed in from caller.
    ML engine never creates its own Fyers connection.

    Returns normalised DataFrame or None on failure.
    """
    if fyers is None:
        logger.info("No Fyers instance provided — falling back to Yahoo")
        return None

    try:
        from data.financial_data_core import get_historical_data
        df = get_historical_data(fyers, symbol, resolution, days=days)
        if df is not None and not df.empty:
            df = _normalise_columns(df)
            logger.info(f"Fyers [{symbol}] res={resolution}: {len(df)} candles")
            return df
    except Exception as e:
        logger.warning(f"Fyers load failed for {symbol}: {e}")
    return None


def load_candles(
    symbol: str,
    interval: str = "15m",
    days: int = 5,
    fyers=None,
    resolution: str = "15",
) -> Optional[pd.DataFrame]:
    """
    Primary entry point. Tries Fyers first, falls back to Yahoo.

    Parameters
    ----------
    symbol     : CB6 or Yahoo symbol string.
    interval   : Yahoo interval string ('15m', '1h', etc.).
    days       : Days of history.
    fyers      : Optional authenticated FyersModel (Fyers path).
    resolution : Fyers resolution string ('5', '15', '60').

    Returns
    -------
    Normalised OHLCV DataFrame or None.
    """
    if fyers is not None:
        df = load_from_fyers(fyers, symbol, resolution=resolution, days=days)
        if df is not None:
            return df

    return load_from_yahoo(symbol, interval=interval, days=days)


def load_multi_symbol(
    symbols: list[str],
    interval: str = "15m",
    days: int = 5,
    fyers=None,
) -> dict[str, pd.DataFrame]:
    """
    Load candles for multiple symbols. Returns dict of {symbol: DataFrame}.
    Missing symbols are skipped with a warning (never raises).
    """
    result = {}
    for sym in symbols:
        try:
            df = load_candles(sym, interval=interval, days=days, fyers=fyers)
            if df is not None and not df.empty:
                result[sym] = df
            else:
                logger.warning(f"No candles loaded for {sym}")
        except Exception as e:
            logger.error(f"Error loading {sym}: {e}")
    logger.info(f"Loaded candles for {len(result)}/{len(symbols)} symbols")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    syms = ["NIFTY", "BANKNIFTY", "XAUUSD", "GBPUSD"]
    data = load_multi_symbol(syms, interval="15m", days=3)
    for sym, df in data.items():
        print(f"\n{sym}: {len(df)} candles")
        print(df.tail(3).to_string())
