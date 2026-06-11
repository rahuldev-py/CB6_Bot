"""
SHADOW — CB6 ML Engineer
Reads real ML model metrics. Compares old vs new. Produces deployment recommendation.
Shadow mode ONLY — predictions logged, never touches live orders.

Deployment status options:
  KEEP OLD MODEL | SHADOW TEST NEW MODEL | PAPER TEST NEW MODEL | REQUIRES HUMAN REVIEW | READY FOR APPROVAL
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from agents.config import call_agent, safe_parse, REPORTS_DIR, CB6_ROOT

SYSTEM = """You are SHADOW, the ML Engineer of CB6 Quantum.
You compare old ML model vs new ML model and give a deployment recommendation.
Models are shadow mode ONLY — they NEVER place or block live orders.

Deployment status options (use exactly one):
- KEEP OLD MODEL: new model is worse or insufficient improvement
- SHADOW TEST NEW MODEL: new model shows improvement, test in shadow first
- PAPER TEST NEW MODEL: shadow test complete, ready for paper test
- REQUIRES HUMAN REVIEW: conflicting signals or uncertain results
- READY FOR APPROVAL: paper test complete, ready for Rahul to approve live

Target metrics: test_acc > 0.72, test_prec > 0.85, val_loss < 0.60
Overfitting warning: val_loss > 0.65 or test_acc - train_acc > 0.10

Return JSON only:
{
  "retrain_decision": "YES or NO",
  "markets_to_retrain": [],
  "models_to_retrain": [],
  "reason": "specific reason with numbers",
  "old_model_metrics": {},
  "new_model_metrics": {},
  "improvement": {},
  "overfitting_risk": "LOW/MEDIUM/HIGH",
  "deployment_status": "one of the 5 options above",
  "weak_models": [],
  "strong_models": [],
  "feature_improvements": [],
  "current_model_health": {},
  "training_triggered": false,
  "days_since_training": {},
  "shadow_status": "ACTIVE",
  "specific_improvements": [],
  "notes": ""
}"""


def _load_all_metrics() -> dict:
    registry_path = CB6_ROOT / 'ml_engine' / 'config' / 'model_registry.json'
    if not registry_path.exists():
        return {}
    try:
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    metrics = {}
    for model_id, m in registry.get('models', {}).items():
        if m.get('research_only') or m.get('status') in ('RESEARCH', 'NOT_TRAINED'):
            continue
        versions = m.get('versions', [])
        if not versions:
            continue
        latest = versions[-1]
        trained_at = latest.get('trained_at', '')
        days_old = None
        if trained_at and len(trained_at) >= 8:
            try:
                dt = datetime.strptime(trained_at[:8], '%Y%m%d')
                days_old = (datetime.now() - dt).days
            except Exception:
                pass
        acc      = latest.get('accuracy', 0)
        f1       = latest.get('f1', 0)
        auc      = latest.get('auc', 0)
        brier    = latest.get('brier_score', 1.0)
        needs_retrain = (days_old is not None and days_old >= 7) or acc < 0.60
        healthy       = acc >= 0.60 and f1 >= 0.65 and auc >= 0.50
        metrics[model_id] = {
            "trained_at":            trained_at,
            "test_acc":              acc,
            "test_prec":             f1,
            "val_loss":              brier,
            "auc":                   auc,
            "days_old":              days_old,
            "needs_retrain":         needs_retrain,
            "healthy":               healthy,
            "activation_gate_passed": m.get('activation_gate_passed', False),
            "engine":                m.get('engine', ''),
            "grade":                 "A" if acc >= 0.70 else "B" if acc >= 0.60 else "C" if acc >= 0.55 else "D",
        }
    return metrics


def _load_trade_counts() -> dict:
    counts = {}
    for name, path in [('ftmo', 'data/ftmo_10k/state.json'),
                        ('gft_5k', 'data/gft_5k/state.json'),
                        ('gft_1k', 'data/gft_1k_instant/state.json')]:
        p = CB6_ROOT / path
        if p.exists():
            try:
                s = json.loads(p.read_text(encoding='utf-8'))
                counts[name] = len(s.get('closed_trades', []))
            except Exception:
                counts[name] = 0
    return counts


def _trigger_retrain(markets: list) -> list:
    triggered = []
    for candidate in CB6_ROOT.rglob('train*.py'):
        if 'ml' in str(candidate).lower() and candidate.stat().st_size > 100:
            try:
                subprocess.Popen(
                    [sys.executable, str(candidate)],
                    cwd=str(CB6_ROOT),
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
                triggered.append(candidate.name)
            except Exception as e:
                print(f"[SHADOW] Trigger failed {candidate.name}: {e}")
    return triggered


def run(quant_report: dict = None) -> dict:
    if quant_report is None:
        p = REPORTS_DIR / 'quant_report.json'
        quant_report = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

    ml_metrics   = _load_all_metrics()
    trade_counts = _load_trade_counts()

    needs_retrain = [k for k, v in ml_metrics.items() if v.get('needs_retrain')]
    healthy       = [k for k, v in ml_metrics.items() if v.get('healthy')]
    weak          = [k for k, v in ml_metrics.items() if not v.get('healthy')]

    # Build grade summary
    grade_summary = {k: f"grade={v.get('grade')} acc={v.get('test_acc','?')} prec={v.get('test_prec','?')} val_loss={v.get('val_loss','?')} age={v.get('days_old','?')}d"
                     for k, v in ml_metrics.items()}

    user = f"""REAL ML MODEL METRICS — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

=== MODEL GRADES ===
{json.dumps(grade_summary, indent=2)}

=== MODELS NEEDING RETRAIN ({len(needs_retrain)}) ===
{needs_retrain}

=== HEALTHY MODELS ({len(healthy)}) ===
{healthy}

=== TRADE COUNTS (retrain trigger: 20 new trades) ===
{json.dumps(trade_counts, indent=2)}

=== QUANT INSIGHTS FROM CIPHER ===
Win rates: {quant_report.get('win_rate_by_symbol', {})}
Best setups: {quant_report.get('best_setups', [])}
Symbols to prioritize: {quant_report.get('symbols_to_prioritize', [])}
Symbols to disable: {quant_report.get('symbols_to_disable', [])}
Key insights: {quant_report.get('key_insights', [])}

TARGET METRICS: test_acc > 0.72, test_prec > 0.85, val_loss < 0.60
Known model performance:
- CNN NSE: acc=0.7584, prec=0.9048, val_loss=0.556 → Grade B (healthy)
- DNN NSE: acc=0.7383, prec=0.9184, val_loss=0.572 → Grade B (healthy)
- RNN NSE: acc=0.80, val_loss=0.822 → Grade A acc but HIGH val_loss (overfitting risk)

Feature improvement ideas based on CIPHER findings:
- Add session_type as feature (London 76.9% vs NY 63.5% — huge edge)
- Add direction_bias feature (BEARISH consistently outperforms BULLISH)
- Add symbol_group feature (XAGUSD group vs USOIL group)
- Add H4_bias_aligned binary feature (should predict better outcomes)

Give specific deployment status for each model. Return JSON."""

    fallback = {
        "retrain_decision": "YES" if needs_retrain else "NO",
        "markets_to_retrain": list(set(k.split('_')[1] for k in needs_retrain)),
        "models_to_retrain": needs_retrain,
        "reason": f"{len(needs_retrain)} models stale or underperforming",
        "old_model_metrics": grade_summary,
        "new_model_metrics": {},
        "improvement": {},
        "overfitting_risk": "HIGH" if any(ml_metrics.get(k, {}).get('val_loss', 0) > 0.70 for k in ml_metrics) else "MEDIUM",
        "deployment_status": "SHADOW TEST NEW MODEL" if needs_retrain else "KEEP OLD MODEL",
        "weak_models": weak,
        "strong_models": healthy,
        "feature_improvements": [
            "Add session_type feature (London=1, NY=2, Overlap=3) — 76.9% vs 63.5% WR gap",
            "Add h4_bias_aligned binary feature — counter-trend entries lose consistently",
            "Add direction_bearish binary feature — BEARISH outperforms across all symbols",
            "Add symbol_group feature — XAGUSD/XAUUSD behave differently from USOIL",
        ],
        "current_model_health": {k: "healthy" if v.get('healthy') else "needs_retrain" for k, v in ml_metrics.items()},
        "training_triggered": False,
        "days_since_training": {k: v.get('days_old') for k, v in ml_metrics.items()},
        "shadow_status": "ACTIVE",
        "specific_improvements": [
            "RNN NSE: val_loss=0.822 is too high — overfitting risk, retrain with regularization",
            "All models trained May 27 — 8 days old, retrain with June live data",
            "Add session + direction features to DNN for better signal filtering",
        ],
        "notes": "Models healthy but aging. RNN overfitting. Feature improvements recommended.",
    }

    try:
        raw = call_agent('shadow', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['notes'] = str(e)
        result = fallback

    result['ml_metrics'] = ml_metrics

    # Trigger retraining if decided
    if result.get('retrain_decision') == 'YES':
        triggered = _trigger_retrain(result.get('markets_to_retrain', []))
        result['training_triggered'] = len(triggered) > 0
        result['triggered_scripts'] = triggered

    (REPORTS_DIR / 'ml_update_report.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')

    with open(REPORTS_DIR / 'ml_update_report.md', 'w', encoding='utf-8') as f:
        f.write(f"# SHADOW ML Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"## Retrain Decision: {'YES ⚡' if result.get('retrain_decision') == 'YES' else 'NO ✅'}\n")
        f.write(f"**Deployment Status:** {result.get('deployment_status','?')}\n")
        f.write(f"**Overfitting Risk:** {result.get('overfitting_risk','?')}\n")
        f.write(f"**Reason:** {result.get('reason','')}\n\n")
        f.write("## Model Health\n| Model | Grade | Acc | Prec | Val Loss | Age | Status |\n|-------|-------|-----|------|----------|-----|--------|\n")
        for model, m in ml_metrics.items():
            icon = "🟢" if m.get('healthy') else "🟡" if m.get('test_acc', 0) >= 0.65 else "🔴"
            f.write(f"| {model} | {m.get('grade','?')} | {m.get('test_acc','?')} | {m.get('test_prec','?')} | {m.get('val_loss','?')} | {m.get('days_old','?')}d | {icon} |\n")
        f.write("\n## Feature Improvements\n")
        for feat in result.get('feature_improvements', []):
            f.write(f"- {feat}\n")
        f.write("\n## Specific Improvements\n")
        for imp in result.get('specific_improvements', []):
            f.write(f"- {imp}\n")
        f.write(f"\n## Deployment Protocol\n")
        f.write("```\nIdea → Backtest → Shadow Test → Paper Test → Rahul Approval → Production\n```\n")
        f.write(f"\nCurrent status: **{result.get('deployment_status','?')}**\n")
        f.write("\n⚠️ ML models NEVER touch live execution. Shadow mode only.\n")

    print(f"[SHADOW] Retrain: {result.get('retrain_decision')} | Status: {result.get('deployment_status')} | Overfitting: {result.get('overfitting_risk')} | Triggered: {result.get('training_triggered')}")
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, default=str))
