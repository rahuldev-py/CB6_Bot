"""
CB6 Quantum — OHLCV Archive Runner
Fetches historical candles from MT5 (Forex) and Fyers/TrueData (NSE),
stores them incrementally in data/cb6_trades.db.

Run from project root:
  python archive_ohlcv.py                    # all markets, default depth
  python archive_ohlcv.py --market forex     # forex only
  python archive_ohlcv.py --market nse       # NSE only
  python archive_ohlcv.py --days 365         # initial deep fetch
  python archive_ohlcv.py --catalog          # show what's stored

Design: incremental — checks latest stored bar per symbol/TF, only fetches delta.
On first run with --days N it backfills up to N calendar days.
"""

import sys
import os
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.ohlcv_archive import save_candles, get_latest_bar_time, catalog
from utils.logger import logger

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FOREX_SYMBOLS    = ["XAGUSD", "USOIL", "EURUSD"]
NSE_SYMBOLS      = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "NSE:FINNIFTY-INDEX",
    "NSE:MIDCPNIFTY-INDEX",
]
TIMEFRAMES_FOREX = ["15m", "1h", "4h", "1d"]
TIMEFRAMES_NSE   = ["15", "60", "D"]          # Fyers TF strings

# Map Fyers TF → our canonical label for storage
_FYERS_TF_LABEL = {"15": "15m", "60": "1h", "D": "D"}

# MT5 timeframe constants (imported lazily)
_MT5_TF = {
    "15m": None,   # mt5.TIMEFRAME_M15
    "1h" : None,   # mt5.TIMEFRAME_H1
    "4h" : None,   # mt5.TIMEFRAME_H4
    "1d" : None,   # mt5.TIMEFRAME_D1
}


# ---------------------------------------------------------------------------
# MT5 helpers
# ---------------------------------------------------------------------------

def _init_mt5_tf():
    """Populate _MT5_TF constants once mt5 is imported."""
    try:
        import MetaTrader5 as mt5
        _MT5_TF["15m"] = mt5.TIMEFRAME_M15
        _MT5_TF["1h"]  = mt5.TIMEFRAME_H1
        _MT5_TF["4h"]  = mt5.TIMEFRAME_H4
        _MT5_TF["1d"]  = mt5.TIMEFRAME_D1
        return mt5
    except ImportError:
        return None


def _mt5_connect(mt5, terminal_path: str = None) -> bool:
    """Initialize MT5. Returns True on success."""
    login    = int(os.getenv("MT5_LOGIN_FTMO", 0))
    password = os.getenv("MT5_PASSWORD_FTMO", "")
    server   = os.getenv("MT5_SERVER_FTMO", "")

    kwargs = {}
    if terminal_path:
        kwargs["path"] = terminal_path

    if login:
        ok = mt5.initialize(login=login, password=password, server=server, **kwargs)
    else:
        ok = mt5.initialize(**kwargs)

    if not ok:
        logger.warning(f"MT5 init failed: {mt5.last_error()}")
    return ok


def _fetch_mt5(mt5, symbol: str, tf_label: str, from_dt: datetime, to_dt: datetime):
    """Fetch candles from MT5 as a pandas DataFrame or None."""
    import pandas as pd

    tf_const = _MT5_TF.get(tf_label)
    if tf_const is None:
        return None

    rates = mt5.copy_rates_range(symbol, tf_const, from_dt, to_dt)
    if rates is None or len(rates) == 0:
        return None

    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "volume"})[
        ["timestamp", "open", "high", "low", "close", "volume"]
    ]
    return df


# ---------------------------------------------------------------------------
# Fyers helpers
# ---------------------------------------------------------------------------

def _build_fyers():
    """Build a FyersModel from .env credentials. Returns instance or None."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from fyers_apiv3 import fyersModel
        client_id = os.getenv("CLIENT_ID", "")
        token_str  = os.getenv("ACCESS_TOKEN", "")
        if not client_id or not token_str:
            logger.warning("CLIENT_ID or ACCESS_TOKEN not set in .env")
            return None
        if ":" in token_str:
            token_str = token_str.split(":", 1)[1]
        fyers = fyersModel.FyersModel(
            client_id=client_id,
            token=token_str,
            is_async=False,
            log_path=os.path.join(os.getcwd(), "logs", ""),
        )
        return fyers
    except Exception as e:
        logger.warning(f"Fyers init failed: {e}")
        return None


def _fetch_fyers(fyers, symbol: str, tf: str, from_dt: datetime, to_dt: datetime):
    """Fetch candles from Fyers as DataFrame or None. Handles 100-day chunk limit."""
    try:
        from scanner.data_fetcher import get_historical_data
        days = (to_dt - from_dt).days + 1
        df = get_historical_data(fyers, symbol, tf, days=days)
        if df is None or df.empty:
            return None
        # Trim to requested range
        if "timestamp" in df.columns:
            df = df[df["timestamp"] >= from_dt.replace(tzinfo=None)]
        return df
    except Exception as e:
        logger.warning(f"Fyers fetch failed {symbol}/{tf}: {e}")
        return None


# ---------------------------------------------------------------------------
# Archive forex
# ---------------------------------------------------------------------------

def archive_forex(days_back: int = 90, terminal_path: str = None) -> dict:
    mt5 = _init_mt5_tf()
    if mt5 is None:
        print("  [SKIP] MetaTrader5 package not installed")
        return {}

    if not _mt5_connect(mt5, terminal_path):
        print("  [SKIP] MT5 not connected — is the terminal running?")
        return {}

    summary = {}
    now = datetime.now(timezone.utc)

    for symbol in FOREX_SYMBOLS:
        for tf in TIMEFRAMES_FOREX:
            latest = get_latest_bar_time("FOREX", symbol, tf)
            if latest:
                # Incremental: fetch from last stored bar
                from_dt = datetime.fromisoformat(latest.replace("Z", "+00:00")) + timedelta(minutes=1)
            else:
                from_dt = now - timedelta(days=days_back)

            if from_dt >= now:
                key = f"{symbol}/{tf}"
                summary[key] = 0
                continue

            df = _fetch_mt5(mt5, symbol, tf, from_dt, now)
            n = save_candles("FOREX", symbol, tf, df)
            key = f"{symbol}/{tf}"
            summary[key] = n
            status = "new" if not latest else "delta"
            print(f"  FOREX {symbol:8} {tf:4}  {n:>5} bars  [{status}]")

    mt5.shutdown()
    return summary


# ---------------------------------------------------------------------------
# Archive NSE
# ---------------------------------------------------------------------------

def archive_nse(days_back: int = 90) -> dict:
    summary = {}

    # TrueData: always run for recent delta (last 15 days max for intraday)
    td_rows = _archive_nse_truedata(min(days_back, 15))
    summary.update(td_rows)

    # Fyers: always run for deep history (handles backfill + any TrueData gaps)
    fy_rows = _archive_nse_fyers(days_back)
    for k, v in fy_rows.items():
        summary[k] = summary.get(k, 0) + v

    return summary


def _archive_nse_truedata(days_back: int) -> dict:
    """Try to fetch NSE candles via TrueData. Returns {} if unavailable."""
    try:
        from data.truedata_feed import TrueDataManager
        td = TrueDataManager()
        if not td.is_healthy():
            return {}
    except Exception:
        return {}

    summary = {}
    now = datetime.now()
    # TrueData supports up to 15 days for 1min/3min; longer for higher TFs
    TD_TF_MAP = {"15m": "15min", "1h": "60min", "D": "eod"}

    nse_symbol_map = {
        "NSE:NIFTY50-INDEX":      "NIFTY 50",
        "NSE:NIFTYBANK-INDEX":    "NIFTY BANK",
        "NSE:FINNIFTY-INDEX":     "FINNIFTY",
        "NSE:MIDCPNIFTY-INDEX":   "MIDCPNIFTY",
    }

    for fyers_sym, td_sym in nse_symbol_map.items():
        for tf_label, td_tf in TD_TF_MAP.items():
            latest = get_latest_bar_time("NSE", fyers_sym, tf_label)
            fetch_days = min(days_back, 15) if tf_label != "D" else days_back
            if latest:
                delta_days = (now - datetime.fromisoformat(latest)).days + 1
                fetch_days = min(delta_days + 1, fetch_days)

            df = td.get_historical_bars(td_sym, td_tf, days=fetch_days)
            n = save_candles("NSE", fyers_sym, tf_label, df)
            summary[f"{fyers_sym}/{tf_label}"] = n
            print(f"  NSE(TD) {fyers_sym.split(':')[1]:25} {tf_label:4}  {n:>5} bars")

    return summary


def _archive_nse_fyers(days_back: int) -> dict:
    """Fetch NSE candles via Fyers API — supports both incremental and deep backfill."""
    fyers = _build_fyers()
    if fyers is None:
        print("  [SKIP] Fyers not available (token missing or expired)")
        return {}

    summary = {}
    now = datetime.now()

    fyers_tf_map = {"15m": "15", "1h": "60", "D": "D"}

    for symbol in NSE_SYMBOLS:
        for tf_label, fyers_tf in fyers_tf_map.items():
            from utils.ohlcv_archive import get_candles as _gc
            latest  = get_latest_bar_time("NSE", symbol, tf_label)
            # Check oldest stored bar to determine if backfill needed
            _oldest_df = _gc("NSE", symbol, tf_label, limit=1) if latest else None
            _oldest_ts = str(_oldest_df.iloc[0]["timestamp"])[:10] if (_oldest_df is not None and not _oldest_df.empty) else None
            _oldest_days = (now - datetime.fromisoformat(_oldest_ts)).days if _oldest_ts else None

            # Need backfill if oldest stored is younger than days_back target
            if _oldest_days is not None and _oldest_days < days_back - 5:
                # Fetch from days_back all the way to current oldest (backward fill)
                backfill_from = now - timedelta(days=days_back)
                backfill_to   = datetime.fromisoformat(_oldest_ts)
                df_back = _fetch_fyers(fyers, symbol, fyers_tf, backfill_from, backfill_to)
                n_back  = save_candles("NSE", symbol, tf_label, df_back)
                if n_back:
                    print(f"  NSE(FY) {symbol.split(':')[1]:25} {tf_label:4}  {n_back:>5} bars  [backfill]")
                    summary[f"{symbol}/{tf_label}/backfill"] = n_back

            if latest:
                delta_days = (now - datetime.fromisoformat(latest.split("T")[0])).days + 1
                fetch_days = min(delta_days + 1, days_back)
            else:
                fetch_days = days_back

            from_dt = now - timedelta(days=fetch_days)
            df = _fetch_fyers(fyers, symbol, fyers_tf, from_dt, now)
            n = save_candles("NSE", symbol, tf_label, df)
            summary[f"{symbol}/{tf_label}"] = n
            status = "new" if not latest else "delta"
            print(f"  NSE(FY) {symbol.split(':')[1]:25} {tf_label:4}  {n:>5} bars  [{status}]")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CB6 OHLCV Archive")
    parser.add_argument("--market",  choices=["forex", "nse", "all"], default="all")
    parser.add_argument("--days",    type=int, default=90,
                        help="How many calendar days back to fetch on first run (default 90)")
    parser.add_argument("--deep",    action="store_true",
                        help="Deep backfill: 365 days NSE, 730 days Forex (overrides --days)")
    parser.add_argument("--terminal", help="MT5 terminal64.exe path for forex fetch")
    parser.add_argument("--catalog", action="store_true", help="Show archive catalog and exit")
    args = parser.parse_args()

    if args.deep:
        args.days = 365   # NSE: 1 year; Forex will use 730 when MT5 supports it

    if args.catalog:
        rows = catalog()
        if not rows:
            print("Archive is empty — run without --catalog to fetch data.")
            return
        print(f"\n{'Market':<8} {'Symbol':<30} {'TF':<5} {'Bars':>7}  {'Oldest':<22} {'Newest'}")
        print("-" * 90)
        for r in rows:
            print(f"{r['market']:<8} {r['symbol']:<30} {r['timeframe']:<5} "
                  f"{r['bars']:>7}  {r['oldest']:<22} {r['newest']}")
        return

    print(f"CB6 OHLCV Archive — {datetime.now().strftime('%Y-%m-%d %H:%M')}  (depth={args.days}d)")
    print()

    total = 0

    if args.market in ("forex", "all"):
        print("--- Forex (MT5) ---")
        forex_days = 730 if args.deep else args.days
        r = archive_forex(days_back=forex_days, terminal_path=args.terminal)
        total += sum(r.values())
        print()

    if args.market in ("nse", "all"):
        print("--- NSE (TrueData / Fyers) ---")
        r = archive_nse(days_back=args.days)
        total += sum(r.values())
        print()

    print(f"Done. Total bars stored: {total}")
    print("Run with --catalog to inspect what's in the archive.")


if __name__ == "__main__":
    main()
