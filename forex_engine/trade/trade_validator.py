# forex_engine/trade/trade_validator.py
# Pre-trade validation — checks all hard blocks before opening a position.

from datetime import datetime, timezone
from typing import Optional
from utils.logger import logger
from forex_engine.scanner.signal_scanner import (
    is_in_kill_zone, is_prime_kz, in_rollover_window
)
from forex_engine.forex_instruments import INSTRUMENTS, FTMO_RISK_GUARD


def validate_trade(
    setup: dict,
    utc_hour: int,
    h1_bias: str,
    h4_bias: str,
    symbol_min_score: dict,
    state: dict,
    spread: Optional[float] = None,
    gft_mode: bool = False,
) -> tuple[bool, str]:
    """
    Run all hard-block checks. Returns (allowed, reason).
    Order: rollover → KZ → sweep → FVG → H4 → H1 → score → cooldown → session limit.
    """
    symbol    = setup['symbol']
    direction = setup['direction']
    score     = setup['confluence']
    mss_type  = setup.get('mss_type', 'BOS')

    # 1. Rollover window
    if in_rollover_window(utc_hour):
        return False, f"ROLLOVER BLOCK ({utc_hour}:xx UTC)"

    # 2. Kill zone
    if not is_in_kill_zone(utc_hour, gft=gft_mode):
        return False, f"OUT-OF-KZ ({utc_hour}:xx UTC)"

    # 3. Liquidity sweep required
    liq    = setup.get('liq_sweep')
    sweep_ok = (
        liq is not None
        and liq.get('direction') == direction
        and liq.get('candles_ago', 999) <= 15
        and liq.get('level_state') == 'SWEPT'
    )
    if not sweep_ok:
        return False, "NO SWEEP — ICT sequence requires liquidity sweep before MSS"
    if int(liq.get('confidence', 0) or 0) < 45:
        return False, f"LOW-QUALITY SWEEP — confidence {liq.get('confidence', 0)}/100"

    # 4. Must be in FVG
    if not setup.get('in_fvg'):
        return False, "NOT IN FVG — price must retest FVG before entry"

    # 5. H4 bias — informational only, not a gate.
    # Removed: 15-day backtest confirmed H4 gate blocked only valid 3-wave setups.

    # 6. Silver Asia SELL block
    if symbol == 'XAGUSD' and utc_hour < 7 and direction == 'BEARISH':
        return False, "XAGUSD Asia SELL block (00-07 UTC)"

    # 7. H1 bias
    choch_override = (mss_type == 'CHOCH' and score >= 11)
    if h1_bias != 'RANGING' and h1_bias != direction and not choch_override:
        return False, f"H1 BIAS BLOCK — H1={h1_bias}, setup={direction}"

    # 8. Score gate
    sym_min       = symbol_min_score.get(symbol, 11)
    h1_ranging    = (h1_bias == 'RANGING')
    eff_score     = score + (1 if mss_type == 'CHOCH' else 0)
    min_score_now = sym_min
    if not is_prime_kz(utc_hour):
        min_score_now += 1
    if h1_ranging:
        min_score_now += 1
    if eff_score < min_score_now:
        return False, (f"SCORE TOO LOW — {score} (eff={eff_score}) < {min_score_now}")

    # 9. Spread check
    cfg = INSTRUMENTS.get(symbol, {})
    max_spread = cfg.get('max_spread')
    if max_spread is not None and spread is not None and spread > max_spread:
        return False, f"SPREAD BLOCK — {spread:.5f} > max {max_spread:.5f}"

    # 10. RRR gate
    sig       = setup['entry_signal']
    sl_dist   = abs(sig['entry'] - sig['stop_loss'])
    t2_dist   = abs(sig['target2'] - sig['entry'])
    entry_rrr = round(t2_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    min_rrr   = FTMO_RISK_GUARD.get('min_entry_rrr', 2.0)
    if entry_rrr < min_rrr:
        return False, f"RRR BLOCK — {entry_rrr:.2f} < min {min_rrr:.2f}"

    return True, 'OK'


def validate_cooldown(symbol: str, state: dict, minutes: int = 90,
                      is_aplus: bool = False) -> tuple[bool, str]:
    """
    Block trading within `minutes` of a loss on this symbol.
    A+ setups bypass the cooldown.
    Returns (allowed, reason).
    """
    if is_aplus:
        return True, 'OK'

    for trade in reversed(state.get('closed_trades', [])):
        if trade.get('symbol') != symbol:
            continue
        if trade.get('pnl_usd', 0) >= 0:
            continue
        exit_time = trade.get('exit_time', '')
        if not exit_time:
            continue
        try:
            loss_dt = datetime.strptime(exit_time, '%Y-%m-%d %H:%M:%S')
            elapsed = (datetime.now() - loss_dt).total_seconds() / 60
            if elapsed < minutes:
                remaining = int(minutes - elapsed)
                return False, (
                    f"COOLDOWN — last loss {elapsed:.0f}min ago, "
                    f"{remaining}min remaining"
                )
        except Exception:
            pass
        break

    return True, 'OK'


def validate_session_limit(symbol: str, state: dict, utc_hour: int,
                           is_aplus: bool = False) -> tuple[bool, str]:
    """
    Max 1 closed trade per symbol per session (London or NY).
    A+ setups bypass the limit.
    """
    if is_aplus:
        return True, 'OK'

    sess_start_h = 7 if utc_hour < 13 else 13
    sess_start   = datetime.now(timezone.utc).replace(
        hour=sess_start_h, minute=0, second=0, microsecond=0
    )
    sess_start_s = sess_start.astimezone().strftime('%Y-%m-%d %H:%M:%S')

    sym_trades = [
        t for t in state.get('closed_trades', [])
        if t.get('symbol') == symbol
        and (t.get('entry_time', '') or '') >= sess_start_s
    ]
    if len(sym_trades) >= 1:
        return False, f"SESSION LIMIT — {len(sym_trades)} trade(s) this session"

    return True, 'OK'
