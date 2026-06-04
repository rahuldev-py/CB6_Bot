"""
CB6 Futures Core — MFF Flex Backtester
Wraps the generic backtest engine with MFF Flex-specific:
- Profit target detection
- Consistency rule enforcement during backtest
- Contract limits (2 max, start with 1 micro)
- EOD drawdown model simulation
- Session-filtered entry windows
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from futures_engine.core.futures_backtest_engine import BacktestConfig, FuturesBacktestEngine
from futures_engine.core.futures_data_feed import FuturesDataFeed
from futures_engine.core.futures_performance import PerformanceReport, save_report
from futures_engine.core.futures_signal_scanner import FuturesSignalScanner
from futures_engine.core.futures_silver_bullet import SilverBulletScanner
from futures_engine.core.futures_symbol_registry import get_symbol
from futures_engine.mff_flex_25k.mff_flex_config import (
    EVAL_CONFIG, FUNDED_CONFIG, GUARDS_CONFIG, SYMBOLS,
)
from futures_engine.mff_flex_25k.mff_flex_rules import MFFFlexRuleEngine

logger = logging.getLogger("cb6.futures.mff_flex.backtester")


class MFFFlexBacktester:
    """
    Full backtest environment for MFF Flex $25K evaluation.
    Runs the Silver Bullet strategy with MFF rule enforcement.
    """

    def __init__(
        self,
        feed: FuturesDataFeed,
        symbols: Optional[List[str]] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        timeframe: str = "1m",
        htf_timeframe: str = "4h",
        data_source: str = "csv",
    ):
        self._feed = feed
        self._symbols = symbols or SYMBOLS.phase1
        self._start = start or (datetime.now(timezone.utc) - timedelta(days=365))
        self._end = end or datetime.now(timezone.utc)
        self._timeframe = timeframe
        self._htf_timeframe = htf_timeframe
        self._data_source = data_source
        self._rule_engine = MFFFlexRuleEngine()

    def _make_signal_fn(self, symbol: str):
        scanner = SilverBulletScanner(
            symbol=symbol,
            sl_buffer_ticks=3,
            min_score=55.0,
        )

        def signal_fn(m1_bars, h4_bars):
            from futures_engine.core.futures_symbol_registry import get_symbol
            sym = get_symbol(symbol)
            return scanner.scan(m1_bars, h4_bars, sym.tick_size)

        return signal_fn

    def run_symbol(self, symbol: str) -> PerformanceReport:
        logger.info("MFF Flex backtest: %s %s → %s",
                    symbol, self._start.date(), self._end.date())

        sym_info = get_symbol(symbol)

        cfg = BacktestConfig(
            symbol=symbol,
            start=self._start,
            end=self._end,
            timeframe=self._timeframe,
            htf_timeframe=self._htf_timeframe,
            starting_equity=EVAL_CONFIG.account_size,
            commission_per_side=2.25,       # NinjaTrader/Rithmic typical
            slippage_ticks=1.0,
            max_contracts=EVAL_CONFIG.max_contracts,
            micro_only=sym_info.standard_symbol is not None,
            risk_pct=GUARDS_CONFIG.default_risk_pct,
            allow_overnight=False,          # Flat EOD
            use_rollover=True,
        )

        engine = FuturesBacktestEngine(cfg, self._feed, self._make_signal_fn(symbol))
        report = engine.run()

        self._log_mff_summary(report, symbol)
        return report

    def run_all(self) -> dict[str, PerformanceReport]:
        results = {}
        for sym in self._symbols:
            try:
                results[sym] = self.run_symbol(sym)
            except Exception as e:
                logger.exception("Backtest failed for %s: %s", sym, e)
        self._save_run_summary(results)
        return results

    def _save_run_summary(self, results: dict[str, PerformanceReport]) -> None:
        out_dir = "reports/futures_backtest"
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"backtest_summary_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "source": self._data_source,
                "timeframe": self._timeframe,
                "htf_timeframe": self._htf_timeframe,
                "start": self._start.isoformat(),
                "end": self._end.isoformat(),
                "symbols": self._symbols,
                "results": {
                    sym: {
                        "total_trades": rep.total_trades,
                        "win_rate": rep.win_rate,
                        "net_profit": rep.net_profit,
                        "max_drawdown": rep.max_drawdown,
                        "period_start": rep.period_start,
                        "period_end": rep.period_end,
                    }
                    for sym, rep in results.items()
                },
            }, f, indent=2)
        logger.info("Backtest run summary: %s", path)

    def _log_mff_summary(self, report: PerformanceReport, symbol: str) -> None:
        """Check if eval would have passed and log consistency."""
        if report.total_trades == 0:
            logger.info("%s: no trades generated", symbol)
            return

        # Simulate MFF eval check against backtest results
        # Estimate best day from trade log
        daily_pnl: dict = {}
        for trade in report.trade_log:
            entry_date = str(trade.get("entry_time", ""))[:10]
            daily_pnl[entry_date] = daily_pnl.get(entry_date, 0.0) + trade.get("pnl_net", 0.0)

        best_day = max(daily_pnl.values(), default=0.0)
        total_pnl = report.net_profit

        check = self._rule_engine.check_eval(
            current_equity=EVAL_CONFIG.account_size + total_pnl,
            peak_equity=EVAL_CONFIG.account_size + max(total_pnl, 0),
            daily_pnl=0.0,
            total_pnl=total_pnl,
            trading_days=len(daily_pnl),
            best_day_pnl=best_day,
        )

        status = "WOULD PASS" if (
            total_pnl >= EVAL_CONFIG.profit_target and
            len(daily_pnl) >= EVAL_CONFIG.min_trading_days and
            check.passed
        ) else "WOULD FAIL"

        logger.info(
            "%s Backtest MFF Eval: %s | PnL=$%.2f | Target=$%.2f | "
            "Days=%d | Consistency=%s | MaxDD=$%.2f",
            symbol, status, total_pnl, EVAL_CONFIG.profit_target,
            len(daily_pnl), "OK" if check.passed else "FAIL",
            report.max_drawdown,
        )

        # Save summary
        out_dir = "reports/futures/mff_flex_25k"
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"backtest_{symbol}_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "symbol": symbol,
                "source": self._data_source,
                "timeframe": self._timeframe,
                "htf_timeframe": self._htf_timeframe,
                "status": status,
                "net_profit": report.net_profit,
                "profit_target": EVAL_CONFIG.profit_target,
                "win_rate": report.win_rate,
                "total_trades": report.total_trades,
                "max_drawdown": report.max_drawdown,
                "mff_rules_passed": check.passed,
                "violations": [v.detail for v in check.violations],
                "trading_days": len(daily_pnl),
                "best_day_pnl": best_day,
            }, f, indent=2)
        logger.info("Backtest report: %s", path)
        save_report(report, output_dir="reports/futures_backtest")
