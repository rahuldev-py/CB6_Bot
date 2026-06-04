"""
ml_engine/reports/ml_report_generator.py

MLReportGenerator: generates a full ML status report.

Covers:
    - Model registry status for all slots
    - Training metrics summary (DNN + LSTM + CNN)
    - Live shadow stats (accuracy, AUC, bucket perf)
    - Drift detector status
    - Step 11 activation gate checklist
    - Last N shadow predictions

Usage:
    from ml_engine.reports.ml_report_generator import MLReportGenerator
    gen = MLReportGenerator()
    report = gen.generate()     # returns str
    gen.save(report)            # saves to ml_engine/reports/latest_report.txt
    gen.send_telegram(report)   # optional — uses bot config
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

REGISTRY_PATH = Path("ml_engine/config/model_registry.json")
REPORT_DIR    = Path("ml_engine/reports")


class MLReportGenerator:

    def __init__(self, engines: list[str] = None):
        self.engines = engines or ["nse", "forex"]

    def generate(self, include_predictions: int = 5) -> str:
        """
        Generate full ML status report as a string.

        Parameters
        ----------
        include_predictions : number of recent shadow predictions to include
        """
        lines = [
            "",
            "=" * 60,
            "  CB6 QUANTUM -- ML ENGINE STATUS REPORT",
            f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
        ]

        # ── Registry overview ─────────────────────────────────────────────
        lines += ["", "  MODEL REGISTRY", "  " + "-" * 40]
        try:
            with open(REGISTRY_PATH) as f:
                registry = json.load(f)
            for key, model in registry.get("models", {}).items():
                status   = model.get("status", "UNKNOWN")
                n_vers   = len(model.get("versions", []))
                active   = model.get("active_version", "--")
                gate_ok  = model.get("activation_gate_passed", False)
                lines.append(
                    f"  {key:<35} {status:<12} "
                    f"versions={n_vers} gate={'PASS' if gate_ok else 'NO'}"
                )
        except Exception as e:
            lines.append(f"  [ERROR loading registry: {e}]")

        # ── Per-engine scorecards ─────────────────────────────────────────
        for engine in self.engines:
            try:
                from ml_engine.reports.model_scorecard import ModelScorecard
                card = ModelScorecard(engine=engine)
                lines += ["", card.render()]
            except Exception as e:
                lines += ["", f"  [{engine.upper()} scorecard error: {e}]"]

        # ── Drift status ──────────────────────────────────────────────────
        lines += ["", "  DRIFT STATUS", "  " + "-" * 40]
        for engine in self.engines:
            try:
                from ml_engine.monitoring.drift_detector import DriftDetector
                det = DriftDetector(engine=engine)
                lines.append(f"  {det.summary()}")
            except Exception as e:
                lines.append(f"  [{engine.upper()} drift check error: {e}]")

        # ── Step 11 activation gate checklist ─────────────────────────────
        lines += ["", "  STEP 11 ACTIVATION GATE CHECKLIST", "  " + "-" * 40]
        lines += self._gate_checklist()

        # ── Recent shadow predictions ─────────────────────────────────────
        if include_predictions > 0:
            lines += ["", f"  LAST {include_predictions} SHADOW PREDICTIONS", "  " + "-" * 40]
            lines += self._recent_predictions(include_predictions)

        lines += ["", "=" * 60, ""]
        return "\n".join(lines)

    def _gate_checklist(self) -> list[str]:
        """Build Step 11 activation gate checklist."""
        lines = []

        for engine in self.engines:
            lines.append(f"  [{engine.upper()}]")

            # Check 1: 500+ historical trades in dataset
            try:
                from ml_engine.training.dataset_builder import build_dataset
                df = build_dataset(base_path="")
                n_labeled = 0
                if df is not None and not df.empty:
                    if "engine" in df.columns:
                        sub = df[df["engine"] == engine]
                    else:
                        sub = df
                    n_labeled = int(sub["win_loss_label"].notna().sum())
                ok = n_labeled >= 500
                lines.append(f"    {'[OK]' if ok else '[NO]'} 500+ labeled trades: {n_labeled}")
            except Exception as e:
                lines.append(f"    [??] Dataset check failed: {e}")

            # Check 2: 100+ shadow predictions audited
            try:
                from ml_engine.monitoring.performance_tracker import PerformanceTracker
                tracker = PerformanceTracker(engine=engine)
                stats = tracker.compute()
                n_aud = stats.get("n_audited", 0)
                ok2   = n_aud >= 100
                lines.append(f"    {'[OK]' if ok2 else '[NO]'} 100+ audited shadow predictions: {n_aud}")
            except Exception as e:
                lines.append(f"    [??] Shadow audit check: {e}")

            # Check 3: A+ > A > B > C (live monotonic)
            try:
                tracker = PerformanceTracker(engine=engine)
                mono = tracker.bucket_monotonic()
                lines.append(f"    {'[OK]' if mono else '[NO]'} Live monotonic win-rate (A+>A>B>C): {mono}")
            except Exception as e:
                lines.append(f"    [??] Monotonic check: {e}")

            # Check 4: Training AUC >= 0.55
            try:
                with open(REGISTRY_PATH) as f:
                    registry = json.load(f)
                best_auc = 0.0
                for key in [f"dnn_trade_scorer_{engine}", f"rnn_trade_scorer_{engine}"]:
                    versions = registry.get("models", {}).get(key, {}).get("versions", [])
                    if versions:
                        best_auc = max(best_auc, float(versions[-1].get("auc") or 0))
                ok4 = best_auc >= 0.55
                lines.append(f"    {'[OK]' if ok4 else '[NO]'} Training AUC >= 0.55: {best_auc:.4f}")
            except Exception as e:
                lines.append(f"    [??] AUC check: {e}")

            # Check 5: 14 days no crash
            lines.append(f"    [MAN] 14 days no crash: MANUAL CHECK REQUIRED")

            lines.append("")

        return lines

    def _recent_predictions(self, n: int) -> list[str]:
        """Return last N shadow predictions as formatted lines."""
        from ml_engine.monitoring.ml_logger import MLLogger
        lines = []
        try:
            shadow_log = Path("ml_engine/logs/shadow_predictions.jsonl")
            if not shadow_log.exists():
                return ["  No shadow predictions yet"]
            with open(shadow_log, encoding="utf-8") as f:
                raw = f.readlines()
            last_n = [json.loads(l.strip()) for l in raw[-n:] if l.strip()]
            for p in last_n:
                ts    = str(p.get("ts", ""))[:19]
                sym   = p.get("symbol", "?")
                dir_  = p.get("direction", "?")[:4]
                wp    = p.get("win_probability", 0.5)
                bkt   = p.get("final_bucket", "C")
                eng   = p.get("engine", "?")
                out   = p.get("actual_outcome")
                out_s = f" => {'WIN' if out else 'LOSS'}" if out is not None else " => pending"
                lines.append(f"  {ts} [{eng}] {sym} {dir_} wp={wp:.3f} bucket={bkt}{out_s}")
        except Exception as e:
            lines.append(f"  [Error reading predictions: {e}]")
        return lines

    def save(self, report: Optional[str] = None) -> Path:
        """Save report to ml_engine/reports/latest_report.txt and timestamped copy."""
        if report is None:
            report = self.generate()

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        latest = REPORT_DIR / "latest_report.txt"
        ts_name = REPORT_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

        for path in [latest, ts_name]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)

        return latest

    def send_telegram(self, report: Optional[str] = None, max_chars: int = 3000) -> bool:
        """
        Send report summary to Telegram via bot config.
        Truncates to max_chars to fit Telegram message limits.
        Returns True if sent successfully.
        """
        if report is None:
            report = self.generate(include_predictions=3)

        # Trim to fit Telegram
        if len(report) > max_chars:
            report = report[:max_chars - 50] + "\n\n[... truncated — see latest_report.txt]"

        try:
            # Look for Telegram config in environment / .env
            import os
            token   = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")

            if not token or not chat_id:
                # Try loading from .env
                env_path = Path(".env")
                if env_path.exists():
                    for line in env_path.read_text().splitlines():
                        if "=" in line and not line.startswith("#"):
                            k, _, v = line.partition("=")
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k in ("TELEGRAM_BOT_TOKEN", "BOT_TOKEN") and not token:
                                token = v
                            if k == "TELEGRAM_CHAT_ID" and not chat_id:
                                chat_id = v

            if not token or not chat_id:
                return False

            import urllib.request
            url  = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({
                "chat_id"   : chat_id,
                "text"      : f"```\n{report}\n```",
                "parse_mode": "Markdown",
            }).encode()
            req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200

        except Exception:
            return False
