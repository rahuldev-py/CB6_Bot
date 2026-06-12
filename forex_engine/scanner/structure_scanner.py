# forex_engine/scanner/structure_scanner.py
# Higher-timeframe bias — H1 and H4 EMA trend + structural shift detection.
#
# H4 bias logic (two-layer):
#   Layer 1: EMA(3) vs EMA(8) on H4 candles → primary trend direction
#   Layer 2: H4 CHoCH/BOS detection → catches structural reversals before EMA flips
#
# If EMA = BEARISH but a BULLISH H4 CHoCH was detected within the last
# H4_CHOCH_WINDOW candles → return 'RANGING' (bias shift in progress).
# This allows counter-EMA reversal setups through the H4 gate when a genuine
# structural shift has occurred (the May 22 disaster was the opposite: we were
# trading SELL while H4 structure was clearly BULLISH — the EMA correctly
# blocked it; here the structure has already shifted, the EMA is just lagging).

from typing import Optional
from utils.logger import logger

# How many H4 candles back a CHoCH is still considered "fresh" (≈2 H4 bars = 8 hours)
H4_CHOCH_WINDOW = 3


def get_htf_bias(connector, symbol: str, interval: str,
                 fast_span: int = 3, slow_span: int = 8,
                 band_pct: float = 0.0002) -> str:
    """
    Generic HTF EMA bias on any timeframe.
    Returns 'BULLISH', 'BEARISH', or 'RANGING'.
    """
    try:
        df = connector.get_klines(symbol, interval, 20)
        if df is None or len(df) < 10:
            return 'RANGING'
        c    = df['close']
        fast = c.ewm(span=fast_span, adjust=False).mean().iloc[-1]
        slow = c.ewm(span=slow_span, adjust=False).mean().iloc[-1]
        if fast > slow * (1 + band_pct):
            return 'BULLISH'
        if fast < slow * (1 - band_pct):
            return 'BEARISH'
        return 'RANGING'
    except Exception:
        return 'RANGING'


def get_h1_bias(connector, symbol: str) -> str:
    """
    H1 trend bias via EMA(3) vs EMA(8).
    Band 0.02% — filters noise, avoids ranging misclassification.
    """
    return get_htf_bias(connector, symbol, '1h',
                        fast_span=3, slow_span=8, band_pct=0.0002)


def get_h4_bias(connector, symbol: str) -> str:
    """
    H4 bias — two-layer detection:
      1. EMA(3) vs EMA(8) on H4 → primary trend
      2. H4 CHoCH/BOS structural shift → overrides EMA to 'RANGING' when
         structure has reversed but EMA hasn't caught up yet.

    Logic:
      - EMA BULLISH → 'BULLISH' (no override needed)
      - EMA BEARISH + fresh H4 BULLISH CHoCH → 'RANGING'  (reversal in play)
      - EMA BULLISH + fresh H4 BEARISH CHoCH → 'RANGING'  (reversal in play)
      - EMA BEARISH, no CHoCH → 'BEARISH'
      - EMA RANGING → 'RANGING'
    """
    try:
        # Fetch extra candles: EMA needs ~20, MSS detection needs ~30
        df = connector.get_klines(symbol, '4h', 40)
        if df is None or len(df) < 15:
            return 'RANGING'

        # ── Layer 1: EMA bias ────────────────────────────────────────────────
        c    = df['close']
        fast = c.ewm(span=3, adjust=False).mean().iloc[-1]
        slow = c.ewm(span=8, adjust=False).mean().iloc[-1]
        band = 0.0003

        if fast > slow * (1 + band):
            ema_bias = 'BULLISH'
        elif fast < slow * (1 - band):
            ema_bias = 'BEARISH'
        else:
            ema_bias = 'RANGING'

        if ema_bias == 'RANGING':
            return 'RANGING'

        # ── Layer 2: H4 structural shift check ──────────────────────────────
        # Only runs when EMA has a strong directional bias. Checks whether
        # the H4 candles have printed a counter-EMA CHoCH recently.
        try:
            from scanner.silver_bullet import detect_sb_mss
            mss = detect_sb_mss(df, lookback=30)
            if mss is not None:
                mss_dir     = mss.get('direction')
                candles_ago = int(mss.get('candles_ago', 999))
                mss_type    = mss.get('type', 'BOS')

                # Fresh counter-EMA CHoCH = structural reversal in progress
                if (mss_dir != ema_bias
                        and mss_type == 'CHOCH'
                        and candles_ago <= H4_CHOCH_WINDOW):
                    logger.info(
                        f"H4 bias {symbol}: EMA={ema_bias} but H4 {mss_type} "
                        f"{mss_dir} {candles_ago}ca ago → returning RANGING "
                        f"(structure shifted, EMA lagging)"
                    )
                    return 'RANGING'
        except Exception as mss_err:
            logger.debug(f"H4 MSS check {symbol}: {mss_err}")

        return ema_bias

    except Exception:
        return 'RANGING'
