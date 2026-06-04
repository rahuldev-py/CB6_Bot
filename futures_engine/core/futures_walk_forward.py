"""
CB6 Futures Core — Walk-Forward Analysis
Splits historical data into IS/OOS windows and runs backtest on each.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, List

from futures_engine.core.futures_backtest_engine import BacktestConfig, FuturesBacktestEngine
from futures_engine.core.futures_data_feed import FuturesDataFeed
from futures_engine.core.futures_performance import PerformanceReport, save_report

logger = logging.getLogger("cb6.futures.wfa")


@dataclass
class WFAWindow:
    window_id: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime
    is_report: PerformanceReport = field(default=None)
    oos_report: PerformanceReport = field(default=None)


@dataclass
class WFAResult:
    symbol: str
    windows: List[WFAWindow]
    combined_oos_trades: int = 0
    combined_oos_net_pnl: float = 0.0
    combined_oos_win_rate: float = 0.0
    combined_oos_max_dd: float = 0.0


class WalkForwardAnalyzer:
    """
    Runs walk-forward analysis: for each window, optimise on IS, validate on OOS.
    For CB6 the "optimise" step is a fixed strategy — we just report IS vs OOS consistency.
    """

    def __init__(
        self,
        config: BacktestConfig,
        feed: FuturesDataFeed,
        signal_fn: Callable,
        is_days: int = 180,
        oos_days: int = 60,
    ):
        self.config = config
        self.feed = feed
        self.signal_fn = signal_fn
        self.is_days = is_days
        self.oos_days = oos_days

    def _build_windows(self) -> List[WFAWindow]:
        windows: List[WFAWindow] = []
        cursor = self.config.start
        total_days = (self.config.end - self.config.start).days
        window_id = 1

        while cursor + timedelta(days=self.is_days + self.oos_days) <= self.config.end:
            is_start = cursor
            is_end = cursor + timedelta(days=self.is_days)
            oos_start = is_end
            oos_end = oos_start + timedelta(days=self.oos_days)
            windows.append(WFAWindow(
                window_id=window_id,
                is_start=is_start, is_end=is_end,
                oos_start=oos_start, oos_end=oos_end,
            ))
            cursor = oos_start  # anchored walk-forward (non-anchored: cursor += oos_days)
            window_id += 1

        return windows

    def run(self) -> WFAResult:
        windows = self._build_windows()
        if not windows:
            logger.warning("WFA: no windows generated for %s", self.config.symbol)
            return WFAResult(symbol=self.config.symbol, windows=[])

        logger.info("WFA: %d windows for %s", len(windows), self.config.symbol)

        for w in windows:
            # In-sample
            is_cfg = BacktestConfig(
                symbol=self.config.symbol,
                start=w.is_start, end=w.is_end,
                timeframe=self.config.timeframe,
                htf_timeframe=self.config.htf_timeframe,
                starting_equity=self.config.starting_equity,
                commission_per_side=self.config.commission_per_side,
                slippage_ticks=self.config.slippage_ticks,
                max_contracts=self.config.max_contracts,
                micro_only=self.config.micro_only,
                risk_pct=self.config.risk_pct,
                allow_overnight=self.config.allow_overnight,
            )
            w.is_report = FuturesBacktestEngine(is_cfg, self.feed, self.signal_fn).run()

            # Out-of-sample
            oos_cfg = BacktestConfig(
                symbol=self.config.symbol,
                start=w.oos_start, end=w.oos_end,
                timeframe=self.config.timeframe,
                htf_timeframe=self.config.htf_timeframe,
                starting_equity=self.config.starting_equity,
                commission_per_side=self.config.commission_per_side,
                slippage_ticks=self.config.slippage_ticks,
                max_contracts=self.config.max_contracts,
                micro_only=self.config.micro_only,
                risk_pct=self.config.risk_pct,
                allow_overnight=self.config.allow_overnight,
            )
            w.oos_report = FuturesBacktestEngine(oos_cfg, self.feed, self.signal_fn).run()

            logger.info(
                "W%d IS: trades=%d WR=%.1f%% net=$%.2f | OOS: trades=%d WR=%.1f%% net=$%.2f",
                w.window_id,
                w.is_report.total_trades, w.is_report.win_rate * 100, w.is_report.net_profit,
                w.oos_report.total_trades, w.oos_report.win_rate * 100, w.oos_report.net_profit,
            )

        # Aggregate OOS
        all_oos = [w.oos_report for w in windows if w.oos_report]
        total_oos_trades = sum(r.total_trades for r in all_oos)
        total_oos_net = sum(r.net_profit for r in all_oos)
        avg_oos_wr = (
            sum(r.win_rate * r.total_trades for r in all_oos) / total_oos_trades
            if total_oos_trades > 0 else 0.0
        )
        max_oos_dd = max((r.max_drawdown for r in all_oos), default=0.0)

        result = WFAResult(
            symbol=self.config.symbol,
            windows=windows,
            combined_oos_trades=total_oos_trades,
            combined_oos_net_pnl=round(total_oos_net, 2),
            combined_oos_win_rate=round(avg_oos_wr, 4),
            combined_oos_max_dd=round(max_oos_dd, 2),
        )

        self._save_summary(result)
        return result

    def _save_summary(self, result: WFAResult) -> None:
        out_dir = "reports/futures/wfa"
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"wfa_{result.symbol}_{ts}.json")
        summary = {
            "symbol": result.symbol,
            "windows": len(result.windows),
            "combined_oos_trades": result.combined_oos_trades,
            "combined_oos_net_pnl": result.combined_oos_net_pnl,
            "combined_oos_win_rate": result.combined_oos_win_rate,
            "combined_oos_max_dd": result.combined_oos_max_dd,
            "windows_detail": [
                {
                    "id": w.window_id,
                    "is_period": f"{w.is_start.date()} → {w.is_end.date()}",
                    "oos_period": f"{w.oos_start.date()} → {w.oos_end.date()}",
                    "is_net": w.is_report.net_profit if w.is_report else 0,
                    "oos_net": w.oos_report.net_profit if w.oos_report else 0,
                    "is_wr": w.is_report.win_rate if w.is_report else 0,
                    "oos_wr": w.oos_report.win_rate if w.oos_report else 0,
                }
                for w in result.windows
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        logger.info("WFA summary saved: %s", path)
