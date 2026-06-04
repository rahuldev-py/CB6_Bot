"""
Trial test: Option chain data quality.

Fetches NIFTY + BANKNIFTY option chains (ATM ± strike_range).
Validates OI presence, bid/ask spreads, strike spacing consistency,
and ATM detection.  Saves snapshot to JSON.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from provider.truedata import (
    TrueDataAuth,
    TrueDataConfig,
    TrueDataRestClient,
    TrialResult,
)
from provider.truedata.models import OptionChainRow
from provider.truedata.option_chain import TrueDataOptionChain

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


def run_option_chain_test(config: TrueDataConfig) -> TrialResult:
    """
    Fetch option chains for all configured underlyings, validate quality,
    and save snapshot to JSON.

    Validation:
    - At least some rows have non-null OI.
    - Bid and ask non-zero for ITM/ATM strikes.
    - Strike spacing is consistent (no jumps).
    - ATM detection returns the closest strike to a synthetic spot.

    Parameters
    ----------
    config:
        TrueData configuration.

    Returns
    -------
    TrialResult
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
    chain_client = TrueDataOptionChain(rest)

    output_dir = config.data_dir / "trial_option_chain"
    output_dir.mkdir(parents=True, exist_ok=True)

    underlyings = config.option_underlyings
    strike_range = config.option_strike_range

    summary: dict = {}
    total_rows = 0
    underlyings_ok = 0

    for underlying in underlyings:
        logger.info("Fetching option chain: %s ATM±%d", underlying, strike_range)
        try:
            # Fetch full chain first
            full_chain = chain_client.get_option_chain(underlying)

            if not full_chain:
                errors.append(f"{underlying}: empty chain returned")
                summary[underlying] = {"rows": 0, "error": "empty chain"}
                continue

            # Get synthetic spot from ATM (mid-point of CE LTPs)
            spot = _estimate_spot(full_chain)

            # ATM-filtered chain
            atm_chain = chain_client.get_atm_chain(
                underlying, spot, n_strikes=strike_range
            )

            # Validation
            oi_filled = sum(1 for r in atm_chain if r.oi is not None and r.oi > 0)
            bid_ask_ok = sum(
                1 for r in atm_chain
                if r.bid is not None and r.ask is not None
                and r.bid > 0 and r.ask > 0
            )
            strikes = sorted(set(r.strike for r in atm_chain))
            spacing_ok, spacing = _check_strike_spacing(strikes)

            # ATM detection
            atm_row = chain_client.detect_atm(atm_chain, spot)
            atm_correct = abs(atm_row.strike - spot) <= (spacing or 50) * 1.5

            metrics = {
                "rows": len(atm_chain),
                "full_chain_rows": len(full_chain),
                "synthetic_spot": round(spot, 2),
                "atm_strike": atm_row.strike,
                "atm_correct": atm_correct,
                "oi_filled_rows": oi_filled,
                "oi_fill_pct": round(oi_filled / max(len(atm_chain), 1) * 100, 1),
                "bid_ask_ok_rows": bid_ask_ok,
                "bid_ask_ok_pct": round(bid_ask_ok / max(len(atm_chain), 1) * 100, 1),
                "strike_spacing_consistent": spacing_ok,
                "detected_spacing": spacing,
                "strikes_count": len(strikes),
            }
            summary[underlying] = metrics
            total_rows += len(atm_chain)

            if oi_filled > 0 and len(atm_chain) > 0:
                underlyings_ok += 1

            # Save snapshot JSON
            _save_snapshot(atm_chain, underlying, output_dir)

        except Exception as exc:
            err_msg = f"{underlying}: {exc}"
            errors.append(err_msg)
            logger.error(err_msg)
            summary[underlying] = {"rows": 0, "error": str(exc)}

    details.update({
        "underlyings": underlyings,
        "strike_range": strike_range,
        "total_rows": total_rows,
        "underlyings_ok": underlyings_ok,
        "output_dir": str(output_dir),
        "summary": summary,
    })

    # Scoring: option chain quality 10 pts
    score = 0
    if underlyings_ok == len(underlyings):
        score = 10
    elif underlyings_ok >= 1:
        score = 5

    # Partial credit for bid/ask data
    if underlyings_ok > 0:
        avg_ba_pct = sum(
            s.get("bid_ask_ok_pct", 0)
            for s in summary.values()
            if isinstance(s, dict)
        ) / max(len(underlyings), 1)
        if avg_ba_pct < 30:
            score = max(score - 3, 0)

    passed = underlyings_ok >= 1 and total_rows > 0 and not errors

    return _build_result(started_at, passed, score, details, errors)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _estimate_spot(chain: list[OptionChainRow]) -> float:
    """
    Estimate the spot price from the option chain.

    Uses the LTP of ATM CE as a proxy.  Falls back to the median strike.
    """
    ce_rows = [r for r in chain if r.option_type == "CE" and r.ltp is not None and r.ltp > 0]
    if not ce_rows:
        strikes = sorted(set(r.strike for r in chain))
        return strikes[len(strikes) // 2] if strikes else 0.0

    # The CE with the highest OI is closest to ATM
    by_oi = sorted(ce_rows, key=lambda r: r.oi or 0, reverse=True)
    return float(by_oi[0].strike)


def _check_strike_spacing(strikes: list[float]) -> tuple[bool, Optional[float]]:
    """
    Check whether strike spacing is consistent.

    Returns (is_consistent, detected_spacing).
    Consistent = all gaps between consecutive strikes are equal.
    """
    if len(strikes) < 2:
        return True, None

    gaps = [strikes[i + 1] - strikes[i] for i in range(len(strikes) - 1)]
    min_gap = min(gaps)
    max_gap = max(gaps)

    # Allow up to 1.1× variation (floating point tolerance)
    consistent = (max_gap - min_gap) < (min_gap * 0.1 + 0.01)
    return consistent, round(min_gap, 2)


def _save_snapshot(
    chain: list[OptionChainRow],
    underlying: str,
    output_dir: Path,
) -> None:
    """Save option chain snapshot to a JSON file."""
    path = output_dir / f"{underlying}_option_chain_snapshot.json"
    try:
        records = []
        for row in chain:
            records.append({
                "symbol": row.symbol,
                "strike": row.strike,
                "option_type": row.option_type,
                "expiry": row.expiry,
                "ltp": row.ltp,
                "bid": row.bid,
                "ask": row.ask,
                "oi": row.oi,
                "oi_change": row.oi_change,
                "volume": row.volume,
                "iv": row.iv,
                "delta": row.delta,
                "gamma": row.gamma,
                "theta": row.theta,
                "vega": row.vega,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "underlying": underlying,
                    "snapshot_time": datetime.now(tz=_IST).isoformat(),
                    "rows": records,
                },
                f,
                indent=2,
            )
        logger.info("Saved option chain snapshot for %s → %s", underlying, path)
    except OSError as exc:
        logger.error("Failed to save option chain snapshot: %s", exc)


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
        test_name="Option Chain",
        passed=passed,
        score=score,
        details=details,
        errors=errors,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=(ended_at - started_at).total_seconds(),
    )
