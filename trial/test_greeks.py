"""
Trial test: Option Greeks data quality.

Fetches Greeks for ATM ± 5 strikes on NIFTY and BANKNIFTY (both CE
and PE).  Validates IV non-zero, delta ranges, gamma/theta signs.
Reports null fields.  Saves to CSV.
"""

from __future__ import annotations

import csv
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
from provider.truedata.greeks_client import TrueDataGreeksClient
from provider.truedata.models import GreeksSnapshot
from provider.truedata.option_chain import TrueDataOptionChain

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

_STRIKES_PER_SIDE = 5


def run_greeks_test(config: TrueDataConfig) -> TrialResult:
    """
    Fetch Greeks for ATM ± 5 strikes (CE + PE) on all configured underlyings.

    Validation:
    - IV is non-null and > 0.
    - CE delta in [0, 1]; PE delta in [-1, 0].
    - Gamma >= 0.
    - Theta is non-null.
    - Vega >= 0.

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
    greeks_client = TrueDataGreeksClient(rest)

    output_dir = config.data_dir / "trial_greeks"
    output_dir.mkdir(parents=True, exist_ok=True)

    underlyings = config.option_underlyings
    summary: dict = {}
    total_fetched = 0
    total_valid = 0
    all_snapshots: list[GreeksSnapshot] = []

    for underlying in underlyings:
        logger.info("Fetching Greeks for %s ATM±%d", underlying, _STRIKES_PER_SIDE)

        # Get ATM chain to find which symbols to request
        try:
            full_chain = chain_client.get_option_chain(underlying)
            if not full_chain:
                errors.append(f"{underlying}: no option chain available")
                summary[underlying] = {"fetched": 0, "error": "no chain"}
                continue

            from provider.truedata.option_chain import _estimate_spot_from_chain
            spot = _estimate_spot_from_chain(full_chain)

            atm_chain = chain_client.get_atm_chain(
                underlying, spot, n_strikes=_STRIKES_PER_SIDE
            )

            symbols = [r.symbol for r in atm_chain]
            logger.info(
                "Requesting Greeks for %d symbols on %s", len(symbols), underlying
            )

        except Exception as exc:
            err_msg = f"{underlying} chain fetch: {exc}"
            errors.append(err_msg)
            logger.error(err_msg)
            summary[underlying] = {"fetched": 0, "error": str(exc)}
            continue

        # Fetch Greeks
        snapshots = greeks_client.get_chain_greeks(underlying, symbols)
        total_fetched += len(snapshots)
        all_snapshots.extend(snapshots)

        # Validate each snapshot
        validation_results: list[dict] = []
        valid_count = 0

        for snap in snapshots:
            result = greeks_client.validate_greeks(snap)
            validation_results.append({
                "symbol": snap.symbol,
                "strike": snap.strike,
                "option_type": snap.option_type,
                "valid": result["valid"],
                "errors": result["errors"],
                "warnings": result["warnings"],
                "null_fields": result["null_fields"],
            })
            if result["valid"]:
                valid_count += 1

        total_valid += valid_count

        # Null field analysis
        all_null_fields: dict[str, int] = {}
        for vr in validation_results:
            for field in vr["null_fields"]:
                all_null_fields[field] = all_null_fields.get(field, 0) + 1

        summary[underlying] = {
            "requested": len(symbols),
            "fetched": len(snapshots),
            "valid": valid_count,
            "valid_pct": round(valid_count / max(len(snapshots), 1) * 100, 1),
            "null_fields": all_null_fields,
            "validation_results": validation_results[:10],  # first 10 for report
        }

    details.update({
        "underlyings": underlyings,
        "strikes_per_side": _STRIKES_PER_SIDE,
        "total_fetched": total_fetched,
        "total_valid": total_valid,
        "valid_pct": round(total_valid / max(total_fetched, 1) * 100, 1),
        "output_dir": str(output_dir),
        "summary": summary,
    })

    # Save to CSV
    if all_snapshots:
        _save_greeks_csv(all_snapshots, output_dir)

    # Scoring: Greeks availability 10 pts
    score = 0
    if total_fetched > 0:
        valid_ratio = total_valid / max(total_fetched, 1)
        if valid_ratio >= 0.8:
            score = 10
        elif valid_ratio >= 0.5:
            score = 7
        elif valid_ratio >= 0.2:
            score = 4
        else:
            score = 2  # At least some data came back

    passed = total_fetched > 0 and total_valid > 0 and not errors

    return _build_result(started_at, passed, score, details, errors)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _save_greeks_csv(snapshots: list[GreeksSnapshot], output_dir: Path) -> None:
    """Save all Greeks snapshots to a CSV file."""
    path = output_dir / "greeks_trial.csv"
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "symbol", "underlying", "strike", "option_type", "expiry",
                "iv", "delta", "gamma", "theta", "vega", "rho", "timestamp",
            ])
            for snap in snapshots:
                writer.writerow([
                    snap.symbol,
                    snap.underlying,
                    snap.strike,
                    snap.option_type,
                    snap.expiry,
                    snap.iv,
                    snap.delta,
                    snap.gamma,
                    snap.theta,
                    snap.vega,
                    snap.rho,
                    snap.timestamp.isoformat(),
                ])
        logger.info("Saved %d Greeks snapshots → %s", len(snapshots), path)
    except OSError as exc:
        logger.error("Failed to save Greeks CSV: %s", exc)


def _build_result(
    started_at: datetime,
    passed: bool,
    score: int,
    details: dict,
    errors: list[str],
) -> TrialResult:
    ended_at = datetime.now(tz=_IST)
    return TrialResult(
        test_name="Option Greeks",
        passed=passed,
        score=score,
        details=details,
        errors=errors,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=(ended_at - started_at).total_seconds(),
    )
