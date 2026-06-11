"""
ATLAS — CB6 CTO
Scans real CB6 engine files for bugs and optimization opportunities.
Reads actual code, finds TODOs, checks signal logic, proposes FORGE tasks.
"""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from agents.config import call_agent, safe_parse, MEMORY_DIR, REPORTS_DIR, CB6_ROOT

_RULES = (MEMORY_DIR / 'prop_firm_rules.md').read_text(encoding='utf-8')

SYSTEM = """You are ATLAS, the CTO of CB6 Quantum.
You receive REAL code snippets and data. Give SPECIFIC engineering tasks.
Reference actual file names, function names, and line numbers where possible.
Do NOT give generic advice. Every task must name the exact file and what to change.

Return JSON only:
{
  "engineering_health": "GREEN/YELLOW/RED",
  "health_summary": "specific one-liner with file names",
  "top_priority_tasks": [
    {"task": "specific task", "file": "exact/file.py", "priority": "URGENT/HIGH/MEDIUM/LOW", "assign_to": "FORGE/SHADOW/CIPHER"}
  ],
  "todos_found": [],
  "issues_found": [],
  "optimization_opportunities": [],
  "blockers": [],
  "standup_summary": "paragraph with specific findings"
}"""


def _syntax_check() -> dict:
    errors = []
    ok = []
    key_files = [
        'forex_engine/forex_worker.py',
        'forex_engine/prop_firms/ftmo/ftmo_state.py',
        'forex_engine/prop_firms/gft/gft_5k_2step.py',
        'forex_engine/forex_instruments.py',
        'main.py', 'forex_main.py',
    ]
    for fp in key_files:
        p = CB6_ROOT / fp
        if p.exists():
            r = subprocess.run(
                [sys.executable, '-c', f"import ast; ast.parse(open(r'{p}', encoding='utf-8').read())"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0:
                errors.append(f"{fp}: {r.stderr[:120]}")
            else:
                ok.append(fp)
    return {"errors": errors, "ok": ok}


def _find_todos() -> list:
    todos = []
    for f in CB6_ROOT.rglob('*.py'):
        if any(skip in str(f) for skip in ['__pycache__', '.git', 'agents', 'ml_engine']):
            continue
        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
            for i, line in enumerate(text.splitlines(), 1):
                # Match only genuine markers — '# BUG' alone triggers false positives on
                # '# Bug fix:' and '# Bug N:' comment blocks (already-resolved explanations).
                upper = line.upper()
                if ('# TODO' in upper or '# FIXME' in upper or '# HACK' in upper
                        or '# XXX' in upper
                        or (upper.lstrip().startswith('# BUG') and '# BUG FIX' not in upper)):
                    rel = str(f.relative_to(CB6_ROOT))
                    todos.append(f"{rel}:{i} — {line.strip()}")
        except Exception:
            pass
    return todos[:20]


def _read_engine_snippet(filepath: str, lines: int = 60) -> str:
    p = CB6_ROOT / filepath
    if p.exists():
        try:
            return p.read_text(encoding='utf-8')[:lines * 80]
        except Exception:
            pass
    return ""


def _check_signal_logic() -> dict:
    issues = []
    # Check GBPUSD is still enabled (quant says it should be disabled)
    instruments_path = CB6_ROOT / 'forex_engine/forex_instruments.py'
    if instruments_path.exists():
        text = instruments_path.read_text(encoding='utf-8', errors='ignore')
        if 'GBPUSD' in text and 'enabled' in text.lower():
            issues.append("GBPUSD still enabled — quant shows 33% WR, should be disabled")
        if 'XAUUSD' in text:
            # XAUUSD re-enabled on all GFT accounts 2026-06-10 with H4 bias filter enforced
            pass  # expected — no action needed

    # Check kill zones
    gft_path = CB6_ROOT / 'forex_engine/prop_firms/gft/gft_5k_2step.py'
    if gft_path.exists():
        text = gft_path.read_text(encoding='utf-8', errors='ignore')
        if '(7,12)' not in text and '(16,20)' not in text:
            issues.append("GFT kill zones may be wrong — should be [(7,12),(16,20)] UTC")

    return {"issues": issues}


def run(quant_report: dict = None) -> dict:
    health    = _syntax_check()
    todos     = _find_todos()
    sig_check = _check_signal_logic()

    # Read key engine snippets for ATLAS to analyze
    worker_snippet = _read_engine_snippet('forex_engine/forex_worker.py', 80)
    ftmo_snippet   = _read_engine_snippet('forex_engine/prop_firms/ftmo/ftmo_state.py', 40)

    quant_ctx = ""
    if quant_report:
        quant_ctx = f"""
CIPHER FINDINGS:
- Symbols to disable: {quant_report.get('symbols_to_disable', [])}
- London WR: {quant_report.get('win_rate_by_session', {}).get('London', 'N/A')}
- NY WR: {quant_report.get('win_rate_by_session', {}).get('NY', 'N/A')}
- BEARISH edge confirmed: {quant_report.get('direction_edge', {})}
- Specific actions needed: {quant_report.get('specific_actions', [])}
"""

    user = f"""CODEBASE HEALTH — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

SYNTAX CHECK:
{json.dumps(health, indent=2)}

TODOs IN CODEBASE:
{chr(10).join(todos[:10]) if todos else 'None found'}

SIGNAL LOGIC ISSUES:
{json.dumps(sig_check, indent=2)}
{quant_ctx}

KEY ENGINE SNIPPET (forex_worker.py — first 80 lines):
{worker_snippet[:2000]}

Based on this REAL code analysis, give SPECIFIC engineering tasks.
Name the exact file and what to change. Return JSON."""

    fallback = {
        "engineering_health": "GREEN" if not health['errors'] else "RED",
        "health_summary": f"{len(health['errors'])} syntax errors | {len(todos)} TODOs | {len(sig_check['issues'])} signal issues",
        "top_priority_tasks": [
            {"task": issue, "file": "forex_engine/forex_instruments.py", "priority": "HIGH", "assign_to": "FORGE"}
            for issue in sig_check['issues']
        ],
        "todos_found": todos[:10],
        "issues_found": health['errors'] + sig_check['issues'],
        "optimization_opportunities": [],
        "blockers": [],
        "standup_summary": f"Syntax: {len(health['ok'])} OK, {len(health['errors'])} errors. Signal: {len(sig_check['issues'])} issues. TODOs: {len(todos)}.",
    }

    try:
        raw = call_agent('atlas', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['issues_found'].append(str(e))
        result = fallback

    result['syntax_check'] = health
    result['todos_raw'] = todos[:10]

    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(result.get('engineering_health',''), "⚪")
    with open(REPORTS_DIR / 'engineering_standup.md', 'w', encoding='utf-8') as f:
        f.write(f"# ATLAS Engineering Standup — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"## {icon} {result.get('engineering_health','?')} — {result.get('health_summary','')}\n\n")
        f.write("## Priority Tasks for FORGE\n")
        for t in result.get('top_priority_tasks', []):
            f.write(f"- **[{t.get('priority','?')}]** `{t.get('file','')}` — {t.get('task','')} → {t.get('assign_to','')}\n")
        if result.get('optimization_opportunities'):
            f.write("\n## Optimization Opportunities\n")
            for o in result['optimization_opportunities']:
                f.write(f"- {o}\n")
        if result.get('todos_found'):
            f.write(f"\n## TODOs Found ({len(todos)} total)\n")
            for td in result['todos_found'][:5]:
                f.write(f"- `{td}`\n")
        if health['errors']:
            f.write(f"\n## ⚠️ Syntax Errors\n")
            for e in health['errors']:
                f.write(f"- {e}\n")
        f.write(f"\n## Standup\n{result.get('standup_summary','')}\n")

    print(f"[ATLAS] Health: {result.get('engineering_health')} | Tasks: {len(result.get('top_priority_tasks',[]))} | TODOs: {len(todos)} | Signal issues: {len(sig_check['issues'])}")
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
