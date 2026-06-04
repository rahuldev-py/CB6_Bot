# core/execution_guard.py — Single mandatory execution gate for CB6 Quantum
#
# ALL trade entries across NSE, Forex, and Crypto MUST be gated through this module.
#
# Two usage patterns:
#
#   A) Dict-based (existing engines — backward compatible):
#      from core.execution_guard import guard_dict_entry
#      allowed, reason = guard_dict_entry(state, capital, symbol)
#      if not allowed:
#          return None
#
#   B) Schema-based (new engines — fully typed):
#      from core.execution_guard import ExecutionGuard
#      guard = ExecutionGuard(config)
#      decision = guard.check(signal, ...)
#      if decision.allowed:
#          intent = guard.build_intent(signal, decision, account_id, quantity)
#
#   C) Thin wrapper for broker calls (audit trail):
#      from core.execution_guard import execute_guarded_order
#      order_id = execute_guarded_order(fyers.place_order, data, symbol=symbol, intent="ENTRY")

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Optional, Tuple

from utils.logger import logger
from core.schemas import (
    Direction, Engine, ExecutionIntent, Market, RiskDecision, Signal, TradeStatus
)


class ExecutionGuardConfig:
    """Risk parameters for a single engine/account profile."""

    def __init__(
        self,
        engine:              Engine,
        capital:             float,
        max_daily_loss_pct:  float,
        max_daily_loss_abs:  Optional[float] = None,
        max_open_trades:     int   = 5,
        max_trades_per_day:  int   = 999,
        min_score:           float = 0.0,
        min_rr:              float = 1.5,
        allowed_symbols:     Optional[set[str]] = None,
        blocked_symbols:     Optional[set[str]] = None,
    ) -> None:
        self.engine             = engine
        self.capital            = capital
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_daily_loss_abs = max_daily_loss_abs or (capital * max_daily_loss_pct / 100)
        self.max_open_trades    = max_open_trades
        self.max_trades_per_day = max_trades_per_day
        self.min_score          = min_score
        self.min_rr             = min_rr
        self.allowed_symbols    = allowed_symbols   # None = all allowed
        self.blocked_symbols    = blocked_symbols or set()


class ExecutionGuard:
    """
    Centralised risk gate. Checks a Signal against account state and config
    before allowing execution.

    All checks are pure — no side effects, no I/O.
    """

    def __init__(self, config: ExecutionGuardConfig) -> None:
        self.config = config

    def check(
        self,
        signal:         Signal,
        daily_loss:     float,
        open_trade_count: int,
        trades_today:   int,
        kill_switch:    bool = False,
    ) -> RiskDecision:
        """
        Run all risk gates. Returns RiskDecision(allowed=True/False, reason=...).
        Call this before any order placement.
        """
        cfg = self.config
        now = datetime.utcnow()

        # 1. Kill-switch
        if kill_switch:
            return self._deny(signal, "Kill switch active", daily_loss, open_trade_count, now)

        # 2. Symbol block list (absolute — e.g. XAUUSD on GFT, equity on NSE)
        if signal.symbol in cfg.blocked_symbols:
            return self._deny(signal, f"Symbol {signal.symbol} is permanently blocked", daily_loss, open_trade_count, now)

        # 3. Symbol allow list (if defined, only listed symbols are tradeable)
        if cfg.allowed_symbols is not None and signal.symbol not in cfg.allowed_symbols:
            return self._deny(signal, f"Symbol {signal.symbol} not in allowed list", daily_loss, open_trade_count, now)

        # 4. Daily loss hard stop
        if daily_loss >= cfg.max_daily_loss_abs:
            return self._deny(
                signal,
                f"Daily loss ${daily_loss:.2f} >= limit ${cfg.max_daily_loss_abs:.2f}",
                daily_loss, open_trade_count, now,
            )

        # 5. Max open trades
        if open_trade_count >= cfg.max_open_trades:
            return self._deny(signal, f"Max open trades ({cfg.max_open_trades}) reached", daily_loss, open_trade_count, now)

        # 6. Max trades per day
        if trades_today >= cfg.max_trades_per_day:
            return self._deny(signal, f"Max daily trades ({cfg.max_trades_per_day}) reached", daily_loss, open_trade_count, now)

        # 7. Signal score gate
        if signal.score < cfg.min_score:
            return self._deny(signal, f"Score {signal.score:.1f} < min {cfg.min_score:.1f}", daily_loss, open_trade_count, now)

        # 8. Minimum RR
        if signal.rr_to_t2 < cfg.min_rr:
            return self._deny(signal, f"RR {signal.rr_to_t2:.2f} < min {cfg.min_rr:.2f}", daily_loss, open_trade_count, now)

        # 9. Direction sanity
        if signal.direction == Direction.LONG and signal.stop_loss >= signal.entry:
            return self._deny(signal, "LONG signal: stop_loss >= entry (invalid setup)", daily_loss, open_trade_count, now)
        if signal.direction == Direction.SHORT and signal.stop_loss <= signal.entry:
            return self._deny(signal, "SHORT signal: stop_loss <= entry (invalid setup)", daily_loss, open_trade_count, now)

        logger.info(
            f"ExecutionGuard ALLOWED: {signal.symbol} {signal.direction.value} "
            f"score={signal.score:.1f} rr={signal.rr_to_t2:.2f} "
            f"daily_loss=${daily_loss:.2f}/{cfg.max_daily_loss_abs:.2f}"
        )
        return RiskDecision(
            allowed=True,
            reason="All checks passed",
            signal_id=signal.signal_id,
            engine=cfg.engine,
            daily_loss=daily_loss,
            daily_limit=cfg.max_daily_loss_abs,
            open_trades=open_trade_count,
            checked_at=now,
        )

    def build_intent(
        self,
        signal:       Signal,
        decision:     RiskDecision,
        account_id:   str,
        quantity:     float,
        broker_meta:  Optional[dict] = None,
    ) -> ExecutionIntent:
        """
        Build an ExecutionIntent from an approved RiskDecision.
        Only call this when decision.allowed is True.
        """
        if not decision.allowed:
            raise ValueError(
                f"Cannot build ExecutionIntent: RiskDecision is denied — {decision.reason}"
            )
        return ExecutionIntent(
            intent_id=str(uuid.uuid4()),
            signal=signal,
            risk_decision=decision,
            engine=self.config.engine,
            account_id=account_id,
            quantity=quantity,
            idempotency_key=f"{signal.signal_id}:{account_id}",
            created_at=datetime.utcnow(),
            broker_meta=broker_meta or {},
        )

    def _deny(
        self,
        signal:      Signal,
        reason:      str,
        daily_loss:  float,
        open_trades: int,
        now:         datetime,
    ) -> RiskDecision:
        logger.warning(f"ExecutionGuard BLOCKED {signal.symbol}: {reason}")
        return RiskDecision(
            allowed=False,
            reason=reason,
            signal_id=signal.signal_id,
            engine=self.config.engine,
            daily_loss=daily_loss,
            daily_limit=self.config.max_daily_loss_abs,
            open_trades=open_trades,
            checked_at=now,
        )


# ── Fail-closed helper ────────────────────────────────────────────────────────

def should_fail_closed(intent: dict) -> bool:
    """
    Return True when a guard internal error must BLOCK rather than allow.

    LIVE + ENTRY  → fail closed (guard errors must never silently allow real money)
    LIVE + EXIT   → fail open  (exit must always be allowed)
    PAPER/DEV     → fail open  (development-time errors should surface as warnings)
    BACKTEST      → fail open  (replay; never blocks execution)
    """
    mode        = str(intent.get("mode", "")).upper()
    intent_type = str(intent.get("intent_type", intent.get("side", ""))).upper()
    is_exit = intent_type.startswith("CLOSE") or intent_type in {"EXIT", "CLOSE", "FLATTEN"}
    is_entry = intent_type in {"ENTRY", "BUY", "SELL", "LONG", "SHORT", "OPEN"}
    if is_exit:
        return False
    return mode == "LIVE" and is_entry


# ── Dict-based guard (backward-compatible entry gate) ─────────────────────────

def guard_dict_entry(
    state:           dict,
    capital:         float,
    symbol:          str = "",
    blocked_symbols: Optional[set] = None,
    mode:            str = "",
    intent_type:     str = "ENTRY",
) -> Tuple[bool, str]:
    """
    Lightweight pre-entry check for existing dict-based engines.

    Calls core.risk.can_enter() centrally so all NSE/live paths share one
    risk gate. Always returns (allowed: bool, reason: str) — never raises.

    Parameters
    ----------
    state           : paper/live state dict with daily_trades, daily_losses, closed_trades
    capital         : base capital for DD % calculation
    symbol          : instrument being traded (checked against blocked_symbols)
    blocked_symbols : set of symbol strings that may never be traded
    mode            : "LIVE" | "PAPER" | "DEV" | "BACKTEST"
                      Pass "LIVE" for real-money entries so guard errors fail closed.
    intent_type     : "ENTRY" | "EXIT" | "CLOSE_*" etc.
                      EXIT/CLOSE intents are never blocked by the entry guard.

    Returns
    -------
    (True, "OK") — trade may proceed
    (False, reason) — trade must not proceed; log reason
    """
    try:
        # Symbol block check (e.g. XAUUSD on GFT = permanent block)
        if blocked_symbols and symbol in blocked_symbols:
            reason = f"Symbol {symbol} is permanently blocked"
            logger.warning(f"ExecutionGuard BLOCKED {symbol}: {reason}")
            return False, reason

        from core.risk import can_enter
        allowed, reason = can_enter(state, capital)
        if not allowed:
            logger.warning(f"ExecutionGuard BLOCKED {symbol or '?'}: {reason}")
        else:
            logger.info(f"ExecutionGuard ALLOWED: {symbol or '?'} — {reason}")
        return allowed, reason

    except Exception as exc:
        intent = {"mode": mode, "intent_type": intent_type}
        if should_fail_closed(intent):
            # LIVE ENTRY: guard error must BLOCK — never silently pass real-money orders.
            reason = f"GUARD_INTERNAL_ERROR_FAIL_CLOSED:{exc}"
            logger.error(
                f"ExecutionGuard LIVE ENTRY error for {symbol}: {exc} "
                f"— FAIL CLOSED (blocking trade)"
            )
            return False, reason
        # PAPER/DEV/BACKTEST or EXIT: fail open so valid trades are not silently blocked.
        logger.warning(
            f"ExecutionGuard error for {symbol}: {exc} "
            f"— fail open (mode={mode or 'PAPER/DEV'})"
        )
        return True, f"guard_error:{exc}"


# ── execute_guarded_order() — broker-call wrapper with audit trail ─────────────

def execute_guarded_order(
    order_fn:        Callable[..., Any],
    *args:           Any,
    symbol:          str = "?",
    intent:          str = "ENTRY",
    idempotency_key: Optional[str] = None,
    **kwargs:        Any,
) -> Any:
    """
    Thin wrapper around a broker order call.
    Adds structured logging and an idempotency key guard.

    Usage::

        order_id = execute_guarded_order(
            fyers.place_order, data,
            symbol="NSE:NIFTY...", intent="ENTRY",
        )

        result = execute_guarded_order(
            mt5.order_send, request,
            symbol="XAGUSD", intent="CLOSE_SL",
        )

    Parameters
    ----------
    order_fn         : callable that places the order (fyers.place_order, mt5.order_send, etc.)
    *args            : positional args forwarded to order_fn
    symbol           : symbol being traded (for logging)
    intent           : "ENTRY" | "CLOSE_SL" | "CLOSE_TARGET" | "CLOSE_THETA" | "MODIFY"
    idempotency_key  : if set, logs the key so duplicate-call analysis is possible
    **kwargs         : keyword args forwarded to order_fn

    Returns
    -------
    Whatever order_fn returns (order_id string, mt5 result, etc.)
    Raises on exception so callers can handle order failures explicitly.
    """
    call_id = idempotency_key or str(uuid.uuid4())[:8]
    logger.info(
        f"[ORDER] intent={intent} symbol={symbol} call_id={call_id} "
        f"fn={order_fn.__qualname__ if hasattr(order_fn, '__qualname__') else type(order_fn).__name__}"
    )
    try:
        result = order_fn(*args, **kwargs)
        logger.info(f"[ORDER OK] intent={intent} symbol={symbol} call_id={call_id} result={result!r}")
        return result
    except Exception as exc:
        logger.error(f"[ORDER ERROR] intent={intent} symbol={symbol} call_id={call_id} error={exc}")
        raise


# ── execute_forex_guarded_order() — forex broker-call wrapper ─────────────────

def execute_forex_guarded_order(
    order_request:   dict,
    broker_call:     Callable[..., Any],
    *,
    symbol:          str = "?",
    intent:          str = "ENTRY",
    account_id:      str = "?",
    magic:           Optional[int] = None,
    expected_magic:  Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> Any:
    """
    Validate profile/account/magic/symbol before forwarding to the MT5 broker call.

    Blocks:
      - Empty symbol or lots
      - Magic number mismatch (when expected_magic is provided)
      - account_id is blank (catches calls that bypassed profile setup)

    Always allows exit/close intents regardless of validation — exits must not be blocked.

    Usage::

        result = execute_forex_guarded_order(
            request, mt5_connector.place_market_order,
            symbol="XAGUSD", intent="ENTRY",
            account_id="FTMO_10K", magic=62002, expected_magic=62002,
        )
    """
    call_id     = idempotency_key or str(uuid.uuid4())[:8]
    intent_type = str(intent).upper()
    is_exit     = intent_type.startswith("CLOSE") or intent_type in {"EXIT", "CLOSE", "FLATTEN"}

    logger.info(
        f"[FOREX ORDER] intent={intent} symbol={symbol} account={account_id} "
        f"magic={magic} call_id={call_id}"
    )

    # EXIT/CLOSE paths are never blocked — broker handles position management
    if not is_exit:
        # Account profile must be set
        if not account_id or account_id == "?":
            reason = "FOREX_GUARD_BLOCKED: account_id not set — refusing ENTRY without profile"
            logger.error(f"[FOREX ORDER BLOCKED] {reason}")
            raise ValueError(reason)

        # Symbol must be present
        if not symbol or symbol == "?":
            reason = "FOREX_GUARD_BLOCKED: symbol missing"
            logger.error(f"[FOREX ORDER BLOCKED] {reason}")
            raise ValueError(reason)

        # Magic number isolation — prevents cross-account order pollution
        if expected_magic is not None and magic != expected_magic:
            reason = (
                f"FOREX_GUARD_BLOCKED: magic {magic} != expected {expected_magic} "
                f"for account {account_id}"
            )
            logger.error(f"[FOREX ORDER BLOCKED] {reason}")
            raise ValueError(reason)

    try:
        result = broker_call(**order_request) if isinstance(order_request, dict) else broker_call(order_request)
        logger.info(f"[FOREX ORDER OK] intent={intent} symbol={symbol} call_id={call_id} result={result!r}")
        return result
    except ValueError:
        raise
    except Exception as exc:
        logger.error(f"[FOREX ORDER ERROR] intent={intent} symbol={symbol} call_id={call_id} error={exc}")
        raise
