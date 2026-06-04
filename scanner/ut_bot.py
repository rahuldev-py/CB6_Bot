# scanner/ut_bot.py — UT Bot ATR Trailing Stop (ported from Pine Script)
#
# Original Pine Script logic by Fatich.id / HPotter
# Adapted for CB6 pandas DataFrames
#
# How it works:
#   trailing_stop adapts to price using ATR distance:
#     - while price rising  → stop trails up  = max(prev_stop, close - ATR*factor)
#     - while price falling → stop trails down = min(prev_stop, close + ATR*factor)
#   BUY  = close crosses ABOVE trailing stop
#   SELL = close crosses BELOW trailing stop
#   TREND = BULLISH if close > stop, BEARISH if close < stop

import numpy as np
import pandas as pd
import os, sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger

UT_KEY_VALUE = 2    # ATR multiplier (Pine default: 2)
UT_ATR_PERIOD = 6   # ATR period    (Pine default: 6)


def _wilder_atr(df: pd.DataFrame, period: int) -> np.ndarray:
    """Wilder's ATR (RMA smoothing) — matches Pine ta.atr()."""
    high  = df['high'].values.astype(float)
    low   = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    n     = len(close)

    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i]  - close[i - 1]))

    atr = np.zeros(n)
    if n >= period:
        atr[period - 1] = np.mean(tr[:period])
        alpha = 1.0 / period
        for i in range(period, n):
            atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha
        # back-fill warm-up bars
        atr[:period - 1] = atr[period - 1]

    return atr


def compute_ut_bot(df: pd.DataFrame,
                   key_value: int = UT_KEY_VALUE,
                   atr_period: int = UT_ATR_PERIOD) -> pd.DataFrame:
    """
    Compute UT Bot trailing stop on a candle DataFrame.
    Returns df copy with added columns:
      ut_stop   — ATR trailing stop level
      ut_trend  — 1 (BULLISH) or -1 (BEARISH)
      ut_buy    — True on the candle where price crosses above stop
      ut_sell   — True on the candle where price crosses below stop
    """
    try:
        close  = df['close'].values.astype(float)
        n      = len(close)
        atr    = _wilder_atr(df, atr_period)
        n_loss = key_value * atr

        stop = np.zeros(n)
        stop[0] = close[0] - n_loss[0]

        for i in range(1, n):
            prev_s = stop[i - 1]
            prev_c = close[i - 1]
            cur_c  = close[i]
            nl     = n_loss[i]

            if cur_c > prev_s and prev_c > prev_s:
                stop[i] = max(prev_s, cur_c - nl)
            elif cur_c < prev_s and prev_c < prev_s:
                stop[i] = min(prev_s, cur_c + nl)
            elif cur_c > prev_s:
                stop[i] = cur_c - nl
            else:
                stop[i] = cur_c + nl

        trend    = np.where(close > stop, 1, -1)
        ut_buy   = np.zeros(n, dtype=bool)
        ut_sell  = np.zeros(n, dtype=bool)

        for i in range(1, n):
            if close[i - 1] < stop[i - 1] and close[i] > stop[i]:
                ut_buy[i]  = True
            if close[i - 1] > stop[i - 1] and close[i] < stop[i]:
                ut_sell[i] = True

        out = df.copy()
        out['ut_stop']  = stop
        out['ut_trend'] = trend
        out['ut_buy']   = ut_buy
        out['ut_sell']  = ut_sell
        return out

    except Exception as e:
        logger.debug(f"UT Bot compute error: {e}")
        return df


def get_ut_signal(df: pd.DataFrame,
                  key_value: int = UT_KEY_VALUE,
                  atr_period: int = UT_ATR_PERIOD) -> dict:
    """
    Returns a dict with current UT Bot state:
      trend        — 'BULLISH' or 'BEARISH'
      stop         — current trailing stop level
      signal       — 'BUY', 'SELL', or None (None = no fresh crossover this bar)
      bars_in_trend — how many consecutive bars in current trend direction
    """
    try:
        result = compute_ut_bot(df, key_value, atr_period)
        trend_val  = int(result['ut_trend'].iloc[-1])
        stop_val   = round(float(result['ut_stop'].iloc[-1]), 2)
        is_buy     = bool(result['ut_buy'].iloc[-1])
        is_sell    = bool(result['ut_sell'].iloc[-1])

        # Count consecutive bars in current trend
        trend_arr = result['ut_trend'].values
        bars = 1
        for i in range(len(trend_arr) - 2, -1, -1):
            if trend_arr[i] == trend_val:
                bars += 1
            else:
                break

        return {
            'trend'        : 'BULLISH' if trend_val == 1 else 'BEARISH',
            'stop'         : stop_val,
            'signal'       : 'BUY' if is_buy else ('SELL' if is_sell else None),
            'bars_in_trend': bars,
            'aligned'      : None,   # filled by caller after knowing setup direction
        }
    except Exception as e:
        logger.debug(f"UT Bot signal error: {e}")
        return {'trend': None, 'stop': None, 'signal': None, 'bars_in_trend': 0, 'aligned': None}
