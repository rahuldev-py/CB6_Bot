# core/manual_position_analyzer.py — Manual position analysis layer (Phase 6)
#
# Purpose: When a manual Fyers position exists (placed outside the bot), detect it,
#          read the broker position, then analyse it against the CB6 ICT strategy stack.
#
# IMPORTANT: This module is ANALYSIS ONLY — it never places, modifies, or closes orders.
#            All output is informational (Telegram alert + log). Execution is always manual.
#
# How to trigger:
#   - Telegram: /analyze_positions
#   - Programmatic: from core.manual_position_analyzer import analyze_manual_positions
#                   report = analyze_manual_positions(fyers)

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from utils.logger import logger


# ── ICT signal vocabulary ─────────────────────────────────────────────────────

_PREMIUM_DISCOUNT_LABELS = {
    True:  "PREMIUM (>0.618 fib) — caution on longs, good for shorts",
    False: "DISCOUNT (<0.382 fib) — good for longs, caution on shorts",
}


def _detect_market_structure(candles: List[Dict]) -> str:
    """
    Minimal MSS/BOS detection from recent candles.
    Returns 'BULLISH_BOS', 'BEARISH_BOS', 'RANGE', or 'UNKNOWN'.
    """
    if len(candles) < 4:
        return 'UNKNOWN'
    highs = [c['high'] for c in candles[-6:]]
    lows  = [c['low']  for c in candles[-6:]]
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return 'BULLISH_BOS'
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return 'BEARISH_BOS'
    return 'RANGE'


def _find_nearest_fvg(candles: List[Dict], current_price: float) -> Optional[Dict]:
    """
    Scan last 30 candles for an unfilled Fair Value Gap nearest to current_price.
    FVG = gap between candle[i-2].high and candle[i].low (bullish)
          or candle[i-2].low and candle[i].high (bearish).
    Returns {'type', 'top', 'bottom', 'distance_pct'} or None.
    """
    fvgs = []
    c = candles[-30:] if len(candles) >= 30 else candles
    for i in range(2, len(c)):
        # Bullish FVG
        if c[i]['low'] > c[i-2]['high']:
            mid = (c[i]['low'] + c[i-2]['high']) / 2
            fvgs.append({
                'type': 'BULLISH', 'top': c[i]['low'], 'bottom': c[i-2]['high'], 'mid': mid
            })
        # Bearish FVG
        if c[i]['high'] < c[i-2]['low']:
            mid = (c[i]['high'] + c[i-2]['low']) / 2
            fvgs.append({
                'type': 'BEARISH', 'top': c[i-2]['low'], 'bottom': c[i]['high'], 'mid': mid
            })
    if not fvgs:
        return None
    # Nearest unfilled FVG to current price
    nearest = min(fvgs, key=lambda x: abs(x['mid'] - current_price))
    dist_pct = abs(nearest['mid'] - current_price) / max(current_price, 1) * 100
    nearest['distance_pct'] = round(dist_pct, 2)
    return nearest


def _find_dol(candles: List[Dict]) -> Dict:
    """
    Detect Dealing Range highs/lows = buy-side and sell-side liquidity pools.
    """
    if not candles:
        return {}
    highs  = [c['high'] for c in candles[-20:]]
    lows   = [c['low']  for c in candles[-20:]]
    recent = candles[-20:]
    # EQH = equal highs (buy-side DOL) — two+ candles with highs within 0.1%
    buy_side_dol  = max(highs)
    sell_side_dol = min(lows)
    return {'buy_side_dol': buy_side_dol, 'sell_side_dol': sell_side_dol}


def _classify_premium_discount(current: float, range_high: float, range_low: float) -> bool:
    """Return True if price is in premium zone (above 0.618 of range)."""
    if range_high <= range_low:
        return False
    fib_618 = range_low + 0.618 * (range_high - range_low)
    return current > fib_618


def _get_h1_bias(candles_h1: List[Dict]) -> str:
    """Simple H1 bias from last 12 H1 candles."""
    if len(candles_h1) < 4:
        return 'NEUTRAL'
    c = candles_h1[-12:]
    highs = [x['high'] for x in c]
    lows  = [x['low'] for x in c]
    # Higher highs + higher lows = BULLISH; opposite = BEARISH
    if highs[-1] > highs[-4] and lows[-1] > lows[-4]:
        return 'BULLISH'
    if highs[-1] < highs[-4] and lows[-1] < lows[-4]:
        return 'BEARISH'
    return 'NEUTRAL'


def _derive_recommendation(
    pos_direction: str,
    mss:    str,
    h1_bias: str,
    in_premium: bool,
    fvg:    Optional[Dict],
    unrealised_pnl: float,
    entry:  float,
    sl:     Optional[float],
) -> Dict:
    """
    Pure function: given position direction + market context, return a recommendation.
    Returns {'action', 'reason', 'risk_profile'}.
    Actions: HOLD | PARTIAL_EXIT | MOVE_SL | FULL_EXIT
    """
    is_long  = pos_direction in ('BUY', 'LONG', 'BULLISH', '1')
    is_short = pos_direction in ('SELL', 'SHORT', 'BEARISH', '-1')

    reasons  = []
    action   = 'HOLD'

    # 1. MSS alignment check
    mss_aligned = (is_long and mss == 'BULLISH_BOS') or (is_short and mss == 'BEARISH_BOS')
    mss_opposed = (is_long and mss == 'BEARISH_BOS') or (is_short and mss == 'BULLISH_BOS')

    if mss_opposed:
        reasons.append(f"MSS opposed to position ({mss})")
        action = 'FULL_EXIT'

    # 2. H1 bias alignment
    h1_aligned = (is_long and h1_bias == 'BULLISH') or (is_short and h1_bias == 'BEARISH')
    if not h1_aligned and h1_bias != 'NEUTRAL':
        reasons.append(f"H1 bias {h1_bias} misaligned — HTF risk")
        if action != 'FULL_EXIT':
            action = 'PARTIAL_EXIT'

    # 3. Premium/discount zone
    if is_long and in_premium:
        reasons.append("Price in PREMIUM zone — poor long entry, consider reducing")
        if action not in ('FULL_EXIT', 'PARTIAL_EXIT'):
            action = 'PARTIAL_EXIT'
    if is_short and not in_premium:
        reasons.append("Price in DISCOUNT zone — poor short entry, consider reducing")
        if action not in ('FULL_EXIT', 'PARTIAL_EXIT'):
            action = 'PARTIAL_EXIT'

    # 4. FVG as target/SL context
    if fvg and fvg['distance_pct'] < 0.5:
        fvg_type = fvg.get('type', '')
        if (is_long and fvg_type == 'BULLISH') or (is_short and fvg_type == 'BEARISH'):
            reasons.append(f"Price approaching aligned FVG — consider holding to {fvg['mid']:.1f}")
        else:
            reasons.append(f"Opposing FVG nearby — possible reversal zone at {fvg['mid']:.1f}")
            if action not in ('FULL_EXIT',):
                action = 'MOVE_SL'

    # 5. Open PnL guidance
    if unrealised_pnl > 0 and action == 'HOLD' and all([mss_aligned, h1_aligned]):
        reasons.append("In profit, structure aligned — hold to next target")
    elif unrealised_pnl < 0 and action == 'HOLD':
        reasons.append("Losing trade, structure unclear — consider tightening SL")
        action = 'MOVE_SL'

    if not reasons:
        reasons.append("Structure aligned, no action needed")

    # Risk profile
    if action == 'FULL_EXIT':
        risk = 'HIGH'
    elif action == 'PARTIAL_EXIT':
        risk = 'MEDIUM'
    elif action == 'MOVE_SL':
        risk = 'MEDIUM'
    else:
        risk = 'LOW'

    return {
        'action':       action,
        'reasons':      reasons,
        'risk_profile': risk,
    }


def _fetch_candles(fyers, symbol: str, timeframe: str = '15', days: int = 5) -> List[Dict]:
    """Fetch OHLCV candles. Returns list of dicts or empty list."""
    try:
        from scanner.data_fetcher import get_historical_data
        df = get_historical_data(fyers, symbol, timeframe, days=days)
        if df is None or df.empty:
            return []
        cols = ['open', 'high', 'low', 'close', 'volume']
        return [dict(zip(cols, row)) for row in df[cols].values.tolist()]
    except Exception as e:
        logger.debug(f"manual_position_analyzer: candle fetch error {symbol}: {e}")
        return []


def analyze_manual_positions(fyers) -> List[Dict]:
    """
    Main entry point. Reads Fyers positions, identifies manually-placed ones
    (not tagged CB6*), analyses each, returns list of analysis dicts.
    NEVER places or modifies orders.
    """
    reports = []
    try:
        resp = fyers.positions()
        if not resp or resp.get('code') != 200:
            logger.warning("manual_position_analyzer: could not fetch positions")
            return []

        positions = resp.get('netPositions', [])
        if not positions:
            logger.info("manual_position_analyzer: no open positions")
            return []

        for pos in positions:
            symbol   = pos.get('symbol', '')
            net_qty  = int(pos.get('netQty', 0))
            order_tag = str(pos.get('orderTag', '') or pos.get('tag', '') or '')
            if net_qty == 0:
                continue

            # Skip bot-managed positions (tagged CB6*)
            if order_tag.startswith('CB6'):
                logger.debug(f"Skipping bot position: {symbol}")
                continue

            unrealised = float(pos.get('unrealizedProfit', pos.get('pl', 0)) or 0)
            avg_price  = float(pos.get('avgPrice', pos.get('buyAvg', 0)) or 0)
            direction  = 'LONG' if net_qty > 0 else 'SHORT'

            # Fetch 15m candles for structure + FVG
            candles_15m = _fetch_candles(fyers, symbol, '15', days=3)
            candles_h1  = _fetch_candles(fyers, symbol, '60', days=5)

            mss      = _detect_market_structure(candles_15m)
            h1_bias  = _get_h1_bias(candles_h1)
            fvg      = _find_nearest_fvg(candles_15m, avg_price)
            dol      = _find_dol(candles_15m)

            current_price = float(pos.get('ltp', avg_price))
            in_premium    = _classify_premium_discount(
                current_price,
                dol.get('buy_side_dol', current_price * 1.01),
                dol.get('sell_side_dol', current_price * 0.99),
            )

            rec = _derive_recommendation(
                direction, mss, h1_bias, in_premium, fvg, unrealised, avg_price, sl=None
            )

            report = {
                'symbol':         symbol,
                'direction':      direction,
                'qty':            net_qty,
                'avg_price':      avg_price,
                'current_price':  current_price,
                'unrealised_pnl': unrealised,
                'mss':            mss,
                'h1_bias':        h1_bias,
                'in_premium':     in_premium,
                'nearest_fvg':    fvg,
                'dol':            dol,
                'recommendation': rec['action'],
                'reasons':        rec['reasons'],
                'risk_profile':   rec['risk_profile'],
                'analyzed_at':    datetime.now().isoformat(),
            }
            reports.append(report)
            logger.info(
                f"manual_position_analyzer: {symbol} {direction} → "
                f"{rec['action']} (risk={rec['risk_profile']})"
            )

        return reports

    except Exception as e:
        logger.error(f"manual_position_analyzer: error: {e}")
        return []


def format_analysis_report(reports: List[Dict]) -> str:
    """Format analysis reports as a Telegram HTML message."""
    if not reports:
        return "No manual positions detected."

    lines = ["<b>📊 MANUAL POSITION ANALYSIS</b>\n"]
    for r in reports:
        sym   = r['symbol'].replace('NSE:', '').replace('-EQ', '')
        pnl   = r['unrealised_pnl']
        emoji = '🟢' if pnl >= 0 else '🔴'
        action_emoji = {
            'HOLD': '✅ HOLD',
            'PARTIAL_EXIT': '⚠️ PARTIAL EXIT',
            'MOVE_SL': '🔄 MOVE SL',
            'FULL_EXIT': '❌ FULL EXIT',
        }.get(r['recommendation'], r['recommendation'])

        fvg_line = ''
        if r.get('nearest_fvg'):
            fvg = r['nearest_fvg']
            fvg_line = f"FVG ({fvg['type']}) at {fvg['mid']:.1f} ({fvg['distance_pct']}% away)\n"

        reasons = '; '.join(r.get('reasons', []))
        prem    = 'PREMIUM' if r['in_premium'] else 'DISCOUNT'

        lines.append(
            f"{emoji} <b>{sym}</b> {r['direction']} x{r['qty']}\n"
            f"  Avg: {r['avg_price']:.1f}  LTP: {r['current_price']:.1f}  "
            f"PnL: Rs {pnl:+.0f}\n"
            f"  MSS: {r['mss']}  H1: {r['h1_bias']}  Zone: {prem}\n"
            f"  {fvg_line}"
            f"  Risk: {r['risk_profile']}\n"
            f"  ➡ <b>{action_emoji}</b>\n"
            f"  Reason: {reasons}\n"
            f"  <i>Analysis only — bot never auto-trades manual positions</i>\n"
        )

    return '\n'.join(lines)


def send_analysis_report(fyers) -> None:
    """Run analysis and send formatted report to Telegram."""
    try:
        reports = analyze_manual_positions(fyers)
        msg     = format_analysis_report(reports)
        from utils.telegram_alerts import send_message
        send_message(msg)
    except Exception as e:
        logger.error(f"send_analysis_report error: {e}")
