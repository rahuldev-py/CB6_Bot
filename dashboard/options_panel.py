from __future__ import annotations

from typing import Any


def render_options_panel(context: dict[str, Any] | None) -> dict[str, Any]:
    """Return a dashboard-friendly options context payload."""
    ctx = context or {}
    return {
        "source": ctx.get("source", "sensibull"),
        "available": bool(ctx.get("option_data_available")),
        "atm_strike": ctx.get("atm_strike"),
        "future_price": ctx.get("future_price"),
        "expiry": ctx.get("expiry"),
        "option_bias": ctx.get("option_bias", "NEUTRAL"),
        "expiry_risk": ctx.get("expiry_risk", "UNKNOWN"),
        "latency_ms": ctx.get("source_latency_ms"),
    }
