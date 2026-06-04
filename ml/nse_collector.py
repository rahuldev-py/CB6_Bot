# ml/nse_collector.py
#
# NSE trade data collector for ML training.
# Called from trader/paper_trader.py and trader/live_trader.py.
#
# Every NSE trade entry is stored with:
#   - Full ICT signal breakdown (CHoCH/BOS, FVG, sweep, DOL, UT Bot)
#   - Score components and confluence flags
#   - Market brain context (FII/DII, brain direction, regime)
#   - H1/H4 bias, time context
#   - Options metadata if applicable (strike, expiry, delta, IV)
#
# Outcome is patched in when the trade closes (targets hit, PnL, exit reason).
#
# Data lives in:  data/ml/nse/trades.jsonl
# Never mix NSE records with Forex records.

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from utils.logger import logger
from ml.base_collector import append_record, patch_outcome, get_utc_context

_MARKET  = 'nse'
_ACCOUNT = ''       # NSE has one account (paper or live)


# ── Entry record ───────────────────────────────────────────────────────────────

def record_entry(trade: dict, setup: dict, mode: str = 'paper') -> bool:
    """
    Call immediately after a trade is opened (paper or live).

    trade  — the trade dict saved to state (from open_paper_trade / open_live_trade)
    setup  — the full setup dict from the scanner (contains all signal context)
    mode   — 'paper' | 'live'
    """
    try:
        sig   = setup.get('entry_signal', {})
        fvg   = setup.get('fvg', sig)
        liq   = setup.get('liq_sweep') or {}
        ut    = setup.get('ut_bot') or {}
        brain = setup.get('brain_context') or {}

        # ── Price / structure features ─────────────────────────────────────────
        entry    = float(sig.get('entry',    trade.get('entry_price', 0)))
        sl       = float(sig.get('stop_loss', trade.get('stop_loss', 0)))
        t1       = float(sig.get('target1',  trade.get('target1', 0)))
        t2       = float(sig.get('target2',  trade.get('target2', 0)))
        t3       = float(sig.get('target3',  trade.get('target3', 0)))
        sl_dist  = round(abs(entry - sl), 4)
        rr_t1    = round(abs(t1 - entry) / sl_dist, 2) if sl_dist > 0 else 0
        rr_t2    = round(abs(t2 - entry) / sl_dist, 2) if sl_dist > 0 else 0
        rr_t3    = round(abs(t3 - entry) / sl_dist, 2) if sl_dist > 0 else 0

        # ── FVG ───────────────────────────────────────────────────────────────
        fvg_low  = float(fvg.get('fvg_low',  sig.get('fvg_low',  0)))
        fvg_high = float(fvg.get('fvg_high', sig.get('fvg_high', 0)))
        fvg_size = round(abs(fvg_high - fvg_low), 4)
        eq_level = round((fvg_low + fvg_high) / 2, 4) if fvg_high else 0

        # ── Liquidity sweep ────────────────────────────────────────────────────
        sweep_type       = liq.get('sweep_type', 'NONE')
        sweep_candles_ago = int(liq.get('candles_ago', 0))
        sweep_confirmed  = bool(setup.get('sweep_confirmed', False))

        # ── MSS / structure ────────────────────────────────────────────────────
        mss_type      = setup.get('mss_type', 'BOS')
        dol_direction = sig.get('dol_direction', setup.get('dol_direction', 'UNKNOWN'))
        dol_price     = float(sig.get('dol_level', 0))
        direction     = setup.get('direction', trade.get('direction', 'UNKNOWN'))
        dol_match     = (dol_direction == direction) if dol_direction != 'UNKNOWN' else None

        # ── UT Bot ────────────────────────────────────────────────────────────
        ut_trend   = ut.get('trend', 'UNKNOWN')
        ut_aligned = bool(ut.get('aligned', False))

        # ── Score / confluence ────────────────────────────────────────────────
        score      = int(setup.get('confluence', sig.get('confluence', 0)))
        score_flags = setup.get('confluence_flags', [])

        # ── NSE bias (from brain context or setup) ────────────────────────────
        h1_bias = setup.get('h1_bias', brain.get('h1_bias', 'UNKNOWN'))
        h4_bias = setup.get('h4_bias', brain.get('h4_bias', 'UNKNOWN'))

        # ── Market brain ──────────────────────────────────────────────────────
        fii_dii_sent   = brain.get('fii_dii_sentiment', 'UNKNOWN')
        brain_dir      = brain.get('direction', 'UNKNOWN')
        brain_score    = int(brain.get('score', 0))
        brain_conf     = int(brain.get('confidence', 0))
        brain_gate     = int(brain.get('gate', 0))
        brain_mode     = brain.get('mode', 'UNKNOWN')

        # ── Time context ──────────────────────────────────────────────────────
        now_ist = datetime.now()  # runs in IST TZ
        time_ctx = get_utc_context()
        ist_hour   = now_ist.hour
        ist_minute = now_ist.minute

        # ── Session label ─────────────────────────────────────────────────────
        if 9 <= ist_hour < 10:
            session = 'morning_open'
        elif 10 <= ist_hour < 12:
            session = 'mid_morning'
        elif 12 <= ist_hour < 14:
            session = 'afternoon'
        elif 14 <= ist_hour < 15:
            session = 'pre_close'
        else:
            session = 'other'

        record = {
            '_type'              : 'ENTRY',
            '_schema_version'    : 2,
            'market'             : 'NSE',
            'mode'               : mode,
            'trade_id'           : trade.get('id', ''),
            'symbol'             : trade.get('symbol', ''),
            'underlying'         : trade.get('underlying', setup.get('underlying', '')),
            'instrument_type'    : trade.get('instrument_type', 'FUTURES'),
            'direction'          : direction,
            'timeframe'          : trade.get('timeframe', '15min'),

            # ── Price action ──────────────────────────────────────────────────
            'entry_price'        : entry,
            'stop_loss'          : sl,
            'target_1'           : t1,
            'target_2'           : t2,
            'target_3'           : t3,
            'sl_distance'        : sl_dist,
            'rr_t1'              : rr_t1,
            'rr_t2'              : rr_t2,
            'rr_t3'              : rr_t3,
            'in_ote'             : bool(sig.get('in_ote', False)),
            'in_fvg'             : bool(sig.get('in_fvg', False)),

            # ── ICT structure ─────────────────────────────────────────────────
            'mss_type'           : mss_type,
            'sweep_type'         : sweep_type,
            'sweep_candles_ago'  : sweep_candles_ago,
            'sweep_confirmed'    : sweep_confirmed,
            'fvg_low'            : fvg_low,
            'fvg_high'           : fvg_high,
            'fvg_size'           : fvg_size,
            'fvg_equilibrium'    : eq_level,
            'fvg_in_discount'    : (entry <= eq_level if direction in ('BUY', 'BULLISH') else entry >= eq_level),
            'dol_direction'      : dol_direction,
            'dol_price'          : dol_price,
            'dol_mss_match'      : dol_match,

            # ── UT Bot ────────────────────────────────────────────────────────
            'ut_bot_trend'       : ut_trend,
            'ut_bot_aligned'     : ut_aligned,

            # ── Confluence ────────────────────────────────────────────────────
            'score'              : score,
            'score_flags'        : score_flags,

            # ── HTF bias ──────────────────────────────────────────────────────
            'h1_bias'            : h1_bias,
            'h4_bias'            : h4_bias,

            # ── Market brain ──────────────────────────────────────────────────
            'fii_dii_sentiment'  : fii_dii_sent,
            'brain_direction'    : brain_dir,
            'brain_score'        : brain_score,
            'brain_confidence'   : brain_conf,
            'brain_gate'         : brain_gate,
            'brain_mode'         : brain_mode,

            # ── Options metadata ──────────────────────────────────────────────
            'strike'             : trade.get('strike'),
            'expiry'             : trade.get('expiry'),
            'delta'              : trade.get('delta'),
            'theta'              : trade.get('theta'),
            'iv'                 : trade.get('iv'),
            'dte'                : trade.get('dte'),

            # ── Position sizing ───────────────────────────────────────────────
            'quantity'           : trade.get('quantity', 0),
            'lot_size'           : trade.get('lot_size'),
            'capital_used_inr'   : trade.get('capital_used', 0),
            'risk_inr'           : trade.get('risk', sig.get('risk', 0)),
            'rr_ratio'           : trade.get('rr_ratio', sig.get('rr_ratio', 0)),

            # ── Time context ──────────────────────────────────────────────────
            'ist_hour'           : ist_hour,
            'ist_minute'         : ist_minute,
            'session'            : session,
            **time_ctx,

            # ── Outcome placeholder (filled on close) ─────────────────────────
            'outcome'            : None,
        }

        ok = append_record(_MARKET, record, _ACCOUNT)
        if ok:
            logger.info(f"ML NSE: entry recorded — {trade.get('id')} {trade.get('symbol')} {direction}")
        return ok

    except Exception as e:
        logger.error(f"ML NSE record_entry error: {e}")
        return False


# ── Outcome record ─────────────────────────────────────────────────────────────

def record_outcome(trade: dict, exit_reason: str, exit_price: float,
                   pnl_inr: float) -> bool:
    """
    Call when a trade closes.

    trade       — the closed trade dict from state
    exit_reason — 'T1' | 'T2' | 'T3' | 'SL' | 'MANUAL' | 'EOD' | 'OVERNIGHT'
    exit_price  — actual exit price
    pnl_inr     — net PnL in INR
    """
    try:
        trade_id = trade.get('id', '')
        entry    = float(trade.get('entry_price', 0))
        sl_dist  = abs(entry - float(trade.get('stop_loss', entry)))
        r_mult   = round(pnl_inr / (sl_dist * trade.get('quantity', 1)), 2) if sl_dist > 0 else 0

        entry_t  = trade.get('entry_time', '')
        exit_t   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        hold_min = 0
        try:
            dt_in  = datetime.strptime(entry_t[:19], '%Y-%m-%d %H:%M:%S')
            dt_out = datetime.strptime(exit_t[:19],  '%Y-%m-%d %H:%M:%S')
            hold_min = int((dt_out - dt_in).total_seconds() / 60)
        except Exception:
            pass

        targets = trade.get('targets_hit', [])
        result  = 'WIN' if pnl_inr > 0 else ('LOSS' if pnl_inr < 0 else 'BREAKEVEN')

        outcome = {
            'exit_reason'       : exit_reason,
            'exit_price'        : exit_price,
            'pnl_inr'           : round(pnl_inr, 2),
            'r_multiple'        : r_mult,
            'targets_hit'       : targets,
            'hold_time_minutes' : hold_min,
            'result'            : result,
            'timestamp_exit_ist': exit_t,
            **get_utc_context(),
        }

        ok = patch_outcome(_MARKET, trade_id, outcome, _ACCOUNT)
        if ok:
            logger.info(f"ML NSE: outcome recorded — {trade_id} {result} R={r_mult} PnL=Rs{pnl_inr:.0f}")
        return ok

    except Exception as e:
        logger.error(f"ML NSE record_outcome error: {e}")
        return False
