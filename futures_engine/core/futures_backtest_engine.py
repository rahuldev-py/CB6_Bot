"""
CB6 Futures Core — Backtest Engine
Bar-by-bar simulation with contract rollover, commissions, and slippage.
Session-aware. Paper mode compatible.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from futures_engine.core.futures_data_feed import FuturesDataFeed, FuturesBar
from futures_engine.core.futures_contract_manager import ContractManager
from futures_engine.core.futures_performance import TradeRecord, PerformanceReport, compute_performance
from futures_engine.core.futures_symbol_registry import get_symbol
from ml_engine.memory.shadow_logger import log_closed_trade
from ml_engine.memory.replay_shadow import archive_closed_trade_shadow

logger = logging.getLogger("cb6.futures.backtest")


@dataclass
class BacktestConfig:
    symbol: str
    start: datetime
    end: datetime
    timeframe: str = "1m"
    htf_timeframe: str = "4h"
    starting_equity: float = 25000.0
    commission_per_side: float = 2.25   # NinjaTrader/Rithmic typical rate
    slippage_ticks: float = 1.0         # 1 tick slippage per fill
    max_contracts: int = 2
    micro_only: bool = True
    risk_pct: float = 0.01
    allow_overnight: bool = False
    use_rollover: bool = True


@dataclass
class BacktestState:
    equity: float
    peak_equity: float
    open_trades: List[TradeRecord] = field(default_factory=list)
    closed_trades: List[TradeRecord] = field(default_factory=list)
    bar_count: int = 0
    current_contract: str = ""


class FuturesBacktestEngine:
    """
    Drives a signal generator bar-by-bar through historical data.
    Manages fills, commissions, slippage, and EOD flat-check.
    """

    SignalCallback = Callable[[List[FuturesBar], List[FuturesBar]], list]

    def __init__(
        self,
        config: BacktestConfig,
        feed: FuturesDataFeed,
        signal_fn: "FuturesBacktestEngine.SignalCallback",
    ):
        self.cfg = config
        self.feed = feed
        self.signal_fn = signal_fn
        self._state = BacktestState(
            equity=config.starting_equity,
            peak_equity=config.starting_equity,
        )
        self._contract_mgr = ContractManager(config.symbol)
        self._sym = get_symbol(config.symbol)
        # F-1: pending setup waiting for next-bar fill confirmation
        self._pending_setup = None

    def _slippage_value(self) -> float:
        return self.cfg.slippage_ticks * self._sym.tick_size * self._sym.point_value / self._sym.tick_size
        # = slippage_ticks * tick_value

    def _fill_price(self, price: float, direction: str) -> float:
        slip = self.cfg.slippage_ticks * self._sym.tick_size
        return price + slip if direction == "LONG" else price - slip

    def _open_trade(
        self,
        setup,
        bar: FuturesBar,
        contracts: int,
    ) -> TradeRecord:
        fill = self._fill_price(setup.entry, setup.direction)
        trade = TradeRecord(
            trade_id=str(uuid.uuid4())[:8],
            symbol=self.cfg.symbol,
            contract=self._state.current_contract,
            direction=setup.direction,
            entry_time=bar.timestamp,
            exit_time=None,
            entry_price=fill,
            exit_price=None,
            contracts=contracts,
            point_value=self._sym.point_value,
            commission=self.cfg.commission_per_side,
            slippage=self.cfg.slippage_ticks * self._sym.tick_value,
            stop_loss=setup.stop_loss,
            target=setup.target_3,
            session=setup.session.value if hasattr(setup, "session") else "",
            open=True,
        )
        self._state.open_trades.append(trade)
        return trade

    def _check_exits(self, bar: FuturesBar) -> None:
        still_open: List[TradeRecord] = []
        for trade in self._state.open_trades:
            exit_price: Optional[float] = None

            if trade.direction == "LONG":
                if bar.low <= trade.stop_loss:
                    exit_price = trade.stop_loss
                elif bar.high >= trade.target:
                    exit_price = trade.target
            else:
                if bar.high >= trade.stop_loss:
                    exit_price = trade.stop_loss
                elif bar.low <= trade.target:
                    exit_price = trade.target

            if exit_price is not None:
                fill = self._fill_price(exit_price, "SHORT" if trade.direction == "LONG" else "LONG")
                trade.close_trade(fill, bar.timestamp)
                self._state.equity += trade.pnl_net
                self._state.peak_equity = max(self._state.peak_equity, self._state.equity)
                self._state.closed_trades.append(trade)
                try:
                    outcome = "WIN" if trade.pnl_net > 0 else ("BREAKEVEN" if trade.pnl_net == 0 else "LOSS")
                    risk = abs(float(trade.entry_price) - float(trade.stop_loss))
                    rr = round((abs(float(trade.exit_price) - float(trade.entry_price)) / risk), 2) if risk > 0 else None
                    log_closed_trade(
                        "futures",
                        "futures_backtest_engine",
                        {
                            "symbol": trade.symbol,
                            "direction": trade.direction,
                            "entry_price": trade.entry_price,
                            "exit_price": trade.exit_price,
                            "session": trade.session,
                            "regime": "UNKNOWN",
                        },
                        result=outcome,
                        rr_achieved=rr,
                        metadata={"pnl_usd": trade.pnl_net, "exit_time": str(trade.exit_time)},
                    )
                    archive_closed_trade_shadow(
                        "futures",
                        "futures_backtest_engine",
                        {
                            "trade_id": trade.trade_id,
                            "symbol": trade.symbol,
                            "direction": trade.direction,
                            "entry_time": str(trade.entry_time),
                            "exit_time": str(trade.exit_time),
                            "session": trade.session,
                            "regime": "UNKNOWN",
                        },
                        result=outcome,
                        rr_achieved=rr,
                        metadata={"pnl_usd": trade.pnl_net, "exit_time": str(trade.exit_time)},
                    )
                except Exception:
                    pass
                logger.debug("closed %s %s pnl=$%.2f", trade.trade_id, trade.direction, trade.pnl_net)
            else:
                still_open.append(trade)

        self._state.open_trades = still_open

    def _eod_flat(self, bar: FuturesBar) -> None:
        """Force-close all positions at bar's close (EOD or rollover)."""
        for trade in self._state.open_trades:
            trade.close_trade(bar.close, bar.timestamp)
            self._state.equity += trade.pnl_net
            self._state.closed_trades.append(trade)
            try:
                outcome = "WIN" if trade.pnl_net > 0 else ("BREAKEVEN" if trade.pnl_net == 0 else "LOSS")
                risk = abs(float(trade.entry_price) - float(trade.stop_loss))
                rr = round((abs(float(trade.exit_price) - float(trade.entry_price)) / risk), 2) if risk > 0 else None
                log_closed_trade(
                    "futures",
                    "futures_backtest_engine",
                    {
                        "symbol": trade.symbol,
                        "direction": trade.direction,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "session": trade.session,
                        "regime": "UNKNOWN",
                    },
                    result=outcome,
                    rr_achieved=rr,
                    metadata={"pnl_usd": trade.pnl_net, "exit_time": str(trade.exit_time), "exit_reason": "EOD_FLAT"},
                )
                archive_closed_trade_shadow(
                    "futures",
                    "futures_backtest_engine",
                    {
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "direction": trade.direction,
                        "entry_time": str(trade.entry_time),
                        "exit_time": str(trade.exit_time),
                        "session": trade.session,
                        "regime": "UNKNOWN",
                    },
                    result=outcome,
                    rr_achieved=rr,
                    metadata={"pnl_usd": trade.pnl_net, "exit_time": str(trade.exit_time), "exit_reason": "EOD_FLAT"},
                )
            except Exception:
                pass
            logger.debug("eod_flat %s pnl=$%.2f", trade.trade_id, trade.pnl_net)
        self._state.open_trades = []

    def run(self) -> PerformanceReport:
        logger.info(
            "Backtest: %s %s %s → %s",
            self.cfg.symbol, self.cfg.timeframe,
            self.cfg.start.date(), self.cfg.end.date()
        )

        m1_bars = self.feed.get_bars(
            self.cfg.symbol, self.cfg.timeframe, self.cfg.start, self.cfg.end
        )
        h4_bars = self.feed.get_bars(
            self.cfg.symbol, self.cfg.htf_timeframe, self.cfg.start, self.cfg.end
        )

        if not m1_bars:
            logger.warning("No bars for %s — returning empty report", self.cfg.symbol)
            return compute_performance([], self.cfg.symbol)

        window_m1: List[FuturesBar] = []
        window_h4: List[FuturesBar] = []
        h4_idx = 0

        from futures_engine.core.futures_position_sizing import calculate_contracts

        for i, bar in enumerate(m1_bars):
            # Update rolling windows
            window_m1.append(bar)
            if len(window_m1) > 200:
                window_m1.pop(0)

            # Advance H4 pointer
            while h4_idx < len(h4_bars) and h4_bars[h4_idx].timestamp <= bar.timestamp:
                window_h4.append(h4_bars[h4_idx])
                if len(window_h4) > 60:
                    window_h4.pop(0)
                h4_idx += 1

            # Update contract
            self._state.current_contract = self._contract_mgr.active_contract(bar.timestamp.date())

            # Check if rollover day — clear pending setup too
            if self.cfg.use_rollover and self._contract_mgr.is_rollover_day(bar.timestamp.date()):
                if self._state.open_trades:
                    self._eod_flat(bar)
                self._pending_setup = None

            # ── Step 1: Exit existing trades on this bar ─────────────────────
            self._check_exits(bar)

            # ── Step 2: EOD flat + expire pending setup ───────────────────────
            if not self.cfg.allow_overnight and i < len(m1_bars) - 1:
                next_bar = m1_bars[i + 1]
                today = bar.timestamp.astimezone(timezone.utc).date()
                next_day = next_bar.timestamp.astimezone(timezone.utc).date()
                if next_day > today:
                    if self._state.open_trades:
                        self._eod_flat(bar)
                    self._pending_setup = None   # pending setups expire at EOD

            # ── Step 3: Attempt fill of PREVIOUS bar's pending setup (F-1 fix) ─
            # The signal fired on bar[i-1]. Now on bar[i], check if price
            # reached the limit entry. If not, the setup expires (1-bar window).
            if self._pending_setup is not None and not self._state.open_trades:
                ps = self._pending_setup
                self._pending_setup = None          # always consume after 1 bar
                filled = False
                if ps.direction == "LONG":
                    # Price must have traded down to the FVG bottom
                    if bar.low <= ps.entry and bar.low > ps.stop_loss:
                        filled = True
                else:  # SHORT
                    # Price must have traded up to the FVG top
                    if bar.high >= ps.entry and bar.high < ps.stop_loss:
                        filled = True

                if filled:
                    try:
                        size = calculate_contracts(
                            symbol=self.cfg.symbol,
                            account_equity=self._state.equity,
                            entry=ps.entry,
                            stop_loss=ps.stop_loss,
                            risk_pct=self.cfg.risk_pct,
                            max_contracts=self.cfg.max_contracts,
                            micro_only=self.cfg.micro_only,
                        )
                        self._open_trade(ps, bar, size.contracts)
                    except ValueError:
                        pass

            # ── Step 4: Detect signal for NEXT bar's fill attempt ─────────────
            # Only generate when flat and no setup pending.
            if not self._state.open_trades and self._pending_setup is None and len(window_m1) >= 10:
                try:
                    setups = self.signal_fn(window_m1, window_h4)
                    if setups:
                        self._pending_setup = max(setups, key=lambda s: s.score)
                except Exception as e:
                    logger.debug("signal_fn error at bar %d: %s", i, e)

            self._state.bar_count += 1

        # Final EOD flat
        if m1_bars and self._state.open_trades:
            self._eod_flat(m1_bars[-1])
        self._pending_setup = None

        report = compute_performance(self._state.closed_trades, self.cfg.symbol)
        logger.info(
            "Backtest done: %d trades | WR=%.1f%% | Net=$%.2f | MaxDD=$%.2f",
            report.total_trades,
            report.win_rate * 100,
            report.net_profit,
            report.max_drawdown,
        )
        return report
