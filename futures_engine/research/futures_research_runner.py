"""
CB6 Futures Core — Research Runner
Runs 2024/2025/2026 backtests for MES, MNQ, MGC, MCL and produces
a research summary table answering:

  - Trades per symbol
  - Win rate
  - Profit factor
  - Max drawdown
  - Expectancy
  - Session breakdown (RTH vs ETH vs kill-zone)
  - MFF eval simulation (would the strategy pass?)

Run from the project root:
    python -m futures_engine.research.futures_research_runner
    python -m futures_engine.research.futures_research_runner --year 2024
    python -m futures_engine.research.futures_research_runner --symbol MES --year 2025
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from futures_engine.core.futures_backtest_engine import BacktestConfig, FuturesBacktestEngine
from futures_engine.core.futures_data_feed import CSVDataFeed
from futures_engine.core.futures_performance import PerformanceReport, save_report
from futures_engine.core.futures_session_manager import FuturesSessionManager, SessionType
from futures_engine.core.futures_silver_bullet import SilverBulletScanner
from futures_engine.core.futures_symbol_registry import get_symbol, PHASE1_SYMBOLS
from futures_engine.mff_flex_25k.mff_flex_config import (
    EVAL_CONFIG, GUARDS_CONFIG,
)
from futures_engine.mff_flex_25k.mff_flex_rules import MFFFlexRuleEngine

logger = logging.getLogger("cb6.futures.research.runner")


def _make_signal_fn(symbol: str):
    scanner = SilverBulletScanner(symbol=symbol, sl_buffer_ticks=3, min_score=55.0)

    def fn(m1_bars, h4_bars):
        sym = get_symbol(symbol)
        return scanner.scan(m1_bars, h4_bars, sym.tick_size)

    return fn


def _best_available_timeframe(symbol: str, data_dir: str, preferred: str = "1m") -> str:
    """Return the finest timeframe that has data on disk for this symbol."""
    for tf in [preferred, "1m", "5m", "15m", "1h", "4h", "1d"]:
        path = os.path.join(data_dir, f"{symbol.upper()}_{tf}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 200:
            return tf
    return preferred  # caller will handle empty data


def run_backtest_year(
    symbol: str,
    year: int,
    feed: CSVDataFeed,
    data_dir: str = "data/futures/historical",
) -> Optional[PerformanceReport]:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end   = datetime(year, 12, 31, 23, 59, tzinfo=timezone.utc)

    # Use finest available timeframe; 1m is ideal, 1h is the research fallback
    trading_tf = _best_available_timeframe(symbol, data_dir, "1m")
    htf_tf     = _best_available_timeframe(symbol, data_dir, "4h")

    if trading_tf != "1m":
        logger.info(
            "%s: 1m data not available — using %s for research (results are approximate)",
            symbol, trading_tf,
        )

    # Adjust lookback: 1h needs fewer bars per scan window than 1m
    scanner_lookback = 120 if trading_tf == "1m" else 40  # ~5 days of 1h bars

    cfg = BacktestConfig(
        symbol=symbol,
        start=start, end=end,
        timeframe=trading_tf,
        htf_timeframe=htf_tf,
        starting_equity=EVAL_CONFIG.account_size,
        commission_per_side=2.25,
        slippage_ticks=1.0,
        max_contracts=1,
        micro_only=True,
        risk_pct=GUARDS_CONFIG.default_risk_pct,
        allow_overnight=False,
        use_rollover=True,
    )

    engine = FuturesBacktestEngine(cfg, feed, _make_signal_fn(symbol))
    try:
        report = engine.run()
        report.symbol = f"{symbol}[{trading_tf}]"  # tag so reader knows which TF was used
        return report
    except Exception as e:
        logger.error("Backtest error %s %d: %s", symbol, year, e)
        return None


def _session_breakdown(report: PerformanceReport) -> dict:
    """Count trades and win rate per session type from trade log."""
    by_session: dict = {}
    for trade in report.trade_log:
        s = trade.get("session", "UNKNOWN")
        if s not in by_session:
            by_session[s] = {"trades": 0, "wins": 0, "pnl": 0.0}
        by_session[s]["trades"] += 1
        pnl = trade.get("pnl_net", 0.0)
        by_session[s]["pnl"] = round(by_session[s]["pnl"] + pnl, 2)
        if pnl > 0:
            by_session[s]["wins"] += 1
    for s in by_session:
        t = by_session[s]["trades"]
        by_session[s]["win_rate"] = round(by_session[s]["wins"] / t, 4) if t else 0
    return by_session


def _compute_eod_drawdown(daily: dict, starting_equity: float) -> tuple[float, float, float]:
    """
    Simulate MFF's EOD trailing drawdown model against a daily PnL dict.
    Peak equity only ratchets UP at end of each trading day.
    Returns (max_eod_drawdown, peak_equity_reached, final_equity).
    """
    equity = starting_equity
    peak = starting_equity
    max_dd = 0.0
    for date_str in sorted(daily.keys()):
        equity += daily[date_str]
        if equity > peak:
            peak = equity         # ratchet up at EOD
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd, peak, equity


def _mff_simulation(report: PerformanceReport, year: int) -> dict:
    """Simulate running the strategy as an MFF eval and check if it would pass."""
    rule_engine = MFFFlexRuleEngine()

    # Gather daily PnL from trade log
    daily: dict = {}
    for trade in report.trade_log:
        d = str(trade.get("entry_time", ""))[:10]
        daily[d] = round(daily.get(d, 0.0) + trade.get("pnl_net", 0.0), 2)

    trading_days = len(daily)
    best_day = max(daily.values(), default=0.0)
    total_pnl = report.net_profit

    # F-3 fix: compute actual EOD trailing drawdown from day-by-day equity curve.
    # Previously peak_equity was wrongly set to starting+total_pnl (making DD = 0).
    max_eod_dd, peak_eq, final_eq = _compute_eod_drawdown(daily, EVAL_CONFIG.account_size)

    # Pass correct peak/current into the rule engine so drawdown violations are detected
    result = rule_engine.check_eval(
        current_equity=final_eq,
        peak_equity=peak_eq,
        daily_pnl=0.0,
        total_pnl=total_pnl,
        trading_days=trading_days,
        best_day_pnl=best_day,
    )

    # Explicit drawdown gate (belt-and-suspenders — rule engine may also flag it)
    dd_within_limit = max_eod_dd < EVAL_CONFIG.max_drawdown

    would_pass = (
        total_pnl >= EVAL_CONFIG.profit_target and
        trading_days >= EVAL_CONFIG.min_trading_days and
        dd_within_limit and
        result.passed
    )

    days_to_target = None
    if total_pnl > 0 and report.total_trades > 0:
        avg_daily = total_pnl / max(trading_days, 1)
        remaining = EVAL_CONFIG.profit_target - total_pnl
        days_to_target = max(0, remaining / avg_daily) if avg_daily > 0 else None

    return {
        "would_pass": would_pass,
        "profit_target": EVAL_CONFIG.profit_target,
        "total_pnl": round(total_pnl, 2),
        "max_eod_drawdown": round(max_eod_dd, 2),
        "max_drawdown_limit": EVAL_CONFIG.max_drawdown,
        "dd_within_limit": dd_within_limit,
        "consistency_ok": result.passed,
        "violations": [v.detail for v in result.violations],
        "trading_days": trading_days,
        "best_day_pnl": round(best_day, 2),
        "avg_daily_pnl": round(total_pnl / max(trading_days, 1), 2),
        "estimated_days_to_target": round(days_to_target, 1) if days_to_target else None,
    }


def build_summary_table(
    results: Dict[str, Dict[int, Optional[PerformanceReport]]],
) -> list:
    """Build the full research summary as a list of rows."""
    rows = []
    for symbol, year_results in results.items():
        for year, report in year_results.items():
            if report is None:
                rows.append({
                    "symbol": symbol, "year": year,
                    "status": "NO_DATA",
                })
                continue

            expectancy = (
                report.win_rate * report.avg_win -
                (1 - report.win_rate) * report.avg_loss
            )
            # Extract timeframe from tagged report.symbol e.g. "MES[1h]" → "1h"
            tf_used = "unknown"
            if "[" in report.symbol:
                tf_used = report.symbol.split("[")[1].rstrip("]")

            row = {
                "symbol": symbol,
                "year": year,
                "timeframe_used": tf_used,
                "status": "OK",
                "total_trades": report.total_trades,
                "win_rate_pct": round(report.win_rate * 100, 1),
                "profit_factor": report.profit_factor,
                "net_pnl": report.net_profit,
                "gross_profit": report.gross_profit,
                "gross_loss": report.gross_loss,
                "avg_win": report.avg_win,
                "avg_loss": report.avg_loss,
                "expectancy": round(expectancy, 2),
                "max_drawdown": report.max_drawdown,
                "sharpe": report.sharpe_ratio,
                "avg_r": report.avg_r_multiple,
                "commissions": report.total_commissions,
                "avg_duration_min": report.avg_trade_duration_min,
                "session_breakdown": _session_breakdown(report),
                "mff_simulation": _mff_simulation(report, year),
            }
            rows.append(row)

    return rows


def print_summary_table(rows: list) -> None:
    """Print a human-readable summary to stdout."""
    print("\n" + "=" * 100)
    print("CB6 FUTURES CORE — RESEARCH SUMMARY")
    print("=" * 100)
    print(f"{'Symbol':<6} {'Year':<5} {'Trades':<8} {'WR%':<7} {'PF':<6} "
          f"{'NetPnL':>9} {'MaxDD':>8} {'Expectancy':>11} {'Sharpe':>7} {'MFF':>6}")
    print("-" * 100)
    for row in rows:
        if row.get("status") == "NO_DATA":
            print(f"{row['symbol']:<6} {row['year']:<5}  *** NO DATA — download first ***")
            continue
        mff = "PASS" if row["mff_simulation"]["would_pass"] else "FAIL"
        print(
            f"{row['symbol']:<6} {row['year']:<5} "
            f"{row['total_trades']:<8} "
            f"{row['win_rate_pct']:<7.1f} "
            f"{row['profit_factor']:<6.2f} "
            f"${row['net_pnl']:>8.0f} "
            f"${row['max_drawdown']:>7.0f} "
            f"${row['expectancy']:>10.2f} "
            f"{row['sharpe']:>7.2f} "
            f"{mff:>6}"
        )
    print("=" * 100)

    # Session breakdown
    print("\nSESSION BREAKDOWN (win rate by session type):")
    for row in rows:
        if row.get("status") == "NO_DATA":
            continue
        sb = row.get("session_breakdown", {})
        if sb:
            parts = [f"{s}: {v['trades']}t {v['win_rate']*100:.0f}%"
                     for s, v in sb.items()]
            print(f"  {row['symbol']} {row['year']}: {' | '.join(parts)}")

    # MFF simulation detail
    print("\nMFF EVAL SIMULATION DETAIL:")
    for row in rows:
        if row.get("status") == "NO_DATA":
            continue
        mff = row["mff_simulation"]
        status = "✓ PASS" if mff["would_pass"] else "✗ FAIL"
        eod_dd = mff.get("max_eod_drawdown", mff.get("max_drawdown_vs_limit", "n/a"))
        print(f"  {row['symbol']} {row['year']}: {status} | "
              f"PnL=${mff['total_pnl']:.0f} vs target ${mff['profit_target']:.0f} | "
              f"EOD DD: ${eod_dd} vs ${mff.get('max_drawdown_limit', 1000):.0f} | "
              f"BestDay=${mff['best_day_pnl']:.0f} | "
              f"TradingDays={mff['trading_days']}")
        if mff.get("violations"):
            for v in mff["violations"]:
                print(f"    VIOLATION: {v}")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="CB6 Futures Research Runner")
    p.add_argument("--symbol", default=None, help="Single symbol (default: all Phase 1)")
    p.add_argument("--year",   default=None, type=int, help="Single year (default: 2024, 2025, 2026)")
    p.add_argument("--data-dir", default="data/futures/historical")
    p.add_argument("--save-reports", action="store_true", help="Save individual JSON reports")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    symbols = [args.symbol.upper()] if args.symbol else PHASE1_SYMBOLS
    years   = [args.year] if args.year else [2024, 2025, 2026]
    feed    = CSVDataFeed(args.data_dir)

    # Check data availability
    logger.info("Checking data inventory...")
    inventory = {}
    for sym in symbols:
        for tf in ["1m", "4h"]:
            key = f"{sym}_{tf}"
            path = os.path.join(args.data_dir, f"{sym}_{tf}.csv")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    bars = sum(1 for _ in f) - 1
                inventory[key] = bars
                logger.info("  %-12s %6d bars", key, bars)
            else:
                logger.warning("  %-12s MISSING — run futures_data_downloader first", key)

    if not inventory:
        print("\nNO DATA FOUND. Please download data first:")
        print("  python -m futures_engine.research.futures_data_downloader --all")
        print("  # or for a single symbol:")
        print("  python -m futures_engine.research.futures_data_downloader --symbol MES --source yahoo --start 2024-01-01")
        return

    # Run backtests
    results: Dict[str, Dict[int, Optional[PerformanceReport]]] = {}
    for sym in symbols:
        results[sym] = {}
        for year in years:
            logger.info("Running: %s %d ...", sym, year)
            report = run_backtest_year(sym, year, feed, data_dir=args.data_dir)
            results[sym][year] = report
            if report and args.save_reports:
                save_report(report, output_dir=f"reports/futures/research/{sym}")

    # Build and display table
    rows = build_summary_table(results)
    print_summary_table(rows)

    # Save full JSON
    out_dir = "reports/futures/research"
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"research_summary_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)
    print(f"\nFull results saved: {out_path}")


if __name__ == "__main__":
    main()
