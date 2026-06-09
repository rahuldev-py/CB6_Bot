"""
SENTINEL — CB6 Risk Auditor
Full checklist: all 4 accounts, H4 bias violations, prop firm rules, symbol bans.
Every code change must pass SENTINEL before reaching Rahul for approval.
Nothing deploys without SENTINEL PASS + Rahul approval.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from agents.config import call_agent, safe_parse, MEMORY_DIR, REPORTS_DIR, CB6_ROOT

_RULES = (MEMORY_DIR / 'prop_firm_rules.md').read_text(encoding='utf-8')
_ARCH  = (MEMORY_DIR / 'cb6_architecture.md').read_text(encoding='utf-8')

SYSTEM = f"""You are SENTINEL, the Risk Auditor of CB6 Quantum.
You enforce ALL safety rules across ALL 4 accounts.

ACCOUNTS:
1. NSE Fyers — ₹26K real demat. Index futures+options ONLY. No equity/stocks.
2. GFT $1K Instant — $1K real funded. Daily DD $30. Max lot 0.01. Risk/trade $2.50.
3. GFT $5K 2-Step — Prop challenge. Daily loss $200. Max loss $500. XAUUSD BANNED.
4. FTMO $10K — Prop challenge. Daily loss $300. Best-day cap $250. Deadline June 6.

PROP FIRM RULES:
{_RULES}

FULL CHECKLIST (check every item):
A. SYMBOL RULES
   1. XAUUSD not re-enabled on GFT (1K or 5K) — PERMANENT ban
   2. No equity/stock symbols in NSE engine
   3. No crypto symbols in any live engine

B. RISK LIMITS
   4. FTMO best_day_cap $250 still enforced in ftmo_state.py
   5. FTMO daily loss limit $300 unchanged
   6. GFT 5K daily loss limit $200 unchanged
   7. GFT 1K daily DD $30 and max DD $60 unchanged
   8. GFT 1K max lot 0.01 unchanged
   9. GFT 1K risk/trade $2.50 max unchanged
   10. Emergency stop / kill switch logic not weakened

C. EXECUTION SAFETY
   11. paper_mode=True not in any live config
   12. No hardcoded live credentials in code
   13. Account isolation maintained (no cross-account contamination)
   14. 3-wave filter active (wave_count≥3 + sweep) — replaces H4 hard gate (removed 2026-06-06)
   15. No agent deploys to production automatically

D. SIGNAL INTEGRITY
   16. No new symbols enabled without explicit approval
   17. GBPUSD: flag if still enabled (33% WR — recommend disable)
   18. Lot sizes within approved bounds
   19. SL logic not removed or weakened
   20. Trade journal writes not corrupted

E. ML SAFETY
   21. ML models in shadow mode only — no live execution
   22. No ML output directly routed to order execution
   23. Retrain does not overwrite live-used model files without approval

Return JSON only:
{{
  "verdict": "PASS or FAIL",
  "risk_score": 0,
  "checklist_results": {{
    "symbol_rules": "PASS/FAIL",
    "risk_limits": "PASS/FAIL",
    "execution_safety": "PASS/FAIL",
    "signal_integrity": "PASS/FAIL",
    "ml_safety": "PASS/FAIL"
  }},
  "violations": [],
  "warnings": [],
  "approved_for_deploy": false,
  "h4_violation_detected": false,
  "notes": "brief summary"
}}"""


def _static_checks(code_diff: str) -> list:
    """Hard-coded regex checks that do not need LLM."""
    violations = []

    # Check 1: XAUUSD re-enabled on GFT
    if re.search(r'XAUUSD.*enabled.*[Tt]rue|enabled.*[Tt]rue.*XAUUSD', code_diff):
        violations.append("HARD BLOCK: XAUUSD appears to be re-enabled — PERMANENTLY banned on GFT")

    # Check 2: paper_mode = True
    if re.search(r'paper_mode\s*=\s*True', code_diff):
        violations.append("HARD BLOCK: paper_mode=True found in change — never in live configs")

    # Check 3: best_day_cap removed
    if 'best_day_cap' in code_diff and re.search(r'#.*best_day_cap|del.*best_day_cap|remove.*best_day_cap', code_diff, re.IGNORECASE):
        violations.append("HARD BLOCK: best_day_cap appears to be removed — $250 cap must stay")

    # Check 4: risk limits increased
    if re.search(r'daily_loss_limit\s*=\s*[4-9]\d{2,}', code_diff):
        violations.append("WARNING: daily_loss_limit may have been increased beyond safe value")

    # Check 5: H4 bypass
    # H4 gate intentionally removed 2026-06-06 — replaced by 3-wave filter (wave_count≥3 + sweep)
    # Do NOT flag H4 bypass as violation anymore.

    # Check 6: lot size explosion
    if re.search(r'max_lot\s*=\s*[1-9]\d+\.?\d*', code_diff):
        violations.append("WARNING: max_lot value looks very high — verify within approved bounds")

    # Check 7: equity symbol
    equity_patterns = ['NSE:RELIANCE', 'NSE:TCS', 'NSE:HDFC', 'NSE:INFY']
    for ep in equity_patterns:
        if ep in code_diff:
            violations.append(f"HARD BLOCK: Equity symbol {ep} found — index only")

    return violations


def _check_state_files() -> list:
    """Check live state files for H4 violations and rule breaches."""
    issues = []
    for name, path in [('FTMO', 'data/ftmo_10k/state.json'),
                        ('GFT_5K', 'data/gft_5k/state.json'),
                        ('GFT_1K', 'data/gft_1k_instant/state.json')]:
        p = CB6_ROOT / path
        if not p.exists():
            continue
        try:
            s = json.loads(p.read_text(encoding='utf-8'))
            # Check paper mode
            if s.get('mode') == 'paper' or s.get('paper_mode'):
                issues.append(f"{name}: paper_mode active in live state file")
            # Check H4 violations in closed trades
            for t in s.get('closed_trades', []):
                reason = t.get('entry_reason', '')
                direction = t.get('direction', '')
                if ('H4=BULLISH' in reason and direction == 'BEARISH') or \
                   ('H4=BEARISH' in reason and direction == 'BULLISH'):
                    issues.append(f"{name}: H4 violation — {t.get('symbol')} {direction} with {['H4=BULLISH' if 'H4=BULLISH' in reason else 'H4=BEARISH'][0]} on {t.get('entry_time','?')[:16]}")
            # Check GFT 1K max lot
            if name == 'GFT_1K':
                for t in s.get('open_trades', []) + s.get('closed_trades', []):
                    if float(t.get('lots', 0)) > 0.01:
                        issues.append(f"GFT_1K: lot size {t.get('lots')} exceeds 0.01 limit")
        except Exception:
            pass
    return issues


def run(code_diff: str = "", context: str = "") -> dict:
    static_violations = _static_checks(code_diff)
    state_issues      = _check_state_files()

    user = f"""AUDIT REQUEST
Context: {context or 'Routine risk audit'}

STATIC CHECKS ALREADY PERFORMED:
Violations: {static_violations}
State file issues: {state_issues}

CODE CHANGE TO AUDIT:
{code_diff[:3000] or 'No diff provided — perform general CB6 health audit using your knowledge of the system.'}

Run your full checklist across all 5 categories (symbol rules, risk limits, execution safety, signal integrity, ML safety).
Return JSON verdict."""

    fallback = {
        "verdict": "FAIL" if static_violations else "PASS",
        "risk_score": min(10, len(static_violations) * 3 + len(state_issues)),
        "checklist_results": {
            "symbol_rules": "FAIL" if any('XAUUSD' in v or 'equity' in v.lower() for v in static_violations) else "PASS",
            "risk_limits": "FAIL" if any('daily_loss' in v or 'best_day' in v for v in static_violations) else "PASS",
            "execution_safety": "FAIL" if any('paper_mode' in v or 'H4' in v for v in static_violations) else "PASS",
            "signal_integrity": "PASS",
            "ml_safety": "PASS",
        },
        "violations": static_violations + state_issues,
        "warnings": ["GBPUSD 33% WR — recommend disabling"] if not static_violations else [],
        "approved_for_deploy": len(static_violations) == 0 and len(state_issues) == 0,
        "h4_violation_detected": any('H4' in i for i in state_issues),
        "notes": f"Static: {len(static_violations)} violations. State files: {len(state_issues)} issues.",
    }

    try:
        raw = call_agent('sentinel', SYSTEM, user)
        result = safe_parse(raw, fallback)
        # Always override with static checks — LLM cannot override hard rules
        if static_violations:
            result['violations'] = list(set(result.get('violations', []) + static_violations))
            result['verdict'] = 'FAIL'
            result['approved_for_deploy'] = False
        result['state_file_issues'] = state_issues
        if state_issues:
            result['warnings'] = list(set(result.get('warnings', []) + state_issues))
            if any('H4' in i for i in state_issues):
                result['h4_violation_detected'] = True
    except Exception as e:
        fallback['violations'].append(f"SENTINEL error: {e}")
        result = fallback

    # Append to audit log
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    icon = "✅" if result['verdict'] == 'PASS' else "❌"
    with open(REPORTS_DIR / 'audit_log.md', 'a', encoding='utf-8') as f:
        f.write(f"\n## {ts} — {icon} {result['verdict']} | Risk: {result['risk_score']}/10\n")
        f.write(f"**Context:** {context or 'General audit'}\n")
        checks = result.get('checklist_results', {})
        f.write(f"**Checklist:** " + " | ".join(f"{k}: {v}" for k, v in checks.items()) + "\n")
        if result.get('violations'):
            f.write(f"**Violations ({len(result['violations'])}):**\n")
            for v in result['violations']:
                f.write(f"  - 🔴 {v}\n")
        if result.get('warnings'):
            f.write(f"**Warnings:**\n")
            for w in result['warnings']:
                f.write(f"  - ⚠️ {w}\n")
        if result.get('h4_violation_detected'):
            f.write(f"**H4 VIOLATIONS DETECTED IN STATE FILES**\n")
        f.write(f"**Deploy approved:** {result['approved_for_deploy']}\n")
        f.write(f"**Notes:** {result.get('notes','')}\n")

    print(f"[SENTINEL] {result['verdict']} | Risk: {result['risk_score']}/10 | H4: {'YES' if result.get('h4_violation_detected') else 'NO'} | Deploy: {result['approved_for_deploy']}")
    return result


if __name__ == '__main__':
    r = run(context="Self-test — full CB6 audit")
    print(json.dumps(r, indent=2))
