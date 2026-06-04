"""
TrueData symbol master client.

Downloads the complete symbol list, parses it into :class:`SymbolInfo`
objects, and provides filtering/lookup helpers.  Results are cached in
memory for the session to avoid repeated downloads.
"""

from __future__ import annotations

import logging
from typing import Optional

from .exceptions import TrueDataSymbolNotFoundError
from .models import SymbolInfo
from .rest_client import TrueDataRestClient

logger = logging.getLogger(__name__)

# TrueData segment values
_FO_SEGMENTS = {"nse_fo", "nse-fo", "fo", "NSE_FO"}
_INDEX_KEYWORDS = {"NIFTY-I", "BANKNIFTY-I", "FINNIFTY-I", "MIDCPNIFTY-I"}
_INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}


class TrueDataSymbolMaster:
    """
    Provides symbol lookup and filtering on top of the TrueData symbol master.

    The master list is fetched once on the first call to any method that
    needs it, then cached in memory.  Call :meth:`refresh` to force a
    fresh download.

    Parameters
    ----------
    rest_client:
        A configured :class:`TrueDataRestClient` instance.
    """

    def __init__(self, rest_client: TrueDataRestClient) -> None:
        self._rest = rest_client
        self._symbols: list[SymbolInfo] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Force a fresh download of the symbol master."""
        self._loaded = False
        self._ensure_loaded()

    def get_all_symbols(self) -> list[SymbolInfo]:
        """
        Return all symbols from the symbol master.

        Returns
        -------
        list[SymbolInfo]
        """
        self._ensure_loaded()
        return list(self._symbols)

    def get_fo_symbols(self) -> list[SymbolInfo]:
        """
        Return F&O (futures + options) symbols only.

        Returns
        -------
        list[SymbolInfo]
        """
        self._ensure_loaded()
        return [
            s for s in self._symbols
            if s.is_futures or s.is_options or _is_fo_segment(s.segment)
        ]

    def get_index_symbols(self) -> list[SymbolInfo]:
        """
        Return index / continuous contract symbols (e.g. ``NIFTY-I``).

        Returns
        -------
        list[SymbolInfo]
        """
        self._ensure_loaded()
        return [s for s in self._symbols if s.is_index]

    def find_symbol(self, name: str) -> Optional[SymbolInfo]:
        """
        Find a symbol by exact name (case-insensitive).

        Parameters
        ----------
        name:
            Symbol string, e.g. ``"NIFTY-I"`` or ``"NIFTY24000CE"``.

        Returns
        -------
        SymbolInfo or None
        """
        self._ensure_loaded()
        name_upper = name.upper()
        for s in self._symbols:
            if s.symbol.upper() == name_upper:
                return s
        return None

    def get_option_strikes(
        self, underlying: str, expiry: str
    ) -> list[SymbolInfo]:
        """
        Return all option contracts for the given underlying and expiry.

        Parameters
        ----------
        underlying:
            Index name, e.g. ``"NIFTY"``.
        expiry:
            Expiry date string, e.g. ``"2026-05-29"``.

        Returns
        -------
        list[SymbolInfo]
            Sorted by strike, then by option_type (CE < PE).
        """
        self._ensure_loaded()
        underlying_upper = underlying.upper()
        rows = [
            s for s in self._symbols
            if s.is_options
            and (s.underlying or "").upper() == underlying_upper
            and (s.expiry or "") == expiry
        ]
        rows.sort(key=lambda s: (s.strike or 0.0, s.option_type or ""))
        return rows

    def get_atm_strikes(
        self,
        underlying: str,
        spot: float,
        n_strikes: int = 10,
        expiry: Optional[str] = None,
    ) -> list[SymbolInfo]:
        """
        Return the ``n_strikes`` closest strikes on each side of the spot price.

        Parameters
        ----------
        underlying:
            Index name, e.g. ``"NIFTY"``.
        spot:
            Current spot price.
        n_strikes:
            Number of strikes on each side to include (so total ≤ 2 × n_strikes
            per option type).
        expiry:
            Optional filter; if None, uses the nearest available expiry.

        Returns
        -------
        list[SymbolInfo]
            CE and PE symbols for the selected strikes.
        """
        self._ensure_loaded()

        if expiry:
            candidates = self.get_option_strikes(underlying, expiry)
        else:
            # Find nearest expiry
            candidates = [
                s for s in self._symbols
                if s.is_options
                and (s.underlying or "").upper() == underlying.upper()
            ]
            if not candidates:
                return []
            nearest_expiry = min(
                (s.expiry for s in candidates if s.expiry),
                default=None,
            )
            if not nearest_expiry:
                return []
            candidates = [s for s in candidates if s.expiry == nearest_expiry]

        # Get unique strikes sorted by distance from spot
        strikes: list[float] = sorted(
            set(s.strike for s in candidates if s.strike is not None),
            key=lambda x: abs(x - spot),
        )
        selected_strikes = set(strikes[:n_strikes * 2])  # n_strikes on each side approx

        result = [s for s in candidates if s.strike in selected_strikes]
        result.sort(key=lambda s: (s.strike or 0.0, s.option_type or ""))
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Download and parse the symbol master if not already loaded."""
        if not self._loaded:
            self._fetch()

    def _fetch(self) -> None:
        """Download symbol master and populate the in-memory cache."""
        logger.info("Downloading TrueData symbol master")
        raw = self._rest.get("/symbols/symbolmaster", params={})

        # API may return list directly or wrapped in a key
        if isinstance(raw, list):
            records = raw
        elif isinstance(raw, dict):
            records = (
                raw.get("symbols")
                or raw.get("data")
                or raw.get("records")
                or []
            )
        else:
            logger.warning("Unexpected symbol master response type: %s", type(raw))
            records = []

        self._symbols = [
            info
            for info in (_parse_symbol_record(r) for r in records)
            if info is not None
        ]
        self._loaded = True
        logger.info("Symbol master loaded: %d symbols", len(self._symbols))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_symbol_record(record: dict) -> Optional[SymbolInfo]:
    """Parse one row from the TrueData symbol master into SymbolInfo."""
    try:
        symbol = str(record.get("symbol") or record.get("Symbol") or "").strip()
        if not symbol:
            return None

        segment = str(record.get("segment") or record.get("exchange_segment") or "").lower()
        exchange = str(record.get("exchange") or "NSE").upper()
        underlying = record.get("underlying") or record.get("Underlying") or None
        option_type_raw = record.get("option_type") or record.get("OptionType") or None
        option_type = None
        if option_type_raw:
            option_type = "CE" if str(option_type_raw).upper() in ("CE", "CALL", "C") else "PE"

        is_futures = bool(
            record.get("is_futures")
            or "FUT" in symbol.upper()
            or "future" in str(record.get("instrument_type", "")).lower()
        )
        is_options = bool(
            option_type
            or record.get("is_options")
            or symbol.upper().endswith(("CE", "PE"))
        )
        is_index = bool(
            record.get("is_index")
            or symbol in _INDEX_KEYWORDS
            or symbol.endswith("-I")
        )

        strike_raw = record.get("strike") or record.get("StrikePrice")
        strike = float(strike_raw) if strike_raw is not None else None

        lot_raw = record.get("lot_size") or record.get("LotSize")
        lot_size = int(lot_raw) if lot_raw is not None else None

        tick_raw = record.get("tick_size") or record.get("TickSize")
        tick_size = float(tick_raw) if tick_raw is not None else None

        expiry = record.get("expiry") or record.get("Expiry") or None
        if expiry:
            expiry = str(expiry).strip()

        return SymbolInfo(
            symbol=symbol,
            exchange=exchange,
            segment=segment or None,
            lot_size=lot_size,
            tick_size=tick_size,
            expiry=expiry,
            strike=strike,
            option_type=option_type,
            underlying=str(underlying).upper() if underlying else None,
            is_index=is_index,
            is_futures=is_futures,
            is_options=is_options,
        )
    except Exception as exc:
        logger.debug("Failed to parse symbol record: %s — %s", exc, record)
        return None


def _is_fo_segment(segment: Optional[str]) -> bool:
    if not segment:
        return False
    return segment.lower().replace("-", "_") in {s.lower().replace("-", "_") for s in _FO_SEGMENTS}
