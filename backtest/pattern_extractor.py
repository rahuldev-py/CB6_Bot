# backtest/pattern_extractor.py
# ─────────────────────────────────────────────────────────────────────────────
# Reads ALL Silver Bullet backtest CSV trades and converts each row into a
# standardised pattern fingerprint that the similarity engine can score
# against a live setup.
#
# Fingerprint dimensions (same set used by both backtest rows and live setups):
#   window              — 'morning' | 'afternoon'
#   hour                — int  (IST hour of the candle)
#   minutes_into_window — int  (minutes elapsed since window open)
#   direction           — 'BULLISH' | 'BEARISH'
#   fvg_size_pts        — float  (FVG gap size in index points)
#   displacement        — bool   (institutional displacement candle)
#   score               — int    (0–10 confluence score)
#   r_achieved          — float  (actual R from backtest walk-forward)
#   targets_hit         — list['T1','T2','T3']
#   is_win              — bool
#   result              — 'TARGET_HIT' | 'SL_HIT' | 'TIMEOUT'
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

# FVG_BUFFER from silver_bullet.py — entry is offset by this from FVG edge
_FVG_BUFFER = 0.5

# Silver Bullet window definitions (IST minutes from midnight)
_WINDOWS = [
    ('morning',   10 * 60,       11 * 60),
    ('afternoon', 13 * 60 + 30,  14 * 60 + 30),
]


def _classify_window(hour: int, minute: int) -> tuple:
    """Return (window_name, minutes_into_window) for a given IST time."""
    cur = hour * 60 + minute
    for name, start, end in _WINDOWS:
        if start <= cur < end:
            return name, cur - start
    # Outside any window — use nearest one for fingerprinting
    best_name, best_dist = 'morning', 9999
    for name, start, _ in _WINDOWS:
        d = abs(cur - start)
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name, 0


def _parse_targets(targets_hit_raw) -> List[str]:
    """Normalise targets_hit from CSV string or list."""
    if isinstance(targets_hit_raw, list):
        return targets_hit_raw
    if not targets_hit_raw or targets_hit_raw in ('[]', ''):
        return []
    # Stored as "['T1', 'T2']" or "T1,T2"
    cleaned = targets_hit_raw.strip("[]").replace("'", "").replace('"', '')
    return [t.strip() for t in cleaned.split(',') if t.strip()]


def row_to_fingerprint(row: Dict, idx: int = 0) -> Optional[Dict]:
    """Convert one CSV row (dict from DictReader) to a pattern fingerprint."""
    try:
        direction_raw = row.get('direction', '').upper()
        direction = 'BULLISH' if direction_raw == 'BUY' else 'BEARISH'

        time_str = row.get('time', '10:00')
        parts = time_str.split(':')
        hour   = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0

        window, mins_in = _classify_window(hour, minute)

        # FVG size: risk_pts ≈ fvg_size + FVG_BUFFER (see silver_bullet.py math)
        risk_pts = float(row.get('risk_pts', 0) or 0)
        fvg_size = max(0.0, risk_pts - _FVG_BUFFER)

        displacement = str(row.get('fvg_displacement', 'False')).strip().lower() in (
            'true', '1', 'yes'
        )
        score        = int(float(row.get('score', 5) or 5))
        r_achieved   = float(row.get('r_multiple', 0) or 0)
        targets_hit  = _parse_targets(row.get('targets_hit', []))
        is_win       = str(row.get('is_win', 'False')).strip().lower() in ('true', '1', 'yes')
        result       = row.get('result', 'TIMEOUT')

        return {
            'id'                  : row.get('date', '') + '_' + time_str.replace(':', '') + '_' + direction_raw + f'_{idx}',
            'date'                : row.get('date', ''),
            'time'                : time_str,
            'window'              : window,
            'hour'                : hour,
            'minutes_into_window' : mins_in,
            'direction'           : direction,
            'fvg_size_pts'        : round(fvg_size, 2),
            'displacement'        : displacement,
            'score'               : score,
            'r_achieved'          : round(r_achieved, 2),
            'targets_hit'         : targets_hit,
            'is_win'              : is_win,
            'result'              : result,
        }
    except Exception as e:
        return None


def load_csv_patterns(csv_path: str) -> List[Dict]:
    """Load all trades from a backtest CSV and return list of fingerprints."""
    if not os.path.exists(csv_path):
        return []
    patterns = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            fp = row_to_fingerprint(row, idx)
            if fp is not None:
                patterns.append(fp)
    return patterns


def load_all_backtest_patterns(data_dir: str = 'data') -> List[Dict]:
    """
    Load from every Silver Bullet backtest CSV found in data_dir.
    Deduplicates by id so re-running the backtest doesn't double-count.
    """
    csv_files = [
        os.path.join(data_dir, f)
        for f in os.listdir(data_dir)
        if f.startswith('backtest_') and f.endswith('.csv')
    ] if os.path.isdir(data_dir) else []

    seen     = set()
    combined = []
    for path in csv_files:
        for fp in load_csv_patterns(path):
            if fp['id'] not in seen:
                seen.add(fp['id'])
                combined.append(fp)
    return combined


def extract_live_fingerprint(setup: Dict) -> Optional[Dict]:
    """
    Convert a live scan_silver_bullet() setup dict into the same fingerprint
    format used by the pattern library, so similarity can be computed directly.
    """
    try:
        from scanner.silver_bullet import minutes_into_window
        from scanner.silver_bullet import SILVER_BULLET_WINDOWS

        direction = setup.get('direction', '')    # 'BULLISH' or 'BEARISH'
        window_raw = setup.get('window', '').lower()
        if 'morning' in window_raw:
            window = 'morning'
        elif 'afternoon' in window_raw or 'london' in window_raw:
            window = 'afternoon'
        else:
            window = 'morning'

        mins_in = minutes_into_window()
        if mins_in < 0:
            mins_in = 0

        from datetime import datetime
        import pytz
        now  = datetime.now(pytz.timezone('Asia/Kolkata'))
        hour = now.hour

        fvg      = setup.get('fvg') or {}
        fvg_size = float(fvg.get('size', 0))
        disp     = bool(fvg.get('displacement', False))
        score    = int(setup.get('confluence', 0))

        return {
            'id'                  : 'LIVE_NOW',
            'date'                : now.strftime('%Y-%m-%d'),
            'time'                : now.strftime('%H:%M'),
            'window'              : window,
            'hour'                : hour,
            'minutes_into_window' : mins_in,
            'direction'           : direction,
            'fvg_size_pts'        : round(fvg_size, 2),
            'displacement'        : disp,
            'score'               : score,
            'r_achieved'          : 0.0,   # unknown
            'targets_hit'         : [],
            'is_win'              : None,
            'result'              : 'LIVE',
        }
    except Exception:
        return None
