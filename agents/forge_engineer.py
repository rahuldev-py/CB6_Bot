"""
FORGE — CB6 Lead Engineer
Follows full task execution protocol: scan → plan → propose → rollback plan.
All proposals go to SENTINEL before Rahul approves. Never deploys directly.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from agents.config import call_agent, safe_parse, MEMORY_DIR, REPORTS_DIR, CB6_ROOT

_ARCH  = (MEMORY_DIR / 'cb6_architecture.md').read_text(encoding='utf-8')
_RULES = (MEMORY_DIR / 'prop_firm_rules.md').read_text(encoding='utf-8')

SYSTEM = f"""You are FORGE, the Lead Engineer of CB6 Quantum.
You follow the full task execution protocol before proposing any code change.

PROTOCOL:
Pre-change:
1. Scan affected modules
2. Identify live-trading impact
3. Identify ML impact
4. Identify risk impact
5. Identify prop-firm impact
6. Create implementation plan
7. Create rollback plan

During proposal:
1. Minimal safe changes only
2. Preserve existing architecture
3. Keep account isolation (FTMO / GFT 5K / GFT 1K / NSE separate)
4. Add logging to any new code
5. Add validation checks
6. Never delete safety code
7. Never fake improvements without evidence

HARD CONSTRAINTS:
- Never re-enable XAUUSD on GFT
- Never change FTMO best-day cap ($250)
- Never change any daily loss limits
- Never set paper_mode=True in live configs
- Never bypass H4 bias check
- Always propose git branch changes, never main directly
- NSE: index futures + options ONLY

CB6 ARCHITECTURE:
{_ARCH}

Return JSON only:
{{
  "task": "task description",
  "pre_flight": {{
    "affected_modules": [],
    "live_trading_impact": "description",
    "ml_impact": "description",
    "risk_impact": "description",
    "prop_firm_impact": "description"
  }},
  "implementation_plan": ["step 1", "step 2", "step 3"],
  "rollback_plan": ["rollback step 1", "rollback step 2"],
  "analysis": "what you found in the codebase",
  "proposal": "what changes are needed and why",
  "files_to_modify": ["file1.py", "file2.py"],
  "code_changes": [
    {{
      "file": "path/to/file.py",
      "change_type": "add/modify/delete",
      "description": "what changes",
      "code_snippet": "actual code",
      "line_hint": "approximate location"
    }}
  ],
  "risk_level": "LOW/MEDIUM/HIGH",
  "requires_sentinel": true,
  "estimated_impact": "specific description of expected improvement",
  "validation_steps": ["how to verify the fix works"]
}}"""


def _read_file(path: str, max_chars: int = 3000) -> str:
    p = CB6_ROOT / path
    if p.exists():
        try:
            return p.read_text(encoding='utf-8', errors='ignore')[:max_chars]
        except Exception:
            pass
    return f"[FILE NOT FOUND: {path}]"


def _scan_for_pattern(pattern: str, file_glob: str = "**/*.py") -> list:
    """Search CB6 codebase for a pattern."""
    results = []
    import re
    for f in CB6_ROOT.glob(file_glob):
        if '__pycache__' in str(f) or 'agents' in str(f):
            continue
        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line, re.IGNORECASE):
                    results.append(f"{f.relative_to(CB6_ROOT)}:{i}: {line.strip()[:100]}")
        except Exception:
            pass
    return results[:20]


def run(task: str, relevant_files: list = None) -> dict:
    # Auto-scan for relevant context based on task keywords
    auto_context = ""

    if 'h4' in task.lower() or 'bias' in task.lower():
        hits = _scan_for_pattern(r'h4_bias|h4_direction|H4', 'forex_engine/**/*.py')
        auto_context += f"\n\nH4 BIAS CODE LOCATIONS:\n" + "\n".join(hits[:10])

    if 'exit' in task.lower() or 'journal' in task.lower():
        hits = _scan_for_pattern(r'trade_journal|exit_price|realized_pnl', '**/*.py')
        auto_context += f"\n\nEXIT/JOURNAL CODE LOCATIONS:\n" + "\n".join(hits[:10])

    if 'gbpusd' in task.lower() or 'symbol' in task.lower():
        hits = _scan_for_pattern(r'GBPUSD|forex_instruments', 'forex_engine/**/*.py')
        auto_context += f"\n\nSYMBOL CODE LOCATIONS:\n" + "\n".join(hits[:10])

    if 'gft' in task.lower():
        auto_context += f"\n\nGFT 5K ENGINE (first 2000 chars):\n" + _read_file('forex_engine/prop_firms/gft/gft_5k_2step.py', 2000)

    if 'nse' in task.lower():
        auto_context += f"\n\nNSE MAIN (first 1500 chars):\n" + _read_file('main.py', 1500)

    # Read specifically requested files
    file_context = ""
    for fp in (relevant_files or [])[:3]:
        file_context += f"\n\n--- {fp} ---\n{_read_file(fp, 2000)}"

    user = f"""ENGINEERING TASK:
{task}

AUTO-SCANNED CONTEXT:{auto_context[:3000]}

SPECIFIC FILE CONTEXT:{file_context[:2000]}

Today: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

Follow the full task execution protocol.
Give specific file names, line hints, and actual code snippets.
Include rollback plan. Return JSON."""

    fallback = {
        "task": task,
        "pre_flight": {
            "affected_modules": [],
            "live_trading_impact": "Unknown — manual review required",
            "ml_impact": "None",
            "risk_impact": "Unknown — SENTINEL must audit",
            "prop_firm_impact": "Unknown — verify manually",
        },
        "implementation_plan": ["Manual investigation required"],
        "rollback_plan": ["git revert the commit", "Restart forex_main.py"],
        "analysis": "Auto-scan complete. Manual investigation needed.",
        "proposal": "Unable to auto-generate proposal. Manual review needed.",
        "files_to_modify": [],
        "code_changes": [],
        "risk_level": "HIGH",
        "requires_sentinel": True,
        "estimated_impact": "Unknown",
        "validation_steps": ["Run pytest", "Check syntax", "Restart bot and monitor for 10 minutes"],
    }

    try:
        raw = call_agent('forge', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['analysis'] = str(e)
        result = fallback

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    with open(REPORTS_DIR / 'forge_proposals.md', 'a', encoding='utf-8') as f:
        f.write(f"\n---\n## [{ts}] {task[:80]}\n")
        f.write(f"**Risk Level:** {result.get('risk_level')} | **SENTINEL Required:** {result.get('requires_sentinel')}\n\n")

        pf = result.get('pre_flight', {})
        f.write(f"### Pre-flight\n")
        f.write(f"- Modules affected: {pf.get('affected_modules', [])}\n")
        f.write(f"- Live trading impact: {pf.get('live_trading_impact','')}\n")
        f.write(f"- Risk impact: {pf.get('risk_impact','')}\n")
        f.write(f"- Prop firm impact: {pf.get('prop_firm_impact','')}\n\n")

        f.write(f"### Implementation Plan\n")
        for step in result.get('implementation_plan', []):
            f.write(f"1. {step}\n")

        f.write(f"\n### Rollback Plan\n")
        for step in result.get('rollback_plan', []):
            f.write(f"- {step}\n")

        f.write(f"\n### Proposal\n{result.get('proposal','')}\n")
        f.write(f"\n### Files to Modify\n")
        for fp in result.get('files_to_modify', []):
            f.write(f"- `{fp}`\n")

        for change in result.get('code_changes', []):
            f.write(f"\n#### `{change.get('file','')}` — {change.get('description','')}\n")
            if change.get('code_snippet'):
                f.write(f"```python\n{change['code_snippet']}\n```\n")

        f.write(f"\n### Validation\n")
        for v in result.get('validation_steps', []):
            f.write(f"- {v}\n")

        f.write(f"\n**Estimated Impact:** {result.get('estimated_impact','')}\n")
        f.write(f"**Status:** ⏳ Awaiting SENTINEL audit + Rahul approval before any deployment.\n")

    print(f"[FORGE] Proposal ready | Risk: {result.get('risk_level')} | Files: {len(result.get('files_to_modify',[]))} | Modules: {result.get('pre_flight',{}).get('affected_modules',[])}")
    return result


if __name__ == '__main__':
    r = run("Review H4 bias filter in GFT engine — counter-trend entries detected")
    print(json.dumps(r, indent=2, default=str))
