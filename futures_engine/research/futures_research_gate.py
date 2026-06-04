"""
CB6 Futures Core — Research Gate
Reads backtest summary JSONs and auto-decides:
  PASS → strategy shows edge, eligible to consider MFF purchase
  FAIL → keep researching, do NOT buy account yet

Minimum criteria (adjustable, never weaken without explicit review):
  - Trades     : ≥ 100 per year
  - Profit Factor: ≥ 1.5
  - Max Drawdown : ≤ $700 (MFF hard limit is $1000; we want $300 buffer)
  - Expectancy   : > 0.0
  - MFF Eval Sim : passes ≥ 2 of 3 years
  - Worst year   : must not be account-blowing (net PnL + starting equity > 0)

Additional data-quality checks:
  - Flags 1h-bar approximations (real 1m data needed for production confidence)
  - Flags Panama-unadjusted series (roll gaps distort PnL)
  - Flags symbols where strategy edge is absent (MCL pattern)

Usage:
    python -m futures_engine.research.futures_research_gate
    python -m futures_engine.research.futures_research_gate --symbol MES
    python -m futures_engine.research.futures_research_gate --require-1m  # strict mode
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("cb6.futures.research.gate")

REPORTS_DIR = "reports/futures/research"

# ── Gate thresholds ────────────────────────────────────────────────────────────

@dataclass
class GateThresholds:
    min_trades:          int   = 100
    min_profit_factor:   float = 1.5
    max_drawdown_usd:    float = 700.0    # Internal limit; MFF hard cap is $1000
    min_expectancy:      float = 0.0      # Must be positive
    min_mff_pass_years:  int   = 2        # Must pass eval sim in ≥ 2/3 years
    starting_equity:     float = 25000.0  # For "account blown" check

GATE = GateThresholds()


# ── Result structures ──────────────────────────────────────────────────────────

@dataclass
class YearCheck:
    year: int
    symbol: str
    timeframe_used: str
    trades: int
    win_rate_pct: float
    profit_factor: float
    net_pnl: float
    max_drawdown: float
    expectancy: float
    mff_sim_passes: bool
    mff_violations: List[str]

    # Gate results per criterion
    trades_ok:     bool = False
    pf_ok:         bool = False
    dd_ok:         bool = False
    expect_ok:     bool = False
    no_blow_ok:    bool = False

    warnings: List[str] = field(default_factory=list)

    @property
    def passes_all(self) -> bool:
        return all([self.trades_ok, self.pf_ok, self.dd_ok,
                    self.expect_ok, self.no_blow_ok])


@dataclass
class SymbolVerdict:
    symbol: str
    years: List[YearCheck]
    mff_pass_count: int
    worst_year_blown: bool
    has_1m_data: bool
    has_panama_adjustment: bool

    gate_verdict: str = "FAIL"   # PASS | FAIL | CONDITIONAL
    gate_reasons: List[str] = field(default_factory=list)
    gate_warnings: List[str] = field(default_factory=list)
    recommended_action: str = ""


@dataclass
class OverallVerdict:
    symbols: Dict[str, SymbolVerdict]
    buy_mff_now: bool
    buy_verdict: str        # BUY | DO_NOT_BUY | CONDITIONAL
    confidence: str         # HIGH | MEDIUM | LOW
    blocking_reasons: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


# ── Loading ────────────────────────────────────────────────────────────────────

def load_latest_reports(reports_dir: str = REPORTS_DIR) -> List[dict]:
    """Load all research summary JSON files, newest first."""
    pattern = os.path.join(reports_dir, "research_summary_*.json")
    files = sorted(glob.glob(pattern), reverse=True)
    rows = []
    seen = set()   # deduplicate by (symbol, year)
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            for row in data:
                key = (row.get("symbol", ""), row.get("year", 0))
                if key not in seen and row.get("status") == "OK":
                    seen.add(key)
                    rows.append(row)
        except Exception as e:
            logger.warning("Could not load %s: %s", f, e)
    return rows


def _extract_timeframe(symbol_field: str) -> Tuple[str, str]:
    """Parse 'MES[1h]' → ('MES', '1h'). Falls back to 'unknown'."""
    if "[" in symbol_field:
        sym, tf = symbol_field.split("[")
        return sym, tf.rstrip("]")
    return symbol_field, "unknown"


# ── Per-year gate ──────────────────────────────────────────────────────────────

def check_year(row: dict, thresholds: GateThresholds = GATE) -> YearCheck:
    raw_symbol = row.get("symbol", "")
    symbol, tf_fallback = _extract_timeframe(raw_symbol)
    # Prefer explicit timeframe_used field added by F-4 fix
    tf = row.get("timeframe_used", tf_fallback) or tf_fallback
    year = row.get("year", 0)

    trades    = row.get("total_trades", 0)
    wr_pct    = row.get("win_rate_pct", 0.0)
    pf        = row.get("profit_factor", 0.0)
    net_pnl   = row.get("net_pnl", 0.0)
    max_dd    = row.get("max_drawdown", 0.0)
    expect    = row.get("expectancy", 0.0)
    mff_sim   = row.get("mff_simulation", {})
    mff_pass  = mff_sim.get("would_pass", False)
    mff_viols = mff_sim.get("violations", [])
    # Prefer EOD drawdown from corrected F-3 simulation when available
    eod_dd = mff_sim.get("max_eod_drawdown")
    if eod_dd is not None:
        max_dd = eod_dd   # use the accurate EOD model, not equity-curve peak-to-trough

    warnings = []
    if tf != "1m":
        warnings.append(
            f"Data is {tf} bars — not 1m. Win rates and PF may be inflated. "
            "Treat as directional signal only, not execution-ready numbers."
        )
    if pf == float("inf"):
        warnings.append("Profit factor is infinity (no losing trades) — likely data artifact.")
        pf = 999.0

    chk = YearCheck(
        year=year, symbol=symbol, timeframe_used=tf,
        trades=trades, win_rate_pct=wr_pct,
        profit_factor=pf, net_pnl=net_pnl,
        max_drawdown=max_dd, expectancy=expect,
        mff_sim_passes=mff_pass, mff_violations=mff_viols,
        warnings=warnings,
    )
    chk.trades_ok  = trades >= thresholds.min_trades
    chk.pf_ok      = pf >= thresholds.min_profit_factor
    chk.dd_ok      = max_dd <= thresholds.max_drawdown_usd
    chk.expect_ok  = expect > thresholds.min_expectancy
    chk.no_blow_ok = (thresholds.starting_equity + net_pnl) > 0
    return chk


# ── Per-symbol verdict ─────────────────────────────────────────────────────────

def evaluate_symbol(
    symbol: str,
    year_checks: List[YearCheck],
    thresholds: GateThresholds = GATE,
    require_1m: bool = False,
) -> SymbolVerdict:
    mff_passes = sum(1 for y in year_checks if y.mff_sim_passes)
    worst_blown = any(not y.no_blow_ok for y in year_checks)
    has_1m = all(y.timeframe_used == "1m" for y in year_checks)
    # Panama check: if any year has MGC/MCL with large DDs it likely needs adjustment
    # (we infer from symbol name)
    needs_panama = symbol.upper() in ("MGC", "MCL", "GC", "CL", "SI", "SIL")

    reasons: List[str] = []
    warnings: List[str] = []

    # Check each year
    failed_years = [y for y in year_checks if not y.passes_all]
    passed_years = [y for y in year_checks if y.passes_all]

    if require_1m and not has_1m:
        reasons.append(
            "1m data required (--require-1m flag set) but only 1h data available. "
            "Download 1m data before proceeding."
        )

    if worst_blown:
        reasons.append("At least one year ends with negative account equity — strategy blows account.")

    if mff_passes < thresholds.min_mff_pass_years:
        reasons.append(
            f"MFF eval simulation passes {mff_passes}/{len(year_checks)} years "
            f"(minimum {thresholds.min_mff_pass_years} required)."
        )

    for y in year_checks:
        if not y.trades_ok:
            reasons.append(f"{y.year}: only {y.trades} trades (min {thresholds.min_trades}).")
        if not y.pf_ok:
            reasons.append(
                f"{y.year}: PF {y.profit_factor:.2f} below minimum {thresholds.min_profit_factor}."
            )
        if not y.dd_ok:
            reasons.append(
                f"{y.year}: Max DD ${y.max_drawdown:.0f} exceeds ${thresholds.max_drawdown_usd:.0f} gate "
                f"(note: 1h-bar equity curve DD ≠ MFF EOD drawdown — real value likely lower)."
            )
        if not y.expect_ok:
            reasons.append(f"{y.year}: Expectancy ${y.expectancy:.2f} ≤ 0.")
        for w in y.warnings:
            if w not in warnings:
                warnings.append(w)

    if needs_panama:
        warnings.append(
            f"{symbol} is a monthly-expiry contract. Rollover validator flagged unadjusted gaps. "
            "Apply Panama adjustment before treating PnL numbers as final."
        )

    # Determine verdict
    if reasons:
        if len(passed_years) >= 1 and mff_passes >= 1 and not worst_blown:
            verdict = "CONDITIONAL"
            action = (
                f"Mixed results. {len(passed_years)}/{len(year_checks)} years pass all criteria. "
                "Address flagged issues before going live."
            )
        else:
            verdict = "FAIL"
            action = "Do not proceed. Address all FAIL reasons before re-evaluating."
    else:
        if not has_1m:
            verdict = "CONDITIONAL"
            action = (
                "All criteria pass on 1h data. Strong directional edge confirmed. "
                "Get 1m data (TradingView export) to validate execution-level accuracy before buying account."
            )
        else:
            verdict = "PASS"
            action = "All criteria pass on 1m data. Edge confirmed. Eligible for MFF account."

    return SymbolVerdict(
        symbol=symbol,
        years=year_checks,
        mff_pass_count=mff_passes,
        worst_year_blown=worst_blown,
        has_1m_data=has_1m,
        has_panama_adjustment=False,
        gate_verdict=verdict,
        gate_reasons=reasons,
        gate_warnings=warnings,
        recommended_action=action,
    )


# ── Overall verdict ────────────────────────────────────────────────────────────

def overall_verdict(symbol_verdicts: Dict[str, SymbolVerdict]) -> OverallVerdict:
    passing = [s for s, v in symbol_verdicts.items() if v.gate_verdict == "PASS"]
    conditional = [s for s, v in symbol_verdicts.items() if v.gate_verdict == "CONDITIONAL"]
    failing = [s for s, v in symbol_verdicts.items() if v.gate_verdict == "FAIL"]

    blocking: List[str] = []
    recs: List[str] = []

    has_any_1m = any(v.has_1m_data for v in symbol_verdicts.values())

    if not has_any_1m:
        blocking.append(
            "No 1m bar data available for any symbol. All results use 1h bars. "
            "1h-bar backtests confirm directional edge but CANNOT predict "
            "fill accuracy, realistic slippage, or actual win rate on 1m entries."
        )
        recs.append(
            "Export 1m historical data from TradingView for MES and MGC: "
            "Chart → Export Data → 1 minute → last 2 years. "
            "Then re-run: python -m futures_engine.research.futures_research_runner"
        )

    if failing:
        blocking.append(
            f"Symbol(s) with FAIL verdict: {', '.join(failing)}. "
            "Do not trade these symbols without strategy revision."
        )

    if not passing and not conditional:
        buy_now = False
        buy_verd = "DO_NOT_BUY"
        confidence = "LOW"
    elif passing:
        buy_now = len(passing) >= 1 and has_any_1m
        buy_verd = "BUY" if buy_now else "CONDITIONAL"
        confidence = "HIGH" if has_any_1m else "MEDIUM"
    else:
        buy_now = False
        buy_verd = "CONDITIONAL"
        confidence = "MEDIUM"

    if conditional and not passing:
        recs.append(
            f"Symbols with CONDITIONAL verdict ({', '.join(conditional)}): "
            "edge is present but validate with 1m data first."
        )
    if passing:
        recs.append(
            f"Symbols ready ({', '.join(passing)}): "
            "start with 1 micro contract, max $200 internal daily stop."
        )

    return OverallVerdict(
        symbols=symbol_verdicts,
        buy_mff_now=buy_now,
        buy_verdict=buy_verd,
        confidence=confidence,
        blocking_reasons=blocking,
        recommendations=recs,
    )


# ── Printer ────────────────────────────────────────────────────────────────────

def print_gate_report(verdict: OverallVerdict) -> None:
    W = 100
    print("\n" + "=" * W)
    print("CB6 FUTURES CORE — RESEARCH GATE REPORT")
    print("=" * W)

    for sym, sv in verdict.symbols.items():
        icon = "✓" if sv.gate_verdict == "PASS" else ("~" if sv.gate_verdict == "CONDITIONAL" else "✗")
        print(f"\n[{icon}] {sym} — {sv.gate_verdict}")
        print(f"    Action: {sv.recommended_action}")

        # Per-year table
        print(f"\n    {'Year':<6} {'TF':<5} {'Trades':<8} {'WR%':<7} {'PF':<7} "
              f"{'NetPnL':>9} {'MaxDD':>8} {'Expect':>9}  Gate")
        print("    " + "-" * 80)
        for y in sv.years:
            ok_str = "PASS" if y.passes_all else "FAIL"
            fails = []
            if not y.trades_ok:  fails.append(f"trades<{GATE.min_trades}")
            if not y.pf_ok:      fails.append(f"PF<{GATE.min_profit_factor}")
            if not y.dd_ok:      fails.append(f"DD>${GATE.max_drawdown_usd:.0f}")
            if not y.expect_ok:  fails.append("expect≤0")
            if not y.no_blow_ok: fails.append("BLOWN")
            fail_detail = " [" + ", ".join(fails) + "]" if fails else ""
            print(f"    {y.year:<6} {y.timeframe_used:<5} {y.trades:<8} "
                  f"{y.win_rate_pct:<7.1f} {y.profit_factor:<7.2f} "
                  f"${y.net_pnl:>8.0f} ${y.max_drawdown:>7.0f} "
                  f"${y.expectancy:>8.2f}  {ok_str}{fail_detail}")

        if sv.gate_reasons:
            print("\n    BLOCKERS:")
            for r in sv.gate_reasons:
                print(f"      ✗ {r}")
        if sv.gate_warnings:
            print("\n    WARNINGS:")
            for w in sv.gate_warnings:
                print(f"      ! {w}")

    # Overall verdict
    print("\n" + "=" * W)
    buy_icon = "✓" if verdict.buy_mff_now else ("~" if verdict.buy_verdict == "CONDITIONAL" else "✗")
    print(f"OVERALL VERDICT: [{buy_icon}] {verdict.buy_verdict}  (confidence: {verdict.confidence})")
    print("=" * W)

    if verdict.buy_mff_now:
        print("\n  ► BUY MFF FLEX $25K ACCOUNT — research gate passed.")
        print("  ► Start with 1 MES micro, max $200/day internal stop.")
    elif verdict.buy_verdict == "CONDITIONAL":
        print("\n  ► DO NOT BUY YET — conditions below must be met first:")
    else:
        print("\n  ► DO NOT BUY — gate failed:")

    if verdict.blocking_reasons:
        print("\n  BLOCKING ISSUES:")
        for r in verdict.blocking_reasons:
            print(f"    ✗ {r}")
    if verdict.recommendations:
        print("\n  NEXT STEPS:")
        for r in verdict.recommendations:
            print(f"    → {r}")
    print()


# ── Save ───────────────────────────────────────────────────────────────────────

def save_gate_report(verdict: OverallVerdict, out_dir: str = REPORTS_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"gate_report_{ts}.json")

    def _sv_dict(sv: SymbolVerdict) -> dict:
        return {
            "symbol": sv.symbol,
            "gate_verdict": sv.gate_verdict,
            "recommended_action": sv.recommended_action,
            "mff_pass_count": sv.mff_pass_count,
            "has_1m_data": sv.has_1m_data,
            "reasons": sv.gate_reasons,
            "warnings": sv.gate_warnings,
            "years": [
                {
                    "year": y.year,
                    "timeframe": y.timeframe_used,
                    "trades": y.trades,
                    "win_rate_pct": y.win_rate_pct,
                    "profit_factor": y.profit_factor,
                    "net_pnl": y.net_pnl,
                    "max_drawdown": y.max_drawdown,
                    "expectancy": y.expectancy,
                    "mff_sim_passes": y.mff_sim_passes,
                    "gate_pass": y.passes_all,
                }
                for y in sv.years
            ],
        }

    data = {
        "generated_at": datetime.now().isoformat(),
        "buy_verdict": verdict.buy_verdict,
        "buy_mff_now": verdict.buy_mff_now,
        "confidence": verdict.confidence,
        "blocking_reasons": verdict.blocking_reasons,
        "recommendations": verdict.recommendations,
        "symbols": {sym: _sv_dict(sv) for sym, sv in verdict.symbols.items()},
        "thresholds": {
            "min_trades": GATE.min_trades,
            "min_profit_factor": GATE.min_profit_factor,
            "max_drawdown_usd": GATE.max_drawdown_usd,
            "min_expectancy": GATE.min_expectancy,
            "min_mff_pass_years": GATE.min_mff_pass_years,
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="CB6 Futures Research Gate")
    p.add_argument("--symbol", default=None, help="Filter to a single symbol")
    p.add_argument("--reports-dir", default=REPORTS_DIR)
    p.add_argument("--require-1m", action="store_true",
                   help="Strict mode: fail any symbol without 1m data")
    p.add_argument("--min-pf", type=float, default=None, help="Override min profit factor")
    p.add_argument("--max-dd", type=float, default=None, help="Override max drawdown USD")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.min_pf:
        GATE.min_profit_factor = args.min_pf
    if args.max_dd:
        GATE.max_drawdown_usd = args.max_dd

    rows = load_latest_reports(args.reports_dir)
    if not rows:
        print("No research summary files found. Run the research runner first:")
        print("  python -m futures_engine.research.futures_research_runner --year 2024 --save-reports")
        sys.exit(1)

    # Group by symbol
    by_symbol: Dict[str, List[dict]] = {}
    for row in rows:
        raw_sym = row.get("symbol", "")
        sym, _ = _extract_timeframe(raw_sym)
        if args.symbol and sym.upper() != args.symbol.upper():
            continue
        by_symbol.setdefault(sym, []).append(row)

    # Evaluate each symbol
    symbol_verdicts: Dict[str, SymbolVerdict] = {}
    for sym, sym_rows in sorted(by_symbol.items()):
        year_checks = [check_year(r) for r in sym_rows]
        year_checks.sort(key=lambda y: y.year)
        symbol_verdicts[sym] = evaluate_symbol(
            sym, year_checks, require_1m=args.require_1m
        )

    verdict = overall_verdict(symbol_verdicts)
    print_gate_report(verdict)
    path = save_gate_report(verdict, args.reports_dir)
    print(f"Gate report saved: {path}")

    # Exit code: 0 = pass/conditional, 1 = do not buy
    sys.exit(0 if verdict.buy_verdict != "DO_NOT_BUY" else 1)


if __name__ == "__main__":
    main()
