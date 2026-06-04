# core/market_brain.py â€” CB6 Market Intelligence Brain
#
# Synthesizes ALL existing signals into a session-level MarketContext.
# Two layers:
#   1. Rule engine  â€” deterministic scoring, always available, runs every scan
#   2. AI layer     â€” Claude Haiku 2-3 sentence reading, cached 2 hours
#
# MarketContext drives:
#   - which direction to favour (BUY / SELL / BOTH / NONE)
#   - how strict the score gate is for this session
#   - whether to trade at all (SIT_OUT)
#   - a plain-English briefing on Telegram via /brain

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple

from utils.logger import logger

# â”€â”€ tunables â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_AI_CACHE_MINUTES   = 120          # refresh Claude reasoning every 2 h
_DAILY_BRIEF_SENT   = ""           # YYYY-MM-DD; morning brief sent once/day

# module-level singleton state
_context:           Optional[MarketContext] = None   # type: ignore[name-defined]
_context_date:      str = ""
_last_ai_call:      Optional[datetime] = None


# â”€â”€ data model â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@dataclass
class MarketContext:
    session_bias:        str    # BULLISH | BEARISH | MIXED | AVOID
    confidence:          int    # 1-10
    trade_mode:          str    # AGGRESSIVE | SELECTIVE | DEFENSIVE | SIT_OUT
    preferred_direction: str    # BUY | SELL | BOTH | NONE
    score_gate:          int    # min confluence score for this session
    score:               int    # raw signal score  -10 â€¦ +10
    signals:             Dict   # raw signal breakdown (for /brain display)
    reasoning:           str    # AI or rule-based explanation
    last_updated:        str    # "HH:MM IST  YYYY-MM-DD"


# â”€â”€ signal collection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _collect_signals(fyers=None) -> Dict:
    """
    Pull every available signal source.  Never raises â€” returns partial data on
    any individual failure so the brain always produces some output.
    """
    sig: Dict = {}

    # 1. Macro bias (removed â€” SB-only mode)
    sig['macro'] = {'aligned': False, 'direction': 'NEUTRAL',
                    'w1': 'NEUTRAL', 'd1': 'NEUTRAL', 'h4': 'NEUTRAL',
                    'reason': 'disabled'}

    # 2. FII / DII institutional flow
    try:
        from data.fii_dii import get_market_bias_from_fii_dii
        bias_str, info = get_market_bias_from_fii_dii()
        sig['fii_dii'] = {
            'bias':    bias_str,
            'fii_net': info.get('fii_net', 0),
            'dii_net': info.get('dii_net', 0),
            'reason':  info.get('reason', ''),
            'stale':   info.get('is_stale', False),
        }
    except Exception as e:
        logger.debug(f"Brain FII/DII: {e}")
        sig['fii_dii'] = {'bias': 'NEUTRAL', 'fii_net': 0, 'dii_net': 0,
                          'reason': 'unavailable', 'stale': True}

    # 3. NIFTY premium / discount zone (removed â€” SB-only mode)
    try:
        sig['zone'] = {}
    except Exception as e:
        logger.debug(f"Brain zone: {e}")
        sig['zone'] = {}

    # 4. Geopolitical / event mode
    try:
        from data.news_calendar import is_geopolitical_event
        sig['event_mode'] = is_geopolitical_event()
    except Exception:
        sig['event_mode'] = False

    # 5. (kill zone timing removed â€” trades allowed all market hours)

    # 6. Today's paper-trade performance
    try:
        from trader.paper_trader import load_state
        state     = load_state()
        today_str = datetime.now().strftime('%Y-%m-%d')
        closed_today = [t for t in state.get('closed_trades', [])
                        if t.get('exit_time', '')[:10] == today_str]
        wins   = sum(1 for t in closed_today if t.get('pnl', 0) > 0)
        losses = sum(1 for t in closed_today if t.get('pnl', 0) < 0)
        pnl    = sum(t.get('pnl', 0) for t in closed_today)
        # Consecutive losses from ALL closed trades (not just today)
        consec = 0
        for t in reversed(state.get('closed_trades', [])):
            if t.get('pnl', 0) < 0:
                consec += 1
            else:
                break
        sig['today_trades'] = {
            'open':               len(state.get('open_trades', [])),
            'wins':               wins,
            'losses':             losses,
            'pnl':                round(pnl, 0),
            'consecutive_losses': consec,
        }
    except Exception:
        sig['today_trades'] = {'open': 0, 'wins': 0, 'losses': 0,
                               'pnl': 0, 'consecutive_losses': 0}

    # 7. Bot memory â€” overall win rate + learned hour preferences
    try:
        from data.bot_memory import load_memory
        mem   = load_memory()
        total = mem.get('total_trades', 0)
        wr    = round(mem.get('winning_trades', 0) / total * 100, 1) if total else 0
        lp    = mem.get('learned_params', {})
        sig['memory'] = {
            'total_trades': total,
            'win_rate':     wr,
            'best_hours':   lp.get('best_hours', []),
            'avoid_hours':  lp.get('avoid_hours', []),
        }
    except Exception:
        sig['memory'] = {'total_trades': 0, 'win_rate': 0,
                         'best_hours': [], 'avoid_hours': []}

    return sig


# â”€â”€ rule-based scoring engine â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _score(signals: Dict) -> Tuple[int, int]:
    """
    Returns (raw_score, confidence).
    raw_score: -10 â€¦ +10  (positive â†’ bullish, negative â†’ bearish)
    confidence: 1-10
    """
    s   = 0
    conf = 5

    # â”€â”€ macro bias â”€â”€
    macro = signals.get('macro', {})
    w1, d1, h4 = (macro.get('w1', 'NEUTRAL'),
                  macro.get('d1', 'NEUTRAL'),
                  macro.get('h4', 'NEUTRAL'))
    bulls = [w1, d1, h4].count('BULLISH')
    bears = [w1, d1, h4].count('BEARISH')

    if macro.get('aligned'):
        if macro['direction'] == 'BULLISH':
            s += 3; conf += 2
        elif macro['direction'] == 'BEARISH':
            s -= 3; conf += 2
    elif bulls == 2:
        s += 1; conf += 1
    elif bears == 2:
        s -= 1; conf += 1
    else:
        conf -= 1   # conflicted macro â†’ lower conviction

    # â”€â”€ FII / DII â”€â”€
    fii = signals.get('fii_dii', {})
    if not fii.get('stale', True):
        fnet = fii.get('fii_net', 0)
        dnet = fii.get('dii_net', 0)
        if fnet > 500:
            s += 2; conf += 1
        elif fnet > 0 and dnet > 0:
            s += 1
        elif fnet < -500:
            s -= 2; conf += 1
        elif fnet < 0 and dnet < 0:
            s -= 1
    # stale FII data â†’ no contribution, no penalty

    # â”€â”€ premium / discount zone â”€â”€
    zone = signals.get('zone', {})
    zone_name = zone.get('zone', 'UNKNOWN')
    pct       = zone.get('position_pct', 50)
    if zone_name == 'DEEP_DISCOUNT':
        s += 2          # price cheap â€” institutions likely to buy
    elif zone_name == 'DISCOUNT':
        s += 1
    elif zone_name == 'DEEP_PREMIUM':
        s -= 2          # price expensive â€” institutions likely to sell
    elif zone_name == 'PREMIUM':
        s -= 1

    # â”€â”€ event / crisis mode â”€â”€
    if signals.get('event_mode'):
        s    = s // 2   # halve the score â€” extreme caution
        conf -= 2

    # â”€â”€ today's performance â”€â”€
    today = signals.get('today_trades', {})
    consec = today.get('consecutive_losses', 0)
    wins_t  = today.get('wins', 0)
    loss_t  = today.get('losses', 0)
    total_t = wins_t + loss_t

    if consec >= 3:
        s -= 10         # force SIT_OUT regardless of macro
        conf -= 3
    elif total_t > 0:
        day_wr = wins_t / total_t
        if day_wr < 0.3:
            s -= 2; conf -= 1
        elif day_wr > 0.65:
            s += 1; conf += 1

    # â”€â”€ bot memory overall win rate â”€â”€
    wr = signals.get('memory', {}).get('win_rate', 0)
    if wr > 55:
        s += 1
    elif 0 < wr < 40:
        s -= 1

    return max(-10, min(10, s)), max(1, min(10, conf))


# â”€â”€ mode derivation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _derive_mode(raw_score: int, conf: int, signals: Dict,
                 ) -> Tuple[str, str, str, int]:
    """
    Returns (session_bias, trade_mode, preferred_direction, score_gate).
    """
    try:
        from settings import MIN_BUY_SCORE, MIN_SELL_SCORE
        base = MIN_BUY_SCORE
    except Exception:
        base = 6

    today  = signals.get('today_trades', {})
    consec = today.get('consecutive_losses', 0)

    # Hard SIT_OUT triggers
    if consec >= 3:
        return 'AVOID', 'SIT_OUT', 'NONE', 99
    if signals.get('event_mode') and conf <= 2:
        return 'AVOID', 'SIT_OUT', 'NONE', 99

    # Bias from score
    if raw_score >= 4:
        bias = 'BULLISH'
    elif raw_score <= -4:
        bias = 'BEARISH'
    elif raw_score > 0:
        bias = 'BULLISH'
    elif raw_score < 0:
        bias = 'BEARISH'
    else:
        bias = 'MIXED'

    # Direction â€” backtest: SELL 72% WR vs BUY 67% on NSE 5-min
    # When macro is neutral (score==0), favour SELL over BOTH
    if raw_score > 0:
        direction = 'BUY'
    elif raw_score < 0:
        direction = 'SELL'
    else:
        direction = 'SELL'   # SELL bias when no macro conviction

    # Trade mode from confidence + score magnitude
    magnitude = abs(raw_score)
    if conf >= 7 and magnitude >= 5:
        mode = 'AGGRESSIVE'
    elif conf >= 4 and magnitude >= 2:
        mode = 'SELECTIVE'
    elif conf >= 3:
        mode = 'SELECTIVE'
    else:
        mode = 'DEFENSIVE'

    # Score gate
    if mode == 'AGGRESSIVE':
        gate = max(base - 1, 5)
    elif mode == 'SELECTIVE':
        gate = base
    else:                       # DEFENSIVE
        gate = base + 2

    return bias, mode, direction, gate


# â”€â”€ AI reasoning layer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _ai_reasoning(signals: Dict, score: int, bias: str, mode: str) -> str:
    """
    Ask Claude Haiku for a 2-3 sentence market read.
    Returns empty string if API unavailable or cache still fresh.
    Caches for _AI_CACHE_MINUTES to avoid burning tokens every scan.
    """
    global _last_ai_call
    try:
        key = os.getenv('ANTHROPIC_API_KEY', '')
        if not key:
            return ''

        now = datetime.now()
        if (_last_ai_call and
                (now - _last_ai_call).total_seconds() < _AI_CACHE_MINUTES * 60):
            return ''   # caller keeps the cached reasoning

        import anthropic as _ant

        macro = signals.get('macro', {})
        fii   = signals.get('fii_dii', {})
        zone  = signals.get('zone', {})
        today = signals.get('today_trades', {})
        mem   = signals.get('memory', {})

        prompt = (
            "You are the market brain for CB6 QUANTUM, an ICT algo trading NSE India.\n\n"
            f"Brain score: {score:+d}/10  |  Session bias: {bias}  |  Mode: {mode}\n"
            f"Macro  W1={macro.get('w1')} D1={macro.get('d1')} H4={macro.get('h4')} "
            f"aligned={macro.get('aligned')}\n"
            f"FII net Rs {fii.get('fii_net', 0):+.0f} Cr  |  DII Rs {fii.get('dii_net', 0):+.0f} Cr  "
            f"bias={fii.get('bias')}\n"
            f"NIFTY zone: {zone.get('zone', '?')} at {zone.get('position_pct', 50):.0f}% of range\n"
            f"Today: {today.get('wins', 0)}W / {today.get('losses', 0)}L  "
            f"PnL Rs {today.get('pnl', 0):+.0f}  consec_losses={today.get('consecutive_losses', 0)}\n"
            f"Bot all-time WR: {mem.get('win_rate', 0):.1f}% from {mem.get('total_trades', 0)} trades\n\n"
            "Give a 2-3 sentence ICT-style market reading for this session:\n"
            "1. What the structure says (bullish / bearish / choppy)\n"
            "2. What to focus on (buy CE / buy PE / sit out / wait for sweep)\n"
            "3. Key risk or level to watch\n\n"
            "Under 150 words. Direct, ICT terminology, no fluff."
        )

        client = _ant.Anthropic(api_key=key)
        resp   = client.messages.create(
            model      = 'claude-haiku-4-5-20251001',
            max_tokens = 200,
            messages   = [{"role": "user", "content": prompt}]
        )
        _last_ai_call = now
        return resp.content[0].text.strip()

    except Exception as e:
        logger.debug(f"Brain AI reasoning: {e}")
        return ''


def _rule_reasoning(signals: Dict, bias: str, mode: str) -> str:
    """Fallback rule-based reasoning when Claude is unavailable."""
    macro = signals.get('macro', {})
    fii   = signals.get('fii_dii', {})
    zone  = signals.get('zone', {})
    today = signals.get('today_trades', {})

    parts = []
    if macro.get('aligned'):
        parts.append(f"W1+D1+H4 all {macro['direction'].lower()}")
    elif macro.get('direction', 'NEUTRAL') != 'NEUTRAL':
        parts.append(f"Macro partially {macro['direction'].lower()}")

    if not fii.get('stale') and fii.get('fii_net', 0) != 0:
        net = fii['fii_net']
        parts.append(
            f"FII {'buying' if net > 0 else 'selling'} Rs {abs(net):.0f} Cr"
        )

    if zone.get('zone'):
        parts.append(f"NIFTY in {zone['zone'].lower().replace('_', ' ')}")

    if today.get('consecutive_losses', 0) >= 2:
        parts.append(f"{today['consecutive_losses']} consecutive losses â€” be cautious")

    action_map = {
        'AGGRESSIVE': f"High-confidence {bias.lower()} session. Take valid setups.",
        'SELECTIVE':  "Be selective â€” only A+ ICT setups qualify.",
        'DEFENSIVE':  "Low conviction â€” wait for clearer structure before entering.",
        'SIT_OUT':    "Conditions unfavourable â€” sit out, protect capital.",
    }
    action = action_map.get(mode, "Trade with caution.")

    body = (", ".join(parts) + ". " + action) if parts else action
    return body


# â”€â”€ public API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def refresh(fyers=None, force: bool = False) -> 'MarketContext':
    """
    Collect all signals and rebuild MarketContext.
    Cached per calendar day unless force=True.
    Always returns a valid MarketContext (uses safe fallback on error).
    """
    global _context, _context_date

    today = datetime.now().strftime('%Y-%m-%d')
    if not force and _context and _context_date == today:
        return _context

    try:
        signals          = _collect_signals(fyers)
        raw_score, conf  = _score(signals)
        bias, mode, direction, gate = _derive_mode(raw_score, conf, signals)

        # AI reasoning (returns '' if cache still warm)
        ai_text = _ai_reasoning(signals, raw_score, bias, mode)
        reasoning = ai_text if ai_text else (
            _context.reasoning if _context and _context_date == today
            else _rule_reasoning(signals, bias, mode)
        )

        import pytz
        ts = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%H:%M IST %d %b %Y')

        _context = MarketContext(
            session_bias        = bias,
            confidence          = conf,
            trade_mode          = mode,
            preferred_direction = direction,
            score_gate          = gate,
            score               = raw_score,
            signals             = signals,
            reasoning           = reasoning,
            last_updated        = ts,
        )
        _context_date = today

        logger.info(
            f"Brain: {bias} | {mode} | dir={direction} | "
            f"gate={gate} | score={raw_score:+d} | conf={conf}"
        )
        return _context

    except Exception as e:
        logger.error(f"Brain refresh error: {e}")
        fallback = MarketContext(
            session_bias='MIXED', confidence=3, trade_mode='DEFENSIVE',
            preferred_direction='SELL', score_gate=11, score=0,
            signals={}, reasoning=f"Brain error â€” {e}",
            last_updated=datetime.now().strftime('%H:%M IST %d %b %Y'),
        )
        _context = fallback
        return fallback


def get_context() -> Optional[MarketContext]:
    """Return the current context without triggering a refresh."""
    return _context


def validate_trade(setup: Dict) -> Tuple[bool, str]:
    """
    Gate called by paper_trader before every trade entry.
    Returns (allowed: bool, reason: str).
    """
    ctx = _context
    if ctx is None:
        return True, "Brain not ready â€” allowing by default"

    direction = setup.get('direction', 'BUY')
    score     = float(setup.get('confluence', 0))

    if ctx.trade_mode == 'SIT_OUT':
        return False, "Brain: SIT_OUT â€” no trades this session"

    if ctx.preferred_direction == 'BUY' and direction == 'SELL':
        return False, (
            f"Brain: session bias {ctx.session_bias} â€” skipping SELL"
        )
    if ctx.preferred_direction == 'SELL' and direction == 'BUY':
        return False, (
            f"Brain: session bias {ctx.session_bias} â€” skipping BUY"
        )
    if score < ctx.score_gate:
        return False, (
            f"Brain: score {score:.0f}/10 < gate {ctx.score_gate}/10 "
            f"({ctx.trade_mode} mode)"
        )

    return True, "Brain: OK"


def format_report() -> str:
    """Format current MarketContext for Telegram /brain command."""
    ctx = _context
    if ctx is None:
        return (
            "CB6 BRAIN â€” Not initialized yet.\n"
            "Run /equity or /nifty to trigger the first scan."
        )

    sig   = ctx.signals
    macro = sig.get('macro', {})
    fii   = sig.get('fii_dii', {})
    zone  = sig.get('zone', {})
    today = sig.get('today_trades', {})
    mem   = sig.get('memory', {})

    zone_str = (
        f"{zone.get('zone', '?')} ({zone.get('position_pct', 0):.0f}%)"
        if zone else "unknown"
    )
    fii_net_str = (
        f"Rs {fii.get('fii_net', 0):+.0f}Cr"
        + (" [STALE]" if fii.get('stale') else "")
    )

    return (
        f"CB6 MARKET BRAIN\n{ctx.last_updated}\n\n"
        f"BIAS       : {ctx.session_bias} ({ctx.confidence}/10)\n"
        f"MODE       : {ctx.trade_mode}\n"
        f"DIRECTION  : {ctx.preferred_direction}\n"
        f"SCORE GATE : {ctx.score_gate}/10 min\n"
        f"RAW SCORE  : {ctx.score:+d}/10\n\n"
        f"SIGNALS\n"
        f"Macro W1   : {macro.get('w1', '?')}\n"
        f"Macro D1   : {macro.get('d1', '?')}\n"
        f"Macro H4   : {macro.get('h4', '?')}\n"
        f"Macro align: {'YES' if macro.get('aligned') else 'NO'} â€” {macro.get('reason', '')}\n"
        f"FII/DII    : {fii.get('bias', '?')} | {fii_net_str}\n"
        f"NIFTY Zone : {zone_str}\n"
        f"Today      : {today.get('wins', 0)}W {today.get('losses', 0)}L  "
        f"PnL Rs {today.get('pnl', 0):+.0f}\n"
        f"Consec loss: {today.get('consecutive_losses', 0)}\n"
        f"All-time WR: {mem.get('win_rate', 0):.1f}% "
        f"({mem.get('total_trades', 0)} trades)\n\n"
        f"BRAIN READ\n{ctx.reasoning}\n\n"
        f"/brain refresh â€” force update\n"
        f"Updated: {ctx.last_updated}"
    )


def morning_brief(fyers=None) -> str:
    """
    Build and return the morning brief message for the day.
    Designed to be called once at market open (9:15 IST).
    Forces a full AI-enhanced refresh.
    """
    ctx = refresh(fyers, force=True)
    return (
        f"CB6 QUANTUM â€” MORNING BRIEF\n{ctx.last_updated}\n\n"
        f"Today's bias : {ctx.session_bias}\n"
        f"Mode         : {ctx.trade_mode}\n"
        f"Favour       : {ctx.preferred_direction}\n"
        f"Score gate   : {ctx.score_gate}/10\n\n"
        f"{ctx.reasoning}\n\n"
        "Type /brain for full signal breakdown."
    )

