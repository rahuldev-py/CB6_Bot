"""
CB6 SOVEREIGN — Agent Configuration
All agents powered by Groq (free tier, $0/month)
Models: llama-3.3-70b-versatile (strategy) + llama-3.1-8b-instant (execution)
"""
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

GROQ_KEY       = os.getenv('GROQ_API_KEY', '')
TELEGRAM_TOKEN = os.getenv('FOREX_TELEGRAM_TOKEN', '')
TELEGRAM_CHAT  = os.getenv('FOREX_TELEGRAM_CHAT_ID', '')

CB6_ROOT    = Path(__file__).parent.parent
REPORTS_DIR = CB6_ROOT / 'agent_reports'
MEMORY_DIR  = Path(__file__).parent / 'memory'
REPORTS_DIR.mkdir(exist_ok=True)

# Model routing
# llama-3.3-70b-versatile → strategic + large context (CEO, CTO, Quant, Risk)
# llama-3.1-8b-instant    → fast execution (content, growth, docs)
MODELS = {
    'nexus':    'llama-3.3-70b-versatile',   # CEO — full synthesis
    'atlas':    'llama-3.3-70b-versatile',   # CTO — code analysis
    'sentinel': 'llama-3.3-70b-versatile',   # Risk Audit — no shortcuts
    'cipher':   'llama-3.3-70b-versatile',   # Quant — large data context
    'shadow':   'llama-3.3-70b-versatile',   # ML — needs reasoning
    'forge':    'llama-3.3-70b-versatile',   # Engineer — code quality
    'ledger':   'llama-3.1-8b-instant',      # CFO — numbers only
    'echo':     'llama-3.1-8b-instant',      # Content — fast
    'reach':    'llama-3.1-8b-instant',      # Growth — fast
    'brief':    'llama-3.1-8b-instant',      # Docs — fast
}

# All 4 account rules — SENTINEL checks these
ACCOUNT_RULES = {
    'nse_fyers': {
        'instruments': 'index_futures_options_only',
        'allowed': ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'],
        'banned': ['equity', 'stocks', 'crypto'],
        'h4_bias_required': True,
    },
    'gft_1k_instant': {
        'daily_dd_limit': 30,
        'max_dd_limit':   60,
        'max_risk_usd':   2.50,
        'max_lot':        0.01,
        'disabled_symbols': ['XAUUSD'],
        'h4_bias_required': True,
    },
    'gft_5k_2step': {
        'daily_loss_limit': 200,
        'max_total_loss':   500,
        'phase1_target':    400,
        'phase2_target':    300,
        'disabled_symbols': ['XAUUSD'],
        'h4_bias_required': True,
    },
    'ftmo_10k': {
        'daily_loss_limit': 300,
        'best_day_cap':     250,
        'max_drawdown':     1000,
        'profit_target':    500,
        'disabled_symbols': [],
        'h4_bias_required': True,
    },
}


def call_agent(agent_name: str, system: str, user: str) -> str:
    """
    Central Groq caller used by every agent.
    Returns raw response text. Agent parses JSON itself.
    Uses response_format=json_object for reliable JSON output.
    """
    from groq import Groq
    client = Groq(api_key=GROQ_KEY)

    resp = client.chat.completions.create(
        model=MODELS[agent_name],
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=4096,
    )
    return resp.choices[0].message.content


def safe_parse(raw: str, fallback: dict) -> dict:
    """Parse JSON response, return fallback on failure."""
    try:
        return json.loads(raw)
    except Exception as e:
        fallback['_parse_error'] = str(e)
        return fallback
