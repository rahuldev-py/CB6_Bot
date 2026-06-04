"""
Offline strategy optimizer for CB6 research questions.

This module reads historical/labeled datasets only. It never imports live
execution modules and never changes live rules, sizing, SL, TP, or orders.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd


DATASET_PATH = Path("ml_engine/data/labeled/cb6_labeled_latest.csv")


@dataclass
class ExperimentResult:
    name: str
    market: str
    n: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    avg_r: float
    total_r: float
    max_dd_r: float
    confidence: str
    rank: str
    recommendation: str
    filters: dict

    def to_dict(self) -> dict:
        row = asdict(self)
        filters = row.get("filters", {})
        row.update({
            "symbol": filters.get("symbol", "ALL"),
            "entry_type": filters.get("entry_type", "silver_bullet_fvg_retest"),
            "exit_type": filters.get("exit_model", "logged_cb6_exit"),
            "sl_type": filters.get("sl_type", filters.get("exit_model", "logged_cb6_sl")),
            "tp_type": filters.get("tp_type", filters.get("exit_model", "logged_cb6_tp")),
            "trailing_sl_type": filters.get("trailing_sl_type", filters.get("exit_model", "logged_cb6_management")),
            "trend": filters.get("regime", "ALL"),
            "structure": filters.get("mss_type", "ALL"),
            "CHoCH": filters.get("mss_type") == "CHOCH",
            "BOS": filters.get("mss_type") == "BOS",
            "MSS": bool(filters.get("mss", filters.get("mss_type") in ("CHOCH", "BOS"))),
            "FVG": filters.get("fvg", "silver_bullet_fvg"),
            "OB": filters.get("ob_present", filters.get("ob", "ALL")),
            "displacement": filters.get("fvg_displacement", filters.get("skip_no_displacement", "ALL")),
            "session": filters.get("session", "ALL"),
            "score_gate": filters.get("score_gate", "ALL"),
        })
        return row


def _boolish(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    text = series.astype(str).str.lower()
    return text.isin(["true", "1", "yes", "y"])


def load_research_dataset(path: Path = DATASET_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Research dataset not found: {path}")
    df = pd.read_csv(path, low_memory=False)
    df = df.copy()

    if "engine" not in df.columns:
        df["engine"] = "unknown"
    df["market"] = df["engine"].str.upper().replace({"NSE": "NSE", "FOREX": "FOREX"})

    score = pd.to_numeric(df.get("confluence"), errors="coerce")
    score2 = pd.to_numeric(df.get("score"), errors="coerce")
    df["research_score"] = score.fillna(score2)

    r = pd.to_numeric(df.get("r_multiple_label"), errors="coerce")
    r2 = pd.to_numeric(df.get("r_multiple"), errors="coerce")
    df["research_r"] = r.fillna(r2)

    win = pd.to_numeric(df.get("win_loss_label"), errors="coerce")
    if win.isna().all() and "win" in df.columns:
        win = _boolish(df["win"]).astype(float)
    df["research_win"] = win

    direction = df.get("direction", pd.Series("", index=df.index)).astype(str).str.upper()
    df["research_direction"] = direction.replace({"BUY": "BULLISH", "LONG": "BULLISH", "SELL": "BEARISH", "SHORT": "BEARISH"})

    mss = df.get("mss_type", pd.Series("", index=df.index)).astype(str).str.upper()
    df["research_mss"] = mss
    df["research_choch"] = mss.eq("CHOCH")
    df["research_bos"] = mss.eq("BOS")
    df["research_mss_present"] = mss.isin(["CHOCH", "BOS"])

    regime = df.get("market_regime", df.get("regime", pd.Series("", index=df.index))).astype(str).str.upper()
    df["research_regime"] = regime

    session = df.get("session", df.get("window", pd.Series("", index=df.index))).astype(str).str.upper()
    if "hour" in df.columns:
        hour = pd.to_numeric(df["hour"], errors="coerce")
        session = session.mask(hour.eq(10), "10:00")
        session = session.mask(hour.eq(13), "13:30")
    elif "entry_time" in df.columns:
        ts = pd.to_datetime(df["entry_time"], errors="coerce")
        session = session.mask(ts.dt.hour.eq(10), "10:00")
        session = session.mask(ts.dt.hour.eq(13), "13:30")
    df["research_session"] = session

    fvg_disp = pd.Series(False, index=df.index)
    for col in ["fvg_displacement", "displacement"]:
        if col in df.columns:
            fvg_disp = fvg_disp | _boolish(df[col])
    df["research_fvg_displacement"] = fvg_disp

    ob = pd.Series(False, index=df.index)
    for col in ["ob_present", "order_block_present", "ob_confluence"]:
        if col in df.columns:
            ob = ob | _boolish(df[col])
    df["research_ob_present"] = ob

    # H1/H4 are not consistently present in historical labeled data. Keep
    # explicit columns so unavailable filters are reported honestly.
    for col in ["h1_bias", "h4_bias"]:
        if col not in df.columns:
            df[col] = np.nan
    df["research_h1_aligned"] = df["h1_bias"].astype(str).str.upper().eq(df["research_direction"])
    df["research_h4_aligned"] = df["h4_bias"].astype(str).str.upper().eq(df["research_direction"])
    df["research_has_h1h4"] = df["h1_bias"].notna() | df["h4_bias"].notna()

    return df[df["research_win"].notna() & df["research_r"].notna()].copy()


def _max_drawdown(r_values: Iterable[float]) -> float:
    vals = np.array(list(r_values), dtype=float)
    if len(vals) == 0:
        return 0.0
    curve = np.cumsum(vals)
    peak = np.maximum.accumulate(curve)
    return float(np.max(peak - curve))


def _profit_factor(r_values: pd.Series) -> float:
    gross_profit = float(r_values[r_values > 0].sum())
    gross_loss = abs(float(r_values[r_values < 0].sum()))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def confidence_level(n: int) -> str:
    if n >= 100:
        return "HIGH"
    if n >= 50:
        return "ACCEPTABLE"
    if n >= 30:
        return "LOW"
    return "LOW CONFIDENCE"


def rank_config(win_rate: float, profit_factor: float, avg_r: float, max_dd_r: float, n: int) -> str:
    dd_ok = max_dd_r <= max(8.0, n * 0.08)
    if win_rate >= 80 and profit_factor >= 2.25 and avg_r > 0 and dd_ok and n >= 50:
        return "A+"
    if win_rate >= 70 and profit_factor >= 2.0 and n >= 100:
        return "A"
    if win_rate >= 60 and profit_factor >= 1.5:
        return "B"
    return "REJECT"


def evaluate_subset(df: pd.DataFrame, name: str, market: str, filters: dict, r_col: str = "research_r") -> ExperimentResult:
    n = len(df)
    if n == 0:
        return ExperimentResult(name, market, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, "LOW CONFIDENCE", "REJECT", "No trades", filters)

    r = pd.to_numeric(df[r_col], errors="coerce").dropna()
    wins = int((r > 0).sum())
    losses = int((r <= 0).sum())
    wr = round(wins / len(r) * 100, 2) if len(r) else 0.0
    pf = round(_profit_factor(r), 4)
    avg_r = round(float(r.mean()), 4)
    total_r = round(float(r.sum()), 4)
    dd = round(_max_drawdown(r), 4)
    conf = confidence_level(len(r))
    rank = rank_config(wr, pf, avg_r, dd, len(r))
    if rank == "A+":
        rec = "Candidate for manual review; meets 80%+ WR and PF 2.25+ with acceptable sample."
    elif rank == "A":
        rec = "Strong filter; does not fully hit 80% WR target but has robust sample."
    elif rank == "B":
        rec = "Useful context filter; combine with stronger confirmation before live changes."
    else:
        rec = "Reject or keep as diagnostic only; does not meet edge threshold."

    return ExperimentResult(name, market, len(r), wins, losses, wr, pf, avg_r, total_r, dd, conf, rank, rec, filters)


def apply_exit_model(df: pd.DataFrame, model: str) -> pd.Series:
    """Approximate exit/SL/TP variants from logged R outcomes.

    This is not a candle-path simulator. It is a conservative research proxy
    until MFE/MAE and per-bar post-entry paths are stored.
    """
    base = pd.to_numeric(df["research_r"], errors="coerce").copy()
    model = model.lower()
    if model == "base":
        return base
    if model == "tp_1r":
        return base.where(base <= 0, base.clip(upper=1.0))
    if model == "tp_2r":
        return base.where(base <= 0, base.clip(upper=2.0))
    if model == "tp_3r":
        return base.where(base <= 0, base.clip(upper=3.0))
    if model == "partial_1r":
        winners = base.clip(lower=0)
        return base.where(base <= 0, 0.33 * winners.clip(upper=1.0) + 0.67 * winners)
    if model == "be_at_1r":
        # Approximation: trades that logged small losses may have been saved
        # after reaching +1R, but without MFE we cannot know. Keep losses as-is.
        return base
    if model == "trail_after_1_5r":
        winners = base.clip(lower=0)
        return base.where(base <= 0, winners.clip(upper=2.5))
    if model == "fvg_sl":
        return base * 0.9
    if model == "ob_sl":
        return base * 1.05
    if model == "structure_sl":
        return base
    if model == "fixed_sl":
        return base * 0.95
    return base


class StrategyOptimizer:
    def __init__(self, df: pd.DataFrame | None = None):
        self.df = df if df is not None else load_research_dataset()

    def _market_df(self, market: str) -> pd.DataFrame:
        if market.lower() == "combined":
            return self.df.copy()
        return self.df[self.df["engine"].str.lower() == market.lower()].copy()

    def run_single_filter_experiments(self, market: str) -> list[ExperimentResult]:
        df = self._market_df(market)
        experiments: list[tuple[str, Callable[[pd.DataFrame], pd.Series], dict]] = []

        for gate in [8, 10, 12, 14, 15]:
            experiments.append((f"score_gate_{gate}", lambda d, g=gate: d["research_score"] >= g, {"score_gate": gate}))
        experiments += [
            ("long_only", lambda d: d["research_direction"].eq("BULLISH"), {"direction": "long"}),
            ("short_only", lambda d: d["research_direction"].eq("BEARISH"), {"direction": "short"}),
            ("10_00_window", lambda d: d["research_session"].str.contains("10", na=False), {"session": "10:00"}),
            ("13_30_window", lambda d: d["research_session"].str.contains("13", na=False), {"session": "13:30"}),
            ("h4_aligned_only", lambda d: d["research_has_h1h4"] & d["research_h4_aligned"], {"h4": "aligned"}),
            ("h1_h4_aligned", lambda d: d["research_has_h1h4"] & d["research_h1_aligned"] & d["research_h4_aligned"], {"h1": "aligned", "h4": "aligned"}),
            ("ob_present", lambda d: d["research_ob_present"], {"ob": True}),
            ("no_ob_score_ge_15", lambda d: (~d["research_ob_present"]) & (d["research_score"] >= 15), {"ob": False, "score_gate": 15}),
            ("choch_present", lambda d: d["research_choch"], {"mss_type": "CHOCH"}),
            ("bos_present", lambda d: d["research_bos"], {"mss_type": "BOS"}),
            ("mss_present", lambda d: d["research_mss_present"], {"mss": True}),
            ("fvg_displacement_only", lambda d: d["research_fvg_displacement"], {"fvg_displacement": True}),
            ("no_displacement_fvg_skip", lambda d: d["research_fvg_displacement"], {"skip_no_displacement": True}),
            ("trending_only", lambda d: d["research_regime"].eq("TRENDING"), {"regime": "TRENDING"}),
            ("neutral_only", lambda d: d["research_regime"].eq("NEUTRAL"), {"regime": "NEUTRAL"}),
            ("choppy_skip", lambda d: ~d["research_regime"].eq("CHOPPY"), {"skip_regime": "CHOPPY"}),
            ("volatility_filter_proxy", lambda d: d["research_regime"].isin(["TRENDING", "NEUTRAL"]), {"volatility": "non_choppy_proxy"}),
            ("news_filter_unavailable", lambda d: pd.Series(False, index=d.index), {"news_filter": "unavailable"}),
        ]

        out: list[ExperimentResult] = []
        for name, fn, filters in experiments:
            mask = fn(df).fillna(False)
            out.append(evaluate_subset(df[mask], name, market.upper(), filters))
        return out

    def run_exit_model_experiments(self, market: str) -> list[ExperimentResult]:
        df = self._market_df(market)
        models = [
            "base", "tp_1r", "tp_2r", "tp_3r", "partial_1r", "be_at_1r",
            "trail_after_1_5r", "fixed_sl", "structure_sl", "fvg_sl", "ob_sl",
        ]
        out = []
        for model in models:
            tmp = df.copy()
            tmp["_model_r"] = apply_exit_model(tmp, model)
            out.append(evaluate_subset(tmp, f"exit_model_{model}", market.upper(), {"exit_model": model}, r_col="_model_r"))
        return out

    def run_combination_search(self, market: str) -> list[ExperimentResult]:
        df = self._market_df(market)
        results: list[ExperimentResult] = []
        score_gates = [8, 10, 12, 14, 15]
        directions = [None, "BULLISH", "BEARISH"]
        regimes = [None, "TRENDING", "NEUTRAL"]
        mss_types = [None, "CHOCH", "BOS"]
        ob_modes = [None, True, False]
        disp_modes = [None, True]

        for gate in score_gates:
            for direction in directions:
                for regime in regimes:
                    for mss in mss_types:
                        for ob_mode in ob_modes:
                            for disp in disp_modes:
                                mask = df["research_score"] >= gate
                                filters = {"score_gate": gate}
                                if direction:
                                    mask &= df["research_direction"].eq(direction)
                                    filters["direction"] = "long" if direction == "BULLISH" else "short"
                                if regime:
                                    mask &= df["research_regime"].eq(regime)
                                    filters["regime"] = regime
                                if mss:
                                    mask &= df["research_mss"].eq(mss)
                                    filters["mss_type"] = mss
                                if ob_mode is not None:
                                    mask &= df["research_ob_present"].eq(ob_mode)
                                    filters["ob_present"] = ob_mode
                                if disp is not None:
                                    mask &= df["research_fvg_displacement"].eq(disp)
                                    filters["fvg_displacement"] = disp

                                subset = df[mask.fillna(False)]
                                if len(subset) >= 10:
                                    name = "combo_" + "_".join(f"{k}={v}" for k, v in filters.items())
                                    results.append(evaluate_subset(subset, name, market.upper(), filters))

        return sorted(
            results,
            key=lambda r: (
                {"A+": 4, "A": 3, "B": 2, "REJECT": 1}.get(r.rank, 0),
                r.profit_factor,
                r.win_rate,
                r.n,
            ),
            reverse=True,
        )

    def run_all(self) -> dict[str, list[ExperimentResult]]:
        output = {}
        for market in ["nse", "forex", "combined"]:
            output[market] = (
                self.run_single_filter_experiments(market)
                + self.run_exit_model_experiments(market)
                + self.run_combination_search(market)
            )
        return output
