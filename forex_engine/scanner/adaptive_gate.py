"""
CB6 Adaptive Trade Gate
=======================
Implements the decision hierarchy from CB6_ADAPTIVE_GATE_IMPLEMENTATION_PLAN_20260611.md.

Produces exactly one of:
    FULL_SIZE | REDUCED_SIZE | T1_ONLY | CAUTION | BLOCKED

Rules:
  - Only the nine approved hard-block conditions produce BLOCKED.
  - Counter-H4, BOS-only, weak session, low score — all produce soft adjustments only.
  - Multiple reductions resolve to the lowest applicable multiplier.
  - T1-only is cumulative: any rule requiring it locks the trade to T1 exit.

Enable with:  CB6_ADAPTIVE_TRADE_GATE_ENABLED=true  (default: false / LEGACY mode)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

FULL_SIZE    = "FULL_SIZE"
REDUCED_SIZE = "REDUCED_SIZE"
T1_ONLY      = "T1_ONLY"
CAUTION      = "CAUTION"
BLOCKED      = "BLOCKED"

_LOG_PATH = Path("data/adaptive_gate_log.jsonl")


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_score(setup: Dict[str, Any]) -> int:
    c = setup.get('confluence', 0)
    if isinstance(c, dict):
        return int(c.get('score', 0) or 0)
    return int(c or 0)


def _sweep_quality(setup: Dict[str, Any]) -> str:
    liq = setup.get('liq_sweep') or {}
    conf = float(liq.get('confidence', 0) or 0)
    wick = bool(liq.get('wick_close_back') or liq.get('wick_close_inside'))
    if conf >= 70 or wick:
        return "STRONG"
    if setup.get('sweep_confirmed') and conf >= 40:
        return "WEAK"
    if setup.get('sweep_confirmed'):
        return "WEAK"
    return "NONE"


def _fvg_quality(setup: Dict[str, Any]) -> str:
    c = setup.get('confluence') or {}
    fvg = (c.get('fvg') if isinstance(c, dict) else None) or setup.get('fvg') or {}
    return "STRONG" if fvg.get('displacement') else "WEAK"


def _session_label(utc_hour: int) -> str:
    if 7 <= utc_hour < 12:
        return "LONDON"
    if 16 <= utc_hour < 20:
        return "NEW_YORK"
    return "OFF_SESSION"


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class GateDecision:
    decision:           str
    trade_allowed:      bool
    size_multiplier:    float
    t1_only:            bool
    hard_block_reasons: List[str] = field(default_factory=list)
    soft_gate_reasons:  List[str] = field(default_factory=list)
    # context carried for Telegram card and logs
    h4_relation:   str = "UNKNOWN"   # ALIGNED / COUNTER / RANGING
    h1_relation:   str = "UNKNOWN"
    mss_type:      str = "UNKNOWN"
    source:        str = "PRIMARY"   # PRIMARY / CASCADE
    score:         int = 0
    rr:            float = 0.0
    session:       str = "UNKNOWN"
    sweep_quality: str = "UNKNOWN"
    fvg_quality:   str = "UNKNOWN"

    @property
    def telegram_card(self) -> str:
        allowed  = "YES" if self.trade_allowed else "NO"
        size_str = f"{self.size_multiplier:.2f}x"
        exit_str = "T1 ONLY" if self.t1_only else "MULTI-TARGET"
        lines = [
            f"<b>FINAL DECISION: {self.decision}</b>",
            f"Trade allowed : {allowed}",
            f"Size          : {size_str}",
            f"Exit          : {exit_str}",
            "",
            f"H4      : {self.h4_relation}",
            f"H1      : {self.h1_relation}",
            f"MSS     : {self.mss_type}",
            f"Source  : {self.source}",
            f"Score   : {self.score}",
            f"RR      : {self.rr:.1f}",
            f"Session : {self.session}",
            f"Sweep   : {self.sweep_quality}",
            f"FVG     : {self.fvg_quality}",
        ]
        if self.hard_block_reasons:
            lines += ["", "Hard blocks:"]
            lines += [f"  • {r}" for r in self.hard_block_reasons]
        if self.soft_gate_reasons:
            lines += ["", "Reasons:"]
            lines += [f"  • {r}" for r in self.soft_gate_reasons]
        return "\n".join(lines)


# ── core evaluator ────────────────────────────────────────────────────────────

def evaluate_adaptive_gate(
    setup: Dict[str, Any],
    h4_bias: str,
    h1_bias: str,
    utc_hour: int,
    *,
    hard_block_reasons: Optional[List[str]] = None,
) -> GateDecision:
    """
    Pure decision evaluator — no I/O, no side effects.

    hard_block_reasons: pre-checked conditions from the caller
        (e.g. daily loss hit, HFT guard, paused risk mode).
        Any entry here immediately returns BLOCKED.

    Soft gates (counter-H4, BOS bands, weak session) reduce size or
    restrict to T1-only but never produce BLOCKED on their own.
    """
    direction  = setup.get('direction', 'BULLISH')
    score      = _get_score(setup)
    mss_type   = str(setup.get('mss_type', 'BOS')).upper()
    rr         = float((setup.get('entry_signal') or {}).get('rr_ratio', 0) or 0)
    is_cascade = bool(setup.get('mtf_cascade'))
    symbol     = str(setup.get('symbol', ''))
    is_gold    = 'XAUUSD' in symbol.upper()

    session  = _session_label(utc_hour)
    h4_rel   = "RANGING" if h4_bias == "RANGING" else ("ALIGNED" if h4_bias == direction else "COUNTER")
    h1_rel   = "RANGING" if h1_bias == "RANGING" else ("ALIGNED" if h1_bias == direction else "COUNTER")
    sq       = _sweep_quality(setup)
    fq       = _fvg_quality(setup)
    source   = "CASCADE" if is_cascade else "PRIMARY"

    ctx = dict(
        h4_relation=h4_rel, h1_relation=h1_rel,
        mss_type=mss_type, source=source,
        score=score, rr=rr, session=session,
        sweep_quality=sq, fvg_quality=fq,
    )

    # ── 1. Hard blocks (pre-checked by caller) ────────────────────────────────
    hb = list(hard_block_reasons or [])
    if hb:
        return GateDecision(
            decision=BLOCKED, trade_allowed=False,
            size_multiplier=0.0, t1_only=True,
            hard_block_reasons=hb, **ctx,
        )

    # ── 2. Collect soft gate adjustments ─────────────────────────────────────
    size_mult = 1.0
    t1_flag   = False
    reasons: List[str] = []

    # ── Counter-H4 ────────────────────────────────────────────────────────────
    if h4_rel == "COUNTER":
        eligible = (
            score    >= 16
            and mss_type == "CHOCH"
            and sq   == "STRONG"
            and fq   == "STRONG"
            and rr   >= 2.0
        )
        missing = _missing_counter_h4(score, mss_type, sq, fq, rr)

        if is_gold:
            # XAUUSD counter-H4 is always CAUTION (never full-size)
            if eligible:
                mult = 0.25 if session == "OFF_SESSION" else 0.50
                size_mult = min(size_mult, mult)
                t1_flag   = True
                reasons.append(
                    f"XAUUSD counter-H4 LONG (H4={h4_bias}) — "
                    f"CAUTION {mult:.2f}x T1-only"
                )
            else:
                return GateDecision(
                    decision=CAUTION, trade_allowed=False,
                    size_multiplier=0.0, t1_only=True,
                    soft_gate_reasons=[
                        f"XAUUSD counter-H4 quality not met: {', '.join(missing)}"
                    ], **ctx,
                )
        else:
            # Non-gold counter-H4
            if eligible:
                size_mult = min(size_mult, 0.50)
                t1_flag   = True
                reasons.append(
                    f"Counter-H4 (H4={h4_bias} vs {direction}) — 0.50x T1-only"
                )
            else:
                return GateDecision(
                    decision=CAUTION, trade_allowed=False,
                    size_multiplier=0.0, t1_only=True,
                    soft_gate_reasons=[
                        f"Counter-H4 quality not met: {', '.join(missing)}"
                    ], **ctx,
                )

    # ── Counter-H1 ────────────────────────────────────────────────────────────
    if h1_rel == "COUNTER":
        wave_count = int(setup.get('wave_count', 0) or 0)
        sweep_ok   = bool(setup.get('sweep_confirmed', False))
        if wave_count >= 3 and sweep_ok:
            size_mult = min(size_mult, 0.50)
            t1_flag   = True
            reasons.append(
                f"Counter-H1 3-wave exception (wave={wave_count}) — 0.50x T1-only"
            )
        else:
            return GateDecision(
                decision=CAUTION, trade_allowed=False,
                size_multiplier=0.0, t1_only=True,
                soft_gate_reasons=[
                    f"Counter-H1 quality not met "
                    f"(wave={wave_count}, sweep_confirmed={sweep_ok})"
                ], **ctx,
            )

    # ── BOS score bands ───────────────────────────────────────────────────────
    if mss_type == "BOS":
        if score >= 15:
            size_mult = min(size_mult, 0.75)
            reasons.append(f"BOS-only score={score}≥15 — 0.75x")
        elif score >= 12:
            size_mult = min(size_mult, 0.50)
            t1_flag   = True
            reasons.append(f"BOS-only score={score} (12–14) — 0.50x T1-only")
        else:
            return GateDecision(
                decision=CAUTION, trade_allowed=False,
                size_multiplier=0.0, t1_only=True,
                soft_gate_reasons=[f"BOS-only score={score}<12 — quality skip"],
                **ctx,
            )

    # ── Weak session ─────────────────────────────────────────────────────────
    if session == "OFF_SESSION":
        size_mult = min(size_mult, 0.50)
        t1_flag   = True
        reasons.append("Off-session entry — 0.50x T1-only")

    # ── Final normalization ───────────────────────────────────────────────────
    if is_gold and h4_rel == "COUNTER" and reasons:
        decision = CAUTION
    elif t1_flag and size_mult < 1.0:
        decision = T1_ONLY
    elif size_mult < 1.0:
        decision = REDUCED_SIZE
    else:
        decision = FULL_SIZE

    return GateDecision(
        decision=decision, trade_allowed=True,
        size_multiplier=size_mult, t1_only=t1_flag,
        soft_gate_reasons=reasons, **ctx,
    )


def _missing_counter_h4(
    score: int, mss_type: str, sq: str, fq: str, rr: float
) -> List[str]:
    missing = []
    if score < 16:        missing.append(f"score={score}<16")
    if mss_type != "CHOCH": missing.append(f"MSS={mss_type} (need CHoCH)")
    if sq != "STRONG":    missing.append(f"sweep={sq} (need STRONG)")
    if fq != "STRONG":    missing.append(f"FVG={fq} (need STRONG)")
    if rr < 2.0:          missing.append(f"RR={rr:.1f}<2.0")
    return missing


# ── structured logger ─────────────────────────────────────────────────────────

def log_gate_decision(symbol: str, decision: GateDecision) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            'ts'           : datetime.now(timezone.utc).isoformat(),
            'symbol'       : symbol,
            'decision'     : decision.decision,
            'trade_allowed': decision.trade_allowed,
            'size_mult'    : decision.size_multiplier,
            't1_only'      : decision.t1_only,
            'hard_blocks'  : decision.hard_block_reasons,
            'soft_reasons' : decision.soft_gate_reasons,
            'h4'           : decision.h4_relation,
            'h1'           : decision.h1_relation,
            'mss'          : decision.mss_type,
            'source'       : decision.source,
            'score'        : decision.score,
            'rr'           : decision.rr,
            'session'      : decision.session,
            'sweep'        : decision.sweep_quality,
            'fvg'          : decision.fvg_quality,
        }
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row) + '\n')
    except Exception:
        pass
