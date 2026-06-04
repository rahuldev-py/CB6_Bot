"""
OI Archive — storage layer for option chain snapshots.
Backed by oi_snapshots + option_chain tables in data/cb6_trades.db.
"""

from datetime import datetime
from typing import Optional
import pandas as pd

from utils.trade_db import _connect, init_db


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_oi_snapshot(symbol: str, ts: str, expiry: str, context: dict,
                     spot_price: float = 0.0) -> bool:
    """
    Save aggregate OI snapshot (PCR, CE/PE totals) from a chain context dict.
    context: output of fetch_option_chain_context() + calculate_option_pressure()
    Returns True on success.
    """
    if not context.get("data_available"):
        return False

    from nse_options.option_pressure_engine import calculate_option_pressure
    try:
        pressure = calculate_option_pressure(context)
    except Exception:
        pressure = {}

    init_db()
    try:
        with _connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO oi_snapshots
                    (symbol, ts, expiry, atm_strike, spot_price,
                     ce_oi, pe_oi, ce_volume, pe_volume,
                     pcr_oi, pcr_volume, option_bias, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, ts, expiry,
                context.get("atm"),
                spot_price,
                pressure.get("ce_oi"),
                pressure.get("pe_oi"),
                pressure.get("ce_volume"),
                pressure.get("pe_volume"),
                pressure.get("pcr_oi"),
                pressure.get("pcr_volume"),
                pressure.get("option_bias"),
                context.get("source", "unknown"),
            ))
        return True
    except Exception as e:
        from utils.logger import logger
        logger.warning(f"OI snapshot save failed: {e}")
        return False


def save_option_chain(symbol: str, ts: str, expiry: str, context: dict) -> int:
    """
    Save per-strike option chain rows. Returns number of strikes stored.
    """
    if not context.get("data_available"):
        return 0

    strikes = context.get("strikes", [])
    ce_data = context.get("ce", {})
    pe_data = context.get("pe", {})

    if not strikes:
        return 0

    rows = []
    for strike in strikes:
        ce = ce_data.get(strike, {})
        pe = pe_data.get(strike, {})
        rows.append((
            symbol, ts, expiry, int(strike),
            ce.get("ltp"), ce.get("iv"), ce.get("oi"), ce.get("volume"),
            ce.get("delta"), ce.get("theta"),
            pe.get("ltp"), pe.get("iv"), pe.get("oi"), pe.get("volume"),
            pe.get("delta"), pe.get("theta"),
        ))

    if not rows:
        return 0

    init_db()
    try:
        with _connect() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO option_chain
                    (symbol, ts, expiry, strike,
                     ce_ltp, ce_iv, ce_oi, ce_volume, ce_delta, ce_theta,
                     pe_ltp, pe_iv, pe_oi, pe_volume, pe_delta, pe_theta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
        return len(rows)
    except Exception as e:
        from utils.logger import logger
        logger.warning(f"Option chain save failed: {e}")
        return 0


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_pcr_history(symbol: str, from_dt: str = None,
                    to_dt: str = None, limit: int = 200) -> pd.DataFrame:
    """Return PCR history for a symbol as a DataFrame."""
    init_db()
    clauses = ["symbol = ?"]
    params  = [symbol]
    if from_dt:
        clauses.append("ts >= ?"); params.append(from_dt)
    if to_dt:
        clauses.append("ts <= ?"); params.append(to_dt)

    where = " AND ".join(clauses)
    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT ts, expiry, atm_strike, spot_price,
                   ce_oi, pe_oi, pcr_oi, pcr_volume, option_bias
            FROM oi_snapshots
            WHERE {where}
            ORDER BY ts DESC LIMIT ?
        """, params + [limit]).fetchall()

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "ts", "expiry", "atm_strike", "spot_price",
        "ce_oi", "pe_oi", "pcr_oi", "pcr_volume", "option_bias"
    ])
    df["ts"] = pd.to_datetime(df["ts"])
    return df.iloc[::-1].reset_index(drop=True)


def get_max_oi_strikes(symbol: str, ts: str = None, expiry: str = None) -> dict:
    """
    Return max OI CE and PE strikes at a given timestamp (or most recent).
    Useful for support/resistance levels.
    """
    init_db()
    if ts:
        # Find nearest snapshot at or before ts
        with _connect() as conn:
            row = conn.execute("""
                SELECT ts FROM oi_snapshots
                WHERE symbol = ? AND ts <= ?
                ORDER BY ts DESC LIMIT 1
            """, (symbol, ts)).fetchone()
        snap_ts = row[0] if row else None
    else:
        with _connect() as conn:
            row = conn.execute("""
                SELECT ts FROM oi_snapshots
                WHERE symbol = ?
                ORDER BY ts DESC LIMIT 1
            """, (symbol,)).fetchone()
        snap_ts = row[0] if row else None

    if not snap_ts:
        return {}

    with _connect() as conn:
        snap = conn.execute("""
            SELECT expiry, atm_strike, pcr_oi, option_bias FROM oi_snapshots
            WHERE symbol = ? AND ts = ?
            ORDER BY ts DESC LIMIT 1
        """, (symbol, snap_ts)).fetchone()

        if not snap:
            return {}

        snap_expiry = expiry or snap[0]

        # Max CE OI strike = resistance
        ce_row = conn.execute("""
            SELECT strike, ce_oi FROM option_chain
            WHERE symbol = ? AND ts = ? AND expiry = ?
            ORDER BY ce_oi DESC LIMIT 1
        """, (symbol, snap_ts, snap_expiry)).fetchone()

        # Max PE OI strike = support
        pe_row = conn.execute("""
            SELECT strike, pe_oi FROM option_chain
            WHERE symbol = ? AND ts = ? AND expiry = ?
            ORDER BY pe_oi DESC LIMIT 1
        """, (symbol, snap_ts, snap_expiry)).fetchone()

    return {
        "symbol":          symbol,
        "as_of":           snap_ts,
        "expiry":          snap_expiry,
        "atm":             snap[1],
        "pcr_oi":          snap[2],
        "option_bias":     snap[3],
        "max_ce_strike":   ce_row[0] if ce_row else None,   # resistance
        "max_ce_oi":       ce_row[1] if ce_row else None,
        "max_pe_strike":   pe_row[0] if pe_row else None,   # support
        "max_pe_oi":       pe_row[1] if pe_row else None,
    }


def oi_catalog() -> list[dict]:
    """Summary of OI archive coverage."""
    init_db()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT symbol,
                   COUNT(*) AS snapshots,
                   MIN(ts) AS oldest,
                   MAX(ts) AS newest
            FROM oi_snapshots
            GROUP BY symbol
            ORDER BY symbol
        """).fetchall()
    return [dict(r) for r in rows]
