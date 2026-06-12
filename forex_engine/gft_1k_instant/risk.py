from forex_engine.gft_1k_instant.config import GFT_1K_INSTANT_PROFILE


def daily_drawdown(state: dict) -> float:
    snapshot     = state.get("daily_snapshot", GFT_1K_INSTANT_PROFILE["account_size"])
    capital      = state.get("capital",        GFT_1K_INSTANT_PROFILE["account_size"])
    floating_pnl = state.get("floating_pnl",   0.0)
    return round(snapshot - (capital + floating_pnl), 2)


def max_drawdown(state: dict) -> float:
    start = GFT_1K_INSTANT_PROFILE["account_size"]
    capital = state.get("capital", start)
    return round(start - capital, 2)


def risk_mode(state: dict) -> tuple[str, str]:
    daily_loss = daily_drawdown(state)
    total_loss = max_drawdown(state)

    if daily_loss >= GFT_1K_INSTANT_PROFILE["daily_dd_limit"]:
        return "paused", (
            f"Daily DD hard stop ${daily_loss:.2f} >= "
            f"${GFT_1K_INSTANT_PROFILE['daily_dd_limit']:.2f}"
        )
    if total_loss >= GFT_1K_INSTANT_PROFILE["max_dd_limit"]:
        return "paused", (
            f"Max DD hard stop ${total_loss:.2f} >= "
            f"${GFT_1K_INSTANT_PROFILE['max_dd_limit']:.2f}"
        )
    if daily_loss >= GFT_1K_INSTANT_PROFILE["daily_dd_danger"]:
        return "reduced", (
            f"Daily DD danger ${daily_loss:.2f} >= "
            f"${GFT_1K_INSTANT_PROFILE['daily_dd_danger']:.2f}"
        )
    if total_loss >= GFT_1K_INSTANT_PROFILE["max_dd_danger"]:
        return "reduced", (
            f"Max DD danger ${total_loss:.2f} >= "
            f"${GFT_1K_INSTANT_PROFILE['max_dd_danger']:.2f}"
        )
    return "normal", "OK"


def validate_entry(setup: dict, lots: float, risk_usd: float, state: dict) -> tuple[bool, str]:
    if state.get("paused"):
        return False, "Engine paused"
    if len(state.get("open_trades", [])) >= GFT_1K_INSTANT_PROFILE["max_open_positions"]:
        return False, "One open trade only"

    signal = setup.get("entry_signal", {})
    if not signal.get("stop_loss"):
        return False, "No SL = block"
    if not (signal.get("target2") or signal.get("target1") or signal.get("target3")):
        return False, "No TP = block"
    if float(signal.get("rr_ratio") or 0) < GFT_1K_INSTANT_PROFILE["min_rr"]:
        return False, "RR below 1.5 = block"

    if lots > GFT_1K_INSTANT_PROFILE["max_lot"]:
        return False, (
            f"Lot {lots:.2f} exceeds max "
            f"{GFT_1K_INSTANT_PROFILE['max_lot']:.2f}"
        )
    if risk_usd > GFT_1K_INSTANT_PROFILE["max_risk_usd"]:
        return False, (
            f"Risk ${risk_usd:.2f} exceeds max "
            f"${GFT_1K_INSTANT_PROFILE['max_risk_usd']:.2f}"
        )

    mode, reason = risk_mode(state)
    if mode == "paused":
        return False, reason
    return True, "OK"

