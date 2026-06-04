"""
TrueData trial orchestrator.

Runs all five trial tests in sequence, generates the full CB6 fit score
report, and saves all artifacts to the configured output directory.

Usage::

    python trial/run_truedata_trial.py
    python trial/run_truedata_trial.py --env live --duration 30
    python trial/run_truedata_trial.py --skip-live   # skip WebSocket test
    python trial/run_truedata_trial.py --only-historical
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Ensure c:\cb6_bot is on the path when running as a script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from provider.truedata import TrueDataAuth, TrueDataConfig, load_config
from provider.truedata.models import TrialResult
from trial.test_greeks import run_greeks_test
from trial.test_historical import run_historical_test
from trial.test_live_feed import run_live_feed_test
from trial.test_option_chain import run_option_chain_test
from trial.trial_report import TrialReportGenerator

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Auth test (inline — no separate module needed)
# ---------------------------------------------------------------------------


def _run_auth_test(config: TrueDataConfig) -> TrialResult:
    """Test auth: login, get_token, and is_authenticated."""
    from datetime import datetime as dt
    started_at = dt.now(tz=_IST)
    errors: list[str] = []
    details: dict = {}

    auth = TrueDataAuth(config)
    try:
        token = auth.login()
        details["token_prefix"] = token[:4] + "****"
        details["is_authenticated"] = auth.is_authenticated
        details["auth_ok"] = True

        # Second call should use cache
        token2 = auth.get_token()
        details["cache_works"] = token == token2

        auth.logout()
        details["logout_ok"] = not auth.is_authenticated

    except Exception as exc:
        errors.append(f"Auth error: {exc}")
        details["auth_ok"] = False

    passed = details.get("auth_ok", False) and not errors
    score = 0  # Auth score is embedded in live feed result
    ended_at = dt.now(tz=_IST)

    return TrialResult(
        test_name="Auth",
        passed=passed,
        score=score,
        details=details,
        errors=errors,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=(ended_at - started_at).total_seconds(),
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> int:
    """
    Run all trial tests and generate the fit score report.

    Returns
    -------
    int
        Exit code: 0 = success, 1 = critical failure.
    """
    _setup_logging()

    logger.info("=" * 60)
    logger.info("CB6 Quantum × TrueData Integration Trial")
    logger.info("Started at %s", datetime.now(tz=_IST).strftime("%Y-%m-%d %H:%M:%S IST"))
    logger.info("=" * 60)

    # Load config
    try:
        config = load_config()
    except ValueError as exc:
        logger.error("Config error: %s", exc)
        logger.error(
            "Set TRUEDATA_USER and TRUEDATA_PASSWORD in .env or environment."
        )
        return 1

    # Apply CLI overrides
    if args.env:
        from dataclasses import replace as dc_replace
        config = dc_replace(config, env=args.env)
        port = 8082 if args.env == "sandbox" else 8086
        config = dc_replace(config, ws_port=port)

    if args.duration:
        from dataclasses import replace as dc_replace
        config = dc_replace(config, trial_duration_minutes=args.duration)

    results: list[TrialResult] = []
    trial_start = time.monotonic()

    # ---- Test 1: Auth ----
    logger.info("\n[1/5] Running auth test...")
    auth_result = _run_auth_test(config)
    results.append(auth_result)
    _log_result(auth_result)

    if not auth_result.passed and not args.force:
        logger.error("Auth test failed — aborting trial (use --force to continue)")
        return 1

    # ---- Test 2: Live WebSocket feed ----
    if not args.skip_live:
        logger.info("\n[2/5] Running live feed test (%dm)...", config.trial_duration_minutes)
        live_result = await run_live_feed_test(config)
        results.append(live_result)
        _log_result(live_result)
    else:
        logger.info("[2/5] Skipping live feed test (--skip-live)")

    # ---- Test 3: Historical ----
    if not args.skip_historical:
        logger.info("\n[3/5] Running historical candle test...")
        loop = asyncio.get_event_loop()
        hist_result = await loop.run_in_executor(None, run_historical_test, config)
        results.append(hist_result)
        _log_result(hist_result)
    else:
        logger.info("[3/5] Skipping historical test")

    # ---- Test 4: Option chain ----
    if not args.skip_options:
        logger.info("\n[4/5] Running option chain test...")
        loop = asyncio.get_event_loop()
        chain_result = await loop.run_in_executor(None, run_option_chain_test, config)
        results.append(chain_result)
        _log_result(chain_result)
    else:
        logger.info("[4/5] Skipping option chain test")

    # ---- Test 5: Greeks ----
    if not args.skip_greeks:
        logger.info("\n[5/5] Running Greeks test...")
        loop = asyncio.get_event_loop()
        greeks_result = await loop.run_in_executor(None, run_greeks_test, config)
        results.append(greeks_result)
        _log_result(greeks_result)
    else:
        logger.info("[5/5] Skipping Greeks test")

    # ---- Generate report ----
    logger.info("\nGenerating trial report...")
    report = TrialReportGenerator(results, config)
    output_dir = config.data_dir / "reports"
    report.save_all(output_dir)

    total_elapsed = time.monotonic() - trial_start
    fit_score = report.compute_fit_score()

    logger.info("\n" + "=" * 60)
    logger.info("TRIAL COMPLETE")
    logger.info("CB6 Fit Score: %d/100", fit_score)
    logger.info("Tests passed: %d/%d", sum(1 for r in results if r.passed), len(results))
    logger.info("Total elapsed: %.1fs", total_elapsed)
    logger.info("Reports saved to: %s", output_dir)
    logger.info("=" * 60)

    print(f"\nCB6 Quantum × TrueData Fit Score: {fit_score}/100")
    print(f"Reports: {output_dir}")

    return 0


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    """Configure logging for the trial runner."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Reduce websockets noise
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _log_result(result: TrialResult) -> None:
    """Log a summary of a TrialResult."""
    status = "PASS" if result.passed else "FAIL"
    logger.info(
        "  %-30s %s  score=%d  duration=%.1fs  errors=%d",
        result.test_name,
        status,
        result.score,
        result.duration_s,
        len(result.errors),
    )
    for e in result.errors[:3]:
        logger.warning("    ERROR: %s", e)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CB6 Quantum × TrueData Integration Trial Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--env",
        choices=["sandbox", "live"],
        default=None,
        help="Override TRUEDATA_ENV from config",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Live feed test duration in minutes (overrides config)",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip the live WebSocket feed test",
    )
    parser.add_argument(
        "--skip-historical",
        action="store_true",
        help="Skip the historical candle test",
    )
    parser.add_argument(
        "--skip-options",
        action="store_true",
        help="Skip the option chain test",
    )
    parser.add_argument(
        "--skip-greeks",
        action="store_true",
        help="Skip the Greeks test",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Continue even if auth fails",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    _args = _parse_args()
    exit_code = asyncio.run(main(_args))
    sys.exit(exit_code)
