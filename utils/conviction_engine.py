"""
Conviction Engine — CB6 Quantum Phase 7
Aggregates all intelligence layers into a single deployment decision.

Converts fragmented signal quality checks into one conviction score (0-100)
that drives risk sizing. The environment determines edge; pattern validates it.

Usage:
    from utils.conviction_engine import ConvictionEngine, ConvictionResult
    ce = ConvictionEngine()
    result = ce.evaluate(
        market="FOREX",
        symbol="XAGUSD",
        direction="BULLISH",
        setup=setup_dict,        # from signal_scanner
        session="london",
    )
    print(result.conviction_grade, result.recommended_risk_multiplier)
    python -m utils.conviction_engine   # CLI demo
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import logger


# ---------------------------------------------------------------------------
# Component weights (must sum to 100)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "technical":   25,   # ICT confluence + A+ similarity
    "regime":      25,   # Market regime at 4H
    "session":     15,   # Kill zone / trading window quality
    "correlation": 10,   # Cross-asset alignment
    "oi_flow":     10,   # OI/PCR alignment (NSE) or neutral (FOREX)
    "macro":       10,   # Knowledge graph macro alignment
    "sector":       5,   # Sector momentum (NSE) or neutral (FOREX)
}

assert sum(WEIGHTS.values()) == 100, "Weights must sum to 100"


# ---------------------------------------------------------------------------
# Risk multiplier lookup
# ---------------------------------------------------------------------------

def _risk_multiplier(score: float) -> float:
    if score >= 85:  return 1.5
    if score >= 70:  return 1.0
    if score >= 55:  return 0.75
    if score >= 40:  return 0.5
    return 0.0   # skip


def _grade(score: float) -> str:
    if score >= 85:  return "A+"
    if score >= 70:  return "A"
    if score >= 55:  return "B"
    if score >= 40:  return "C"
    return "D"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConvictionResult:
    conviction_score:             float        # 0 – 100
    conviction_grade:             str          # A+ | A | B | C | D
    recommended_risk_multiplier:  float        # 0.0 | 0.5 | 0.75 | 1.0 | 1.5
    hard_block:                   bool         # CHOPPY regime or other veto
    hard_block_reason:            str          # human-readable veto reason
    reasons:                      list[str]    # explain the score
    components:                   dict         # raw per-component scores (0-100 each)
    weights_used:                 dict         # weights applied (copy of WEIGHTS)

    def should_trade(self) -> bool:
        return not self.hard_block and self.conviction_score >= 40

    def to_dict(self) -> dict:
        return {
            "conviction_score":            round(self.conviction_score, 1),
            "conviction_grade":            self.conviction_grade,
            "recommended_risk_multiplier": self.recommended_risk_multiplier,
            "hard_block":                  self.hard_block,
            "hard_block_reason":           self.hard_block_reason,
            "reasons":                     self.reasons,
            "components":                  {k: round(v, 1) for k, v in self.components.items()},
            "weights_used":                self.weights_used,
        }


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _score_technical(setup: dict) -> tuple[float, str]:
    """
    0-100 component score from ICT confluence + A+ similarity.
    confluence is 0-18 (from setup_scorer). sim_ratio is 0-100.
    """
    confluence = setup.get("confluence", 0)
    sim_ratio  = float(setup.get("sim_ratio") or 0)

    # Normalize confluence 0-18 → 0-80
    base = min(80.0, round(confluence / 18.0 * 80.0, 1))

    # A+ bonus up to +20
    if sim_ratio >= 85:   bonus = 20.0
    elif sim_ratio >= 70: bonus = 14.0
    elif sim_ratio >= 55: bonus = 7.0
    else:                 bonus = 0.0

    score = min(100.0, base + bonus)

    parts = [f"ICT={confluence}/18"]
    if sim_ratio > 0:
        parts.append(f"A+sim={sim_ratio:.0f}%")
    return score, f"Technical ({', '.join(parts)})"


def _score_regime(regime_4h: str, direction: str) -> tuple[float, str]:
    """
    TRENDING in direction → 100
    TRENDING opposite     → 20
    RANGING               → 40
    CHOPPY                → 0  (triggers hard block upstream)
    UNKNOWN/None          → 30
    """
    if not regime_4h:
        return 30.0, "Regime: unknown (30)"

    r = regime_4h.upper()
    d = direction.upper()

    if r == "CHOPPY":
        return 0.0, "Regime: CHOPPY — hard block"

    if r == "TRENDING_UP":
        if d in ("BULLISH", "BUY", "LONG"):
            return 100.0, "Regime: TRENDING_UP aligned"
        else:
            return 20.0, "Regime: TRENDING_UP vs BEARISH — counter-trend"

    if r == "TRENDING_DOWN":
        if d in ("BEARISH", "SELL", "SHORT"):
            return 100.0, "Regime: TRENDING_DOWN aligned"
        else:
            return 20.0, "Regime: TRENDING_DOWN vs BULLISH — counter-trend"

    if r == "RANGING":
        return 40.0, "Regime: RANGING — reduced conviction"

    return 30.0, f"Regime: {r} — unclassified"


def _score_session(session: str, market: str) -> tuple[float, str]:
    """
    Kill zone or primary window → 100
    Secondary window → 65
    Off-session → 10
    """
    s = (session or "").lower()

    # Forex kill zones
    if market == "FOREX":
        if any(k in s for k in ("london", "new_york", "newyork", "ny_open", "nyopen")):
            return 100.0, f"Session: {session} kill zone (FOREX)"
        if any(k in s for k in ("overlap", "asian_close")):
            return 65.0, f"Session: {session} secondary (FOREX)"
        return 10.0, f"Session: {session} off-hours (FOREX)"

    # NSE windows
    if market == "NSE":
        if any(k in s for k in ("am", "10am", "morning", "silver_bullet_1")):
            return 100.0, f"Session: {session} NSE AM window"
        if any(k in s for k in ("pm", "13", "14", "silver_bullet_2")):
            return 85.0, f"Session: {session} NSE PM window"
        if any(k in s for k in ("close", "15", "silver_bullet_3")):
            return 70.0, f"Session: {session} NSE close window"
        return 10.0, f"Session: {session} off-window (NSE)"

    return 50.0, f"Session: {session} (market={market})"


def _score_correlation(market: str, symbol: str, direction: str) -> tuple[float, str]:
    """
    Pull cross-asset correlation from correlation engine.
    Aligned = 100, Neutral = 50, Diverging = 10.
    """
    try:
        from utils.correlation_engine import compute

        if market == "NSE":
            c = compute("NSE", "NSE:NIFTY50-INDEX", "NSE", "NSE:NIFTYBANK-INDEX", "1h", window=30)
            corr_val = c.correlation  # +1 = both moving same way
            dir_up   = direction.upper() in ("BULLISH", "BUY", "LONG")
            aligned  = (dir_up and corr_val > 0.4) or (not dir_up and corr_val > 0.4)
            if aligned and abs(corr_val) >= 0.6:
                return 100.0, f"Corr: NIFTY-BANK {corr_val:.2f} aligned"
            if abs(corr_val) >= 0.4:
                return 60.0, f"Corr: NIFTY-BANK {corr_val:.2f} moderate"
            return 30.0, f"Corr: NIFTY-BANK {corr_val:.2f} weak"

        if market == "FOREX" and symbol in ("XAGUSD", "USOIL"):
            c = compute("FOREX", "XAGUSD", "FOREX", "USOIL", "1h", window=30)
            corr_val = c.correlation
            dir_up   = direction.upper() in ("BULLISH", "BUY", "LONG")
            # If XAG and OIL are positively correlated and direction aligns → +
            if abs(corr_val) >= 0.6:
                return 80.0, f"Corr: XAG-OIL {corr_val:.2f} strong"
            return 50.0, f"Corr: XAG-OIL {corr_val:.2f} neutral"

        if market == "FOREX" and symbol == "EURUSD":
            c = compute("FOREX", "XAGUSD", "FOREX", "EURUSD", "1h", window=30)
            corr_val = c.correlation
            if abs(corr_val) >= 0.5:
                return 70.0, f"Corr: XAG-EUR {corr_val:.2f}"
            return 50.0, f"Corr: XAG-EUR {corr_val:.2f} low"

    except Exception as e:
        logger.debug(f"ConvictionEngine: correlation score failed: {e}")

    return 50.0, "Corr: unavailable (neutral)"


def _score_oi_flow(market: str, symbol: str, direction: str) -> tuple[float, str]:
    """
    NSE: PCR + option bias alignment with direction.
    FOREX: neutral 50 (no OI data).
    """
    if market != "NSE":
        return 50.0, "OI: FOREX — neutral (50)"

    try:
        from utils.oi_archive import get_max_oi_strikes

        _sym_map = {
            "NSE:NIFTY50-INDEX":    "NIFTY",
            "NSE:NIFTYBANK-INDEX":  "BANKNIFTY",
            "NSE:FINNIFTY-INDEX":   "FINNIFTY",
            "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
        }
        oi_sym = _sym_map.get(symbol, symbol.split(":")[-1].split("-")[0])
        oi = get_max_oi_strikes(oi_sym)

        if not oi:
            return 50.0, "OI: no snapshot available"

        bias     = (oi.get("option_bias") or "NEUTRAL").upper()
        pcr      = float(oi.get("pcr_oi") or 1.0)
        dir_bull = direction.upper() in ("BULLISH", "BUY", "LONG")

        # PCR > 1.2 → PUT heavy → bullish pressure
        # PCR < 0.8 → CALL heavy → bearish pressure
        pcr_bullish = pcr > 1.2
        pcr_bearish = pcr < 0.8

        bias_aligned = (bias == "BULLISH" and dir_bull) or (bias == "BEARISH" and not dir_bull)
        pcr_aligned  = (pcr_bullish and dir_bull) or (pcr_bearish and not dir_bull)

        if bias_aligned and pcr_aligned:
            return 100.0, f"OI: bias={bias} PCR={pcr:.2f} — both aligned"
        if bias_aligned or pcr_aligned:
            return 65.0, f"OI: bias={bias} PCR={pcr:.2f} — partial"
        return 20.0, f"OI: bias={bias} PCR={pcr:.2f} — against"

    except Exception as e:
        logger.debug(f"ConvictionEngine: OI score failed: {e}")

    return 50.0, "OI: error — neutral"


def _score_macro(market: str, symbol: str, direction: str) -> tuple[float, str]:
    """
    Knowledge graph macro alignment.
    STRONG aligned → 90, MODERATE → 65, WEAK/NEUTRAL → 50, AGAINST → 20.
    """
    try:
        from utils.knowledge_graph import KnowledgeGraph

        # Map trading symbol to KG node
        _node_map = {
            "XAGUSD":               "SILVER_PRICE",
            "XAUUSD":               "GOLD_PRICE",
            "USOIL":                "OIL_PRICE",
            "EURUSD":               "EUR_USD",
            "NSE:NIFTY50-INDEX":    "NIFTY50",
            "NSE:NIFTYBANK-INDEX":  "NIFTYBANK",
            "NSE:FINNIFTY-INDEX":   "NIFTY_IT",   # closest proxy
            "NSE:MIDCPNIFTY-INDEX": "NIFTY50",
        }
        node = _node_map.get(symbol)
        if not node:
            return 50.0, "Macro: symbol not in KG — neutral"

        kg = KnowledgeGraph()
        ctx = kg.trade_context(node)

        if not ctx:
            return 50.0, "Macro: no KG context"

        # trade_context returns a list of ImpactResult
        tailwind  = [r for r in ctx if r.direction == "POSITIVE"]
        headwind  = [r for r in ctx if r.direction == "NEGATIVE"]
        dir_bull  = direction.upper() in ("BULLISH", "BUY", "LONG")

        # Net strength: positive tailwinds vs headwinds
        tail_str  = sum(r.strength for r in tailwind)
        head_str  = sum(r.strength for r in headwind)
        net       = tail_str - head_str if dir_bull else head_str - tail_str

        if net >= 1.5:
            return 90.0, f"Macro: strong tailwind net={net:.1f}"
        if net >= 0.5:
            return 65.0, f"Macro: moderate tailwind net={net:.1f}"
        if net >= -0.5:
            return 50.0, f"Macro: neutral net={net:.1f}"
        return 20.0, f"Macro: headwind net={net:.1f}"

    except Exception as e:
        logger.debug(f"ConvictionEngine: macro score failed: {e}")

    return 50.0, "Macro: unavailable — neutral"


def _score_sector(market: str, symbol: str, direction: str) -> tuple[float, str]:
    """
    NSE: sector momentum aligned with direction.
    FOREX: 50 (no sector data).
    """
    if market != "NSE":
        return 50.0, "Sector: FOREX — neutral (50)"

    try:
        from utils.sector_intelligence import SectorIntelligence

        # Symbol → sector
        _sector_map = {
            "NSE:NIFTYBANK-INDEX":  "NIFTYBANK",
            "NSE:FINNIFTY-INDEX":   "FINNIFTY",
            "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
            "NSE:NIFTY50-INDEX":    None,           # broad market, skip sector check
        }
        sector_id = _sector_map.get(symbol)
        if sector_id is None:
            return 50.0, "Sector: broad index — neutral"

        si = SectorIntelligence()
        snap = si.sector_snapshot()
        states = [s for s in snap if s.sector == sector_id]

        if not states:
            return 50.0, f"Sector: {sector_id} — no data"

        state    = states[0]
        dir_bull = direction.upper() in ("BULLISH", "BUY", "LONG")

        macro_bull = (state.macro_bias or "").upper() == "BULLISH"
        macro_bear = (state.macro_bias or "").upper() == "BEARISH"
        regime_ok  = "TRENDING" in (state.regime or "")

        if dir_bull and macro_bull and regime_ok:
            return 100.0, f"Sector: {sector_id} BULLISH+TRENDING aligned"
        if not dir_bull and macro_bear and regime_ok:
            return 100.0, f"Sector: {sector_id} BEARISH+TRENDING aligned"
        if (dir_bull and macro_bull) or (not dir_bull and macro_bear):
            return 70.0, f"Sector: {sector_id} bias aligned, no trend"
        if (dir_bull and macro_bear) or (not dir_bull and macro_bull):
            return 10.0, f"Sector: {sector_id} macro against direction"

        return 50.0, f"Sector: {sector_id} neutral"

    except Exception as e:
        logger.debug(f"ConvictionEngine: sector score failed: {e}")

    return 50.0, "Sector: unavailable — neutral"


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class ConvictionEngine:
    """
    Thread-safe singleton.  Call evaluate() for every signal before sizing.
    All component scorers are called with silent fallback — engine never
    throws and never blocks order flow.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def evaluate(
        self,
        market:    str,           # FOREX | NSE
        symbol:    str,           # e.g. XAGUSD, NSE:NIFTY50-INDEX
        direction: str,           # BULLISH | BEARISH
        setup:     dict,          # ICT setup dict from signal_scanner
        session:   str = "",      # session label from the signal
        regime_4h: Optional[str] = None,  # pre-fetched regime (avoids double call)
    ) -> ConvictionResult:
        """
        Compute conviction score.  Returns ConvictionResult regardless of errors.
        Hard blocks CHOPPY regime before scoring.
        """
        reasons: list[str] = []

        # ── 0. Pre-fetch regime if not provided ──────────────────────────────
        if regime_4h is None:
            try:
                from utils.market_intelligence import MarketIntelligence
                r = MarketIntelligence().get_regime(market, symbol, "4h")
                regime_4h = r.regime
            except Exception as e:
                logger.debug(f"ConvictionEngine: regime fetch failed: {e}")
                regime_4h = "UNKNOWN"

        # ── 1. Hard block: CHOPPY ────────────────────────────────────────────
        if (regime_4h or "").upper() == "CHOPPY":
            return ConvictionResult(
                conviction_score=0.0,
                conviction_grade="D",
                recommended_risk_multiplier=0.0,
                hard_block=True,
                hard_block_reason="CHOPPY regime — no trades",
                reasons=["CHOPPY 4H regime is a hard block"],
                components={k: 0.0 for k in WEIGHTS},
                weights_used=dict(WEIGHTS),
            )

        # ── 2. Score each component ──────────────────────────────────────────
        tech_score,  tech_reason  = _score_technical(setup)
        reg_score,   reg_reason   = _score_regime(regime_4h, direction)
        sess_score,  sess_reason  = _score_session(session, market)
        corr_score,  corr_reason  = _score_correlation(market, symbol, direction)
        oi_score,    oi_reason    = _score_oi_flow(market, symbol, direction)
        macro_score, macro_reason = _score_macro(market, symbol, direction)
        sec_score,   sec_reason   = _score_sector(market, symbol, direction)

        components = {
            "technical":   tech_score,
            "regime":      reg_score,
            "session":     sess_score,
            "correlation": corr_score,
            "oi_flow":     oi_score,
            "macro":       macro_score,
            "sector":      sec_score,
        }

        # ── 3. Weighted sum ──────────────────────────────────────────────────
        total = sum(
            components[k] * WEIGHTS[k] / 100.0
            for k in WEIGHTS
        )
        total = round(min(100.0, max(0.0, total)), 1)

        # ── 4. Grade + multiplier ────────────────────────────────────────────
        grade = _grade(total)
        mult  = _risk_multiplier(total)

        # ── 5. Reason list (most important first) ────────────────────────────
        reason_pairs = [
            (WEIGHTS["technical"],   tech_reason),
            (WEIGHTS["regime"],      reg_reason),
            (WEIGHTS["session"],     sess_reason),
            (WEIGHTS["correlation"], corr_reason),
            (WEIGHTS["oi_flow"],     oi_reason),
            (WEIGHTS["macro"],       macro_reason),
            (WEIGHTS["sector"],      sec_reason),
        ]
        reasons = [r for _, r in sorted(reason_pairs, reverse=True)]

        # Append grade explanation
        if mult == 0.0:
            reasons.append(f"Grade {grade} ({total}) → SKIP trade")
        elif mult < 1.0:
            reasons.append(f"Grade {grade} ({total}) → {mult}× size")
        else:
            reasons.append(f"Grade {grade} ({total}) → {mult}× size (standard+)")

        return ConvictionResult(
            conviction_score=total,
            conviction_grade=grade,
            recommended_risk_multiplier=mult,
            hard_block=False,
            hard_block_reason="",
            reasons=reasons,
            components=components,
            weights_used=dict(WEIGHTS),
        )


# ---------------------------------------------------------------------------
# Module-level helper for one-line use in signal flow
# ---------------------------------------------------------------------------

def evaluate_conviction(
    market: str, symbol: str, direction: str,
    setup: dict, session: str = "", regime_4h: str = None,
) -> ConvictionResult:
    """Convenience wrapper around the singleton ConvictionEngine."""
    return ConvictionEngine().evaluate(
        market=market, symbol=symbol, direction=direction,
        setup=setup, session=session, regime_4h=regime_4h,
    )


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=== CB6 Quantum — Conviction Engine Demo ===\n")

    demo_cases = [
        {
            "market": "FOREX", "symbol": "XAGUSD", "direction": "BULLISH",
            "setup": {"confluence": 14, "sim_ratio": 72},
            "session": "london",
        },
        {
            "market": "NSE", "symbol": "NSE:NIFTY50-INDEX", "direction": "BEARISH",
            "setup": {"confluence": 16, "sim_ratio": 88},
            "session": "nse_am",
        },
        {
            "market": "FOREX", "symbol": "USOIL", "direction": "BULLISH",
            "setup": {"confluence": 8, "sim_ratio": 0},
            "session": "asian",
            "regime_4h": "CHOPPY",
        },
        {
            "market": "FOREX", "symbol": "EURUSD", "direction": "BEARISH",
            "setup": {"confluence": 11, "sim_ratio": 55},
            "session": "new_york",
            "regime_4h": "TRENDING_DOWN",
        },
    ]

    ce = ConvictionEngine()
    for case in demo_cases:
        r4h = case.pop("regime_4h", None)
        res = ce.evaluate(**case, regime_4h=r4h)
        print(f"Symbol : {case['symbol']} {case['direction']}")
        print(f"Score  : {res.conviction_score}  Grade: {res.conviction_grade}  Mult: {res.recommended_risk_multiplier}×")
        print(f"Block  : {res.hard_block} {res.hard_block_reason}")
        for r in res.reasons:
            print(f"  • {r}")
        print()
