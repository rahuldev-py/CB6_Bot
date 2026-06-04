"""
CB6 Futures Core — Performance Reporting
Computes standard futures trading metrics from a trade log.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional
import math


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    contract: str
    direction: str           # "LONG" | "SHORT"
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    contracts: int
    point_value: float
    commission: float        # per side, total round-trip = 2×
    slippage: float          # per side, total = 2×
    pnl_gross: float = 0.0
    pnl_net: float = 0.0
    r_multiple: float = 0.0  # realised R
    stop_loss: float = 0.0
    target: float = 0.0
    session: str = ""
    tags: List[str] = field(default_factory=list)
    open: bool = True

    def close_trade(
        self,
        exit_price: float,
        exit_time: datetime,
    ) -> None:
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.open = False
        direction_mult = 1 if self.direction == "LONG" else -1
        self.pnl_gross = (
            direction_mult * (exit_price - self.entry_price)
            * self.contracts * self.point_value
        )
        total_costs = (self.commission + self.slippage) * 2
        self.pnl_net = self.pnl_gross - total_costs

        risk_points = abs(self.entry_price - self.stop_loss) if self.stop_loss else 0
        risk_usd = risk_points * self.contracts * self.point_value if risk_points else 0
        self.r_multiple = round(self.pnl_net / risk_usd, 2) if risk_usd else 0.0


@dataclass
class PerformanceReport:
    symbol: str
    period_start: str
    period_end: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float              # 0-1
    gross_profit: float
    gross_loss: float
    net_profit: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_r_multiple: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    max_drawdown: float          # peak-to-trough of equity curve
    max_drawdown_pct: float
    sharpe_ratio: float
    total_commissions: float
    total_slippage: float
    avg_trade_duration_min: float
    trade_log: List[dict] = field(default_factory=list)


def compute_performance(
    trades: List[TradeRecord],
    symbol: str = "ALL",
    risk_free_rate: float = 0.05,
) -> PerformanceReport:
    closed = [t for t in trades if not t.open]
    if not closed:
        return PerformanceReport(
            symbol=symbol, period_start="", period_end="",
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0.0, gross_profit=0.0, gross_loss=0.0, net_profit=0.0,
            profit_factor=0.0, avg_win=0.0, avg_loss=0.0, avg_r_multiple=0.0,
            max_consecutive_wins=0, max_consecutive_losses=0,
            max_drawdown=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
            total_commissions=0.0, total_slippage=0.0, avg_trade_duration_min=0.0,
        )

    closed.sort(key=lambda t: t.entry_time)
    pnls = [t.pnl_net for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_profit = gross_profit - gross_loss
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate = len(wins) / len(pnls)

    # Consecutive wins/losses
    max_cw = max_cl = cur_cw = cur_cl = 0
    for p in pnls:
        if p > 0:
            cur_cw += 1; cur_cl = 0
            max_cw = max(max_cw, cur_cw)
        else:
            cur_cl += 1; cur_cw = 0
            max_cl = max(max_cl, cur_cl)

    # Equity curve drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    peak_for_pct = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
            peak_for_pct = peak
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    max_dd_pct = max_dd / peak_for_pct if peak_for_pct > 0 else 0.0

    # Sharpe (daily returns assumed)
    import statistics
    daily_ret = pnls
    if len(daily_ret) > 1:
        avg_r = statistics.mean(daily_ret)
        std_r = statistics.stdev(daily_ret)
        sharpe = (avg_r - risk_free_rate / 252) / std_r * math.sqrt(252) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Duration
    durations = []
    for t in closed:
        if t.exit_time and t.entry_time:
            durations.append((t.exit_time - t.entry_time).total_seconds() / 60)
    avg_dur = sum(durations) / len(durations) if durations else 0.0

    total_comm = sum(t.commission * 2 for t in closed)
    total_slip = sum(t.slippage * 2 for t in closed)
    avg_r = sum(t.r_multiple for t in closed) / len(closed)

    return PerformanceReport(
        symbol=symbol,
        period_start=closed[0].entry_time.isoformat(),
        period_end=closed[-1].entry_time.isoformat(),
        total_trades=len(closed),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(win_rate, 4),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        net_profit=round(net_profit, 2),
        profit_factor=round(profit_factor, 3),
        avg_win=round(sum(wins) / len(wins) if wins else 0, 2),
        avg_loss=round(abs(sum(losses) / len(losses)) if losses else 0, 2),
        avg_r_multiple=round(avg_r, 2),
        max_consecutive_wins=max_cw,
        max_consecutive_losses=max_cl,
        max_drawdown=round(max_dd, 2),
        max_drawdown_pct=round(max_dd_pct, 4),
        sharpe_ratio=round(sharpe, 3),
        total_commissions=round(total_comm, 2),
        total_slippage=round(total_slip, 2),
        avg_trade_duration_min=round(avg_dur, 1),
        trade_log=[asdict(t) if hasattr(t, '__dataclass_fields__') else vars(t) for t in closed],
    )


def save_report(report: PerformanceReport, output_dir: str = "reports/futures") -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"perf_{report.symbol}_{ts}.json")
    data = {k: v for k, v in vars(report).items() if k != "trade_log"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    csv_path = os.path.join(output_dir, f"trades_{report.symbol}_{ts}.csv")
    if report.trade_log:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=report.trade_log[0].keys())
            writer.writeheader()
            writer.writerows(report.trade_log)

    return path
