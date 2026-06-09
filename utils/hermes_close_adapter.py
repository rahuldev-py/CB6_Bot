"""Fail-open observer adapter for Hermes closed-trade learning."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any


logger = logging.getLogger("cb6.hermes_close_adapter")


def is_trade_durably_closed(load_state, trade: Any) -> bool:
    """Best-effort readback confirmation for observer notification only."""
    try:
        if not isinstance(trade, Mapping):
            return False
        trade_id = _first(trade, "id", "trade_id", "ticket")
        if trade_id in (None, "", 0):
            return False
        durable_state = load_state()
        return any(
            str(closed.get("id", closed.get("trade_id", closed.get("ticket"))))
            == str(trade_id)
            for closed in durable_state.get("closed_trades", [])
            if isinstance(closed, Mapping)
        )
    except Exception:
        logger.exception("Hermes durable-close readback failed open")
        return False


def _first(trade: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = trade.get(key)
        if value not in (None, ""):
            return value
    return default


def _safe_float(value: Any) -> float:
    try:
        return float(value) if value not in (None, "", "nan") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value not in (None, "", "nan") else 0
    except (TypeError, ValueError):
        return 0


def notify_hermes_trade_closed(
    trade: Any,
    source: str,
    account: str | None = None,
    market: str | None = None,
) -> bool:
    """Observe a durable close without mutating state or affecting the caller."""
    try:
        if not isinstance(trade, Mapping):
            logger.warning("Hermes close observer skipped: trade is not a mapping")
            return False

        trade_id = _first(trade, "id", "trade_id", "ticket")
        symbol = _first(trade, "symbol")
        if trade_id in (None, "", 0) or symbol in (None, ""):
            logger.warning(
                "Hermes close observer skipped: missing trade ID or symbol "
                "(source=%s)",
                source,
            )
            return False

        targets_hit = _first(trade, "targets_hit", default=[])
        if isinstance(targets_hit, (list, tuple, set)):
            targets_copy = list(targets_hit)
        else:
            targets_copy = []

        normalized = {
            "id": str(trade_id),
            "symbol": str(symbol),
            "direction": str(_first(trade, "direction", "side", default="")),
            "entry_price": _safe_float(_first(trade, "entry_price", "entry")),
            "stop_loss": _safe_float(
                _first(trade, "stop_loss", "current_sl", "sl")
            ),
            "exit_price": _safe_float(_first(trade, "exit_price")),
            "pnl_usd": _safe_float(
                _first(trade, "pnl_usd", "pnl", "realized_pnl")
            ),
            "risk_usd": _safe_float(
                _first(trade, "risk_usd", "risk_amount")
            ),
            "entry_time": str(_first(trade, "entry_time", default="")),
            "exit_time": str(_first(trade, "exit_time", default="")),
            "exit_reason": str(
                _first(trade, "exit_reason", "status", default="")
            ),
            "confluence": _safe_int(
                _first(trade, "confluence", "score", default=0)
            ),
            "mss_type": str(_first(trade, "mss_type", default="")),
            "risk_mode": str(_first(trade, "risk_mode", default="normal")),
            "targets_hit": targets_copy,
        }

        from ml_engine.learning.feedback_loop import process_closed_trade

        process_closed_trade(
            trade=normalized,
            market=str(market or _first(trade, "market", default="")).lower(),
            account=str(account or _first(trade, "account", default="")),
            session=str(_first(trade, "session", default="")),
            h4_bias=str(_first(trade, "h4_bias", default="")),
            setup_type=str(
                _first(
                    trade,
                    "setup_type",
                    default="DOL_SWEEP_OB_BOS_FVG",
                )
            ),
            fvg_body_pct=_safe_float(
                _first(trade, "fvg_body_pct", default=0.0)
            ),
            sweep_age_ca=_safe_int(
                _first(trade, "sweep_age_ca", default=0)
            ),
            notes=f"source={source}",
        )
        logger.info(
            "Hermes close observer recorded trade=%s symbol=%s source=%s",
            trade_id,
            symbol,
            source,
        )
        return True
    except Exception:
        logger.exception("Hermes close observer failed open (source=%s)", source)
        return False
