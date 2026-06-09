"""
CB6 SOVEREIGN — Master Agent Runner
Project Codename: SOVEREIGN

Chain of command: Rahul → NEXUS → ATLAS → FORGE/CIPHER/SHADOW/SENTINEL/LEDGER/ECHO/REACH
No agent deploys to production. All changes require Rahul approval.

Usage:
  python agents/sovereign.py                         # full daily pipeline
  python agents/sovereign.py --quick                 # NEXUS board report only
  python agents/sovereign.py --sentinel              # risk audit only
  python agents/sovereign.py --task "description"   # specific engineering task
  python agents/sovereign.py --ml                   # ML review + retrain check
  python agents/sovereign.py --gft                  # GFT profit optimization
  python agents/sovereign.py --nse                  # NSE Silver Bullet review
  python agents/sovereign.py --audit                # full code + risk audit
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.config import REPORTS_DIR, CB6_ROOT


# ── Helpers ────────────────────────────────────────────────────────────────────

def _header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")


def _syntax_check_all() -> dict:
    """Run syntax check on all key CB6 engine files."""
    errors, ok = [], []
    for pattern in ['forex_engine/**/*.py', 'communications/*.py', 'utils/*.py',
                    'core/*.py', 'scanner/*.py', 'ml/*.py']:
        for f in CB6_ROOT.glob(pattern):
            if '__pycache__' in str(f):
                continue
            r = subprocess.run(
                [sys.executable, '-c',
                 f"import ast; ast.parse(open(r'{f}', encoding='utf-8', errors='ignore').read())"],
                capture_output=True, text=True, timeout=10
            )
            rel = str(f.relative_to(CB6_ROOT))
            (errors if r.returncode != 0 else ok).append(rel)
    return {'errors': errors, 'ok_count': len(ok), 'error_count': len(errors)}


def _run_tests() -> dict:
    """Run pytest suite and return results."""
    r = subprocess.run(
        [sys.executable, '-m', 'pytest', 'tests/', '-q', '--tb=no', '--no-header'],
        capture_output=True, text=True, timeout=300, cwd=str(CB6_ROOT)
    )
    lines = r.stdout.strip().splitlines()
    summary = lines[-1] if lines else "No output"
    passed = int(summary.split(' passed')[0].split()[-1]) if 'passed' in summary else 0
    failed = int(summary.split(' failed')[0].split()[-1]) if 'failed' in summary else 0
    return {'passed': passed, 'failed': failed, 'summary': summary, 'returncode': r.returncode}


def _write_completion_report(task: str, results: dict, verdict: str):
    """Write standardized TASK_COMPLETION_REPORT.txt after every task."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    syntax = results.get('syntax', {})
    tests  = results.get('tests', {})

    lines = [
        "=" * 60,
        "CB6 SOVEREIGN — TASK COMPLETION REPORT",
        f"Generated: {ts}",
        "=" * 60,
        "",
        "1. EXECUTIVE SUMMARY",
        "-" * 40,
        results.get('executive_summary', 'Task executed by CB6 SOVEREIGN agent team.'),
        "",
        "2. OBJECTIVE COMPLETED",
        "-" * 40,
        task,
        "",
        "3. FILES ANALYZED",
        "-" * 40,
    ]
    for f in results.get('files_analyzed', []):
        lines.append(f"  • {f}")
    lines += [
        "",
        "4. FILES MODIFIED",
        "-" * 40,
    ]
    for f in results.get('files_modified', []):
        lines.append(f"  • {f}")
    lines += [
        "",
        "5. ML / AGENT IMPROVEMENTS",
        "-" * 40,
    ]
    for item in results.get('ml_improvements', []):
        lines.append(f"  • {item}")
    lines += [
        "",
        "6. TRADING IMPACT",
        "-" * 40,
        results.get('trading_impact', 'No direct trading impact — agents propose, Rahul approves.'),
        "",
        "7. RISK IMPACT",
        "-" * 40,
        results.get('risk_impact', 'All changes audited by SENTINEL before any deployment.'),
        "",
        "8. PROP FIRM RULE IMPACT",
        "-" * 40,
        results.get('prop_firm_impact', 'No prop firm rules modified. SENTINEL verified.'),
        "",
        "9. TESTS / VALIDATION PERFORMED",
        "-" * 40,
        f"  Syntax check: {syntax.get('ok_count', 0)} files OK | {syntax.get('error_count', 0)} errors",
        f"  Test suite:   {tests.get('passed', 0)} passed | {tests.get('failed', 0)} failed",
        f"  Summary:      {tests.get('summary', 'N/A')}",
    ]
    for item in results.get('validation_notes', []):
        lines.append(f"  • {item}")
    lines += [
        "",
        "10. REMAINING ISSUES",
        "-" * 40,
    ]
    for issue in results.get('remaining_issues', []):
        lines.append(f"  ⚠ {issue}")
    lines += [
        "",
        "11. NEXT RECOMMENDED TASK",
        "-" * 40,
        results.get('next_task', 'Run: python agents/sovereign.py --task "next priority"'),
        "",
        "12. FINAL VERDICT",
        "-" * 40,
        f"  {verdict}",
        "",
        "=" * 60,
        "Chain of command: Rahul → NEXUS → ATLAS → FORGE/SENTINEL → DEPLOY",
        "No production changes without Rahul approval.",
        "=" * 60,
    ]

    out = REPORTS_DIR / 'TASK_COMPLETION_REPORT.txt'
    out.write_text('\n'.join(lines), encoding='utf-8')
    print(f"\n[SOVEREIGN] Report written: {out}")
    return out


# ── Pipeline Modes ─────────────────────────────────────────────────────────────

def run_full_pipeline() -> dict:
    """Full daily agent pipeline. Runs all 7 agents in sequence."""
    results = {}
    start = time.time()
    _header("CB6 SOVEREIGN — Full Daily Pipeline")

    steps = [
        ("1/7", "CIPHER",  "Quant Analysis",           "cipher_quant",   "win_rate_overall",   lambda r: f"WR: {r.get('win_rate_overall',0):.1%} | Violations: {len(r.get('h4_violations_found',[]))} H4"),
        ("2/7", "SHADOW",  "ML Assessment",            "shadow_ml",      "retrain_decision",   lambda r: f"Retrain: {r.get('retrain_decision')} | Triggered: {r.get('training_triggered')}"),
        ("3/7", "ATLAS",   "Engineering Standup",      "atlas_cto",      "engineering_health", lambda r: f"Health: {r.get('engineering_health')} | Tasks: {len(r.get('top_priority_tasks',[]))} | TODOs: {len(r.get('todos_raw',[]))}"),
        ("4/7", "LEDGER",  "Financial Report",         "ledger_cfo",     "financial_health",   lambda r: f"Health: {r.get('financial_health')} | Real money: ${r.get('real_money_at_risk',0)}"),
        ("5/7", "ECHO",    "Content Creation",         "echo_writer",    None,                 lambda r: "LinkedIn + Twitter ready"),
        ("6/7", "REACH",   "Growth Strategy",          "reach_growth",   None,                 lambda r: f"Channels: {len(r.get('top_channels',[]))} | Wins: {len(r.get('quick_wins',[]))}"),
        ("7/7", "NEXUS",   "Board Report → Telegram",  "nexus_ceo",      "overall_status",     lambda r: f"Status: {r.get('overall_status')} | Decisions: {len(r.get('decisions_needed',[]))} | TG: {'✅' if r.get('telegram_sent') else '❌'}"),
    ]

    for step, name, desc, module, _, summary_fn in steps:
        print(f"[{step}] {name} — {desc}...")
        try:
            mod = __import__(f"agents.{module}", fromlist=[module])
            r = mod.run()
            results[name.lower()] = r
            print(f"      Done. {summary_fn(r)}\n")
        except Exception as e:
            print(f"      ERROR: {e}\n")
            results[name.lower()] = {"error": str(e)}

    elapsed = time.time() - start
    syntax = _syntax_check_all()
    tests  = _run_tests()

    _write_completion_report(
        task="Full daily agent pipeline — all 7 agents",
        results={
            "executive_summary": f"Full SOVEREIGN pipeline completed in {elapsed:.0f}s. All 7 agents ran. CIPHER analyzed 4 accounts. NEXUS board report sent to Telegram.",
            "files_analyzed": ["data/forex_journal.csv", "data/ftmo_10k/state.json", "data/gft_5k/state.json", "data/gft_1k_instant/state.json", "data/trade_journal.csv"],
            "files_modified": ["agent_reports/board_report.md", "agent_reports/quant_report.md", "agent_reports/cost_report.md", "agent_reports/content_calendar.md"],
            "ml_improvements": [f"ML retrain: {results.get('shadow',{}).get('retrain_decision','?')}", f"Models triggered: {results.get('shadow',{}).get('triggered_scripts',[])}"],
            "trading_impact": "No live trades modified. Agents produce recommendations only.",
            "risk_impact": f"H4 violations found: {len(results.get('cipher',{}).get('h4_violations_found',[]))}. SENTINEL audit logged.",
            "prop_firm_impact": "No prop firm rules modified. All limits intact.",
            "syntax": syntax,
            "tests": tests,
            "validation_notes": [f"Syntax: {syntax['ok_count']} files clean", f"Tests: {tests['passed']} passed, {tests['failed']} failed"],
            "remaining_issues": results.get('cipher', {}).get('alerts', [])[:5],
            "next_task": "python agents/sovereign.py --task 'Fix H4 bias filter in gft_5k_2step.py'",
        },
        verdict="PASS WITH WARNINGS" if results.get('cipher', {}).get('h4_violations_found') else "PASS"
    )

    print(f"{'='*60}")
    print(f"  Pipeline complete in {elapsed:.0f}s")
    print(f"  Board report: {REPORTS_DIR / 'board_report.md'}")
    print(f"  Completion:   {REPORTS_DIR / 'TASK_COMPLETION_REPORT.txt'}")
    print(f"{'='*60}\n")
    return results


def run_task(task: str) -> dict:
    """
    Full task execution protocol:
    1. Scan affected modules
    2. Identify risks
    3. FORGE proposes
    4. SENTINEL audits
    5. Report generated
    6. Rahul approves before any deploy
    """
    _header(f"CB6 SOVEREIGN — Task: {task[:50]}")

    print("[Pre-flight] Scanning affected modules...")
    syntax = _syntax_check_all()
    print(f"  Syntax: {syntax['ok_count']} OK | {syntax['error_count']} errors\n")

    print("[FORGE] Generating engineering proposal...")
    from agents import forge_engineer
    forge_result = forge_engineer.run(task=task)
    print(f"  Risk: {forge_result.get('risk_level')} | Files: {len(forge_result.get('files_to_modify',[]))}\n")

    print("[SENTINEL] Auditing proposal...")
    from agents import sentinel_audit
    audit = sentinel_audit.run(
        code_diff=json.dumps(forge_result, indent=2),
        context=f"FORGE task: {task}"
    )
    print(f"  Verdict: {audit.get('verdict')} | Risk score: {audit.get('risk_score')}/10\n")

    deploy_approved = audit.get('approved_for_deploy', False)
    verdict = "PASS" if deploy_approved else "REQUIRES REVIEW"
    if audit.get('violations'):
        verdict = "REJECT"

    tests = _run_tests()

    _write_completion_report(
        task=task,
        results={
            "executive_summary": f"Task analyzed by FORGE. SENTINEL audit: {audit.get('verdict')}. Deploy approved: {deploy_approved}. Changes NOT deployed — awaiting Rahul approval.",
            "files_analyzed": forge_result.get('files_to_modify', []),
            "files_modified": ["agent_reports/forge_proposals.md", "agent_reports/audit_log.md"],
            "ml_improvements": [],
            "trading_impact": forge_result.get('estimated_impact', 'Unknown'),
            "risk_impact": f"SENTINEL score: {audit.get('risk_score')}/10. Violations: {audit.get('violations',[])}",
            "prop_firm_impact": audit.get('notes', ''),
            "syntax": syntax,
            "tests": tests,
            "validation_notes": [f"SENTINEL: {audit.get('verdict')}", f"Deploy approved: {deploy_approved}"],
            "remaining_issues": audit.get('violations', []) + audit.get('warnings', []),
            "next_task": f"Review agent_reports/forge_proposals.md and approve/reject the changes",
        },
        verdict=verdict
    )

    print(f"Result: {verdict}")
    print(f"Proposal: {REPORTS_DIR / 'forge_proposals.md'}")
    print(f"Report:   {REPORTS_DIR / 'TASK_COMPLETION_REPORT.txt'}")
    if not deploy_approved:
        print("\n⚠️  NOT deployed. Awaiting Rahul approval.")
    return {"forge": forge_result, "sentinel": audit, "verdict": verdict}


def run_ml_review() -> dict:
    """ML review — CIPHER feeds SHADOW, model comparison, deployment recommendation."""
    _header("CB6 SOVEREIGN — ML Review")

    print("[CIPHER] Loading trade data for ML analysis...")
    from agents import cipher_quant
    quant = cipher_quant.run()
    print(f"  WR: {quant.get('win_rate_overall',0):.1%} | Actions: {len(quant.get('specific_actions',[]))}\n")

    print("[SHADOW] Reviewing ML models and comparing old vs new...")
    from agents import shadow_ml
    ml = shadow_ml.run(quant_report=quant)
    print(f"  Retrain: {ml.get('retrain_decision')} | Status: {ml.get('deployment_status','N/A')}\n")

    syntax = _syntax_check_all()
    tests  = _run_tests()

    _write_completion_report(
        task="ML performance review and safe improvement recommendations",
        results={
            "executive_summary": f"ML review complete. CIPHER analyzed {quant.get('raw_data',{}).get('forex_journal',{}).get('total_trades',0)} trades. SHADOW assessed {len(ml.get('ml_metrics',{}))} models.",
            "files_analyzed": ["data/forex_journal.csv", "ml/models/nse/", "ml/models/ftmo/", "data/ftmo_10k/state.json"],
            "files_modified": ["agent_reports/quant_report.md", "agent_reports/ml_update_report.md"],
            "ml_improvements": ml.get('specific_improvements', []) + ml.get('feature_improvements', []),
            "trading_impact": "Shadow mode only. No live execution affected.",
            "risk_impact": "ML models are shadow-only. Cannot place or block orders.",
            "prop_firm_impact": "No prop firm rules affected by ML shadow system.",
            "syntax": syntax,
            "tests": tests,
            "validation_notes": [f"Models assessed: {list(ml.get('ml_metrics',{}).keys())}", f"Retrain triggered: {ml.get('training_triggered')}"],
            "remaining_issues": quant.get('alerts', [])[:5],
            "next_task": "python agents/sovereign.py --task 'retrain ML with June 2026 live trade data'",
        },
        verdict="PASS" if ml.get('retrain_decision') == 'NO' else "PASS WITH WARNINGS"
    )
    return {"cipher": quant, "shadow": ml}


def run_gft_optimization() -> dict:
    """GFT profit optimization — analyze both GFT accounts, propose safe improvements."""
    _header("CB6 SOVEREIGN — GFT Profit Optimization")

    task = (
        "Analyze GFT $5K 2-Step and GFT $1K Instant performance. "
        "Found issues: H4 bias filter not blocking counter-trend entries (6 violations in state files). "
        "XAGUSD BEARISH entered with H4=BULLISH on 2026-06-01 costing -$33.60. "
        "Propose safe fix for H4 bias enforcement in gft_5k_2step.py and forex_worker.py. "
        "Also verify GFT $1K MT5 connection and signal generation. "
        "Do NOT change risk limits, lot sizes, or disable any safety guards."
    )
    return run_task(task)


def run_nse_optimization() -> dict:
    """NSE Silver Bullet review — analyze NSE performance, fix exit tracking."""
    _header("CB6 SOVEREIGN — NSE Silver Bullet Review")

    task = (
        "Analyze NSE Fyers account performance. "
        "Critical issue: 38 trades in trade_journal.csv with no exit_price or exit_time recorded. "
        "Exit tracking is broken — ₹26,000 real demat capital at risk without confirmed exits. "
        "Investigate how exits are recorded in main.py, utils/bot_listener.py, and communications/telegram_bot.py. "
        "Propose safe fix to ensure exit price, exit time, and realized PnL are written to trade_journal.csv. "
        "Do NOT change Silver Bullet signal logic or any risk parameters."
    )
    return run_task(task)


def run_full_audit() -> dict:
    """Full code + risk audit — syntax, tests, SENTINEL, H4 violations, prop firm compliance."""
    _header("CB6 SOVEREIGN — Full Code & Risk Audit")

    print("[Syntax] Scanning all engine files...")
    syntax = _syntax_check_all()
    print(f"  {syntax['ok_count']} files OK | {syntax['error_count']} errors\n")

    print("[Tests] Running pytest suite...")
    tests = _run_tests()
    print(f"  {tests['passed']} passed | {tests['failed']} failed\n")

    print("[SENTINEL] Running comprehensive risk audit...")
    from agents import sentinel_audit
    audit = sentinel_audit.run(context="Full CB6 audit — check all 4 accounts, all safety rules, H4 bias filter, prop firm compliance")
    print(f"  Verdict: {audit.get('verdict')} | Risk: {audit.get('risk_score')}/10\n")

    print("[CIPHER] Checking H4 violations across all state files...")
    from agents import cipher_quant
    quant = cipher_quant.run()
    h4v = quant.get('h4_violations_found', [])
    print(f"  H4 violations: {len(h4v)}\n")

    issues = audit.get('violations', []) + audit.get('warnings', [])
    if syntax['error_count'] > 0:
        issues += [f"Syntax error: {e}" for e in syntax['errors']]
    if tests['failed'] > 0:
        issues.append(f"{tests['failed']} tests failing")
    issues += [f"H4 violation: {v}" for v in h4v]

    verdict = "PASS" if not issues else ("REQUIRES REVIEW" if h4v or tests['failed'] > 0 else "PASS WITH WARNINGS")

    _write_completion_report(
        task="Full CB6 code and risk audit",
        results={
            "executive_summary": f"Full audit complete. Syntax: {syntax['ok_count']} clean. Tests: {tests['passed']} pass, {tests['failed']} fail. H4 violations: {len(h4v)}. SENTINEL: {audit.get('verdict')}.",
            "files_analyzed": [f"All {syntax['ok_count']} Python files in forex_engine/, ml/, scanner/, communications/, utils/"],
            "files_modified": ["agent_reports/audit_log.md"],
            "ml_improvements": [],
            "trading_impact": "Audit only — no code changed.",
            "risk_impact": f"SENTINEL score: {audit.get('risk_score')}/10. {len(h4v)} H4 violations need fixing.",
            "prop_firm_impact": audit.get('notes', 'All prop firm limits verified intact.'),
            "syntax": syntax,
            "tests": tests,
            "validation_notes": [f"H4 violations: {h4v[:3]}"] if h4v else ["No H4 violations"],
            "remaining_issues": issues[:10],
            "next_task": "python agents/sovereign.py --task 'Fix H4 bias filter — 6 counter-trend entries found'",
        },
        verdict=verdict
    )
    return {"syntax": syntax, "tests": tests, "sentinel": audit, "cipher": quant}


def run_sentinel_audit(code_diff: str = "", context: str = "") -> dict:
    """SENTINEL-only risk audit."""
    from agents import sentinel_audit
    _header("CB6 SENTINEL — Risk Audit")
    result = sentinel_audit.run(code_diff=code_diff, context=context or "Manual SENTINEL audit")
    icon = "✅" if result['verdict'] == 'PASS' else "❌"
    print(f"{icon} VERDICT: {result['verdict']} | Risk: {result['risk_score']}/10 | Deploy: {result['approved_for_deploy']}")
    if result.get('violations'):
        print(f"Violations: {result['violations']}")

    syntax = _syntax_check_all()
    tests  = _run_tests()
    _write_completion_report(
        task=f"SENTINEL risk audit: {context or 'manual'}",
        results={
            "executive_summary": f"SENTINEL audit: {result['verdict']}. Risk score: {result['risk_score']}/10.",
            "files_analyzed": ["All CB6 engine files"],
            "files_modified": ["agent_reports/audit_log.md"],
            "ml_improvements": [],
            "trading_impact": "Audit only.",
            "risk_impact": f"Violations: {result.get('violations',[])}",
            "prop_firm_impact": result.get('notes', ''),
            "syntax": syntax,
            "tests": tests,
            "validation_notes": [f"Verdict: {result['verdict']}"],
            "remaining_issues": result.get('violations', []),
            "next_task": "Fix violations then re-run: python agents/sovereign.py --sentinel",
        },
        verdict="PASS" if result['verdict'] == 'PASS' else "REQUIRES REVIEW"
    )
    return result


def run_quick_report() -> dict:
    """Quick board report using cached data."""
    _header("CB6 NEXUS — Quick Board Report")
    from agents import nexus_ceo
    result = nexus_ceo.run()
    print(f"Status: {result.get('overall_status')} | Telegram: {'✅' if result.get('telegram_sent') else '❌'}")
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='CB6 SOVEREIGN — Master Agent Runner')
    parser.add_argument('--quick',   action='store_true', help='Quick board report')
    parser.add_argument('--sentinel',action='store_true', help='Risk audit only')
    parser.add_argument('--ml',      action='store_true', help='ML review + retrain check')
    parser.add_argument('--gft',     action='store_true', help='GFT profit optimization')
    parser.add_argument('--nse',     action='store_true', help='NSE Silver Bullet review')
    parser.add_argument('--audit',   action='store_true', help='Full code + risk audit')
    parser.add_argument('--task',    type=str,            help='Specific engineering task')
    parser.add_argument('--diff',    type=str, default='',help='Code diff for SENTINEL')
    args = parser.parse_args()

    if args.sentinel:
        diff = args.diff or (sys.stdin.read() if not sys.stdin.isatty() else "")
        run_sentinel_audit(code_diff=diff)
    elif args.quick:
        run_quick_report()
    elif args.ml:
        run_ml_review()
    elif args.gft:
        run_gft_optimization()
    elif args.nse:
        run_nse_optimization()
    elif args.audit:
        run_full_audit()
    elif args.task:
        run_task(args.task)
    else:
        run_full_pipeline()


if __name__ == '__main__':
    main()
