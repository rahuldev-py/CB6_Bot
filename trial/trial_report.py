"""
Trial report generator.

Aggregates :class:`TrialResult` objects from all trial tests, computes
the CB6 fit score (0–100), and produces:
- Summary JSON
- Markdown report
- Latency CSV
- Missing data CSV
- Fit score markdown breakdown
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from provider.truedata import TrueDataConfig
from provider.truedata.models import TrialResult

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Score weights — must sum to 100
# ---------------------------------------------------------------------------
_WEIGHTS: dict[str, int] = {
    "auth_stability": 10,
    "ws_stability": 20,
    "tick_quality": 15,
    "candle_quality": 15,
    "historical_availability": 10,
    "option_chain_quality": 10,
    "greeks_availability": 10,
    "latency_p95": 5,
    "error_handling": 3,
    "integration_complexity": 2,
}
assert sum(_WEIGHTS.values()) == 100, "Score weights must sum to 100"


class TrialReportGenerator:
    """
    Generates a comprehensive CB6 Quantum fit-score report from trial results.

    Parameters
    ----------
    results:
        List of :class:`TrialResult` objects from each test module.
    config:
        TrueData configuration (used for metadata in reports).
    """

    def __init__(
        self,
        results: list[TrialResult],
        config: TrueDataConfig,
    ) -> None:
        self._results = results
        self._config = config
        self._by_name: dict[str, TrialResult] = {r.test_name: r for r in results}
        self._generated_at = datetime.now(tz=_IST)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_fit_score(self) -> int:
        """
        Compute the overall CB6 Quantum fit score (0–100).

        The score is assembled from weighted sub-scores for each dimension.
        Each dimension extracts its evidence from the relevant TrialResult(s).

        Returns
        -------
        int
            Fit score between 0 and 100.
        """
        breakdown = self._compute_breakdown()
        return min(100, max(0, sum(breakdown.values())))

    def compute_score_breakdown(self) -> dict[str, int]:
        """
        Return the per-dimension score breakdown.

        Returns
        -------
        dict[str, int]
            Dimension name → points earned.
        """
        return self._compute_breakdown()

    def generate_summary_json(self) -> dict:
        """
        Build a machine-readable summary dictionary.

        Returns
        -------
        dict
            Full summary with all test results, scores, and metadata.
        """
        breakdown = self._compute_breakdown()
        total = sum(breakdown.values())

        return {
            "generated_at": self._generated_at.isoformat(),
            "provider": "TrueData",
            "env": self._config.env,
            "fit_score": total,
            "fit_score_label": _score_label(total),
            "score_breakdown": breakdown,
            "score_weights": _WEIGHTS,
            "tests": [
                {
                    "name": r.test_name,
                    "passed": r.passed,
                    "score": r.score,
                    "duration_s": round(r.duration_s, 2),
                    "errors": r.errors,
                    "details": r.details,
                }
                for r in self._results
            ],
            "total_errors": sum(len(r.errors) for r in self._results),
            "tests_passed": sum(1 for r in self._results if r.passed),
            "tests_total": len(self._results),
        }

    def generate_summary_md(self) -> str:
        """
        Build a human-readable Markdown report.

        Returns
        -------
        str
            Markdown-formatted report string.
        """
        score = self.compute_fit_score()
        breakdown = self._compute_breakdown()
        summary = self.generate_summary_json()

        lines: list[str] = [
            "# TrueData Integration Trial Report",
            "",
            f"**Generated:** {self._generated_at.strftime('%Y-%m-%d %H:%M:%S IST')}  ",
            f"**Provider:** TrueData ({self._config.env.upper()})  ",
            f"**CB6 Fit Score:** {score}/100 — *{_score_label(score)}*  ",
            "",
            "---",
            "",
            "## Score Breakdown",
            "",
            "| Dimension | Weight | Earned |",
            "|-----------|--------|--------|",
        ]

        for dim, weight in _WEIGHTS.items():
            earned = breakdown.get(dim, 0)
            bar = "█" * earned + "░" * (weight - earned)
            lines.append(f"| {dim.replace('_', ' ').title()} | {weight} | {earned} {bar} |")

        lines += [
            "",
            f"**Total: {score}/100**",
            "",
            "---",
            "",
            "## Test Results",
            "",
        ]

        for r in self._results:
            status = "PASS" if r.passed else "FAIL"
            lines += [
                f"### {r.test_name} — {status}",
                f"- **Score:** {r.score}",
                f"- **Duration:** {r.duration_s:.1f}s",
            ]
            if r.errors:
                lines += ["- **Errors:**"]
                for e in r.errors[:5]:
                    lines.append(f"  - {e}")
            # Key metrics from details
            for key in ("total_ticks", "total_candles", "total_rows",
                        "latency_p95_ms", "total_gaps", "total_duplicates",
                        "total_fetched", "valid_pct"):
                if key in r.details:
                    label = key.replace("_", " ").title()
                    lines.append(f"- **{label}:** {r.details[key]}")
            lines.append("")

        lines += [
            "---",
            "",
            "## Recommendation",
            "",
            _recommendation(score),
            "",
        ]

        return "\n".join(lines)

    def generate_fit_score_md(self) -> str:
        """
        Generate a compact fit score card in Markdown.

        Returns
        -------
        str
        """
        score = self.compute_fit_score()
        label = _score_label(score)
        breakdown = self._compute_breakdown()

        lines: list[str] = [
            "# CB6 Quantum × TrueData — Fit Score Card",
            "",
            f"## Overall Score: {score}/100 — {label}",
            "",
            "| Category | Max | Score |",
            "|----------|-----|-------|",
        ]
        for dim, weight in _WEIGHTS.items():
            earned = breakdown.get(dim, 0)
            lines.append(
                f"| {dim.replace('_', ' ').title()} | {weight} | {earned} |"
            )
        lines += [
            f"| **TOTAL** | **100** | **{score}** |",
            "",
            _recommendation(score),
        ]
        return "\n".join(lines)

    def generate_latency_csv(self, output_dir: Path) -> None:
        """
        Write per-symbol latency statistics to a CSV file.

        Parameters
        ----------
        output_dir:
            Directory to write ``latency_stats.csv`` into.
        """
        path = output_dir / "latency_stats.csv"
        live_result = self._by_name.get("Live WebSocket Feed")
        if not live_result:
            logger.info("No live feed result — skipping latency CSV")
            return

        per_sym = live_result.details.get("per_symbol_latency", {})
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["symbol", "count", "mean_ms", "p95_ms"])
                for symbol, stats in per_sym.items():
                    writer.writerow([
                        symbol,
                        stats.get("count", 0),
                        stats.get("mean_ms", 0.0),
                        stats.get("p95_ms", 0.0),
                    ])
            logger.info("Latency CSV saved → %s", path)
        except OSError as exc:
            logger.error("Failed to write latency CSV: %s", exc)

    def generate_missing_data_csv(self, output_dir: Path) -> None:
        """
        Write missing/gap information to a CSV file.

        Parameters
        ----------
        output_dir:
            Directory to write ``missing_data.csv`` into.
        """
        path = output_dir / "missing_data.csv"
        hist_result = self._by_name.get("Historical Candle Data")
        if not hist_result:
            logger.info("No historical result — skipping missing data CSV")
            return

        symbol_summary = hist_result.details.get("summary", {})
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "symbol", "interval", "candle_count",
                    "gaps", "duplicates", "strictly_increasing",
                ])
                for symbol, intervals in symbol_summary.items():
                    if not isinstance(intervals, dict):
                        continue
                    for interval, metrics in intervals.items():
                        if not isinstance(metrics, dict):
                            continue
                        writer.writerow([
                            symbol,
                            interval,
                            metrics.get("count", 0),
                            metrics.get("gaps", 0),
                            metrics.get("duplicates", 0),
                            metrics.get("strictly_increasing", False),
                        ])
            logger.info("Missing data CSV saved → %s", path)
        except OSError as exc:
            logger.error("Failed to write missing data CSV: %s", exc)

    def save_all(self, output_dir: Path) -> None:
        """
        Save all report artifacts to ``output_dir``.

        Creates:
        - ``trial_summary.json``
        - ``trial_report.md``
        - ``fit_score.md``
        - ``latency_stats.csv``
        - ``missing_data.csv``

        Parameters
        ----------
        output_dir:
            Output directory (created if it does not exist).
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # JSON summary
        json_path = output_dir / "trial_summary.json"
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.generate_summary_json(), f, indent=2, default=str)
            logger.info("Summary JSON saved → %s", json_path)
        except OSError as exc:
            logger.error("Failed to write summary JSON: %s", exc)

        # Markdown report
        md_path = output_dir / "trial_report.md"
        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(self.generate_summary_md())
            logger.info("Markdown report saved → %s", md_path)
        except OSError as exc:
            logger.error("Failed to write markdown report: %s", exc)

        # Fit score card
        fit_path = output_dir / "fit_score.md"
        try:
            with open(fit_path, "w", encoding="utf-8") as f:
                f.write(self.generate_fit_score_md())
            logger.info("Fit score card saved → %s", fit_path)
        except OSError as exc:
            logger.error("Failed to write fit score card: %s", exc)

        # CSVs
        self.generate_latency_csv(output_dir)
        self.generate_missing_data_csv(output_dir)

        score = self.compute_fit_score()
        logger.info(
            "All trial reports saved to %s | CB6 Fit Score: %d/100 (%s)",
            output_dir, score, _score_label(score),
        )

    # ------------------------------------------------------------------
    # Internal scoring logic
    # ------------------------------------------------------------------

    def _compute_breakdown(self) -> dict[str, int]:
        """
        Compute per-dimension scores and return as a dict.

        Each dimension pulls evidence from one or more TrialResult objects.
        """
        live = self._by_name.get("Live WebSocket Feed")
        hist = self._by_name.get("Historical Candle Data")
        chain = self._by_name.get("Option Chain")
        greeks = self._by_name.get("Option Greeks")

        bd: dict[str, int] = {}

        # Auth stability (10 pts)
        auth_ok = any(
            r.details.get("auth_ok", False) for r in self._results
        )
        bd["auth_stability"] = 10 if auth_ok else 0

        # WebSocket stability (20 pts)
        ws_score = 0
        if live:
            syms_with_ticks = live.details.get("symbols_with_ticks", 0)
            total_syms = len(live.details.get("symbols", [1]))
            disconnects = live.details.get("disconnect_events", 99)
            reconnects = live.details.get("reconnect_events", 99)

            if syms_with_ticks == total_syms and disconnects == 0:
                ws_score = 20
            elif syms_with_ticks == total_syms:
                ws_score = 15
            elif syms_with_ticks >= total_syms // 2:
                ws_score = 10
            elif syms_with_ticks >= 1:
                ws_score = 5

            # Penalty for many reconnects
            if reconnects > 5:
                ws_score = max(ws_score - 5, 0)
        bd["ws_stability"] = ws_score

        # Tick quality (15 pts)
        tick_score = 0
        if live and live.passed:
            total_ticks = live.details.get("total_ticks", 0)
            gaps = live.details.get("total_seq_gaps", 0)
            if total_ticks > 0:
                gap_ratio = gaps / max(total_ticks, 1)
                if gap_ratio < 0.001:
                    tick_score = 15
                elif gap_ratio < 0.01:
                    tick_score = 10
                else:
                    tick_score = 5
        bd["tick_quality"] = tick_score

        # Candle quality (15 pts)
        candle_score = 0
        if hist:
            gaps_c = hist.details.get("total_gaps", 999)
            dupes = hist.details.get("total_duplicates", 999)
            candles = hist.details.get("total_candles", 0)
            if candles > 0:
                if gaps_c == 0 and dupes == 0:
                    candle_score = 15
                elif gaps_c <= 5 and dupes == 0:
                    candle_score = 12
                elif gaps_c <= 20:
                    candle_score = 7
                else:
                    candle_score = 3
        bd["candle_quality"] = candle_score

        # Historical availability (10 pts)
        hist_score = 0
        if hist:
            ok = hist.details.get("intervals_ok", 0)
            total = hist.details.get("intervals_tested", 1)
            hist_score = round(ok / max(total, 1) * 10)
        bd["historical_availability"] = hist_score

        # Option chain quality (10 pts)
        bd["option_chain_quality"] = min(chain.score if chain else 0, 10)

        # Greeks availability (10 pts)
        bd["greeks_availability"] = min(greeks.score if greeks else 0, 10)

        # Latency p95 (5 pts)
        latency_score = 0
        if live:
            p95 = live.details.get("latency_p95_ms", 9999)
            if p95 < 200:
                latency_score = 5
            elif p95 < 500:
                latency_score = 4
            elif p95 < 1000:
                latency_score = 2
        bd["latency_p95"] = latency_score

        # Error handling (3 pts)
        # Award if errors were caught gracefully (no crashes, TrialResults created)
        all_ran = len(self._results) >= 3
        bd["error_handling"] = 3 if all_ran else (1 if self._results else 0)

        # Integration complexity (2 pts — inverse: simpler = more points)
        # TrueData has clean REST + WS → 2 pts
        bd["integration_complexity"] = 2

        return bd


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _score_label(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Acceptable"
    if score >= 40:
        return "Marginal"
    return "Poor"


def _recommendation(score: int) -> str:
    if score >= 85:
        return (
            "**Recommendation: Proceed with TrueData integration.**  \n"
            "All critical data feeds are high quality.  "
            "Wire TrueData into the live NSE engine."
        )
    if score >= 70:
        return (
            "**Recommendation: Integrate with monitoring.**  \n"
            "Data quality is good.  Monitor latency and gap rates for the first week.  "
            "Consider a fallback to Yahoo Feed for historical if gaps persist."
        )
    if score >= 55:
        return (
            "**Recommendation: Integrate with caution.**  \n"
            "Some data quality issues detected.  "
            "Run a longer live trial (48h) before going live.  "
            "Implement gap-fill fallback logic."
        )
    return (
        "**Recommendation: Do not integrate yet.**  \n"
        "Significant data quality or connectivity issues.  "
        "Contact TrueData support and re-run the trial after resolution."
    )
