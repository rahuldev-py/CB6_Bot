"""
ml_engine/monitoring/prediction_audit.py

PredictionAuditor: matches shadow predictions to actual trade outcomes.

Reads:
    ml_engine/logs/shadow_predictions.jsonl    (shadow log from ShadowPredictor)
    data/paper_state.json / data/forex_paper_state.json  (closed trades)
    data/trade_journal.csv / data/forex_journal.csv       (manual journal)

Matching strategy:
    1. Match by trade_id if present
    2. Else match by symbol + direction + timestamp proximity (within 30 min)

Writes:
    Updates shadow_predictions.jsonl in-place (rewrites with filled actual_outcome)
    Appends audit events to ml_events.jsonl via MLLogger

Usage:
    from ml_engine.monitoring.prediction_audit import PredictionAuditor
    auditor = PredictionAuditor()
    stats = auditor.run()
    print(stats)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("cb6.ml.prediction_audit")

SHADOW_LOG  = Path("ml_engine/logs/shadow_predictions.jsonl")
MATCH_WINDOW = timedelta(minutes=30)


class PredictionAuditor:
    """
    Fills in actual_outcome / actual_r for shadow predictions that have closed trades.
    Call .run() periodically (e.g. after each trading session).
    """

    def __init__(self, base_path: str = ""):
        self.base  = Path(base_path) if base_path else Path(".")
        self._trades: pd.DataFrame = self._load_all_trades()

    def _load_all_trades(self) -> pd.DataFrame:
        """Load all closed trades from paper state + journals."""
        frames = []

        # NSE paper state
        for fname in ["data/paper_state.json", "paper_state.json"]:
            p = self.base / fname
            if p.exists():
                try:
                    with open(p) as f:
                        state = json.load(f)
                    trades = state.get("closed_trades", []) or state.get("trades", [])
                    if trades:
                        df = pd.DataFrame(trades)
                        df["_source"] = "paper_state"
                        df["_engine"] = "nse"
                        frames.append(df)
                except Exception as e:
                    logger.warning(f"Could not load {fname}: {e}")

        # Forex paper state
        for fname in ["data/forex_paper_state.json", "forex_paper_state.json"]:
            p = self.base / fname
            if p.exists():
                try:
                    with open(p) as f:
                        state = json.load(f)
                    trades = state.get("closed_trades", []) or state.get("trades", [])
                    if trades:
                        df = pd.DataFrame(trades)
                        df["_source"] = "forex_paper_state"
                        df["_engine"] = "forex"
                        frames.append(df)
                except Exception as e:
                    logger.warning(f"Could not load {fname}: {e}")

        # Journals
        for fname, engine in [("data/trade_journal.csv", "nse"), ("data/forex_journal.csv", "forex")]:
            p = self.base / fname
            if p.exists():
                try:
                    df = pd.read_csv(p)
                    df["_source"] = "journal"
                    df["_engine"] = engine
                    frames.append(df)
                except Exception as e:
                    logger.warning(f"Could not load {fname}: {e}")

        if not frames:
            logger.info("No closed trade data found for auditing")
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)

        # Normalise timestamp column
        for col in ["exit_time", "entry_time", "timestamp", "date"]:
            if col in combined.columns:
                combined["_ts"] = pd.to_datetime(combined[col], errors="coerce")
                break
        if "_ts" not in combined.columns:
            combined["_ts"] = pd.NaT

        # Normalise win/loss
        for col in ["win_loss_label", "result", "outcome"]:
            if col in combined.columns:
                combined["_win"] = pd.to_numeric(combined[col], errors="coerce")
                break
        if "_win" not in combined.columns:
            combined["_win"] = float("nan")

        # Map win/loss strings
        if combined["_win"].isna().all() and "result" in combined.columns:
            combined["_win"] = combined["result"].map(
                {"WIN": 1, "LOSS": 0, "win": 1, "loss": 0, "W": 1, "L": 0}
            )

        # R-multiple
        for col in ["r_multiple_label", "r_multiple", "r_mult"]:
            if col in combined.columns:
                combined["_r"] = pd.to_numeric(combined[col], errors="coerce")
                break
        if "_r" not in combined.columns:
            combined["_r"] = float("nan")

        logger.info(f"Loaded {len(combined)} closed trades for audit matching")
        return combined

    def _find_match(self, pred: dict) -> Optional[dict]:
        """Find a closed trade matching this shadow prediction."""
        if self._trades.empty:
            return None

        engine = pred.get("engine", "nse")
        symbol = str(pred.get("symbol", "")).upper()
        direction = str(pred.get("direction", "")).upper()
        trade_id = pred.get("trade_id")

        subset = self._trades

        # Engine filter
        if "_engine" in subset.columns and engine:
            subset = subset[subset["_engine"] == engine]

        # Match by trade_id first
        if trade_id and "trade_id" in subset.columns:
            match = subset[subset["trade_id"].astype(str) == str(trade_id)]
            if len(match) == 1:
                row = match.iloc[0]
                return {"win": row["_win"], "r": row["_r"]}

        # Match by symbol + direction + time proximity
        pred_ts = pd.to_datetime(pred.get("ts"), errors="coerce")
        if pd.isna(pred_ts):
            return None

        # Symbol filter
        if "symbol" in subset.columns and symbol:
            sym_mask = subset["symbol"].astype(str).str.upper().str.contains(
                symbol.split("_")[0][:6], na=False
            )
            subset = subset[sym_mask]

        # Direction filter
        if "direction" in subset.columns and direction:
            dir_mask = subset["direction"].astype(str).str.upper().str.contains(
                direction[:4], na=False
            )
            subset = subset[dir_mask]

        # Time proximity
        if "_ts" in subset.columns and not subset.empty:
            time_diff = (subset["_ts"] - pred_ts).abs()
            close = subset[time_diff <= MATCH_WINDOW]
            if len(close) == 1:
                row = close.iloc[0]
                return {"win": row["_win"], "r": row["_r"]}
            if len(close) > 1:
                # Take closest
                row = close.loc[time_diff[close.index].idxmin()]
                return {"win": row["_win"], "r": row["_r"]}

        return None

    def run(self, dry_run: bool = False) -> dict:
        """
        Match all unaudited shadow predictions to closed trades.

        Parameters
        ----------
        dry_run : if True, log matches but don't rewrite shadow_predictions.jsonl

        Returns
        -------
        dict with: total, audited, matched, unmatched, accuracy stats
        """
        from ml_engine.monitoring.ml_logger import MLLogger

        if not SHADOW_LOG.exists():
            return {"total": 0, "audited": 0, "matched": 0, "message": "no shadow log"}

        with open(SHADOW_LOG, encoding="utf-8") as f:
            lines = f.readlines()

        predictions = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    predictions.append(json.loads(line))
                except Exception:
                    pass

        total     = len(predictions)
        matched   = 0
        unchanged = 0
        updated   = []

        for pred in predictions:
            # Already audited
            if pred.get("actual_outcome") is not None:
                updated.append(pred)
                unchanged += 1
                continue

            result = self._find_match(pred)
            if result is None:
                updated.append(pred)
                continue

            win_val = result["win"]
            r_val   = result["r"]

            if pd.isna(win_val):
                updated.append(pred)
                continue

            pred["actual_outcome"] = int(win_val)
            pred["actual_r"]       = float(r_val) if not pd.isna(r_val) else None
            pred["audited_at"]     = datetime.now().isoformat()
            updated.append(pred)
            matched += 1

            MLLogger.log_audit(
                engine=pred.get("engine", "nse"),
                prediction_id=pred.get("trade_id") or pred.get("ts", ""),
                predicted_bucket=pred.get("final_bucket", "C"),
                actual_win=int(win_val),
                actual_r=float(r_val) if not pd.isna(r_val) else None,
                win_prob=float(pred.get("win_probability", 0.5)),
            )

        if not dry_run and matched > 0:
            with open(SHADOW_LOG, "w", encoding="utf-8") as f:
                for p in updated:
                    f.write(json.dumps(p, default=str) + "\n")
            logger.info(f"Audit complete: {matched} predictions matched to outcomes")

        # Compute accuracy on audited predictions
        audited = [p for p in updated if p.get("actual_outcome") is not None]
        accuracy = None
        if audited:
            correct = sum(
                int((p["win_probability"] >= 0.5) == bool(p["actual_outcome"]))
                for p in audited
            )
            accuracy = round(correct / len(audited), 4)

        return {
            "total"    : total,
            "audited"  : len(audited),
            "matched"  : matched,
            "unmatched": total - len(audited),
            "accuracy" : accuracy,
            "dry_run"  : dry_run,
        }
