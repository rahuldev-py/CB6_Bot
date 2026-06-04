"""
Market Intelligence — CB6 Quantum Phase 3 Unified Facade
Single entry point for regime, volatility, and correlation data.
Results are cached for CACHE_TTL seconds (default 15 min).

Usage:
    from utils.market_intelligence import MarketIntelligence
    mi = MarketIntelligence()

    # Single symbol
    r = mi.get_regime("NSE", "NSE:NIFTY50-INDEX", "1h")
    print(r.regime, r.volatility, r.trend_strength)

    # Full snapshot
    snap = mi.snapshot()

    # CLI: python -m utils.market_intelligence
"""

import time
import threading
from typing import Optional

from utils.ohlcv_archive import get_candles
from utils.regime_detector import detect, RegimeResult
from utils.correlation_engine import compute, scan_all, CorrelationResult

CACHE_TTL = 900   # 15 minutes


# ---------------------------------------------------------------------------
# Symbols to include in the snapshot
# ---------------------------------------------------------------------------

SNAPSHOT_SYMBOLS = [
    # (market, symbol, timeframe)
    ("NSE",   "NSE:NIFTY50-INDEX",   "1h"),
    ("NSE",   "NSE:NIFTY50-INDEX",   "D"),
    ("NSE",   "NSE:NIFTYBANK-INDEX", "1h"),
    ("NSE",   "NSE:NIFTYBANK-INDEX", "D"),
    ("NSE",   "NSE:FINNIFTY-INDEX",  "1h"),
    ("NSE",   "NSE:MIDCPNIFTY-INDEX","1h"),
    ("FOREX", "XAGUSD",              "1h"),
    ("FOREX", "XAGUSD",              "4h"),
    ("FOREX", "USOIL",               "1h"),
    ("FOREX", "USOIL",               "4h"),
    ("FOREX", "EURUSD",              "1h"),
    ("FOREX", "EURUSD",              "4h"),
]


class MarketIntelligence:
    """Thread-safe singleton with TTL cache for regime + correlation data."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._cache: dict = {}
                cls._instance._cache_ts: dict = {}
                cls._instance._corr_cache: Optional[list] = None
                cls._instance._corr_ts: float = 0.0
        return cls._instance

    # ---------------------------------------------------------------------------
    # Regime
    # ---------------------------------------------------------------------------

    def get_regime(self, market: str, symbol: str, timeframe: str,
                   limit: int = 300) -> RegimeResult:
        """Return regime for a symbol/TF pair, using cache if fresh."""
        key = (market, symbol, timeframe)
        now = time.monotonic()

        with self._lock:
            if key in self._cache and (now - self._cache_ts.get(key, 0)) < CACHE_TTL:
                return self._cache[key]

        df = get_candles(market, symbol, timeframe, limit=limit)
        result = detect(df, symbol=symbol, timeframe=timeframe)

        with self._lock:
            self._cache[key] = result
            self._cache_ts[key] = time.monotonic()

        return result

    def invalidate(self, market: str = None, symbol: str = None, timeframe: str = None):
        """Force cache expiry for a specific key or all entries."""
        with self._lock:
            if market and symbol and timeframe:
                key = (market, symbol, timeframe)
                self._cache_ts.pop(key, None)
            else:
                self._cache_ts.clear()
                self._corr_ts = 0.0

    # ---------------------------------------------------------------------------
    # Correlations
    # ---------------------------------------------------------------------------

    def get_correlations(self, timeframe: str = "1h", window: int = 50) -> list[dict]:
        """Return correlation scan for all default pairs, cached."""
        now = time.monotonic()
        with self._lock:
            if self._corr_cache and (now - self._corr_ts) < CACHE_TTL:
                return self._corr_cache

        results = scan_all(timeframe=timeframe, window=window)

        with self._lock:
            self._corr_cache = results
            self._corr_ts = time.monotonic()

        return results

    # ---------------------------------------------------------------------------
    # Snapshot
    # ---------------------------------------------------------------------------

    def snapshot(self) -> dict:
        """
        Full market intelligence snapshot — regimes for all tracked symbols
        plus correlation pairs. Returns a structured dict.
        """
        regimes = []
        for market, symbol, tf in SNAPSHOT_SYMBOLS:
            r = self.get_regime(market, symbol, tf)
            regimes.append(r.to_dict())

        correlations = self.get_correlations()

        return {
            "regimes":      regimes,
            "correlations": correlations,
        }

    # ---------------------------------------------------------------------------
    # Convenience: H4 bias for a symbol (used by forex_worker)
    # ---------------------------------------------------------------------------

    def h4_bias(self, market: str, symbol: str) -> str:
        """
        Return 'BULLISH' | 'BEARISH' | 'RANGING' | 'UNKNOWN' for H4 timeframe.
        Replaces the current H4 bias logic in forex_worker where archive has data.
        """
        r = self.get_regime(market, symbol, "4h")
        if r.regime == "UNKNOWN":
            return "UNKNOWN"
        if r.regime == "TRENDING_UP":
            return "BULLISH"
        if r.regime == "TRENDING_DOWN":
            return "BEARISH"
        return "RANGING"

    def daily_bias(self, market: str, symbol: str) -> str:
        """Return 'BULLISH' | 'BEARISH' | 'RANGING' | 'UNKNOWN' for Daily timeframe."""
        r = self.get_regime(market, symbol, "D")
        if r.regime == "UNKNOWN":
            return "UNKNOWN"
        if r.regime == "TRENDING_UP":
            return "BULLISH"
        if r.regime == "TRENDING_DOWN":
            return "BEARISH"
        return "RANGING"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_snapshot():
    mi = MarketIntelligence()
    print("\nCB6 Quantum — Market Intelligence Snapshot")
    print("=" * 70)

    snap = mi.snapshot()

    print("\nREGIMES")
    print(f"  {'Symbol':<30} {'TF':<5} {'Regime':<15} {'Strength':<10} {'Vol':<8} {'ADX':>6}")
    print("  " + "-" * 68)
    for r in snap["regimes"]:
        sym = r["symbol"].replace("NSE:", "")
        print(f"  {sym:<30} {r['timeframe']:<5} {r['regime']:<15} "
              f"{r['trend_strength']:<10} {r['volatility']:<8} {r['adx']:>6.1f}")

    print("\nCORRELATIONS (1h, 50-bar window)")
    print(f"  {'Pair':<45} {'Corr':>7}  {'Strength':<10} {'Direction'}")
    print("  " + "-" * 75)
    for c in snap["correlations"]:
        a = c["symbol_a"].replace("NSE:", "")
        b = c["symbol_b"].replace("NSE:", "")
        pair = f"{a} ↔ {b}"
        if c["bars_used"] == 0:
            print(f"  {pair:<45}  {'N/A':>7}  (no data)")
        else:
            print(f"  {pair:<45} {c['correlation']:>7.3f}  {c['strength']:<10} {c['direction']}")
    print()


if __name__ == "__main__":
    _print_snapshot()
