# forex_engine/scanner/setup_scorer.py
# Confluence scoring and A+ similarity scoring.

from typing import Optional
import pandas as pd


def score_confluence(setup: dict, nse_regime_adjust: int = 0) -> int:
    """
    Compute confluence score (max 19).
    DOL(5) + CHoCH(2)/BOS(1) + inFVG(1) + disp(1) + RR(1) + UT(2)
    + sweep(2) + high-quality sweep(1) + OB(1) + EQH/EQL(2) + regime(+1/-1)
    nse_regime_adjust: +1 if regime favours this direction, -1 if opposing, 0 if neutral.
    Only applied when symbol is NSE (pass 0 for Forex).
    """
    dol         = setup.get('dol')
    mss         = setup.get('mss')
    direction   = setup.get('direction', '')
    dol_agrees  = dol is not None and dol.get('direction') == direction
    mss_type    = mss.get('type', 'BOS') if mss else 'BOS'
    in_fvg      = setup.get('in_fvg', False)
    displacement= setup.get('fvg', {}).get('displacement', False) if setup.get('fvg') else False
    rr          = setup.get('entry_signal', {}).get('rr_ratio', 0)
    ut_aligned  = setup.get('ut_bot', {}).get('aligned', False)
    sweep_ok    = setup.get('sweep_confirmed', False)
    sweep_conf  = int(setup.get('sweep_confidence', 0) or 0)
    ob_present  = setup.get('ob_present', False)
    dol_eqh_eql = setup.get('dol_is_eqh_eql', False)

    score  = 5 if dol_agrees else 4
    score += 2 if mss_type == 'CHOCH' else 1
    score += 1 if in_fvg else 0
    score += 1 if displacement else 0
    score += 1 if rr >= 3.0 else 0
    score += 2 if ut_aligned else 0
    score += 2 if sweep_ok else 0
    score += 1 if sweep_conf >= 70 else 0
    score += 1 if ob_present else 0
    score += 2 if dol_eqh_eql else 0
    score += max(-1, min(1, nse_regime_adjust))   # capped ±1
    return score


def score_aplus_similarity(
    setup: dict,
    df15: Optional[pd.DataFrame],
    h4_bias: str,
    h1_bias: str,
    utc_hour: int,
) -> tuple[float, dict]:
    """
    Score 0.0–1.0 vs A+ template. Returns (ratio, breakdown_dict).
    12 features max (10 original + 2 for OB duration). ≥ 55% → lot boost.

    === VALIDATED TEMPLATE: DOL_SWEEP_OB_BOS_FVG v2 (2026-06-05) ===

    Reference trades:
      XAGUSD BULL 2026-05-21 16:30 UTC — score 13/15, +$144, R=2.88
      USOIL  BEAR 2026-05-21 17:30 UTC — score 14/15, +$107, R=2.13
      NIFTY  BULL 2026-06-05 14:18 IST — score 14/15, +Rs689, R=1.27

    Backtest validation (2026-06-05) — 258 LONG trades across NSE + Forex:
      NSE  LONG:  55 trades  | matched 55 (100%) | WR 61.8% | Avg R 1.78
      Forex LONG: 203 trades | matched 203 (100%)| WR 60.1% | Avg R 1.17
      A+ grade (≥85%): 226 trades | WR 62.3% | Avg R 1.30

    Feature firing rate in A+/A winning setups:
      sweep_confirmed  : 100% — MANDATORY
      bos_or_choch     : 100% — MANDATORY
      fvg_present      : 100% — MANDATORY
      kill_zone        : 100% (Forex) | present in all NSE windows
      choch_bonus      : 70–77% — CHoCH outperforms BOS for LONG
      high_sweep_qual  : 79–88% — quality sweep = institutional trap

    Key insights:
      - OB duration ≥45min confirms institutional loading (+1 pt)
      - CHoCH beats BOS for LONG entries (higher WR)
      - Counter-H4 LONGs valid at 50% size when sweep+OB+BOS all confirmed
    """
    from forex_engine.scanner.signal_scanner import is_prime_kz

    direction = setup.get('direction', '')
    pts = 0.0
    bd  = {}

    # 1. M15 bias
    m15_bias = 'RANGING'
    if df15 is not None and len(df15) >= 15:
        c    = df15['close']
        fast = c.ewm(span=5,  adjust=False).mean().iloc[-1]
        slow = c.ewm(span=13, adjust=False).mean().iloc[-1]
        if   fast > slow * 1.0001: m15_bias = 'BULLISH'
        elif fast < slow * 0.9999: m15_bias = 'BEARISH'
    m15_pt = 1.0 if m15_bias == direction else (0.5 if m15_bias == 'RANGING' else 0.0)
    pts += m15_pt;  bd['m15'] = m15_pt

    # 2. H1
    h1_pt = 1.0 if h1_bias == direction else (0.5 if h1_bias == 'RANGING' else 0.0)
    pts += h1_pt;   bd['h1'] = h1_pt

    # 3. H4
    h4_pt = 1.0 if h4_bias == direction else (0.5 if h4_bias == 'RANGING' else 0.0)
    pts += h4_pt;   bd['h4'] = h4_pt

    # 4. Score ≥ 13
    sc    = setup.get('confluence', 0)
    sc_pt = 1.0 if sc >= 13 else (0.5 if sc >= 12 else 0.0)
    pts += sc_pt;   bd['score'] = sc_pt

    # 5. Fresh sweep ≤ 5c
    liq   = setup.get('liq_sweep')
    sw_pt = (1.0 if liq and liq.get('candles_ago', 999) <= 5
             else 0.5 if liq and liq.get('candles_ago', 999) <= 15
             else 0.0)
    pts += sw_pt;   bd['sweep'] = sw_pt

    # 6. In FVG
    fvg_pt = 1.0 if setup.get('in_fvg') else 0.0
    pts += fvg_pt;  bd['in_fvg'] = fvg_pt

    # 7. Order Block
    ob_pt = 1.0 if setup.get('ob_present') else 0.0
    pts += ob_pt;   bd['ob'] = ob_pt

    # 8. UT Bot
    ut_pt = 1.0 if setup.get('ut_bot', {}).get('aligned') else 0.0
    pts += ut_pt;   bd['ut'] = ut_pt

    # 9. Kill zone
    kz_pt = 1.0 if is_prime_kz(utc_hour) else 0.5
    pts += kz_pt;   bd['kz'] = kz_pt

    # 10. Displacement ≥ 4×
    disp_pt = 0.0
    if df15 is not None and len(df15) >= 15:
        bodies   = (df15['close'] - df15['open']).abs()
        avg_body = bodies.iloc[-20:].mean()
        if avg_body > 0:
            ratio   = bodies.iloc[-15:].max() / avg_body
            disp_pt = 1.0 if ratio >= 4.0 else (0.5 if ratio >= 3.0 else 0.0)
    pts += disp_pt; bd['disp'] = disp_pt

    # 11–12. OB accumulation duration (validated 2026-06-05 NIFTY + Forex)
    ob_dur = float(setup.get('ob_duration_mins', 0) or 0)
    ob_pt  = 2.0 if ob_dur >= 90 else (1.0 if ob_dur >= 45 else (0.5 if ob_dur >= 15 else 0.0))
    pts += ob_pt; bd['ob_dur'] = ob_pt

    # 13–14. 3-Wave completion + base (validated 2026-06-05 session)
    # Rahul's rule: 3 waves complete + base + CHoCH = entry. Not before.
    # +1 if wave count ≥ 3 (exhaustion confirmed), +1 if base formed (indecision = reversal ready)
    wave_count  = int(setup.get('wave_count', 0) or 0)
    base_formed = bool(setup.get('base_formed', False))
    wave_pt = 1.0 if wave_count >= 3 else (0.5 if wave_count == 2 else 0.0)
    base_pt = 1.0 if base_formed else 0.0
    pts += wave_pt; bd['wave'] = wave_pt
    pts += base_pt; bd['base'] = base_pt

    # Max pts = 14 (12 original + 2 wave/base)
    return round(pts / 14.0, 3), bd


def lot_boost_factor(sim_ratio: float) -> float:
    if sim_ratio >= 0.85: return 2.00
    if sim_ratio >= 0.70: return 1.50
    if sim_ratio >= 0.55: return 1.25
    return 1.00
