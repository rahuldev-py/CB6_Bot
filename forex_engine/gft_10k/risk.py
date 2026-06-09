from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P


def daily_drawdown(state: dict) -> float:
    snapshot = state.get("daily_snapshot", _P["account_size"])
    capital  = state.get("capital",        _P["account_size"])
    return round(snapshot - capital, 2)


def max_drawdown(state: dict) -> float:
    capital = state.get("capital", _P["account_size"])
    return round(_P["account_size"] - capital, 2)


def risk_mode(state: dict) -> tuple:
    daily_loss = daily_drawdown(state)
    total_loss = max_drawdown(state)

    if daily_loss >= _P["daily_dd_limit"]:
        return "paused", f"Daily DD hard stop ${daily_loss:.2f} >= ${_P['daily_dd_limit']:.2f}"
    if total_loss >= _P["max_dd_limit"]:
        return "paused", f"Max DD hard stop ${total_loss:.2f} >= ${_P['max_dd_limit']:.2f}"
    if daily_loss >= _P["daily_dd_danger"]:
        return "reduced", f"Daily DD danger ${daily_loss:.2f} >= ${_P['daily_dd_danger']:.2f}"
    if total_loss >= _P["max_dd_danger"]:
        return "reduced", f"Max DD danger ${total_loss:.2f} >= ${_P['max_dd_danger']:.2f}"
    return "normal", "OK"


def validate_entry(setup: dict, lots: float, risk_usd: float, state: dict) -> tuple:
    if state.get("paused"):
        return False, "Engine paused"
    if len(state.get("open_trades", [])) >= _P["max_open_positions"]:
        return False, "One open trade only"

    signal = setup.get("entry_signal", {})
    if not signal.get("stop_loss"):
        return False, "No SL = block"
    if not (signal.get("target2") or signal.get("target1") or signal.get("target3")):
        return False, "No TP = block"
    if float(signal.get("rr_ratio") or 0) < _P["min_rr"]:
        return False, f"RR below {_P['min_rr']} = block"
    if lots > _P["max_lot"]:
        return False, f"Lot {lots:.2f} exceeds max {_P['max_lot']:.2f}"
    if risk_usd > _P["max_risk_usd"]:
        return False, f"Risk ${risk_usd:.2f} exceeds cap ${_P['max_risk_usd']:.2f}"

    mode, reason = risk_mode(state)
    if mode == "paused":
        return False, reason
    return True, "OK"
