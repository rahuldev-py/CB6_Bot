from __future__ import annotations

import time
from typing import Any

from utils.logger import logger
from nse_options.expiry_risk_engine import evaluate_expiry_risk
from nse_options.option_cache import load_option_config
from nse_options.option_chain_fetcher import fetch_option_chain_context
from nse_options.option_pressure_engine import calculate_option_pressure


def neutral_options_context(reason: str = "disabled") -> dict[str, Any]:
    return {
        "option_data_available": False,
        "option_bias": "NEUTRAL",
        "option_score_delta": 0,
        "risk_multiplier": 1.0,
        "expiry_risk": "UNKNOWN",
        "source_latency_ms": 0,
        "reason": reason,
    }


def enrich_setup_with_options_context(setup: dict[str, Any]) -> dict[str, Any]:
    config = load_option_config()
    if not config.get("enabled", False) or not config.get("use_for_trade_confirmation", True):
        setup["options_context"] = neutral_options_context("disabled")
        return setup

    symbol = _underlying(setup.get("symbol", ""))
    if symbol not in set(config.get("symbols", [])):
        setup["options_context"] = neutral_options_context("symbol_not_enabled")
        return setup

    option_info = setup.get("option_info") or {}
    expiry = option_info.get("expiry")
    if not expiry:
        setup["options_context"] = neutral_options_context("missing_expiry")
        return setup

    started = time.perf_counter()
    try:
        chain_ctx = fetch_option_chain_context(
            symbol=symbol,
            expiry=expiry,
            strikes_around_atm=int(config.get("num_strikes_from_atm", 5)),
        )
        pressure = calculate_option_pressure(chain_ctx) if chain_ctx.get("data_available") else {}
        expiry_risk = evaluate_expiry_risk(expiry, chain_ctx)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        option_bias = pressure.get("option_bias", "NEUTRAL")
        direction = setup.get("direction")
        score_delta = 0
        risk_multiplier = 1.0
        if direction == "BULLISH" and option_bias == "BULLISH":
            score_delta = 1 if pressure.get("pressure_score", 0) == 1 else 2
        elif direction == "BEARISH" and option_bias == "BEARISH":
            score_delta = 1 if abs(pressure.get("pressure_score", 0)) == 1 else 2
        elif option_bias in ("BULLISH", "BEARISH"):
            risk_multiplier = 0.5
            if config.get("allow_strong_contradiction_block", False):
                setup["options_context_block"] = True

        if expiry_risk.get("expiry_risk") == "HIGH":
            risk_multiplier = min(risk_multiplier, 0.5)

        context = {
            "option_data_available": bool(chain_ctx.get("data_available")),
            "atm_strike": chain_ctx.get("atm"),
            "expiry": expiry,
            "future_price": chain_ctx.get("future_price"),
            "option_bias": option_bias,
            "ce_pressure_score": pressure.get("ce_oi"),
            "pe_pressure_score": pressure.get("pe_oi"),
            "pressure": pressure,
            "option_score_delta": score_delta,
            "risk_multiplier": risk_multiplier,
            "expiry_risk": expiry_risk.get("expiry_risk"),
            "expiry_warning": expiry_risk.get("warning"),
            "source_latency_ms": latency_ms,
            "source": "sensibull",
        }
        setup["options_context"] = context
        setup["confluence"] = int(setup.get("confluence", 0)) + score_delta
        return setup
    except Exception as exc:
        logger.warning(f"Options context unavailable for {symbol}: {exc}")
        setup["options_context"] = neutral_options_context(str(exc))
        return setup


def _underlying(symbol: str) -> str:
    return str(symbol).upper().replace("NSE:", "").replace("-INDEX", "").replace("-EQ", "")
