# scanner/nifty_levels.py
# ─────────────────────────────────────────────────────────────────────────────
# NIFTY ICT levels analysis + backtest-matched buy/sell probability.
#
# Fetches live 5-min + 15-min + daily data, maps all ICT key levels,
# cross-references the backtest pattern library for directional edge,
# and returns a Telegram-ready formatted report.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import logger

IST = pytz.timezone('Asia/Kolkata')


# ── helpers ───────────────────────────────────────────────────────────────────

def _wr(trades: List[Dict]) -> Tuple[float, int, int]:
    wins  = sum(1 for t in trades if t.get('is_win'))
    total = len(trades)
    return round(wins / max(total, 1) * 100, 1), wins, total


def _avg_r(trades: List[Dict]) -> Tuple[float, float]:
    wins   = [t for t in trades if t.get('is_win')]
    losses = [t for t in trades if not t.get('is_win')]
    ar_w   = round(sum(t.get('r_achieved', 0) for t in wins)   / max(len(wins),   1), 2)
    ar_l   = round(sum(t.get('r_achieved', 0) for t in losses) / max(len(losses), 1), 2)
    return ar_w, ar_l


def _swing_points(df, n: int = 80, window: int = 3) -> Tuple[List, List]:
    recent = df.tail(n).reset_index(drop=True)
    highs, lows = [], []
    for i in range(window, len(recent) - window):
        h = float(recent['high'].iloc[i])
        l = float(recent['low'].iloc[i])
        if h == float(recent['high'].iloc[i - window:i + window + 1].max()):
            highs.append(round(h, 2))
        if l == float(recent['low'].iloc[i - window:i + window + 1].min()):
            lows.append(round(l, 2))
    return sorted(set(highs), reverse=True)[:5], sorted(set(lows))[:5]


def _bar(pct: float, width: int = 15) -> str:
    filled = int(pct / 100 * width)
    return '█' * filled + '░' * (width - filled)


# ── core analysis ─────────────────────────────────────────────────────────────

def analyse_nifty(fyers) -> Dict:
    """
    Full analysis: levels + ICT chain + pattern WR + FII/DII → probability.
    Returns a result dict. Call format_report() to get the Telegram string.
    """
    from scanner.data_fetcher  import get_historical_data
    from scanner.index_futures import get_active_futures
    from scanner.silver_bullet import (
        find_draw_on_liquidity, detect_sb_mss, detect_sb_fvg,
        opening_range_swept, get_opening_range,
    )
    from data.pattern_library  import load_library
    from data.fii_dii          import get_market_bias_from_fii_dii

    futures   = get_active_futures()
    nifty_fut = futures['NIFTY']
    nifty_idx = 'NSE:NIFTY50-INDEX'

    df5  = get_historical_data(fyers, nifty_fut, '5',  days=5)
    df15 = get_historical_data(fyers, nifty_fut, '15', days=10)
    dfd  = get_historical_data(fyers, nifty_idx, 'D',  days=30)

    if df5 is None or df15 is None:
        return {'error': 'No data returned — check Fyers token'}

    ltp       = round(float(df5['close'].iloc[-1]), 2)
    now_ist   = datetime.now(IST)
    today_str = now_ist.strftime('%Y-%m-%d')

    # Previous day levels
    prev_high = prev_low = prev_close = None
    if dfd is not None and len(dfd) >= 2:
        yd         = dfd.iloc[-2]
        prev_high  = round(float(yd['high']),  2)
        prev_low   = round(float(yd['low']),   2)
        prev_close = round(float(yd['close']), 2)

    # Today's session levels
    today_mask  = df5['timestamp'].dt.strftime('%Y-%m-%d') == today_str
    today_bars  = df5[today_mask]
    day_high    = round(float(today_bars['high'].max()),  2) if len(today_bars) else ltp
    day_low     = round(float(today_bars['low'].min()),   2) if len(today_bars) else ltp
    open_price  = round(float(today_bars['open'].iloc[0]), 2) if len(today_bars) else ltp

    # Opening range (Judas Swing zone)
    or_range  = get_opening_range(df5)
    or_high   = round(or_range['high'], 2) if or_range else None
    or_low    = round(or_range['low'],  2) if or_range else None

    # ICT chain
    dol          = find_draw_on_liquidity(df15, lookback=80)
    mss          = detect_sb_mss(df15, lookback=40)
    fvg_bull     = detect_sb_fvg(df15, 'BULLISH', lookback=30)
    fvg_bear     = detect_sb_fvg(df15, 'BEARISH', lookback=30)
    or_swept_b   = opening_range_swept(df15, 'BULLISH')
    or_swept_s   = opening_range_swept(df15, 'BEARISH')

    # Swing points
    swing_highs, swing_lows = _swing_points(df15)

    # Pattern library
    library    = load_library()
    bull_lib   = [p for p in library if p.get('direction') == 'BULLISH']
    bear_lib   = [p for p in library if p.get('direction') == 'BEARISH']
    bull_wr, bull_wins, bull_n = _wr(bull_lib)
    bear_wr, bear_wins, bear_n = _wr(bear_lib)
    bull_ar_w, bull_ar_l = _avg_r(bull_lib)
    bear_ar_w, bear_ar_l = _avg_r(bear_lib)

    # Window win rates
    cur_min = now_ist.hour * 60 + now_ist.minute
    if 10*60 <= cur_min < 11*60:
        active_window = 'morning'
    elif 13*60+30 <= cur_min < 14*60+30:
        active_window = 'afternoon'
    else:
        active_window = None

    win_in_window = {}
    for direction in ('BULLISH', 'BEARISH'):
        if active_window:
            wt = [p for p in library
                  if p.get('direction') == direction and p.get('window') == active_window]
            win_in_window[direction] = _wr(wt)
        else:
            win_in_window[direction] = (0.0, 0, 0)

    # FII/DII
    try:
        fii_bias, fii_info = get_market_bias_from_fii_dii()
        fii_net   = fii_info.get('fii_net', 0)
        dii_net   = fii_info.get('dii_net', 0)
        fii_stale = fii_info.get('is_stale', True)
    except Exception:
        fii_bias = 'NEUTRAL'; fii_net = 0; dii_net = 0; fii_stale = True

    oi_data = None

    # ── Probability model ─────────────────────────────────────────────────────
    buy_score = 0.0
    sell_score = 0.0
    factors: List[str] = []

    # 1. DOL (25 pts)
    if dol:
        if dol['direction'] == 'BULLISH':
            buy_score  += 25
            factors.append(f"DOL BULLISH draw → {dol['level']} ({dol['type']} unswept)  +25 BUY")
        else:
            sell_score += 25
            factors.append(f"DOL BEARISH draw → {dol['level']} ({dol['type']} unswept)  +25 SELL")
    else:
        factors.append("DOL: not detected (no clear liquidity pool)")

    # 2. MSS (20 pts)
    if mss:
        if mss['direction'] == 'BULLISH':
            buy_score  += 20
            factors.append(f"MSS BULLISH (broke {mss['level']})  +20 BUY")
        else:
            sell_score += 20
            factors.append(f"MSS BEARISH (broke {mss['level']})  +20 SELL")
    else:
        factors.append("MSS: not confirmed (no structure shift yet)")

    # 3. Opening range sweep (10 pts)
    if or_swept_b and not or_swept_s:
        buy_score  += 10
        factors.append("Opening range swept BULLISH (Judas low done)  +10 BUY")
    elif or_swept_s and not or_swept_b:
        sell_score += 10
        factors.append("Opening range swept BEARISH (Judas high done)  +10 SELL")
    elif or_swept_b and or_swept_s:
        factors.append("Both sides of OR swept — choppy, no edge here")

    # 4. FVG at price (10 pts each)
    if fvg_bull:
        at_bull = fvg_bull['fvg_low'] <= ltp <= fvg_bull['fvg_high']
        near_b  = abs(ltp - fvg_bull['mid']) / max(fvg_bull['mid'], 1) <= 0.006
        if at_bull or near_b:
            buy_score  += 10
            factors.append(f"Price AT bullish FVG {fvg_bull['fvg_low']}–{fvg_bull['fvg_high']}  +10 BUY")
        else:
            factors.append(f"Bullish FVG at {fvg_bull['fvg_low']}–{fvg_bull['fvg_high']} (price away)")

    if fvg_bear:
        at_bear = fvg_bear['fvg_low'] <= ltp <= fvg_bear['fvg_high']
        near_s  = abs(ltp - fvg_bear['mid']) / max(fvg_bear['mid'], 1) <= 0.006
        if at_bear or near_s:
            sell_score += 10
            factors.append(f"Price AT bearish FVG {fvg_bear['fvg_low']}–{fvg_bear['fvg_high']}  +10 SELL")
        else:
            factors.append(f"Bearish FVG at {fvg_bear['fvg_low']}–{fvg_bear['fvg_high']} (price away)")

    # 5. FII/DII (15 pts)
    if not fii_stale:
        if fii_net > 500:
            buy_score  += 15
            factors.append(f"FII buying Rs {fii_net:+.0f}Cr  +15 BUY")
        elif fii_net > 0:
            buy_score  += 7
            factors.append(f"FII mildly bullish Rs {fii_net:+.0f}Cr  +7 BUY")
        elif fii_net < -500:
            sell_score += 15
            factors.append(f"FII selling Rs {fii_net:+.0f}Cr  +15 SELL")
        elif fii_net < 0:
            sell_score += 7
            factors.append(f"FII mildly bearish Rs {fii_net:+.0f}Cr  +7 SELL")
        else:
            factors.append("FII neutral (net ~0)")
    else:
        factors.append("FII/DII stale — not counted")

    # 6. OI levels / PCR (15 pts)
    if oi_data:
        pcr        = oi_data['pcr']
        pcr_bias   = oi_data['pcr_bias']
        max_ce_str = oi_data['max_ce_oi']['strike']   # resistance wall
        max_pe_str = oi_data['max_pe_oi']['strike']   # support floor

        # PCR bias
        if pcr_bias == 'BULLISH':
            buy_score  += 10
            factors.append(f"OI PCR {pcr:.2f} (>{1.2}) — writers protecting PE side  +10 BUY")
        elif pcr_bias == 'BEARISH':
            sell_score += 10
            factors.append(f"OI PCR {pcr:.2f} (<{0.8}) — writers protecting CE side  +10 SELL")
        else:
            factors.append(f"OI PCR {pcr:.2f} — neutral (no directional edge)")

        # Price position relative to max OI walls (+5 pts)
        if max_pe_str < ltp < max_ce_str:
            # Price between the walls — favour direction toward closer wall
            dist_to_ce = max_ce_str - ltp
            dist_to_pe = ltp - max_pe_str
            if dist_to_pe < dist_to_ce:
                buy_score  += 5
                factors.append(
                    f"OI: price closer to PE wall {max_pe_str:.0f} (support) than CE {max_ce_str:.0f}  +5 BUY"
                )
            else:
                sell_score += 5
                factors.append(
                    f"OI: price closer to CE wall {max_ce_str:.0f} (resistance) than PE {max_pe_str:.0f}  +5 SELL"
                )
        elif ltp >= max_ce_str:
            sell_score += 5
            factors.append(f"OI: price AT/ABOVE max CE wall {max_ce_str:.0f} — heavy resistance  +5 SELL")
        else:
            buy_score  += 5
            factors.append(f"OI: price AT/BELOW max PE wall {max_pe_str:.0f} — strong support  +5 BUY")
    else:
        factors.append("OI levels: not available (bhavcopy not downloaded yet)")

    # 7. Backtest WR edge (20 pts)
    bull_edge = bull_wr - 50
    bear_edge = bear_wr - 50
    if bull_edge > bear_edge and bull_n >= 3:
        contrib = min(bull_edge * 0.6, 20)
        buy_score  += contrib
        factors.append(f"Backtest BULLISH edge {bull_wr}% WR ({bull_n} trades)  +{contrib:.0f} BUY")
    elif bear_edge > bull_edge and bear_n >= 3:
        contrib = min(bear_edge * 0.6, 20)
        sell_score += contrib
        factors.append(f"Backtest BEARISH edge {bear_wr}% WR ({bear_n} trades)  +{contrib:.0f} SELL")
    else:
        factors.append(f"Backtest WR balanced (BUY {bull_wr}% / SELL {bear_wr}%)")

    # Normalise
    total_score = buy_score + sell_score
    buy_pct  = round(buy_score  / max(total_score, 1) * 100, 1)
    sell_pct = round(sell_score / max(total_score, 1) * 100, 1)

    # Verdict
    if buy_pct >= 65:
        verdict  = "BULLISH — BUY BIAS"
        action   = "Wait for DOL sweep → MSS → Bullish FVG → entry"
        opt_type = "CE (Call) | ITM/ATM | Delta 0.6–0.8"
    elif sell_pct >= 65:
        verdict  = "BEARISH — SELL BIAS"
        action   = "Wait for DOL sweep → MSS → Bearish FVG → entry"
        opt_type = "PE (Put) | ITM/ATM | Delta 0.6–0.8"
    else:
        verdict  = "NEUTRAL / MIXED — wait for clarity"
        action   = "No strong edge — sit out until chain aligns"
        opt_type = "No trade until direction confirmed"

    return {
        'symbol'       : nifty_fut,
        'ltp'          : ltp,
        'time_ist'     : now_ist.strftime('%H:%M IST %d %b %Y'),
        'prev_high'    : prev_high,
        'prev_low'     : prev_low,
        'prev_close'   : prev_close,
        'open_price'   : open_price,
        'day_high'     : day_high,
        'day_low'      : day_low,
        'or_high'      : or_high,
        'or_low'       : or_low,
        'swing_highs'  : swing_highs,
        'swing_lows'   : swing_lows,
        'dol'          : dol,
        'mss'          : mss,
        'fvg_bull'     : fvg_bull,
        'fvg_bear'     : fvg_bear,
        'or_swept_bull': or_swept_b,
        'or_swept_bear': or_swept_s,
        'bull_wr'      : bull_wr, 'bull_wins': bull_wins, 'bull_n': bull_n,
        'bear_wr'      : bear_wr, 'bear_wins': bear_wins, 'bear_n': bear_n,
        'bull_ar_w'    : bull_ar_w, 'bull_ar_l': bull_ar_l,
        'bear_ar_w'    : bear_ar_w, 'bear_ar_l': bear_ar_l,
        'active_window': active_window,
        'win_in_window': win_in_window,
        'total_lib'    : len(library),
        'fii_net'      : fii_net,
        'dii_net'      : dii_net,
        'fii_bias'     : fii_bias,
        'fii_stale'    : fii_stale,
        'oi_data'      : oi_data,
        'factors'      : factors,
        'buy_pct'      : buy_pct,
        'sell_pct'     : sell_pct,
        'verdict'      : verdict,
        'action'       : action,
        'opt_type'     : opt_type,
    }


# ── Telegram formatter ────────────────────────────────────────────────────────

def format_report(r: Dict) -> str:
    if 'error' in r:
        return f"CB6 NIFTY LEVELS ERROR\n\n{r['error']}"

    ltp  = r['ltp']
    lines = [
        f"CB6 NIFTY ICT LEVELS",
        f"{r['time_ist']}",
        f"",
        f"Symbol  : {r['symbol']}",
        f"LTP     : {ltp}",
        f"",
        f"--- KEY LEVELS ---",
    ]

    if r['prev_high']:
        lines += [
            f"PDH     : {r['prev_high']}  ({round(r['prev_high']-ltp,1):+.1f})",
            f"PDL     : {r['prev_low']}  ({round(r['prev_low']-ltp,1):+.1f})",
            f"PDC     : {r['prev_close']}  ({round(r['prev_close']-ltp,1):+.1f})",
        ]
    lines += [
        f"Open    : {r['open_price']}",
        f"D-High  : {r['day_high']}  (today)",
        f"D-Low   : {r['day_low']}  (today)",
    ]
    if r['or_high']:
        lines += [
            f"OR High : {r['or_high']}  (9:15-10:00 Judas)",
            f"OR Low  : {r['or_low']}",
        ]

    lines.append(f"")
    lines.append(f"SWING HIGHS (BSL pools / resistance):")
    for h in r['swing_highs']:
        dist = round(h - ltp, 1)
        lines.append(f"  {h}  ({dist:+.1f})")

    lines.append(f"")
    lines.append(f"SWING LOWS (SSL pools / support):")
    for l in r['swing_lows']:
        dist = round(l - ltp, 1)
        lines.append(f"  {l}  ({dist:+.1f})")

    lines += [
        f"",
        f"--- ICT CHAIN ---",
        f"DOL  : {'✓ ' + r['dol']['direction'] + ' → ' + str(r['dol']['level']) if r['dol'] else '✗ not detected'}",
        f"MSS  : {'✓ ' + r['mss']['direction'] + ' @ ' + str(r['mss']['level']) if r['mss'] else '✗ not confirmed'}",
        f"OR B : {'✓ swept' if r['or_swept_bull'] else '✗ not swept'}  |  OR S : {'✓ swept' if r['or_swept_bear'] else '✗ not swept'}",
        f"",
    ]

    if r['fvg_bull']:
        f = r['fvg_bull']
        tag = ' ← PRICE HERE' if f['fvg_low'] <= ltp <= f['fvg_high'] else ''
        d   = 'Y (displaced)' if f.get('displacement') else 'N (weak)'
        lines.append(f"FVG BUY : {f['fvg_low']} – {f['fvg_high']}  disp={d}{tag}")
    else:
        lines.append(f"FVG BUY : not found")

    if r['fvg_bear']:
        f = r['fvg_bear']
        tag = ' ← PRICE HERE' if f['fvg_low'] <= ltp <= f['fvg_high'] else ''
        d   = 'Y (displaced)' if f.get('displacement') else 'N (weak)'
        lines.append(f"FVG SELL: {f['fvg_low']} – {f['fvg_high']}  disp={d}{tag}")
    else:
        lines.append(f"FVG SELL: not found")

    lines += [
        f"",
        f"--- BACKTEST MATCH ({r['total_lib']} trades) ---",
        f"BULLISH : {r['bull_wr']}% WR  ({r['bull_wins']}/{r['bull_n']})  avgW {r['bull_ar_w']}R",
        f"BEARISH : {r['bear_wr']}% WR  ({r['bear_wins']}/{r['bear_n']})  avgW {r['bear_ar_w']}R",
    ]

    if r['active_window']:
        bw = r['win_in_window'].get('BULLISH', (0, 0, 0))
        sw = r['win_in_window'].get('BEARISH', (0, 0, 0))
        lines.append(
            f"In {r['active_window'].upper()} window:"
            f"  B {bw[0]}%({bw[2]})  S {sw[0]}%({sw[2]})"
        )

    stale = ' [STALE]' if r['fii_stale'] else ''
    lines += [
        f"",
        f"--- FII / DII ---",
        f"FII : Rs {r['fii_net']:+.0f}Cr  DII : Rs {r['dii_net']:+.0f}Cr{stale}",
        f"Bias: {r['fii_bias']}",
    ]


    lines += [
        f"",
        f"--- FACTORS ---",
    ]
    for fact in r['factors']:
        lines.append(f"• {fact}")

    lines += [
        f"",
        f"--- PROBABILITY ---",
        f"BUY  : {r['buy_pct']:5.1f}%  {_bar(r['buy_pct'])}",
        f"SELL : {r['sell_pct']:5.1f}%  {_bar(r['sell_pct'])}",
        f"",
        f"VERDICT : {r['verdict']}",
        f"PLAN    : {r['action']}",
        f"OPTIONS : {r['opt_type']}",
    ]

    return "\n".join(lines)
