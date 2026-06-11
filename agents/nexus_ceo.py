"""
NEXUS — CB6 CEO Agent
Synthesizes all real agent findings into specific numbered decisions for Rahul.
Sends Telegram DM daily. The ONLY agent Rahul reads.
"""
import json
import requests
from datetime import datetime
from pathlib import Path

from agents.config import call_agent, safe_parse, MEMORY_DIR, REPORTS_DIR, TELEGRAM_TOKEN, TELEGRAM_CHAT

_ARCH  = (MEMORY_DIR / 'cb6_architecture.md').read_text(encoding='utf-8')

SYSTEM = """You are NEXUS, the CEO of CB6 Quantum.
You synthesize ALL department reports into ONE clear board message for Rahul (Chairman).

Rules:
- Be SPECIFIC. Use real numbers from the reports.
- Give numbered decisions Rahul must make — max 3.
- Give clear WINS (what worked today).
- Give clear RISKS (what could blow the account).
- $1M path: are we ahead or behind?
- Rahul reads this ONCE and decides. Make it count.

Return JSON only:
{
  "date": "YYYY-MM-DD",
  "executive_headline": "one punchy sentence — specific numbers",
  "overall_status": "GREEN/YELLOW/RED",
  "prop_firm_update": "specific: FTMO $X of $500 target, GFT $X of $400, deadline X days",
  "engineering_update": "specific: X files checked, X issues found, top task is Y",
  "ml_update": "specific: CNN acc=X%, DNN prec=X%, retrain=YES/NO",
  "growth_update": "specific content output + top growth channel",
  "financial_update": "specific: agent cost $X, total PnL $X, path to $1M",
  "decisions_needed": [
    {"number": 1, "decision": "exact decision", "recommendation": "NEXUS says: do X", "urgency": "URGENT/HIGH/LOW", "impact": "what happens if ignored"}
  ],
  "wins": ["specific win with number"],
  "risks": ["specific risk with number"],
  "tomorrow_priorities": ["specific priority with file/action"],
  "path_to_1m_status": "specific: X months at current trajectory"
}"""


def _load_reports() -> dict:
    files = {
        'quant':       'quant_report.json',
        'ml':          'ml_update_report.json',
        'engineering': 'engineering_standup.md',
        'financial':   'cost_report.md',
        'content':     'content_calendar.md',
        'growth':      'growth_strategy.md',
        'audit':       'audit_log.md',
        'forge':       'forge_proposals.md',
    }
    reports = {}
    for key, fn in files.items():
        p = REPORTS_DIR / fn
        if p.exists():
            try:
                if fn.endswith('.json'):
                    reports[key] = json.loads(p.read_text(encoding='utf-8'))
                else:
                    reports[key] = p.read_text(encoding='utf-8')[-1500:]
            except Exception:
                reports[key] = "unavailable"
        else:
            reports[key] = "not generated"
    return reports


def _send_telegram(msg: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("[NEXUS] Telegram not configured")
        return False
    try:
        # Split long messages
        chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
        for chunk in chunks:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": chunk, "parse_mode": "HTML"},
                timeout=10
            )
            if r.status_code != 200:
                return False
        return True
    except Exception as e:
        print(f"[NEXUS] Telegram error: {e}")
        return False


def _to_telegram(r: dict) -> str:
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(r.get('overall_status',''), "⚪")
    ts = datetime.now().strftime('%Y-%m-%d %H:%M IST')

    lines = [
        f"<b>CB6 SOVEREIGN — Board Report</b>",
        f"<i>{ts}</i>",
        f"",
        f"{icon} <b>{r.get('executive_headline','')}</b>",
        f"",
        f"<b>💰 Prop Firms:</b>",
        r.get('prop_firm_update',''),
        f"",
        f"<b>⚙️ Engineering:</b>",
        r.get('engineering_update',''),
        f"",
        f"<b>🤖 ML:</b> {r.get('ml_update','')}",
        f"<b>📢 Growth:</b> {r.get('growth_update','')}",
        f"<b>📊 Financials:</b> {r.get('financial_update','')}",
    ]

    decisions = r.get('decisions_needed', [])
    if decisions:
        lines += ["", "<b>⚡ DECISIONS FOR RAHUL:</b>"]
        for d in decisions[:3]:
            urgency_icon = "🔴" if d.get('urgency') == "URGENT" else "🟡" if d.get('urgency') == "HIGH" else "🔵"
            lines.append(f"{urgency_icon} <b>#{d.get('number','?')}:</b> {d.get('decision','')}")
            lines.append(f"   → {d.get('recommendation','')}")
            if d.get('impact'):
                lines.append(f"   ⚠️ If ignored: {d.get('impact','')}")

    wins = r.get('wins', [])
    if wins:
        lines += ["", "<b>✅ WINS:</b>"]
        lines += [f"• {w}" for w in wins[:3]]

    risks = r.get('risks', [])
    if risks:
        lines += ["", "<b>⚠️ RISKS:</b>"]
        lines += [f"• {x}" for x in risks[:3]]

    tmrw = r.get('tomorrow_priorities', [])
    if tmrw:
        lines += ["", "<b>📋 TOMORROW:</b>"]
        lines += [f"• {p}" for p in tmrw[:3]]

    lines += [
        "",
        f"<b>🎯 $1M Path:</b> {r.get('path_to_1m_status','')}",
        "",
        "<i>CB6 SOVEREIGN | Rahul approves → Agents execute | Cost: $0/month</i>",
    ]

    return "\n".join(lines)


def run(department_reports: dict = None) -> dict:
    if department_reports is None:
        department_reports = _load_reports()

    # Serialize each report section
    def serialize(v):
        if isinstance(v, dict):
            return json.dumps(v, indent=2)[:2000]
        return str(v)[:1500]

    reports_text = "\n\n".join(
        f"=== {k.upper()} ===\n{serialize(v)}"
        for k, v in department_reports.items()
    )

    user = f"""ALL DEPARTMENT REPORTS — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

{reports_text}

Known facts:
- XAGUSD: 80% WR | XAUUSD: 83.7% WR | USOIL: 63% WR | GBPUSD: 33% WR (problem)
- London session 76.9% WR vs NY 63.5%
- BEARISH trades outperform BULLISH on all symbols
- CNN acc=75.8%, DNN prec=91.8% — ML healthy
- FTMO: deprioritized, runs as-is
- GFT 5K: balance $4,744.48 (started $5,000, total PnL -$255.52), needs +$655.52 for Phase 1 target (+$400 profit)

Synthesize into board report. BE SPECIFIC WITH NUMBERS. Return JSON."""

    fallback = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "executive_headline": "CB6 SOVEREIGN running — GFT $5K Phase 1 in progress, 3 live accounts active",
        "overall_status": "YELLOW",
        "prop_firm_update": "GFT $5K: $4,744.48 balance, need +$655.52 profit for Phase 1. GFT $1K: $982.53. GFT $10K: $10,000. FTMO deprioritized.",
        "engineering_update": "Codebase healthy. XAUUSD re-enabled on all GFT accounts with H4 filter.",
        "ml_update": "CNN 75.8% acc, DNN 91.8% prec — models healthy. Monitor for retraining.",
        "growth_update": "Daily content pipeline active. brokera.in gate not yet open.",
        "financial_update": "Agent cost: $0/month. GFT $5K total PnL: -$255.52. Need Phase 1 win to fund infrastructure.",
        "decisions_needed": [],
        "wins": ["XAUUSD re-enabled on all GFT accounts with H4 filter 2026-06-10", "GFT $10K dry_run blocker fixed", "NSE + Forex scanner ICT reversal fix applied"],
        "risks": ["GFT $5K needs +$655.52 more profit for Phase 1 target", "NSE token must be refreshed before 10:00 IST"],
        "tomorrow_priorities": ["GFT $5K: target XAUUSD + XAGUSD London session setups", "NSE: ICT Silver Bullet windows 10-11 / 13-14 IST", "Monitor all 3 GFT accounts for XAUUSD first trade after re-enable"],
        "path_to_1m_status": "Month 1-2 prop firm phase. SaaS gate needs NSE WR ≥56% + GFT funded.",
    }

    try:
        raw = call_agent('nexus', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['risks'].append(str(e))
        result = fallback

    # Write board report
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(result.get('overall_status',''), "⚪")
    with open(REPORTS_DIR / 'board_report.md', 'w', encoding='utf-8') as f:
        f.write(f"# CB6 SOVEREIGN Board Report\n**{ts}** | {icon} {result.get('overall_status','?')}\n\n")
        f.write(f"## {result.get('executive_headline','')}\n\n")
        f.write(f"### 💰 Prop Firms\n{result.get('prop_firm_update','')}\n\n")
        f.write(f"### ⚙️ Engineering\n{result.get('engineering_update','')}\n\n")
        f.write(f"### 🤖 ML\n{result.get('ml_update','')}\n\n")
        f.write(f"### 📢 Growth\n{result.get('growth_update','')}\n\n")
        f.write(f"### 📊 Financials\n{result.get('financial_update','')}\n\n")
        if result.get('decisions_needed'):
            f.write("## ⚡ Decisions Needed\n")
            for d in result['decisions_needed']:
                f.write(f"### #{d.get('number','?')} [{d.get('urgency','?')}] {d.get('decision','')}\n")
                f.write(f"**NEXUS Recommendation:** {d.get('recommendation','')}\n")
                f.write(f"**Impact if ignored:** {d.get('impact','')}\n\n")
        if result.get('wins'):
            f.write("## ✅ Wins\n" + "\n".join(f"- {w}" for w in result['wins']) + "\n\n")
        if result.get('risks'):
            f.write("## ⚠️ Risks\n" + "\n".join(f"- {x}" for x in result['risks']) + "\n\n")
        if result.get('tomorrow_priorities'):
            f.write("## 📋 Tomorrow\n" + "\n".join(f"- {p}" for p in result['tomorrow_priorities']) + "\n\n")
        f.write(f"## 🎯 $1M Path\n{result.get('path_to_1m_status','')}\n")

    sent = _send_telegram(_to_telegram(result))
    result['telegram_sent'] = sent

    print(f"[NEXUS] Status: {result.get('overall_status')} | Decisions: {len(result.get('decisions_needed',[]))} | Telegram: {'✅' if sent else '❌'}")
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
