"""
scanner/oi_filters.py — OI-based and Bid/Ask signal quality filters for CB6 Quantum.

These filters are ADDITIVE and GRACEFULLY OPTIONAL:
  - If the 'oi' column is absent (Fyers fallback), all OI checks return pass.
  - If TrueData live feed is down, bid/ask check returns pass.
  - No regressions: existing ICT logic is unchanged when data is unavailable.

All functions return (passed: bool, reason: str).

Integration points in scanner/silver_bullet.py:
  1. After find_draw_on_liquidity()  → score_dol_by_oi()       [scoring boost]
  2. After in_fvg check passes       → check_oi_entry_filter()  [entry gate]
  3. Same location                   → check_bidask_filter()    [spread gate]
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — tuned empirically; adjust after 90+ day paid subscription data
# ---------------------------------------------------------------------------

# OI spike = bar OI > rolling mean × this multiplier
_OI_SPIKE_MULTIPLIER   = 1.25   # 25% above rolling mean = institutional activity

# Rolling window for OI mean (bars)
_OI_ROLLING_WINDOW     = 20

# Entry OI confirmation: OI must be rising over last N bars at FVG touch
_OI_RISING_WINDOW      = 3

# Bid/ask spread limits (% of LTP) per instrument class
_SPREAD_LIMIT_INDEX    = 0.001   # 0.10% — NIFTY/BANKNIFTY futures
_SPREAD_LIMIT_DEFAULT  = 0.002   # 0.20% — FINNIFTY/MIDCPNIFTY (lower liquidity)


# ---------------------------------------------------------------------------
# 1. OI-Weighted DOL Score
# ---------------------------------------------------------------------------

def score_dol_by_oi(df: pd.DataFrame, dol: dict) -> Tuple[float, str]:
    """
    Return an OI-based score boost (0.0–2.0) for a DOL level.

    High OI at the swing high/low means institutional positions are
    defending that level — it's a stronger liquidity pool.

    Returns:
        (boost: float, reason: str)
        boost == 0.0  → no OI data or no spike at DOL level
        boost == 1.0  → OI spike within 5 bars of the DOL level
        boost == 2.0  → OI spike AND EQH/EQL cluster (densest liquidity)
    """
    if "oi" not in df.columns or df["oi"].isna().all():
        return 0.0, "NO_OI_DATA"

    try:
        dol_level   = dol.get("level", 0.0)
        dol_type    = dol.get("type", "")
        is_eqh_eql  = dol.get("is_eqh_eql", False)

        recent = df.tail(60).reset_index(drop=True)
        oi_mean = recent["oi"].rolling(_OI_ROLLING_WINDOW, min_periods=5).mean()

        # Find bars within 0.3% of the DOL level
        tolerance = dol_level * 0.003
        near_dol  = recent[
            (recent["high"] >= dol_level - tolerance) &
            (recent["low"]  <= dol_level + tolerance)
        ]

        if near_dol.empty:
            return 0.0, "DOL_NOT_IN_WINDOW"

        # Check if any bar at/near the DOL had an OI spike
        oi_at_dol   = recent.loc[near_dol.index, "oi"]
        mean_at_dol = oi_mean.loc[near_dol.index]
        has_spike   = (oi_at_dol > mean_at_dol * _OI_SPIKE_MULTIPLIER).any()

        if not has_spike:
            return 0.0, f"NO_OI_SPIKE_AT_{dol_type}"

        if is_eqh_eql:
            logger.debug(
                "OI DOL boost +2.0: spike at %s %.1f (EQH/EQL cluster)",
                dol_type, dol_level,
            )
            return 2.0, f"OI_SPIKE_EQH_EQL_{dol_type}"

        logger.debug(
            "OI DOL boost +1.0: spike at %s %.1f (single swing)",
            dol_type, dol_level,
        )
        return 1.0, f"OI_SPIKE_{dol_type}"

    except Exception as e:
        logger.debug("score_dol_by_oi error: %s", e)
        return 0.0, "OI_ERROR"


# ---------------------------------------------------------------------------
# 2. OI Entry Confirmation Filter
# ---------------------------------------------------------------------------

def check_oi_entry_filter(
    df: pd.DataFrame,
    direction: str,
    lookback: int = _OI_RISING_WINDOW,
) -> Tuple[bool, str]:
    """
    Confirm OI is building in the direction of the trade at FVG touch.

    BULLISH entry: OI should be rising (new longs being added = conviction).
    BEARISH entry: OI should be rising (new shorts being added = conviction).

    Both directions require rising OI because any new institutional commitment
    is expressed through increasing open interest, regardless of direction.

    Declining OI at entry = profit-taking / position reduction = weaker signal.

    Returns:
        (passed: bool, reason: str)
    """
    if "oi" not in df.columns or df["oi"].isna().all():
        return True, "NO_OI_PASS_THROUGH"

    try:
        if len(df) < lookback + 2:
            return True, "INSUFFICIENT_BARS"

        recent_oi = df["oi"].iloc[-(lookback + 1):].values

        # Check if OI trend is generally upward
        oi_start = float(recent_oi[0])
        oi_end   = float(recent_oi[-1])

        if oi_end <= 0 or oi_start <= 0:
            return True, "OI_ZERO_PASS"

        pct_change = (oi_end - oi_start) / oi_start

        # Rising OI: at least 0.5% increase over lookback bars
        if pct_change >= 0.005:
            logger.debug(
                "OI entry filter PASS: OI +%.2f%% over %d bars (%s)",
                pct_change * 100, lookback, direction,
            )
            return True, f"OI_RISING_{pct_change * 100:.1f}pct"

        # Flat OI: within ±0.5% — treat as neutral, allow trade
        if abs(pct_change) < 0.005:
            logger.debug(
                "OI entry filter PASS (flat): OI %.2f%% over %d bars",
                pct_change * 100, lookback,
            )
            return True, f"OI_FLAT_{pct_change * 100:.1f}pct"

        # Declining OI: > -0.5% drop — weaker signal, block
        logger.info(
            "OI entry filter: OI declining %.2f%% over %d bars — %s setup weakened",
            abs(pct_change) * 100, lookback, direction,
        )
        return False, f"OI_DECLINING_{abs(pct_change) * 100:.1f}pct"

    except Exception as e:
        logger.debug("check_oi_entry_filter error: %s", e)
        return True, "OI_ERROR_PASS"


# ---------------------------------------------------------------------------
# 3. OI Position Confirmation at Target (for SL trail logic)
# ---------------------------------------------------------------------------

def check_oi_at_target(
    df: pd.DataFrame,
    target_level: float,
    direction: str,
) -> Tuple[bool, str]:
    """
    Check whether institutional positions are clustered near a target level.
    Used to decide whether to trail SL aggressively or let the trade run.

    If OI spikes within 1% of T2/T3:
    - Institutions are defending that level (opposing your direction)
    - Trail SL more aggressively — don't let a winner turn to breakeven

    Returns:
        (spike_present: bool, reason: str)
        True = institutional defense near target → trail SL tight
        False = clean target area → let it run to T3
    """
    if "oi" not in df.columns or df["oi"].isna().all():
        return False, "NO_OI_DATA"

    try:
        recent   = df.tail(40).reset_index(drop=True)
        oi_mean  = recent["oi"].mean()
        if oi_mean <= 0:
            return False, "OI_MEAN_ZERO"

        tolerance   = target_level * 0.01
        near_target = recent[
            (recent["high"] >= target_level - tolerance) &
            (recent["low"]  <= target_level + tolerance)
        ]

        if near_target.empty:
            return False, "TARGET_NOT_IN_HISTORY"

        oi_at_target = recent.loc[near_target.index, "oi"]
        has_spike    = (oi_at_target > oi_mean * _OI_SPIKE_MULTIPLIER).any()

        if has_spike:
            logger.info(
                "OI at target %.1f: institutional defense detected — trail SL tight (%s)",
                target_level, direction,
            )
            return True, f"OI_DEFENSE_AT_{target_level:.1f}"

        return False, f"CLEAN_TARGET_{target_level:.1f}"

    except Exception as e:
        logger.debug("check_oi_at_target error: %s", e)
        return False, "OI_ERROR"


# ---------------------------------------------------------------------------
# 4. Bid/Ask Spread FVG Filter
# ---------------------------------------------------------------------------

def check_bidask_filter(
    symbol: str,
    fvg_low: float,
    fvg_high: float,
) -> Tuple[bool, str]:
    """
    Verify bid/ask spread at the FVG touch is within acceptable bounds.

    Wide spread inside an FVG = low market liquidity at that level.
    Entering on a wide spread means immediate slippage and poor fill quality.

    The spread limit scales by instrument:
    - NIFTY/BANKNIFTY: 0.10% of LTP (most liquid)
    - FINNIFTY/MIDCPNIFTY: 0.20% of LTP (less liquid)

    Returns:
        (passed: bool, reason: str)
    """
    try:
        from scanner.websocket_feed import get_latest_tick
        tick = get_latest_tick(symbol)

        bid = tick.get("bid") or tick.get("best_bid")
        ask = tick.get("ask") or tick.get("best_ask")
        ltp = tick.get("ltp")

        if not bid or not ask or not ltp or float(ltp) <= 0:
            return True, "NO_BIDASK_PASS_THROUGH"

        bid = float(bid)
        ask = float(ask)
        ltp = float(ltp)

        spread     = ask - bid
        spread_pct = spread / ltp

        # Pick limit based on instrument
        sym_upper = symbol.upper()
        if "BANKNIFTY" in sym_upper or "NIFTYBANK" in sym_upper:
            limit = _SPREAD_LIMIT_INDEX
        elif "NIFTY50" in sym_upper or "NIFTY-I" in sym_upper:
            limit = _SPREAD_LIMIT_INDEX
        else:
            limit = _SPREAD_LIMIT_DEFAULT

        if spread_pct > limit:
            logger.info(
                "Bid/Ask filter: %s spread=%.3f%% > %.3f%% limit at LTP=%.1f — skip FVG entry",
                symbol, spread_pct * 100, limit * 100, ltp,
            )
            return False, f"SPREAD_WIDE_{spread_pct * 100:.3f}pct"

        logger.debug(
            "Bid/Ask filter PASS: %s spread=%.4f%% LTP=%.1f", symbol, spread_pct * 100, ltp
        )
        return True, f"SPREAD_OK_{spread_pct * 100:.4f}pct"

    except Exception as e:
        logger.debug("check_bidask_filter error: %s", e)
        return True, "BIDASK_ERROR_PASS"


# ---------------------------------------------------------------------------
# 5. OI Divergence Warning (informational, does not block trades)
# ---------------------------------------------------------------------------

def get_oi_divergence_signal(df: pd.DataFrame, direction: str) -> Optional[str]:
    """
    Detect OI divergence — price moving in direction but OI falling.
    This suggests the move is driven by short covering / long liquidation,
    not new institutional commitment. Informational only — added to alert notes.

    Returns:
        'DIVERGENCE' | 'CONFIRMATION' | None (no OI data)
    """
    if "oi" not in df.columns or len(df) < 8:
        return None

    try:
        recent    = df.tail(8)
        oi_trend  = recent["oi"].iloc[-1] > recent["oi"].iloc[-5]   # rising = confirmation
        price_up  = recent["close"].iloc[-1] > recent["close"].iloc[-5]

        if direction == "BULLISH":
            if price_up and oi_trend:
                return "CONFIRMATION"  # price up + OI up = real longs
            if price_up and not oi_trend:
                return "DIVERGENCE"    # price up + OI down = short covering only

        if direction == "BEARISH":
            if not price_up and oi_trend:
                return "CONFIRMATION"  # price down + OI up = real shorts
            if not price_up and not oi_trend:
                return "DIVERGENCE"    # price down + OI down = long liquidation only

        return None

    except Exception:
        return None
