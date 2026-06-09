"""
REACH — CB6 Growth Hacker
Growth strategy toward 100 → 1000 → 2500 users for brokera.in.
All output is recommendations — Rahul approves before any action.
"""
import json
from datetime import datetime

from agents.config import call_agent, safe_parse, REPORTS_DIR

SYSTEM = """You are REACH, the Growth Hacker of CB6 Quantum.
Goal: fastest path to 2500 paying users for brokera.in at ₹2999/month.

Target audience: Indian retail traders on Zerodha, Fyers, Angel One.
Channels: Telegram groups, Twitter/X, YouTube, Reddit, LinkedIn, TradingView.

Return JSON only:
{
  "date": "YYYY-MM-DD",
  "top_channels": [
    {"channel": "name", "audience": "size estimate", "strategy": "how to engage", "priority": "HIGH/MEDIUM/LOW"}
  ],
  "quick_wins": ["actionable steps this week"],
  "partnership_ideas": [
    {"partner": "who", "value_exchange": "what both get", "approach": "how to reach"}
  ],
  "growth_summary": "one paragraph for board report"
}"""


def run(phase: str = "pre-launch") -> dict:
    user = f"""Today: {datetime.now().strftime('%Y-%m-%d')}
Phase: {phase} — building audience before brokera.in launch.
SaaS gate: NSE WR ≥56% + GFT funded account profitable (not yet open).
Focus on organic, low-cost channels. Return growth strategy as JSON."""

    fallback = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "top_channels": [],
        "quick_wins": [],
        "partnership_ideas": [],
        "growth_summary": "Growth assessment unavailable.",
    }

    try:
        raw = call_agent('reach', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['growth_summary'] = str(e)
        result = fallback

    with open(REPORTS_DIR / 'growth_strategy.md', 'a', encoding='utf-8') as f:
        f.write(f"\n---\n## Growth — {datetime.now().strftime('%Y-%m-%d')}\n")
        for ch in result.get('top_channels', [])[:5]:
            f.write(f"- **[{ch.get('priority','?')}] {ch.get('channel','')}**: {ch.get('strategy','')}\n")
        f.write(f"\n### Quick Wins\n")
        for w in result.get('quick_wins', []):
            f.write(f"- {w}\n")

    print(f"[REACH] Channels: {len(result.get('top_channels',[]))} | Quick wins: {len(result.get('quick_wins',[]))}")
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
