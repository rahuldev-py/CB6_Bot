"""
TrueData option chain client.

Fetches option chain snapshots (with Greeks) for NIFTY, BANKNIFTY, etc.
and provides ATM-aware filtering helpers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .exceptions import TrueDataAPIError, TrueDataSymbolNotFoundError
from .models import OptionChainRow
from .rest_client import TrueDataRestClient

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


class TrueDataOptionChain:
    """
    Retrieves and filters option chain data from TrueData.

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

    def get_option_chain(
        self,
        underlying: str,
        expiry: Optional[str] = None,
    ) -> list[OptionChainRow]:
        """
        Fetch the full option chain for an underlying.

        Parameters
        ----------
        underlying:
            Index name, e.g. ``"NIFTY"`` or ``"BANKNIFTY"``.
        expiry:
            Optional expiry date string ``"YYYY-MM-DD"``.  If None, the
            API returns the nearest expiry.

        Returns
        -------
        list[OptionChainRow]
            Sorted by strike (ascending), CE before PE.

        Raises
        ------
        TrueDataSymbolNotFoundError
            If the underlying is not found.
        TrueDataAPIError
            On unexpected API responses.
        """
        params: dict = {"underlying": underlying.upper()}
        if expiry:
            params["expiry"] = expiry

        logger.info(
            "Fetching option chain: underlying=%s expiry=%s",
            underlying, expiry or "nearest",
        )

        raw = self._rest.get("/options/chain", params=params)
        rows = self._parse_chain_response(raw, underlying, expiry)

        rows.sort(key=lambda r: (r.strike, r.option_type))
        logger.info(
            "Option chain for %s expiry=%s: %d rows",
            underlying, expiry or "nearest", len(rows),
        )
        return rows

    def get_atm_chain(
        self,
        underlying: str,
        spot: float,
        n_strikes: int = 10,
        expiry: Optional[str] = None,
    ) -> list[OptionChainRow]:
        """
        Return option chain rows within ``n_strikes`` strikes of spot.

        Parameters
        ----------
        underlying:
            Index name.
        spot:
            Current spot price; used to find ATM strike.
        n_strikes:
            Number of strikes on each side of ATM to include.
        expiry:
            Optional expiry filter.

        Returns
        -------
        list[OptionChainRow]
        """
        chain = self.get_option_chain(underlying, expiry)
        if not chain:
            return []

        atm = self.detect_atm(chain, spot)
        atm_strike = atm.strike

        # Collect all unique strikes, sorted
        all_strikes = sorted(set(r.strike for r in chain))
        try:
            atm_idx = all_strikes.index(atm_strike)
        except ValueError:
            atm_idx = 0

        lo = max(0, atm_idx - n_strikes)
        hi = min(len(all_strikes), atm_idx + n_strikes + 1)
        selected = set(all_strikes[lo:hi])

        filtered = [r for r in chain if r.strike in selected]
        filtered.sort(key=lambda r: (r.strike, r.option_type))
        return filtered

    @staticmethod
    def detect_atm(
        chain: list[OptionChainRow],
        spot: float,
    ) -> OptionChainRow:
        """
        Find the ATM row (CE preferred) closest to ``spot``.

        Parameters
        ----------
        chain:
            List of :class:`OptionChainRow` objects.
        spot:
            Current spot price.

        Returns
        -------
        OptionChainRow
            The row whose strike is closest to spot.

        Raises
        ------
        ValueError
            If ``chain`` is empty.
        """
        if not chain:
            raise ValueError("Cannot detect ATM on empty option chain")

        # Prefer CE rows for ATM detection
        ces = [r for r in chain if r.option_type == "CE"]
        candidates = ces if ces else chain

        atm = min(candidates, key=lambda r: abs(r.strike - spot))
        logger.debug(
            "ATM detected: strike=%.0f spot=%.2f diff=%.2f",
            atm.strike, spot, abs(atm.strike - spot),
        )
        return atm

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_chain_response(
        self,
        raw: dict | list,
        underlying: str,
        expiry: Optional[str],
    ) -> list[OptionChainRow]:
        """Parse the raw API response into a list of OptionChainRow."""
        now_ist = datetime.now(tz=_IST)

        if isinstance(raw, list):
            records = raw
        elif isinstance(raw, dict):
            # Check for error
            if raw.get("status") == "error":
                msg = raw.get("message", "")
                if "invalid" in msg.lower() or "not found" in msg.lower():
                    raise TrueDataSymbolNotFoundError(underlying)
                raise TrueDataAPIError(
                    f"Option chain API error: {msg}", status_code=200
                )
            records = (
                raw.get("records")
                or raw.get("data")
                or raw.get("chain")
                or []
            )
        else:
            logger.warning("Unexpected option chain response type: %s", type(raw))
            return []

        rows: list[OptionChainRow] = []
        for record in records:
            row = _parse_chain_row(record, underlying, expiry, now_ist)
            if row is not None:
                rows.append(row)

        return rows


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_chain_row(
    record: dict,
    underlying: str,
    expiry: Optional[str],
    snapshot_time: datetime,
) -> Optional[OptionChainRow]:
    """Parse one raw dict into an OptionChainRow."""
    try:
        symbol = str(record.get("symbol") or "").strip()
        if not symbol:
            return None

        strike_raw = record.get("strike") or record.get("StrikePrice")
        if strike_raw is None:
            return None
        strike = float(strike_raw)

        opt_type_raw = (
            record.get("option_type")
            or record.get("OptionType")
            or record.get("type")
            or ""
        )
        opt_type = opt_type_raw.upper()
        if opt_type not in ("CE", "PE"):
            # Try to infer from symbol
            if symbol.upper().endswith("CE"):
                opt_type = "CE"
            elif symbol.upper().endswith("PE"):
                opt_type = "PE"
            else:
                return None

        row_expiry = (
            str(record.get("expiry") or record.get("Expiry") or expiry or "").strip()
        )

        return OptionChainRow(
            symbol=symbol,
            underlying=underlying.upper(),
            strike=strike,
            option_type=opt_type,  # type: ignore[arg-type]
            expiry=row_expiry,
            ltp=_opt_float(record.get("ltp") or record.get("LTP")),
            bid=_opt_float(record.get("bid")),
            ask=_opt_float(record.get("ask")),
            oi=_opt_int(record.get("oi") or record.get("OI")),
            oi_change=_opt_int(record.get("oi_change") or record.get("OIChange")),
            volume=_opt_int(record.get("volume") or record.get("Volume")),
            iv=_opt_float(record.get("iv") or record.get("IV")),
            delta=_opt_float(record.get("delta") or record.get("Delta")),
            gamma=_opt_float(record.get("gamma") or record.get("Gamma")),
            theta=_opt_float(record.get("theta") or record.get("Theta")),
            vega=_opt_float(record.get("vega") or record.get("Vega")),
            rho=_opt_float(record.get("rho") or record.get("Rho")),
            timestamp=snapshot_time,
        )
    except Exception as exc:
        logger.debug("Failed to parse option chain row: %s — %s", exc, record)
        return None


def _estimate_spot_from_chain(chain: list[OptionChainRow]) -> float:
    """
    Estimate spot from a full option chain.

    Uses the strike with the highest OI in CE rows as a proxy for ATM.
    Falls back to the median strike.
    """
    ce_rows = [r for r in chain if r.option_type == "CE" and r.oi is not None and r.oi > 0]
    if ce_rows:
        best = max(ce_rows, key=lambda r: r.oi or 0)
        return float(best.strike)
    strikes = sorted(set(r.strike for r in chain))
    return strikes[len(strikes) // 2] if strikes else 0.0


def _opt_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _opt_int(val: object) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
