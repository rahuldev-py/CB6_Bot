"""
Unified trade database — SQLite store for all CB6 Quantum trade records.
Sources: state.json files (FTMO, GFT), JSONL trade files (forex, NSE).
DB path: data/cb6_trades.db
"""

import json
import sqlite3
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "cb6_trades.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id        TEXT PRIMARY KEY,
    ticket          INTEGER,
    market          TEXT,           -- FOREX | NSE
    account         TEXT,           -- FTMO | GFT_5K | NSE_PAPER | NSE_LIVE
    broker          TEXT,
    mode            TEXT,           -- live | paper | free_trial
    symbol          TEXT,
    direction       TEXT,           -- BULLISH | BEARISH
    lots            REAL,
    risk_usd        REAL,
    risk_mode       TEXT,
    entry_price     REAL,
    stop_loss       REAL,
    target1         REAL,
    target2         REAL,
    target3         REAL,
    score           INTEGER,
    mss_type        TEXT,           -- CHOCH | BOS
    entry_time      TEXT,
    entry_reason    TEXT,
    spread_at_entry REAL,
    sim_ratio       REAL,           -- A+ similarity score
    lot_boost       REAL,
    is_aplus        INTEGER,        -- 0/1
    session         TEXT,
    utc_hour        INTEGER,
    day_of_week     INTEGER,
    day_name        TEXT,
    week_of_month   INTEGER,
    exit_time       TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    pnl_usd         REAL,
    r_multiple      REAL,
    targets_hit     TEXT,           -- JSON array e.g. '["T1","T2"]'
    be_triggered    INTEGER,        -- 0/1
    hold_time_min   INTEGER,
    result          TEXT,           -- WIN | LOSS | BE
    phase           TEXT,           -- GFT: phase_1 | phase_2
    source_file     TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trade_context (
    trade_id            TEXT PRIMARY KEY,
    fvg_low             REAL,
    fvg_high            REAL,
    fvg_size            REAL,
    fvg_equilibrium     REAL,
    fvg_in_discount     INTEGER,
    sweep_type          TEXT,
    sweep_candles_ago   INTEGER,
    sweep_confirmed     INTEGER,
    sweep_confidence    REAL,
    sweep_wick_ratio    REAL,
    sweep_volume_spike  REAL,
    sweep_displacement  REAL,
    dol_direction       TEXT,
    dol_price           REAL,
    ob_present          INTEGER,
    h4_bias             TEXT,
    h1_bias             TEXT,
    h4_aligned          INTEGER,
    h1_aligned          INTEGER,
    in_kill_zone        INTEGER,
    raw_entry_json      TEXT,
    raw_outcome_json    TEXT,
    -- Phase 3.5 replay fields: captured at entry time
    regime_4h           TEXT,       -- TRENDING_UP | TRENDING_DOWN | RANGING | CHOPPY
    regime_1h           TEXT,
    volatility_at_entry TEXT,       -- HIGH | NORMAL | LOW
    adx_at_entry        REAL,
    corr_nifty_bank     REAL,       -- correlation NIFTY50 ↔ NIFTYBANK at entry
    corr_silver_oil     REAL,       -- correlation XAGUSD ↔ USOIL at entry
    oi_pcr              REAL,       -- PCR at entry (NSE only)
    oi_bias             TEXT,       -- BULLISH | BEARISH | NEUTRAL
    oi_max_ce_strike    INTEGER,    -- max CE OI strike (resistance) at entry
    oi_max_pe_strike    INTEGER,    -- max PE OI strike (support) at entry
    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_account    ON trades(account);
CREATE INDEX IF NOT EXISTS idx_trades_symbol     ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_result     ON trades(result);

CREATE TABLE IF NOT EXISTS candles (
    market     TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    timeframe  TEXT NOT NULL,   -- 15m | 1h | 4h | D
    ts         TEXT NOT NULL,   -- ISO datetime UTC
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     REAL,
    oi         REAL,            -- NSE: open interest (NULL for forex)
    PRIMARY KEY (market, symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(market, symbol, timeframe, ts);

CREATE TABLE IF NOT EXISTS oi_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,      -- NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY
    ts          TEXT NOT NULL,      -- ISO datetime IST
    expiry      TEXT NOT NULL,      -- YYYY-MM-DD
    atm_strike  INTEGER,
    spot_price  REAL,
    ce_oi       REAL,
    pe_oi       REAL,
    ce_volume   REAL,
    pe_volume   REAL,
    pcr_oi      REAL,
    pcr_volume  REAL,
    option_bias TEXT,               -- BULLISH | BEARISH | NEUTRAL
    source      TEXT,               -- truedata | sensibull | fyers
    UNIQUE(symbol, ts, expiry)
);
CREATE INDEX IF NOT EXISTS idx_oi_symbol_ts ON oi_snapshots(symbol, ts);

CREATE TABLE IF NOT EXISTS option_chain (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    ts          TEXT NOT NULL,
    expiry      TEXT NOT NULL,
    strike      INTEGER NOT NULL,
    ce_ltp      REAL,
    ce_iv       REAL,
    ce_oi       REAL,
    ce_volume   REAL,
    ce_delta    REAL,
    ce_theta    REAL,
    pe_ltp      REAL,
    pe_iv       REAL,
    pe_oi       REAL,
    pe_volume   REAL,
    pe_delta    REAL,
    pe_theta    REAL,
    UNIQUE(symbol, ts, expiry, strike)
);
CREATE INDEX IF NOT EXISTS idx_chain_symbol_ts ON option_chain(symbol, ts, expiry);
"""


_MIGRATIONS = [
    # Phase 3.5: trade_context replay fields
    "ALTER TABLE trade_context ADD COLUMN regime_4h TEXT",
    "ALTER TABLE trade_context ADD COLUMN regime_1h TEXT",
    "ALTER TABLE trade_context ADD COLUMN volatility_at_entry TEXT",
    "ALTER TABLE trade_context ADD COLUMN adx_at_entry REAL",
    "ALTER TABLE trade_context ADD COLUMN corr_nifty_bank REAL",
    "ALTER TABLE trade_context ADD COLUMN corr_silver_oil REAL",
    "ALTER TABLE trade_context ADD COLUMN oi_pcr REAL",
    "ALTER TABLE trade_context ADD COLUMN oi_bias TEXT",
    "ALTER TABLE trade_context ADD COLUMN oi_max_ce_strike INTEGER",
    "ALTER TABLE trade_context ADD COLUMN oi_max_pe_strike INTEGER",
    # Phase 4: outcome fields on trades table
    "ALTER TABLE trades ADD COLUMN mfe_r REAL",
    "ALTER TABLE trades ADD COLUMN mae_r REAL",
    "ALTER TABLE trades ADD COLUMN exit_type TEXT",
    # Phase 7: conviction fields on trade_context
    "ALTER TABLE trade_context ADD COLUMN conviction_score REAL",
    "ALTER TABLE trade_context ADD COLUMN conviction_grade TEXT",
    "ALTER TABLE trade_context ADD COLUMN conviction_components TEXT",
    "ALTER TABLE trade_context ADD COLUMN conviction_risk_mult REAL",
    "ALTER TABLE trade_context ADD COLUMN conviction_hard_block INTEGER",
    "ALTER TABLE trade_context ADD COLUMN conviction_reasons TEXT",
]


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _run_migrations(conn):
    """Apply schema migrations — silently skip if column already exists."""
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _run_migrations(conn)


def _result_from_pnl(pnl_usd):
    if pnl_usd is None:
        return None
    if pnl_usd > 0:
        return "WIN"
    if pnl_usd < 0:
        return "LOSS"
    return "BE"


def _upsert_trade(conn, row: dict):
    conn.execute("""
        INSERT INTO trades (
            trade_id, ticket, market, account, broker, mode,
            symbol, direction, lots, risk_usd, risk_mode,
            entry_price, stop_loss, target1, target2, target3,
            score, mss_type, entry_time, entry_reason, spread_at_entry,
            sim_ratio, lot_boost, is_aplus,
            session, utc_hour, day_of_week, day_name, week_of_month,
            exit_time, exit_price, exit_reason,
            pnl_usd, r_multiple, targets_hit, be_triggered, hold_time_min,
            result, phase, source_file, updated_at
        ) VALUES (
            :trade_id, :ticket, :market, :account, :broker, :mode,
            :symbol, :direction, :lots, :risk_usd, :risk_mode,
            :entry_price, :stop_loss, :target1, :target2, :target3,
            :score, :mss_type, :entry_time, :entry_reason, :spread_at_entry,
            :sim_ratio, :lot_boost, :is_aplus,
            :session, :utc_hour, :day_of_week, :day_name, :week_of_month,
            :exit_time, :exit_price, :exit_reason,
            :pnl_usd, :r_multiple, :targets_hit, :be_triggered, :hold_time_min,
            :result, :phase, :source_file, datetime('now')
        )
        ON CONFLICT(trade_id) DO UPDATE SET
            ticket          = excluded.ticket,
            exit_time       = excluded.exit_time,
            exit_price      = excluded.exit_price,
            exit_reason     = excluded.exit_reason,
            pnl_usd         = excluded.pnl_usd,
            r_multiple      = excluded.r_multiple,
            targets_hit     = excluded.targets_hit,
            be_triggered    = excluded.be_triggered,
            hold_time_min   = excluded.hold_time_min,
            result          = excluded.result,
            sim_ratio       = COALESCE(excluded.sim_ratio, trades.sim_ratio),
            lot_boost       = COALESCE(excluded.lot_boost, trades.lot_boost),
            is_aplus        = COALESCE(excluded.is_aplus, trades.is_aplus),
            session         = COALESCE(excluded.session, trades.session),
            utc_hour        = COALESCE(excluded.utc_hour, trades.utc_hour),
            entry_reason    = COALESCE(excluded.entry_reason, trades.entry_reason),
            spread_at_entry = COALESCE(excluded.spread_at_entry, trades.spread_at_entry),
            updated_at      = datetime('now')
    """, row)


def _upsert_context(conn, ctx: dict):
    conn.execute("""
        INSERT INTO trade_context (
            trade_id, fvg_low, fvg_high, fvg_size, fvg_equilibrium, fvg_in_discount,
            sweep_type, sweep_candles_ago, sweep_confirmed, sweep_confidence,
            sweep_wick_ratio, sweep_volume_spike, sweep_displacement,
            dol_direction, dol_price, ob_present,
            h4_bias, h1_bias, h4_aligned, h1_aligned, in_kill_zone,
            raw_entry_json, raw_outcome_json
        ) VALUES (
            :trade_id, :fvg_low, :fvg_high, :fvg_size, :fvg_equilibrium, :fvg_in_discount,
            :sweep_type, :sweep_candles_ago, :sweep_confirmed, :sweep_confidence,
            :sweep_wick_ratio, :sweep_volume_spike, :sweep_displacement,
            :dol_direction, :dol_price, :ob_present,
            :h4_bias, :h1_bias, :h4_aligned, :h1_aligned, :in_kill_zone,
            :raw_entry_json, :raw_outcome_json
        )
        ON CONFLICT(trade_id) DO UPDATE SET
            raw_outcome_json = COALESCE(excluded.raw_outcome_json, trade_context.raw_outcome_json),
            sweep_confidence = COALESCE(excluded.sweep_confidence, trade_context.sweep_confidence),
            sweep_wick_ratio = COALESCE(excluded.sweep_wick_ratio, trade_context.sweep_wick_ratio),
            sweep_volume_spike = COALESCE(excluded.sweep_volume_spike, trade_context.sweep_volume_spike),
            sweep_displacement = COALESCE(excluded.sweep_displacement, trade_context.sweep_displacement),
            h4_bias          = COALESCE(excluded.h4_bias, trade_context.h4_bias),
            h1_bias          = COALESCE(excluded.h1_bias, trade_context.h1_bias)
    """, ctx)


# ---------------------------------------------------------------------------
# Sync from state.json
# ---------------------------------------------------------------------------

def sync_from_state(state_path: str, account: str, market: str = "FOREX") -> int:
    """Load closed_trades from a state.json file into the DB. Returns rows inserted/updated."""
    path = Path(state_path)
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        state = json.load(f)

    broker = state.get("broker", account.lower())
    mode = state.get("mode", "live")
    closed = state.get("closed_trades", [])
    if not closed:
        return 0

    count = 0
    with _connect() as conn:
        for t in closed:
            targets_hit = t.get("targets_hit", [])
            pnl = t.get("pnl_usd")
            row = {
                "trade_id":      t.get("id"),
                "ticket":        t.get("ticket"),
                "market":        market,
                "account":       account,
                "broker":        broker,
                "mode":          mode,
                "symbol":        t.get("symbol"),
                "direction":     t.get("direction"),
                "lots":          t.get("lots"),
                "risk_usd":      t.get("risk_usd"),
                "risk_mode":     t.get("risk_mode"),
                "entry_price":   t.get("entry_price"),
                "stop_loss":     t.get("stop_loss"),
                "target1":       t.get("target1"),
                "target2":       t.get("target2"),
                "target3":       t.get("target3"),
                "score":         t.get("confluence"),
                "mss_type":      t.get("mss_type"),
                "entry_time":    t.get("entry_time"),
                "entry_reason":  t.get("entry_reason"),
                "spread_at_entry": t.get("spread_at_entry"),
                "sim_ratio":     t.get("sim_ratio"),
                "lot_boost":     t.get("lot_boost"),
                "is_aplus":      None,
                "session":       None,
                "utc_hour":      None,
                "day_of_week":   None,
                "day_name":      None,
                "week_of_month": None,
                "exit_time":     t.get("exit_time"),
                "exit_price":    t.get("exit_price"),
                "exit_reason":   t.get("exit_reason"),
                "pnl_usd":       pnl,
                "r_multiple":    t.get("actual_rrr"),
                "targets_hit":   json.dumps(targets_hit),
                "be_triggered":  1 if t.get("be_triggered") else 0,
                "hold_time_min": None,
                "result":        _result_from_pnl(pnl),
                "phase":         t.get("phase"),
                "source_file":   str(path),
            }
            if row["trade_id"] is None:
                continue
            _upsert_trade(conn, row)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Sync from JSONL (ENTRY + OUTCOME pairs)
# ---------------------------------------------------------------------------

def sync_from_jsonl(jsonl_path: str) -> int:
    """Load ENTRY+OUTCOME pairs from a JSONL trade file. Returns rows upserted."""
    path = Path(jsonl_path)
    if not path.exists():
        return 0

    entries = {}
    outcomes = {}

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtype = rec.get("_type")
            tid = rec.get("trade_id")
            if not tid:
                continue
            if rtype == "ENTRY":
                entries[tid] = rec
            elif rtype == "OUTCOME":
                outcomes[tid] = rec.get("outcome", rec)

    if not entries:
        return 0

    count = 0
    with _connect() as conn:
        for tid, e in entries.items():
            o = outcomes.get(tid, {})
            pnl = o.get("pnl_usd") if o else None
            targets_hit = o.get("targets_hit", []) if o else []
            hold = o.get("hold_time_minutes") if o else None
            if hold is not None and hold < 0:
                hold = abs(hold)

            account = e.get("account", "UNKNOWN")
            market  = e.get("market", "FOREX")
            mode    = e.get("mode", "live")

            row = {
                "trade_id":      tid,
                "ticket":        None,
                "market":        market,
                "account":       account,
                "broker":        account.lower(),
                "mode":          mode,
                "symbol":        e.get("symbol"),
                "direction":     e.get("direction"),
                "lots":          e.get("lots"),
                "risk_usd":      e.get("risk_usd"),
                "risk_mode":     e.get("risk_mode"),
                "entry_price":   e.get("entry_price"),
                "stop_loss":     e.get("stop_loss"),
                "target1":       e.get("target_1"),
                "target2":       e.get("target_2"),
                "target3":       e.get("target_3"),
                "score":         e.get("score"),
                "mss_type":      e.get("mss_type"),
                "entry_time":    e.get("timestamp_utc"),
                "entry_reason":  None,
                "spread_at_entry": e.get("spread_at_entry"),
                "sim_ratio":     e.get("aplus_sim_ratio"),
                "lot_boost":     e.get("aplus_lot_boost"),
                "is_aplus":      1 if e.get("is_aplus") else 0,
                "session":       e.get("session"),
                "utc_hour":      e.get("utc_hour"),
                "day_of_week":   e.get("day_of_week"),
                "day_name":      e.get("day_name"),
                "week_of_month": e.get("week_of_month"),
                "exit_time":     o.get("timestamp_exit_utc") if o else None,
                "exit_price":    o.get("exit_price") if o else None,
                "exit_reason":   o.get("exit_reason") if o else None,
                "pnl_usd":       pnl,
                "r_multiple":    o.get("r_multiple") if o else None,
                "targets_hit":   json.dumps(targets_hit),
                "be_triggered":  None,
                "hold_time_min": hold,
                "result":        o.get("result") if o else None,
                "phase":         None,
                "source_file":   str(path),
            }
            _upsert_trade(conn, row)

            ctx = {
                "trade_id":          tid,
                "fvg_low":           e.get("fvg_low"),
                "fvg_high":          e.get("fvg_high"),
                "fvg_size":          e.get("fvg_size"),
                "fvg_equilibrium":   e.get("fvg_equilibrium"),
                "fvg_in_discount":   1 if e.get("fvg_in_discount") else 0,
                "sweep_type":        e.get("sweep_type"),
                "sweep_candles_ago": e.get("sweep_candles_ago"),
                "sweep_confirmed":   1 if e.get("sweep_confirmed") else 0,
                "sweep_confidence":  e.get("sweep_confidence"),
                "sweep_wick_ratio":  e.get("sweep_wick_ratio"),
                "sweep_volume_spike": e.get("sweep_volume_spike"),
                "sweep_displacement": e.get("sweep_displacement"),
                "dol_direction":     e.get("dol_direction"),
                "dol_price":         e.get("dol_price"),
                "ob_present":        1 if e.get("ob_present") else 0,
                "h4_bias":           e.get("h4_bias"),
                "h1_bias":           e.get("h1_bias"),
                "h4_aligned":        1 if e.get("h4_aligned") else 0,
                "h1_aligned":        1 if e.get("h1_aligned") else 0,
                "in_kill_zone":      1 if e.get("in_kill_zone") else 0,
                "raw_entry_json":    json.dumps(e),
                "raw_outcome_json":  json.dumps(o) if o else None,
            }
            _upsert_context(conn, ctx)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Sync all sources (main entry point)
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent

SOURCES = {
    "state_files": [
        (BASE / "data/ftmo_10k/state.json",  "FTMO",    "FOREX"),
        (BASE / "data/gft_5k/state.json",    "GFT_5K",  "FOREX"),
        (BASE / "data/paper_state.json",     "NSE_PAPER", "NSE"),
        (BASE / "data/forex_paper_state.json", "FOREX_PAPER", "FOREX"),
    ],
    "jsonl_files": [
        BASE / "data/ml/forex/ftmo_trades.jsonl",
        BASE / "data/ml/forex/gft_trades.jsonl",
        BASE / "data/ml/nse/trades.jsonl",
    ],
}


def sync_all(verbose: bool = True) -> dict:
    """Sync all sources into the DB. Returns summary counts."""
    init_db()
    summary = {}

    for state_path, account, market in SOURCES["state_files"]:
        n = sync_from_state(str(state_path), account, market)
        if n or verbose:
            summary[f"state:{account}"] = n

    for jsonl_path in SOURCES["jsonl_files"]:
        n = sync_from_jsonl(str(jsonl_path))
        label = f"jsonl:{Path(jsonl_path).stem}"
        if n or verbose:
            summary[label] = n

    return summary


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return list of dicts."""
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_trade(trade_id: str) -> dict | None:
    """Fetch a single trade with its context, fully joined."""
    rows = query("""
        SELECT t.*, c.fvg_low, c.fvg_high, c.fvg_size, c.fvg_equilibrium, c.fvg_in_discount,
               c.sweep_type, c.sweep_candles_ago, c.sweep_confirmed, c.sweep_confidence,
               c.sweep_displacement, c.dol_direction, c.dol_price,
               c.h4_bias, c.h1_bias, c.h4_aligned, c.h1_aligned, c.in_kill_zone,
               c.raw_entry_json, c.raw_outcome_json
        FROM trades t
        LEFT JOIN trade_context c ON t.trade_id = c.trade_id
        WHERE t.trade_id = ?
    """, (trade_id,))
    return rows[0] if rows else None


def stats(account: str = None) -> dict:
    """Return aggregate stats, optionally filtered by account."""
    where = "WHERE account = ?" if account else ""
    params = (account,) if account else ()

    rows = query(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result='BE'   THEN 1 ELSE 0 END) AS be,
            ROUND(AVG(pnl_usd), 2)    AS avg_pnl,
            ROUND(SUM(pnl_usd), 2)    AS total_pnl,
            ROUND(AVG(r_multiple), 3) AS avg_r,
            ROUND(MAX(pnl_usd), 2)    AS best_trade,
            ROUND(MIN(pnl_usd), 2)    AS worst_trade
        FROM trades {where}
        WHERE result IS NOT NULL
    """, params)
    row = rows[0] if rows else {}
    total = row.get("total") or 0
    wins  = row.get("wins")  or 0
    row["win_rate"] = round(wins / total * 100, 1) if total else 0.0
    return row
