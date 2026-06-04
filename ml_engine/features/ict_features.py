"""
ml_engine/features/ict_features.py

ICT-specific features derived from labeled dataset columns.
All features computed from pre-existing trade metadata — no live scanner calls.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def add_ict_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # ── Direction ────────────────────────────────────────────────────────────
    if "direction_bin" not in out.columns:
        out["direction_bin"] = (
            out["direction"].str.upper().isin(["BULLISH", "BUY", "LONG"])
        ).astype(float)

    # ── MSS ──────────────────────────────────────────────────────────────────
    if "choch_bin" not in out.columns:
        out["choch_bin"] = out["mss_type"].str.upper().eq("CHOCH").astype(float) \
            if "mss_type" in out.columns else 0.0

    out["bos_bin"] = out["mss_type"].str.upper().eq("BOS").astype(float) \
        if "mss_type" in out.columns else 0.0

    # ── FVG ──────────────────────────────────────────────────────────────────
    # fvg_present as 0/1
    for src in ["fvg_present", "in_fvg", "price_in_fvg", "displacement"]:
        if src in out.columns:
            out["fvg_present_bin"] = pd.to_numeric(
                out[src].map({True: 1, False: 0, "True": 1, "False": 0,
                              "true": 1, "false": 0}), errors="coerce"
            ).fillna(0).astype(float)
            break

    # fvg_quality ordinal
    if "fvg_quality_ord" not in out.columns:
        qmap = {"NONE": 0, "WEAK": 1, "STRONG": 2}
        if "fvg_quality" in out.columns:
            out["fvg_quality_ord"] = out["fvg_quality"].map(qmap).fillna(0).astype(float)
        else:
            out["fvg_quality_ord"] = 0.0

    # fvg displacement flag
    for src in ["fvg_displacement", "displacement"]:
        if src in out.columns:
            out["fvg_displacement_bin"] = pd.to_numeric(
                out[src].map({True: 1, False: 0, "True": 1, "False": 0,
                              "true": 1, "false": 0}), errors="coerce"
            ).fillna(0).astype(float)
            break
    else:
        out["fvg_displacement_bin"] = 0.0

    # fvg size normalised by risk
    if "fvg_size" in out.columns and "risk" in out.columns:
        risk = pd.to_numeric(out["risk"], errors="coerce").replace(0, np.nan)
        out["fvg_size_risk_ratio"] = pd.to_numeric(out["fvg_size"], errors="coerce") / risk
    else:
        out["fvg_size_risk_ratio"] = np.nan

    # ── Order Block ──────────────────────────────────────────────────────────
    for src in ["order_block_present", "ob_present", "ob_confluence"]:
        if src in out.columns:
            if src == "ob_confluence":
                out["ob_present_bin"] = pd.to_numeric(out[src], errors="coerce").gt(0).astype(float)
            else:
                out["ob_present_bin"] = pd.to_numeric(
                    out[src].map({True: 1, False: 0, "True": 1, "False": 0,
                                  "true": 1, "false": 0}), errors="coerce"
                ).fillna(0).astype(float)
            break
    else:
        out["ob_present_bin"] = 0.0

    # OB size (if zone data available)
    if all(c in out.columns for c in ["ob_high", "ob_low"]):
        hi = pd.to_numeric(out["ob_high"], errors="coerce")
        lo = pd.to_numeric(out["ob_low"],  errors="coerce")
        out["ob_size"] = (hi - lo).abs()
    else:
        out["ob_size"] = np.nan

    # ── DOL ──────────────────────────────────────────────────────────────────
    if "dol_direction" in out.columns and "direction" in out.columns:
        out["dol_agrees_bin"] = (
            out["dol_direction"].str.upper() == out["direction"].str.upper()
        ).astype(float)
    else:
        out["dol_agrees_bin"] = np.nan

    # Distance from entry to DOL level (target proxy)
    if all(c in out.columns for c in ["dol_level", "entry"]):
        entry = pd.to_numeric(out["entry"], errors="coerce")
        dol   = pd.to_numeric(out["dol_level"], errors="coerce")
        risk  = pd.to_numeric(out.get("risk"), errors="coerce").replace(0, np.nan) \
            if "risk" in out.columns else pd.Series(np.nan, index=out.index)
        out["dol_distance_r"] = (dol - entry).abs() / risk
    else:
        out["dol_distance_r"] = np.nan

    # ── UT Bot ────────────────────────────────────────────────────────────────
    for src in ["ut_aligned", "ut_bot_aligned"]:
        if src in out.columns:
            out["ut_aligned_bin"] = pd.to_numeric(
                out[src].map({True: 1, False: 0, "True": 1, "False": 0,
                              "true": 1, "false": 0}), errors="coerce"
            ).fillna(0).astype(float)
            break
    else:
        out["ut_aligned_bin"] = np.nan

    # ── Three-bar reversal ────────────────────────────────────────────────────
    for src in ["three_bar_reversal", "three_bar"]:
        if src in out.columns:
            out["three_bar_bin"] = pd.to_numeric(
                out[src].map({True: 1, False: 0, "True": 1, "False": 0,
                              "true": 1, "false": 0}), errors="coerce"
            ).fillna(0).astype(float)
            break
    else:
        out["three_bar_bin"] = 0.0

    # ── Confluence score ──────────────────────────────────────────────────────
    score_col = next((c for c in ["confluence", "score"] if c in out.columns), None)
    if score_col:
        out["confluence_score"] = pd.to_numeric(out[score_col], errors="coerce")
        # Normalise to 0-1 range (max observed ~20)
        out["confluence_norm"] = (out["confluence_score"] / 20.0).clip(0, 1)
    else:
        out["confluence_score"] = np.nan
        out["confluence_norm"]  = np.nan

    return out


ICT_FEATURE_COLS = [
    "direction_bin", "choch_bin", "bos_bin",
    "fvg_present_bin", "fvg_quality_ord", "fvg_displacement_bin", "fvg_size_risk_ratio",
    "ob_present_bin", "ob_size", "ob_agrees_dol",
    "dol_agrees_bin", "dol_distance_r",
    "ut_aligned_bin", "three_bar_bin",
    "confluence_score", "confluence_norm",
]
