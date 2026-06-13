# forex_engine/risk/correlation_tracker.py
#
# CB6 Quantum — Correlated Exposure Tracker
#
# XAUUSD and XAGUSD are highly correlated (~0.85). Holding both simultaneously
# doubles directional risk. This module detects correlated exposure and returns
# a warning/block signal.
#
# Rules enforced:
#   - Max 1 correlated pair open at the same time per account (XAUUSD + XAGUSD).
#   - If both are open in the SAME direction → flag as HIGH_CORRELATED.
#   - Opposite directions (hedge) → flag as HEDGED (lower risk, still warn).
#
# Shadow/context only — caller decides whether to block. All accounts use same logic.

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Correlated groups: any two symbols in the same group are considered correlated
_CORRELATED_GROUPS: List[List[str]] = [
    ['XAUUSD', 'XAGUSD'],   # Gold + Silver — tight positive correlation
]

# Within this group, USOIL has lower correlation to Gold/Silver
_MODERATE_GROUPS: List[List[str]] = [
    ['XAUUSD', 'USOIL'],
    ['XAGUSD', 'USOIL'],
]


def get_correlated_exposure(state: dict, account_id: str = '') -> Dict:
    """
    Analyse open trades for correlated position risk.

    Args:
        state      : account state dict with 'open_trades' list
        account_id : label for logging context (e.g. 'GFT_5K')

    Returns dict:
        has_correlated      — bool, True if any highly-correlated pair is open
        correlation_level   — 'NONE' | 'MODERATE' | 'HIGH_SAME_DIR' | 'HEDGED'
        correlated_pairs    — list of (sym_a, sym_b, direction_a, direction_b)
        total_corr_risk_usd — sum of risk_usd across correlated positions
        context             — human-readable summary
    """
    open_trades: List[dict] = state.get('open_trades', [])

    if not open_trades:
        return _clean_result()

    # Build quick lookup: symbol → list of (direction, risk_usd)
    positions: Dict[str, List[Tuple[str, float]]] = {}
    for t in open_trades:
        sym = str(t.get('symbol', '')).upper()
        direction = str(t.get('direction', t.get('side', ''))).upper()
        risk_usd = float(t.get('risk_usd', 0))
        positions.setdefault(sym, []).append((direction, risk_usd))

    correlated_pairs = []
    total_corr_risk = 0.0
    correlation_level = 'NONE'

    for group in _CORRELATED_GROUPS:
        held = [s for s in group if s in positions]
        if len(held) < 2:
            continue
        for i in range(len(held)):
            for j in range(i + 1, len(held)):
                sym_a, sym_b = held[i], held[j]
                for dir_a, risk_a in positions[sym_a]:
                    for dir_b, risk_b in positions[sym_b]:
                        correlated_pairs.append((sym_a, sym_b, dir_a, dir_b))
                        total_corr_risk += risk_a + risk_b
                        if dir_a == dir_b:
                            correlation_level = 'HIGH_SAME_DIR'
                        elif correlation_level != 'HIGH_SAME_DIR':
                            correlation_level = 'HEDGED'

    if not correlated_pairs:
        # Check moderate groups
        for group in _MODERATE_GROUPS:
            held = [s for s in group if s in positions]
            if len(held) >= 2:
                correlation_level = 'MODERATE'
                break

    has_correlated = bool(correlated_pairs)

    ctx_parts = []
    if correlated_pairs:
        for sym_a, sym_b, dir_a, dir_b in correlated_pairs:
            ctx_parts.append(f"{sym_a}({dir_a})+{sym_b}({dir_b})")
        ctx = (
            f"[{account_id}] Correlated exposure: "
            f"{', '.join(ctx_parts)} → {correlation_level} "
            f"total_risk=${total_corr_risk:.2f}"
        )
    else:
        ctx = f"[{account_id}] No correlated pairs open ({correlation_level})"

    return {
        'has_correlated'     : has_correlated,
        'correlation_level'  : correlation_level,
        'correlated_pairs'   : correlated_pairs,
        'total_corr_risk_usd': round(total_corr_risk, 2),
        'context'            : ctx,
    }


def check_new_trade_correlation(
    state: dict,
    new_symbol: str,
    new_direction: str,
    account_id: str = '',
) -> Tuple[bool, str]:
    """
    Check whether opening `new_symbol` in `new_direction` would create high
    correlated exposure with existing open positions.

    Returns (allowed: bool, reason: str).
    - Returns True even if correlated — caller decides the block/warn policy.
    - This is an advisory check, not a hard gate.
    """
    open_trades: List[dict] = state.get('open_trades', [])
    new_sym = new_symbol.upper()
    new_dir = new_direction.upper()

    for group in _CORRELATED_GROUPS:
        if new_sym not in group:
            continue
        peers = [s for s in group if s != new_sym]
        for t in open_trades:
            sym = str(t.get('symbol', '')).upper()
            if sym in peers:
                existing_dir = str(t.get('direction', t.get('side', ''))).upper()
                if existing_dir == new_dir:
                    return (
                        False,
                        f"HIGH_CORRELATED: {new_sym}({new_dir}) would pair with "
                        f"{sym}({existing_dir}) already open [{account_id}]",
                    )
                return (
                    True,
                    f"HEDGED: {new_sym}({new_dir}) pairs with {sym}({existing_dir}) "
                    f"[opposite dirs, lower risk — allowed] [{account_id}]",
                )

    return True, 'OK — no correlated positions open'


def _clean_result() -> Dict:
    return {
        'has_correlated'     : False,
        'correlation_level'  : 'NONE',
        'correlated_pairs'   : [],
        'total_corr_risk_usd': 0.0,
        'context'            : 'No open trades',
    }
