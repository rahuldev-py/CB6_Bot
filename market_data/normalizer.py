"""
TrueData → CB6 internal model normalizer.

Converts raw TrueData API response dicts into the typed Pydantic models
used across CB6 Quantum.  Handles missing/null fields gracefully and
normalizes all timestamps to IST (Asia/Kolkata).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from provider.truedata.models import (
    GreeksSnapshot,
    MarketBar,
    MarketTick,
    OptionChainRow,
    SymbolInfo,
)

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Timestamp normalizer
# ---------------------------------------------------------------------------


def normalize_timestamp(raw: Any, fallback_to_now: bool = True) -> Optional[datetime]:
    """
    Convert various raw timestamp representations to a timezone-aware IST datetime.

    Accepted formats:
    - Unix int/float (seconds since epoch)
    - ISO-8601 string (with or without timezone)
    - ``"YYYY-MM-DD HH:MM:SS"`` string
    - ``"YYYY-MM-DD"`` string (assumes start of day in IST)

    Parameters
    ----------
    raw:
        The raw timestamp value from TrueData API.
    fallback_to_now:
        If True and parsing fails, return the current IST time.
        If False, return None on failure.

    Returns
    -------
    datetime or None
    """
    if raw is None:
        return datetime.now(tz=_IST) if fallback_to_now else None

    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=_IST)
        return raw.astimezone(_IST)

    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=_IST)
        except (ValueError, OSError):
            pass

    if isinstance(raw, str):
        raw_s = raw.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(raw_s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_IST)
                return dt.astimezone(_IST)
            except ValueError:
                continue

    logger.debug("Cannot parse timestamp: %r", raw)
    return datetime.now(tz=_IST) if fallback_to_now else None


# ---------------------------------------------------------------------------
# Tick normalizer
# ---------------------------------------------------------------------------


def normalize_tick(raw: dict[str, Any], default_exchange: str = "NSE") -> Optional[MarketTick]:
    """
    Normalize a raw TrueData tick message dict into a :class:`MarketTick`.

    Parameters
    ----------
    raw:
        Raw dict from WebSocket or tick history API.
    default_exchange:
        Exchange to use if the ``exchange`` field is absent.

    Returns
    -------
    MarketTick or None
        Returns None if the symbol or LTP fields are missing.
    """
    symbol = (
        raw.get("symbol")
        or raw.get("sym")
        or raw.get("Symbol")
    )
    if not symbol:
        logger.debug("normalize_tick: missing symbol in %s", list(raw.keys()))
        return None

    ltp_raw = raw.get("ltp") or raw.get("price") or raw.get("last_price") or raw.get("LTP")
    if ltp_raw is None:
        logger.debug("normalize_tick: missing ltp for %s", symbol)
        return None

    ts = normalize_timestamp(
        raw.get("timestamp") or raw.get("time") or raw.get("exchange_time") or raw.get("ts"),
        fallback_to_now=True,
    )

    return MarketTick(
        symbol=str(symbol).strip(),
        exchange=str(raw.get("exchange") or default_exchange).upper(),
        timestamp=ts,
        ltp=float(ltp_raw),
        open=_opt_float(raw.get("open") or raw.get("Open")),
        high=_opt_float(raw.get("high") or raw.get("High")),
        low=_opt_float(raw.get("low") or raw.get("Low")),
        close=_opt_float(raw.get("close") or raw.get("prev_close") or raw.get("Close")),
        volume=_opt_int(raw.get("volume") or raw.get("vol") or raw.get("Volume")),
        oi=_opt_int(raw.get("oi") or raw.get("open_interest") or raw.get("OI")),
        bid=_opt_float(raw.get("bid") or raw.get("Bid")),
        ask=_opt_float(raw.get("ask") or raw.get("Ask")),
        bid_qty=_opt_int(raw.get("bid_qty") or raw.get("BidQty")),
        ask_qty=_opt_int(raw.get("ask_qty") or raw.get("AskQty")),
        seq=_opt_int(raw.get("seq") or raw.get("Seq")),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Bar normalizer
# ---------------------------------------------------------------------------


def normalize_bar(
    raw: dict[str, Any],
    symbol: str,
    interval: str,
    default_exchange: str = "NSE",
) -> Optional[MarketBar]:
    """
    Normalize a raw TrueData candle record into a :class:`MarketBar`.

    Parameters
    ----------
    raw:
        Raw dict from historical or bar-feed API.
    symbol:
        Symbol string (not always present in record rows).
    interval:
        Bar interval string, e.g. ``"1min"``.
    default_exchange:
        Exchange to use if absent from record.

    Returns
    -------
    MarketBar or None
        Returns None if critical fields (timestamp, OHLC) are missing.
    """
    ts_raw = (
        raw.get("timestamp")
        or raw.get("time")
        or raw.get("bar_time")
        or raw.get("Timestamp")
    )
    ts = normalize_timestamp(ts_raw, fallback_to_now=False)
    if ts is None:
        logger.debug("normalize_bar: missing timestamp for %s", symbol)
        return None

    try:
        return MarketBar(
            symbol=str(raw.get("symbol") or symbol).strip(),
            exchange=str(raw.get("exchange") or default_exchange).upper(),
            timestamp=ts,
            bar_time=normalize_timestamp(raw.get("bar_time"), fallback_to_now=False),
            interval=interval,
            open=float(raw.get("open") or raw.get("Open") or 0.0),
            high=float(raw.get("high") or raw.get("High") or 0.0),
            low=float(raw.get("low") or raw.get("Low") or 0.0),
            close=float(raw.get("close") or raw.get("Close") or 0.0),
            volume=int(raw.get("volume") or raw.get("Volume") or 0),
            oi=_opt_int(raw.get("oi") or raw.get("OI")),
        )
    except (ValueError, TypeError) as exc:
        logger.debug("normalize_bar: parse error for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Option chain normalizer
# ---------------------------------------------------------------------------


def normalize_option_chain_row(
    raw: dict[str, Any],
    underlying: str,
    expiry: Optional[str],
    snapshot_time: Optional[datetime] = None,
) -> Optional[OptionChainRow]:
    """
    Normalize a raw option chain record into an :class:`OptionChainRow`.

    Parameters
    ----------
    raw:
        Raw dict from the option chain API.
    underlying:
        Index name.
    expiry:
        Expiry date string.
    snapshot_time:
        Snapshot timestamp; defaults to now in IST.

    Returns
    -------
    OptionChainRow or None
    """
    symbol = str(raw.get("symbol") or raw.get("Symbol") or "").strip()
    if not symbol:
        return None

    strike_raw = raw.get("strike") or raw.get("StrikePrice")
    if strike_raw is None:
        return None

    opt_type_raw = (
        raw.get("option_type") or raw.get("OptionType") or raw.get("type") or ""
    )
    opt_type = str(opt_type_raw).upper()
    if opt_type not in ("CE", "PE"):
        if symbol.upper().endswith("CE"):
            opt_type = "CE"
        elif symbol.upper().endswith("PE"):
            opt_type = "PE"
        else:
            return None

    ts = snapshot_time or datetime.now(tz=_IST)
    row_expiry = str(raw.get("expiry") or raw.get("Expiry") or expiry or "").strip()

    try:
        return OptionChainRow(
            symbol=symbol,
            underlying=underlying.upper(),
            strike=float(strike_raw),
            option_type=opt_type,  # type: ignore[arg-type]
            expiry=row_expiry,
            ltp=_opt_float(raw.get("ltp") or raw.get("LTP")),
            bid=_opt_float(raw.get("bid") or raw.get("Bid")),
            ask=_opt_float(raw.get("ask") or raw.get("Ask")),
            oi=_opt_int(raw.get("oi") or raw.get("OI")),
            oi_change=_opt_int(raw.get("oi_change") or raw.get("OIChange")),
            volume=_opt_int(raw.get("volume") or raw.get("Volume")),
            iv=_opt_float(raw.get("iv") or raw.get("IV")),
            delta=_opt_float(raw.get("delta") or raw.get("Delta")),
            gamma=_opt_float(raw.get("gamma") or raw.get("Gamma")),
            theta=_opt_float(raw.get("theta") or raw.get("Theta")),
            vega=_opt_float(raw.get("vega") or raw.get("Vega")),
            rho=_opt_float(raw.get("rho") or raw.get("Rho")),
            timestamp=ts,
        )
    except (ValueError, TypeError) as exc:
        logger.debug("normalize_option_chain_row error: %s — %s", exc, raw)
        return None


# ---------------------------------------------------------------------------
# Symbol info normalizer
# ---------------------------------------------------------------------------


def normalize_symbol_info(raw: dict[str, Any]) -> Optional[SymbolInfo]:
    """
    Normalize a raw symbol master record into a :class:`SymbolInfo`.

    Parameters
    ----------
    raw:
        Raw dict from the symbol master API.

    Returns
    -------
    SymbolInfo or None
    """
    symbol = str(raw.get("symbol") or raw.get("Symbol") or "").strip()
    if not symbol:
        return None

    opt_type_raw = raw.get("option_type") or raw.get("OptionType") or ""
    opt_type = str(opt_type_raw).upper()
    if opt_type not in ("CE", "PE"):
        opt_type_val = None
    else:
        opt_type_val = opt_type  # type: ignore[assignment]

    underlying = raw.get("underlying") or raw.get("Underlying")

    return SymbolInfo(
        symbol=symbol,
        exchange=str(raw.get("exchange") or "NSE").upper(),
        segment=str(raw.get("segment") or raw.get("exchange_segment") or "").lower() or None,
        lot_size=_opt_int(raw.get("lot_size") or raw.get("LotSize")),
        tick_size=_opt_float(raw.get("tick_size") or raw.get("TickSize")),
        expiry=str(raw.get("expiry") or raw.get("Expiry") or "").strip() or None,
        strike=_opt_float(raw.get("strike") or raw.get("StrikePrice")),
        option_type=opt_type_val,
        underlying=str(underlying).upper() if underlying else None,
        is_index=bool(raw.get("is_index") or symbol.endswith("-I")),
        is_futures=bool(raw.get("is_futures") or "FUT" in symbol.upper()),
        is_options=bool(opt_type_val or symbol.upper().endswith(("CE", "PE"))),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _opt_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _opt_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
