"""
utils/ml_capital_allocator.py — ML-driven dynamic capital allocation for NSE.

Replaces fixed lot sizing with a signal-quality-weighted capital allocation
that scales exposure up on high-confidence setups and down (or to zero) on
weak signals, adverse conditions, or risk-budget exhaustion.

Hard limits that ML CANNOT override:
  - emergency_stop flag
  - daily_halt flag
  - stale data block
  - duplicate protection (caller's responsibility)
  - missing SL / zero SL distance
  - missing TP
  - max daily loss (Rs 1,000 hard cap)
  - broker rejection
  - risk_amount > MAX_RISK_PER_TRADE (₹500) — hard ceiling enforced twice
  - allocation_pct > MAX_CAPITAL_PCT (50%) — hard ceiling enforced twice
  - available_capital < min_margin_per_lot — block rather than trade with no margin

Entry point for callers:
    from utils.ml_capital_allocator import safe_calculate_alloc
    result = safe_calculate_alloc(signal, memory, account_state,
                                  paper_mode=False,
                                  min_margin_per_lot=0)
    if result['blocked']:
        return  # do not place trade
    risk_amount = result['risk_amount']
    capital_to_use = result['capital_to_use']
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# ── Configuration constants ─────────────────────────────────────────────────

BOT_CAPITAL         : float = float(os.getenv("CAPITAL", 20_000))   # Rs 20,000 bot capital

# Allocation bands (% of available bot capital)
ALLOC_WEAK          : float = 0.10   # score < 10 or low confidence
ALLOC_NORMAL        : float = 0.25   # score 10-12, normal ML confidence
ALLOC_STRONG        : float = 0.40   # score 13-14 or HIGH ML confidence
ALLOC_APLUS         : float = 0.50   # score ≥ 15 or ML VERY_HIGH + A+ memory match

# Hard limits (Rs) — enforced both inside _compute and in safe_calculate_alloc
MAX_RISK_PER_TRADE  : float = 500.0  # Rs — absolute risk ceiling per trade
MAX_CAPITAL_PCT     : float = 0.50   # never deploy > 50% of free capital
MAX_DAILY_LOSS      : float = 1_000.0
DAILY_CAUTION_LOSS  : float = 700.0  # reduce risk when daily loss > this

# Spread gate
MAX_SPREAD_PCT      : float = 1.0    # block if option spread > 1% of bid

# ML confidence thresholds
ML_BLOCK_CONFIDENCE : str   = "AVOID"       # explicit ML block
ML_MIN_WIN_PROB     : float = 0.40          # win_prob < 40% → block

# Consecutive loss management
CONSEC_LOSS_REDUCE  : int   = 2     # 2 losses in a row → halve allocation
CONSEC_LOSS_BLOCK   : int   = 3     # 3 losses in a row → no new trade (optional)


# ── Result structure ────────────────────────────────────────────────────────

def _result(
    alloc_pct    : float,
    capital      : float,
    available    : float,
    risk_amount  : float,
    reason       : str,
    confidence   : float,
    blocked      : bool,
    block_reason : str = "",
) -> Dict[str, Any]:
    # Belt-and-suspenders: clamp before returning regardless of how we got here
    alloc_pct   = min(alloc_pct,  MAX_CAPITAL_PCT)
    risk_amount = min(risk_amount, MAX_RISK_PER_TRADE)
    return {
        "allocation_pct"   : round(alloc_pct * 100, 1),   # percent
        "capital_to_use"   : round(capital, 2),             # Rs
        "risk_amount"      : round(risk_amount, 2),         # Rs
        "available_capital": round(available, 2),            # Rs
        "reason"           : reason,
        "confidence"       : round(confidence, 3),
        "blocked"          : blocked,
        "block_reason"     : block_reason,
    }


def _blocked(reason: str) -> Dict[str, Any]:
    return _result(0, 0, 0, 0, reason, 0.0, True, reason)


# ── Core calculation ─────────────────────────────────────────────────────────

def calculate_ml_capital_allocation(
    signal            : Dict[str, Any],
    memory            : Optional[Dict[str, Any]]   = None,
    account_state     : Optional[Dict[str, Any]]   = None,
    data_health       : Optional[Any]              = None,
    min_margin_per_lot: float                       = 0.0,
) -> Dict[str, Any]:
    """
    Compute dynamic capital allocation for one NSE ICT Silver Bullet trade.

    Parameters
    ----------
    signal : dict
        ICT scanner output. Expected keys:
          confluence (int), direction (str), symbol (str),
          in_fvg (bool), mss_type (str), ltp (float),
          entry_signal.stop_loss (float), entry_signal.entry (float),
          option_spread_pct (float, optional),
          option_volume (int, optional),
          ml_prediction (dict, optional):
            { win_prob: float, confidence: str, r_hat: float }

    memory : dict, optional
        Pattern library / ML memory output. Expected keys:
          should_trade (bool), score_gate (int), win_rate (float),
          similarity (float), decision (str)

    account_state : dict, optional
        paper_state.json dict. Expected keys:
          capital (float), available_capital (float),
          daily_pnl (float), daily_losses (int),
          closed_trades (list), daily_halted (bool), paused (bool)

    data_health : DataHealthMonitor, optional
        From data.data_health.get_monitor(). Used for feed-quality gating.

    min_margin_per_lot : float
        Minimum margin required for one lot of this instrument (INR).
        If > 0 and available_capital < min_margin_per_lot → blocked.

    Returns
    -------
    dict with keys:
      allocation_pct, capital_to_use, risk_amount, available_capital,
      reason, confidence, blocked, block_reason
    """
    signal        = signal or {}
    memory        = memory or {}
    account_state = account_state or {}

    # ── 1. HARD BLOCKS — ML cannot override ────────────────────────────────

    # 1a. Emergency stop
    try:
        from utils.emergency_stop import is_emergency_stop_active
        if is_emergency_stop_active():
            return _blocked("EMERGENCY_STOP active")
    except Exception:
        pass

    # 1b. Daily halt
    if account_state.get("daily_halted"):
        return _blocked(f"Daily halt active ({account_state.get('daily_halt_reason','cap hit')})")

    if account_state.get("paused"):
        return _blocked("Bot paused")

    # 1c. Daily loss hard cap
    available_capital = float(account_state.get("available_capital", BOT_CAPITAL))
    capital_base      = float(account_state.get("capital", BOT_CAPITAL))
    daily_pnl         = float(account_state.get("daily_pnl", 0))

    if daily_pnl <= -MAX_DAILY_LOSS:
        return _blocked(f"Daily loss cap hit (Rs {abs(daily_pnl):.0f} >= Rs {MAX_DAILY_LOSS:.0f})")

    # 1d. Minimum margin feasibility — must come before SL check
    if min_margin_per_lot > 0 and available_capital < min_margin_per_lot:
        return _blocked(
            f"Insufficient capital for 1 lot: "
            f"have Rs{available_capital:.0f} need Rs{min_margin_per_lot:.0f}"
        )

    # 1e. Missing or zero SL — block, cannot size without risk distance
    entry_sig = signal.get("entry_signal", {})
    sl        = float(entry_sig.get("stop_loss", 0) or signal.get("stop_loss", 0) or 0)
    entry_px  = float(entry_sig.get("entry", 0) or signal.get("ltp", 0) or 0)
    if sl <= 0 or entry_px <= 0:
        return _blocked("SL or entry price missing — cannot size position")

    risk_per_unit = abs(entry_px - sl)
    if risk_per_unit <= 0:
        return _blocked("sl_pts=0 — SL equals entry, cannot size position")

    # 1f. Missing TP
    t1 = float(entry_sig.get("target1", 0) or 0)
    t2 = float(entry_sig.get("target2", 0) or 0)
    if t1 <= 0 and t2 <= 0:
        return _blocked("No TP defined — cannot enter without exit plan")

    # 1g. Stale data gate
    if data_health is not None:
        try:
            td_symbols = [signal.get("td_symbol", "NIFTY-I")]
            safe, reason = data_health.is_trading_safe(truedata_symbols=td_symbols)
            if not safe:
                return _blocked(f"Data stale: {reason}")
        except Exception:
            pass

    # 1h. Spread gate
    spread_pct = float(signal.get("option_spread_pct", 0) or 0)
    if spread_pct > MAX_SPREAD_PCT:
        return _blocked(f"Option spread {spread_pct:.2f}% > max {MAX_SPREAD_PCT:.1f}%")

    # ── 2. ML/MEMORY BLOCKS ─────────────────────────────────────────────────

    ml_pred  = signal.get("ml_prediction", {}) or {}
    win_prob = float(ml_pred.get("win_prob", 0.5))
    ml_conf  = str(ml_pred.get("confidence", "MEDIUM")).upper()
    ml_r_hat = float(ml_pred.get("r_hat", 0))

    if ml_conf == ML_BLOCK_CONFIDENCE:
        return _blocked(f"ML confidence=AVOID (win_prob={win_prob:.0%})")

    if win_prob < ML_MIN_WIN_PROB:
        return _blocked(f"ML win_prob {win_prob:.0%} < threshold {ML_MIN_WIN_PROB:.0%}")

    # Memory/pattern library gate
    mem_should_trade = memory.get("should_trade", True)
    mem_decision     = memory.get("decision", "")
    if mem_should_trade is False:
        return _blocked(f"Pattern library blocked: {mem_decision[:80]}")

    # ── 3. BASE ALLOCATION BY SIGNAL STRENGTH ───────────────────────────────

    score    = int(signal.get("confluence", 0) or 0)
    in_fvg   = bool(signal.get("in_fvg", False))
    mss_type = str(signal.get("mss_type", "BOS")).upper()
    mem_wr   = float(memory.get("win_rate", 0.5))
    mem_sim  = float(memory.get("similarity", 0.0))

    if score >= 15 or (ml_conf in ("HIGH", "VERY_HIGH") and score >= 13):
        base_alloc = ALLOC_APLUS
        band = "A+"
    elif score >= 13:
        base_alloc = ALLOC_STRONG
        band = "STRONG"
    elif score >= 10 and in_fvg:
        base_alloc = ALLOC_NORMAL
        band = "NORMAL"
    else:
        base_alloc = ALLOC_WEAK
        band = "WEAK"

    # ── 4. MODIFIERS ────────────────────────────────────────────────────────

    modifier = 1.0
    reasons  = [f"band={band}(score={score})"]

    # 4a. CHoCH bonus (higher conviction than BOS)
    if mss_type == "CHOCH":
        modifier *= 1.10
        reasons.append("CHoCH+10%")

    # 4b. In FVG bonus
    if in_fvg:
        modifier *= 1.05
        reasons.append("inFVG+5%")

    # 4c. ML win_prob bonus/penalty
    if win_prob >= 0.65:
        modifier *= 1.15
        reasons.append(f"MLprob{win_prob:.0%}+15%")
    elif win_prob >= 0.55:
        modifier *= 1.05
        reasons.append(f"MLprob{win_prob:.0%}+5%")
    elif win_prob < 0.45:
        modifier *= 0.80
        reasons.append(f"MLprob{win_prob:.0%}-20%")

    # 4d. Memory similarity bonus
    if mem_sim >= 0.70:
        modifier *= 1.10
        reasons.append(f"memsim{mem_sim:.0%}+10%")
    elif mem_sim >= 0.55:
        modifier *= 1.05
        reasons.append(f"memsim{mem_sim:.0%}+5%")

    # 4e. Memory win rate bonus
    if mem_wr >= 0.65:
        modifier *= 1.10
        reasons.append(f"memWR{mem_wr:.0%}+10%")
    elif mem_wr < 0.40:
        modifier *= 0.80
        reasons.append(f"memWR{mem_wr:.0%}-20%")

    # 4f. Consecutive loss reduction
    closed = account_state.get("closed_trades", [])
    consec = 0
    for t in reversed(closed):
        if (t.get("pnl", 0) or t.get("realized_pnl", 0)) < 0:
            consec += 1
        else:
            break

    if consec >= CONSEC_LOSS_BLOCK:
        modifier *= 0.50
        reasons.append(f"consec_loss={consec}→-50%")
    elif consec >= CONSEC_LOSS_REDUCE:
        modifier *= 0.60
        reasons.append(f"consec_loss={consec}→-40%")

    # 4g. Daily PnL caution zone
    if daily_pnl <= -DAILY_CAUTION_LOSS:
        modifier *= 0.50
        reasons.append(f"daily_pnl={daily_pnl:.0f}→-50%")
    elif daily_pnl <= -(DAILY_CAUTION_LOSS * 0.5):
        modifier *= 0.75
        reasons.append(f"daily_pnl={daily_pnl:.0f}→-25%")

    # 4h. TrueData health penalty (not block, just reduce)
    if data_health is not None:
        try:
            if not data_health.is_healthy():
                modifier *= 0.70
                reasons.append("TrueData_unhealthy→-30%")
        except Exception:
            pass

    # 4i. Option liquidity bonus/penalty
    opt_vol = int(signal.get("option_volume", 0) or 0)
    if opt_vol > 500_000:
        modifier *= 1.05
        reasons.append("liq_high+5%")
    elif opt_vol > 0 and opt_vol < 50_000:
        modifier *= 0.80
        reasons.append("liq_low-20%")

    # 4j. ML R-hat bonus
    if ml_r_hat >= 2.0:
        modifier *= 1.10
        reasons.append(f"Rhat={ml_r_hat:.1f}+10%")
    elif ml_r_hat < 0:
        modifier *= 0.85
        reasons.append(f"Rhat={ml_r_hat:.1f}-15%")

    # ── 5. COMPUTE ALLOCATION ────────────────────────────────────────────────

    raw_alloc = base_alloc * modifier

    # First clamp: band ceiling
    raw_alloc = max(ALLOC_WEAK, min(raw_alloc, ALLOC_APLUS))

    # Capital
    capital_to_use = round(
        min(available_capital * raw_alloc, available_capital * MAX_CAPITAL_PCT), 2
    )

    # Quantity-derived risk
    qty_estimate = int(capital_to_use / max(entry_px, 1))
    raw_risk     = round(risk_per_unit * qty_estimate, 2)

    # Risk cap: scale capital_to_use down to fit within MAX_RISK_PER_TRADE
    if raw_risk > MAX_RISK_PER_TRADE:
        scale          = MAX_RISK_PER_TRADE / max(raw_risk, 0.01)
        capital_to_use = round(capital_to_use * scale, 2)
        qty_estimate   = int(capital_to_use / max(entry_px, 1))
        raw_risk       = round(risk_per_unit * qty_estimate, 2)
        reasons.append(f"risk_cap→Rs{MAX_RISK_PER_TRADE:.0f}")

    # Belt-and-suspenders: final hard ceiling regardless of math above
    if raw_risk > MAX_RISK_PER_TRADE:
        raw_risk = MAX_RISK_PER_TRADE

    # Final allocation % — clamp to MAX_CAPITAL_PCT as a hard ceiling
    final_alloc = min(
        capital_to_use / max(available_capital, 1),
        MAX_CAPITAL_PCT,
    )

    reason_str = " | ".join(reasons)
    confidence = min(win_prob * (score / 15.0) * (1 + mem_sim * 0.3), 1.0)

    return _result(
        alloc_pct   = final_alloc,
        capital     = capital_to_use,
        available   = available_capital,
        risk_amount = raw_risk,
        reason      = reason_str,
        confidence  = confidence,
        blocked     = False,
    )


# ── Safe wrapper (the intended public API) ───────────────────────────────────

def safe_calculate_alloc(
    signal            : Dict[str, Any],
    memory            : Optional[Dict[str, Any]] = None,
    account_state     : Optional[Dict[str, Any]] = None,
    paper_mode        : bool                      = False,
    min_margin_per_lot: float                     = 0.0,
    data_health       : Optional[Any]            = None,
) -> Dict[str, Any]:
    """
    Exception-safe wrapper around calculate_ml_capital_allocation.

    Fail-safety contract:
      - paper_mode=False (live):  any exception → _blocked("ALLOC_EXCEPTION_FAIL_CLOSED")
      - paper_mode=True  (paper): any exception → 1-lot pass result for testing continuity

    All clamp guarantees (risk ≤ ₹500, alloc ≤ 50%) are re-enforced on the
    returned result even if calculate_ml_capital_allocation had a logic error.
    """
    try:
        result = calculate_ml_capital_allocation(
            signal             = signal,
            memory             = memory,
            account_state      = account_state,
            data_health        = data_health,
            min_margin_per_lot = min_margin_per_lot,
        )
    except Exception as exc:
        if paper_mode:
            return {
                "allocation_pct"   : round(ALLOC_WEAK * 100, 1),
                "capital_to_use"   : 0.0,
                "risk_amount"      : 0.0,
                "available_capital": 0.0,
                "reason"           : f"ALLOC_EXCEPTION_PAPER_FALLBACK: {exc}",
                "confidence"       : 0.0,
                "blocked"          : False,
                "block_reason"     : "",
            }
        return _blocked(f"ALLOC_EXCEPTION_FAIL_CLOSED: {exc}")

    # Re-enforce clamps on whatever calculate_ml_capital_allocation returned
    if not result.get("blocked"):
        result["risk_amount"]    = min(float(result.get("risk_amount", 0)),    MAX_RISK_PER_TRADE)
        result["allocation_pct"] = min(float(result.get("allocation_pct", 0)), MAX_CAPITAL_PCT * 100)

    return result
