"""
set_bot_commands.py
Registers the /command menu for all three CB6 Quantum Telegram bots.
Run once after any command changes — updates appear instantly in Telegram.

Usage: python set_bot_commands.py

Bots:
  NSE bot   — TELEGRAM_BOT_TOKEN        (Indian markets)
  FTMO bot  — TELEGRAM_BOT_TOKEN_FTMO   (FTMO $10K only)
  GFT bot   — TELEGRAM_BOT_TOKEN_GFT    (GFT $5K 2-Step only)
"""

import os
import sys
import requests
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import dotenv_values
_env = dotenv_values('.env')
for k, v in _env.items():
    if k not in os.environ:
        os.environ[k] = v


def set_commands(token: str, commands: list, label: str):
    if not token or token.startswith('REPLACE_'):
        print(f"  SKIP {label} — token not set or placeholder")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/setMyCommands",
        json={"commands": commands},
        timeout=10,
    )
    if r.status_code == 200 and r.json().get("ok"):
        print(f"  OK   {label} — {len(commands)} commands registered")
    else:
        print(f"  FAIL {label} — {r.text}")


# ── Indian NSE Bot ─────────────────────────────────────────────────────────────
NSE_COMMANDS = [
    {"command": "start",            "description": "Overview + all commands"},
    {"command": "sb",               "description": "Trigger Silver Bullet scan now"},
    {"command": "scan",             "description": "Index futures scan (NIFTY/BNF/FIN/MID)"},
    {"command": "check",            "description": "Check a specific index — /check NIFTY"},
    {"command": "nse_status",       "description": "Bot health, windows, today's trades"},
    {"command": "trades",           "description": "Open index positions + live PnL"},
    {"command": "portfolio",        "description": "Capital & P&L summary"},
    {"command": "excel",            "description": "Download Excel dashboard"},
    {"command": "levels",           "description": "NIFTY ICT levels + buy/sell probability"},
    {"command": "brain",            "description": "Market bias + session score"},
    {"command": "options",          "description": "Strike selector — /options NIFTY"},
    {"command": "fiidii",           "description": "FII/DII flow data"},
    {"command": "expiry",           "description": "F&O expiry calendar"},
    {"command": "ml_scan",          "description": "Multi-TF ML scan — /ml_scan NIFTY or ALL"},
    {"command": "ml_status",        "description": "ML model accuracy + predictions"},
    {"command": "ml_train",         "description": "Force ML retrain now"},
    {"command": "ask",              "description": "Ask AI anything — /ask question"},
    {"command": "memory",           "description": "AI trade stats"},
    {"command": "learn",            "description": "Learned parameters"},
    {"command": "lessons",          "description": "Trade post-mortems"},
    {"command": "pattern",          "description": "Pattern library stats (WR by window)"},
    {"command": "reloadpatterns",   "description": "Reload pattern library"},
    {"command": "stop",             "description": "Halt trading today"},
    {"command": "resume",           "description": "Resume trading"},
    {"command": "eventmode",        "description": "Crisis filter — /eventmode on|off"},
    {"command": "execution_mode",   "description": "Execution gate config"},
    {"command": "execution_stats",  "description": "Execution validation stats"},
    {"command": "help",             "description": "ICT Silver Bullet strategy rules"},
    {"command": "info",             "description": "Full command reference"},
]

# ── FTMO Bot (FTMO $10K only) ──────────────────────────────────────────────────
FTMO_COMMANDS = [
    {"command": "start",                  "description": "FTMO overview + command list"},
    {"command": "fx_status",              "description": "FTMO engine health + heartbeat"},
    {"command": "fx_pnl",                 "description": "FTMO P&L + challenge progress"},
    {"command": "fx_ftmo",                "description": "FTMO rule tracker with progress bars"},
    {"command": "fx_terminals",           "description": "FTMO terminal isolation status"},
    {"command": "fx_positions",           "description": "FTMO open trades with live price + uPnL"},
    {"command": "fx_journal",             "description": "Last 5 FTMO closed trades"},
    {"command": "fx_lots",                "description": "FTMO lot sizes + risk per symbol"},
    {"command": "fx_exit",                "description": "Close FTMO trade manually — /fx_exit A"},
    {"command": "fx_stop",                "description": "Pause FTMO engine"},
    {"command": "fx_resume",              "description": "Resume FTMO engine"},
    {"command": "ml_status",              "description": "ML shadow accuracy + model status"},
    {"command": "ml_train",               "description": "Force ML retrain"},
    {"command": "forex_execution_mode",   "description": "FTMO execution gate config"},
    {"command": "forex_execution_stats",  "description": "FTMO execution validation stats"},
    {"command": "fx_help",                "description": "FTMO strategy rules + all commands"},
]

# ── GFT Bot (GFT $5K 2-Step only) ─────────────────────────────────────────────
GFT_COMMANDS = [
    {"command": "start",           "description": "GFT overview + command list"},
    {"command": "gft_status",      "description": "GFT engine health + heartbeat"},
    {"command": "gft_pnl",         "description": "GFT P&L + phase progress"},
    {"command": "gft_phase",       "description": "GFT phase tracker with progress bars"},
    {"command": "gft_terminal",    "description": "GFT terminal isolation status"},
    {"command": "gft_positions",   "description": "GFT open trades with live price + uPnL"},
    {"command": "gft_journal",     "description": "Last 5 GFT closed trades"},
    {"command": "gft_lots",        "description": "GFT lot sizes + risk per symbol"},
    {"command": "gft_exit",        "description": "Close GFT trade manually — /gft_exit A"},
    {"command": "gft_stop",        "description": "Pause GFT engine"},
    {"command": "gft_resume",      "description": "Resume GFT engine"},
    {"command": "ml_status",       "description": "ML shadow accuracy + model status"},
    {"command": "ml_train",        "description": "Force ML retrain"},
    {"command": "gft_help",        "description": "GFT strategy rules + all commands"},
]


print("Registering Telegram bot menus...\n")
set_commands(os.getenv("TELEGRAM_BOT_TOKEN",      ""), NSE_COMMANDS,  "NSE Bot   (Indian markets)")
set_commands(os.getenv("TELEGRAM_BOT_TOKEN_FTMO", ""), FTMO_COMMANDS, "FTMO Bot  (FTMO $10K only)")
set_commands(os.getenv("TELEGRAM_BOT_TOKEN_GFT",  ""), GFT_COMMANDS,  "GFT Bot   (GFT $5K 2-Step only)")
print("\nDone. Open Telegram and type / to see the updated menus.")
