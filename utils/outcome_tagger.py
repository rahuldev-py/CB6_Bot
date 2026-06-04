"""
Outcome Tagger — CB6 Quantum Phase 4
Enriches closed trade records with:
  - exit_type: clean categorical label for how the trade exited
  - mfe_r: max favorable excursion (in R units) — derived from targets_hit + price data
  - mae_r: max adverse excursion (in R units) — derived from exit_price + stop_loss

All values are approximations from available state data.
Precision improves when OHLCV archive has candles covering the trade window.
"""

import json
from typing import Optional
from utils.trade_db import _connect, init_db
from utils.logger import logger


# ---------------------------------------------------------------------------
# Exit type tagging
# ---------------------------------------------------------------------------

EXIT_TYPE_MAP = {
    # From exit_reason field (newer trades)
    "SL":         "SL_HIT",
    "MAE_EXIT":   "MAE_EXIT",
    "MANUAL":     "MANUAL",
    "TIME_EXIT":  "TIME_EXIT",
    "BE":         "BE_HIT",
    "BE_SL":      "BE_HIT",
    "TARGET":     "TP_HIT",
}


def tag_exit_type(trade: dict) -> str:
    """Derive a clean exit type string from trade fields."""
    targets_hit = trade.get("targets_hit") or "[]"
    if isinstance(targets_hit, str):
        try:
            targets_hit = json.loads(targets_hit)
        except Exception:
            targets_hit = []

    exit_reason = (trade.get("exit_reason") or "").upper().strip()
    result = (trade.get("result") or "").upper()
    pnl    = trade.get("pnl_usd") or 0.0

    # Priority 1: explicit exit_reason
    if exit_reason in EXIT_TYPE_MAP:
        mapped = EXIT_TYPE_MAP[exit_reason]
        # Refine TP_HIT with actual target level
        if mapped in ("TP_HIT",) or (mapped == "MANUAL" and targets_hit):
            return _tp_level_from_targets(targets_hit, result)
        return mapped

    # Priority 2: infer from targets_hit + result
    if targets_hit:
        return _tp_level_from_targets(targets_hit, result)

    # Priority 3: infer from result + price data
    if result == "WIN":
        return "PARTIAL_WIN"   # won but no targets hit — manual or partial scale
    if result == "LOSS":
        return "SL_HIT"
    if result == "BE":
        return "BE_HIT"
    return "UNKNOWN"


def _tp_level_from_targets(targets_hit: list, result: str) -> str:
    if "T3" in targets_hit:
        return "TP3_HIT"
    if "T2" in targets_hit:
        return "TP2_PARTIAL"
    if "T1" in targets_hit:
        return "TP1_PARTIAL"
    if result == "WIN":
        return "PARTIAL_WIN"
    return "SL_HIT"


# ---------------------------------------------------------------------------
# MFE / MAE computation
# ---------------------------------------------------------------------------

def compute_mfe_mae(trade: dict) -> tuple[float, float]:
    """
    Compute max favorable excursion (MFE) and max adverse excursion (MAE) in R units.
    R = SL distance from entry.

    Returns (mfe_r, mae_r). Values are approximations from available data.
    """
    entry    = trade.get("entry_price") or 0.0
    sl       = trade.get("stop_loss")   or 0.0
    t1       = trade.get("target1")     or 0.0
    t2       = trade.get("target2")     or 0.0
    t3       = trade.get("target3")     or 0.0
    exit_p   = trade.get("exit_price")  or 0.0
    r_mult   = trade.get("r_multiple")  # may be None for old trades
    result   = (trade.get("result") or "").upper()
    direction = (trade.get("direction") or "").upper()
    targets_hit = trade.get("targets_hit") or "[]"
    if isinstance(targets_hit, str):
        try:
            targets_hit = json.loads(targets_hit)
        except Exception:
            targets_hit = []

    if not entry or not sl or entry == sl:
        return 0.0, 0.0

    sl_dist = abs(entry - sl)
    if sl_dist == 0:
        return 0.0, 0.0

    # ── MFE ─────────────────────────────────────────────────────────────────
    # How far did price go in our favor?
    if "T3" in targets_hit and t3:
        mfe_r = abs(t3 - entry) / sl_dist
    elif "T2" in targets_hit and t2:
        mfe_r = abs(t2 - entry) / sl_dist
    elif "T1" in targets_hit and t1:
        mfe_r = abs(t1 - entry) / sl_dist
    elif r_mult is not None and float(r_mult) > 0:
        mfe_r = float(r_mult)
    elif result == "WIN" and exit_p:
        mfe_r = abs(exit_p - entry) / sl_dist
    else:
        mfe_r = 0.0   # loss with no targets — unknown MFE, conservative 0

    # Try to refine with candle data if available
    mfe_from_candles = _mfe_from_candles(trade)
    if mfe_from_candles and mfe_from_candles > mfe_r:
        mfe_r = mfe_from_candles

    # ── MAE ─────────────────────────────────────────────────────────────────
    # How far did price go against us?
    exit_type = tag_exit_type(trade)
    if exit_type in ("SL_HIT", "MAE_EXIT") and exit_p:
        mae_r = abs(exit_p - entry) / sl_dist
    elif exit_p and result == "LOSS":
        mae_r = abs(exit_p - entry) / sl_dist
    elif r_mult is not None and float(r_mult) < 0:
        mae_r = abs(float(r_mult))
    else:
        mae_r = 0.1   # won cleanly — estimate minimal drawdown

    return round(mfe_r, 3), round(mae_r, 3)


def _mfe_from_candles(trade: dict) -> Optional[float]:
    """
    Try to compute more precise MFE from the OHLCV archive.
    Returns MFE in R or None if archive data unavailable.
    """
    try:
        from utils.ohlcv_archive import get_candles
        from datetime import datetime
        import pandas as pd

        market    = trade.get("market", "FOREX")
        symbol    = trade.get("symbol", "")
        entry_ts  = trade.get("entry_time")
        exit_ts   = trade.get("exit_time")
        entry_p   = trade.get("entry_price") or 0.0
        sl        = trade.get("stop_loss") or 0.0
        direction = (trade.get("direction") or "").upper()

        if not all([market, symbol, entry_ts, exit_ts, entry_p, sl]):
            return None

        sl_dist = abs(entry_p - sl)
        if sl_dist == 0:
            return None

        df = get_candles(market, symbol, "15m", from_dt=str(entry_ts)[:16], to_dt=str(exit_ts)[:16])
        if df is None or df.empty:
            return None

        if direction == "BULLISH":
            max_price = float(df["high"].max())
            return abs(max_price - entry_p) / sl_dist
        else:
            min_price = float(df["low"].min())
            return abs(min_price - entry_p) / sl_dist
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Backfill existing trades
# ---------------------------------------------------------------------------

def backfill_outcomes(account: str = None, dry_run: bool = False) -> dict:
    """
    Compute and store mfe_r, mae_r, exit_type for all closed trades missing them.
    Returns summary: {updated, skipped, errors}
    """
    init_db()
    with _connect() as conn:
        where = "WHERE result IS NOT NULL AND (mfe_r IS NULL OR exit_type IS NULL)"
        if account:
            where += f" AND account = '{account}'"
        rows = conn.execute(f"""
            SELECT trade_id, symbol, direction, market, account,
                   entry_price, stop_loss, target1, target2, target3,
                   exit_price, exit_reason, pnl_usd, r_multiple,
                   targets_hit, result, entry_time, exit_time
            FROM trades {where}
        """).fetchall()

    trades = [dict(r) for r in rows]
    updated = skipped = errors = 0

    for t in trades:
        try:
            exit_type     = tag_exit_type(t)
            mfe_r, mae_r  = compute_mfe_mae(t)

            if not dry_run:
                with _connect() as conn:
                    conn.execute("""
                        UPDATE trades SET exit_type=?, mfe_r=?, mae_r=?
                        WHERE trade_id=?
                    """, (exit_type, mfe_r, mae_r, t["trade_id"]))
            updated += 1
        except Exception as e:
            logger.debug(f"Outcome backfill failed {t.get('trade_id')}: {e}")
            errors += 1

    return {"updated": updated, "skipped": skipped, "errors": errors, "dry_run": dry_run}
