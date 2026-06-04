# scanner/silver_bullet.py — ICT Silver Bullet Strategy (Indian Market)
#
# Strategy:
#   1. Wait for Silver Bullet window (10:00-11:00 or 13:30-14:30 IST)
#   2. Identify Draw on Liquidity (nearest unswept swing high/low)
#   3. Detect Market Structure Shift (MSS) — close beyond last swing
#   4. Find Fair Value Gap (FVG) formed after MSS
#   5. Enter on first touch of FVG, SL at FVG edge, T2 = 1:3 RR
#   6. Options: ITM/ATM, Delta 0.6-0.8. Exit if stuck in FVG > 15 min.
#
# No trades before 10:00 AM — let the 9:15 Judas Swing clear retail first.

from __future__ import annotations

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytz

from utils.logger import logger
from ml_engine.memory.shadow_logger import log_scanner_outcome

IST = pytz.timezone('Asia/Kolkata')

# ── Silver Bullet windows (IST) ───────────────────────────────────────────────
SILVER_BULLET_WINDOWS: List[Dict] = [
    {
        'name'   : 'Morning Silver Bullet',
        'start_h': 10, 'start_m': 0,
        'end_h'  : 11, 'end_m'  : 0,
        'desc'   : 'NSE open — smart money active',
    },
    {
        'name'   : 'Afternoon Silver Bullet',
        'start_h': 13, 'start_m': 0,
        'end_h'  : 14, 'end_m'  : 0,
        'desc'   : 'London cross — Europe opens, high volatility',
    },
    {
        'name'   : 'Close Silver Bullet',
        'start_h': 15, 'start_m': 0,
        'end_h'  : 15, 'end_m'  : 30,
        'desc'   : 'NSE pre-close — final smart money sweep',
    },
]

# ── time helpers ──────────────────────────────────────────────────────────────

def is_silver_bullet_window() -> Tuple[bool, str]:
    """Return (in_window, window_name)."""
    now = datetime.now(IST)
    cur = now.hour * 60 + now.minute
    for w in SILVER_BULLET_WINDOWS:
        start = w['start_h'] * 60 + w['start_m']
        end   = w['end_h']   * 60 + w['end_m']
        if start <= cur < end:
            return True, w['name']
    return False, ''


def get_window_status() -> str:
    in_w, name = is_silver_bullet_window()
    if in_w:
        return f"ACTIVE — {name}"
    now = datetime.now(IST)
    cur = now.hour * 60 + now.minute
    for w in SILVER_BULLET_WINDOWS:
        start = w['start_h'] * 60 + w['start_m']
        if cur < start:
            return f"Waiting — next: {w['name']} at {w['start_h']:02d}:{w['start_m']:02d} IST"
    return "No more Silver Bullet windows today — resume tomorrow 10:00 IST"


def minutes_into_window() -> int:
    """Minutes elapsed since the current window opened. -1 if not in window."""
    now = datetime.now(IST)
    cur = now.hour * 60 + now.minute
    for w in SILVER_BULLET_WINDOWS:
        start = w['start_h'] * 60 + w['start_m']
        end   = w['end_h']   * 60 + w['end_m']
        if start <= cur < end:
            return cur - start
    return -1


# ── opening-range guard ───────────────────────────────────────────────────────

FVG_BUFFER    = 0.5    # fallback — use _fvg_buffer(symbol) for per-index scaling
MAX_FVG_PTS   = 50.0   # NSE index points — rejects overnight/structural gaps; override via scan_silver_bullet(max_fvg_pts=X)

# Strike gaps per index (NSE option chain spacing in points)
_INDEX_STRIKE_GAPS = {
    'MIDCPNIFTY': 25,
    'FINNIFTY'  : 50,
    'NIFTY'     : 50,
    'BANKNIFTY' : 100,
}

def _fvg_buffer(symbol: str) -> float:
    """Return FVG buffer scaled to 1% of the index strike gap."""
    sym = (symbol or '').upper().replace('NSE:', '').replace('-INDEX', '')
    for idx, gap in sorted(_INDEX_STRIKE_GAPS.items(), key=lambda x: -len(x[0])):
        if idx in sym:
            return round(gap * 0.01, 2)
    return FVG_BUFFER


# ── Market regime detection ───────────────────────────────────────────────────

def _adx(df, period: int = 14) -> float:
    """Compute ADX(period) on the last row. Returns 0 on error."""
    try:
        import pandas as pd
        h, l, c = df['high'], df['low'], df['close']
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        up   = h.diff();  dn = -l.diff()
        pdm  = up.where((up > dn) & (up > 0), 0.0)
        ndm  = dn.where((dn > up) & (dn > 0), 0.0)
        pdi  = 100 * pdm.ewm(span=period, adjust=False).mean() / atr
        ndi  = 100 * ndm.ewm(span=period, adjust=False).mean() / atr
        dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1)
        return round(dx.ewm(span=period, adjust=False).mean().iloc[-1], 1)
    except Exception:
        return 0.0


def market_regime(df) -> str:
    """
    Returns 'TRENDING' | 'NEUTRAL' | 'CHOPPY'.
    ADX >= 25 → trending  |  18-25 → neutral  |  < 18 → choppy
    """
    adx = _adx(df)
    if adx >= 25:
        return 'TRENDING'
    if adx >= 18:
        return 'NEUTRAL'
    return 'CHOPPY'


def get_dte(symbol: str) -> int:
    """
    Days-to-expiry for a given NSE index symbol/future symbol.
    Returns 0 on expiry day, 99 if unknown.
    """
    try:
        from scanner.expiry_calendar import get_active_index_option_expiry, days_to_expiry
        sym = symbol.upper().replace('NSE:', '').replace('-INDEX', '')
        import re
        base = re.sub(r'\d{2}[A-Z]{3}FUT$', '', sym)
        IDX_MAP = {'NIFTY50': 'NIFTY', 'NIFTYBANK': 'BANKNIFTY'}
        base = IDX_MAP.get(base, base)
        # get_active_index_option_expiry falls back to monthly for BANKNIFTY/FINNIFTY/MIDCPNIFTY
        expiry = get_active_index_option_expiry(base)
        if expiry is None:
            return 99
        dte = days_to_expiry(expiry)
        return max(dte, 0) if dte is not None else 99
    except Exception:
        return 99


# Target multiplier table: (dte_bucket, regime) → (t1_mult, t2_mult, t3_mult)
# dte_bucket: 0 = expiry today, 1 = 1 DTE, 2+ = normal
_TARGET_MULTS = {
    (0, 'TRENDING'): (1.0, 1.5, 2.0),
    (0, 'NEUTRAL') : (0.75, 1.0, 1.5),
    (0, 'CHOPPY')  : (0.5,  0.75, 1.0),
    (1, 'TRENDING'): (1.5, 2.0, 3.0),
    (1, 'NEUTRAL') : (1.0, 1.5, 2.0),
    (1, 'CHOPPY')  : (0.75, 1.0, 1.5),
    (2, 'TRENDING'): (2.0, 3.0, 4.0),
    (2, 'NEUTRAL') : (1.5, 2.0, 3.0),
    (2, 'CHOPPY')  : (1.0, 1.5, 2.0),
}


def _target_multipliers(dte: int, regime: str) -> tuple:
    bucket = min(dte, 2)
    return _TARGET_MULTS.get((bucket, regime), (2.0, 3.0, 4.0))


def premium_discount_context(df, price: float, lookback: int = 60,
                             equilibrium_buffer: float = 0.05) -> Dict:
    """
    Classify price inside the active recent range.
    BULLISH entries should form in discount; BEARISH entries in premium.
    """
    try:
        recent = df.tail(lookback)
        if recent.empty:
            return {'zone': 'UNKNOWN', 'aligned': True}

        high = float(recent['high'].max())
        low = float(recent['low'].min())
        width = high - low
        if width <= 0:
            return {'zone': 'UNKNOWN', 'aligned': True}

        equilibrium = (high + low) / 2
        position = (float(price) - low) / width
        if abs(position - 0.5) <= equilibrium_buffer:
            zone = 'EQUILIBRIUM'
        elif position < 0.5:
            zone = 'DISCOUNT'
        else:
            zone = 'PREMIUM'

        return {
            'zone': zone,
            'high': high,
            'low': low,
            'equilibrium': equilibrium,
            'position': round(position, 4),
            'aligned': True,
        }
    except Exception as e:
        logger.debug(f"Premium/discount context error: {e}")
        return {'zone': 'UNKNOWN', 'aligned': True}


def premium_discount_aligned(direction: str, context: Dict) -> bool:
    zone = context.get('zone', 'UNKNOWN')
    if zone == 'UNKNOWN':
        return True
    if direction == 'BULLISH':
        return zone == 'DISCOUNT'
    if direction == 'BEARISH':
        return zone == 'PREMIUM'
    return False


def get_opening_range(df) -> Optional[Dict]:
    """
    Return the high and low of the 9:15–10:00 AM IST opening range.
    df must have a datetime index or a 'datetime' column.
    Silver Bullet only triggers AFTER this range is swept.
    """
    try:
        import pandas as pd
        d = df.copy()
        # Normalise index to datetime
        if not isinstance(d.index, pd.DatetimeIndex):
            if 'datetime' in d.columns:
                d = d.set_index(pd.to_datetime(d['datetime']))
            elif 'date' in d.columns:
                d = d.set_index(pd.to_datetime(d['date']))
            else:
                return None

        if d.index.tz is None:
            d.index = d.index.tz_localize('Asia/Kolkata')
        else:
            d.index = d.index.tz_convert('Asia/Kolkata')

        today = d.index[-1].date()
        open_start = pd.Timestamp(f"{today} 09:15:00", tz='Asia/Kolkata')
        open_end   = pd.Timestamp(f"{today} 10:00:00", tz='Asia/Kolkata')
        opening    = d[(d.index >= open_start) & (d.index < open_end)]

        if opening.empty:
            return None
        return {
            'high': float(opening['high'].max()),
            'low' : float(opening['low'].min()),
        }
    except Exception as e:
        logger.debug(f"Opening range error: {e}")
        return None


def opening_range_swept(df, direction: str) -> bool:
    """
    BULLISH: price must have broken ABOVE the 9:15-10:00 high (liquidity sweep).
    BEARISH: price must have broken BELOW the 9:15-10:00 low (liquidity sweep).
    Returns True if the sweep has occurred (Judas Swing complete).
    """
    try:
        or_range = get_opening_range(df)
        if or_range is None:
            return False  # can't confirm Judas sweep without opening range data — block

        # Check if any post-open bar's wick swept the opening range level.
        # ICT sweep requires price to touch/pierce the level intrabar — not just on close.
        import pandas as pd
        d = df.copy()
        if not isinstance(d.index, pd.DatetimeIndex):
            if 'datetime' in d.columns:
                d = d.set_index(pd.to_datetime(d['datetime']))
            elif 'date' in d.columns:
                d = d.set_index(pd.to_datetime(d['date']))
        if d.index.tz is None:
            d.index = d.index.tz_localize('Asia/Kolkata')
        else:
            d.index = d.index.tz_convert('Asia/Kolkata')
        today = d.index[-1].date()
        post_open = d[d.index >= pd.Timestamp(f"{today} 10:00:00", tz='Asia/Kolkata')]
        if post_open.empty:
            # No post-10:00 closed candle yet (scanner fired at exactly 10:00).
            # Can't confirm sweep — but don't block; let DOL/MSS/FVG chain decide.
            return True
        if direction == 'BULLISH':
            return float(post_open['high'].max()) > or_range['high']
        else:
            return float(post_open['low'].min()) < or_range['low']
    except Exception:
        return True  # data error — don't block, let other filters handle it


# ── Opening gap bias ─────────────────────────────────────────────────────────

def get_opening_gap_bias(df) -> Optional[str]:
    """
    Detect institutional day bias from the opening gap + first candle direction.

    Gap up + first 9:15 candle bearish → BEARISH:
        Smart money gapped price up to grab retail breakout longs, then sold hard.
    Gap down + first 9:15 candle bullish → BULLISH:
        Smart money gapped price down to grab retail breakdown shorts, then bought.

    Returns 'BEARISH', 'BULLISH', or None (no clear bias).
    """
    try:
        import pandas as pd
        d = df.copy()
        if not isinstance(d.index, pd.DatetimeIndex):
            if 'datetime' in d.columns:
                d = d.set_index(pd.to_datetime(d['datetime']))
            elif 'date' in d.columns:
                d = d.set_index(pd.to_datetime(d['date']))
            else:
                return None
        if d.index.tz is None:
            d.index = d.index.tz_localize('Asia/Kolkata')
        else:
            d.index = d.index.tz_convert('Asia/Kolkata')

        today      = d.index[-1].date()
        today_bars = d[pd.Series(d.index.date, index=d.index) == today]
        prev_bars  = d[pd.Series(d.index.date, index=d.index) < today]
        if today_bars.empty or prev_bars.empty:
            return None

        first    = today_bars.iloc[0]
        prev_cls = float(prev_bars['close'].iloc[-1])
        gap_pct  = (float(first['open']) - prev_cls) / prev_cls * 100

        if abs(gap_pct) < 0.1:          # gap < 0.1% — no institutional intent
            return None

        first_bear = float(first['close']) < float(first['open'])
        first_bull = float(first['close']) > float(first['open'])

        if gap_pct > 0 and first_bear:
            return 'BEARISH'            # gap up + rejection candle → sell the gap
        if gap_pct < 0 and first_bull:
            return 'BULLISH'            # gap down + acceptance candle → buy the dip
        return None
    except Exception as e:
        logger.debug(f"Gap bias error: {e}")
        return None


# ── Day extremes (HOD / LOD) ─────────────────────────────────────────────────

def get_day_extremes(df) -> Optional[Dict]:
    """
    Returns today's HOD and LOD with the timestamps they were set.
    {'high': float, 'high_time': Timestamp, 'low': float, 'low_time': Timestamp}

    HOD / LOD are primary liquidity pools — retail SLs cluster just above HOD and
    just below LOD. Used as DOL fallback and for PM reversal detection.
    """
    try:
        import pandas as pd
        d = df.copy()
        if not isinstance(d.index, pd.DatetimeIndex):
            if 'datetime' in d.columns:
                d = d.set_index(pd.to_datetime(d['datetime']))
            elif 'date' in d.columns:
                d = d.set_index(pd.to_datetime(d['date']))
            else:
                return None
        if d.index.tz is None:
            d.index = d.index.tz_localize('Asia/Kolkata')
        else:
            d.index = d.index.tz_convert('Asia/Kolkata')

        today      = d.index[-1].date()
        today_bars = d[pd.Series(d.index.date, index=d.index) == today]
        if today_bars.empty:
            return None

        hod_idx = today_bars['high'].idxmax()
        lod_idx = today_bars['low'].idxmin()
        return {
            'high'     : float(today_bars['high'].max()),
            'high_time': hod_idx,
            'low'      : float(today_bars['low'].min()),
            'low_time' : lod_idx,
        }
    except Exception as e:
        logger.debug(f"Day extremes error: {e}")
        return None


# ── step 0 — Liquidity Sweep detection ───────────────────────────────────────

def detect_liquidity_sweep(df, lookback: int = 60,
                            sweep_window: int = 20) -> Optional[Dict]:
    """
    Detect a recent smart-money liquidity sweep (stop hunt).

    HOW IT WORKS
    ─────────────
    Smart money needs liquidity to fill large orders.  They push price just
    beyond a known swing high/low where retail stop-losses cluster, grab that
    liquidity, then reverse hard.  The candle leaves a long wick but CLOSES
    back inside the prior range — a trap, not a breakout.

    BEARISH setup  →  sweep of swing HIGH
        Wick above swing high + close back below it
        → retail longs stopped out at the top
        → smart money distributed / sold into that spike
        → expect DOWN move after sweep

    BULLISH setup  →  sweep of swing LOW
        Wick below swing low + close back above it
        → retail shorts stopped out at the bottom
        → smart money accumulated / bought into that dip
        → expect UP move after sweep

    PARAMETERS
    ──────────
    lookback      : total candles to search for the swing reference level
    sweep_window  : how many of the MOST RECENT candles to check for the sweep
                    (sweep must be fresh — stale sweeps lose edge)

    RETURNS
    ───────
    {
      'direction'   : 'BULLISH'|'BEARISH',   — trade direction AFTER the sweep
      'swept_level' : float,                  — the swing level that was swept
      'candles_ago' : int,                    — how recent the sweep was
      'wick_extreme': float,                  — how far beyond the level price went
      'sweep_type'  : 'LOW_SWEEP'|'HIGH_SWEEP',
    }
    or None.
    """
    try:
        if len(df) < 20:
            return None

        recent = df.tail(lookback).reset_index(drop=True)
        n      = len(recent)

        # Swing reference: candles BEFORE the sweep window
        structure = recent.iloc[:n - sweep_window]
        sweep_rgn = recent.iloc[n - sweep_window:].reset_index(drop=True)

        if len(structure) < 8:
            return None

        # Build ALL qualified swing highs/lows from the structure window.
        # Bug 4 fix: old code used max(swing_highs)/min(swing_lows) — only the
        # single most extreme level. ICT Silver Bullet sweeps happen at ANY recent
        # swing (equal highs, minor killzone pivots, PDH/PDL).  Tracking every
        # qualified swing and testing each in the sweep window captures those
        # intermediate and EQH/EQL sweeps that the old detector missed entirely.
        swing_levels: list = []  # [{'side': 'high'|'low', 'level': float}]

        for i in range(3, len(structure) - 3):
            h = float(structure['high'].iloc[i])
            l = float(structure['low'].iloc[i])
            if h == float(structure['high'].iloc[i - 3:i + 4].max()):
                swing_levels.append({'side': 'high', 'level': h})
            if l == float(structure['low'].iloc[i - 3:i + 4].min()):
                swing_levels.append({'side': 'low', 'level': l})

        if not swing_levels:
            return None

        # Scan sweep region — check EVERY tracked swing level against each candle.
        # Prefer the most recent sweep (smallest candles_ago).
        # When the same candle sweeps multiple levels, prefer the larger wick excess
        # (more stops grabbed = higher probability reversal per ICT).
        best: Optional[Dict] = None

        for i in range(len(sweep_rgn)):
            row         = sweep_rgn.iloc[i]
            candles_ago = len(sweep_rgn) - 1 - i   # 0 = current candle

            high = float(row['high'])
            low  = float(row['low'])
            cls  = float(row['close'])

            for sl in swing_levels:
                lvl = sl['level']

                if sl['side'] == 'high' and high > lvl and cls < lvl:
                    # BEARISH sweep: wick above swing HIGH, close back below —
                    # buy-side stops grabbed → smart money distributed at the top
                    wick_excess = high - lvl
                    existing_excess = (
                        abs(best['wick_extreme'] - best['swept_level'])
                        if best is not None else -1
                    )
                    if (best is None
                            or candles_ago < best['candles_ago']
                            or (candles_ago == best['candles_ago']
                                and wick_excess > existing_excess)):
                        best = {
                            'direction'   : 'BEARISH',
                            'swept_level' : lvl,
                            'candles_ago' : candles_ago,
                            'wick_extreme': high,
                            'sweep_type'  : 'HIGH_SWEEP',
                        }

                elif sl['side'] == 'low' and low < lvl and cls > lvl:
                    # BULLISH sweep: wick below swing LOW, close back above —
                    # sell-side stops grabbed → smart money accumulated at the bottom
                    wick_excess = lvl - low
                    existing_excess = (
                        abs(best['wick_extreme'] - best['swept_level'])
                        if best is not None else -1
                    )
                    if (best is None
                            or candles_ago < best['candles_ago']
                            or (candles_ago == best['candles_ago']
                                and wick_excess > existing_excess)):
                        best = {
                            'direction'   : 'BULLISH',
                            'swept_level' : lvl,
                            'candles_ago' : candles_ago,
                            'wick_extreme': low,
                            'sweep_type'  : 'LOW_SWEEP',
                        }

        return best

    except Exception as e:
        logger.debug(f"Liquidity sweep detect error: {e}")
        return None


def score_sweep_quality(liq_sweep: Optional[Dict], dol: Optional[Dict] = None) -> int:
    """
    Score a detected liquidity sweep 0-10.

    Dimensions
    ----------
    Freshness   (0-3): how recent the sweep candle was
    Wick excess (0-3): how far price extended beyond the level (institutional force)
    Level type  (0-2): EQH/EQL / PDH/PDL cluster = external > single intraday swing
    Direction   (0-2): sweep direction confirmed against trade setup direction

    Thresholds (enforced at call site)
    -----------------------------------
    < 6  = weak sweep, trade blocked
    6-7  = medium sweep, tradeable
    8-9  = strong sweep
    10   = institutional sweep (rare: fresh + large wick + external level)
    """
    if liq_sweep is None:
        return 0

    s = 0

    # Freshness: 0=current candle, higher = older
    ca = liq_sweep.get('candles_ago', 99)
    if ca <= 3:
        s += 3      # sweep on current or last 3 candles — hot signal
    elif ca <= 8:
        s += 2      # still fresh
    elif ca <= 15:
        s += 1      # acceptable
    # > 15: 0 — but enforced earlier by sweep_confirmed (candles_ago <= 15)

    # Wick excess as % of swept level
    swept = liq_sweep.get('swept_level', 0)
    wick  = abs(liq_sweep.get('wick_extreme', swept) - swept)
    if swept > 0:
        excess_pct = wick / swept * 100
        if excess_pct >= 0.5:
            s += 3   # strong stop hunt — large institutional order
        elif excess_pct >= 0.20:
            s += 2
        elif excess_pct >= 0.05:
            s += 1
        # < 0.05%: micro-wick, not institutionally meaningful

    # Level type — external liquidity scores higher
    if dol is not None:
        dol_type = dol.get('type', '')
        if dol.get('is_eqh_eql') or dol_type in ('EQH', 'EQL'):
            s += 2   # equal highs/lows = dense stop cluster = external quality
        elif dol_type in ('PDH', 'PDL', 'PWH', 'PWL'):
            s += 2   # prior day/week levels = clean external liquidity
        elif dol_type in ('HOD', 'LOD'):
            s += 1   # intraday extreme — internal but still meaningful
        else:
            s += 1   # single swing — internal liquidity (minimum)
    else:
        s += 1       # no DOL info — assume internal

    # Direction confirmation (+2 always when sweep_confirmed already validated)
    # This bonus is applied by the caller only when direction matches
    s += 2

    return min(10, s)


def _detect_compression(df, atr_lookback: int = 14, compress_window: int = 5) -> Dict:
    """
    Detect volatility compression (tight range) vs. true chop.

    Returns {'is_compressed': bool, 'compression_pct': float, 'breakout_ready': bool}

    ATR contraction: current ATR < 60% of 14-period ATR mean → compressed.
    Breakout ready: compressed + price at range extreme (within 5% of high or low).

    Use case: block entry in true chop (ADX < 18 + NOT compressed),
    but allow entry when compressed (ADX < 18 + ATR contracting = coiling for breakout).
    """
    try:
        if len(df) < atr_lookback + compress_window + 2:
            return {'is_compressed': False, 'compression_pct': 100.0, 'breakout_ready': False}

        highs  = df['high'].values.astype(float)
        lows   = df['low'].values.astype(float)
        closes = df['close'].values.astype(float)

        # True Range
        tr = []
        for i in range(1, len(df)):
            tr.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i]  - closes[i-1])
            ))
        tr = tr[-(atr_lookback + compress_window):]
        if not tr:
            return {'is_compressed': False, 'compression_pct': 100.0, 'breakout_ready': False}

        atr_full    = sum(tr) / len(tr)
        atr_recent  = sum(tr[-compress_window:]) / compress_window
        comp_pct    = (atr_recent / atr_full * 100) if atr_full > 0 else 100.0

        is_compressed = comp_pct < 60.0   # recent ATR < 60% of baseline

        # Breakout ready: price near range extreme
        recent_high = max(highs[-compress_window:])
        recent_low  = min(lows[-compress_window:])
        last_close  = closes[-1]
        range_size  = recent_high - recent_low
        breakout_ready = False
        if is_compressed and range_size > 0:
            dist_from_extreme = min(
                abs(last_close - recent_high),
                abs(last_close - recent_low)
            )
            breakout_ready = (dist_from_extreme / range_size) < 0.15

        return {
            'is_compressed'  : is_compressed,
            'compression_pct': round(comp_pct, 1),
            'breakout_ready' : breakout_ready,
            'atr_recent'     : round(atr_recent, 2),
            'atr_baseline'   : round(atr_full, 2),
        }
    except Exception as e:
        logger.debug(f"Compression detect error: {e}")
        return {'is_compressed': False, 'compression_pct': 100.0, 'breakout_ready': False}


# ── step 1 — Draw on Liquidity ────────────────────────────────────────────────

def detect_eqh_eql(df, lookback: int = 100, tolerance: float = 0.0005,
                   min_touches: int = 2, swing_window: int = 3) -> Dict:
    """
    Detect Equal Highs (EQH) and Equal Lows (EQL) — engineered liquidity pools.

    EQH: 2+ swing highs within `tolerance` of each other → Buy-Side Liquidity above price.
         Retail longs cluster stops just above these highs; smart money hunts them.
    EQL: 2+ swing lows within `tolerance` → Sell-Side Liquidity below price.
         Retail shorts cluster stops just below these lows; smart money hunts them.

    tolerance=0.0005 (0.05%) → for NIFTY at 24 000 that is ±12 pts.
    Real equal highs/lows are never exactly equal — second touch typically lands
    a few points higher or lower (e.g. 24850 → 24855 or 24848 → 24858).
    0.05% captures these natural variations without merging clearly distinct levels.

    Returns:
        {
          'eqh': [{'level': float, 'count': int, 'candles_ago': int, 'type': 'EQH'}, ...],
          'eql': [{'level': float, 'count': int, 'candles_ago': int, 'type': 'EQL'}, ...],
        }
        Lists are sorted: most-touched cluster first, then nearest to current price.
    """
    try:
        if len(df) < 20:
            return {'eqh': [], 'eql': []}

        recent     = df.tail(lookback).reset_index(drop=True)
        last_close = float(recent['close'].iloc[-1])
        n          = len(recent)

        # ── collect swing pivots ───────────────────────────────────────────────
        highs: list[tuple[float, int]] = []
        lows:  list[tuple[float, int]] = []

        for i in range(swing_window, n - swing_window):
            h = float(recent['high'].iloc[i])
            l = float(recent['low'].iloc[i])
            hi_slice = recent['high'].iloc[i - swing_window : i + swing_window + 1]
            lo_slice = recent['low'].iloc[i - swing_window : i + swing_window + 1]
            if h == float(hi_slice.max()):
                highs.append((h, i))
            if l == float(lo_slice.min()):
                lows.append((l, i))

        def _cluster(points: list[tuple[float, int]], is_high: bool) -> list[dict]:
            if not points:
                return []
            pts = sorted(points, key=lambda x: x[0])
            clusters: list[dict] = []
            group = [pts[0]]

            def _emit(grp):
                if len(grp) < min_touches:
                    return
                level      = sum(p[0] for p in grp) / len(grp)
                latest_idx = max(p[1] for p in grp)
                c_ago      = n - 1 - latest_idx
                future     = recent['close'].iloc[latest_idx + 1:]
                if is_high:
                    if any(future > level * 1.0005):
                        return          # already swept
                    if level <= last_close:
                        return          # below price — not above
                else:
                    if any(future < level * 0.9995):
                        return          # already swept
                    if level >= last_close:
                        return          # above price — not below
                clusters.append({
                    'level'      : round(level, 2),
                    'count'      : len(grp),
                    'candles_ago': c_ago,
                    'type'       : 'EQH' if is_high else 'EQL',
                })

            for pt in pts[1:]:
                if abs(pt[0] - group[-1][0]) / group[-1][0] <= tolerance:
                    group.append(pt)
                else:
                    _emit(group)
                    group = [pt]
            _emit(group)

            clusters.sort(key=lambda c: (-c['count'], abs(c['level'] - last_close)))
            return clusters

        return {
            'eqh': _cluster(highs, is_high=True),
            'eql': _cluster(lows,  is_high=False),
        }

    except Exception as exc:
        logger.debug(f"EQH/EQL detect error: {exc}")
        return {'eqh': [], 'eql': []}


def find_draw_on_liquidity(df, lookback: int = 60,
                           wick_sweep: bool = False) -> Optional[Dict]:
    """
    Find the primary unswept liquidity pool — this is where price is 'drawn'.

    Priority: EQH/EQL clusters (2+ equal highs/lows) beat single swing pivots because
    they represent denser stop accumulation. Falls back to nearest single swing.

    Returns:
        {
          'type'         : 'EQH'|'EQL'|'HIGH'|'LOW'|'HOD'|'LOD',
          'level'        : float,
          'direction'    : 'BULLISH'|'BEARISH',
          'is_eqh_eql'   : bool,
          'cluster_count': int,
        }
    """
    try:
        if len(df) < 20:
            return None

        recent     = df.tail(lookback).reset_index(drop=True)
        last_close = float(recent['close'].iloc[-1])

        # ── single-swing pivot scan ────────────────────────────────────────────
        swing_highs: list[dict] = []
        swing_lows:  list[dict] = []

        for i in range(3, len(recent) - 3):
            window_slice_h = recent['high'].iloc[i - 3:i + 4]
            window_slice_l = recent['low'].iloc[i - 3:i + 4]

            if float(recent['high'].iloc[i]) == float(window_slice_h.max()):
                level = float(recent['high'].iloc[i])
                swept = (any(recent['high'].iloc[i + 1:] > level) if wick_sweep
                         else any(recent['close'].iloc[i + 1:] > level * 1.0005))
                swing_highs.append({'level': level, 'swept': swept})

            if float(recent['low'].iloc[i]) == float(window_slice_l.min()):
                level = float(recent['low'].iloc[i])
                swept = (any(recent['low'].iloc[i + 1:] < level) if wick_sweep
                         else any(recent['close'].iloc[i + 1:] < level * 0.9995))
                swing_lows.append({'level': level, 'swept': swept})

        unswept_highs = [h for h in swing_highs if not h['swept'] and h['level'] > last_close]
        unswept_lows  = [l for l in swing_lows  if not l['swept'] and l['level'] < last_close]

        # ── EQH/EQL — preferred DOL when cluster exists ───────────────────────
        eqh_eql = detect_eqh_eql(recent, lookback=len(recent))

        # Best EQH above price (BULLISH draw) — prefer over single swing high
        best_bull: Optional[Dict] = None
        if eqh_eql['eqh']:
            c = eqh_eql['eqh'][0]   # sorted: most-touched first
            best_bull = {
                'type': 'EQH', 'level': c['level'],
                'direction': 'BULLISH',
                'is_eqh_eql': True, 'cluster_count': c['count'],
            }
        elif unswept_highs:
            h = min(unswept_highs, key=lambda x: abs(x['level'] - last_close))
            best_bull = {
                'type': 'HIGH', 'level': h['level'],
                'direction': 'BULLISH',
                'is_eqh_eql': False, 'cluster_count': 1,
            }

        # Best EQL below price (BEARISH draw) — prefer over single swing low
        best_bear: Optional[Dict] = None
        if eqh_eql['eql']:
            c = eqh_eql['eql'][0]
            best_bear = {
                'type': 'EQL', 'level': c['level'],
                'direction': 'BEARISH',
                'is_eqh_eql': True, 'cluster_count': c['count'],
            }
        elif unswept_lows:
            l = min(unswept_lows, key=lambda x: abs(x['level'] - last_close))
            best_bear = {
                'type': 'LOW', 'level': l['level'],
                'direction': 'BEARISH',
                'is_eqh_eql': False, 'cluster_count': 1,
            }

        # Pick nearest to current price between bull and bear candidates
        if best_bull is None and best_bear is None:
            return None
        if best_bull is None:
            return best_bear
        if best_bear is None:
            return best_bull
        d_bull = abs(best_bull['level'] - last_close)
        d_bear = abs(best_bear['level'] - last_close)
        return best_bull if d_bull <= d_bear else best_bear

    except Exception as e:
        logger.debug(f"DOL error: {e}")
        return None


# ── step 2 — Market Structure Shift ──────────────────────────────────────────

# Differentiated recency limits — data-driven from 838 NSE trades:
#   CHoCH = direction flip → must be fresh (stale CHoCH = fake signal)
#   BOS   = continuation   → can be older (structure persists longer)
MSS_RECENCY_CHOCH = 15   # CHoCH: 15×3min = 45 min on 3-min bars
MSS_RECENCY_BOS   = 30   # BOS:   30×3min = 90 min on 3-min bars
MSS_RECENCY_CANDLES = MSS_RECENCY_CHOCH  # legacy alias, kept for imports

def detect_sb_mss(df, lookback: int = 30, use_wicks: bool = False) -> Optional[Dict]:
    """
    MSS: any candle in the lookback window that broke beyond a prior swing.
    Returns the MOST RECENT such event within MSS_RECENCY_CANDLES.
    {'direction': 'BULLISH'/'BEARISH', 'level': float, 'candles_ago': int}

    use_wicks=False (NSE default): break confirmed only when candle CLOSES beyond swing.
    use_wicks=True  (Forex/Crypto): any wick beyond the swing counts as a break —
                    catches CHoCH/BOS earlier, consistent with ICT wick-based structure.
    """
    try:
        recent = df.tail(lookback).reset_index(drop=True)
        n      = len(recent)
        if n < 10:
            return None

        events = []   # {direction, level, candles_ago, j}

        for i in range(3, n - 1):
            hi = float(recent['high'].iloc[i])
            lo = float(recent['low'].iloc[i])
            # ±3 window (7 candles = 21 min on 3-min bars) — finds real structural swings.
            # Was ±2 (5 candles = 15 min) which detected micro-swings as structure.
            window_h = float(recent['high'].iloc[max(0, i - 3):i + 4].max())
            window_l = float(recent['low'].iloc[max(0, i - 3):i + 4].min())

            # Swing high at position i
            if hi == window_h:
                for j in range(i + 1, n):
                    if use_wicks:
                        # Wick break: any candle whose HIGH exceeds the swing high
                        if float(recent['high'].iloc[j]) > hi:
                            events.append({
                                'direction'  : 'BULLISH',
                                'level'      : hi,
                                'candles_ago': n - 1 - j,
                                'j'          : j,
                            })
                            break
                    else:
                        p = float(recent['close'].iloc[j - 1])
                        c = float(recent['close'].iloc[j])
                        if p < hi and c > hi:
                            events.append({
                                'direction'  : 'BULLISH',
                                'level'      : hi,
                                'candles_ago': n - 1 - j,
                                'j'          : j,
                            })
                            break

            # Swing low at position i
            if lo == window_l:
                for j in range(i + 1, n):
                    if use_wicks:
                        # Wick break: any candle whose LOW undercuts the swing low
                        if float(recent['low'].iloc[j]) < lo:
                            events.append({
                                'direction'  : 'BEARISH',
                                'level'      : lo,
                                'candles_ago': n - 1 - j,
                                'j'          : j,
                            })
                            break
                    else:
                        p = float(recent['close'].iloc[j - 1])
                        c = float(recent['close'].iloc[j])
                        if p > lo and c < lo:
                            events.append({
                                'direction'  : 'BEARISH',
                                'level'      : lo,
                                'candles_ago': n - 1 - j,
                                'j'          : j,
                            })
                            break

        if not events:
            return None

        # Sort by candle index (chronological)
        events.sort(key=lambda x: x['j'])

        # Label CHoCH vs BOS — CHoCH = direction flipped from previous event
        prev_dir = None
        for ev in events:
            if prev_dir is None or prev_dir != ev['direction']:
                ev['type'] = 'CHOCH'   # direction flip = change of character
            else:
                ev['type'] = 'BOS'     # same direction = break of structure
            prev_dir = ev['direction']

        # Differentiated recency: CHoCH must be fresh (15ca), BOS can be older (30ca).
        # Data: CHoCH is a direction flip — stale ones are noise.
        #       BOS is continuation — structure can persist 90+ min on 3-min bars.
        recent_events = [
            e for e in events
            if e['candles_ago'] <= (MSS_RECENCY_BOS if e['type'] == 'BOS' else MSS_RECENCY_CHOCH)
        ]
        if not recent_events:
            return None

        # Prefer most recent; tie-break by type (CHoCH > BOS for same recency)
        best = min(recent_events, key=lambda x: (x['candles_ago'], 0 if x['type'] == 'CHOCH' else 1))
        return best

    except Exception as e:
        logger.warning(f"SB MSS error: {e}")
        return None


# ── step 3 — FVG after MSS ────────────────────────────────────────────────────

def detect_sb_fvg(df, direction: str, lookback: int = 20,
                   displacement_mult: float = 1.2,
                   use_range: bool = False,
                   mss_candles_ago: Optional[int] = None) -> Optional[Dict]:
    """
    Find the most recent FVG in the direction of the MSS.
    Bullish FVG: candle[i-2].high < candle[i].low  (gap up — uses wicks)
    Bearish FVG: candle[i-2].low  > candle[i].high (gap down — uses wicks)

    use_range=True  → displacement measured by full range (high-low) incl. wicks — crypto
    use_range=False → displacement measured by body (close-open) only — NSE default
    """
    try:
        recent = df.tail(lookback + 10).reset_index(drop=True)
        fvgs   = []

        if use_range:
            candle_sizes = (recent['high'] - recent['low']).astype(float)
        else:
            candle_sizes = (recent['close'] - recent['open']).abs().astype(float)

        for i in range(2, len(recent)):
            c0 = recent.iloc[i - 2]
            c1 = recent.iloc[i - 1]    # middle candle — the displacement candle
            c2 = recent.iloc[i]

            prior_avg = float(candle_sizes.iloc[max(0, i - 11):i - 1].mean())
            if use_range:
                mid_size = float(c1['high']) - float(c1['low'])
            else:
                mid_size = abs(float(c1['close']) - float(c1['open']))
            c1_range = float(c1['high']) - float(c1['low'])
            c1_body = abs(float(c1['close']) - float(c1['open']))
            body_ratio = (c1_body / c1_range) if c1_range > 0 else 0.0
            # Tiered body quality — data-driven from 838 NSE backtest trades:
            #   body < 0.45 → wick-dominated, unreliable (skip)
            #   body 0.45–0.65 → PARTIAL displacement (valid, -1 score penalty in scanner)
            #   body ≥ 0.65 → STRONG displacement (full quality, no penalty)
            # Size check remains the primary gate (candle must be larger than avg).
            has_size_disp = prior_avg > 0 and (mid_size >= displacement_mult * prior_avg)
            if body_ratio >= 0.65:
                body_tier = 'STRONG'
            elif body_ratio >= 0.45:
                body_tier = 'PARTIAL'
            else:
                body_tier = 'WEAK'
            has_displacement = has_size_disp and body_tier != 'WEAK'
            candles_ago = len(recent) - 1 - i
            if mss_candles_ago is not None and candles_ago > int(mss_candles_ago):
                continue
            future = recent.iloc[i + 1:]

            if direction == 'BULLISH':
                if float(c0['high']) < float(c2['low']):
                    fvg_low = float(c0['high'])
                    fvg_high = float(c2['low'])
                    if not future.empty and (future['close'].astype(float) <= fvg_low).any():
                        continue
                    size = fvg_high - fvg_low
                    fvgs.append({
                        'fvg_low'       : fvg_low,             # wick high of c0
                        'fvg_high'      : fvg_high,            # wick low  of c2
                        'mid'           : (fvg_low + fvg_high) / 2,
                        'size'          : size,
                        'direction'     : 'BULLISH',
                        'displacement'  : has_displacement,
                        'body_tier'     : body_tier,           # STRONG/PARTIAL/WEAK
                        'mid_candle_sz' : round(mid_size, 2),
                        'avg_candle_sz' : round(prior_avg, 2),
                        'body_ratio'    : round(body_ratio, 3),
                        'candles_ago'   : candles_ago,
                        'mss_bound'     : mss_candles_ago is not None,
                        'mitigated'     : False,
                        'c0_wick_sl'    : float(c0['low']),    # wick LOW of c0 — SL anchor
                    })
            else:
                if float(c0['low']) > float(c2['high']):
                    fvg_low = float(c2['high'])
                    fvg_high = float(c0['low'])
                    if not future.empty and (future['close'].astype(float) >= fvg_high).any():
                        continue
                    size = fvg_high - fvg_low
                    fvgs.append({
                        'fvg_low'       : fvg_low,             # wick high of c2
                        'fvg_high'      : fvg_high,            # wick low  of c0
                        'mid'           : (fvg_low + fvg_high) / 2,
                        'size'          : size,
                        'direction'     : 'BEARISH',
                        'displacement'  : has_displacement,
                        'body_tier'     : body_tier,           # STRONG/PARTIAL/WEAK
                        'mid_candle_sz' : round(mid_size, 2),
                        'avg_candle_sz' : round(prior_avg, 2),
                        'body_ratio'    : round(body_ratio, 3),
                        'candles_ago'   : candles_ago,
                        'mss_bound'     : mss_candles_ago is not None,
                        'mitigated'     : False,
                        'c0_wick_sl'    : float(c0['high']),   # wick HIGH of c0 — SL anchor
                    })

        # Prefer the most recent FVG that has displacement; fall back to any FVG
        displaced = [f for f in fvgs if f.get('displacement')]
        return displaced[-1] if displaced else (fvgs[-1] if fvgs else None)

    except Exception as e:
        logger.warning(f"SB FVG error: {e}")
        return None


# ── Order Block detection ─────────────────────────────────────────────────────

def detect_order_block(df, direction: str, lookback: int = 40) -> Optional[Dict]:
    """
    LuxAlgo-style Order Block using candle wicks (high/low), not body.
    Bull OB: last bearish candle before the displacement that caused the bullish structure break.
    Bear OB: last bullish candle before the displacement that caused the bearish structure break.
    Returns zone dict or None.
    """
    try:
        import pandas as pd
        recent = df.tail(lookback).reset_index(drop=True)
        n = len(recent)
        if n < 5:
            return None

        avg_range = float((recent['high'] - recent['low']).mean())

        if direction == 'BULLISH':
            for i in range(n - 1, 2, -1):
                c = recent.iloc[i]
                if (float(c['close']) > float(c['open']) and
                        (float(c['high']) - float(c['low'])) >= avg_range * 1.2):
                    for j in range(i - 1, 0, -1):
                        prev = recent.iloc[j]
                        if float(prev['close']) < float(prev['open']):
                            return {
                                'ob_high': float(prev['high']),
                                'ob_low' : float(prev['low']),
                                'ob_mid' : (float(prev['high']) + float(prev['low'])) / 2,
                                'type'   : 'BULL_OB',
                            }
                    break
        else:
            for i in range(n - 1, 2, -1):
                c = recent.iloc[i]
                if (float(c['close']) < float(c['open']) and
                        (float(c['high']) - float(c['low'])) >= avg_range * 1.2):
                    for j in range(i - 1, 0, -1):
                        prev = recent.iloc[j]
                        if float(prev['close']) > float(prev['open']):
                            return {
                                'ob_high': float(prev['high']),
                                'ob_low' : float(prev['low']),
                                'ob_mid' : (float(prev['high']) + float(prev['low'])) / 2,
                                'type'   : 'BEAR_OB',
                            }
                    break
        return None
    except Exception as e:
        logger.debug(f"OB detect error: {e}")
        return None


# ── Double OB test detection ──────────────────────────────────────────────────

def detect_double_ob_test(df, ob: Optional[Dict], direction: str,
                           lookback: int = 100) -> bool:
    """
    Returns True when the same OB zone has been touched AND rejected at least
    once before the current test — the 'double rejection' pattern.

    May 19 NIFTY SHORT: OB at 23,781-23,800 rejected on May 18 AND May 19.
    Both rejections confirmed → strong institutional supply/demand.

    BEARISH: prior wick into ob_high zone, then close below ob_low = rejection.
    BULLISH: prior wick into ob_low zone, then close above ob_high = rejection.
    Excludes the last 3 candles (current test in progress).
    """
    try:
        if ob is None:
            return False
        recent = df.tail(lookback).reset_index(drop=True)
        n      = len(recent)
        ob_lo  = ob['ob_low']
        ob_hi  = ob['ob_high']

        for i in range(0, n - 3):   # skip last 3 = current approach
            hi = float(recent['high'].iloc[i])
            lo = float(recent['low'].iloc[i])
            if lo <= ob_hi and hi >= ob_lo:    # wick touched zone
                # Check next 1-4 candles for rejection
                future = recent.iloc[i + 1:i + 5]
                if direction == 'BEARISH':
                    if float(future['close'].min()) < ob_lo:
                        return True
                else:
                    if float(future['close'].max()) > ob_hi:
                        return True
        return False
    except Exception as e:
        logger.debug(f"Double OB test error: {e}")
        return False


# ── Three Bar Reversal detection ──────────────────────────────────────────────

def detect_three_bar_reversal(df, direction: str) -> bool:
    """
    Three Bar Reversal pattern using candle wicks.
    Bull: bar[-3] bearish → bar[-2] lower-low + lower-high bearish → bar[-1] bullish breaks above bar[-3] HIGH.
    Bear: bar[-3] bullish → bar[-2] higher-high + higher-low bullish → bar[-1] bearish breaks below bar[-3] LOW.
    """
    try:
        if len(df) < 3:
            return False
        b0 = df.iloc[-1]
        b1 = df.iloc[-2]
        b2 = df.iloc[-3]

        if direction == 'BULLISH':
            return (
                float(b2['close']) < float(b2['open']) and
                float(b1['low'])   < float(b2['low'])   and
                float(b1['high'])  < float(b2['high'])  and
                float(b1['close']) < float(b1['open'])  and
                float(b0['close']) > float(b0['open'])  and
                float(b0['high'])  > float(b2['high'])
            )
        else:
            return (
                float(b2['close']) > float(b2['open']) and
                float(b1['high'])  > float(b2['high'])  and
                float(b1['low'])   > float(b2['low'])   and
                float(b1['close']) > float(b1['open'])  and
                float(b0['close']) < float(b0['open'])  and
                float(b0['low'])   < float(b2['low'])
            )
    except Exception as e:
        logger.debug(f"3BR detect error: {e}")
        return False


# ── NSE H1 bias ──────────────────────────────────────────────────────────────

def _get_nse_h1_bias(fyers, symbol: str) -> str:
    """
    H1 (60-min) trend bias for NSE via EMA(3) vs EMA(8).

    Mirrors the forex _get_h1_bias pattern. Blocks counter-trend 5m entries:
      BULLISH H1 → only take BULLISH setups on this symbol
      BEARISH H1 → only take BEARISH setups
      RANGING    → allow both, but raise score gate by +1

    Fallback: RANGING (allow trade, stricter gate) when Fyers data unavailable.
    """
    try:
        from scanner.data_fetcher import get_historical_data
        df_h1 = get_historical_data(fyers, symbol, '60', days=10)
        if df_h1 is None or len(df_h1) < 8:
            return 'RANGING'
        c    = df_h1['close'].astype(float)
        fast = float(c.ewm(span=3, adjust=False).mean().iloc[-1])
        slow = float(c.ewm(span=8, adjust=False).mean().iloc[-1])
        if fast > slow * 1.0002:
            return 'BULLISH'
        if fast < slow * 0.9998:
            return 'BEARISH'
        return 'RANGING'
    except Exception:
        return 'RANGING'


_h4_bias_cache: dict = {}   # {symbol: (bias, fetched_epoch)}
_H4_CACHE_TTL = 1800        # 30 min — H4 bar is 4 hours; no need to re-fetch every 3 min


def _get_nse_h4_bias(fyers, symbol: str) -> str:
    """
    H4 (240-min) trend bias for NSE via EMA(3) vs EMA(8).

    Hard gate — no CHoCH override. H4 is the primary trend frame:
      BULLISH H4 → only take BULLISH setups
      BEARISH H4 → only take BEARISH setups
      RANGING    → allow both, but raise score gate by +1 (handled in scoring)

    Results are cached 30 min to avoid excessive Fyers API calls (scanner fires every 3 min).
    Fallback: RANGING when data unavailable.
    """
    import time
    now = time.time()
    cached = _h4_bias_cache.get(symbol)
    if cached and (now - cached[1]) < _H4_CACHE_TTL:
        return cached[0]

    try:
        from scanner.data_fetcher import get_historical_data
        df_h4 = get_historical_data(fyers, symbol, '240', days=20)
        if df_h4 is None or len(df_h4) < 8:
            _h4_bias_cache[symbol] = ('RANGING', now)
            return 'RANGING'
        c    = df_h4['close'].astype(float)
        fast = float(c.ewm(span=3, adjust=False).mean().iloc[-1])
        slow = float(c.ewm(span=8, adjust=False).mean().iloc[-1])
        if fast > slow * 1.0003:
            bias = 'BULLISH'
        elif fast < slow * 0.9997:
            bias = 'BEARISH'
        else:
            bias = 'RANGING'
        _h4_bias_cache[symbol] = (bias, now)
        return bias
    except Exception:
        _h4_bias_cache[symbol] = ('RANGING', now)
        return 'RANGING'


# ── full Silver Bullet scan ───────────────────────────────────────────────────

def scan_silver_bullet(df, symbol: str, tf: str = '5',
                        fyers=None, force: bool = True,
                        max_fvg_pts: float = MAX_FVG_PTS,
                        forex_mode: bool = False) -> Optional[Dict]:
    """
    Complete Silver Bullet setup scan. Window gate removed — scans all market hours.
    Chain: DOL → MSS → FVG → price at/near FVG → trade plan
    """
    try:
        if df is None or len(df) < 30:
            return None

        # Auto-detect forex mode: symbol ends in .x or fyers is None and not NSE symbol
        _is_forex = forex_mode or (
            symbol.endswith('.x') or
            (fyers is None and not symbol.startswith('NSE:') and 'NIFTY' not in symbol.upper())
        )

        # Normalise index to IST DatetimeIndex — required by all time-based helpers.
        # get_historical_data returns RangeIndex + 'timestamp' column; fyers.history()
        # returns DatetimeIndex directly. Both cases handled here once for all.
        import pandas as pd
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'timestamp' in df.columns:
                df = df.copy()
                df.index = pd.to_datetime(df['timestamp'])
            elif 'datetime' in df.columns:
                df = df.copy()
                df.index = pd.to_datetime(df['datetime'])
            elif 'date' in df.columns:
                df = df.copy()
                df.index = pd.to_datetime(df['date'])
        if df.index.tz is None:
            df.index = df.index.tz_localize('Asia/Kolkata')
        else:
            df.index = df.index.tz_convert('Asia/Kolkata')

        in_window, window_name = is_silver_bullet_window()
        if not in_window:
            window_name = 'All Hours'

        # Day context — computed once, used by DOL fallback, scoring, and PM reversal
        gap_bias = get_opening_gap_bias(df)
        day_ext  = get_day_extremes(df)

        # 0. Liquidity Sweep — did smart money grab retail stops at a swing level?
        #    Sweep of highs → expect SHORT (smart money distributed at the top)
        #    Sweep of lows  → expect LONG  (smart money accumulated at the bottom)
        #    Not a hard block — adds +2 to score. Without sweep, setup needs higher
        #    structural score to pass minimum threshold (CHoCH + FVG still required).
        liq_sweep = detect_liquidity_sweep(df, lookback=60, sweep_window=20)

        # 2. Draw on Liquidity — wick_sweep=True catches Judas Swing wick traps.
        #    Fallback: use today's HOD/LOD when no swing high/low qualifies.
        #    HOD above price → buy-side liquidity pool → BULLISH draw.
        #    LOD below price → sell-side liquidity pool → BEARISH draw.
        dol = find_draw_on_liquidity(df, wick_sweep=True)
        if dol is None and day_ext:
            last_c = float(df['close'].iloc[-1])
            if day_ext['high'] > last_c:
                dol = {'type': 'HOD', 'level': day_ext['high'], 'direction': 'BULLISH'}
            elif day_ext['low'] < last_c:
                dol = {'type': 'LOD', 'level': day_ext['low'], 'direction': 'BEARISH'}
        if dol is None:
            logger.info(f"SB skip {symbol}: no DOL — no unswept swing high/low or HOD/LOD")
            return None

        direction = dol['direction']  # 'BULLISH' or 'BEARISH'

        # OI DOL quality — optional boost: 0 (no OI data), 1 (OI spike at swing), 2 (spike + EQH/EQL)
        try:
            from scanner.oi_filters import score_dol_by_oi
            oi_dol_boost, oi_dol_reason = score_dol_by_oi(df, dol)
        except Exception:
            oi_dol_boost, oi_dol_reason = 0.0, "OI_SKIP"

        # 3. MSS matching direction
        mss = detect_sb_mss(df)
        if mss is None:
            logger.info(f"SB skip {symbol}: no MSS detected (need CHoCH or BOS on 3m/5m)")
            return None
        if mss['direction'] != direction:
            logger.info(
                f"SB skip {symbol}: MSS direction {mss['direction']} != DOL direction {direction} "
                f"(DOL={dol.get('type')} @ {dol.get('level')})"
            )
            return None
        mss_type = mss.get('type', 'BOS')   # extracted early — used by H1 CHoCH override

        # Temporal ordering note (NSE path): log when sweep.ca <= mss.ca but do NOT
        # nullify.  Nullification + no gate fix = −4 effective score gap = silent
        # freeze.  detect_sb_fvg already receives mss_candles_ago (line below) which
        # enforces displacement occurred after the MSS — that is the binding gate.
        if liq_sweep is not None and liq_sweep.get('candles_ago', 0) <= mss.get('candles_ago', 0):
            logger.info(
                f"SB {symbol}: temporal order note — "
                f"sweep {liq_sweep['candles_ago']}ca vs MSS {mss['candles_ago']}ca. "
                f"Kept for scoring; FVG mss_candles_ago enforces post-MSS displacement."
            )

        # 4. Opening-range sweep guard — Judas Swing must be complete.
        #    ICT pattern: price fakes ONE direction (Judas), grabs stops, then reverses.
        #    BEARISH trade after BULLISH Judas: OR high was swept → now reversing SHORT.
        #    BULLISH trade after BEARISH Judas: OR low was swept → now reversing LONG.
        #    Allow if EITHER the trade direction OR the opposite direction was swept.
        #    FOREX: bypass — NSE 9:15 IST opening range is irrelevant for Gold/Silver/WTI.
        #           Use liquidity sweep detection instead (detect_liquidity_sweep handles forex).
        if _is_forex:
            or_swept = True   # forex has no NSE opening range — DOL/MSS/FVG chain is enough
            logger.debug(f"SB {symbol}: forex mode — opening range guard bypassed")
        else:
            judas_dir = 'BULLISH' if direction == 'BEARISH' else 'BEARISH'
            or_swept  = opening_range_swept(df, direction) or opening_range_swept(df, judas_dir)
            if not or_swept:
                logger.info(f"SB skip {symbol}: opening range not yet swept (neither direction) — Judas Swing incomplete")
                return None

        # 5. FVG in direction after MSS
        fvg = detect_sb_fvg(df, direction, mss_candles_ago=mss.get('candles_ago'))
        if fvg is None:
            logger.info(
                f"SB skip {symbol}: no {direction} FVG found after MSS "
                f"(MSS {mss.get('candles_ago')}ca ago, type={mss.get('type')})"
            )
            return None
        # FVG displacement quality — tiered, not binary.
        # Data: 838 NSE trades show body<45% is noise, 45-65% is valid with -1 score,
        # >=65% is full institutional displacement.
        _body_tier = fvg.get('body_tier', 'WEAK')
        if _body_tier == 'WEAK':
            logger.info(
                f"SB skip {symbol}: FVG body {fvg.get('body_ratio', 0):.0%} < 45% — "
                f"wick-dominated candle, no institutional displacement"
            )
            return None
        # PARTIAL (45–65%): allowed, score-1 applied below in scoring section
        # STRONG (>=65%):   standard, no penalty

        # Per-index minimum FVG size gate (data-driven: p10 of winning trades).
        # Prevents micro-FVGs that are too small to be meaningful at index level.
        _fvg_min_pts = {'NIFTY': 5.0, 'BANKNIFTY': 15.0, 'FINNIFTY': 10.0, 'MIDCPNIFTY': 6.0}
        _sym_upper = symbol.upper()
        for _idx_key, _min_sz in _fvg_min_pts.items():
            if _idx_key in _sym_upper:
                if fvg.get('size', 0) < _min_sz:
                    logger.info(
                        f"SB skip {symbol}: FVG too small ({fvg.get('size', 0):.1f}pts "
                        f"< {_min_sz:.0f}pt min for {_idx_key})"
                    )
                    return None
                break

        last_close = float(df['close'].iloc[-1])
        last_low   = float(df['low'].iloc[-1])
        last_high  = float(df['high'].iloc[-1])
        fvg_low    = fvg['fvg_low']
        fvg_high   = fvg['fvg_high']
        fvg_mid    = fvg['mid']

        pd_context = premium_discount_context(df, fvg_mid)
        pd_context['aligned'] = premium_discount_aligned(direction, pd_context)
        if not pd_context['aligned']:
            logger.info(
                f"SB skip {symbol}: {direction} FVG in {pd_context.get('zone')} "
                f"(eq={pd_context.get('equilibrium', 0):.2f})"
            )
            return None

        # OI entry confirmation + bid/ask spread check.
        # Both are gracefully optional: no data → pass through unchanged.
        try:
            from scanner.oi_filters import check_oi_entry_filter, check_bidask_filter, get_oi_divergence_signal
            oi_entry_ok, oi_entry_reason = check_oi_entry_filter(df, direction)
            if not oi_entry_ok:
                logger.info(f"SB skip {symbol}: OI declining — {oi_entry_reason}")
                return None
            bidask_ok, bidask_reason = check_bidask_filter(symbol, fvg_low, fvg_high)
            if not bidask_ok:
                logger.info(f"SB skip {symbol}: bid/ask too wide — {bidask_reason}")
                return None
            oi_divergence = get_oi_divergence_signal(df, direction)
        except Exception:
            oi_entry_reason = "OI_SKIP"
            bidask_reason   = "BIDASK_SKIP"
            oi_divergence   = None

        # 6. Price proximity to FVG — two tiers of entry:
        #
        # Tier A — Standard (price INSIDE FVG, wick overlap):
        #   Data: WR 65.2%, AvgR +1.15 (838-trade NSE backtest).
        #   Entry price = current close. Score: +1 for in_fvg.
        #
        # Tier B — Approach entry (price heading toward FVG, within 1× FVG-size of edge):
        #   Data: WR 75.8%, AvgR +3.09 — BETTER than standard. Pre-touch = better price,
        #   more room to T1/T2. Requires score >= 12 (high-confidence only).
        #   Entry price = close. Limit order at FVG edge is ideal but market order valid.
        in_fvg = last_low <= fvg_high and last_high >= fvg_low  # wick overlap into gap
        fvg_size_pts = fvg_high - fvg_low

        if direction == 'BULLISH':
            # Price approaches from above — enters by falling into gap
            approaching = (last_close > fvg_high) and (last_close <= fvg_high + fvg_size_pts)
        else:
            # Price approaches from below — enters by rising into gap
            approaching = (last_close < fvg_low) and (last_close >= fvg_low - fvg_size_pts)

        _approach_entry = False
        if not in_fvg:
            if approaching:
                # Valid approach setup — full score evaluated below, gate applied after scoring
                _approach_entry = True
                logger.info(
                    f"SB {symbol}: APPROACH — price {last_close:.2f} heading toward "
                    f"{direction} FVG {fvg_low:.2f}–{fvg_high:.2f} "
                    f"(within {fvg_size_pts:.1f}pts of edge, scoring now)"
                )
            else:
                logger.info(
                    f"SB skip {symbol}: price not in FVG and not approaching — "
                    f"close={last_close:.2f}, FVG={fvg_low:.2f}–{fvg_high:.2f} ({direction})"
                )
                return None

        # 6b. H1 bias filter — block counter-trend 5m entries (same as forex engine)
        #     CHoCH overrides when it confirms a true structure shift.
        #     RANGING → allow trade but raise score gate by +1 (handled in scoring).
        h1_bias = 'RANGING'
        if fyers is not None:
            h1_bias = _get_nse_h1_bias(fyers, symbol)
        h1_ranging      = (h1_bias == 'RANGING')
        choch_h1_override = (mss_type == 'CHOCH')
        if not h1_ranging and h1_bias != direction and not choch_h1_override:
            logger.info(
                f"SB skip {symbol}: H1 {h1_bias} ≠ {direction} — counter-trend block"
            )
            return None
        if choch_h1_override and h1_bias != direction and not h1_ranging:
            logger.info(
                f"SB {symbol}: CHoCH overrides H1 {h1_bias} — structure already shifted"
            )

        # 6c. H4 bias filter — hard gate, no CHoCH override.
        #     H4 is the primary trend; counter-trend H4 = no trade regardless of setup quality.
        #     RANGING → allow both directions but raise score gate by +1 (handled in scoring).
        h4_bias    = 'RANGING'
        h4_ranging = True
        if fyers is not None:
            h4_bias    = _get_nse_h4_bias(fyers, symbol)
            h4_ranging = (h4_bias == 'RANGING')
            if not h4_ranging and h4_bias != direction:
                logger.info(
                    f"SB skip {symbol}: H4 {h4_bias} != {direction} — counter-trend hard block"
                )
                return None

        # 7. Build trade plan — SL covers the full FVG size (matches backtest logic)
        #    min 2pts so micro-gaps don't produce untradeable risk
        fvg_size = max(fvg.get('size', 1.0), 2.0)

        # Hard cap: reject FVGs wider than max_fvg_pts.
        # Default 50pts for NSE (96.4% of winning trades had SL ≤ 50pts, median 10.7pts).
        # Pass max_fvg_pts=X to override for other instruments.
        if fvg_size > max_fvg_pts:
            logger.info(
                f"FVG too wide: {fvg_size:.1f}pts > {max_fvg_pts:.1f}pt hard cap "
                f"({fvg_low:.1f}–{fvg_high:.1f}) — overnight/structural gap, skip"
            )
            return None

        # ── Regime + DTE-aware target scaling ────────────────────────────────
        regime      = market_regime(df)
        dte         = get_dte(symbol)
        compression = _detect_compression(df)    # computed once, used in CHOPPY block + setup dict

        # CHOPPY hard block — ML: CHOPPY WR=60% PF=3.01 vs TRENDING 69.5% PF=7.76
        # Forex markets trend at lower ADX — use threshold 12 instead of 18
        if _is_forex:
            adx_val = _adx(df)
            if adx_val < 12:
                logger.info(f"SB skip {symbol}: CHOPPY regime (forex ADX={adx_val:.1f} < 12)")
                return None
            # Recalculate regime with forex thresholds
            if adx_val >= 25:
                regime = 'TRENDING'
            elif adx_val >= 12:
                regime = 'NEUTRAL'
            else:
                regime = 'CHOPPY'
        elif regime == 'CHOPPY':
            # Before blocking, check for volatility compression (coiling for breakout).
            # True chop = random noise. Compression = directional energy building.
            # Allow compressed setups: ATR contracting + price near range extreme.
            if compression['is_compressed'] and compression['breakout_ready']:
                logger.info(
                    f"SB {symbol}: CHOPPY but COMPRESSED "
                    f"({compression['compression_pct']:.0f}% ATR, breakout_ready) "
                    f"— allowing with score cap"
                )
                # Compression setups are lower confidence — flag for downstream cap
                pass   # score cap applied after scoring block below
            else:
                logger.info(
                    f"SB skip {symbol}: CHOPPY regime — ADX too low, no compression "
                    f"(ATR={compression['compression_pct']:.0f}%)"
                )
                return None

        m1, m2, m3 = _target_multipliers(dte, regime)
        logger.info(f"SB {symbol}: DTE={dte} regime={regime} → target mults {m1}/{m2}/{m3}")

        # ── Order Block detection (before entry — used to refine entry/SL) ────
        # Reference: NIFTY LONG May 19 2026
        #   FVG entry (mid): 23,606  SL: 23,355  risk: ~250 pts  TP3 R: ~0.9R
        #   OB entry (top) : 23,364  SL: 23,339  risk:  24.75 pts TP3 R: 10.7R
        # Same TP3. 10× better R because entry is at OB top, SL just below OB low.
        # Rule: when OB overlaps FVG → enter at OB edge, not FVG mid.
        ob = detect_order_block(df, direction)
        ob_confluence = False
        ob_in_fvg     = False
        if ob:
            ob_in_fvg     = not (ob['ob_high'] < fvg_low or ob['ob_low'] > fvg_high)
            ob_confluence = True

        # ── Entry + SL — data-driven FVG zone logic ──────────────────────────
        # 838-trade NSE backtest finding:
        #   Approach (pre-touch) : WR 75.8%, AvgR +3.09 → entry = FVG edge (limit)
        #   In-FVG 0-50% fill    : WR ~86%             → entry = last_close (best)
        #   In-FVG 50-75% fill   : WR ~79%             → entry = last_close (OK)
        #   In-FVG 75-100% fill  : WR lower            → score penalty already applied
        #
        # SL always just outside the FVG (not 2× FVG away — old formula was too wide).
        # Risk = entry distance to SL; targets scaled from actual entry price.

        _buf = _fvg_buffer(symbol)

        # FVG fill depth at current price (0% = just touched edge, 100% = fully filled)
        if direction == 'BULLISH':
            if _approach_entry:
                # Limit order at FVG top edge — best price, pre-touch
                entry = round(fvg_high - _buf, 2)
            else:
                # Market order at current price (already inside FVG)
                entry = last_close
            stop_loss = round(fvg_low - _buf, 2)      # just below FVG bottom
            risk = round(entry - stop_loss, 2)
            if risk <= 0:
                return None
            # Fill depth: how far from top edge (0% = touched top, 100% = fully filled)
            _fill_pct = ((fvg_high - entry) / fvg_size_pts * 100) if fvg_size_pts > 0 else 50
            target1  = round(entry + risk * m1, 2)
            target2  = round(entry + risk * m2, 2)
            dol_draw = dol['level']
            _t3_raw  = dol_draw if dol_draw > (entry + risk * m3) else entry + risk * m3
            target3  = round(max(_t3_raw, target2), 2)
            rr       = round((target2 - entry) / risk, 1)
        else:
            if _approach_entry:
                # Limit order at FVG bottom edge — best price, pre-touch
                entry = round(fvg_low + _buf, 2)
            else:
                entry = last_close
            stop_loss = round(fvg_high + _buf, 2)     # just above FVG top
            risk = round(stop_loss - entry, 2)
            if risk <= 0:
                return None
            _fill_pct = ((entry - fvg_low) / fvg_size_pts * 100) if fvg_size_pts > 0 else 50
            target1  = round(entry - risk * m1, 2)
            target2  = round(entry - risk * m2, 2)
            dol_draw = dol['level']
            _t3_raw  = dol_draw if dol_draw < (entry - risk * m3) else entry - risk * m3
            target3  = round(min(_t3_raw, target2), 2)
            rr       = round((entry - target2) / risk, 1)

        # ── UT Bot trend confirmation ─────────────────────────────────────────
        try:
            from scanner.ut_bot import get_ut_signal
            ut = get_ut_signal(df)
            ut['aligned'] = (ut['trend'] == direction)
        except Exception as ue:
            logger.debug(f"UT Bot error: {ue}")
            ut = {'trend': None, 'stop': None, 'signal': None,
                  'bars_in_trend': 0, 'aligned': None}

        # ── Three Bar Reversal ────────────────────────────────────────────────
        three_bar = detect_three_bar_reversal(df, direction)

        # ── Double OB test ────────────────────────────────────────────────────
        # Same OB zone tested and rejected before current test = strong institutional level.
        # e.g. May 19 NIFTY SHORT: supply OB at 23,781-23,800 rejected May 18 + May 19.
        double_ob = detect_double_ob_test(df, ob, direction)

        # ── PM reversal detection ─────────────────────────────────────────────
        # In the afternoon window, if the AM extreme (HOD or LOD) was set before
        # 13:00 IST, the PM session typically reverses FROM that extreme.
        # HOD before 13:00 → expect PM to push DOWN (BEARISH bias).
        # LOD before 13:00 → expect PM to push UP (BULLISH bias).
        pm_reversal     = False
        pm_reversal_dir = None
        if in_window and window_name == 'Afternoon Silver Bullet' and day_ext:
            hod_t = day_ext.get('high_time')
            lod_t = day_ext.get('low_time')
            if hod_t is not None and hod_t.hour < 13:
                pm_reversal     = True
                pm_reversal_dir = 'BEARISH'
            elif lod_t is not None and lod_t.hour < 13:
                pm_reversal     = True
                pm_reversal_dir = 'BULLISH'

        # ── Confluence score ──────────────────────────────────────────────────
        # Max ~29.5: base(5) + CHoCH(2)/BOS(1) + inFVG(1) + displaced(1) + UT(2)
        #            + RR(1) + OB(1) + 3BR(1) + doubleOB(1) + gap_bias(1) + pm_rev(1)
        #            + sweep(2) + EQH/EQL(2) + H1_aligned(1) + H4_aligned(1)
        #            + oi_dol(1/2) + oi_sweep_combo(+1.5) — oi_divergence(-1.5)
        #            [H1_ranging: -1, H4_ranging: -1]
        # Float score: OI combo bonus/penalty uses 0.5 increments.
        # mss_type already extracted after MSS check above — not re-derived here.

        # Liquidity sweep agreement — sweep direction must match trade direction
        sweep_confirmed = (
            liq_sweep is not None and
            liq_sweep['direction'] == direction and
            liq_sweep['candles_ago'] <= 10   # tightened from 15 → 10 (30 min on 3-min bars)
        )

        # ── MANDATORY SWEEP ENFORCEMENT ──────────────────────────────────────
        # A Silver Bullet setup WITHOUT a confirmed liquidity sweep is NOT ICT.
        # No stop hunt = no institutional participation = no edge.
        # Exception: opening_range_swept already confirmed a Judas swing above.
        # If both sweep_confirmed=False AND or_swept covered only one direction,
        # we require the structural sweep to be present.
        if not sweep_confirmed:
            # Opposite-direction sweep: smart money swept the WRONG side — counter-signal
            if liq_sweep is not None and liq_sweep['direction'] != direction:
                logger.info(
                    f"SB skip {symbol}: OPPOSITE sweep detected "
                    f"({liq_sweep['sweep_type']}) — counter-signal, no trade"
                )
            else:
                logger.info(
                    f"SB skip {symbol}: no confirmed liquidity sweep — "
                    f"mandatory for Silver Bullet (ICT requirement)"
                )
            return None

        # ── SWEEP QUALITY GATE ────────────────────────────────────────────────
        # Data-driven minimum by MSS type (838 NSE trades):
        #   CHoCH + sweep_q=6: WR 81.8% — quality 6 is fine for direction flips
        #   BOS   + sweep_q=6: WR 63.6% — NOT enough for continuation patterns
        #   BOS   + sweep_q=7: WR 90.9% — BOS needs a stronger sweep to be valid
        sweep_quality  = score_sweep_quality(liq_sweep, dol)
        _min_sweep_q   = 7 if mss_type == 'BOS' else 6
        if sweep_quality < _min_sweep_q:
            logger.info(
                f"SB skip {symbol}: sweep quality {sweep_quality}/10 < {_min_sweep_q} "
                f"({mss_type} needs q>={_min_sweep_q} — "
                f"wick={abs(liq_sweep.get('wick_extreme', 0) - liq_sweep.get('swept_level', 0)):.1f}pt "
                f"age={liq_sweep.get('candles_ago', 0)}ca)"
            )
            return None

        score = 5.0  # base: DOL + MSS + FVG all present (float — OI bonuses use 0.5 steps)
        if mss_type == 'CHOCH':
            score += 2   # CHoCH = direction flip = strongest MSS signal
        else:
            score += 1   # BOS = continuation, still valid
        if in_fvg:
            score += 1   # price already in FVG zone (Tier A entry)
        # Tier B approach entry: no +1 for in_fvg, score gate enforced after full score
        if fvg.get('displacement'):
            score += 1   # institutional displacement (body >= 45% + size >= avg)
        if _body_tier == 'PARTIAL':
            score -= 1   # body 45–65%: weaker displacement, penalise once

        # ── MSS/BOS/CHoCH gap-based scoring — data-driven from 838 NSE trades ──
        _sym_up      = symbol.upper()
        _mss_ca      = mss.get('candles_ago', 99)   # how many candles ago MSS fired
        _gap_pts     = fvg_size_pts                  # MSS-to-entry gap = FVG size

        # 1. Per-index BOS penalty (BOS WR 60% on BANKNIFTY/FINNIFTY)
        if mss_type == 'BOS' and ('BANKNIFTY' in _sym_up or 'FINNIFTY' in _sym_up):
            score -= 1
            logger.debug(f"SB {symbol}: BOS on {_sym_up} — score-1 (data WR 60%)")

        # 2. CHoCH BUY directional penalty (WR 59.4% vs 69.4% SELL)
        if mss_type == 'CHOCH' and direction == 'BULLISH':
            score -= 1
            logger.debug(f"SB {symbol}: CHoCH BUY — score-1 (data WR 59.4%)")

        # 3. CHoCH freshness bonus — data: fresh CHoCH(score>=13) WR=71.4% vs stale WR=52.6%
        #    candles_ago<=5 = CHoCH fired in last 15 min (3-min bars) — very fresh
        if mss_type == 'CHOCH' and _mss_ca <= 5:
            score += 1
            logger.debug(f"SB {symbol}: CHoCH fresh ({_mss_ca}ca) — score+1")

        # 4. BOS gap size penalty — data: BOS win_med_gap=9.2pts, loss_med_gap=28.4pts
        #    Small gap (<14pts) → WR 92%+. Large gap (>25pts) → WR 73% and declining.
        if mss_type == 'BOS':
            if _gap_pts <= 14:
                score += 1   # tight BOS structure → near the break level
                logger.debug(f"SB {symbol}: BOS tight gap {_gap_pts:.1f}pts — score+1")
            elif _gap_pts > 25:
                score -= 1   # wide BOS gap → entered far from structure
                logger.debug(f"SB {symbol}: BOS wide gap {_gap_pts:.1f}pts — score-1")

        # 5. Per-index optimal FVG gap range (from winning trade p75 analysis)
        #    Gap beyond p75 of winners = entering in a zone where losers dominate
        _idx_max_gap = {
            'NIFTY': 15, 'BANKNIFTY': 31, 'FINNIFTY': 39, 'MIDCPNIFTY': 16
        }
        for _ik, _max_g in _idx_max_gap.items():
            if _ik in _sym_up and _gap_pts > _max_g:
                score -= 1
                logger.debug(
                    f"SB {symbol}: FVG gap {_gap_pts:.1f}pts > {_max_g}pt "
                    f"{_ik} optimal zone — score-1"
                )
                break
        # FVG fill depth penalty — data: deep fills win less (838 NSE trades)
        #   0-50%  fill: no penalty (price in upper half of FVG — best zone)
        #   50-75% fill: -1 (lower half of FVG, closer to SL)
        #   75%+   fill: -2 (nearly fully filled, marginal setup)
        if in_fvg and not _approach_entry:
            if _fill_pct > 75:
                score -= 2
                logger.info(
                    f"SB {symbol}: FVG deep fill {_fill_pct:.0f}% — score-2 "
                    f"(price deep in gap, higher reversal risk)"
                )
            elif _fill_pct > 50:
                score -= 1
                logger.debug(f"SB {symbol}: FVG fill {_fill_pct:.0f}% — score-1")
        if ut.get('aligned'):
            score += 2   # UT Bot trend agrees — powerful confluence
        if rr >= 3.0:
            score += 1
        if fvg['size'] / fvg_mid > 0.0008:
            score += 1
        if ob_confluence:
            score += 1   # Order Block present = institutional supply/demand zone
        if three_bar:
            score += 1   # Three Bar Reversal = confirmed turn at FVG
        if double_ob:
            score += 1   # Same OB/supply zone rejected before — double confirmation
        if gap_bias and gap_bias == direction:
            score += 1   # opening gap bias confirms trade direction
        if pm_reversal and pm_reversal_dir == direction:
            score += 1   # PM session reversal from AM extreme confirms direction
        # Sweep is now mandatory — score bonus reflects QUALITY not presence
        # sweep_quality already >= 6 at this point (hard gate above)
        if sweep_quality >= 9:
            score += 3   # institutional sweep: fresh + large wick + external level
        elif sweep_quality >= 7:
            score += 2   # strong/medium sweep
        else:
            score += 1   # minimum quality (6) — weak but valid
        if dol.get('is_eqh_eql'):
            score += 2   # DOL is an EQH/EQL cluster — denser stops, higher probability sweep
        if oi_dol_boost > 0:
            score += oi_dol_boost        # OI spike at DOL: +1 single swing, +2 EQH/EQL cluster
        if sweep_confirmed and oi_dol_boost > 0:
            score += 1.5                 # OI spike coincides with confirmed sweep = institutional trap
            logger.debug(
                "SB %s: OI+sweep combo bonus +1.5 (sweep_confirmed=True, oi_dol_boost=%.1f)",
                symbol, oi_dol_boost,
            )
        if oi_divergence == "DIVERGENCE":
            score -= 1.5                 # Price moving but OI falling = short-cover/long-liquidation
            logger.info(
                "SB %s: OI divergence penalty -1.5 (%s move, declining OI) — setup weight reduced",
                symbol, direction,
            )
        if h1_bias == direction:
            score += 1   # H1 trend agrees — not fighting the HTF
        elif h1_ranging:
            score -= 1   # RANGING H1 → weaker confirmation, raise effective gate by 1
        if h4_bias == direction:
            score += 1   # H4 primary trend aligned — highest confidence
        elif h4_ranging:
            score -= 1   # RANGING H4 → trend unclear, require stronger setup

        # Compression mode score cap: coiling setups have lower max confidence
        if regime == 'CHOPPY' and compression.get('is_compressed'):
            score = min(score, 11.0)

        # ── Expiry day adjustment ─────────────────────────────────────────────
        # Expiry Thursday: gamma explosion distorts sweeps and FVGs.
        # Required minimum score raised by 2; risk multiplier halved downstream.
        from data.news_calendar import is_expiry_thursday
        _is_expiry = is_expiry_thursday()
        if _is_expiry:
            logger.info(f"SB {symbol}: EXPIRY DAY — score gate +2, risk 50%")

        # Approach entry gate — MSS type + regime aware (from 838-trade analysis):
        #
        #   BOS + TRENDING + approach : WR 87.5%  → gate = 10  (best setup in dataset)
        #   CHoCH + approach          : WR 64.2%  → gate = 12  (no early-entry benefit)
        #   CHOPPY + approach         : WR 42.9%  → BLOCK      (actively harmful)
        #   All other regimes         : gate = 12
        if _approach_entry:
            _app_regime = regime if regime else 'UNKNOWN'
            if _app_regime == 'CHOPPY':
                logger.info(
                    f"SB skip {symbol}: APPROACH blocked in CHOPPY regime "
                    f"(data: WR 42.9% — worse than random)"
                )
                return None
            # Score gate: BOS+TRENDING = 10, everything else = 12
            _app_gate = 10 if (mss_type == 'BOS' and _app_regime == 'TRENDING') else 12
            if score < _app_gate:
                logger.info(
                    f"SB skip {symbol}: APPROACH entry score {score:.1f} < {_app_gate} "
                    f"(mss={mss_type}, regime={_app_regime})"
                )
                return None
            logger.info(
                f"SB {symbol}: APPROACH entry CONFIRMED — "
                f"score={score:.1f}≥{_app_gate} mss={mss_type} regime={_app_regime} "
                f"{direction} FVG {fvg_low:.1f}–{fvg_high:.1f} "
                f"entry@{entry:.1f} (limit at FVG edge)"
            )

        setup = {
            'symbol'          : symbol,
            'direction'       : direction,
            'window'          : window_name,
            'confluence'      : score,
            'is_expiry_day'   : _is_expiry,
            'sweep_quality'   : sweep_quality,
            'compression'     : compression,
            'in_fvg'          : in_fvg,
            'approach_entry'  : _approach_entry,
            'fvg_body_tier'   : _body_tier,
            'fvg_fill_pct'    : round(_fill_pct, 1),
            'entry_type'      : 'APPROACH' if _approach_entry else f'IN_FVG_{_fill_pct:.0f}pct',
            'dol'             : dol,
            'dol_is_eqh_eql'  : dol.get('is_eqh_eql', False),
            'dol_cluster_count': dol.get('cluster_count', 1),
            'h1_bias'         : h1_bias,
            'h4_bias'         : h4_bias,
            'mss'             : mss,
            'mss_type'        : mss_type,
            'fvg'             : fvg,
            'premium_discount': pd_context,
            'ut_bot'          : ut,
            'ob'              : ob,
            'ob_confluence'   : ob_confluence,
            'ob_in_fvg'       : ob_in_fvg,
            'three_bar'       : three_bar,
            'double_ob_test'  : double_ob,
            'gap_bias'        : gap_bias,
            'day_extremes'    : day_ext,
            'pm_reversal'     : pm_reversal,
            'pm_reversal_dir' : pm_reversal_dir,
            'liq_sweep'       : liq_sweep,
            'sweep_confirmed' : sweep_confirmed,
            'oi_dol_boost'    : oi_dol_boost,
            'oi_dol_reason'   : oi_dol_reason,
            'oi_entry_reason' : oi_entry_reason,
            'bidask_reason'   : bidask_reason,
            'oi_divergence'   : oi_divergence,
            'setup_type'      : 'SILVER_BULLET',
            'dte'             : dte,
            'regime'          : regime,
            'entry_signal': {
                'entry'    : entry,
                'stop_loss': stop_loss,
                'target1'  : target1,
                'target2'  : target2,
                'target3'  : target3,
                'risk'     : risk,
                'rr_ratio' : rr,
                'fvg_low'  : round(fvg_low, 2),
                'fvg_high' : round(fvg_high, 2),
                'dol_level': round(dol['level'], 2),
                'mss_level': round(mss['level'], 2),
            },
        }

        # Auto-select option strike when fyers session is available
        if fyers is not None:
            try:
                from scanner.option_strike_selector import select_option_for_setup
                strike_info = select_option_for_setup(fyers, setup, last_close)
                if strike_info:
                    setup['option_symbol'] = strike_info['symbol']
                    setup['option_info']   = strike_info
            except Exception as oe:
                logger.debug(f"Option strike selector error: {oe}")

        # Optional NSE options intelligence layer. Disabled by default and fail-safe:
        # if Sensibull/source data is unavailable, the setup continues unchanged.
        try:
            from nse_options import enrich_setup_with_options_context
            setup = enrich_setup_with_options_context(setup)
        except Exception as _oe:
            logger.debug(f"Options context enrichment skipped: {_oe}")

        # Telemetry: record that a valid setup was produced for this symbol.
        try:
            from data.live_session_monitor import get_monitor
            get_monitor().record_signal(symbol)
        except Exception:
            pass

        try:
            log_scanner_outcome('nse', 'nse_silver_bullet_scanner', symbol, setup, outcome='SCANNER_PASS')
        except Exception:
            pass
        return setup

    except Exception as e:
        logger.error(f"Silver Bullet scan error {symbol}: {e}")
        try:
            log_scanner_outcome('nse', 'nse_silver_bullet_scanner', symbol, None, outcome='SCANNER_FAIL', reason='exception')
        except Exception:
            pass
        return None


# ── bulk scanner for a list of dataframes ────────────────────────────────────

def run_silver_bullet_scanner(all_data: Dict, tf: str = '5',
                               fyers=None, force: bool = False) -> List[Dict]:
    """
    Run Silver Bullet scan across multiple symbols.
    `all_data` = {symbol: df} dict (same format as existing scanners).
    Pass `fyers` to enable automatic option strike selection per setup.
    Pass `force=True` to skip the window-time check (manual equity scan).
    """
    setups = []
    for symbol, df in all_data.items():
        setup = scan_silver_bullet(df, symbol, tf=tf, fyers=fyers, force=force)
        if setup:
            setups.append(setup)
    return setups


# ── Telegram alert formatter ──────────────────────────────────────────────────

def format_sb_alert(setup: Dict, tf: str = '5') -> str:
    sym        = setup['symbol'].replace('NSE:', '').replace('-EQ', '').replace('-INDEX', '')
    sig        = setup['entry_signal']
    score      = setup.get('confluence', 0)
    direction  = setup['direction']
    window     = setup.get('window', 'Silver Bullet')
    dol        = setup.get('dol', {})
    mss_type   = setup.get('mss_type', 'BOS')
    ut         = setup.get('ut_bot', {})
    ob         = setup.get('ob')
    three_bar  = setup.get('three_bar', False)
    dte        = setup.get('dte', 99)
    regime     = setup.get('regime', 'NEUTRAL')

    gap_bias      = setup.get('gap_bias')
    pm_reversal   = setup.get('pm_reversal', False)
    pm_rev_dir    = setup.get('pm_reversal_dir')
    day_ext       = setup.get('day_extremes') or {}

    dol_eqh    = setup.get('dol_is_eqh_eql', False)
    dol_cnt    = setup.get('dol_cluster_count', 1)
    h1_bias    = setup.get('h1_bias', 'N/A')
    h4_bias    = setup.get('h4_bias', 'N/A')
    dol_info   = setup.get('dol', {})

    dir_label  = 'BUY (LONG)' if direction == 'BULLISH' else 'SELL (SHORT)'
    opt_type   = 'CE (Call)'  if direction == 'BULLISH' else 'PE (Put)'
    mss_label  = f'{mss_type} ⚡' if mss_type == 'CHOCH' else mss_type
    h1_label   = ('✅' if h1_bias == direction else ('⚠️' if h1_bias == 'RANGING' else '❌')) + f' {h1_bias}'
    h4_label   = ('✅' if h4_bias == direction else ('⚠️' if h4_bias == 'RANGING' else '❌')) + f' {h4_bias}'
    dol_label  = (f'✅ {dol_info.get("type","?")} @ {dol_info.get("level","?")} '
                  f'[EQH/EQL ×{dol_cnt}]' if dol_eqh
                  else f'{dol_info.get("type","?")} @ {dol_info.get("level","?")} (single swing)')

    ut_trend   = ut.get('trend', '?')
    ut_stop    = ut.get('stop', '?')
    ut_bars    = ut.get('bars_in_trend', 0)
    ut_aligned = ut.get('aligned')
    ut_label   = (f"{'✅' if ut_aligned else '⚠️'} {ut_trend} | Stop: {ut_stop} | {ut_bars} bars")

    double_ob  = setup.get('double_ob_test', False)
    ob_label   = f"{ob['ob_low']} – {ob['ob_high']} ✅" if ob else 'None'
    if ob and double_ob:
        ob_label += '  🔁 DOUBLE REJECTION'
    tbr_label  = 'YES ✅' if three_bar else 'No'

    # Gap bias label
    if gap_bias:
        gap_ok     = gap_bias == direction
        gap_label  = f"{'✅' if gap_ok else '⚠️'} {gap_bias} (gap {'up' if gap_bias == 'BEARISH' else 'down'} + rejection)"
    else:
        gap_label  = 'None (flat open)'

    # PM reversal label
    if pm_reversal:
        pm_ok     = pm_rev_dir == direction
        hod       = day_ext.get('high', '?')
        lod       = day_ext.get('low', '?')
        ext_level = hod if pm_rev_dir == 'BEARISH' else lod
        pm_label  = f"{'✅' if pm_ok else '⚠️'} {pm_rev_dir} reversal from AM {'HOD' if pm_rev_dir == 'BEARISH' else 'LOD'} ({ext_level})"
    else:
        pm_label  = 'No AM extreme set before 13:00'

    # Liquidity sweep label
    liq_sweep       = setup.get('liq_sweep')
    sweep_confirmed = setup.get('sweep_confirmed', False)
    if sweep_confirmed and liq_sweep:
        s_type = 'LOW swept (longs protected)' if liq_sweep['sweep_type'] == 'LOW_SWEEP' else 'HIGH swept (shorts trapped)'
        sweep_label = (
            f"✅ {s_type}\n"
            f"           Level: {liq_sweep['swept_level']:.2f}  "
            f"| Wick: {liq_sweep['wick_extreme']:.2f}  "
            f"| {liq_sweep['candles_ago']} candles ago"
        )
    elif liq_sweep and liq_sweep['direction'] != direction:
        sweep_label = f"⚠️ Sweep detected ({liq_sweep['sweep_type']}) but OPPOSITE direction — weak setup"
    else:
        sweep_label = 'None detected — no stop hunt confirmed yet'

    # Option info — flag next-expiry trades clearly
    option_info   = setup.get('option_info', {}) or {}
    is_next_exp   = option_info.get('next_expiry_trade', False)
    opt_sym       = option_info.get('symbol', f'ITM/ATM {opt_type}')
    opt_ltp       = option_info.get('ltp', '')
    opt_delta     = option_info.get('delta', '')
    opt_exp       = option_info.get('expiry', '')
    opt_label     = opt_sym
    if is_next_exp:
        opt_label = f"{opt_sym}  ⚠️ NEXT EXPIRY (current too theta-decayed)"
    opt_detail    = f"LTP: {opt_ltp}  Delta: {opt_delta:.3f}  Exp: {opt_exp}" if opt_ltp else f"Delta 0.50–0.65"

    regime_emoji  = {'TRENDING': '📈', 'NEUTRAL': '➡️', 'CHOPPY': '〰️'}.get(regime, '➡️')
    dte_label     = 'EXPIRY TODAY' if dte == 0 else f'{dte} DTE'

    # OB precision entry reference — shown when OB sits inside FVG
    ob_ref_line = ''
    if setup.get('ob_confluence') and setup.get('ob_in_fvg') and ob:
        ob_e = ob['ob_high'] if setup['direction'] == 'BULLISH' else ob['ob_low']
        ob_s = round(ob['ob_low'] - _fvg_buffer(symbol), 2) if setup['direction'] == 'BULLISH' \
               else round(ob['ob_high'] + _fvg_buffer(symbol), 2)
        ob_r = round(abs(ob_e - ob_s), 2)
        ob_ref_line = (
            f"\n⚡ OB PRECISION ENTRY (manual):\n"
            f"   Enter : {ob_e}  SL: {ob_s}  Risk: {ob_r} pts\n"
            f"   (10× tighter SL — take only if OB clear on chart)"
        )

    return (
        f"CB6 SETUP — {sym}  [{score}/26]\n\n"
        f"Window    : {window}\n"
        f"Direction : {dir_label}\n"
        f"H4 Bias   : {h4_label}\n"
        f"H1 Bias   : {h1_label}\n"
        f"Market    : {regime_emoji} {regime}  |  {dte_label}\n\n"
        f"--- STRUCTURE ---\n"
        f"Liq Sweep : {sweep_label}\n"
        f"DOL       : {dol_label}\n"
        f"MSS       : {sig['mss_level']} ({mss_label})\n"
        f"FVG Zone  : {sig['fvg_low']} – {sig['fvg_high']}\n"
        f"FVG       : {'IN ZONE ✅' if setup.get('in_fvg') else 'APPROACHING'}\n"
        f"OB Zone   : {ob_label}\n"
        f"3-Bar Rev : {tbr_label}\n"
        f"UT Bot    : {ut_label}\n"
        f"Gap Bias  : {gap_label}\n"
        f"PM Rev    : {pm_label}\n\n"
        f"--- TRADE PLAN (FVG entry — bot executes) ---\n"
        f"Entry     : {sig['entry']}\n"
        f"SL        : {sig['stop_loss']}\n"
        f"T1 (1/3)  : {sig['target1']}\n"
        f"T2 (1/3)  : {sig['target2']}\n"
        f"T3 (1/3)  : {sig['target3']}\n"
        f"Risk      : {sig['risk']} pts  |  RR 1:{sig['rr_ratio']}"
        f"{ob_ref_line}\n\n"
        f"--- OPTION ---\n"
        f"Contract  : {opt_label}\n"
        f"           {opt_detail}\n"
        f"Mode      : Paper Trade"
    )
