"""
Regime Gate — CB6 Quantum Phase 3.5
Translates regime intelligence into concrete trade decision adjustments.

Rules (in priority order):
  CHOPPY                    → BLOCK all trades
  UNKNOWN (no archive data) → PASS (don't penalise missing data)
  RANGING + HIGH_VOL        → reduce lots 50%, require score +2
  RANGING                   → require score +1
  TRENDING against setup    → require score +1 (H4 gate already handles hard blocks)
  HIGH_VOL only             → switch risk_mode to "reduced"
  TRENDING aligned          → no change (standard rules apply)

Returns a RegimeDecision dataclass consumed by both NSE and Forex engines.
"""

from dataclasses import dataclass


@dataclass
class RegimeDecision:
    allowed:        bool    = True
    block_reason:   str     = ""
    score_boost:    int     = 0     # add to required score gate (+1 = harder to pass)
    lot_multiplier: float   = 1.0   # 0.5 = half lots, 1.0 = normal
    risk_mode:      str     = ""    # "reduced" | "normal" | "" (empty = no override)
    note:           str     = ""    # human-readable summary of what changed


def evaluate(
    regime:     str,        # TRENDING_UP | TRENDING_DOWN | RANGING | CHOPPY | UNKNOWN
    volatility: str,        # HIGH | NORMAL | LOW | UNKNOWN
    direction:  str = "",   # BULLISH | BEARISH — setup direction for counter-trend check
    h4_trend:   str = "",   # BULLISH | BEARISH | RANGING — from H4 EMA bias
) -> RegimeDecision:
    """
    Evaluate regime and return a RegimeDecision.
    direction and h4_trend are optional — used for counter-trend detection.
    """

    # ── 1. CHOPPY — hard block ───────────────────────────────────────────────
    if regime == "CHOPPY":
        return RegimeDecision(
            allowed=False,
            block_reason="CHOPPY regime — erratic price action, no edge",
            note="CHOPPY: blocked"
        )

    # ── 2. UNKNOWN — no archive data yet, pass silently ─────────────────────
    if regime == "UNKNOWN":
        return RegimeDecision(
            allowed=True,
            note="UNKNOWN regime (no archive data) — gate skipped"
        )

    # ── Accumulate adjustments ───────────────────────────────────────────────
    score_boost    = 0
    lot_multiplier = 1.0
    risk_mode      = ""
    notes          = []

    # ── 3. High volatility → reduce risk mode ───────────────────────────────
    if volatility == "HIGH":
        risk_mode = "reduced"
        notes.append("HIGH_VOL → risk_mode=reduced")

    # ── 4. RANGING regime ───────────────────────────────────────────────────
    if regime == "RANGING":
        score_boost += 1
        notes.append("RANGING → score gate +1")
        if volatility == "HIGH":
            score_boost    += 1
            lot_multiplier  = 0.5
            notes.append("RANGING+HIGH_VOL → score gate +1 more, lots ×0.5")

    # ── 5. Counter-trend setup (regime direction ≠ setup direction) ─────────
    elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
        trend_dir = "BULLISH" if regime == "TRENDING_UP" else "BEARISH"
        if direction and direction != trend_dir:
            score_boost += 1
            notes.append(f"counter-trend ({direction} vs {regime}) → score gate +1")

    note_str = " | ".join(notes) if notes else f"{regime} aligned — no adjustments"

    return RegimeDecision(
        allowed=True,
        score_boost=score_boost,
        lot_multiplier=lot_multiplier,
        risk_mode=risk_mode,
        note=note_str,
    )


def evaluate_from_setup(setup: dict) -> RegimeDecision:
    """
    Convenience wrapper: read regime fields already stored in the setup dict
    (written by forex_worker's regime_ctx injection or NSE's scanner).
    """
    regime    = setup.get("market_regime",    "UNKNOWN")
    volatility = setup.get("volatility_regime", "UNKNOWN")
    direction  = setup.get("direction",         "")
    return evaluate(regime, volatility, direction)
