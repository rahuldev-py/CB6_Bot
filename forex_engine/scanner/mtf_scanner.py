# forex_engine/scanner/mtf_scanner.py
# Multi-timeframe cascade scanner — catches setups the 3m scanner misses.
#
# Cascade chain: 1H CHoCH → 15m BOS (optional) → 5m FVG → price at FVG
# Works for both Forex (MT5 connector) and NSE (Fyers/TrueData fetcher).
#
# H4 BEARISH + cascade BULLISH = counter-trend: half size, T1 only (baked in).
# H4 aligned = full size, all targets.

from typing import Optional, Callable
import pandas as pd

from utils.logger import logger
from forex_engine.forex_instruments import INSTRUMENTS

# Minimum candle counts required per TF for reliable detection
_MIN_CANDLES = {'1h': 25, '15m': 35, '5m': 50}

# Granular confirmation chain: 45m → 30m → 15m → 5m → 2m → 1m
# (tf, fetch_count, mss_lookback)
_GRANULAR_TFS = [
    ('45m', 30, 20),
    ('30m', 40, 25),
    ('15m', 50, 30),
    ('5m',  60, 40),
    ('2m',  60, 40),
    ('1m',  60, 40),
]
_GRANULAR_MIN = {'45m': 15, '30m': 20, '15m': 25, '5m': 35, '2m': 30, '1m': 30}


def scan_mtf_cascade(
    connector,
    symbol: str,
    h4_bias: Optional[str] = None,
    min_rr: float = 2.0,
) -> Optional[dict]:
    """
    MTF cascade for Forex engines (MT5 connector).
    Called when the primary 3m scan_setup() returns None.
    """
    try:
        df_1h  = connector.get_klines(symbol, '1h',  40)
        df_15m = connector.get_klines(symbol, '15m', 60)
        df_5m  = connector.get_klines(symbol, '5m',  80)
        return _run_cascade(symbol, df_1h, df_15m, df_5m, h4_bias, min_rr, source='MTF-Forex')
    except Exception as exc:
        logger.debug(f"MTF {symbol}: cascade error — {exc}")
        return None


def scan_mtf_cascade_nse(
    fetch_fn: Callable,
    symbol: str,
    h4_bias: Optional[str] = None,
    min_rr: float = 2.0,
) -> Optional[dict]:
    """
    MTF cascade for NSE engine (Fyers/TrueData fetcher).
    fetch_fn: callable(symbol, timeframe_str, days) → DataFrame
    Timeframe strings: '60' = 1H, '15' = 15m, '5' = 5m (Fyers/TrueData format).
    """
    try:
        df_1h  = fetch_fn(symbol, '60', 5)
        df_15m = fetch_fn(symbol, '15', 3)
        df_5m  = fetch_fn(symbol, '5',  2)
        return _run_cascade(symbol, df_1h, df_15m, df_5m, h4_bias, min_rr, source='MTF-NSE')
    except Exception as exc:
        logger.debug(f"MTF-NSE {symbol}: cascade error — {exc}")
        return None


def _run_cascade(
    symbol: str,
    df_1h: Optional[pd.DataFrame],
    df_15m: Optional[pd.DataFrame],
    df_5m: Optional[pd.DataFrame],
    h4_bias: Optional[str],
    min_rr: float,
    source: str,
) -> Optional[dict]:
    """
    Core cascade logic — shared by Forex and NSE paths.
    All DataFrames must have columns: open, high, low, close (+ optionally volume).
    """
    from scanner.silver_bullet import detect_sb_mss, detect_sb_fvg

    cfg = INSTRUMENTS.get(symbol, {})

    # ── Validate candle data ─────────────────────────────────────────────────
    if df_1h is None or len(df_1h) < _MIN_CANDLES['1h']:
        logger.debug(f"{source} {symbol}: insufficient 1H candles ({len(df_1h) if df_1h is not None else 0})")
        return None
    if df_15m is None or len(df_15m) < _MIN_CANDLES['15m']:
        logger.debug(f"{source} {symbol}: insufficient 15m candles ({len(df_15m) if df_15m is not None else 0})")
        return None
    if df_5m is None or len(df_5m) < _MIN_CANDLES['5m']:
        logger.debug(f"{source} {symbol}: insufficient 5m candles ({len(df_5m) if df_5m is not None else 0})")
        return None

    # ── Step 1: 1H CHoCH/BOS — sets trade direction ──────────────────────────
    mss_1h = detect_sb_mss(df_1h, lookback=20)
    if mss_1h is None:
        logger.debug(f"{source} {symbol}: no 1H MSS — cascade skip")
        return None

    direction = mss_1h['direction']
    logger.info(
        f"{source} {symbol}: 1H {mss_1h.get('type','MSS')} {direction} "
        f"@ {mss_1h['level']:.5f} ({mss_1h['candles_ago']}ca)"
    )

    # ── Step 2: H4 alignment ─────────────────────────────────────────────────
    # XAUUSD: H4 counter-trend = half size, T1 only (baked in here).
    # XAGUSD/USOIL: H4 is informational — no block, log only.
    _is_gold = 'XAUUSD' in symbol.upper()
    h4_aligned = h4_bias in ('RANGING', direction) if h4_bias else True

    if not h4_aligned and _is_gold:
        logger.info(
            f"{source} {symbol}: XAUUSD counter-H4 ({h4_bias} vs {direction}) "
            f"— MTF cascade allowed, half size T1-only"
        )
    elif not h4_aligned:
        logger.info(
            f"{source} {symbol}: H4={h4_bias} vs {direction} "
            f"(informational only for {symbol}, cascade proceeds)"
        )

    # ── Step 3: 15m BOS/CHoCH — momentum confirmation (optional) ───────────
    mss_15m = detect_sb_mss(df_15m, lookback=30)
    has_15m  = mss_15m is not None and mss_15m['direction'] == direction
    if has_15m:
        logger.info(
            f"{source} {symbol}: 15m {mss_15m.get('type','MSS')} {direction} confirmed "
            f"({mss_15m['candles_ago']}ca)"
        )
    else:
        logger.info(f"{source} {symbol}: no 15m {direction} confirmation (cascade continues)")

    # ── Step 4: 5m MSS in same direction — must match ────────────────────────
    mss_5m = detect_sb_mss(df_5m, lookback=40)
    if mss_5m is None or mss_5m['direction'] != direction:
        _5m_dir = mss_5m['direction'] if mss_5m else 'None'
        logger.info(f"{source} {symbol}: 5m MSS={_5m_dir} != {direction} — cascade skip")
        return None

    logger.info(
        f"{source} {symbol}: 5m {mss_5m.get('type','MSS')} {direction} "
        f"@ {mss_5m['level']:.5f} ({mss_5m['candles_ago']}ca)"
    )

    # ── Step 5: 5m FVG after the 5m MSS ─────────────────────────────────────
    min_sl  = cfg.get('min_sl_dist', 0.001)
    min_fvg = cfg.get('min_fvg_size', min_sl * 0.5)

    fvg = detect_sb_fvg(
        df_5m, direction,
        lookback=20,
        displacement_mult=1.0,
        use_range=True,
        mss_candles_ago=mss_5m['candles_ago'],
    )
    if fvg is None:
        logger.info(f"{source} {symbol}: no 5m {direction} FVG after MSS — cascade skip")
        return None
    if fvg.get('size', 0) < min_fvg:
        logger.info(
            f"{source} {symbol}: 5m FVG too small "
            f"({fvg.get('size',0):.5f} < {min_fvg:.5f}) — cascade skip"
        )
        return None
    if not fvg.get('displacement'):
        logger.info(f"{source} {symbol}: 5m FVG weak displacement — cascade skip")
        return None

    fvg_low  = fvg['fvg_low']
    fvg_high = fvg['fvg_high']
    logger.info(
        f"{source} {symbol}: 5m FVG {direction} "
        f"[{fvg_low:.5f}–{fvg_high:.5f}] size={fvg.get('size',0):.5f}"
    )

    # ── Step 6: Price gate — LTP must be touching or approaching FVG ─────────
    last_close = float(df_5m['close'].iloc[-1])
    last_high  = float(df_5m['high'].iloc[-1])
    last_low   = float(df_5m['low'].iloc[-1])
    fvg_mid    = fvg['mid']

    in_fvg   = last_low <= fvg_high and last_high >= fvg_low
    near_pct = abs(last_close - fvg_mid) / (fvg_mid + 1e-9)
    near_fvg = near_pct <= 0.005   # within 0.5% of FVG midpoint

    # Directional near-FVG: only approaching from the correct side
    if direction == 'BULLISH':
        near_fvg = near_fvg and last_close <= fvg_high
    else:
        near_fvg = near_fvg and last_close >= fvg_low

    if not (in_fvg or near_fvg):
        logger.info(
            f"{source} {symbol}: price not at 5m FVG "
            f"(close={last_close:.5f} FVG=[{fvg_low:.5f}–{fvg_high:.5f}]) — cascade skip"
        )
        return None

    # ── Step 7: Trade plan from 5m FVG ───────────────────────────────────────
    fvg_buf  = cfg.get('fvg_buf', 0.0003)
    fvg_size = max(fvg.get('size', min_sl), min_sl)

    if direction == 'BULLISH':
        entry = round(fvg_low + fvg_buf, 5)
        sl    = round(fvg_low - fvg_size, 5)
        risk  = round(entry - sl, 5)
        if risk <= 0:
            return None
        t1 = round(entry + risk * 2.0, 5)
        t2 = round(entry + risk * 3.0, 5)
        t3 = round(entry + risk * 4.0, 5)
        rr = round((t2 - entry) / risk, 1)
    else:
        entry = round(fvg_high - fvg_buf, 5)
        sl    = round(fvg_high + fvg_size, 5)
        risk  = round(sl - entry, 5)
        if risk <= 0:
            return None
        t1 = round(entry - risk * 2.0, 5)
        t2 = round(entry - risk * 3.0, 5)
        t3 = round(entry - risk * 4.0, 5)
        rr = round((entry - t2) / risk, 1)

    if rr < min_rr:
        logger.info(f"{source} {symbol}: MTF RR {rr} < {min_rr} — cascade skip")
        return None

    # ── Step 8: Score ─────────────────────────────────────────────────────────
    score = 10                        # 1H CHoCH + 5m FVG = confirmed base
    if has_15m:      score += 3       # 15m momentum adds confidence
    if h4_aligned:   score += 5       # H4 aligned = highest quality
    if mss_1h.get('type') == 'CHoCH': score += 2  # reversal > continuation

    cascade_tfs = ['1H', '15m', '5m'] if has_15m else ['1H', '5m']

    # Counter-H4 XAUUSD: half size, T1 only (already enforced in cascade)
    size_mult = 0.5 if (_is_gold and not h4_aligned) else 1.0
    t1_only   = _is_gold and not h4_aligned

    logger.info(
        f"{source} {symbol}: CASCADE {direction}  "
        f"entry={entry:.5f} SL={sl:.5f} risk={risk:.5f}  "
        f"T1={t1:.5f} T2={t2:.5f} RR={rr}  "
        f"TFs={cascade_tfs} score={score}  "
        f"{'HALF-SIZE T1-only (counter-H4)' if t1_only else 'FULL SIZE'}"
    )

    return {
        'symbol'         : symbol,
        'direction'      : direction,
        'mtf_cascade'    : True,
        'h4_aligned'     : h4_aligned,
        'cascade_tfs'    : cascade_tfs,
        'score'          : score,
        'mss_type'       : mss_1h.get('type', 'CHoCH'),
        'sweep_confirmed': False,
        'wave_count'     : 0,
        'base_formed'    : False,
        'risk_mode'      : 'normal',
        'lot_boost'      : 1.0,
        'size_multiplier': size_mult,
        't1_only'        : t1_only,
        'reversal_3wave' : False,
        'entry_reason'   : (
            f"{source} {'+'.join(cascade_tfs)} "
            f"{'counter-H4 half-size' if t1_only else 'aligned'}"
        ),
        'confluence'     : {
            'score'          : score,
            'dol_agrees'     : False,
            'sweep_confirmed': False,
            'ob_present'     : False,
            'mss_type'       : mss_1h.get('type'),
            'mtf_cascade'    : True,
            'cascade_tfs'    : cascade_tfs,
            'h4_aligned'     : h4_aligned,
        },
        'entry_signal'   : {
            'entry'    : entry,
            'stop_loss': sl,
            'target1'  : t1,
            'target2'  : t2,
            'target3'  : t3,
            'rr_ratio' : rr,
            'fvg_low'  : fvg_low,
            'fvg_high' : fvg_high,
            'risk_usd' : 0.0,
        },
    }


def granular_mtf_confirm(
    connector,
    symbol: str,
    direction: str,
    h4_bias: Optional[str],
) -> dict:
    """
    6-TF entry confirmation: 45m → 30m → 15m → 5m → 2m → 1m.

    H4 is a hard pre-gate for XAUUSD: counter-H4 LONG/SHORT = BLOCKED.
    For other symbols H4 is informational only.

    Scoring:
        ≥4/6 TFs aligned → FULL_SIZE  (1.0× or 0.5× if counter-H4 non-gold)
        3/6              → REDUCED_SIZE (0.5×)
        2/6              → T1_ONLY      (0.25×)
        <2               → BLOCKED

    Returns dict: confirmed, score, action, size_multiplier, t1_only,
                  tf_results, blocking_reason
    """
    from scanner.silver_bullet import detect_sb_mss

    def _blocked(reason: str) -> dict:
        return {
            'confirmed': False, 'score': 0, 'action': 'BLOCKED',
            'size_multiplier': 0.0, 't1_only': False,
            'tf_results': {}, 'blocking_reason': reason,
        }

    _is_gold   = 'XAUUSD' in symbol.upper()
    h4_aligned = h4_bias in ('RANGING', direction) if h4_bias else True

    # H4 mandatory gate for XAUUSD (both LONG and SHORT must align)
    if _is_gold and not h4_aligned:
        logger.info(
            f"GRANULAR {symbol}: BLOCKED — H4={h4_bias} counter-trend "
            f"vs {direction} (XAUUSD H4 filter mandatory)"
        )
        return _blocked(f"H4={h4_bias} counter-trend — XAUUSD H4 filter mandatory")

    tf_results: dict = {}
    aligned    = 0

    for tf, n_candles, lookback in _GRANULAR_TFS:
        try:
            df = connector.get_klines(symbol, tf, n_candles)
            if df is None or len(df) < _GRANULAR_MIN.get(tf, 20):
                tf_results[tf] = {'status': 'NO_DATA', 'aligned': False}
                logger.debug(f"GRANULAR {symbol} {tf}: no data")
                continue

            mss = detect_sb_mss(df, lookback=lookback)
            if mss is None:
                tf_results[tf] = {'status': 'NO_MSS', 'aligned': False}
                logger.debug(f"GRANULAR {symbol} {tf}: no MSS")
                continue

            ok = mss['direction'] == direction
            tf_results[tf] = {
                'status'     : 'ALIGNED' if ok else 'OPPOSING',
                'aligned'    : ok,
                'mss_type'   : mss.get('type', 'BOS'),
                'level'      : round(float(mss.get('level', 0)), 5),
                'candles_ago': mss.get('candles_ago'),
            }
            if ok:
                aligned += 1
                logger.info(
                    f"GRANULAR {symbol} {tf}: {mss.get('type','MSS')} {direction} "
                    f"@ {mss['level']:.5f} ({mss['candles_ago']}ca) ✓"
                )
            else:
                logger.info(
                    f"GRANULAR {symbol} {tf}: {mss.get('type','MSS')} "
                    f"{mss['direction']} (opposing {direction}) ✗"
                )
        except Exception as exc:
            tf_results[tf] = {'status': 'ERROR', 'aligned': False}
            logger.debug(f"GRANULAR {symbol} {tf}: error — {exc}")

    # Resolve action from aligned count
    if aligned >= 4:
        action    = 'FULL_SIZE'
        size_mult = 1.0 if h4_aligned else 0.5
        t1_only   = not h4_aligned
    elif aligned == 3:
        action    = 'REDUCED_SIZE'
        size_mult = 0.5
        t1_only   = False
    elif aligned == 2:
        action    = 'T1_ONLY'
        size_mult = 0.25
        t1_only   = True
    else:
        action    = 'BLOCKED'
        size_mult = 0.0
        t1_only   = False

    confirmed = aligned >= 3
    label     = f"{aligned}/6 TFs"
    logger.info(
        f"GRANULAR {symbol} {direction}: {label} → {action} "
        f"size={size_mult}× {'T1-only' if t1_only else 'all-targets'}"
    )

    return {
        'confirmed'      : confirmed,
        'score'          : aligned,
        'action'         : action,
        'size_multiplier': size_mult,
        't1_only'        : t1_only,
        'tf_results'     : tf_results,
        'blocking_reason': None if confirmed else f"Only {aligned}/6 TFs aligned",
    }
