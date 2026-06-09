"""
LEDGER — CB6 CFO
Tracks all 4 accounts: NSE Fyers, GFT $1K, GFT $5K, FTMO.
Priority: protect real money (NSE + GFT $1K) first, then prop challenges.
"""
import json
from datetime import datetime
from pathlib import Path

from agents.config import call_agent, safe_parse, MEMORY_DIR, REPORTS_DIR, CB6_ROOT

_ARCH = (MEMORY_DIR / 'cb6_architecture.md').read_text(encoding='utf-8')

SYSTEM = """You are LEDGER, the CFO of CB6 Quantum.
You track 4 accounts. Priority: real money first, then prop firm challenges.

Account priority:
1. GFT $1K Instant — real live funded money ($1,000)
2. NSE Fyers — real demat money (₹26,000 = ~$310)
3. GFT $5K 2-Step — prop challenge (pass → get $5K master → fund infrastructure)
4. FTMO $10K — prop challenge (pass → get $10K account)

Path to $1M ARR:
- GFT $5K + FTMO pass → funded accounts → trading profits
- NSE WR ≥56% → brokera.in SaaS launches
- 2,500 users × ₹2,999/month = ~$1M ARR

Return JSON only:
{
  "date": "YYYY-MM-DD",
  "account_status": {
    "gft_1k_instant": {"capital_usd": 0, "daily_pnl": 0, "total_pnl": 0, "daily_dd_used_pct": 0, "max_dd_used_pct": 0, "status": "HEALTHY/AT_RISK/CRITICAL", "priority": 1},
    "nse_fyers": {"capital_inr": 26000, "capital_usd": 310, "open_trades": 0, "status": "HEALTHY/AT_RISK/CRITICAL", "priority": 2},
    "gft_5k_2step": {"capital_usd": 0, "total_pnl": 0, "phase1_target": 400, "phase1_progress": 0, "phase1_remaining": 0, "phase1_pct": 0, "status": "ON_TRACK/BEHIND/AT_RISK", "priority": 3},
    "ftmo_10k": {"capital_usd": 0, "total_pnl": 0, "target": 500, "remaining": 0, "deadline": "June 6 2026", "days_left": 0, "status": "ON_TRACK/BEHIND/URGENT", "priority": 4}
  },
  "total_real_capital_usd": 0,
  "total_prop_capital_usd": 0,
  "agent_costs": {"monthly_usd": 0.0, "note": "Groq free tier"},
  "infrastructure_fund": {"source": "GFT $5K master after passing", "amount_expected": 5000, "status": "pending"},
  "saas_projection": {"gate_open": false, "gate_condition": "NSE WR ≥56% + GFT funded", "months_to_launch": 0, "path_to_1m": ""},
  "financial_health": "GREEN/YELLOW/RED",
  "real_money_at_risk": 0,
  "alerts": [],
  "cfo_summary": "specific paragraph with real numbers"
}"""


def _load_state(path: str) -> dict:
    p = CB6_ROOT / path
    if not p.exists():
        return {}
    try:
        s = json.loads(p.read_text(encoding='utf-8'))
        closed = s.get('closed_trades', [])
        total_pnl = sum(t.get('pnl_usd', t.get('pnl', 0)) for t in closed)
        return {
            "capital": s.get('capital', 0),
            "starting_capital": s.get('starting_capital', 0),
            "total_pnl": round(s.get('total_pnl', total_pnl), 2),
            "daily_pnl": round(s.get('daily_pnl', 0), 2),
            "peak_capital": s.get('peak_capital', s.get('capital', 0)),
            "paused": s.get('paused', False),
            "closed_trade_count": len(closed),
        }
    except Exception:
        return {}


def run() -> dict:
    gft_1k  = _load_state('data/gft_1k_instant/state.json')
    gft_5k  = _load_state('data/gft_5k/state.json')
    ftmo    = _load_state('data/ftmo_10k/state.json')

    gft_5k_pnl       = gft_5k.get('total_pnl', -33)
    ftmo_pnl         = ftmo.get('total_pnl', 0)
    gft_1k_capital   = gft_1k.get('capital', 1000)
    gft_5k_capital   = gft_5k.get('capital', 4967)
    ftmo_capital     = ftmo.get('capital', 9891)

    real_capital_usd = gft_1k_capital + 310  # GFT $1K + NSE ₹26K converted

    user = f"""ACCOUNT DATA — {datetime.now().strftime('%Y-%m-%d')}

PRIORITY 1 — GFT $1K INSTANT (Real live funded money):
Capital: ${gft_1k_capital} | Daily PnL: ${gft_1k.get('daily_pnl',0)} | Closed trades: {gft_1k.get('closed_trade_count',0)}
Daily DD limit: $30 | Max DD: $60 | Status: {'PAUSED' if gft_1k.get('paused') else 'ACTIVE'}

PRIORITY 2 — NSE FYERS (Real demat money):
Capital: ₹26,000 (~$310) | 38 open trade entries, 0 exits recorded
Status: Exit tracking may be broken

PRIORITY 3 — GFT $5K 2-STEP (Prop challenge):
Capital: ${gft_5k_capital} | Total PnL: ${gft_5k_pnl}
Phase 1: Need +$400 | Progress: ${gft_5k_pnl}/+$400 = {round((gft_5k_pnl+400)/400*100 if gft_5k_pnl > -400 else 0, 1)}% | Remaining: ${round(400-gft_5k_pnl, 2)}
Both closed trades lost. H4 violation found.
After passing: Get $5K master account → fund CB6 infrastructure

PRIORITY 4 — FTMO $10K (Prop challenge):
Capital: ${ftmo_capital} | Total PnL: ${ftmo_pnl}
Target: +$500 | Remaining: ~${max(0, 500-ftmo_pnl)} | DEADLINE: ~June 6 2026

TOTAL REAL MONEY AT RISK: ${real_capital_usd} (GFT $1K + NSE ₹26K)
TOTAL PROP CAPITAL: ${gft_5k_capital + ftmo_capital}
AGENT COST: $0/month (Groq free tier)

SaaS gate: NSE WR ≥56% + GFT funded. Not yet open.
brokera.in pricing: ₹2,999/month/user. 2,500 users = $1M ARR target.

Return CFO report as JSON with real numbers."""

    fallback = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "account_status": {
            "gft_1k_instant": {"capital_usd": gft_1k_capital, "daily_pnl": gft_1k.get('daily_pnl', 0), "total_pnl": gft_1k.get('total_pnl', 0), "daily_dd_used_pct": 0, "max_dd_used_pct": 0, "status": "ACTIVE_NO_TRADES", "priority": 1},
            "nse_fyers": {"capital_inr": 26000, "capital_usd": 310, "open_trades": 38, "status": "EXIT_TRACKING_CHECK_NEEDED", "priority": 2},
            "gft_5k_2step": {"capital_usd": gft_5k_capital, "total_pnl": gft_5k_pnl, "phase1_target": 400, "phase1_progress": gft_5k_pnl, "phase1_remaining": round(400 - gft_5k_pnl, 2), "phase1_pct": round((gft_5k_pnl + 400) / 400 * 100, 1), "status": "BEHIND", "priority": 3},
            "ftmo_10k": {"capital_usd": ftmo_capital, "total_pnl": ftmo_pnl, "target": 500, "remaining": max(0, round(500 - ftmo_pnl, 2)), "deadline": "June 6 2026", "days_left": 2, "status": "URGENT", "priority": 4},
        },
        "total_real_capital_usd": real_capital_usd,
        "total_prop_capital_usd": gft_5k_capital + ftmo_capital,
        "agent_costs": {"monthly_usd": 0.0, "note": "Groq free tier"},
        "infrastructure_fund": {"source": "GFT $5K master after passing", "amount_expected": 5000, "status": "pending_phase1"},
        "saas_projection": {"gate_open": False, "gate_condition": "NSE WR ≥56% + GFT funded", "months_to_launch": 4, "path_to_1m": "Pass GFT $5K → infrastructure fund → NSE WR gate → brokera.in → 2500 users"},
        "financial_health": "YELLOW",
        "real_money_at_risk": real_capital_usd,
        "alerts": ["FTMO deadline June 6 — 2 days", "GFT $5K H4 violation cost -$33", "NSE exit tracking needs verification"],
        "cfo_summary": f"4 accounts active. Real money: ${real_capital_usd} (GFT $1K + NSE ₹26K). Prop challenges: GFT $5K at -$33 (need +$433 for Phase 1), FTMO needs +$608 by June 6. Agent cost: $0/month.",
    }

    try:
        raw = call_agent('ledger', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['alerts'].append(str(e))
        result = fallback

    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(result.get('financial_health', ''), "⚪")
    with open(REPORTS_DIR / 'cost_report.md', 'w', encoding='utf-8') as f:
        f.write(f"# LEDGER CFO Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"## {icon} {result.get('financial_health','?')}\n\n")
        f.write(f"**Total Real Money at Risk:** ${result.get('real_money_at_risk', 0)}\n")
        f.write(f"**Total Prop Capital:** ${result.get('total_prop_capital_usd', 0)}\n")
        f.write(f"**Agent Cost:** ${result.get('agent_costs',{}).get('monthly_usd',0):.2f}/month\n\n")

        f.write("## Account Status (Priority Order)\n")
        accounts = result.get('account_status', {})
        sorted_accounts = sorted(accounts.items(), key=lambda x: x[1].get('priority', 99) if isinstance(x[1], dict) else 99)
        for acct, data in sorted_accounts:
            if not isinstance(data, dict):
                continue
            status = data.get('status', '?')
            si = "🔴" if any(x in status for x in ['URGENT','CRITICAL','VIOLATION']) else "🟡" if any(x in status for x in ['BEHIND','AT_RISK','CHECK']) else "🟢"
            f.write(f"\n### #{data.get('priority','?')} {si} {acct.upper().replace('_',' ')}\n")
            for k, v in data.items():
                if k not in ('priority', 'actions'):
                    f.write(f"- **{k}:** {v}\n")

        infra = result.get('infrastructure_fund', {})
        f.write(f"\n## 🏗️ Infrastructure Fund\n")
        f.write(f"- Source: {infra.get('source','')}\n")
        f.write(f"- Expected: ${infra.get('amount_expected',0)}\n")
        f.write(f"- Status: {infra.get('status','')}\n")

        saas = result.get('saas_projection', {})
        f.write(f"\n## 🚀 SaaS Gate\n")
        f.write(f"- Open: {'YES' if saas.get('gate_open') else 'NO'}\n")
        f.write(f"- Condition: {saas.get('gate_condition','')}\n")
        f.write(f"- Months to launch: {saas.get('months_to_launch','')}\n")
        f.write(f"- Path to $1M: {saas.get('path_to_1m','')}\n")

        if result.get('alerts'):
            f.write(f"\n## ⚠️ Alerts\n")
            for a in result['alerts']:
                f.write(f"- {a}\n")

        f.write(f"\n## CFO Summary\n{result.get('cfo_summary','')}\n")

    print(f"[LEDGER] Real money at risk: ${result.get('real_money_at_risk',0)} | Prop capital: ${result.get('total_prop_capital_usd',0)} | Agent cost: $0/mo")
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, default=str))
