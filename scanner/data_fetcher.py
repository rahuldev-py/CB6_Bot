# scanner/data_fetcher.py — Fetch candle data (TrueData primary, Fyers fallback)
import os
import sys
import time
import threading
import pandas as pd
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger

# ── Global rate limiter ─────────────────────────────────────────────────────
# Fyers historical API limit ≈ 10 req/sec. Empirically observed ~6-7 req/sec
# is the safe ceiling — 7.7 was triggering 429s on the 2nd fetch pass.
_RATE_LOCK     = threading.Lock()
_LAST_CALL_TS  = [0.0]
MIN_INTERVAL   = 0.18   # ~5.5 req/sec — conservative to avoid 429 storms

def _throttle():
    """Block until at least MIN_INTERVAL has passed since the last call."""
    with _RATE_LOCK:
        now      = time.monotonic()
        wait     = MIN_INTERVAL - (now - _LAST_CALL_TS[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL_TS[0] = time.monotonic()


# ── Per-day cache: largest fetch wins, smaller windows sliced from it ───────
# Avoids double-fetching when macro_bias asks for days=60 and scanner days=30.
_CACHE_LOCK    = threading.Lock()
_DATA_CACHE    = {}     # {(symbol, timeframe, today): {'days': N, 'df': DataFrame}}
_CACHE_DATE    = [None]


def _cache_today_key():
    return datetime.now().strftime('%Y-%m-%d')


CACHE_TTL_SECONDS = 120   # refresh live data every 2 minutes


def _cache_get(symbol, timeframe, days):
    """Return cached df trimmed to last `days` if available + fresh enough."""
    today = _cache_today_key()
    with _CACHE_LOCK:
        # New day → flush
        if _CACHE_DATE[0] != today:
            _DATA_CACHE.clear()
            _CACHE_DATE[0] = today
        entry = _DATA_CACHE.get((symbol, timeframe))
        if entry and entry['days'] >= days:
            # TTL check — force re-fetch after CACHE_TTL_SECONDS
            age = time.monotonic() - entry.get('fetched_at', 0)
            if age > CACHE_TTL_SECONDS:
                return None
            df = entry['df']
            # Trim to last `days` worth
            cutoff = datetime.now() - timedelta(days=days)
            sliced = df[df['timestamp'] >= cutoff]
            if len(sliced) > 20:
                return sliced.reset_index(drop=True)
            return df
    return None


def _cache_put(symbol, timeframe, days, df):
    today = _cache_today_key()
    with _CACHE_LOCK:
        if _CACHE_DATE[0] != today:
            _DATA_CACHE.clear()
            _CACHE_DATE[0] = today
        existing = _DATA_CACHE.get((symbol, timeframe))
        existing_stale = (
            existing and
            'fetched_at' in existing and
            (time.monotonic() - existing['fetched_at']) > CACHE_TTL_SECONDS
        )
        # Overwrite if: no entry, new fetch covers more days, missing timestamp, or TTL expired
        if not existing or existing['days'] < days or 'fetched_at' not in existing or existing_stale:
            _DATA_CACHE[(symbol, timeframe)] = {
                'days': days, 'df': df, 'fetched_at': time.monotonic()
            }


def clear_cache():
    """Clear the historical-data cache. Useful for tests / forced refresh."""
    with _CACHE_LOCK:
        _DATA_CACHE.clear()
        _CACHE_DATE[0] = None


# Fyers API hard limit: intraday resolutions (≤240 min) max 100 days per request.
# Daily/weekly have no such limit.
_INTRADAY_CHUNK = 90   # stay safely under the 100-day ceiling


def _fetch_single_range(fyers, symbol, timeframe, start_date, end_date, max_retries=3):
    """Fetch one date range. Returns DataFrame or None. No caching — caller handles it."""
    payload = {
        "symbol"      : symbol,
        "resolution"  : timeframe,
        "date_format" : "1",
        "range_from"  : start_date.strftime("%Y-%m-%d"),
        "range_to"    : end_date.strftime("%Y-%m-%d"),
        "cont_flag"   : "1"
    }
    backoff = 1.0
    for attempt in range(max_retries):
        try:
            _throttle()
            response = fyers.history(data=payload)

            if response.get('code') == 200 or response.get('s') == 'ok':
                candles = response.get('candles', [])
                if not candles:
                    return None
                df = pd.DataFrame(candles, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume'
                ])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                df['timestamp'] = df['timestamp'] + timedelta(hours=5, minutes=30)
                return df

            if response.get('code') == 429:
                wait = backoff * (2 ** attempt)
                logger.warning(f"{symbol}: rate-limited (429), backoff {wait:.1f}s")
                time.sleep(wait)
                continue

            logger.error(f"Failed to fetch {symbol}: {response}")
            return None

        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            time.sleep(0.5)

    logger.error(f"{symbol}: gave up after {max_retries} retries")
    return None


def _get_historical_data_truedata(symbol, timeframe, days):
    """
    Try fetching via TrueData REST API.
    Returns DataFrame on success, None if TrueData is unavailable, unhealthy, or stale.

    Health gate: if TrueData is in a reconnect storm (5+ reconnects in 60s),
    skip TrueData entirely so the caller falls back to Fyers immediately.
    Bar freshness: returned bars must have a recent last-candle timestamp,
    otherwise the 2-min cache could serve stale data during a reconnect window.
    """
    try:
        # ── Health gate ─────────────────────────────────────────────────────
        from data.data_health import get_monitor as _get_health
        health = _get_health()
        if not health.is_healthy():
            logger.debug(
                "%s: TrueData skipped — provider unhealthy (Fyers fallback active)", symbol
            )
            return None

        from data.truedata_feed import get_manager, fyers_to_td_symbol, tf_to_bar_size
        td = get_manager()
        if not td.is_hist_ready:
            if not td.connect_hist():
                return None
        td_sym  = fyers_to_td_symbol(symbol)
        bar_sz  = tf_to_bar_size(str(timeframe))
        df = td.get_historical_bars(td_sym, bar_sz, days)

        if df is None or len(df) <= 20:
            return None

        # ── Bar freshness gate ──────────────────────────────────────────────
        if not health.is_bar_fresh(df):
            logger.warning(
                "%s: TrueData bars discarded — last candle too old during market hours",
                symbol,
            )
            return None

        logger.info(f"{symbol}: TrueData {len(df)} candles ({timeframe}min)")
        return df

    except Exception as exc:
        logger.debug(f"TrueData fetch failed for {symbol}: {exc}")
    return None


def get_historical_data(fyers, symbol, timeframe, days=30, max_retries=3):
    """
    Fetch historical candle data. Tries TrueData first, falls back to Fyers.
    Results are cached for 2 minutes.

    Bar freshness gate: cached bars are validated for recency before being
    returned during market hours.  A reconnect storm can leave 2-min-old
    cache entries that pre-date the disconnect.  Those are discarded so the
    scanner always sees live data or nothing.
    """
    cached = _cache_get(symbol, timeframe, days)
    if cached is not None:
        # Validate bar freshness before trusting the cache
        try:
            from data.data_health import get_monitor as _get_health
            if not _get_health().is_bar_fresh(cached):
                logger.debug(
                    "%s: cached bars stale — discarding and re-fetching", symbol
                )
                cached = None
        except Exception:
            pass
    if cached is not None:
        logger.debug(f"{symbol} ({timeframe}min): cache hit ({len(cached)} candles)")
        return cached

    # ── TrueData primary path ────────────────────────────────────────────
    df_td = _get_historical_data_truedata(symbol, timeframe, days)
    if df_td is not None:
        _cache_put(symbol, timeframe, days, df_td)
        return df_td

    # ── Fyers fallback ───────────────────────────────────────────────────
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days)

    # Determine if chunking is needed (intraday resolutions only)
    try:
        res_int = int(timeframe)
        needs_chunk = (res_int <= 240) and (days > _INTRADAY_CHUNK)
    except ValueError:
        needs_chunk = False   # 'D', 'W' etc — no limit

    if needs_chunk:
        # Split into _INTRADAY_CHUNK-day windows and merge
        chunks = []
        chunk_start = start_date
        while chunk_start < end_date:
            chunk_end = min(chunk_start + timedelta(days=_INTRADAY_CHUNK), end_date)
            logger.debug(f"{symbol}: fetching chunk {chunk_start.date()} → {chunk_end.date()}")
            df_chunk = _fetch_single_range(fyers, symbol, timeframe, chunk_start, chunk_end, max_retries)
            if df_chunk is not None and len(df_chunk) > 0:
                chunks.append(df_chunk)
            chunk_start = chunk_end + timedelta(days=1)

        if not chunks:
            logger.error(f"{symbol}: all chunks returned empty data")
            return None

        df = pd.concat(chunks, ignore_index=True)
        df = df.drop_duplicates(subset='timestamp')
        df = df.sort_values('timestamp').reset_index(drop=True)
        logger.info(f"Fetched {len(df)} candles for {symbol} ({timeframe}min) via {len(chunks)} chunks")
        _cache_put(symbol, timeframe, days, df)
        return df

    # Single fetch (within limit)
    df = _fetch_single_range(fyers, symbol, timeframe, start_date, end_date, max_retries)
    if df is None:
        # Both TrueData and Fyers failed — fire a one-time alert during market hours
        try:
            from data.data_health import get_monitor as _get_health, _is_market_hours
            if _is_market_hours():
                _get_health().send_both_stale_alert()
        except Exception:
            pass
        return None
    df = df.sort_values('timestamp').reset_index(drop=True)
    logger.info(f"Fetched {len(df)} candles for {symbol} ({timeframe}min)")
    _cache_put(symbol, timeframe, days, df)
    return df


def get_all_data(fyers, symbols, timeframe, days=30):
    """
    Fetch data for all symbols. Throttling is handled inside
    get_historical_data() — no extra sleep needed here.
    Returns dict: {symbol: dataframe}
    """
    all_data = {}
    total    = len(symbols)
    for i, symbol in enumerate(symbols):
        if (i + 1) % 25 == 0 or i + 1 == total:
            logger.info(f"Progress: {i+1}/{total} symbols")
        df = get_historical_data(fyers, symbol, timeframe, days)
        if df is not None and len(df) > 20:
            all_data[symbol] = df
    logger.info(f"Successfully fetched data for {len(all_data)} symbols")
    return all_data