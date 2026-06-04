"""
Edge Attribution Engine — CB6 Quantum Phase 4
Finds which market conditions produce profit and which produce loss.
Works across live trades (DB) and backtest CSVs.

Usage:
    from utils.edge_engine import EdgeEngine
    engine = EdgeEngine()
    engine.load_live()
    engine.load_backtest()

    # Attribution
    report = engine.full_report()

    # Rule suggestions
    rules = engine.suggest_rules()
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json

BASE = Path(__file__).parent.parent

# Minimum samples for each confidence tier
CONF_HIGH   = 30
CONF_MEDIUM = 10
CONF_LOW    = 3


@dataclass
class EdgeStats:
    dimension:  str        # e.g. "regime", "session", "mss_type"
    value:      str        # e.g. "TRENDING_DOWN", "london_open", "CHOCH"
    source:     str        # "live" | "backtest" | "mixed"
    total:      int        = 0
    wins:       int        = 0
    losses:     int        = 0
    win_rate:   float      = 0.0
    avg_r:      float      = 0.0
    avg_pnl:    float      = 0.0
    avg_mfe_r:  float      = 0.0
    avg_mae_r:  float      = 0.0
    confidence: str        = "LOW"   # LOW | MEDIUM | HIGH
    verdict:    str        = "NEUTRAL"  # EDGE | AVOID | NEUTRAL | SAMPLE_TOO_SMALL

    def compute(self):
        """Finalize derived fields after aggregation."""
        if self.total >= CONF_HIGH:
            self.confidence = "HIGH"
        elif self.total >= CONF_MEDIUM:
            self.confidence = "MEDIUM"
        elif self.total >= CONF_LOW:
            self.confidence = "LOW"
        else:
            self.confidence = "INSUFFICIENT"

        self.win_rate = round(self.wins / self.total * 100, 1) if self.total else 0.0

        if self.confidence == "INSUFFICIENT" or self.total < CONF_LOW:
            self.verdict = "SAMPLE_TOO_SMALL"
        elif self.win_rate >= 60:
            self.verdict = "EDGE"
        elif self.win_rate <= 35:
            self.verdict = "AVOID"
        else:
            self.verdict = "NEUTRAL"

    def to_dict(self) -> dict:
        return {
            "dimension":  self.dimension,
            "value":      self.value,
            "source":     self.source,
            "total":      self.total,
            "wins":       self.wins,
            "win_rate":   self.win_rate,
            "avg_r":      round(self.avg_r, 3),
            "avg_pnl":    round(self.avg_pnl, 2),
            "avg_mfe_r":  round(self.avg_mfe_r, 3),
            "avg_mae_r":  round(self.avg_mae_r, 3),
            "confidence": self.confidence,
            "verdict":    self.verdict,
        }


@dataclass
class RuleSuggestion:
    rule:       str        # human-readable rule
    evidence:   str        # data behind it
    action:     str        # BLOCK | REDUCE_LOT | REQUIRE_HIGHER_SCORE | ALLOW | WATCH
    confidence: str        # HIGH | MEDIUM | LOW
    dimension:  str        # what condition triggered this
    value:      str        # the condition value


class EdgeEngine:
    """Load trade data from multiple sources and attribute edge."""

    def __init__(self):
        self._trades: list[dict] = []

    # ---------------------------------------------------------------------------
    # Data loading
    # ---------------------------------------------------------------------------

    def load_live(self) -> int:
        """Load closed trades from the trade DB."""
        from utils.trade_db import _connect, init_db
        init_db()
        with _connect() as conn:
            rows = conn.execute("""
                SELECT t.trade_id, t.account, t.market, t.symbol, t.direction,
                       t.entry_time, t.score, t.mss_type, t.session,
                       t.pnl_usd, t.r_multiple, t.result, t.exit_reason,
                       t.exit_type, t.mfe_r, t.mae_r, t.hold_time_min,
                       t.is_aplus, t.risk_mode, t.lot_boost,
                       c.regime_4h, c.regime_1h, c.volatility_at_entry,
                       c.oi_bias, c.corr_nifty_bank
                FROM trades t
                LEFT JOIN trade_context c ON t.trade_id = c.trade_id
                WHERE t.result IS NOT NULL
            """).fetchall()

        for row in rows:
            d = dict(row)
            d["_source"] = "live"
            self._trades.append(d)
        return len(rows)

    def load_backtest(self, max_rows: int = 5000) -> int:
        """Load from backtest CSVs — NSE master + Forex MT5."""
        count = 0
        count += self._load_nse_master(max_rows)
        count += self._load_forex_bt()
        return count

    def _load_nse_master(self, limit: int) -> int:
        """Load NSE bt_master_2023_2026.csv"""
        path = BASE / "ml/training_data/bt_master_2023_2026.csv"
        if not path.exists():
            return 0
        try:
            import csv
            with open(path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                n = 0
                for row in reader:
                    win = _to_int(row.get("win", "0"))
                    self._trades.append({
                        "_source":     "backtest_nse",
                        "market":      "NSE",
                        "symbol":      row.get("symbol", "NIFTY"),
                        "direction":   "BULLISH" if row.get("dir","").upper() in ("LONG","BUY","BULLISH") else "BEARISH",
                        "session":     row.get("session", ""),
                        "score":       _to_float(row.get("score")),
                        "mss_type":    row.get("mss", ""),
                        "regime_4h":   row.get("regime", ""),
                        "r_multiple":  _to_float(row.get("r")),
                        "hold_time_min": _to_int(row.get("hold_mins")),
                        "result":      "WIN" if win else "LOSS",
                        "wins":        win,
                        "exit_reason": row.get("outcome", ""),
                        "exit_type":   _outcome_to_exit_type(row.get("outcome", "")),
                        "mfe_r":       None,
                        "mae_r":       None,
                        "pnl_usd":     None,
                        "is_aplus":    None,
                        "volatility_at_entry": None,
                        "oi_bias":     None,
                    })
                    n += 1
                    if n >= limit:
                        break
            return n
        except Exception as e:
            from utils.logger import logger
            logger.debug(f"NSE backtest load failed: {e}")
            return 0

    def _load_forex_bt(self) -> int:
        """Load Forex bt_forex_mt5.csv"""
        path = BASE / "ml/training_data/bt_forex_mt5.csv"
        if not path.exists():
            return 0
        try:
            import csv
            with open(path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                n = 0
                for row in reader:
                    outcome = row.get("outcome", "")
                    win = outcome.startswith("T") or float(row.get("r", 0) or 0) > 0
                    self._trades.append({
                        "_source":     "backtest_forex",
                        "market":      "FOREX",
                        "symbol":      row.get("symbol", ""),
                        "direction":   "BULLISH" if row.get("dir","").upper() in ("LONG","BUY","BULLISH") else "BEARISH",
                        "session":     row.get("session", ""),
                        "score":       _to_float(row.get("score")),
                        "mss_type":    row.get("mss", ""),
                        "regime_4h":   None,
                        "r_multiple":  _to_float(row.get("r")),
                        "hold_time_min": _to_int(row.get("hold_mins")),
                        "result":      "WIN" if win else "LOSS",
                        "exit_reason": outcome,
                        "exit_type":   _outcome_to_exit_type(outcome),
                        "mfe_r":       None,
                        "mae_r":       None,
                        "pnl_usd":     None,
                        "is_aplus":    None,
                        "volatility_at_entry": None,
                        "oi_bias":     None,
                    })
                    n += 1
            return n
        except Exception as e:
            from utils.logger import logger
            logger.debug(f"Forex backtest load failed: {e}")
            return 0

    # ---------------------------------------------------------------------------
    # Attribution
    # ---------------------------------------------------------------------------

    def attribute_by(self, dimension: str, source_filter: str = None,
                     min_n: int = 1) -> list[EdgeStats]:
        """
        Group trades by a dimension field and compute EdgeStats for each value.
        dimension: field name in trade dict (e.g. "regime_4h", "session", "mss_type")
        source_filter: "live" | "backtest_nse" | "backtest_forex" | None (all)
        """
        groups: dict[str, list] = {}
        for t in self._trades:
            if source_filter and t.get("_source") != source_filter:
                if not t.get("_source", "").startswith(source_filter):
                    continue
            val = str(t.get(dimension) or "UNKNOWN").strip()
            if not val or val == "None":
                val = "UNKNOWN"
            groups.setdefault(val, []).append(t)

        results = []
        for val, trades in groups.items():
            if len(trades) < min_n:
                continue
            stats = EdgeStats(dimension=dimension, value=val,
                              source=source_filter or "all",
                              total=len(trades))
            stats.wins   = sum(1 for t in trades if t.get("result") == "WIN")
            stats.losses = sum(1 for t in trades if t.get("result") == "LOSS")
            r_vals   = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
            pnl_vals = [float(t["pnl_usd"])    for t in trades if t.get("pnl_usd")    is not None]
            mfe_vals = [float(t["mfe_r"])       for t in trades if t.get("mfe_r")      is not None]
            mae_vals = [float(t["mae_r"])       for t in trades if t.get("mae_r")      is not None]
            stats.avg_r     = sum(r_vals)   / len(r_vals)   if r_vals   else 0.0
            stats.avg_pnl   = sum(pnl_vals) / len(pnl_vals) if pnl_vals else 0.0
            stats.avg_mfe_r = sum(mfe_vals) / len(mfe_vals) if mfe_vals else 0.0
            stats.avg_mae_r = sum(mae_vals) / len(mae_vals) if mae_vals else 0.0
            stats.compute()
            results.append(stats)

        return sorted(results, key=lambda s: (-s.total, -s.win_rate))

    def all_dimensions(self, source_filter: str = None) -> dict[str, list[EdgeStats]]:
        """Run attribution across all key dimensions."""
        dims = ["regime_4h", "session", "mss_type", "direction", "symbol",
                "exit_type", "volatility_at_entry", "oi_bias", "risk_mode"]
        return {d: self.attribute_by(d, source_filter=source_filter, min_n=1)
                for d in dims}

    # ---------------------------------------------------------------------------
    # Rule Suggestions
    # ---------------------------------------------------------------------------

    def suggest_rules(self, source_filter: str = None) -> list[RuleSuggestion]:
        """Generate data-driven rule suggestions from attribution results."""
        rules = []
        dims = self.all_dimensions(source_filter)

        for dim, stats_list in dims.items():
            for s in stats_list:
                if s.verdict == "SAMPLE_TOO_SMALL":
                    continue

                if s.verdict == "AVOID":
                    action = "BLOCK" if s.win_rate < 25 else "REDUCE_LOT"
                    rules.append(RuleSuggestion(
                        rule=f"{dim}={s.value}: {s.win_rate:.0f}% WR — {'BLOCK trades' if action == 'BLOCK' else 'reduce lot size'}",
                        evidence=f"n={s.total} | WR={s.win_rate:.0f}% | avgR={s.avg_r:.2f}R",
                        action=action,
                        confidence=s.confidence,
                        dimension=dim,
                        value=s.value,
                    ))
                elif s.verdict == "EDGE":
                    rules.append(RuleSuggestion(
                        rule=f"{dim}={s.value}: {s.win_rate:.0f}% WR — continue trading",
                        evidence=f"n={s.total} | WR={s.win_rate:.0f}% | avgR={s.avg_r:.2f}R",
                        action="ALLOW",
                        confidence=s.confidence,
                        dimension=dim,
                        value=s.value,
                    ))

        # Sort: BLOCK first, then HIGH confidence
        conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        action_order = {"BLOCK": 0, "REDUCE_LOT": 1, "ALLOW": 2, "REQUIRE_HIGHER_SCORE": 3}
        rules.sort(key=lambda r: (action_order.get(r.action, 9), conf_order.get(r.confidence, 9)))
        return rules

    # ---------------------------------------------------------------------------
    # Summary stats
    # ---------------------------------------------------------------------------

    def summary(self, source_filter: str = None) -> dict:
        trades = [t for t in self._trades
                  if (not source_filter or t.get("_source", "").startswith(source_filter))]
        total  = len(trades)
        wins   = sum(1 for t in trades if t.get("result") == "WIN")
        r_vals = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
        return {
            "total":    total,
            "wins":     wins,
            "losses":   total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
            "avg_r":    round(sum(r_vals) / len(r_vals), 3) if r_vals else 0.0,
            "sources":  {s: sum(1 for t in trades if t.get("_source") == s)
                         for s in set(t.get("_source") for t in trades)},
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _to_int(v) -> Optional[int]:
    try:
        return int(float(v)) if v is not None else None
    except Exception:
        return None


def _outcome_to_exit_type(outcome: str) -> str:
    o = (outcome or "").upper().strip()
    if o == "T3":    return "TP3_HIT"
    if o == "T2":    return "TP2_PARTIAL"
    if o == "T1":    return "TP1_PARTIAL"
    if o in ("SL", "STOP"):  return "SL_HIT"
    if o == "MAE_EXIT":      return "MAE_EXIT"
    if o == "MANUAL":        return "MANUAL"
    if o == "TIME_EXIT":     return "TIME_EXIT"
    if o.startswith("T") and o[1:].isdigit(): return "TP_HIT"
    return "UNKNOWN"
