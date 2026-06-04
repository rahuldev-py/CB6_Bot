"""
ml_engine/features/silver_bullet_features.py

Silver Bullet–specific composite features that cross multiple ICT concepts.
These capture the holistic quality of a setup beyond individual components.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def add_silver_bullet_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # ── A+ Setup criteria score (0-7 binary checks) ───────────────────────
    checks = {
        "choch_check"    : out.get("choch_bin", pd.Series(np.nan, index=out.index)),
        "fvg_check"      : out.get("fvg_present_bin", pd.Series(np.nan, index=out.index)),
        "disp_check"     : out.get("fvg_displacement_bin", pd.Series(np.nan, index=out.index)),
        "ob_check"       : out.get("ob_present_bin", pd.Series(np.nan, index=out.index)),
        "dol_check"      : out.get("dol_agrees_bin", pd.Series(np.nan, index=out.index)),
        "ut_check"       : out.get("ut_aligned_bin", pd.Series(np.nan, index=out.index)),
        "session_check"  : out.get("in_sb_window", out.get("in_forex_kz",
                           pd.Series(np.nan, index=out.index))),
    }

    check_df = pd.DataFrame({k: pd.to_numeric(v, errors="coerce")
                             for k, v in checks.items()}, index=out.index)
    out["sb_checklist_score"] = check_df.sum(axis=1)
    out["sb_checklist_pct"]   = check_df.mean(axis=1)

    # ── CHoCH + FVG + Displacement (A+ trifecta) ──────────────────────────
    choch = pd.to_numeric(out.get("choch_bin"), errors="coerce").fillna(0)
    fvg   = pd.to_numeric(out.get("fvg_present_bin"), errors="coerce").fillna(0)
    disp  = pd.to_numeric(out.get("fvg_displacement_bin"), errors="coerce").fillna(0)
    out["aplus_trifecta"] = ((choch == 1) & (fvg == 1) & (disp == 1)).astype(float)

    # ── CHoCH + OB confluence ─────────────────────────────────────────────
    ob  = pd.to_numeric(out.get("ob_present_bin"), errors="coerce").fillna(0)
    out["choch_ob_confluence"] = ((choch == 1) & (ob == 1)).astype(float)

    # ── FVG quality × CHoCH interaction ──────────────────────────────────
    fvg_ord = pd.to_numeric(out.get("fvg_quality_ord"), errors="coerce").fillna(0)
    out["fvg_choch_interaction"] = fvg_ord * choch

    # ── DOL distance as target quality proxy ─────────────────────────────
    dol_r = pd.to_numeric(out.get("dol_distance_r"), errors="coerce")
    rr    = pd.to_numeric(out.get("rr_ratio"), errors="coerce")
    # DOL vs stated RR: 1 = DOL aligns with T2/T3, <1 = DOL closer than T2
    out["dol_rr_ratio"] = (dol_r / rr.replace(0, np.nan)).clip(0, 5)

    # ── Regime × MSS interaction ──────────────────────────────────────────
    reg   = pd.to_numeric(out.get("regime_ord"), errors="coerce").fillna(1)
    mss   = pd.to_numeric(out.get("mss_confirmed"), errors="coerce").fillna(0)
    out["trending_mss"] = reg * mss  # 2 = trending + MSS confirmed, best

    # ── Confluence above threshold flags ─────────────────────────────────
    conf = pd.to_numeric(out.get("confluence_score", out.get("score")), errors="coerce")
    out["score_gte_10"] = (conf >= 10).astype(float)
    out["score_gte_12"] = (conf >= 12).astype(float)
    out["score_gte_14"] = (conf >= 14).astype(float)

    return out


SILVER_BULLET_FEATURE_COLS = [
    "sb_checklist_score", "sb_checklist_pct",
    "aplus_trifecta", "choch_ob_confluence", "fvg_choch_interaction",
    "dol_rr_ratio", "trending_mss",
    "score_gte_10", "score_gte_12", "score_gte_14",
]
