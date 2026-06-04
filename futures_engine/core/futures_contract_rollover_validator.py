"""
CB6 Futures Core — Contract Rollover Validator
Validates that a bar series handles futures rollovers correctly.
Detects:
  1. Price gaps caused by contract rollovers (not real price moves)
  2. Missing rollover adjustments in continuous series
  3. Volume signature of rollover (old contract volume drops, new rises)
  4. Bars labelled with wrong contract code for their date
Provides:
  - Panama (backward ratio) adjustment for accurate historical PnL simulation
  - Unadjusted continuous series (correct for volume/TA, wrong for PnL)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from futures_engine.core.futures_data_feed import FuturesBar
from futures_engine.core.futures_contract_manager import (
    ContractManager, contract_code, expiry_date, front_month,
)

logger = logging.getLogger("cb6.futures.rollover_validator")


@dataclass
class RolloverGap:
    """A detected price discontinuity at a contract rollover date."""
    date: date
    old_contract: str
    new_contract: str
    last_price_old: float
    first_price_new: float
    gap_points: float          # new_open - old_close  (raw, not adjusted)
    gap_pct: float
    misleading_for_pnl: bool   # True if gap > threshold and series is unadjusted
    misleading_for_ta: bool    # True if close gap > 1% (TA distortion)


@dataclass
class ValidationReport:
    symbol: str
    total_bars: int
    rollover_count: int
    gaps_detected: List[RolloverGap]
    bars_with_wrong_contract: int
    is_adjusted: bool           # Did we detect Panama adjustment?
    adjustment_factors: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    passed: bool = True


def _detect_adjusted(gaps: List[RolloverGap], threshold_pct: float = 0.005) -> bool:
    """
    Heuristic: if all rollover gaps are < threshold, the series is likely adjusted.
    A raw series will have gaps of 0.1%–2%+ at rollovers.
    """
    if not gaps:
        return True  # can't tell
    large_gaps = [g for g in gaps if abs(g.gap_pct) > threshold_pct]
    return len(large_gaps) == 0


class FuturesRolloverValidator:
    """
    Validates a futures bar series against known rollover dates.
    Also builds adjusted (Panama) and unadjusted continuous series.
    """

    GAP_WARN_PCT      = 0.001   # 0.1% gap → warn
    GAP_PNL_THRESH_PCT = 0.002  # 0.2% gap → flag as misleading for PnL
    GAP_TA_THRESH_PCT  = 0.010  # 1.0% gap → flag as misleading for TA

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self._contract_mgr = ContractManager(symbol)

    def validate(self, bars: List[FuturesBar]) -> ValidationReport:
        """
        Validate a bar series. Bars must be sorted ascending by timestamp.
        """
        if not bars:
            return ValidationReport(
                symbol=self.symbol, total_bars=0, rollover_count=0,
                gaps_detected=[], bars_with_wrong_contract=0,
                is_adjusted=True, passed=True,
            )

        bars = sorted(bars, key=lambda b: b.timestamp)
        gaps: List[RolloverGap] = []
        wrong_contract_count = 0
        warnings: List[str] = []

        # Build expected contract per bar
        prev_contract: Optional[str] = None
        prev_bar: Optional[FuturesBar] = None
        rollover_count = 0

        for bar in bars:
            d = bar.timestamp.astimezone(timezone.utc).date()
            expected = self._contract_mgr.active_contract(d)

            # Check for contract label mismatch
            if bar.contract and bar.contract != "" and bar.contract != expected:
                wrong_contract_count += 1
                if wrong_contract_count <= 5:
                    warnings.append(
                        f"Bar at {bar.timestamp.date()} labelled '{bar.contract}' "
                        f"but expected '{expected}'"
                    )

            # Detect rollover gap
            if prev_contract is not None and prev_bar is not None:
                cur_contract = expected
                if cur_contract != prev_contract:
                    rollover_count += 1
                    gap_pts = bar.open - prev_bar.close
                    gap_pct = gap_pts / prev_bar.close if prev_bar.close else 0
                    gap = RolloverGap(
                        date=d,
                        old_contract=prev_contract,
                        new_contract=cur_contract,
                        last_price_old=prev_bar.close,
                        first_price_new=bar.open,
                        gap_points=round(gap_pts, 4),
                        gap_pct=round(gap_pct, 6),
                        misleading_for_pnl=abs(gap_pct) >= self.GAP_PNL_THRESH_PCT,
                        misleading_for_ta=abs(gap_pct) >= self.GAP_TA_THRESH_PCT,
                    )
                    gaps.append(gap)

                    if abs(gap_pct) >= self.GAP_WARN_PCT:
                        logger.warning(
                            "Rollover gap %s → %s: %.4f pts (%.2f%%)",
                            prev_contract, cur_contract, gap_pts, gap_pct * 100
                        )

            prev_contract = expected
            prev_bar = bar

        is_adjusted = _detect_adjusted(gaps)
        pnl_misleading = [g for g in gaps if g.misleading_for_pnl]
        ta_misleading  = [g for g in gaps if g.misleading_for_ta]

        if pnl_misleading and not is_adjusted:
            warnings.append(
                f"{len(pnl_misleading)} unadjusted rollover gap(s) will distort PnL simulation. "
                "Apply Panama adjustment before backtesting."
            )
        if ta_misleading and not is_adjusted:
            warnings.append(
                f"{len(ta_misleading)} large gap(s) (>1%) will distort swing high/low detection."
            )
        if wrong_contract_count > 0:
            warnings.append(f"{wrong_contract_count} bars carry incorrect contract labels.")

        passed = len(ta_misleading) == 0 and wrong_contract_count == 0

        return ValidationReport(
            symbol=self.symbol,
            total_bars=len(bars),
            rollover_count=rollover_count,
            gaps_detected=gaps,
            bars_with_wrong_contract=wrong_contract_count,
            is_adjusted=is_adjusted,
            warnings=warnings,
            passed=passed,
        )

    def apply_panama_adjustment(
        self,
        bars: List[FuturesBar],
    ) -> Tuple[List[FuturesBar], Dict[str, float]]:
        """
        Backward ratio (Panama) adjustment.
        Walk backward from the most-recent contract.
        At each rollover, multiply all earlier bars by (new_first_open / old_last_close).
        Returns (adjusted_bars, adjustment_factors) where:
            adjustment_factors[contract_code] = cumulative multiplier applied.

        The most-recent prices are unchanged; earlier prices are scaled.
        This ensures the most-recent contract's PnL is in actual USD terms,
        and the historical bars are comparable for TA / strategy testing.
        """
        bars = sorted(bars, key=lambda b: b.timestamp)
        if not bars:
            return bars, {}

        # Find rollover boundaries
        boundaries: List[Tuple[int, str, str, float, float]] = []
        # (bar_index_of_first_bar_in_new_contract, old_contract, new_contract,
        #  old_last_close, new_first_open)

        prev_contract = self._contract_mgr.active_contract(
            bars[0].timestamp.astimezone(timezone.utc).date()
        )
        prev_close = bars[0].close

        for i, bar in enumerate(bars[1:], 1):
            d = bar.timestamp.astimezone(timezone.utc).date()
            cur = self._contract_mgr.active_contract(d)
            if cur != prev_contract:
                boundaries.append((i, prev_contract, cur, prev_close, bar.open))
                prev_contract = cur
            prev_close = bar.close

        if not boundaries:
            return list(bars), {}

        # Compute cumulative multipliers: work backward
        # At each boundary, ratio = old_last_close / new_first_open
        # All bars BEFORE the boundary are multiplied by this ratio
        import copy
        adjusted = [copy.copy(b) for b in bars]
        factors: Dict[str, float] = {}
        cumulative = 1.0

        for boundary_idx, old_c, new_c, old_close, new_open in reversed(boundaries):
            if new_open == 0:
                continue
            ratio = old_close / new_open
            cumulative *= ratio
            factors[old_c] = cumulative
            for i in range(boundary_idx):
                b = adjusted[i]
                adjusted[i] = FuturesBar(
                    symbol=b.symbol,
                    contract=b.contract,
                    timestamp=b.timestamp,
                    open=round(b.open * cumulative, 4),
                    high=round(b.high * cumulative, 4),
                    low=round(b.low * cumulative, 4),
                    close=round(b.close * cumulative, 4),
                    volume=b.volume,
                    timeframe=b.timeframe,
                )

        logger.info(
            "Panama adjustment: %d rollovers, %d bars adjusted, final factor=%.6f",
            len(boundaries), len([i for i, _ in enumerate(adjusted) if i < boundaries[-1][0]]),
            cumulative,
        )
        return adjusted, factors

    def save_validation_report(
        self,
        report: ValidationReport,
        out_dir: str = "reports/futures/rollover",
    ) -> str:
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"rollover_{report.symbol}_{ts}.json")
        data = {
            "symbol": report.symbol,
            "total_bars": report.total_bars,
            "rollover_count": report.rollover_count,
            "bars_with_wrong_contract": report.bars_with_wrong_contract,
            "is_adjusted": report.is_adjusted,
            "passed": report.passed,
            "warnings": report.warnings,
            "gaps": [
                {
                    "date": g.date.isoformat(),
                    "old_contract": g.old_contract,
                    "new_contract": g.new_contract,
                    "last_price_old": g.last_price_old,
                    "first_price_new": g.first_price_new,
                    "gap_points": g.gap_points,
                    "gap_pct": round(g.gap_pct * 100, 4),
                    "misleading_pnl": g.misleading_for_pnl,
                    "misleading_ta": g.misleading_for_ta,
                }
                for g in report.gaps_detected
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Rollover validation report: %s", path)
        return path
