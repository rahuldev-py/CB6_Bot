"""
ECHO — CB6 Content Writer
Generates daily brand content for CB6 Quantum toward brokera.in launch.
"""
import json
from datetime import datetime

from agents.config import call_agent, safe_parse, REPORTS_DIR

SYSTEM = """You are ECHO, the Content Writer of CB6 Quantum.
You create daily content to build the CB6 Quantum brand.

Brand: CB6 Quantum — algo trading for Indian NSE + Forex prop firms.
Audience: Indian retail traders wanting automation.
Tone: Expert, educational, trustworthy — never hype, never guarantee profits.
Goal: Build authority before brokera.in SaaS launch.

Rules:
- Never reveal live signals or specific entries
- Never claim guaranteed profits
- Focus on education: ICT, prop firm tips, algo trading basics
- Subtle CTA toward CB6 Quantum / brokera.in

Return JSON only:
{
  "date": "YYYY-MM-DD",
  "linkedin_post": {
    "hook": "attention-grabbing first line",
    "body": "full post body",
    "cta": "call to action",
    "hashtags": []
  },
  "twitter_thread": ["tweet1 max 280 chars", "tweet2", "tweet3"],
  "blog_idea": {
    "title": "blog title",
    "outline": ["section1", "section2", "section3"],
    "keywords": ["kw1", "kw2"]
  }
}"""


def run(topic: str = None, quant_insights: list = None) -> dict:
    topic = topic or "algo trading for Indian retail traders — prop firm challenges"
    insights = f"\nRecent insights (anonymized): {quant_insights[:2]}" if quant_insights else ""

    user = f"""Today: {datetime.now().strftime('%Y-%m-%d')}
Topic: {topic}{insights}

Create today's content package. Educational, not salesy. Return JSON."""

    fallback = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "linkedin_post": {"hook": "", "body": "Content unavailable", "cta": "", "hashtags": []},
        "twitter_thread": [],
        "blog_idea": {"title": "", "outline": [], "keywords": []},
    }

    try:
        raw = call_agent('echo', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['linkedin_post']['body'] = str(e)
        result = fallback

    ts = datetime.now().strftime('%Y-%m-%d')
    with open(REPORTS_DIR / 'content_calendar.md', 'a', encoding='utf-8') as f:
        li = result.get('linkedin_post', {})
        f.write(f"\n---\n## {ts} — LinkedIn\n**{li.get('hook','')}**\n{li.get('body','')}\n{li.get('cta','')}\n")
        f.write(f"{' '.join(li.get('hashtags',[]))}\n")
        tw = result.get('twitter_thread', [])
        if tw:
            f.write(f"\n### Twitter Thread\n")
            for i, t in enumerate(tw, 1):
                f.write(f"{i}. {t}\n")

    print(f"[ECHO] Content ready for {ts}")
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
