"""
CB6 Futures Core — MFF Flex Runner
Main orchestrator for the MFF Flex $25K account.
Modes: OFF | PAPER | BACKTEST | MANUAL_MONITOR | SEMI_AUTO
LIVE_AUTO is permanently disabled.

Entry point: MFFFlexRunner.run()
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional

from futures_engine.core.futures_data_feed import FuturesDataFeed, PaperDataFeed
from futures_engine.core.futures_execution_router import FuturesExecutionRouter
from futures_engine.core.futures_signal_scanner import FuturesSignalScanner
from futures_engine.mff_flex_25k.mff_flex_backtester import MFFFlexBacktester
from futures_engine.mff_flex_25k.mff_flex_config import EVAL_CONFIG, GUARDS_CONFIG, SYMBOLS
from futures_engine.mff_flex_25k.mff_flex_connector import MFFFlexConnector
from futures_engine.mff_flex_25k.mff_flex_manual_bridge import MFFFlexManualBridge
from futures_engine.mff_flex_25k.mff_flex_payout_guard import MFFFlexPayoutGuard
from futures_engine.mff_flex_25k.mff_flex_risk_guard import MFFFlexRiskGuard
from futures_engine.mff_flex_25k.mff_flex_rules import MFFFlexRuleEngine
from futures_engine.mff_flex_25k.mff_flex_state import MFFFlexState

logger = logging.getLogger("cb6.futures.mff_flex.runner")

LIVE_AUTO_ENABLED = False   # NEVER set True without full broker auth (Phase 7)

VALID_MODES = {"OFF", "PAPER", "BACKTEST", "MANUAL_MONITOR", "SEMI_AUTO"}

# ── Semi-auto approval flow ────────────────────────────────────────────────────

class SemiAutoApprovalQueue:
    """
    Holds pending setups waiting for manual approval in SEMI_AUTO mode.
    Approval is polled from a JSON file (user writes True/False to approve).
    """

    QUEUE_PATH = "data/futures/mff_flex_25k/approval_queue.json"

    def __init__(self):
        os.makedirs(os.path.dirname(self.QUEUE_PATH), exist_ok=True)

    def push(self, setup_dict: dict) -> None:
        queue = self._load()
        queue.append({**setup_dict, "approved": False, "rejected": False,
                      "queued_at": datetime.now(timezone.utc).isoformat()})
        self._save(queue)
        logger.info("Setup queued for approval: %s %s @ %.4f",
                    setup_dict.get("symbol"), setup_dict.get("direction"), setup_dict.get("entry", 0))

    def pop_approved(self) -> list:
        queue = self._load()
        approved = [s for s in queue if s.get("approved")]
        remaining = [s for s in queue if not s.get("approved") and not s.get("rejected")]
        self._save(remaining)
        return approved

    def reject_all_stale(self, max_age_seconds: int = 300) -> int:
        queue = self._load()
        now = datetime.now(timezone.utc)
        count = 0
        fresh = []
        for s in queue:
            queued_at_str = s.get("queued_at", "")
            try:
                queued_at = datetime.fromisoformat(queued_at_str)
                age = (now - queued_at).total_seconds()
                if age > max_age_seconds:
                    count += 1
                    continue
            except Exception:
                pass
            fresh.append(s)
        self._save(fresh)
        return count

    def _load(self) -> list:
        if not os.path.exists(self.QUEUE_PATH):
            return []
        try:
            with open(self.QUEUE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, queue: list) -> None:
        with open(self.QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2)

    def pending_count(self) -> int:
        return len([s for s in self._load() if not s.get("approved") and not s.get("rejected")])


# ── Runner ─────────────────────────────────────────────────────────────────────

class MFFFlexRunner:
    """
    Main loop for MFF Flex $25K trading.

    Initialisation steps:
    1. Load or create state
    2. Build risk guard, payout guard, manual bridge, execution router
    3. Enter run loop based on mode
    """

    def __init__(
        self,
        mode: str = "PAPER",
        feed: Optional[FuturesDataFeed] = None,
        symbols: Optional[List[str]] = None,
        poll_interval: float = 60.0,
        timeframe: str = "1m",
        htf_timeframe: str = "4h",
        data_source: str = "csv",
        backtest_start: Optional[datetime] = None,
        backtest_end: Optional[datetime] = None,
    ):
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. LIVE_AUTO is disabled.")

        self._mode = mode
        self._symbols = symbols or SYMBOLS.phase1
        self._poll_interval = poll_interval
        self._timeframe = timeframe
        self._htf_timeframe = htf_timeframe
        self._data_source = data_source
        self._backtest_start = backtest_start
        self._backtest_end = backtest_end
        self._running = False

        # State (isolated from all other CB6 engines)
        self._state = MFFFlexState()
        self._state.set_mode(mode)

        # Feed
        self._feed = feed or PaperDataFeed("data/futures/historical")

        # Sub-systems
        self._risk_guard = MFFFlexRiskGuard(self._state)
        self._payout_guard = MFFFlexPayoutGuard(self._state)
        self._manual_bridge = MFFFlexManualBridge(self._state)
        self._connector = MFFFlexConnector(self._state)
        self._execution_router = FuturesExecutionRouter(mode=mode)
        self._approval_queue = SemiAutoApprovalQueue()
        self._rule_engine = MFFFlexRuleEngine()

        # Signal scanner (micros only in Phase 1)
        self._scanner = FuturesSignalScanner(
            feed=self._feed,
            symbols=self._symbols,
            min_score=55.0,
            scan_mode=mode,
        )

        logger.info(
            "MFFFlexRunner initialised: mode=%s symbols=%s source=%s timeframe=%s htf=%s",
            mode,
            self._symbols,
            self._data_source,
            self._timeframe,
            self._htf_timeframe,
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._mode == "OFF":
            logger.info("Runner mode is OFF — nothing to do")
            return
        if self._mode == "BACKTEST":
            self._run_backtest()
            return

        self._running = True
        logger.info("Starting MFF Flex runner in %s mode", self._mode)

        try:
            while self._running:
                self._tick()
                time.sleep(self._poll_interval)
        except KeyboardInterrupt:
            logger.info("Runner stopped by KeyboardInterrupt")
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._running = False

    def status(self) -> dict:
        return {
            "mode": self._mode,
            "running": self._running,
            "state": self._state.snapshot(),
            "risk": self._risk_guard.snapshot(),
            "payout": self._payout_guard.summary(),
            "manual_trades": self._manual_bridge.summary(),
            "approval_pending": self._approval_queue.pending_count(),
        }

    # ── Tick ───────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)

        # Reject stale approvals in SEMI_AUTO
        if self._mode == "SEMI_AUTO":
            stale = self._approval_queue.reject_all_stale(max_age_seconds=300)
            if stale:
                logger.info("Rejected %d stale approval(s)", stale)

        # Check if account is halted
        if self._state.halted:
            logger.warning("Account HALTED (%s) — skipping tick",
                           self._state.full_state().get("halt_reason"))
            return

        # Scan for setups
        if self._mode in ("PAPER", "SEMI_AUTO"):
            self._scan_and_act(now)

        # Monitor manual trades
        if self._mode == "MANUAL_MONITOR":
            self._monitor_manual(now)

    def _scan_and_act(self, now: datetime) -> None:
        results = self._scanner.scan_all(now)
        for result in results:
            if result.errors:
                logger.debug("Scan errors %s: %s", result.symbol, result.errors)
            for setup in result.setups:
                # Risk gate check
                allowed, reason = self._risk_guard.allow_trade(1, now)
                if not allowed:
                    logger.info("Risk gate blocked %s %s: %s",
                                setup.symbol, setup.direction, reason)
                    continue

                if self._mode == "SEMI_AUTO":
                    self._approval_queue.push({
                        "symbol": setup.symbol,
                        "direction": setup.direction,
                        "entry": setup.entry,
                        "stop_loss": setup.stop_loss,
                        "target_1": setup.target_1,
                        "target_2": setup.target_2,
                        "target_3": setup.target_3,
                        "score": setup.score,
                        "session": setup.session.value,
                        "htf_bias": setup.htf_bias.value,
                    })

                elif self._mode == "PAPER":
                    # Auto-execute on paper
                    from futures_engine.brokers.base_connector import OrderType, OrderRequest
                    from futures_engine.core.futures_symbol_registry import get_symbol
                    sym_info = get_symbol(setup.symbol)
                    order = OrderRequest(
                        symbol=setup.symbol,
                        contract="",
                        direction=setup.direction,
                        order_type=OrderType.LIMIT,
                        contracts=GUARDS_CONFIG.max_trade_contracts,
                        limit_price=setup.entry,
                        meta={"approved": True},
                    )
                    result_order = self._execution_router.submit(order, paper_fill_price=setup.entry)
                    logger.info(
                        "PAPER fill: %s %s @ %.4f | score=%.1f | SL=%.4f | T3=%.4f",
                        setup.symbol, setup.direction, setup.entry,
                        setup.score, setup.stop_loss, setup.target_3,
                    )

    def _monitor_manual(self, now: datetime) -> None:
        open_trades = self._manual_bridge.open_trades()
        if open_trades:
            logger.info("MANUAL_MONITOR: %d open manual trade(s)", len(open_trades))
        else:
            logger.debug("MANUAL_MONITOR: no open trades")

    # ── Backtest ───────────────────────────────────────────────────────────

    def _run_backtest(self) -> None:
        from datetime import timedelta
        start = self._backtest_start or (datetime.now(timezone.utc) - timedelta(days=365))
        end = self._backtest_end or datetime.now(timezone.utc)
        backtester = MFFFlexBacktester(
            feed=self._feed,
            symbols=self._symbols,
            start=start,
            end=end,
            timeframe=self._timeframe,
            htf_timeframe=self._htf_timeframe,
            data_source=self._data_source,
        )
        reports = backtester.run_all()
        for sym, rep in reports.items():
            logger.info(
                "Backtest %s: trades=%d WR=%.1f%% net=$%.2f maxDD=$%.2f",
                sym, rep.total_trades, rep.win_rate * 100,
                rep.net_profit, rep.max_drawdown
            )

    # ── Shutdown ───────────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        self._running = False
        self._risk_guard.end_of_day()
        logger.info("MFFFlexRunner shutdown complete. Final state: %s",
                    json.dumps(self._state.snapshot(), indent=2))
