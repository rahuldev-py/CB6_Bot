# ml_engine/memory/trade_pattern_db.py
#
# Searchable trade pattern database using SQLite FTS5.
# Every closed trade gets written here. Claude Code can query it via
# /pattern-search to find historical setups matching current conditions.
#
# Schema designed for ICT Silver Bullet setups across NSE + GFT.

import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(_ROOT, 'ml_engine', 'memory', 'trade_pattern_db.sqlite')

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if not exists. Safe to call on every startup."""
    with _lock:
        conn = _connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id      TEXT UNIQUE,
                recorded_at   TEXT,

                -- Instrument
                market        TEXT,   -- 'forex' or 'nse'
                account       TEXT,   -- 'gft_5k', 'gft_1k', 'nse_fyers', 'ftmo'
                symbol        TEXT,
                direction     TEXT,   -- 'BULLISH'/'BEARISH' or 'LONG'/'SHORT'

                -- Session context
                session       TEXT,   -- 'London', 'NY', 'Morning_SB', 'Afternoon_SB'
                h4_bias       TEXT,   -- 'BULLISH', 'BEARISH', 'RANGING'
                h4_aligned    INTEGER, -- 1 if direction matches h4_bias

                -- Setup quality
                setup_type    TEXT,   -- 'DOL_SWEEP_OB_BOS_FVG', 'CHOCH_ONLY', etc.
                mss_type      TEXT,   -- 'CHOCH', 'BOS'
                confluence    INTEGER,
                fvg_body_pct  REAL,
                sweep_age_ca  INTEGER, -- candles since sweep

                -- Trade mechanics
                entry_price   REAL,
                sl_price      REAL,
                sl_distance   REAL,
                risk_usd      REAL,
                risk_mode     TEXT,   -- 'normal', 'reduced', 'aplus'

                -- Outcome
                outcome       TEXT,   -- 'WIN', 'LOSS', 'BREAKEVEN'
                exit_reason   TEXT,   -- 'T1', 'T2', 'T3', 'SL', 'MAE_EXIT', 'TIME_EXIT'
                targets_hit   TEXT,   -- JSON array e.g. '["T1","T2"]'
                pnl_usd       REAL,
                pnl_r         REAL,   -- R-multiple
                hold_minutes  INTEGER,

                -- Notes
                notes         TEXT
            );

            -- FTS5 virtual table for full-text search across text fields
            CREATE VIRTUAL TABLE IF NOT EXISTS trades_fts USING fts5(
                trade_id, market, account, symbol, direction,
                session, h4_bias, setup_type, mss_type, outcome, exit_reason, notes,
                content='trades', content_rowid='id'
            );

            -- Triggers to keep FTS5 in sync
            CREATE TRIGGER IF NOT EXISTS trades_ai AFTER INSERT ON trades BEGIN
                INSERT INTO trades_fts(rowid, trade_id, market, account, symbol, direction,
                    session, h4_bias, setup_type, mss_type, outcome, exit_reason, notes)
                VALUES (new.id, new.trade_id, new.market, new.account, new.symbol, new.direction,
                    new.session, new.h4_bias, new.setup_type, new.mss_type, new.outcome,
                    new.exit_reason, new.notes);
            END;

            CREATE TRIGGER IF NOT EXISTS trades_ad AFTER DELETE ON trades BEGIN
                INSERT INTO trades_fts(trades_fts, rowid, trade_id, market, account, symbol,
                    direction, session, h4_bias, setup_type, mss_type, outcome, exit_reason, notes)
                VALUES ('delete', old.id, old.trade_id, old.market, old.account, old.symbol,
                    old.direction, old.session, old.h4_bias, old.setup_type, old.mss_type,
                    old.outcome, old.exit_reason, old.notes);
            END;

            CREATE TRIGGER IF NOT EXISTS trades_au AFTER UPDATE ON trades BEGIN
                INSERT INTO trades_fts(trades_fts, rowid, trade_id, market, account, symbol,
                    direction, session, h4_bias, setup_type, mss_type, outcome, exit_reason, notes)
                VALUES ('delete', old.id, old.trade_id, old.market, old.account, old.symbol,
                    old.direction, old.session, old.h4_bias, old.setup_type, old.mss_type,
                    old.outcome, old.exit_reason, old.notes);
                INSERT INTO trades_fts(rowid, trade_id, market, account, symbol, direction,
                    session, h4_bias, setup_type, mss_type, outcome, exit_reason, notes)
                VALUES (new.id, new.trade_id, new.market, new.account, new.symbol, new.direction,
                    new.session, new.h4_bias, new.setup_type, new.mss_type, new.outcome,
                    new.exit_reason, new.notes);
            END;

            -- Aggregate stats view — used by /pattern-search and parameter-optimizer
            CREATE VIEW IF NOT EXISTS pattern_stats AS
            SELECT
                symbol, direction, session, h4_bias, setup_type, mss_type,
                COUNT(*)                                         AS total,
                SUM(CASE WHEN outcome='WIN'  THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) AS losses,
                ROUND(100.0 * SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                                 AS win_rate_pct,
                ROUND(AVG(pnl_r), 2)                             AS avg_r,
                ROUND(AVG(fvg_body_pct), 1)                      AS avg_fvg_body_pct,
                ROUND(AVG(confluence), 1)                        AS avg_confluence
            FROM trades
            GROUP BY symbol, direction, session, h4_bias, setup_type, mss_type
            HAVING COUNT(*) >= 3;
        """)
        conn.commit()
        conn.close()


def record_trade(trade: dict, market: str, account: str,
                 session: str = '', h4_bias: str = '',
                 setup_type: str = 'DOL_SWEEP_OB_BOS_FVG',
                 fvg_body_pct: float = 0.0, sweep_age_ca: int = 0,
                 notes: str = ''):
    """
    Write a closed trade to the pattern DB.
    Called from feedback_loop.py after every trade close.
    """
    try:
        entry  = trade.get('entry_price', 0)
        sl     = trade.get('stop_loss', 0)
        sl_dist = round(abs(entry - sl), 5) if entry and sl else 0
        pnl_usd = trade.get('pnl_usd', 0)
        risk_usd = trade.get('risk_usd', 1)
        pnl_r   = round(pnl_usd / risk_usd, 2) if risk_usd else 0

        outcome = 'WIN' if pnl_usd > 0 else ('BREAKEVEN' if pnl_usd == 0 else 'LOSS')

        def _parse_dt(s):
            if not s:
                return None
            for fmt in ('%Y-%m-%d %H:%M:%S', '%H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    pass
            return None

        entry_dt  = _parse_dt(trade.get('entry_time'))
        exit_dt   = _parse_dt(trade.get('exit_time'))
        hold_mins = int((exit_dt - entry_dt).total_seconds() / 60) if entry_dt and exit_dt else 0

        direction  = trade.get('direction', '')
        h4_aligned = 1 if (
            (direction in ('BULLISH', 'LONG')  and h4_bias == 'BULLISH') or
            (direction in ('BEARISH', 'SHORT') and h4_bias == 'BEARISH')
        ) else 0

        import json
        with _lock:
            conn = _connect()
            conn.execute("""
                INSERT OR IGNORE INTO trades (
                    trade_id, recorded_at, market, account, symbol, direction,
                    session, h4_bias, h4_aligned, setup_type, mss_type,
                    confluence, fvg_body_pct, sweep_age_ca,
                    entry_price, sl_price, sl_distance, risk_usd, risk_mode,
                    outcome, exit_reason, targets_hit, pnl_usd, pnl_r,
                    hold_minutes, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade.get('id', ''),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                market, account,
                trade.get('symbol', ''), direction,
                session, h4_bias, h4_aligned,
                setup_type, trade.get('mss_type', ''),
                trade.get('confluence', 0), fvg_body_pct, sweep_age_ca,
                entry, sl, sl_dist,
                risk_usd, trade.get('risk_mode', 'normal'),
                outcome, trade.get('exit_reason', ''),
                json.dumps(trade.get('targets_hit', [])),
                pnl_usd, pnl_r, hold_mins, notes
            ))
            conn.commit()
            conn.close()
    except Exception as e:
        from utils.logger import logger
        logger.warning(f"trade_pattern_db: failed to record trade {trade.get('id')}: {e}")


def query(symbol: str = '', direction: str = '', session: str = '',
          h4_bias: str = '', min_confluence: int = 0,
          min_fvg_body: float = 0.0, outcome: str = '',
          fts_text: str = '') -> list:
    """
    Flexible query for /pattern-search.
    Returns list of matching trade rows as dicts.
    """
    conn = _connect()
    clauses, params = [], []

    if fts_text:
        ids = [r[0] for r in conn.execute(
            "SELECT rowid FROM trades_fts WHERE trades_fts MATCH ?", (fts_text,)
        ).fetchall()]
        if not ids:
            conn.close()
            return []
        clauses.append(f"id IN ({','.join('?' * len(ids))})")
        params.extend(ids)

    if symbol:       clauses.append("symbol=?");             params.append(symbol.upper())
    if direction:    clauses.append("direction=?");          params.append(direction.upper())
    if session:      clauses.append("session=?");            params.append(session)
    if h4_bias:      clauses.append("h4_bias=?");            params.append(h4_bias.upper())
    if min_confluence: clauses.append("confluence>=?");      params.append(min_confluence)
    if min_fvg_body:   clauses.append("fvg_body_pct>=?");   params.append(min_fvg_body)
    if outcome:      clauses.append("outcome=?");            params.append(outcome.upper())

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM trades {where} ORDER BY recorded_at DESC LIMIT 200", params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(symbol: str = '', session: str = '', h4_bias: str = '') -> list:
    """Return pattern_stats view rows filtered by optional criteria."""
    conn = _connect()
    clauses, params = [], []
    if symbol:   clauses.append("symbol=?");   params.append(symbol.upper())
    if session:  clauses.append("session=?");  params.append(session)
    if h4_bias:  clauses.append("h4_bias=?");  params.append(h4_bias.upper())
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM pattern_stats {where} ORDER BY total DESC", params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def backfill_from_state(state_path: str, market: str, account: str):
    """
    One-time backfill: import all closed_trades from an existing state.json
    into the pattern DB. Safe to call repeatedly — INSERT OR IGNORE prevents dupes.
    """
    import json
    try:
        with open(state_path, encoding='utf-8-sig') as f:
            state = json.load(f)
        for t in state.get('closed_trades', []):
            record_trade(
                t, market=market, account=account,
                session=t.get('session', ''),
                h4_bias=t.get('h4_bias', ''),
            )
        from utils.logger import logger
        logger.info(f"trade_pattern_db: backfilled {len(state.get('closed_trades', []))} trades from {state_path}")
    except Exception as e:
        from utils.logger import logger
        logger.warning(f"trade_pattern_db: backfill failed for {state_path}: {e}")


# Auto-init on import
init_db()
