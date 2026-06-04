# ml/forex_collector.py
#
# Forex trade data collector for ML training.
# Called from forex_engine/forex_worker.py (FTMO) and
#             forex_engine/prop_firms/gft/gft_5k_2step.py (GFT).
#
# FTMO and GFT records are stored in separate files:
#   data/ml/forex/ftmo_trades.jsonl
#   data/ml/forex/gft_trades.jsonl
#
# Each record captures the full ICT signal chain + market context at entry.
# Outcome (exit reason, PnL, R-multiple) is patched in on close.
# NSE data is NEVER written to these files.

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from utils.logger import logger
from ml.base_collector import append_record, patch_outcome, get_utc_context

_MARKET = 'forex'


def _session_label(utc_hour: int) -> str:
    if 7 <= utc_hour < 10:
        return 'london_open'
    elif 10 <= utc_hour < 12:
        return 'london_mid'
    elif 12 <= utc_hour < 16:
        return 'between_sessions'
    elif 16 <= utc_hour < 18:
        return 'ny_open'
    elif 18 <= utc_hour < 20:
        return 'ny_mid'
    elif 22 <= utc_hour < 23:
        return 'rollover'
    elif 0 <= utc_hour < 7:
        return 'asia'
    else:
        return 'off_hours'


# ── Entry record ───────────────────────────────────────────────────────────────

def record_entry(trade: dict, setup: dict, account: str,
                 lots: float, risk_usd: float,
                 h1_bias: str = 'UNKNOWN', h4_bias: str = 'UNKNOWN',
                 sim_ratio: float = 0.0, lot_boost: float = 1.0,
                 risk_mode: str = 'normal') -> bool:
    """
    Call immediately after a Forex trade is opened.

    trade     — trade dict written to state
    setup     — full setup dict from scanner (signal + structure context)
    account   — 'ftmo' | 'gft'
    lots      — actual lots placed
    risk_usd  — actual USD risk for this trade
    h1_bias   — H1 EMA trend at entry
    h4_bias   — H4 EMA trend at entry
    sim_ratio — A+ template similarity score (0–1)
    lot_boost — lot multiplier applied for A+ setup
    risk_mode — 'normal' | 'reduced' | 'aplus_only'
    """
    try:
        sig  = setup.get('entry_signal', {})
        liq  = setup.get('liq_sweep') or {}
        ut   = setup.get('ut_bot') or {}
        ob   = setup.get('order_block') or {}

        # ── Price / structure ─────────────────────────────────────────────────
        entry   = float(sig.get('entry',    trade.get('entry_price', 0)))
        sl      = float(sig.get('stop_loss', trade.get('current_sl',  0)))
        t1      = float(sig.get('target1',  trade.get('target1', 0)))
        t2      = float(sig.get('target2',  trade.get('target2', 0)))
        t3      = float(sig.get('target3',  trade.get('target3', 0)))
        sl_dist = round(abs(entry - sl), 6)
        rr_t1   = round(abs(t1 - entry) / sl_dist, 2) if sl_dist > 0 else 0
        rr_t2   = round(abs(t2 - entry) / sl_dist, 2) if sl_dist > 0 else 0
        rr_t3   = round(abs(t3 - entry) / sl_dist, 2) if sl_dist > 0 else 0

        # ── FVG ──────────────────────────────────────────────────────────────
        fvg_low  = float(sig.get('fvg_low',  0))
        fvg_high = float(sig.get('fvg_high', 0))
        fvg_size = round(abs(fvg_high - fvg_low), 6)
        eq_level = round((fvg_low + fvg_high) / 2, 6) if fvg_high else 0
        direction = setup.get('direction', trade.get('direction', 'UNKNOWN'))
        fvg_in_discount = (entry <= eq_level if direction == 'BULLISH' else entry >= eq_level)

        # ── Liquidity sweep ───────────────────────────────────────────────────
        sweep_type        = liq.get('sweep_type', 'NONE')
        sweep_candles_ago = int(liq.get('candles_ago', 0))
        sweep_confirmed   = bool(setup.get('sweep_confirmed', False))
        sweep_quality     = setup.get('sweep_quality') or liq.get('quality') or {}
        sweep_confidence  = int(setup.get('sweep_confidence', liq.get('confidence', 0)) or 0)
        sweep_wick_ratio  = float(sweep_quality.get('wick_ratio', liq.get('wick_ratio', 0.0)) or 0.0)
        sweep_volume_spike = float(sweep_quality.get('volume_spike', liq.get('volume_spike', 0.0)) or 0.0)
        sweep_atr_expansion = float(sweep_quality.get('atr_expansion', liq.get('atr_expansion', 0.0)) or 0.0)
        sweep_displacement = float(sweep_quality.get('displacement_ratio', liq.get('displacement_ratio', 0.0)) or 0.0)
        liquidity_state = setup.get('liquidity_state') or {}

        # ── MSS / DOL ────────────────────────────────────────────────────────
        mss_type      = setup.get('mss_type', 'BOS')
        dol_direction = sig.get('dol_direction', setup.get('dol_direction', 'UNKNOWN'))
        dol_price     = float(sig.get('dol_level', 0))
        dol_match     = (dol_direction == direction) if dol_direction != 'UNKNOWN' else None

        # ── UT Bot ────────────────────────────────────────────────────────────
        ut_trend   = ut.get('trend', 'UNKNOWN')
        ut_aligned = bool(ut.get('aligned', False))

        # ── Order Block ───────────────────────────────────────────────────────
        ob_type    = ob.get('type', 'NONE') if ob else 'NONE'
        ob_present = bool(ob)

        # ── Score ─────────────────────────────────────────────────────────────
        score       = int(setup.get('confluence', 0))
        score_flags = setup.get('confluence_flags', [])

        # ── Time context ──────────────────────────────────────────────────────
        time_ctx   = get_utc_context()
        utc_hour   = time_ctx['utc_hour']
        session    = _session_label(utc_hour)
        in_kz      = 7 <= utc_hour < 12 or 16 <= utc_hour < 20

        # ── Spread ────────────────────────────────────────────────────────────
        spread = float(trade.get('spread_at_entry', setup.get('spread_at_entry', 0)) or 0)

        record = {
            '_type'              : 'ENTRY',
            '_schema_version'    : 2,
            'market'             : 'FOREX',
            'account'            : account.upper(),
            'mode'               : 'live',
            'trade_id'           : trade.get('id', ''),
            'symbol'             : trade.get('symbol', setup.get('symbol', '')),
            'direction'          : direction,
            'timeframe'          : '15m',

            # ── Price action ─────────────────────────────────────────────────
            'entry_price'        : entry,
            'stop_loss'          : sl,
            'target_1'           : t1,
            'target_2'           : t2,
            'target_3'           : t3,
            'sl_distance'        : sl_dist,
            'rr_t1'              : rr_t1,
            'rr_t2'              : rr_t2,
            'rr_t3'              : rr_t3,
            'in_fvg'             : bool(setup.get('in_fvg', False)),

            # ── ICT structure ────────────────────────────────────────────────
            'mss_type'           : mss_type,
            'sweep_type'         : sweep_type,
            'sweep_candles_ago'  : sweep_candles_ago,
            'sweep_confirmed'    : sweep_confirmed,
            'sweep_confidence'   : sweep_confidence,
            'sweep_wick_ratio'   : round(sweep_wick_ratio, 4),
            'sweep_volume_spike' : round(sweep_volume_spike, 4),
            'sweep_atr_expansion': round(sweep_atr_expansion, 4),
            'sweep_displacement' : round(sweep_displacement, 4),
            'sweep_level_state'  : liq.get('level_state', 'UNKNOWN'),
            'sweep_level_type'   : liq.get('level_type', 'UNKNOWN'),
            'active_buy_side_liquidity' : (
                (liquidity_state.get('active_buy_side_liquidity') or {}).get('level')
            ),
            'active_sell_side_liquidity': (
                (liquidity_state.get('active_sell_side_liquidity') or {}).get('level')
            ),
            'violated_liquidity_count'  : len(liquidity_state.get('violated_levels') or []),
            'fvg_low'            : fvg_low,
            'fvg_high'           : fvg_high,
            'fvg_size'           : fvg_size,
            'fvg_equilibrium'    : eq_level,
            'fvg_in_discount'    : fvg_in_discount,
            'dol_direction'      : dol_direction,
            'dol_price'          : dol_price,
            'dol_mss_match'      : dol_match,

            # ── Order Block ──────────────────────────────────────────────────
            'ob_present'         : ob_present,
            'ob_type'            : ob_type,

            # ── UT Bot ───────────────────────────────────────────────────────
            'ut_bot_trend'       : ut_trend,
            'ut_bot_aligned'     : ut_aligned,

            # ── HTF bias ─────────────────────────────────────────────────────
            'h1_bias'            : h1_bias,
            'h4_bias'            : h4_bias,
            'h1_aligned'         : (h1_bias == direction),
            'h4_aligned'         : (h4_bias == direction),
            'both_htf_aligned'   : (h1_bias == direction and h4_bias == direction),

            # ── Confluence ───────────────────────────────────────────────────
            'score'              : score,
            'score_flags'        : score_flags,

            # ── A+ template matching ─────────────────────────────────────────
            'aplus_sim_ratio'    : round(sim_ratio, 4),
            'aplus_lot_boost'    : round(lot_boost, 2),
            'is_aplus'           : sim_ratio >= 0.55,
            'is_aplus_high'      : sim_ratio >= 0.70,
            'is_aplus_ultra'     : sim_ratio >= 0.85,

            # ── Risk / sizing ────────────────────────────────────────────────
            'lots'               : lots,
            'risk_usd'           : round(risk_usd, 2),
            'spread_at_entry'    : spread,
            'risk_mode'          : risk_mode,

            # ── Session / time ───────────────────────────────────────────────
            'session'            : session,
            'in_kill_zone'       : in_kz,
            **time_ctx,

            # ── Outcome placeholder ──────────────────────────────────────────
            'outcome'            : None,
        }

        ok = append_record(_MARKET, record, account.lower())
        if ok:
            logger.info(
                f"ML FOREX [{account.upper()}]: entry recorded — "
                f"{trade.get('id')} {record['symbol']} {direction} score={score}"
            )
        return ok

    except Exception as e:
        logger.error(f"ML FOREX record_entry error ({account}): {e}")
        return False


# ── Outcome record ─────────────────────────────────────────────────────────────

def record_outcome(trade: dict, account: str,
                   exit_reason: str, exit_price: float,
                   pnl_usd: float) -> bool:
    """
    Call when a Forex trade closes.

    trade       — closed trade dict from state
    account     — 'ftmo' | 'gft'
    exit_reason — 'T1' | 'T2' | 'T3' | 'SL' | 'MAE_EXIT' | 'TIME_EXIT'
                  | 'T1_BE' | 'BE_TRIGGER' | 'MANUAL'
    exit_price  — actual fill price at exit
    pnl_usd     — net P&L in USD
    """
    try:
        trade_id = trade.get('id', '')
        entry    = float(trade.get('entry_price', 0))
        sl       = float(trade.get('current_sl', trade.get('stop_loss', entry)))
        sl_dist  = abs(entry - sl)
        lots     = float(trade.get('lots', 0))
        risk_usd = float(trade.get('risk_usd', 0))

        # R-multiple: pnl / initial_risk
        if risk_usd > 0:
            r_mult = round(pnl_usd / risk_usd, 3)
        elif sl_dist > 0 and lots > 0:
            from forex_engine.forex_instruments import INSTRUMENTS
            sym = trade.get('symbol', '')
            cs  = INSTRUMENTS.get(sym, {}).get('contract_size', 100)
            r_mult = round(pnl_usd / (lots * cs * sl_dist), 3)
        else:
            r_mult = 0.0

        entry_t  = str(trade.get('entry_time', ''))
        exit_t   = datetime.now(timezone.utc).isoformat()
        hold_min = 0
        try:
            fmt = '%Y-%m-%d %H:%M:%S'
            dt_in  = datetime.strptime(entry_t[:19], fmt).replace(tzinfo=timezone.utc)
            dt_out = datetime.now(timezone.utc)
            hold_min = int((dt_out - dt_in).total_seconds() / 60)
        except Exception:
            pass

        targets = trade.get('targets_hit', [])
        result  = 'WIN' if pnl_usd > 0 else ('LOSS' if pnl_usd < 0 else 'BREAKEVEN')

        outcome = {
            'exit_reason'       : exit_reason,
            'exit_price'        : exit_price,
            'pnl_usd'           : round(pnl_usd, 2),
            'r_multiple'        : r_mult,
            'targets_hit'       : targets,
            'hold_time_minutes' : hold_min,
            'result'            : result,
            'timestamp_exit_utc': exit_t,
            **get_utc_context(),
        }

        ok = patch_outcome(_MARKET, trade_id, outcome, account.lower())
        if ok:
            logger.info(
                f"ML FOREX [{account.upper()}]: outcome recorded — "
                f"{trade_id} {result} R={r_mult} PnL=${pnl_usd:.2f}"
            )
        return ok

    except Exception as e:
        logger.error(f"ML FOREX record_outcome error ({account}): {e}")
        return False
