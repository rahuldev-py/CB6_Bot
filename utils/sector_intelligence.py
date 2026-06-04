"""
Sector Intelligence Engine — CB6 Quantum Phase 6
NSE sector mapping, momentum tracking, macro-driven rotation prediction.

Provides:
  sector_snapshot()        → current momentum/regime for each sector
  rotation_prediction(regime) → winners/losers given macro regime
  sector_vs_nifty(sector)  → relative performance vs Nifty50
  trade_context(symbol)    → macro + sector context for a trading symbol

Usage:
  from utils.sector_intelligence import SectorIntelligence
  si = SectorIntelligence()
  snap = si.sector_snapshot()
  si.rotation_prediction("RATE_CUT_CYCLE")
  python -m utils.sector_intelligence
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MAP_PATH   = Path(__file__).parent.parent / "data" / "nse_sector_map.json"
GRAPH_PATH = Path(__file__).parent.parent / "data" / "macro_graph.json"


@dataclass
class SectorState:
    sector:        str
    label:         str
    fyers_symbol:  str
    regime:        str        # from regime_detector
    trend_strength:str
    volatility:    str
    adx:           float
    momentum_7d:   float      # % change over last 7 days
    vs_nifty_7d:   float      # momentum relative to Nifty50
    macro_bias:    str        # BULLISH | BEARISH | NEUTRAL based on macro drivers
    data_available:bool


class SectorIntelligence:

    def __init__(self):
        with open(MAP_PATH, encoding="utf-8") as f:
            self._map = json.load(f)
        self._sectors = self._map["sectors"]
        self._rotation_matrix = self._map["sector_rotation_matrix"]["regimes"]

    # ---------------------------------------------------------------------------
    # Sector snapshot — current state of each sector
    # ---------------------------------------------------------------------------

    def sector_snapshot(self) -> list[SectorState]:
        """Get current regime + momentum state for all sectors in archive."""
        from utils.market_intelligence import MarketIntelligence
        from utils.ohlcv_archive import get_candles

        mi = MarketIntelligence()
        results = []

        for sector_id, info in self._sectors.items():
            sym = info.get("fyers_symbol", "")
            if not sym:
                continue

            # Regime from archive
            r = mi.get_regime("NSE", sym, "1h")

            # 7-day momentum from daily candles
            momentum_7d = 0.0
            vs_nifty_7d = 0.0
            data_available = r.regime != "UNKNOWN"

            try:
                df = get_candles("NSE", sym, "D", limit=10)
                if df is not None and len(df) >= 2:
                    old_close = float(df.iloc[-8]["close"]) if len(df) >= 8 else float(df.iloc[0]["close"])
                    new_close = float(df.iloc[-1]["close"])
                    momentum_7d = round((new_close - old_close) / old_close * 100, 2)

                    # Compare vs Nifty50
                    nifty_df = get_candles("NSE", "NSE:NIFTY50-INDEX", "D", limit=10)
                    if nifty_df is not None and len(nifty_df) >= 2:
                        n_old = float(nifty_df.iloc[-8]["close"]) if len(nifty_df) >= 8 else float(nifty_df.iloc[0]["close"])
                        n_new = float(nifty_df.iloc[-1]["close"])
                        nifty_7d = (n_new - n_old) / n_old * 100
                        vs_nifty_7d = round(momentum_7d - nifty_7d, 2)
                    data_available = True
            except Exception:
                pass

            results.append(SectorState(
                sector=sector_id,
                label=info["label"],
                fyers_symbol=sym,
                regime=r.regime,
                trend_strength=r.trend_strength,
                volatility=r.volatility,
                adx=r.adx,
                momentum_7d=momentum_7d,
                vs_nifty_7d=vs_nifty_7d,
                macro_bias="NEUTRAL",
                data_available=data_available,
            ))

        return sorted(results, key=lambda s: -abs(s.vs_nifty_7d))

    # ---------------------------------------------------------------------------
    # Rotation prediction — given a macro regime, who wins?
    # ---------------------------------------------------------------------------

    def rotation_prediction(self, macro_regime: str) -> dict:
        """
        Given a macro regime string (e.g. "RATE_CUT_CYCLE", "HIGH_OIL"),
        return expected sector winners and losers.
        """
        regime_data = self._rotation_matrix.get(macro_regime.upper())
        if not regime_data:
            available = list(self._rotation_matrix.keys())
            return {
                "error": f"Unknown regime '{macro_regime}'",
                "available_regimes": available,
            }

        def enrich(sector_ids: list) -> list[dict]:
            result = []
            for sid in sector_ids:
                info = self._sectors.get(sid, {})
                result.append({
                    "sector":    sid,
                    "label":     info.get("label", sid),
                    "symbol":    info.get("fyers_symbol", ""),
                    "notes":     info.get("macro_drivers", {}).get("notes", ""),
                })
            return result

        return {
            "regime":  macro_regime,
            "notes":   regime_data.get("notes", ""),
            "winners": enrich(regime_data.get("winners", [])),
            "losers":  enrich(regime_data.get("losers", [])),
        }

    def multi_regime_prediction(self, active_regimes: list[str]) -> dict:
        """
        Given multiple active macro regimes simultaneously,
        aggregate winner/loser scores across regimes.
        """
        scores: dict[str, float] = {}
        sector_labels: dict[str, str] = {}

        for regime in active_regimes:
            rdata = self._rotation_matrix.get(regime.upper(), {})
            for sid in rdata.get("winners", []):
                scores[sid] = scores.get(sid, 0) + 1.0
                sector_labels[sid] = self._sectors.get(sid, {}).get("label", sid)
            for sid in rdata.get("losers", []):
                scores[sid] = scores.get(sid, 0) - 1.0
                sector_labels[sid] = self._sectors.get(sid, {}).get("label", sid)

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        winners = [{"sector": s, "label": sector_labels[s], "score": sc}
                   for s, sc in ranked if sc > 0]
        losers  = [{"sector": s, "label": sector_labels[s], "score": sc}
                   for s, sc in ranked if sc < 0]

        return {
            "active_regimes": active_regimes,
            "winners":        winners,
            "losers":         losers,
            "neutral":        [s for s, sc in ranked if sc == 0],
        }

    # ---------------------------------------------------------------------------
    # Sector context for a specific trading symbol
    # ---------------------------------------------------------------------------

    def trade_context(self, symbol: str) -> dict:
        """
        Return sector + macro context for a symbol being traded.
        e.g. symbol="NSE:NIFTYBANK-INDEX" → banking sector context
        """
        # Find which sector this symbol belongs to
        sector_id = None
        for sid, info in self._sectors.items():
            if info.get("fyers_symbol") == symbol or sid in symbol:
                sector_id = sid
                break

        if not sector_id:
            return {"symbol": symbol, "sector": "UNKNOWN", "note": "Symbol not in sector map"}

        sector_info = self._sectors[sector_id]
        drivers = sector_info.get("macro_drivers", {})

        return {
            "symbol":        symbol,
            "sector":        sector_id,
            "label":         sector_info["label"],
            "nifty_weight":  sector_info.get("weight_in_nifty50_pct", 0),
            "primary_drivers": drivers.get("primary", []),
            "bullish_conditions": drivers.get("positive", []),
            "bearish_conditions": drivers.get("negative", []),
            "behavior": sector_info.get("sector_behavior", {}),
            "notes": drivers.get("notes", ""),
        }

    # ---------------------------------------------------------------------------
    # Current macro regime detection (simple heuristic from live data)
    # ---------------------------------------------------------------------------

    def detect_active_regimes(self) -> list[str]:
        """
        Detect which macro regimes are currently active by querying live data.
        Returns list of active regime names (e.g. ["HIGH_OIL", "USD_STRONG"]).
        Note: uses available archive data + FII data; expands as more data is connected.
        """
        active = []

        # Check FII flow
        try:
            from data.fii_dii import get_fii_dii_data
            fii = get_fii_dii_data()
            if fii:
                fii_net = fii.get("fii_net", 0)
                if fii_net < -500:
                    active.append("FII_OUTFLOW")
                elif fii_net > 500:
                    active.append("FII_INFLOW")
        except Exception:
            pass

        # Check Nifty50 vs NIFTYBANK relative momentum (rate sensitivity signal)
        try:
            from utils.ohlcv_archive import get_candles
            nifty_df  = get_candles("NSE", "NSE:NIFTY50-INDEX",   "D", limit=5)
            bank_df   = get_candles("NSE", "NSE:NIFTYBANK-INDEX", "D", limit=5)
            if nifty_df is not None and len(nifty_df) >= 2 and bank_df is not None and len(bank_df) >= 2:
                n_ret = (float(nifty_df.iloc[-1]["close"]) - float(nifty_df.iloc[0]["close"])) / float(nifty_df.iloc[0]["close"])
                b_ret = (float(bank_df.iloc[-1]["close"]) - float(bank_df.iloc[0]["close"])) / float(bank_df.iloc[0]["close"])
                if b_ret > n_ret + 0.01:
                    active.append("BANK_OUTPERFORMING")   # typically rate-cut environment
                elif b_ret < n_ret - 0.01:
                    active.append("BANK_UNDERPERFORMING")  # rate hike concern
        except Exception:
            pass

        # Forex-derived signals (when archive data available)
        try:
            from utils.market_intelligence import MarketIntelligence
            mi = MarketIntelligence()
            # USD proxy: if EURUSD is in downtrend, USD is strong
            eurusd_r = mi.get_regime("FOREX", "EURUSD", "4h")
            if eurusd_r.regime == "TRENDING_DOWN":
                active.append("USD_STRONG")
            elif eurusd_r.regime == "TRENDING_UP":
                active.append("USD_WEAK")
        except Exception:
            pass

        return active if active else ["UNKNOWN"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _demo():
    si = SectorIntelligence()

    print("\n" + "═" * 72)
    print(f"{'CB6 QUANTUM — SECTOR INTELLIGENCE':^72}")
    print("═" * 72)

    # Sector snapshot
    print("\n  SECTOR SNAPSHOT (current momentum)")
    print(f"  {'Sector':<16} {'Regime':<15} {'Str':<8} {'7d%':>6}  {'vs Nifty':>9}")
    print(f"  {'─'*16} {'─'*14} {'─'*7} {'─'*6}  {'─'*9}")
    snap = si.sector_snapshot()
    for s in snap:
        if not s.data_available:
            continue
        trend_arrow = "↑" if s.momentum_7d > 0 else "↓"
        rel_arrow   = "↑" if s.vs_nifty_7d > 0 else "↓"
        print(f"  {s.sector:<16} {s.regime:<15} {s.trend_strength:<8} "
              f"{trend_arrow}{abs(s.momentum_7d):>5.1f}%  {rel_arrow}{abs(s.vs_nifty_7d):>7.1f}%")

    # Rotation predictions for key regimes
    print()
    for regime in ["RATE_CUT_CYCLE", "HIGH_OIL", "USD_STRONG", "GLOBAL_RISK_OFF"]:
        pred = si.rotation_prediction(regime)
        winners = [p["label"] for p in pred["winners"]]
        losers  = [p["label"] for p in pred["losers"]]
        print(f"  [{regime}]")
        print(f"    Winners: {', '.join(winners)}")
        print(f"    Losers:  {', '.join(losers)}")
        print(f"    Note:    {pred['notes']}")

    # Active regime detection
    print("\n  ACTIVE MACRO REGIMES (heuristic from live data):")
    active = si.detect_active_regimes()
    if active and active != ["UNKNOWN"]:
        for r in active:
            print(f"    → {r}")
        # Combined prediction
        pred = si.multi_regime_prediction(active)
        if pred["winners"]:
            print(f"\n  Combined winner sectors: {', '.join(w['label'] for w in pred['winners'])}")
        if pred["losers"]:
            print(f"  Combined loser sectors:  {', '.join(l['label'] for l in pred['losers'])}")
    else:
        print("    No strong regime signal detected (insufficient data)")

    # Trade context for key symbols
    print()
    for sym in ["NSE:NIFTYBANK-INDEX", "NSE:FINNIFTY-INDEX", "NSE:MIDCPNIFTY-INDEX"]:
        ctx = si.trade_context(sym)
        print(f"  {sym}: [{ctx['label']}]")
        print(f"    Nifty50 weight : {ctx['nifty_weight']}%")
        print(f"    Key drivers    : {', '.join(ctx['primary_drivers'])}")
        print(f"    Notes          : {ctx['notes'][:80]}")
        print()


if __name__ == "__main__":
    _demo()
