"""
OHLCV Archive — storage layer for historical candle data.
Backed by the candles table in data/cb6_trades.db.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from utils.trade_db import DB_PATH, _connect, init_db


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_candles(market: str, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
    """
    Upsert a DataFrame of candles into the archive.
    df must have columns: timestamp (datetime or ISO str), open, high, low, close, volume.
    Optional column: oi (open interest, NSE only).
    Returns number of rows inserted/replaced.
    """
    if df is None or df.empty:
        return 0

    init_db()
    rows = []
    for _, row in df.iterrows():
        ts = row.get("timestamp", row.name)
        if isinstance(ts, pd.Timestamp):
            ts = ts.isoformat()
        elif isinstance(ts, datetime):
            ts = ts.isoformat()
        else:
            ts = str(ts)

        rows.append((
            market, symbol, timeframe, ts,
            float(row.get("open",   row.get("Open",   0))),
            float(row.get("high",   row.get("High",   0))),
            float(row.get("low",    row.get("Low",    0))),
            float(row.get("close",  row.get("Close",  0))),
            float(row.get("volume", row.get("Volume", 0)) or 0),
            float(row["oi"]) if "oi" in row and row["oi"] is not None else None,
        ))

    if not rows:
        return 0

    with _connect() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO candles
                (market, symbol, timeframe, ts, open, high, low, close, volume, oi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

    return len(rows)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_candles(market: str, symbol: str, timeframe: str,
                from_dt: Optional[str] = None,
                to_dt: Optional[str] = None,
                limit: int = 500) -> pd.DataFrame:
    """
    Retrieve candles from the archive as a DataFrame.
    from_dt / to_dt: ISO datetime strings (UTC). None = no bound.
    limit: max rows (most recent first if no date bounds, else oldest first).
    """
    init_db()
    clauses = ["market = ?", "symbol = ?", "timeframe = ?"]
    params  = [market, symbol, timeframe]

    if from_dt:
        clauses.append("ts >= ?")
        params.append(from_dt)
    if to_dt:
        clauses.append("ts <= ?")
        params.append(to_dt)

    where = " AND ".join(clauses)
    order = "ASC" if from_dt else "DESC"

    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT ts, open, high, low, close, volume, oi
            FROM candles
            WHERE {where}
            ORDER BY ts {order}
            LIMIT ?
        """, params + [limit]).fetchall()

    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if order == "DESC":
        df = df.iloc[::-1].reset_index(drop=True)
    return df


def get_latest_bar_time(market: str, symbol: str, timeframe: str) -> Optional[str]:
    """Return ISO timestamp of the most recent stored candle, or None."""
    init_db()
    with _connect() as conn:
        row = conn.execute("""
            SELECT MAX(ts) FROM candles
            WHERE market = ? AND symbol = ? AND timeframe = ?
        """, (market, symbol, timeframe)).fetchone()
    val = row[0] if row else None
    return val


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def catalog() -> list[dict]:
    """Return a summary of what's in the archive: per symbol/TF bar count + date range."""
    init_db()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT market, symbol, timeframe,
                   COUNT(*) AS bars,
                   MIN(ts) AS oldest,
                   MAX(ts) AS newest
            FROM candles
            GROUP BY market, symbol, timeframe
            ORDER BY market, symbol, timeframe
        """).fetchall()
    return [dict(r) for r in rows]
