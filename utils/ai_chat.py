# utils/ai_chat.py — CB6 Bot Live AI Trade Explainer (powered by Claude)
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger

_client           = None
_chat_history     = []   # rolling conversation context
MAX_HISTORY_TURNS = 6    # 3 full exchanges kept in context

SYSTEM_PROMPT = """You are CB6 Bot's personal trading assistant for Rahul Panchal, trading the ICT Silver Bullet strategy on NSE India (NIFTY / BANKNIFTY / equity).

You explain every trade decision the CB6 automated bot makes — the WHY, WHERE, and HOW — in clear, confident language.

=== ICT SILVER BULLET STRATEGY ===
The Silver Bullet is a time-based FVG setup. It only fires within two 60-minute windows per day.

Windows (IST):
- Morning Silver Bullet  : 10:00 – 11:00 IST
- Afternoon Silver Bullet: 13:30 – 14:30 IST

Do NOT trade before 10:00 AM. The 9:15 NSE open is the "Judas Swing" — smart money sweeps retail stops, then reverses. Wait for this to complete.

Setup Chain (3 steps, in order):
1. DRAW ON LIQUIDITY (DOL): Identify the nearest unswept swing high or low. This is the magnet — price will be drawn toward it. DOL tells you the DIRECTION.
2. MARKET STRUCTURE SHIFT (MSS): A candle closes beyond the last swing in the direction of the DOL. Confirms smart money has shifted. No MSS = no trade.
3. FAIR VALUE GAP (FVG): A 3-candle imbalance that forms AFTER the MSS. Enter on the FIRST touch of this FVG. The FVG is the entry zone, not a target.

=== ICT CONCEPTS ===
- Draw on Liquidity: Unswept swing highs/lows = stop clusters institutions will hunt. Price always moves toward the nearest DOL.
- MSS: Close beyond last swing = structure flip. Bullish MSS = close above last swing high. Bearish MSS = close below last swing low.
- FVG: Gap between candle[i-2].high and candle[i].low (bullish) or candle[i-2].low and candle[i].high (bearish). Entry is at the FVG, not after it fills.
- PDH/PDL: Previous Day High/Low — strongest DOL targets for the day.
- Judas Swing: Fake move at open (9:15) to trigger retail stops. Never trade this — wait for 10:00.

=== TRADE RULES ===
- Instruments: NIFTY / BANKNIFTY futures first; equity stocks secondary
- Options: ITM or ATM only | Delta must be 0.6–0.8 | OTM banned (theta kills RR)
- Entry: First touch of FVG after MSS — do not chase
- Stop Loss: FVG edge (the high of bullish FVG for buys, low of bearish FVG for sells)
- Target 1: 1:2 RR — exit 50% of position, move SL to break-even
- Target 2: 1:3 RR — primary target
- Target 3: DOL level — let remaining position run
- Theta rule: If price stays inside the FVG for more than 15 minutes without moving toward the target — EXIT. Time is money in Indian options.
- Risk: Max 1-2% capital per trade. Calculate lot size from SL distance.
- FII/DII: If FII is net buyers → prefer longs. Net sellers → prefer shorts.

=== YOUR COMMUNICATION STYLE ===
- Speak like an experienced ICT trader, not a textbook
- Be specific — mention actual price levels from the context
- Keep answers to 150–250 words for Telegram (punchy, no fluff)
- If the user asks a follow-up, remember the context
- If something in the trade looks risky, say so honestly

You have access to the bot's current state (trades, P&L, market conditions) in every message."""


def _get_client():
    global _client
    if _client is None:
        try:
            import anthropic
            key = os.getenv('ANTHROPIC_API_KEY', '')
            if not key:
                return None
            _client = anthropic.Anthropic(api_key=key)
        except Exception as e:
            logger.error(f"AI client init error: {e}")
    return _client


def _build_context() -> str:
    """Collect current bot state and format it for Claude's context window."""
    lines = []
    from datetime import datetime
    lines.append(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M IST')}")

    # Market conditions
    try:
        from data.fii_dii import get_market_bias_from_fii_dii
        bias, _ = get_market_bias_from_fii_dii()
        lines.append(f"FII/DII Market Bias: {bias}")
    except Exception:
        lines.append("FII/DII Bias: UNKNOWN")

    # Open trades
    try:
        from trader.paper_trader import load_state
        state  = load_state()
        cap    = state.get('capital', 0)
        avail  = state.get('available_capital', 0)
        pnl    = state.get('total_pnl', 0)
        open_t = state.get('open_trades', [])
        closed = state.get('closed_trades', [])

        lines.append(f"\nCapital: Rs {cap:,.0f} | Available: Rs {avail:,.0f} | Realized PnL: Rs {pnl:,.0f}")

        if open_t:
            lines.append(f"\nOPEN TRADES ({len(open_t)}):")
            for t in open_t:
                sym  = t['symbol'].replace('NSE:', '').replace('-EQ', '')
                hits = ', '.join(t.get('targets_hit', [])) or 'none'
                rpnl = t.get('realized_pnl', 0)
                upnl = t.get('pnl', 0)
                frvp = t.get('frvp') or {}
                frvp_str = (
                    f" | POC:{frvp.get('poc','-')} "
                    f"VAH:{frvp.get('vah','-')} VAL:{frvp.get('val','-')}"
                    if frvp else ""
                )
                psy = t.get('psychology') or {}
                psy_str = (
                    f" | Phase:{psy.get('phase','?')} Crowd:{psy.get('crowd','?')}"
                    if psy else ""
                )
                lines.append(
                    f"  [{t.get('direction','BUY')}] {sym} | TF:{t['timeframe']} | "
                    f"Entry:{t['entry_price']} SL:{t['current_sl']} "
                    f"T1:{t['target1']} T2:{t['target2']} T3:{t['target3']} | "
                    f"Qty:{t['quantity']} (orig:{t.get('original_quantity',t['quantity'])}) | "
                    f"Score:{t.get('confluence','?')}/10 | "
                    f"OTE:{t.get('in_ote',False)} FVG:{t.get('in_fvg',False)}"
                    f"{frvp_str}{psy_str} | "
                    f"Targets hit:{hits} | "
                    f"Realized PnL:Rs {rpnl:.0f} | Unrealized:Rs {upnl:.0f}"
                )
        else:
            lines.append("\nNo open trades.")

        if closed:
            lines.append(f"\nLAST 5 CLOSED TRADES:")
            for t in closed[-5:]:
                sym = t['symbol'].replace('NSE:', '').replace('-EQ', '')
                lines.append(
                    f"  {sym} {t.get('result','?')} | "
                    f"Entry:{t.get('entry_price')} → Exit:{t.get('exit_price')} | "
                    f"PnL:Rs {t.get('pnl',0):.0f} | {t.get('status','')}"
                )
    except Exception as e:
        lines.append(f"Trade state unavailable: {e}")

    # AI memory stats
    try:
        from data.bot_memory import load_memory
        m   = load_memory()
        tot = m.get('total_trades', 0)
        win = m.get('winning_trades', 0)
        wr  = round(win / tot * 100, 1) if tot > 0 else 0
        p   = m.get('learned_params', {})
        lines.append(
            f"\nAI Memory: {tot} trades | Win Rate: {wr}% | "
            f"Best hours: {p.get('best_hours',[])} | "
            f"Score threshold: {p.get('best_score_threshold',7)}"
        )
    except Exception:
        pass

    return "\n".join(lines)


def chat(user_message: str) -> str:
    """
    Multi-turn AI chat. Pass any question about the trade/strategy.
    Maintains last 3 exchanges in memory.
    Returns the assistant's reply string.
    """
    global _chat_history

    client = _get_client()
    if not client:
        return (
            "AI chat is offline.\n\n"
            "Add ANTHROPIC_API_KEY to your .env file:\n"
            "ANTHROPIC_API_KEY=sk-ant-..."
        )

    # Inject fresh context into each user turn (state changes between messages)
    context_text = _build_context()
    enriched_msg = (
        f"[LIVE BOT STATE]\n{context_text}\n\n"
        f"[QUESTION] {user_message}"
    )

    _chat_history.append({"role": "user", "content": enriched_msg})

    # Keep only last MAX_HISTORY_TURNS messages
    history = _chat_history[-MAX_HISTORY_TURNS:]

    try:
        import anthropic as _ant
        resp  = client.messages.create(
            model      = os.getenv('AI_CHAT_MODEL', 'claude-haiku-4-5-20251001'),
            max_tokens = 500,
            system     = SYSTEM_PROMPT,
            messages   = history
        )
        reply = resp.content[0].text
        _chat_history.append({"role": "assistant", "content": reply})
        # Trim history to prevent unbounded growth
        if len(_chat_history) > MAX_HISTORY_TURNS * 2:
            _chat_history = _chat_history[-MAX_HISTORY_TURNS:]
        return reply
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return f"AI error: {e}\nCheck your ANTHROPIC_API_KEY."


def clear_history():
    """Reset conversation history."""
    global _chat_history
    _chat_history = []
    return "Chat history cleared. Starting fresh conversation."


def is_available() -> bool:
    return _get_client() is not None
