"""
ml_engine/gate/activation_gate.py

CB6 Quantum ML Activation Gate — Step 11 of the ML build plan.

Evaluates ALL conditions that must pass before ML is promoted from
shadow-only to active observation mode.

Gate conditions (ALL must pass per engine):
    G1  500+ labeled training rows in dataset
    G2  Training AUC >= 0.55 (best model: DNN or LSTM)
    G3  Training monotonic win-rate: A+ > A > B > C
    G4  All A+/A/B buckets positive expectancy in training evaluation
    G5  100+ shadow predictions with known outcomes (audited)
    G6  Live accuracy >= 50% over audited window
    G7  Live bucket monotonicity confirmed (A+ > A > B > C in real trades)
    G8  Zero drift alerts at CRITICAL level in last 50 predictions
    G9  Model file exists and loads without error
    G10 14 consecutive trading days without ML subsystem crash (manual)

Important:
    - This gate NEVER enables live trading. It promotes shadow mode only.
    - Passing this gate means: ML predictions are shown in reports and
      the dashboard. They still never affect SL, TP, lots, or entry.
    - ML_CAN_TRADE, ML_CAN_MODIFY_RISK, ML_CAN_BLOCK_TRADES remain FALSE.

Usage:
    python -m ml_engine.gate.activation_gate              # evaluate both engines
    python -m ml_engine.gate.activation_gate --engine nse # one engine
    python -m ml_engine.gate.activation_gate --activate nse  # activate if PASS
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cb6.ml.gate")

REGISTRY_PATH = Path("ml_engine/config/model_registry.json")
CONFIG_PATH   = Path("ml_engine/config/ml_config.json")
GATE_LOG      = Path("ml_engine/logs/gate_checks.jsonl")

# Minimum thresholds
MIN_TRAIN_ROWS   = 500
MIN_TRAIN_AUC    = 0.55
MIN_SHADOW_AUDITED = 100
MIN_LIVE_ACCURACY  = 0.50


# ── Individual check functions ─────────────────────────────────────────────────

def _check_g1_dataset_size(engine: str) -> tuple[bool, str]:
    """G1: 500+ labeled training rows."""
    try:
        from ml_engine.training.dataset_builder import build_dataset
        df = build_dataset(base_path="")
        if df is None or df.empty:
            return False, "Dataset empty"
        if "engine" in df.columns:
            sub = df[df["engine"] == engine]
        else:
            sub = df
        n = int(sub["win_loss_label"].notna().sum()) if "win_loss_label" in sub.columns else 0
        ok = n >= MIN_TRAIN_ROWS
        return ok, f"{n} labeled rows (need {MIN_TRAIN_ROWS}+)"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g2_training_auc(engine: str) -> tuple[bool, str]:
    """G2: Best training AUC >= 0.55 across any trained model for this engine."""
    try:
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)
        best_auc = 0.0
        best_ver = None
        # Check all registry keys that contain the engine name (covers dnn_, rnn_, rnn_sequence_)
        for key, model in registry.get("models", {}).items():
            if engine not in key or "cnn" in key:
                continue
            for v in model.get("versions", []):
                auc = float(v.get("auc") or 0)
                if auc > best_auc:
                    best_auc = auc
                    best_ver = v.get("version_id")
        ok = best_auc >= MIN_TRAIN_AUC
        return ok, f"AUC={best_auc:.4f} (need {MIN_TRAIN_AUC}+) [{best_ver}]"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g3_monotonic_wr(engine: str) -> tuple[bool, str]:
    """G3: Training monotonic win-rate A+ > A > B > C."""
    try:
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)
        for key in [f"dnn_trade_scorer_{engine}", f"rnn_trade_scorer_{engine}"]:
            versions = registry.get("models", {}).get(key, {}).get("versions", [])
            if versions:
                # Check if any version had monotonic WR (stored in train result)
                # We re-read from training logs if available
                pass
        # Try reading from ml_events
        from ml_engine.monitoring.ml_logger import MLLogger
        train_events = MLLogger.tail(20, event_type="model_trained")
        eng_events = [e for e in train_events if e.get("engine") == engine]
        if eng_events:
            latest = eng_events[-1]
            mono = latest.get("monotonic_wr", False)
            return bool(mono), f"monotonic_wr={mono} (from last training run)"
        return False, "No training event found in logs"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g4_positive_expectancy(engine: str) -> tuple[bool, str]:
    """G4: All A+/A/B buckets positive expectancy in training."""
    try:
        from ml_engine.monitoring.ml_logger import MLLogger
        train_events = MLLogger.tail(20, event_type="model_trained")
        eng_events = [e for e in train_events if e.get("engine") == engine]
        if eng_events:
            latest = eng_events[-1]
            pos_exp = latest.get("all_pos_expectancy", False)
            return bool(pos_exp), f"all_positive_expectancy={pos_exp}"
        return False, "No training event found in logs"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g5_shadow_audited(engine: str) -> tuple[bool, str]:
    """G5: 100+ shadow predictions with known outcomes."""
    try:
        from ml_engine.monitoring.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker(engine=engine)
        stats   = tracker.compute()
        n_aud   = stats.get("n_audited", 0)
        n_total = stats.get("n_total", 0)
        ok = n_aud >= MIN_SHADOW_AUDITED
        return ok, f"{n_aud} audited / {n_total} total (need {MIN_SHADOW_AUDITED}+)"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g6_live_accuracy(engine: str) -> tuple[bool, str]:
    """G6: Live accuracy >= 50% over audited window."""
    try:
        from ml_engine.monitoring.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker(engine=engine)
        stats   = tracker.compute()
        acc     = stats.get("accuracy")
        if acc is None:
            return False, "No audited predictions yet"
        ok = acc >= MIN_LIVE_ACCURACY
        return ok, f"live accuracy={acc:.1%} (need {MIN_LIVE_ACCURACY:.0%}+)"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g7_live_monotonic(engine: str) -> tuple[bool, str]:
    """G7: Live bucket monotonicity A+ > A > B > C."""
    try:
        from ml_engine.monitoring.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker(engine=engine)
        stats   = tracker.compute()
        if stats.get("n_audited", 0) < 20:
            return False, f"Only {stats.get('n_audited', 0)} audited predictions — need 20+ for live monotonic check"
        mono = tracker.bucket_monotonic()
        return mono, f"live monotonic={mono}"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g8_no_critical_drift(engine: str) -> tuple[bool, str]:
    """G8: No CRITICAL drift alerts in last 50 predictions."""
    try:
        from ml_engine.monitoring.drift_detector import DriftDetector
        det    = DriftDetector(engine=engine, window=50)
        alerts = det.check()
        crits  = [a for a in alerts if a.get("severity") == "critical"]
        ok     = len(crits) == 0
        if ok:
            return True, "No critical drift detected"
        msg = ", ".join(a.get("type", "?") for a in crits)
        return False, f"CRITICAL drift: {msg}"
    except Exception as e:
        return False, f"Error: {e}"


def _check_g9_model_loads(engine: str) -> tuple[bool, str]:
    """G9: Best model file exists and loads without error."""
    try:
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)
        candidate_keys = [k for k in registry.get("models", {}) if engine in k and "cnn" not in k]
        for key in candidate_keys:
            versions = registry.get("models", {}).get(key, {}).get("versions", [])
            for v in reversed(versions):
                p = v.get("model_path", "")
                if p and Path(p).exists():
                    # Try loading
                    if "dnn" in key:
                        from ml_engine.models.dnn_trade_scorer import DNNTradeScorer
                        m = DNNTradeScorer.load(Path(p))
                        return True, f"DNN loaded: {Path(p).name}"
                    else:
                        from ml_engine.models.rnn_sequence_model import RNNTradeScorer
                        m = RNNTradeScorer.load(Path(p))
                        return True, f"LSTM loaded: {Path(p).name}"
        return False, "No saved model file found"
    except Exception as e:
        return False, f"Load error: {e}"


def _check_g10_manual(engine: str) -> tuple[bool, str]:
    """G10: 14 days no crash — manual confirmation only."""
    gate_log = GATE_LOG
    if gate_log.exists():
        try:
            with open(gate_log) as f:
                lines = f.readlines()
            confirmed = [
                json.loads(l) for l in lines
                if l.strip() and
                json.loads(l).get("check") == "g10_manual" and
                json.loads(l).get("engine") == engine and
                json.loads(l).get("confirmed") is True
            ]
            if confirmed:
                latest = confirmed[-1]
                ts = latest.get("confirmed_at", "?")
                return True, f"Manually confirmed at {ts}"
        except Exception:
            pass
    return False, "Not confirmed — run: python -m ml_engine.gate.activation_gate --confirm-g10 <engine>"


# ── Gate evaluator ─────────────────────────────────────────────────────────────

CHECKS = [
    ("G1",  "500+ labeled training rows",         _check_g1_dataset_size),
    ("G2",  "Training AUC >= 0.55",               _check_g2_training_auc),
    ("G3",  "Training monotonic win-rate",         _check_g3_monotonic_wr),
    ("G4",  "Positive expectancy all buckets",     _check_g4_positive_expectancy),
    ("G5",  "100+ shadow predictions audited",     _check_g5_shadow_audited),
    ("G6",  "Live accuracy >= 50%",                _check_g6_live_accuracy),
    ("G7",  "Live monotonic win-rate (A+>A>B>C)",  _check_g7_live_monotonic),
    ("G8",  "No critical drift alerts",            _check_g8_no_critical_drift),
    ("G9",  "Model file loads without error",      _check_g9_model_loads),
    ("G10", "14 days no crash (manual)",           _check_g10_manual),
]


def evaluate(engine: str, verbose: bool = True) -> dict:
    """
    Evaluate all gate conditions for one engine.

    Returns
    -------
    dict:
        engine        str
        passed        bool   (ALL checks passed)
        results       list of {check, label, ok, detail}
        pass_count    int
        total         int
        evaluated_at  str
    """
    results = []
    for gid, label, fn in CHECKS:
        try:
            ok, detail = fn(engine)
        except Exception as e:
            ok, detail = False, f"Exception: {e}"
        results.append({"check": gid, "label": label, "ok": ok, "detail": detail})

    pass_count = sum(1 for r in results if r["ok"])
    all_passed = pass_count == len(CHECKS)

    if verbose:
        _print_results(engine, results, all_passed)

    outcome = {
        "engine"      : engine,
        "passed"      : all_passed,
        "results"     : results,
        "pass_count"  : pass_count,
        "total"       : len(CHECKS),
        "evaluated_at": datetime.now().isoformat(),
    }

    # Log gate check
    try:
        from ml_engine.monitoring.ml_logger import MLLogger
        MLLogger.log_gate(
            engine=engine,
            model_type="gate_evaluation",
            passed=all_passed,
            reason=f"{pass_count}/{len(CHECKS)} checks passed",
            metrics={},
        )
    except Exception:
        pass

    return outcome


def _print_results(engine: str, results: list[dict], all_passed: bool) -> None:
    width = 58
    print(f"\n{'='*width}")
    print(f"  CB6 QUANTUM -- ML ACTIVATION GATE ({engine.upper()})")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*width}")
    for r in results:
        icon = "[PASS]" if r["ok"] else "[FAIL]"
        print(f"  {icon} {r['check']:<4} {r['label']}")
        print(f"         {r['detail']}")
    print(f"{'='*width}")
    if all_passed:
        print(f"  GATE STATUS: ALL CHECKS PASS")
        print(f"")
        print(f"  ML engine is READY for active shadow mode.")
        print(f"  To activate, run:")
        print(f"    python -m ml_engine.gate.activation_gate --activate {engine}")
        print(f"")
        print(f"  NOTE: This enables shadow reporting only.")
        print(f"        ML_CAN_TRADE / ML_CAN_MODIFY_RISK remain FALSE.")
    else:
        failed = [r for r in results if not r["ok"]]
        print(f"  GATE STATUS: {len(failed)} CHECK(S) FAILING")
        print(f"")
        print(f"  What to fix:")
        for r in failed:
            print(f"    {r['check']}: {r['detail']}")
        print(f"")
        print(f"  Run this check again after addressing the failing items.")
    print(f"{'='*width}\n")


def activate(engine: str) -> bool:
    """
    Activate ML shadow mode for one engine if gate passes.
    Only sets ml_enabled=True — never enables trading flags.
    """
    outcome = evaluate(engine, verbose=True)
    if not outcome["passed"]:
        print(f"\nACTIVATION BLOCKED: gate has {outcome['total'] - outcome['pass_count']} failing check(s).")
        return False

    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)

        # ONLY these flags are touched — nothing execution-related
        cfg["ml_enabled"]     = True
        cfg["ml_shadow_mode"] = True

        # Hard-wired safety — never changed by this function
        cfg["ml_can_trade"]        = False
        cfg["ml_can_modify_risk"]  = False
        cfg["ml_can_block_trades"] = False
        cfg["ml_can_close_trades"] = False

        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)

        _log_activation(engine, outcome)

        print(f"\nACTIVATED: ml_enabled=True for engine={engine}")
        print(f"Shadow predictions will now be logged automatically.")
        print(f"")
        print(f"Run reports with:")
        print(f"  python -m ml_engine.reports.ml_report_generator")
        print(f"")
        print(f"REMINDER: ml_can_trade=False / ml_can_modify_risk=False")
        return True

    except Exception as e:
        print(f"\nActivation error: {e}")
        return False


def confirm_g10(engine: str) -> None:
    """Manually confirm G10 (14 days no crash) for one engine."""
    entry = {
        "check"       : "g10_manual",
        "engine"      : engine,
        "confirmed"   : True,
        "confirmed_at": datetime.now().isoformat(),
        "confirmed_by": "manual",
    }
    GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(GATE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"G10 confirmed for engine={engine} at {entry['confirmed_at']}")


def _log_activation(engine: str, outcome: dict) -> None:
    entry = {
        "event"       : "activation",
        "engine"      : engine,
        "activated_at": datetime.now().isoformat(),
        "pass_count"  : outcome["pass_count"],
        "total"       : outcome["total"],
    }
    GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(GATE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="CB6 ML Activation Gate")
    parser.add_argument("--engine", default="all", help="nse | forex | all")
    parser.add_argument("--activate", metavar="ENGINE", help="Activate ML for engine if gate passes")
    parser.add_argument("--confirm-g10", metavar="ENGINE", dest="confirm_g10",
                        help="Manually confirm G10 (14 days no crash) for engine")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    if args.confirm_g10:
        confirm_g10(args.confirm_g10)
        return

    if args.activate:
        sys.exit(0 if activate(args.activate) else 1)

    engines = ["nse", "forex"] if args.engine == "all" else [args.engine]
    all_results = {}

    for eng in engines:
        outcome = evaluate(eng, verbose=not args.json)
        all_results[eng] = outcome

    if args.json:
        print(json.dumps(all_results, indent=2, default=str))

    # Exit code: 0 if all engines pass, 1 otherwise
    all_pass = all(r["passed"] for r in all_results.values())
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
