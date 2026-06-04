from __future__ import annotations

from typing import Any

from utils.logger import logger


def maybe_alert_options_context(setup: dict[str, Any]) -> None:
    """Send only meaningful options intelligence alerts."""
    ctx = setup.get("options_context") or {}
    if not ctx.get("option_data_available"):
        return
    option_bias = ctx.get("option_bias")
    direction = setup.get("direction")
    aligned = (
        direction == "BULLISH" and option_bias == "BULLISH"
        or direction == "BEARISH" and option_bias == "BEARISH"
    )
    if not aligned and option_bias in ("BULLISH", "BEARISH"):
        try:
            from utils.telegram_alerts import send_message
            send_message(
                "CB6 OPTIONS CONTEXT CONTRADICTION\n\n"
                f"Setup: {setup.get('symbol')} {direction}\n"
                f"Options bias: {option_bias}\n"
                f"Risk multiplier: {ctx.get('risk_multiplier', 1.0)}"
            )
        except Exception as exc:
            logger.debug(f"Options alert failed: {exc}")
