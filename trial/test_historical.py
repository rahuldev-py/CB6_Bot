"""
Trial test: Historical candle data quality.

Fetches 1m, 3m, 5m, 15m candles for NIFTY-I and BANKNIFTY-I over the
last 5 trading days.  Validates timestamps, gaps, and duplicates.
Saves to CSV.  Returns a :class:`TrialResult`.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from provider.truedata import (
    TrueDataAuth,
    TrueDataConfig,
    TrueDataHistoricalClient,
    TrueDataRestClient,
    TrialResult,
)
from provider.truedata.models import MarketBar

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

_INTERVALS = ["1min", "3min", "5min", "15min"]
_TEST_SYMBOLS = ["NIFTY-I", "BANKNIFTY-I"]
_TRADING_DAYS_TO_FETCH = 5

# NSE market hours for gap detection
_OPEN_MINUTE = 9 * 60 + 15   # 09:15
_CLOSE_MINUTE = 15 * 60 + 30  # 15:30


def run_historical_test(config: TrueDataConfig) -> TrialResult:
    """
    Fetch candles for the last 5 trading days, validate quality, save CSV.

    Validation checks:
    - No duplicate timestamps within the same symbol+interval.
    - Timestamps strictly increasing.
    - No unexplained gaps during market hours (> 2× interval).

    Parameters
    ----------
    config:
        TrueData configuration.

    Returns
    -------
    TrialResult
        Score, gap/duplicate counts, and per-symbol/interval metrics.
    """
    started_at = datetime.now(tz=_IST)
    errors: list[str] = []
    details: dict = {}

    auth = TrueDataAuth(config)
    try:
        auth.login()
    except Exception as exc:
        errors.append(f"Auth failed: {exc}")
        return _build_result(started_at, False, 0, details, errors)

    rest = TrueDataRestClient(config, auth)
    hist = TrueDataHistoricalClient(rest)

    # Calculate date range: last 5 trading days
    to_date = date.today()
    from_date = _n_trading_days_back(to_date, _TRADING_DAYS_TO_FETCH)

    output_dir = config.data_dir / "trial_candles"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {}  # symbol -> interval -> metrics
    total_candles = 0
    total_gaps = 0
    total_dupes = 0
    intervals_ok = 0
    intervals_tested = 0

    for symbol in _TEST_SYMBOLS:
        summary[symbol] = {}
        for interval in _INTERVALS:
            intervals_tested += 1
            logger.info(
                "Fetching %s %s from %s to %s", symbol, interval, from_date, to_date
            )
            try:
                bars = hist.get_candles(symbol, interval, from_date, to_date)
            except Exception as exc:
                err_msg = f"{symbol} {interval}: {exc}"
                errors.append(err_msg)
                logger.warning(err_msg)
                summary[symbol][interval] = {
                    "count": 0, "gaps": 0, "dupes": 0, "error": str(exc)
                }
                continue

            gaps = _count_gaps(bars, interval)
            dupes = _count_duplicates(bars)
            strict_ok = _check_strictly_increasing(bars)

            total_candles += len(bars)
            total_gaps += gaps
            total_dupes += dupes

            if gaps == 0 and dupes == 0 and strict_ok:
                intervals_ok += 1

            summary[symbol][interval] = {
                "count": len(bars),
                "gaps": gaps,
                "duplicates": dupes,
                "strictly_increasing": strict_ok,
                "from": str(from_date),
                "to": str(to_date),
            }

            if bars:
                _save_csv(bars, symbol, interval, output_dir)

    details.update({
        "symbols": _TEST_SYMBOLS,
        "intervals": _INTERVALS,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "total_candles": total_candles,
        "total_gaps": total_gaps,
        "total_duplicates": total_dupes,
        "intervals_ok": intervals_ok,
        "intervals_tested": intervals_tested,
        "output_dir": str(output_dir),
        "summary": summary,
    })

    # Scoring: candle quality 15 pts + historical availability 10 pts
    score = 0

    # Historical availability: 10 pts — proportional to intervals with data
    availability_ratio = intervals_ok / max(intervals_tested, 1)
    score += round(availability_ratio * 10)

    # Candle quality: 15 pts
    if total_gaps == 0 and total_dupes == 0 and intervals_ok == intervals_tested:
        score += 15
    elif total_gaps <= 5 and total_dupes == 0:
        score += 10
    elif total_gaps <= 20:
        score += 5

    passed = (
        intervals_ok >= max(1, intervals_tested // 2)
        and total_candles > 0
        and not errors
    )

    return _build_result(started_at, passed, score, details, errors)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _count_gaps(bars: list[MarketBar], interval: str) -> int:
    """Count gaps larger than 2× the interval during market hours."""
    if len(bars) < 2:
        return 0
    interval_minutes = _interval_to_minutes(interval)
    if interval_minutes is None:
        return 0
    max_delta = timedelta(minutes=interval_minutes * 2)
    gaps = 0
    for i in range(1, len(bars)):
        delta = bars[i].timestamp - bars[i - 1].timestamp
        if delta > max_delta:
            # Only flag if both bars are within market hours
            if _is_market_time(bars[i - 1].timestamp) and _is_market_time(bars[i].timestamp):
                gaps += 1
    return gaps


def _count_duplicates(bars: list[MarketBar]) -> int:
    """Count bars with duplicate timestamps."""
    seen: set[datetime] = set()
    dupes = 0
    for bar in bars:
        if bar.timestamp in seen:
            dupes += 1
        seen.add(bar.timestamp)
    return dupes


def _check_strictly_increasing(bars: list[MarketBar]) -> bool:
    """Return True if all bar timestamps are strictly increasing."""
    for i in range(1, len(bars)):
        if bars[i].timestamp <= bars[i - 1].timestamp:
            return False
    return True


def _is_market_time(ts: datetime) -> bool:
    """Return True if timestamp is within NSE market hours."""
    minutes = ts.hour * 60 + ts.minute
    return _OPEN_MINUTE <= minutes <= _CLOSE_MINUTE


def _n_trading_days_back(from_date: date, n: int) -> date:
    """Return the date that is n trading days before from_date."""
    d = from_date
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri
            count += 1
    return d


def _interval_to_minutes(interval: str) -> Optional[int]:
    mapping = {
        "1min": 1, "3min": 3, "5min": 5,
        "10min": 10, "15min": 15, "30min": 30, "60min": 60,
    }
    return mapping.get(interval)


def _save_csv(
    bars: list[MarketBar], symbol: str, interval: str, output_dir: Path
) -> None:
    """Save a list of bars to a CSV file."""
    safe_sym = symbol.replace("/", "-")
    path = output_dir / f"{safe_sym}_{interval}_candles.csv"
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "symbol", "timestamp", "interval",
                "open", "high", "low", "close", "volume", "oi",
            ])
            for bar in bars:
                writer.writerow([
                    bar.symbol, bar.timestamp.isoformat(), bar.interval,
                    bar.open, bar.high, bar.low, bar.close, bar.volume, bar.oi,
                ])
        logger.info("Saved %d bars to %s", len(bars), path)
    except OSError as exc:
        logger.error("Failed to save CSV for %s %s: %s", symbol, interval, exc)


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


def _build_result(
    started_at: datetime,
    passed: bool,
    score: int,
    details: dict,
    errors: list[str],
) -> TrialResult:
    ended_at = datetime.now(tz=_IST)
    return TrialResult(
        test_name="Historical Candle Data",
        passed=passed,
        score=score,
        details=details,
        errors=errors,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=(ended_at - started_at).total_seconds(),
    )
