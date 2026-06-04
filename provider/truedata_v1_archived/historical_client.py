"""
TrueData historical data client.

Fetches OHLCV candles and tick-level data from the TrueData history API,
validates the response, and returns typed :class:`MarketBar` /
:class:`MarketTick` objects with IST timestamps.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .exceptions import TrueDataAPIError, TrueDataSymbolNotFoundError
from .models import MarketBar, MarketTick
from .rest_client import TrueDataRestClient

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# Valid TrueData interval strings
VALID_INTERVALS = frozenset(
    {"1min", "3min", "5min", "10min", "15min", "30min", "60min", "1day"}
)


class TrueDataHistoricalClient:
    """
    Fetch historical candle and tick data from TrueData.

    Parameters
    ----------
    rest_client:
        A configured :class:`TrueDataRestClient` instance.
    """

    def __init__(self, rest_client: TrueDataRestClient) -> None:
        self._rest = rest_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_candles(
        self,
        symbol: str,
        interval: str,
        from_dt: datetime | date,
        to_dt: datetime | date,
    ) -> list[MarketBar]:
        """
        Fetch OHLCV bars for a symbol over a date/datetime range.

        Parameters
        ----------
        symbol:
            TrueData symbol string, e.g. ``"NIFTY-I"``.
        interval:
            One of ``1min``, ``3min``, ``5min``, ``10min``, ``15min``,
            ``30min``, ``60min``, ``1day``.
        from_dt:
            Start of the range (inclusive).  Date or datetime objects
            accepted.
        to_dt:
            End of the range (inclusive).

        Returns
        -------
        list[MarketBar]
            Validated, sorted list of bars.  Empty list if none returned.

        Raises
        ------
        ValueError
            If ``interval`` is not a recognised value.
        TrueDataSymbolNotFoundError
            If TrueData reports the symbol does not exist.
        TrueDataAPIError
            On unexpected API responses.
        """
        if interval not in VALID_INTERVALS:
            raise ValueError(
                f"Invalid interval '{interval}'. "
                f"Valid values: {sorted(VALID_INTERVALS)}"
            )

        from_str = _to_date_str(from_dt)
        to_str = _to_date_str(to_dt)

        logger.info(
            "Fetching %s candles for %s from %s to %s",
            interval, symbol, from_str, to_str,
        )

        raw = self._rest.get_history(
            "/getAllData",
            params={
                "symbol": symbol,
                "interval": interval,
                "from": from_str,
                "to": to_str,
            },
        )

        records = self._extract_records(raw, symbol)
        bars = [self._parse_bar(r, symbol, interval) for r in records]
        bars = [b for b in bars if b is not None]

        bars.sort(key=lambda b: b.timestamp)
        bars = self._deduplicate(bars)
        bars = self._validate_sequence(bars, symbol, interval)

        logger.info(
            "Got %d bars for %s (%s) from %s to %s",
            len(bars), symbol, interval, from_str, to_str,
        )
        return bars

    def get_ticks(
        self,
        symbol: str,
        date_: date | datetime | str,
    ) -> list[MarketTick]:
        """
        Fetch all tick records for a symbol on a given trading day.

        Parameters
        ----------
        symbol:
            TrueData symbol string.
        date_:
            The trading date.  Accepts ``date``, ``datetime``, or
            ``"YYYY-MM-DD"`` string.

        Returns
        -------
        list[MarketTick]
            Sorted list of ticks.  Empty if none returned.
        """
        date_str = _to_date_str(date_) if not isinstance(date_, str) else date_

        logger.info("Fetching tick data for %s on %s", symbol, date_str)

        raw = self._rest.get_history(
            "/getTickData",
            params={"symbol": symbol, "date": date_str},
        )

        records = self._extract_records(raw, symbol)
        ticks = [self._parse_tick(r, symbol) for r in records]
        ticks = [t for t in ticks if t is not None]
        ticks.sort(key=lambda t: t.timestamp)

        logger.info("Got %d ticks for %s on %s", len(ticks), symbol, date_str)
        return ticks

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_records(raw: dict, symbol: str) -> list[dict]:
        """Pull the records list from the API response, with validation."""
        if "records" in raw:
            records = raw["records"]
        elif "data" in raw:
            records = raw["data"]
        elif isinstance(raw, list):
            records = raw
        else:
            # Check for error signals
            if raw.get("status") == "error" or raw.get("message", "").lower().find("invalid symbol") >= 0:
                raise TrueDataSymbolNotFoundError(symbol)
            logger.warning(
                "Unexpected response structure for %s; keys=%s", symbol, list(raw.keys())
            )
            return []

        if records is None:
            return []

        if not isinstance(records, list):
            logger.warning(
                "Expected list for records, got %s for %s", type(records).__name__, symbol
            )
            return []

        return records

    @staticmethod
    def _parse_bar(record: dict, symbol: str, interval: str) -> Optional[MarketBar]:
        """Parse one raw dict into a MarketBar, returning None on failure."""
        try:
            ts = _parse_timestamp(
                record.get("timestamp") or record.get("time") or record.get("bar_time")
            )
            if ts is None:
                logger.debug("Skipping bar with missing timestamp for %s", symbol)
                return None

            bar_time_raw = record.get("bar_time") or record.get("timestamp")
            bar_time = _parse_timestamp(bar_time_raw)

            return MarketBar(
                symbol=symbol,
                exchange=record.get("exchange", "NSE"),
                timestamp=ts,
                bar_time=bar_time,
                interval=interval,
                open=float(record.get("open", 0.0)),
                high=float(record.get("high", 0.0)),
                low=float(record.get("low", 0.0)),
                close=float(record.get("close", 0.0)),
                volume=int(record.get("volume", 0) or 0),
                oi=int(record["oi"]) if record.get("oi") is not None else None,
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Failed to parse bar record for %s: %s — %s", symbol, exc, record)
            return None

    @staticmethod
    def _parse_tick(record: dict, symbol: str) -> Optional[MarketTick]:
        """Parse one raw dict into a MarketTick, returning None on failure."""
        try:
            ts = _parse_timestamp(
                record.get("timestamp") or record.get("time")
            )
            if ts is None:
                return None

            ltp_raw = record.get("ltp") or record.get("price") or record.get("close")
            if ltp_raw is None:
                logger.debug("Skipping tick with no LTP for %s", symbol)
                return None

            return MarketTick(
                symbol=symbol,
                exchange=record.get("exchange", "NSE"),
                timestamp=ts,
                ltp=float(ltp_raw),
                open=_optional_float(record.get("open")),
                high=_optional_float(record.get("high")),
                low=_optional_float(record.get("low")),
                close=_optional_float(record.get("close")),
                volume=_optional_int(record.get("volume")),
                oi=_optional_int(record.get("oi")),
                bid=_optional_float(record.get("bid")),
                ask=_optional_float(record.get("ask")),
                bid_qty=_optional_int(record.get("bid_qty")),
                ask_qty=_optional_int(record.get("ask_qty")),
                seq=_optional_int(record.get("seq")),
                raw=record,
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Failed to parse tick record for %s: %s — %s", symbol, exc, record)
            return None

    @staticmethod
    def _deduplicate(bars: list[MarketBar]) -> list[MarketBar]:
        """Remove bars with duplicate timestamps, keeping the first."""
        seen: set[datetime] = set()
        deduped: list[MarketBar] = []
        dupes = 0
        for bar in bars:
            if bar.timestamp in seen:
                dupes += 1
            else:
                seen.add(bar.timestamp)
                deduped.append(bar)
        if dupes:
            logger.warning("Removed %d duplicate bars", dupes)
        return deduped

    @staticmethod
    def _validate_sequence(
        bars: list[MarketBar], symbol: str, interval: str
    ) -> list[MarketBar]:
        """
        Log any gaps in the bar sequence.  Does not remove bars.

        Currently only checks 1min, 3min, 5min, 15min, 30min intraday bars.
        """
        if not bars or interval == "1day":
            return bars

        interval_minutes = _interval_to_minutes(interval)
        if interval_minutes is None:
            return bars

        expected_delta = timedelta(minutes=interval_minutes)
        gaps = 0
        for i in range(1, len(bars)):
            actual_delta = bars[i].timestamp - bars[i - 1].timestamp
            if actual_delta > expected_delta * 1.5:  # allow 50% slack for market breaks
                gaps += 1
                logger.debug(
                    "Gap detected in %s %s between %s and %s (%.0f min)",
                    symbol, interval,
                    bars[i - 1].timestamp.isoformat(),
                    bars[i].timestamp.isoformat(),
                    actual_delta.total_seconds() / 60,
                )

        if gaps:
            logger.info("Detected %d gap(s) in %s %s bars", gaps, symbol, interval)
        return bars


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _to_date_str(dt: datetime | date) -> str:
    """Return ``YYYY-MM-DD`` string from date or datetime object."""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def _parse_timestamp(raw: object) -> Optional[datetime]:
    """
    Parse various timestamp formats into a timezone-aware IST datetime.

    Accepts:
    - ISO-8601 strings (with or without timezone)
    - Unix int/float (seconds since epoch)
    - Strings like ``"2026-05-01 09:15:00"``
    """
    if raw is None:
        return None
    ist = ZoneInfo("Asia/Kolkata")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=ist)
    if isinstance(raw, str):
        raw = raw.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ist)
                return dt.astimezone(ist)
            except ValueError:
                continue
    logger.debug("Cannot parse timestamp: %r", raw)
    return None


def _optional_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _optional_int(val: object) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _interval_to_minutes(interval: str) -> Optional[int]:
    """Map interval string to its minute count."""
    mapping = {
        "1min": 1,
        "3min": 3,
        "5min": 5,
        "10min": 10,
        "15min": 15,
        "30min": 30,
        "60min": 60,
    }
    return mapping.get(interval)
