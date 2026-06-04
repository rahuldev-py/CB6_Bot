"""
Trade Replay Enrichment — CB6 Quantum Phase 3.5
Captures regime, correlation, and OI context at trade entry time,
storing it in the trade_context table for post-trade analysis.

Call capture_entry_context(trade_id, market, symbol) immediately after
a trade is opened. Non-blocking — any failure is silently logged.

Also provides query helpers for replaying a trade with full context.
"""

import json
from datetime import datetime
from typing import Optional

from utils.trade_db import _connect, init_db
from utils.logger import logger


# ---------------------------------------------------------------------------
# Capture at entry
# ---------------------------------------------------------------------------

def capture_entry_context(
    trade_id: str,
    market: str,           # FOREX | NSE
    symbol: str,           # e.g. XAGUSD, NSE:NIFTY50-INDEX
    direction: str = "",   # BULLISH | BEARISH
    setup: dict = None,    # ICT setup dict — enables conviction scoring
    session: str = "",     # session label — passed to conviction engine
) -> bool:
    """
    Snapshot regime + correlation + OI at the moment of trade entry.
    Upserts into trade_context — safe to call even if row already exists.
    Returns True on success.
    """
    ctx = {}

    # ── Regime ──────────────────────────────────────────────────────────────
    try:
        from utils.market_intelligence import MarketIntelligence
        mi = MarketIntelligence()
        r4h = mi.get_regime(market, symbol, "4h")
        r1h = mi.get_regime(market, symbol, "1h")
        ctx["regime_4h"]           = r4h.regime
        ctx["regime_1h"]           = r1h.regime
        ctx["volatility_at_entry"] = r4h.volatility
        ctx["adx_at_entry"]        = round(r4h.adx, 2)
    except Exception as e:
        logger.debug(f"Trade replay: regime capture failed for {trade_id}: {e}")

    # ── Correlation ──────────────────────────────────────────────────────────
    try:
        from utils.correlation_engine import compute
        if market == "NSE":
            c = compute("NSE", "NSE:NIFTY50-INDEX", "NSE", "NSE:NIFTYBANK-INDEX", "1h", window=30)
            ctx["corr_nifty_bank"] = round(c.correlation, 3)
        elif market == "FOREX":
            c = compute("FOREX", "XAGUSD", "FOREX", "USOIL", "1h", window=30)
            ctx["corr_silver_oil"] = round(c.correlation, 3)
    except Exception as e:
        logger.debug(f"Trade replay: correlation capture failed for {trade_id}: {e}")

    # ── OI context (NSE only) ────────────────────────────────────────────────
    if market == "NSE":
        try:
            from utils.oi_archive import get_max_oi_strikes
            # Map trading symbol to OI symbol
            _sym_map = {
                "NSE:NIFTY50-INDEX":    "NIFTY",
                "NSE:NIFTYBANK-INDEX":  "BANKNIFTY",
                "NSE:FINNIFTY-INDEX":   "FINNIFTY",
                "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
            }
            oi_sym = _sym_map.get(symbol, symbol.split(":")[-1].split("-")[0])
            oi = get_max_oi_strikes(oi_sym)
            if oi:
                ctx["oi_pcr"]          = oi.get("pcr_oi")
                ctx["oi_bias"]         = oi.get("option_bias")
                ctx["oi_max_ce_strike"] = oi.get("max_ce_strike")
                ctx["oi_max_pe_strike"] = oi.get("max_pe_strike")
        except Exception as e:
            logger.debug(f"Trade replay: OI capture failed for {trade_id}: {e}")

    if not ctx:
        return False

    # ── Conviction context ────────────────────────────────────────────────────
    if setup is not None:
        try:
            from utils.conviction_engine import ConvictionEngine
            regime_4h = ctx.get("regime_4h")
            conviction = ConvictionEngine().evaluate(
                market=market,
                symbol=symbol,
                direction=direction,
                setup=setup,
                session=session,
                regime_4h=regime_4h,
            )
            ctx["conviction_score"]      = conviction.conviction_score
            ctx["conviction_grade"]      = conviction.conviction_grade
            ctx["conviction_components"] = json.dumps(conviction.components)
            ctx["conviction_risk_mult"]  = conviction.recommended_risk_multiplier
            ctx["conviction_hard_block"] = int(conviction.hard_block)
            ctx["conviction_reasons"]    = json.dumps(conviction.reasons)
        except Exception as e:
            logger.debug(f"Trade replay: conviction capture failed for {trade_id}: {e}")

    # ── Upsert into trade_context ────────────────────────────────────────────
    try:
        init_db()
        with _connect() as conn:
            # Ensure row exists first (may have been created by sync_from_jsonl)
            conn.execute("""
                INSERT OR IGNORE INTO trade_context (trade_id) VALUES (?)
            """, (trade_id,))

            fields = ", ".join(f"{k} = ?" for k in ctx)
            values = list(ctx.values()) + [trade_id]
            conn.execute(f"""
                UPDATE trade_context SET {fields} WHERE trade_id = ?
            """, values)
        return True
    except Exception as e:
        logger.warning(f"Trade replay: DB write failed for {trade_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Replay query — full trade context
# ---------------------------------------------------------------------------

def replay(trade_id: str) -> Optional[dict]:
    """
    Return full trade + context for replay/analysis.
    Joins trades + trade_context into one dict.
    """
    init_db()
    with _connect() as conn:
        row = conn.execute("""
            SELECT
                t.trade_id, t.account, t.market, t.symbol, t.direction,
                t.entry_time, t.entry_price, t.stop_loss, t.target1, t.target2, t.target3,
                t.lots, t.risk_usd, t.risk_mode, t.score, t.mss_type,
                t.entry_reason, t.spread_at_entry,
                t.sim_ratio, t.lot_boost, t.is_aplus,
                t.session, t.utc_hour, t.day_name,
                t.exit_time, t.exit_price, t.exit_reason,
                t.pnl_usd, t.r_multiple, t.targets_hit, t.result,
                -- context
                c.fvg_low, c.fvg_high, c.fvg_size, c.fvg_in_discount,
                c.sweep_type, c.sweep_candles_ago, c.sweep_confidence,
                c.dol_direction, c.dol_price,
                c.h4_bias, c.h1_bias, c.in_kill_zone,
                -- replay fields
                c.regime_4h, c.regime_1h, c.volatility_at_entry, c.adx_at_entry,
                c.corr_nifty_bank, c.corr_silver_oil,
                c.oi_pcr, c.oi_bias, c.oi_max_ce_strike, c.oi_max_pe_strike
            FROM trades t
            LEFT JOIN trade_context c ON t.trade_id = c.trade_id
            WHERE t.trade_id = ?
        """, (trade_id,)).fetchone()

    if not row:
        return None
    return dict(row)


def replay_all(account: str = None, result: str = None, limit: int = 50) -> list[dict]:
    """
    Query multiple trades with replay context.
    Filters: account (FTMO, GFT_5K, etc.), result (WIN/LOSS/BE).
    """
    init_db()
    clauses, params = [], []
    if account: clauses.append("t.account = ?"); params.append(account)
    if result:  clauses.append("t.result = ?");  params.append(result)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT
                t.trade_id, t.account, t.symbol, t.direction,
                t.entry_time, t.score, t.mss_type, t.session,
                t.pnl_usd, t.r_multiple, t.result, t.exit_reason,
                c.regime_4h, c.volatility_at_entry, c.oi_pcr, c.oi_bias
            FROM trades t
            LEFT JOIN trade_context c ON t.trade_id = c.trade_id
            {where}
            ORDER BY t.entry_time DESC
            LIMIT ?
        """, params + [limit]).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Pattern analysis — which conditions make money
# ---------------------------------------------------------------------------

def winning_conditions(account: str = None) -> dict:
    """
    Aggregate: which regime, volatility, session, mss_type correlated with wins.
    Returns grouped stats dict.
    """
    init_db()
    where = "WHERE t.result IS NOT NULL" + (f" AND t.account = '{account}'" if account else "")

    with _connect() as conn:
        def _group(group_col):
            rows = conn.execute(f"""
                SELECT {group_col},
                       COUNT(*) total,
                       SUM(CASE WHEN t.result='WIN' THEN 1 ELSE 0 END) wins,
                       ROUND(AVG(t.pnl_usd), 2) avg_pnl,
                       ROUND(AVG(t.r_multiple), 3) avg_r
                FROM trades t
                LEFT JOIN trade_context c ON t.trade_id = c.trade_id
                {where}
                GROUP BY {group_col}
                ORDER BY wins DESC
            """).fetchall()
            return [dict(r) for r in rows]

        return {
            "by_regime_4h":    _group("c.regime_4h"),
            "by_volatility":   _group("c.volatility_at_entry"),
            "by_session":      _group("t.session"),
            "by_mss_type":     _group("t.mss_type"),
            "by_oi_bias":      _group("c.oi_bias"),
        }
