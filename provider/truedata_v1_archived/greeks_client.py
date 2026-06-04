"""
TrueData Greeks client.

Fetches option Greeks for individual symbols or batches via the
``/options/greeks`` endpoint.  Validates that key fields (IV, delta)
are non-null and within expected ranges.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .exceptions import TrueDataAPIError, TrueDataSymbolNotFoundError
from .models import GreeksSnapshot
from .rest_client import TrueDataRestClient

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


class TrueDataGreeksClient:
    """
    Fetches and validates option Greeks from TrueData.

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

    def get_greeks(self, symbol: str) -> GreeksSnapshot:
        """
        Fetch Greeks for a single option symbol.

        Parameters
        ----------
        symbol:
            Full option symbol, e.g. ``"NIFTY24000CE"``.

        Returns
        -------
        GreeksSnapshot

        Raises
        ------
        TrueDataSymbolNotFoundError
            If the symbol is not found.
        TrueDataAPIError
            On unexpected API responses.
        """
        logger.debug("Fetching Greeks for %s", symbol)
        raw = self._rest.get("/options/greeks", params={"symbol": symbol})
        snapshot = self._parse_single(raw, symbol)
        return snapshot

    def get_chain_greeks(
        self,
        underlying: str,
        strikes: list[str],
    ) -> list[GreeksSnapshot]:
        """
        Fetch Greeks for multiple option symbols (one request per symbol).

        Parameters
        ----------
        underlying:
            Index name (used only for logging, not sent to API).
        strikes:
            List of full option symbol strings.

        Returns
        -------
        list[GreeksSnapshot]
            Successfully retrieved snapshots.  Symbols that fail are
            skipped with a warning.
        """
        results: list[GreeksSnapshot] = []
        for symbol in strikes:
            try:
                snap = self.get_greeks(symbol)
                results.append(snap)
            except TrueDataSymbolNotFoundError:
                logger.warning("Greeks not found for %s (skipping)", symbol)
            except TrueDataAPIError as exc:
                logger.warning("Greeks API error for %s: %s (skipping)", symbol, exc)
        logger.info(
            "Greeks fetched: %d/%d for %s",
            len(results), len(strikes), underlying,
        )
        return results

    @staticmethod
    def validate_greeks(snapshot: GreeksSnapshot) -> dict:
        """
        Validate a Greeks snapshot for data quality.

        Checks:
        - IV is non-null and > 0
        - Delta is in correct range: [0, 1] for CE, [-1, 0] for PE
        - Gamma is non-null and >= 0
        - Theta is non-null (typically negative for long options)
        - Vega is non-null and >= 0

        Parameters
        ----------
        snapshot:
            The Greeks snapshot to validate.

        Returns
        -------
        dict
            Keys:
            - ``valid`` (bool): True if all critical checks pass.
            - ``warnings`` (list[str]): Non-critical issues.
            - ``errors`` (list[str]): Critical validation failures.
            - ``null_fields`` (list[str]): Fields that are None.
        """
        errors: list[str] = []
        warnings: list[str] = []
        null_fields: list[str] = []

        # Check for nulls
        for field_name in ("iv", "delta", "gamma", "theta", "vega"):
            val = getattr(snapshot, field_name)
            if val is None:
                null_fields.append(field_name)

        # IV
        if snapshot.iv is None:
            errors.append("IV is null")
        elif snapshot.iv <= 0:
            errors.append(f"IV is non-positive: {snapshot.iv}")
        elif snapshot.iv > 300:
            warnings.append(f"IV seems very high: {snapshot.iv}%")

        # Delta range
        if snapshot.delta is None:
            errors.append("Delta is null")
        else:
            if snapshot.option_type == "CE":
                if not (0.0 <= snapshot.delta <= 1.0):
                    errors.append(
                        f"CE delta out of range [0,1]: {snapshot.delta}"
                    )
            else:  # PE
                if not (-1.0 <= snapshot.delta <= 0.0):
                    errors.append(
                        f"PE delta out of range [-1,0]: {snapshot.delta}"
                    )

        # Gamma (should be >= 0)
        if snapshot.gamma is None:
            warnings.append("Gamma is null")
        elif snapshot.gamma < 0:
            warnings.append(f"Gamma is negative: {snapshot.gamma}")

        # Theta (typically negative for long options, but API may return either sign)
        if snapshot.theta is None:
            warnings.append("Theta is null")

        # Vega (should be >= 0)
        if snapshot.vega is None:
            warnings.append("Vega is null")
        elif snapshot.vega < 0:
            warnings.append(f"Vega is negative: {snapshot.vega}")

        return {
            "valid": len(errors) == 0,
            "warnings": warnings,
            "errors": errors,
            "null_fields": null_fields,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_single(self, raw: dict | list, symbol: str) -> GreeksSnapshot:
        """Parse API response into a GreeksSnapshot."""
        now_ist = datetime.now(tz=_IST)

        if isinstance(raw, list):
            record = raw[0] if raw else {}
        elif isinstance(raw, dict):
            if raw.get("status") == "error":
                msg = raw.get("message", "")
                if "invalid" in msg.lower() or "not found" in msg.lower():
                    raise TrueDataSymbolNotFoundError(symbol)
                raise TrueDataAPIError(f"Greeks API error: {msg}", status_code=200)
            record = raw.get("data") or raw.get("greeks") or raw
        else:
            raise TrueDataAPIError(
                f"Unexpected Greeks response type: {type(raw)}", status_code=200
            )

        return _build_snapshot(record, symbol, now_ist)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_snapshot(
    record: dict,
    symbol: str,
    timestamp: datetime,
) -> GreeksSnapshot:
    """Build a GreeksSnapshot from a raw API record dict."""
    # Infer option type from symbol name if not in record
    opt_type_raw = (
        record.get("option_type")
        or record.get("OptionType")
        or ("CE" if symbol.upper().endswith("CE") else "PE")
    )
    opt_type = "CE" if str(opt_type_raw).upper() in ("CE", "CALL", "C") else "PE"

    underlying = str(
        record.get("underlying") or record.get("Underlying") or _infer_underlying(symbol)
    ).upper()

    strike_raw = record.get("strike") or record.get("StrikePrice") or _infer_strike(symbol)
    strike = float(strike_raw) if strike_raw is not None else 0.0

    expiry = str(record.get("expiry") or record.get("Expiry") or "").strip()

    return GreeksSnapshot(
        symbol=symbol,
        underlying=underlying,
        strike=strike,
        option_type=opt_type,  # type: ignore[arg-type]
        expiry=expiry,
        iv=_opt_float(record.get("iv") or record.get("IV")),
        delta=_opt_float(record.get("delta") or record.get("Delta")),
        gamma=_opt_float(record.get("gamma") or record.get("Gamma")),
        theta=_opt_float(record.get("theta") or record.get("Theta")),
        vega=_opt_float(record.get("vega") or record.get("Vega")),
        rho=_opt_float(record.get("rho") or record.get("Rho")),
        timestamp=timestamp,
    )


def _infer_underlying(symbol: str) -> str:
    """Guess the underlying from a symbol like NIFTY24000CE."""
    for idx in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX"):
        if symbol.upper().startswith(idx):
            return idx
    return symbol[:5]


def _infer_strike(symbol: str) -> Optional[float]:
    """Try to extract the strike from a symbol like NIFTY24000CE."""
    import re
    match = re.search(r"(\d{4,6})(CE|PE)$", symbol.upper())
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def _opt_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
