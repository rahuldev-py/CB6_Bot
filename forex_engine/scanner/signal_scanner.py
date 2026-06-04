# forex_engine/scanner/signal_scanner.py
# Main ICT Silver Bullet signal scanner — full setup detection pipeline.
# Orchestrates: DOL → sweep → MSS → FVG → price gate → OB → UT → score.

import os
from datetime import datetime, timezone
from typing import Optional
import pandas as pd

from utils.logger import logger
from ml_engine.memory.shadow_logger import log_scanner_outcome
from forex_engine.forex_instruments import INSTRUMENTS

# ── Kill Zone Windows (UTC) ─────────────────────────────────────────────────────
# London KZ: 07-12 UTC  |  NY KZ: 16-20 UTC
# Hard block outside these windows — no trades.
KILL_ZONE_WINDOWS = [
    (7,  12),
    (16, 20),
]
PRIME_KZ_HOURS = set(range(7, 10)) | set(range(16, 18))

# GFT Silver Bullet windows (UTC)
GFT_KZ_WINDOWS = [(8, 9), (15, 16), (19, 20)]

# Rollover block
ROLLOVER_BLOCK_START = 22
ROLLOVER_BLOCK_END   = 23


def is_in_kill_zone(utc_hour: int, gft: bool = False) -> bool:
    windows = GFT_KZ_WINDOWS if gft else KILL_ZONE_WINDOWS
    return any(s <= utc_hour < e for s, e in windows)


def is_prime_kz(utc_hour: int) -> bool:
    return utc_hour in PRIME_KZ_HOURS


def in_rollover_window(utc_hour: int) -> bool:
    return ROLLOVER_BLOCK_START <= utc_hour < ROLLOVER_BLOCK_END


def approaching_rollover() -> bool:
    now = datetime.now(timezone.utc)
    return now.hour == (ROLLOVER_BLOCK_START - 1) and now.minute >= 55


def gft_session_label(utc_hour: int) -> str:
    """Human-readable label for GFT Silver Bullet kill zone windows."""
    if utc_hour == 8:  return 'GFT London SB (08-09 UTC)'
    if utc_hour == 15: return 'GFT NY AM SB (15-16 UTC)'
    if utc_hour == 19: return 'GFT NY PM SB (19-20 UTC)'
    return 'GFT off-window'


# ── News filter ─────────────────────────────────────────────────────────────────
# Manually maintained high-impact news windows (UTC).
# Format: (YYYY-MM-DD, HH_start, HH_end) — add before each event, remove after.
NEWS_WINDOWS: list = [
    # Example: ('2026-05-30', 14, 15),  # USD Non-Farm Payrolls
]

BLOCK_NEWS_TRADING: bool = os.getenv('BLOCK_NEWS', 'false').lower() == 'true'


def in_news_window() -> bool:
    """True if current UTC time falls within a manually-declared red-folder news block."""
    if not BLOCK_NEWS_TRADING or not NEWS_WINDOWS:
        return False
    now  = datetime.now(timezone.utc)
    date = now.strftime('%Y-%m-%d')
    h    = now.hour
    return any(d == date and s <= h < e for d, s, e in NEWS_WINDOWS)


def current_session_label(utc_hour: int = None) -> str:
    h = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour
    if   0  <= h <  7: return 'Asia (no entries)'
    elif 7  <= h < 10: return 'London Open KZ'
    elif 10 <= h < 12: return 'London Mid KZ (off-peak)'
    elif 12 <= h < 16: return 'London/NY Overlap (no entries)'
    elif 16 <= h < 18: return 'NY Open KZ'
    elif 18 <= h < 20: return 'NY Session KZ'
    else:               return 'After-Hours (no entries)'


def _apply_atr_targets(
    entry: float,
    sl: float,
    risk: float,
    t1: float,
    t2: float,
    t3: float,
    rr: float,
    direction: str,
    daily_atr: float,
    cfg: dict,
    min_rr: float,
    symbol: str,
) -> tuple:
    """
    Replace or compress fixed risk-multiple targets with ATR-fractional sizing.

    Rules (from Dukascopy backtest analysis — EOS timeout root cause):
      T1 = Entry ± (atr_t1_factor × ATR_daily)   [default: 0.15 × ATR]
           → Captures intraday expansion early; prevents timeout stagnation.
      Safety: if T2_distance > atr_t2_max_factor × ATR_daily [default: 0.50 × ATR]
           → Compress T2 to 0.50 × ATR (or skip if compressed RR < min_rr).

    Returns updated (t1, t2, t3, rr, skip) where skip=True means discard the setup.
    """
    if daily_atr <= 0:
        return t1, t2, t3, rr, False

    t1_factor  = cfg.get('atr_t1_factor',   0.15)
    t2_max_fac = cfg.get('atr_t2_max_factor', 0.50)

    # ── T1: ATR-anchored near target ─────────────────────────────────────────
    atr_t1_dist = daily_atr * t1_factor
    if direction == 'BULLISH':
        t1_atr = round(entry + atr_t1_dist, 5)
        # Only substitute if the ATR T1 is tighter than the fixed T1 but still > entry
        if entry < t1_atr < t2:
            logger.info(
                f"FOREX {symbol}: ATR T1 adjusted "
                f"{t1:.5f} → {t1_atr:.5f}  (0.15×ATR={atr_t1_dist:.4f})"
            )
            t1 = t1_atr
    else:
        t1_atr = round(entry - atr_t1_dist, 5)
        if t2 < t1_atr < entry:
            logger.info(
                f"FOREX {symbol}: ATR T1 adjusted "
                f"{t1:.5f} → {t1_atr:.5f}  (0.15×ATR={atr_t1_dist:.4f})"
            )
            t1 = t1_atr

    # ── T2 runway safety check ───────────────────────────────────────────────
    atr_t2_max_dist = daily_atr * t2_max_fac
    t2_dist         = abs(t2 - entry)
    if t2_dist > atr_t2_max_dist:
        # Compress T2 to 0.50 × ATR
        if direction == 'BULLISH':
            t2_new = round(entry + atr_t2_max_dist, 5)
        else:
            t2_new = round(entry - atr_t2_max_dist, 5)

        rr_new = round(abs(t2_new - entry) / risk, 1) if risk > 0 else 0.0
        logger.info(
            f"FOREX {symbol}: T2 runway compressed "
            f"{t2:.5f} → {t2_new:.5f}  "
            f"(T2 dist {t2_dist:.4f} > 0.50×ATR {atr_t2_max_dist:.4f})  "
            f"RR {rr} → {rr_new}"
        )
        if rr_new < min_rr:
            logger.info(
                f"FOREX {symbol}: ATR compression killed RR "
                f"({rr_new} < {min_rr}) — no daily runway, skipping setup"
            )
            return t1, t2, t3, rr, True   # skip=True
        t2  = t2_new
        rr  = rr_new
        # Adjust T3 proportionally if it was anchored to the old T2
        t3_dist = abs(t3 - entry)
        if t3_dist > atr_t2_max_dist * 1.5:
            t3 = round(t2 + (t2 - entry) if direction == 'BULLISH'
                        else t2 + (t2 - entry), 5)  # T3 = T2 + 1R beyond

    return t1, t2, t3, rr, False


def scan_setup(df: pd.DataFrame, symbol: str,
               min_rr: float = 3.0,
               daily_atr: Optional[float] = None) -> Optional[dict]:
    """
    Run the full ICT Silver Bullet detection chain on a 15m DataFrame.

    Chain:
      1. DOL  (Draw on Liquidity)
      2. Sweep (Liquidity sweep — stop hunt)
      3. MSS  (Market Structure Shift — CHoCH or BOS)
      4. FVG  (Fair Value Gap — imbalance zone)
      5. Spread filter
      6. Price gate (price must be in/near FVG)
      7. OB   (Order Block)
      8. Trade plan (entry, SL, T1/T2/T3)
      9. UT Bot confirmation
      10. Confluence score

    Returns setup dict or None.
    """
    try:
        from scanner.silver_bullet import (
            find_draw_on_liquidity,
            detect_sb_mss,
            detect_sb_fvg,
            detect_order_block,
            premium_discount_context,
            premium_discount_aligned,
        )
        from forex_engine.scanner.liquidity_sweep import (
            analyze_liquidity_state,
            detect_sweep,
            sweep_confirmed as _sweep_ok,
        )
        from scanner.ut_bot import get_ut_signal

        if df is None or len(df) < 40:
            return None

        cfg = INSTRUMENTS.get(symbol, {})

        # 0. Liquidity state + sweep quality
        liquidity_state = analyze_liquidity_state(
            df,
            symbol=symbol,
            timeframe='15m',
            lookback=80,
            sweep_window=20,
        )
        liq_sweep = detect_sweep(
            df,
            lookback=80,
            sweep_window=20,
            symbol=symbol,
            timeframe='15m',
        )
        if liq_sweep:
            logger.info(
                f"FOREX {symbol}: sweep {liq_sweep['sweep_type']} "
                f"@ {liq_sweep['swept_level']:.5f} "
                f"{liq_sweep['candles_ago']} candles ago "
                f"conf={liq_sweep.get('confidence', 0)}/100"
            )

        # 1. DOL
        dol = find_draw_on_liquidity(df, lookback=80, wick_sweep=True)
        if dol is None:
            return None
        logger.info(f"FOREX {symbol}: DOL {dol['direction']} @ {dol['level']:.5f}")

        # 2. MSS
        mss = detect_sb_mss(df, lookback=40)
        if mss is None:
            return None
        direction = mss['direction']
        logger.info(f"FOREX {symbol}: MSS {direction} {mss.get('type')} @ {mss['level']:.5f}")

        if dol['direction'] != direction:
            logger.info(f"FOREX {symbol}: DOL/MSS mismatch — proceeding without DOL bonus")

        # Temporal ordering check: Sweep → MSS → FVG (ICT canonical sequence).
        # candles_ago is end-of-df relative; larger value = older in time.
        # sweep.ca <= mss.ca means sweep is MORE RECENT than the MSS, which
        # suggests the MSS pre-dates the sweep.  We LOG this as a warning but
        # do NOT nullify liq_sweep — doing so combined with no gate-penalty
        # removal previously created a −4 effective gap (the silent freeze).
        # The FVG is already constrained to post-MSS via mss_candles_ago below;
        # that temporal gate is sufficient.  Sweep scoring reflects quality
        # naturally: a structurally inconsistent sweep will usually score ≤ 70
        # confidence and contribute 0 sweep_confidence bonus.
        if liq_sweep is not None and liq_sweep.get('candles_ago', 0) <= mss.get('candles_ago', 0):
            logger.info(
                f"FOREX {symbol}: temporal order note — "
                f"sweep {liq_sweep['candles_ago']}ca vs MSS {mss['candles_ago']}ca. "
                f"Sweep kept for scoring; FVG gate enforces post-MSS displacement."
            )

        # 3. FVG — constrained to post-MSS candles only (Bug 5: FVG temporal gate)
        # mss_candles_ago causes detect_sb_fvg to skip any FVG whose c2 candle is
        # older than the MSS, ensuring the displacement leg formed AFTER the structure shift.
        fvg = detect_sb_fvg(df, direction, lookback=25, displacement_mult=1.0, use_range=True,
                            mss_candles_ago=mss['candles_ago'])
        if fvg is None:
            logger.info(f"FOREX {symbol}: no {direction} FVG after MSS — skip")
            return None

        if not fvg.get('displacement'):
            logger.info(
                f"FOREX {symbol}: weak FVG displacement "
                f"(body={fvg.get('body_ratio', 0):.0%}) â€” skip"
            )
            return None

        min_sl  = cfg.get('min_sl_dist', 0.0010)
        min_fvg = cfg.get('min_fvg_size', min_sl * 0.5)
        if fvg.get('size', 0) < min_fvg:
            logger.info(
                f"FOREX {symbol}: FVG too small ({fvg.get('size', 0):.5f} < {min_fvg:.5f}) — skip"
            )
            return None

        # 4. Spread check (candle column, live mode only)
        if 'spread' in df.columns:
            raw_spread   = float(df['spread'].iloc[-1])
            point_size   = cfg.get('point_size', 0.00001)
            spread_price = raw_spread * point_size
            max_spread   = cfg.get('max_spread', 999)
            if spread_price > max_spread:
                logger.info(
                    f"FOREX {symbol}: spread {raw_spread:.0f}pts ({spread_price:.5f}) "
                    f"> max {max_spread} — skip"
                )
                return None

        # 5. Price gate — must be in or near FVG
        last_low   = float(df['low'].iloc[-1])
        last_high  = float(df['high'].iloc[-1])
        last_close = float(df['close'].iloc[-1])
        fvg_low    = fvg['fvg_low']
        fvg_high   = fvg['fvg_high']
        fvg_mid    = fvg['mid']

        pd_context = premium_discount_context(df, fvg_mid, lookback=40)
        pd_context['aligned'] = premium_discount_aligned(direction, pd_context)
        if not pd_context['aligned']:
            logger.info(
                f"FOREX {symbol}: {direction} FVG in {pd_context.get('zone')} "
                f"(eq={pd_context.get('equilibrium', 0):.5f}) â€” skip"
            )
            return None

        in_fvg   = last_low <= fvg_high and last_high >= fvg_low
        near_fvg = abs(last_close - fvg_mid) / (fvg_mid + 1e-9) <= 0.005
        if not (in_fvg or near_fvg):
            return None

        # 6. Order Block
        ob = detect_order_block(df, direction, lookback=40)
        if ob:
            logger.info(
                f"FOREX {symbol}: OB {ob['type']} "
                f"{ob['ob_low']:.5f}–{ob['ob_high']:.5f}"
            )

        # 7. Trade plan
        fvg_buf  = cfg.get('fvg_buf', 0.0003)
        fvg_size = max(fvg.get('size', min_sl), min_sl)

        if direction == 'BULLISH':
            entry = round(fvg_low + fvg_buf, 5)
            sl    = round(fvg_low - fvg_size, 5)
            risk  = round(entry - sl, 5)
            if risk <= 0:
                return None
            t1  = round(entry + risk * 2.0, 5)
            t2  = round(entry + risk * 3.0, 5)
            dol_l = dol['level']
            t3  = round(max(dol_l if dol_l > t2 else entry + risk * 4.0, t2), 5)
            rr  = round((t2 - entry) / risk, 1)
        else:
            entry = round(fvg_high - fvg_buf, 5)
            sl    = round(fvg_high + fvg_size, 5)
            risk  = round(sl - entry, 5)
            if risk <= 0:
                return None
            t1  = round(entry - risk * 2.0, 5)
            t2  = round(entry - risk * 3.0, 5)
            dol_l = dol['level']
            t3  = round(min(dol_l if dol_l < t2 else entry - risk * 4.0, t2), 5)
            rr  = round((entry - t2) / risk, 1)

        if rr < min_rr:
            return None

        logger.info(
            f"FOREX {symbol}: plan {direction}  "
            f"entry={entry:.5f}  SL={sl:.5f}  risk={risk:.5f}  "
            f"T1={t1:.5f}  T2={t2:.5f}  T3={t3:.5f}  RR={rr}"
        )

        # 7b. ATR-fractional target adjustment (if daily_atr was provided by caller)
        # Replaces fixed-multiple targets with volatility-anchored sizing to prevent
        # EOS-timeout failures (root cause: T1/T2 too far for the day's actual range).
        if daily_atr and daily_atr > 0:
            t1, t2, t3, rr, _skip = _apply_atr_targets(
                entry=entry, sl=sl, risk=risk,
                t1=t1, t2=t2, t3=t3, rr=rr,
                direction=direction,
                daily_atr=daily_atr,
                cfg=cfg,
                min_rr=min_rr,
                symbol=symbol,
            )
            if _skip:
                return None   # No daily ATR runway — don't enter

        # 8. UT Bot
        try:
            ut = get_ut_signal(df)
            ut['aligned'] = (ut.get('trend') == direction)
        except Exception:
            ut = {'trend': None, 'stop': None, 'signal': None,
                  'bars_in_trend': 0, 'aligned': None}

        # 9. Confluence
        mss_type       = mss.get('type', 'BOS')
        dol_agrees     = (dol['direction'] == direction)
        # Bug 2 fix: use lenient sweep_confirmed() helper instead of inline
        # level_state == 'SWEPT' check.  Helper allows level_state None (sweep
        # detected but not tracked by state machine) or STATE_SWEPT — both are
        # valid ICT sweeps.  The strict == 'SWEPT' caused silent false negatives
        # when the in-memory state bucket had not yet registered the level.
        sweep_confirmed  = _sweep_ok(liq_sweep, direction, max_candles_ago=15, min_confidence=0)
        sweep_confidence = int((liq_sweep or {}).get('confidence', 0))
        ob_present = ob is not None

        dol_eqh_eql = dol.get('is_eqh_eql', False)

        score  = 5 if dol_agrees else 4
        score += 2 if mss_type == 'CHOCH' else 1
        score += 1 if in_fvg else 0
        score += 1 if fvg.get('displacement') else 0
        score += 1 if rr >= 3.0 else 0
        score += 2 if ut.get('aligned') else 0
        score += 2 if sweep_confirmed else 0
        score += 1 if sweep_confidence >= 70 else 0
        score += 1 if ob_present else 0
        score += 2 if dol_eqh_eql else 0   # EQH/EQL = denser stop cluster → higher sweep probability

        logger.info(
            f"FOREX {symbol}: score {score}/18 "
            f"(CHoCH={mss_type=='CHOCH'} inFVG={in_fvg} "
            f"sweep={sweep_confirmed} sweepConf={sweep_confidence} "
            f"OB={ob_present} UT={ut.get('aligned')} "
            f"EQH_EQL={dol_eqh_eql})"
        )

        setup_out = {
            'symbol'         : symbol,
            'direction'      : direction,
            'confluence'     : score,
            'in_fvg'         : in_fvg,
            'near_fvg'       : near_fvg,
            'mss_type'       : mss_type,
            'dol'            : dol,
            'dol_is_eqh_eql' : dol_eqh_eql,
            'mss'            : mss,
            'fvg'            : fvg,
            'premium_discount': pd_context,
            'ob'             : ob,
            'ob_present'     : ob_present,
            'ut_bot'         : ut,
            'liq_sweep'      : liq_sweep,
            'liquidity_state': liquidity_state,
            'sweep_confidence': sweep_confidence,
            'sweep_quality'  : (liq_sweep or {}).get('quality', {}),
            'sweep_confirmed': sweep_confirmed,
            'daily_atr'      : daily_atr,   # None if not fetched; float if ATR sizing was applied
            'entry_signal': {
                'entry'    : entry,
                'stop_loss': sl,
                'target1'  : t1,
                'target2'  : t2,
                'target3'  : t3,
                'risk'     : risk,
                'rr_ratio' : rr,
                'fvg_low'  : round(fvg_low, 5),
                'fvg_high' : round(fvg_high, 5),
                'dol_level': round(dol['level'], 5),
                'mss_level': round(mss['level'], 5),
            },
        }
        log_scanner_outcome('forex', 'forex_signal_scanner', symbol, setup_out, outcome='SCANNER_PASS')
        return setup_out

    except Exception as e:
        import traceback
        logger.error(f"scan_setup({symbol}) error: {e}\n{traceback.format_exc()}")
        log_scanner_outcome('forex', 'forex_signal_scanner', symbol, None, outcome='SCANNER_FAIL', reason='exception')
        return None
