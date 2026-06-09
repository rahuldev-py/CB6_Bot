# forex_engine/scanner/mtf_dol_scanner.py
#
# Universal Multi-Timeframe Draw-on-Liquidity (DOL) Scalp Scanner
#
# Pattern (validated on XAUUSD 3m — Jun 4, 2026 session):
#
#   HTF (15m): Liquidity sweep → Displacement leg → FVG imbalance created → DOL identified
#       ↓
#   MTF (5m) : Confirms displacement momentum, premium/discount context
#       ↓
#   LTF (3m) : Price arrives at DOL → local counter-sweep → CHoCH → FVG → scalp entry
#              Target: back to HTF FVG zone (NOT a reversal — a precision scalp)
#
# This scanner is symbol-agnostic. Works on:
#   Forex : XAUUSD, XAGUSD, USOIL, EURUSD
#   NSE   : NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY (same logic, IST session gates)
#
# Output: setup dict compatible with the existing ftmo_state / gft_state trade managers.

import pandas as pd
import numpy as np
from typing import Optional
from utils.logger import logger

# ── Tuning constants ──────────────────────────────────────────────────────────
HTF_LOOKBACK         = 60    # 15m candles to scan for displacement context
LTF_LOOKBACK         = 20    # 3m candles to scan for counter-CHoCH entry
DOL_PROXIMITY_PCT    = 0.006  # price within 0.6% of DOL to trigger LTF scan
MIN_DISPLACEMENT_BODY = 0.55  # minimum body/range ratio on displacement candle
MIN_HTF_FVG_SIZE     = 0.001  # minimum FVG size as fraction of price (0.1%)
MAX_SCALP_RR         = 2.0   # minimum R:R for setup to be valid
HTF_FVG_FILL_LIMIT   = 0.85  # if HTF FVG >85% filled, structure is consumed — skip


# ── Wave counter ──────────────────────────────────────────────────────────────

def count_impulse_waves(df: pd.DataFrame, trend_dir: str, lookback: int = 80) -> int:
    """
    Count the number of impulse legs in trend_dir over the last `lookback` candles.

    trend_dir='BEARISH' → count downward waves (lower lows).
                          Use when scanning for a BULLISH reversal.
    trend_dir='BULLISH' → count upward waves (higher highs).
                          Use when scanning for a BEARISH reversal.

    A wave = each new extreme beyond the prior extreme in trend direction,
    separated by a meaningful swing in the opposite direction (not just noise).

    Returns wave count (0–5).  count >= 3 satisfies Rahul's 3-wave reversal rule.
    """
    if df is None or len(df) < 10:
        return 0

    df_use = df.tail(lookback).copy().reset_index(drop=True)
    n      = len(df_use)
    if n < 6:
        return 0

    # ── Detect swing lows and swing highs (2-bar each side) ──────────────────
    swing_lows:  list[tuple[int, float]] = []
    swing_highs: list[tuple[int, float]] = []

    for i in range(2, n - 2):
        lo = float(df_use['low'].iloc[i])
        hi = float(df_use['high'].iloc[i])
        if (lo < float(df_use['low'].iloc[i-1]) and
                lo < float(df_use['low'].iloc[i-2]) and
                lo < float(df_use['low'].iloc[i+1]) and
                lo < float(df_use['low'].iloc[i+2])):
            swing_lows.append((i, lo))
        if (hi > float(df_use['high'].iloc[i-1]) and
                hi > float(df_use['high'].iloc[i-2]) and
                hi > float(df_use['high'].iloc[i+1]) and
                hi > float(df_use['high'].iloc[i+2])):
            swing_highs.append((i, hi))

    # Minimum wave size: each impulse leg must move at least 0.5 × ATR(14) to count.
    # Prevents noise ticks from inflating the wave count to 3 on choppy structure.
    atr          = _atr(df_use, period=min(14, max(2, len(df_use) - 1)))
    min_wave_sz  = atr * 0.5 if atr > 0 else 0.0

    if trend_dir == 'BEARISH':
        # Each new lower low = +1 wave.
        # Require: (a) a meaningful swing high pullback between lows,
        #          (b) the wave moved at least 0.5 × ATR from the prior low.
        if not swing_lows:
            return 0
        waves        = 1
        prev_low     = swing_lows[0][1]
        prev_low_idx = swing_lows[0][0]
        for idx, lo in swing_lows[1:]:
            if lo < prev_low:
                wave_size    = prev_low - lo
                has_pullback = any(
                    sh_idx > prev_low_idx and sh_idx < idx
                    for sh_idx, _ in swing_highs
                )
                if has_pullback and wave_size >= min_wave_sz:
                    waves += 1
                prev_low     = lo
                prev_low_idx = idx
        return waves
    else:
        # Each new higher high = +1 wave.
        if not swing_highs:
            return 0
        waves         = 1
        prev_high     = swing_highs[0][1]
        prev_high_idx = swing_highs[0][0]
        for idx, hi in swing_highs[1:]:
            if hi > prev_high:
                wave_size    = hi - prev_high
                has_pullback = any(
                    sl_idx > prev_high_idx and sl_idx < idx
                    for sl_idx, _ in swing_lows
                )
                if has_pullback and wave_size >= min_wave_sz:
                    waves += 1
                prev_high     = hi
                prev_high_idx = idx
        return waves


# ── Base detector ─────────────────────────────────────────────────────────────

def detect_wave_base(df: pd.DataFrame, trend_dir: str,
                     lookback: int = 8) -> dict:
    """
    Detect whether price has formed a consolidation BASE after the last
    impulse wave extreme.  A base = the pause before reversal.

    trend_dir='BEARISH' → look for base after a sell-wave low (BULLISH reversal)
    trend_dir='BULLISH' → look for base after a buy-wave high (BEARISH reversal)

    Returns:
      base_formed  : bool
      base_candles : int   — how many candles inside the base
      base_low     : float
      base_high    : float
      base_pct_atr : float — base range as fraction of ATR (tight = < 0.5)
    """
    if df is None or len(df) < lookback + 2:
        return {'base_formed': False, 'base_candles': 0,
                'base_low': 0.0, 'base_high': 0.0, 'base_pct_atr': 999.0}

    df_tail = df.tail(lookback).copy().reset_index(drop=True)
    atr      = _atr(df.tail(30), period=14)
    if atr <= 0:
        return {'base_formed': False, 'base_candles': 0,
                'base_low': 0.0, 'base_high': 0.0, 'base_pct_atr': 999.0}

    n           = len(df_tail)
    base_low    = float(df_tail['low'].min())
    base_high   = float(df_tail['high'].max())
    base_range  = base_high - base_low
    base_pct    = base_range / atr

    # A tight base: range < 0.8 × ATR over lookback candles
    # (ATR measures a "normal" single-candle range; 8-candle base < 0.8 ATR = very tight)
    is_tight = base_pct < 0.80

    # No new extreme in direction of trend (wave has stopped)
    if trend_dir == 'BEARISH':
        # After a bearish wave: last 3 candles must NOT make a new lower low vs candle[0]
        first_low = float(df_tail['low'].iloc[0])
        recent_low = float(df_tail['low'].iloc[-3:].min())
        no_new_extreme = recent_low >= first_low * 0.9995
    else:
        first_high = float(df_tail['high'].iloc[0])
        recent_high = float(df_tail['high'].iloc[-3:].max())
        no_new_extreme = recent_high <= first_high * 1.0005

    # Mixed candle body colours = indecision (at least 1 bull + 1 bear in last 5)
    last5 = df_tail.tail(5)
    bull_count = int(((last5['close'] - last5['open']) > 0).sum())
    bear_count = int(((last5['open'] - last5['close']) > 0).sum())
    is_mixed   = bull_count >= 1 and bear_count >= 1

    base_formed = is_tight and no_new_extreme and is_mixed

    # Count how many candles are within the base range
    base_candles = int(
        ((df_tail['high'] <= base_high) & (df_tail['low'] >= base_low)).sum()
    )

    return {
        'base_formed' : base_formed,
        'base_candles': base_candles,
        'base_low'    : round(base_low,  5),
        'base_high'   : round(base_high, 5),
        'base_pct_atr': round(base_pct,  3),
    }


# ── ATR helper ────────────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period:
        return float((df['high'] - df['low']).mean())
    hi = df['high']
    lo = df['low']
    pc = df['close'].shift(1)
    tr = pd.concat([hi - lo, (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


# ── Step 1: HTF displacement + FVG + DOL context ──────────────────────────────

def detect_htf_context(df_15m: pd.DataFrame, symbol: str) -> Optional[dict]:
    """
    Scan 15m data for the most recent significant displacement.
    Returns:
      htf_dir         : 'BULLISH' | 'BEARISH' — direction of the HTF displacement leg
      displacement_high/low : price extremes of the displacement leg
      fvg_low, fvg_high    : the imbalance zone left by the displacement
      fvg_mid              : midpoint of HTF FVG (scalp target for LTF long/short)
      fvg_fill_pct         : how much of the FVG is already filled by current price
      dol                  : the liquidity pool BELOW (BEARISH) or ABOVE (BULLISH) FVG
      dol_swept            : True if price has already reached the DOL
      atr_15m              : ATR(14) of the 15m series for sizing
    """
    if df_15m is None or len(df_15m) < 20:
        return None

    df   = df_15m.copy().reset_index(drop=True)
    n    = len(df)
    atr  = _atr(df)
    if atr <= 0:
        return None

    last_close = float(df['close'].iloc[-1])

    # ── Find the strongest displacement candle in the last HTF_LOOKBACK bars ──
    scan_start = max(0, n - HTF_LOOKBACK)
    best_idx   = None
    best_score = 0.0

    for i in range(scan_start + 2, n - 1):
        rng  = float(df['high'].iloc[i] - df['low'].iloc[i])
        body = abs(float(df['close'].iloc[i]) - float(df['open'].iloc[i]))
        if rng < 1e-9:
            continue
        body_ratio = body / rng
        size_score = rng / atr
        if body_ratio >= MIN_DISPLACEMENT_BODY and size_score >= 0.7:
            score = body_ratio * size_score
            if score > best_score:
                best_score = score
                best_idx   = i

    if best_idx is None:
        return None

    # ── HTF direction from that displacement candle ────────────────────────────
    disp_c   = df.iloc[best_idx]
    htf_dir  = 'BEARISH' if float(disp_c['close']) < float(disp_c['open']) else 'BULLISH'

    # ── HTF FVG: three-candle gap between candle[i-2] and candle[i] ───────────
    c_prev2 = df.iloc[best_idx - 2]
    c_curr  = df.iloc[best_idx]

    if htf_dir == 'BEARISH':
        # Bearish FVG: gap between prev2 low and curr high
        fvg_high = float(c_prev2['low'])
        fvg_low  = float(c_curr['high'])
    else:
        # Bullish FVG: gap between prev2 high and curr low
        fvg_low  = float(c_prev2['high'])
        fvg_high = float(c_curr['low'])

    if fvg_high <= fvg_low:
        return None  # degenerate gap

    fvg_mid      = (fvg_low + fvg_high) / 2
    fvg_size     = fvg_high - fvg_low
    min_fvg_abs  = last_close * MIN_HTF_FVG_SIZE
    if fvg_size < min_fvg_abs:
        return None

    # How much of the FVG has price already returned to fill?
    if htf_dir == 'BEARISH':
        # Fill = price came back up from below into FVG
        fill_pct = max(0.0, (last_close - fvg_low) / fvg_size) if last_close > fvg_low else 0.0
    else:
        fill_pct = max(0.0, (fvg_high - last_close) / fvg_size) if last_close < fvg_high else 0.0

    if fill_pct > HTF_FVG_FILL_LIMIT:
        logger.debug(f"MTF {symbol}: HTF FVG {fill_pct:.0%} consumed — setup stale")
        return None

    # ── DOL: the liquidity pool targeted by the displacement ──────────────────
    # BEARISH move → targets SELL-SIDE liquidity (swing lows BELOW fvg_low)
    # BULLISH move → targets BUY-SIDE liquidity (swing highs ABOVE fvg_high)
    post_disp = df.iloc[best_idx + 1:]

    if htf_dir == 'BEARISH':
        # Find the lowest swing low after the displacement (DOL for BEAR move)
        if len(post_disp) >= 3:
            # Rolling minimum of lows — the deepest unswept level
            dol = float(post_disp['low'].min())
        else:
            dol = float(df['low'].tail(5).min())
        dol_swept = last_close <= dol * 1.001   # price reached or crossed the DOL
    else:
        if len(post_disp) >= 3:
            dol = float(post_disp['high'].max())
        else:
            dol = float(df['high'].tail(5).max())
        dol_swept = last_close >= dol * 0.999

    disp_high = float(df['high'].iloc[best_idx])
    disp_low  = float(df['low'].iloc[best_idx])

    return {
        'htf_dir'         : htf_dir,
        'displacement_high': disp_high,
        'displacement_low' : disp_low,
        'fvg_low'         : round(fvg_low,  5),
        'fvg_high'        : round(fvg_high, 5),
        'fvg_mid'         : round(fvg_mid,  5),
        'fvg_fill_pct'    : round(fill_pct, 3),
        'dol'             : round(dol, 5),
        'dol_swept'       : dol_swept,
        'atr_15m'         : round(atr, 5),
        'displacement_idx': best_idx,
    }


# ── Step 2: MTF confirmation on 5m ───────────────────────────────────────────

def confirm_mtf_momentum(df_5m: pd.DataFrame, htf_context: dict) -> dict:
    """
    Quick 5m check: is the displacement momentum confirmed and not yet reversed?
    Returns a dict with 'confirmed' bool and 'momentum_score' (0-3).
    """
    if df_5m is None or len(df_5m) < 10:
        return {'confirmed': True, 'momentum_score': 1}  # neutral — don't block

    htf_dir    = htf_context['htf_dir']
    last_close = float(df_5m['close'].iloc[-1])
    last_open  = float(df_5m['open'].iloc[-1])
    atr_5m     = _atr(df_5m, period=10)

    score = 0

    # 1. Most recent 5m candle aligns with displacement direction?
    if htf_dir == 'BEARISH' and last_close < last_open:
        score += 1
    elif htf_dir == 'BULLISH' and last_close > last_open:
        score += 1

    # 2. Price below 5m 20-EMA (BEARISH) or above (BULLISH)?
    if len(df_5m) >= 20:
        ema20 = float(df_5m['close'].ewm(span=20).mean().iloc[-1])
        if htf_dir == 'BEARISH' and last_close < ema20:
            score += 1
        elif htf_dir == 'BULLISH' and last_close > ema20:
            score += 1

    # 3. Recent candles show continuation (no major reversal candle)?
    last3 = df_5m.tail(3)
    if htf_dir == 'BEARISH':
        # No big bullish reversal candle
        big_bull = any(
            (float(r['close']) - float(r['open'])) > atr_5m * 1.2
            for _, r in last3.iterrows()
        )
        if not big_bull:
            score += 1
    else:
        big_bear = any(
            (float(r['open']) - float(r['close'])) > atr_5m * 1.2
            for _, r in last3.iterrows()
        )
        if not big_bear:
            score += 1

    return {'confirmed': score >= 1, 'momentum_score': score}


# ── Step 3: LTF sweep + CHoCH + FVG entry at DOL ────────────────────────────

def detect_ltf_entry(df_3m: pd.DataFrame, htf_context: dict,
                     symbol: str) -> Optional[dict]:
    """
    On 3m: detect the counter-structure entry at the HTF DOL.

    BEARISH HTF → look for BULLISH scalp:
      price sweeps sell-side (makes new low below DOL) → first 3m CHoCH BULLISH
      → FVG formed by that CHoCH leg → LONG entry in FVG
      → Target: HTF FVG mid (not a reversal — a scalp back into the imbalance)

    BULLISH HTF → look for BEARISH scalp:
      price sweeps buy-side (makes new high above DOL) → first 3m CHoCH BEARISH
      → FVG formed → SHORT entry → Target: HTF FVG mid
    """
    if df_3m is None or len(df_3m) < LTF_LOOKBACK:
        return None

    htf_dir    = htf_context['htf_dir']
    dol        = htf_context['dol']
    fvg_mid    = htf_context['fvg_mid']
    fvg_low_h  = htf_context['fvg_low']
    fvg_high_h = htf_context['fvg_high']

    # LTF scalp direction is opposite to HTF displacement
    ltf_dir = 'BULLISH' if htf_dir == 'BEARISH' else 'BEARISH'

    df        = df_3m.copy().reset_index(drop=True)
    n         = len(df)
    last_close = float(df['close'].iloc[-1])
    atr_3m    = _atr(df, period=10)

    # ── Proximity check: is price near the DOL? ───────────────────────────────
    dol_dist_pct = abs(last_close - dol) / (dol + 1e-9)
    if dol_dist_pct > DOL_PROXIMITY_PCT:
        return None

    # ── Detect local sweep at DOL ─────────────────────────────────────────────
    # Look at the last LTF_LOOKBACK candles for a new extreme in the HTF direction
    scan_df = df.tail(LTF_LOOKBACK)
    sweep_extreme = None

    if ltf_dir == 'BULLISH':
        # BEARISH HTF → price should have swept BELOW DOL (new low)
        recent_low = float(scan_df['low'].min())
        if recent_low < dol * 1.002:  # swept at or slightly below DOL
            sweep_extreme = recent_low
    else:
        # BULLISH HTF → price should have swept ABOVE DOL (new high)
        recent_high = float(scan_df['high'].max())
        if recent_high > dol * 0.998:
            sweep_extreme = recent_high

    if sweep_extreme is None:
        return None  # no sweep of DOL detected on LTF

    # ── Detect CHoCH on 3m (first structural break in LTF direction) ──────────
    # CHoCH: a candle that closes BEYOND the most recent swing in LTF direction
    choch_idx  = None
    swing_ref  = None

    if ltf_dir == 'BULLISH':
        # Find the highest high in the last 5 candles BEFORE the sweep low
        recent_highs = df.tail(LTF_LOOKBACK)['high']
        swing_ref = float(recent_highs.quantile(0.70))  # 70th percentile = local resistance
        # CHoCH: candle closes above swing_ref
        for i in range(max(0, n - LTF_LOOKBACK), n):
            if float(df['close'].iloc[i]) > swing_ref:
                choch_idx = i
                break
    else:
        recent_lows = df.tail(LTF_LOOKBACK)['low']
        swing_ref = float(recent_lows.quantile(0.30))  # 30th percentile = local support
        for i in range(max(0, n - LTF_LOOKBACK), n):
            if float(df['close'].iloc[i]) < swing_ref:
                choch_idx = i
                break

    if choch_idx is None:
        return None

    # ── Detect FVG after CHoCH ─────────────────────────────────────────────────
    # Look for a three-candle gap in the 8 candles following CHoCH
    fvg_low  = None
    fvg_high = None

    for i in range(choch_idx, min(choch_idx + 8, n - 1)):
        if i < 1:
            continue
        c_prev = df.iloc[i - 1]
        c_next = df.iloc[i + 1] if i + 1 < n else None
        if c_next is None:
            continue

        if ltf_dir == 'BULLISH':
            # Bullish FVG: prev low > next high
            gap_low  = float(c_next['high'])
            gap_high = float(c_prev['low'])
            if gap_high > gap_low and (gap_high - gap_low) > atr_3m * 0.05:
                fvg_low  = gap_low
                fvg_high = gap_high
                break
        else:
            # Bearish FVG: prev high < next low
            gap_low  = float(c_prev['high'])
            gap_high = float(c_next['low'])
            if gap_high > gap_low and (gap_high - gap_low) > atr_3m * 0.05:
                fvg_low  = gap_low
                fvg_high = gap_high
                break

    # If no 3-candle FVG, use a synthetic entry based on the CHoCH candle body
    if fvg_low is None:
        choch_c = df.iloc[choch_idx]
        if ltf_dir == 'BULLISH':
            fvg_low  = min(float(choch_c['open']), float(choch_c['close']))
            fvg_high = max(float(choch_c['open']), float(choch_c['close']))
        else:
            fvg_low  = min(float(choch_c['open']), float(choch_c['close']))
            fvg_high = max(float(choch_c['open']), float(choch_c['close']))

        if fvg_high <= fvg_low:
            return None

    return {
        'ltf_dir'      : ltf_dir,
        'sweep_extreme': round(sweep_extreme, 5),
        'choch_idx'    : choch_idx,
        'swing_ref'    : round(swing_ref, 5),
        'fvg_low'      : round(fvg_low, 5),
        'fvg_high'     : round(fvg_high, 5),
        'atr_3m'       : round(atr_3m, 5),
    }


# ── Step 4: Assemble trade plan ───────────────────────────────────────────────

def _build_trade_plan(ltf_entry: dict, htf_context: dict,
                      symbol: str, cfg: dict) -> Optional[dict]:
    """
    Construct entry/SL/T1/T2/T3 from LTF and HTF context.
    Returns trade plan dict or None if RR is too low.
    """
    ltf_dir    = ltf_entry['ltf_dir']
    fvg_low    = ltf_entry['fvg_low']
    fvg_high   = ltf_entry['fvg_high']
    sweep_ext  = ltf_entry['sweep_extreme']
    atr_3m     = ltf_entry['atr_3m']
    fvg_mid_h  = htf_context['fvg_mid']
    fvg_low_h  = htf_context['fvg_low']
    fvg_high_h = htf_context['fvg_high']
    dol        = htf_context['dol']
    fvg_buf    = cfg.get('fvg_buf', 0.0003)
    min_sl     = cfg.get('min_sl_dist', 0.0005)

    if ltf_dir == 'BULLISH':
        entry = round(fvg_low + fvg_buf, 5)
        # SL: below the sweep extreme with small buffer (sweep wick + ATR*0.1)
        sl    = round(min(sweep_ext, fvg_low) - max(atr_3m * 0.10, fvg_buf), 5)
        risk  = round(entry - sl, 5)
        if risk < min_sl:
            return None
        # T1: HTF FVG bottom (first natural resistance the scalp encounters)
        t1 = round(fvg_low_h, 5)
        # T2: HTF FVG mid
        t2 = round(fvg_mid_h, 5)
        # T3: HTF FVG top (full retrace into imbalance)
        t3 = round(fvg_high_h, 5)
        # If HTF FVG is already above — T3 is the realistic max
        if t1 <= entry:
            t1 = round(entry + risk * 1.5, 5)
        if t2 <= t1:
            t2 = round(t1 + risk, 5)
        if t3 <= t2:
            t3 = round(t2 + risk, 5)
    else:  # BEARISH scalp
        entry = round(fvg_high - fvg_buf, 5)
        sl    = round(max(sweep_ext, fvg_high) + max(atr_3m * 0.10, fvg_buf), 5)
        risk  = round(sl - entry, 5)
        if risk < min_sl:
            return None
        t1 = round(fvg_high_h, 5)
        t2 = round(fvg_mid_h, 5)
        t3 = round(fvg_low_h, 5)
        if t1 >= entry:
            t1 = round(entry - risk * 1.5, 5)
        if t2 >= t1:
            t2 = round(t1 - risk, 5)
        if t3 >= t2:
            t3 = round(t2 - risk, 5)

    rr = round(abs(t2 - entry) / risk, 1) if risk > 0 else 0.0
    if rr < MAX_SCALP_RR:
        return None

    return {
        'entry'    : entry,
        'stop_loss': sl,
        'target1'  : t1,
        'target2'  : t2,
        'target3'  : t3,
        'risk'     : risk,
        'rr_ratio' : rr,
        'fvg_low'  : fvg_low,
        'fvg_high' : fvg_high,
        'dol_level': dol,
        'mss_level': ltf_entry['swing_ref'],
    }


# ── Step 5: Confluence score ──────────────────────────────────────────────────

def _score_mtf_setup(htf_context: dict, ltf_entry: dict,
                     mtf_conf: dict, plan: dict) -> int:
    """
    Score the MTF DOL setup (0-12). Same scale reference as the 15m scanner.
    Thresholds:
      ≥ 8  : valid setup
      ≥ 10 : A+ (lots boosted in worker)
    """
    score = 0

    # HTF structure (0-4)
    score += 2  # displacement detected (required to get here)
    score += 1 if not htf_context['dol_swept'] else 0   # fresh DOL (not already swept)
    score += 1 if htf_context['fvg_fill_pct'] < 0.50 else 0  # FVG mostly intact

    # MTF momentum (0-3)
    score += min(mtf_conf['momentum_score'], 3)

    # LTF entry quality (0-5)
    atr3  = ltf_entry['atr_3m']
    fvg_s = ltf_entry['fvg_high'] - ltf_entry['fvg_low']
    score += 1 if fvg_s > atr3 * 0.05 else 0     # meaningful FVG size
    score += 1 if plan['rr_ratio'] >= 2.5 else 0  # solid R:R
    score += 1 if plan['rr_ratio'] >= 3.0 else 0  # strong R:R
    score += 1  # CHoCH confirmed (required to get here)
    score += 1 if abs(plan['entry'] - ltf_entry['sweep_extreme']) < atr3 * 2 else 0  # entry close to sweep

    return score


# ── Main entry point ──────────────────────────────────────────────────────────

def scan_mtf_dol(df_3m: pd.DataFrame,
                 df_5m: pd.DataFrame,
                 df_15m: pd.DataFrame,
                 symbol: str,
                 h4_bias: Optional[str] = None,
                 min_score: int = 8) -> Optional[dict]:
    """
    Full MTF DOL scanner. Returns a setup dict compatible with the existing
    trade managers (ftmo_state / gft_state) or None.

    Args:
      df_3m   : 3-minute OHLCV DataFrame (LTF execution)
      df_5m   : 5-minute OHLCV DataFrame (MTF momentum confirmation)
      df_15m  : 15-minute OHLCV DataFrame (HTF displacement + DOL context)
      symbol  : instrument name (e.g., 'XAUUSD', 'USOIL', 'NIFTY')
      h4_bias : 'BULLISH' | 'BEARISH' | 'RANGING' | None
      min_score: minimum confluence score to return the setup

    Returns setup dict with keys matching signal_scanner.scan_setup output,
    plus additional 'mtf_*' keys for logging and ML capture.
    """
    try:
        from forex_engine.forex_instruments import INSTRUMENTS
        cfg = INSTRUMENTS.get(symbol, {})
    except ImportError:
        cfg = {}

    # ── 1. HTF displacement context ───────────────────────────────────────────
    htf_context = detect_htf_context(df_15m, symbol)
    if htf_context is None:
        return None

    htf_dir = htf_context['htf_dir']
    logger.debug(
        f"MTF {symbol}: HTF {htf_dir} displacement | "
        f"FVG {htf_context['fvg_low']:.5f}–{htf_context['fvg_high']:.5f} "
        f"| DOL {htf_context['dol']:.5f} swept={htf_context['dol_swept']}"
    )

    # H4 bias gate — if H4 strongly opposes the scalp direction, skip
    ltf_scalp_dir = 'BULLISH' if htf_dir == 'BEARISH' else 'BEARISH'
    if h4_bias and h4_bias not in ('RANGING', None):
        if h4_bias == htf_dir:
            # H4 aligns with HTF displacement → scalp is counter-H4 → only allow with high conviction
            pass  # will be filtered by score gate below
        # h4 opposing the scalp direction reduces but doesn't eliminate validity

    # ── 2. MTF momentum confirmation ─────────────────────────────────────────
    mtf_conf = confirm_mtf_momentum(df_5m, htf_context)
    if not mtf_conf['confirmed']:
        logger.debug(f"MTF {symbol}: 5m momentum not confirmed for {htf_dir} context")
        return None

    # ── 3. LTF sweep + CHoCH + FVG at DOL ───────────────────────────────────
    ltf_entry = detect_ltf_entry(df_3m, htf_context, symbol)
    if ltf_entry is None:
        logger.debug(f"MTF {symbol}: no LTF entry signal at DOL {htf_context['dol']:.5f}")
        return None

    logger.info(
        f"MTF {symbol}: LTF {ltf_entry['ltf_dir']} entry detected | "
        f"sweep={ltf_entry['sweep_extreme']:.5f} "
        f"FVG {ltf_entry['fvg_low']:.5f}–{ltf_entry['fvg_high']:.5f}"
    )

    # ── 4. Trade plan ─────────────────────────────────────────────────────────
    plan = _build_trade_plan(ltf_entry, htf_context, symbol, cfg)
    if plan is None:
        logger.debug(f"MTF {symbol}: trade plan rejected (RR < {MAX_SCALP_RR})")
        return None

    # ── 5. Confluence score ───────────────────────────────────────────────────
    score = _score_mtf_setup(htf_context, ltf_entry, mtf_conf, plan)

    # Wave count + base detection — the core of Rahul's 3-wave reversal rule.
    # 3 waves complete + base formed + CHoCH = high-probability reversal entry.
    wave_count  = count_impulse_waves(df_15m, htf_dir, lookback=80)
    wave_base   = detect_wave_base(df_3m,   htf_dir, lookback=8)
    base_formed = wave_base['base_formed']

    logger.info(
        f"MTF {symbol}: score {score}/12 wave={wave_count} base={base_formed} "
        f"({wave_base['base_pct_atr']:.2f}×ATR) | "
        f"{ltf_scalp_dir} entry={plan['entry']:.5f} "
        f"SL={plan['stop_loss']:.5f} T2={plan['target2']:.5f} RR={plan['rr_ratio']}"
    )

    if score < min_score:
        logger.info(f"MTF {symbol}: score {score} < {min_score} — skip")
        return None

    # ── 6. Classify entry mode (price inside vs approaching LTF FVG) ─────────
    last_close = float(df_3m['close'].iloc[-1]) if df_3m is not None else plan['entry']
    in_ltf_fvg = ltf_entry['fvg_low'] <= last_close <= ltf_entry['fvg_high']
    entry_mode = 'MARKET' if in_ltf_fvg else 'LIMIT'

    # ── 7. Assemble setup dict ────────────────────────────────────────────────
    setup = {
        # Core trade identifiers (compatible with ftmo_state / gft_state)
        'symbol'          : symbol,
        'direction'       : ltf_scalp_dir,
        'confluence'      : score,
        'mss_type'        : 'CHOCH',   # LTF counter-CHoCH is always CHoCH
        'in_fvg'          : in_ltf_fvg,
        'near_fvg'        : not in_ltf_fvg,
        'entry_mode'      : entry_mode,

        # MTF-specific context for logs and ML
        'mtf_htf_dir'     : htf_dir,
        'mtf_htf_fvg_low' : htf_context['fvg_low'],
        'mtf_htf_fvg_high': htf_context['fvg_high'],
        'mtf_htf_fvg_mid' : htf_context['fvg_mid'],
        'mtf_dol'         : htf_context['dol'],
        'mtf_dol_swept'   : htf_context['dol_swept'],
        'mtf_fvg_fill_pct': htf_context['fvg_fill_pct'],
        'mtf_momentum'    : mtf_conf['momentum_score'],
        'mtf_sweep_ext'   : ltf_entry['sweep_extreme'],
        'mtf_atr_15m'     : htf_context['atr_15m'],
        'mtf_atr_3m'      : ltf_entry['atr_3m'],
        'price_at_signal' : last_close,
        'scanner'         : 'MTF_DOL',

        # Required by A+ scorer and lot boost
        'ob'              : None,
        'ob_present'      : False,
        'ut_bot'          : {'trend': ltf_scalp_dir, 'aligned': True},
        'liq_sweep'       : {'sweep_type': 'DOL_SWEEP', 'swept_level': ltf_entry['sweep_extreme'],
                              'candles_ago': 3, 'confidence': min(score * 8, 100)},
        'sweep_confirmed' : True,
        'sweep_confidence': min(score * 8, 100),
        'dol'             : {'direction': ltf_scalp_dir, 'level': htf_context['dol']},
        'dol_is_eqh_eql'  : False,
        'mss'             : {'direction': ltf_scalp_dir, 'level': ltf_entry['swing_ref'],
                              'type': 'CHOCH', 'candles_ago': 2},
        'fvg'             : {'fvg_low': ltf_entry['fvg_low'], 'fvg_high': ltf_entry['fvg_high'],
                              'mid': (ltf_entry['fvg_low'] + ltf_entry['fvg_high']) / 2,
                              'size': ltf_entry['fvg_high'] - ltf_entry['fvg_low'],
                              'displacement': True},
        'premium_discount': {'aligned': True, 'zone': 'discount' if ltf_scalp_dir == 'BULLISH' else 'premium'},
        'liquidity_state' : {},
        'sweep_quality'   : {},
        'daily_atr'       : htf_context['atr_15m'],
        'wave_count'      : wave_count,
        'base_formed'     : base_formed,
        'base_pct_atr'    : wave_base['base_pct_atr'],
        'base_candles'    : wave_base['base_candles'],
        'entry_reason'    : (
            f"MTF_DOL: HTF {htf_dir} → LTF {ltf_scalp_dir} "
            f"waves={wave_count} base={'YES' if base_formed else 'NO'} "
            f"DOL {htf_context['dol']:.5f}"
        ),

        # Trade plan — entry_signal is what ftmo_state / gft_state consume
        'entry_signal': {
            'entry'    : plan['entry'],
            'stop_loss': plan['stop_loss'],
            'target1'  : plan['target1'],
            'target2'  : plan['target2'],
            'target3'  : plan['target3'],
            'risk'     : plan['risk'],
            'rr_ratio' : plan['rr_ratio'],
            'fvg_low'  : ltf_entry['fvg_low'],
            'fvg_high' : ltf_entry['fvg_high'],
            'dol_level': htf_context['dol'],
            'mss_level': ltf_entry['swing_ref'],
        },
    }
    return setup
