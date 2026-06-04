"""
CB6 Futures Core — Signal Scanner
Orchestrates multi-symbol scanning using Silver Bullet + market structure.
No dependency on forex_signal_scanner or NSE scanner.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from futures_engine.core.futures_data_feed import FuturesDataFeed, FuturesBar
from futures_engine.core.futures_silver_bullet import SilverBulletScanner, SilverBulletSetup
from futures_engine.core.futures_symbol_registry import get_symbol, PHASE1_SYMBOLS
from ml_engine.memory.shadow_logger import log_scanner_outcome

logger = logging.getLogger("cb6.futures.scanner")


@dataclass
class ScanResult:
    symbol: str
    timestamp: datetime
    setups: List[SilverBulletSetup]
    htf_bias: str
    scan_mode: str      # "PAPER" | "BACKTEST" | "SEMI_AUTO"
    errors: List[str] = field(default_factory=list)


class FuturesSignalScanner:
    """
    Multi-symbol futures signal scanner.
    Pulls bars from the data feed and delegates to Silver Bullet scanner per symbol.
    """

    def __init__(
        self,
        feed: FuturesDataFeed,
        symbols: Optional[List[str]] = None,
        m1_lookback: int = 120,    # bars of 1m data to evaluate
        h4_lookback: int = 50,     # bars of 4h data for HTF bias
        min_score: float = 55.0,
        scan_mode: str = "PAPER",
    ):
        self.feed = feed
        self.symbols = [s.upper() for s in (symbols or PHASE1_SYMBOLS)]
        self.m1_lookback = m1_lookback
        self.h4_lookback = h4_lookback
        self.min_score = min_score
        self.scan_mode = scan_mode
        self._scanners: Dict[str, SilverBulletScanner] = {}

    def _get_scanner(self, symbol: str) -> SilverBulletScanner:
        if symbol not in self._scanners:
            sym_info = get_symbol(symbol)
            self._scanners[symbol] = SilverBulletScanner(
                symbol=symbol,
                sl_buffer_ticks=3,
                min_score=self.min_score,
            )
        return self._scanners[symbol]

    def scan_symbol(self, symbol: str, as_of: Optional[datetime] = None) -> ScanResult:
        now = as_of or datetime.now(timezone.utc)
        errors: List[str] = []
        setups: List[SilverBulletSetup] = []
        htf_bias = "NEUTRAL"

        try:
            sym_info = get_symbol(symbol)
            if not sym_info.mff_permitted:
                errors.append(f"{symbol} is not MFF-permitted")
                return ScanResult(symbol, now, setups, htf_bias, self.scan_mode, errors)

            from datetime import timedelta
            m1_start = now - timedelta(minutes=self.m1_lookback)
            h4_start = now - timedelta(hours=self.h4_lookback * 4)

            m1_bars = self.feed.get_bars(symbol, "1m", m1_start, now)
            h4_bars = self.feed.get_bars(symbol, "4h", h4_start, now)

            if len(m1_bars) < 10:
                errors.append(f"{symbol}: insufficient 1m bars ({len(m1_bars)})")
                return ScanResult(symbol, now, setups, htf_bias, self.scan_mode, errors)

            from futures_engine.core.futures_market_structure import get_htf_bias, Bias
            bias = get_htf_bias(h4_bars) if h4_bars else Bias.NEUTRAL
            htf_bias = bias.value

            scanner = self._get_scanner(symbol)
            setups = scanner.scan(m1_bars, h4_bars, sym_info.tick_size)

            logger.info(
                "scan %s: %d setup(s) | htf=%s | mode=%s",
                symbol, len(setups), htf_bias, self.scan_mode
            )
            if setups:
                best = max(setups, key=lambda s: s.score)
                setup_payload = {
                    "symbol": symbol,
                    "direction": best.direction,
                    "confluence": float(best.score),
                    "session": str(getattr(best, "session", "")),
                    "regime": str(getattr(best, "regime", "UNKNOWN")),
                    "entry_signal": {
                        "entry": getattr(best, "entry", None),
                        "stop_loss": getattr(best, "stop_loss", None),
                        "target1": getattr(best, "target_1", None),
                        "target2": getattr(best, "target_2", None),
                        "target3": getattr(best, "target_3", None),
                    },
                }
                log_scanner_outcome('futures', 'futures_signal_scanner', symbol, setup_payload, outcome='SCANNER_PASS')
            else:
                log_scanner_outcome('futures', 'futures_signal_scanner', symbol, None, outcome='SCANNER_FAIL', reason='no_setup')

        except Exception as e:
            errors.append(f"{symbol} scan error: {e}")
            logger.exception("scan_symbol %s failed", symbol)
            log_scanner_outcome('futures', 'futures_signal_scanner', symbol, None, outcome='SCANNER_FAIL', reason='exception')

        return ScanResult(symbol, now, setups, htf_bias, self.scan_mode, errors)

    def scan_all(self, as_of: Optional[datetime] = None) -> List[ScanResult]:
        results = []
        for sym in self.symbols:
            results.append(self.scan_symbol(sym, as_of))
        return results

    def best_setup(self, as_of: Optional[datetime] = None) -> Optional[SilverBulletSetup]:
        """Return the highest-scoring setup across all symbols."""
        all_setups: List[SilverBulletSetup] = []
        for result in self.scan_all(as_of):
            all_setups.extend(result.setups)
        if not all_setups:
            return None
        return max(all_setups, key=lambda s: s.score)
