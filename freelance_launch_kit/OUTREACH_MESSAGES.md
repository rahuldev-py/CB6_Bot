# Outreach Messages — Rahul (rahuldev-py)

> Copy-paste ready. Non-spammy, value-first. Never mass-blast the same message.
> Rule: Read the community rules before posting. Getting banned from a subreddit on Day 1 costs you.

---

## REDDIT

### r/forhire — "For Hire" Post

```
[FOR HIRE] Python Developer — Trading Bots, Broker APIs (Fyers/Zerodha/MT5), Discord Bots, Automation | $99–$500 | Fixed Price

---

What I build:

• Trading bots — MT5 Expert Advisor control, TradingView webhook → Discord/Telegram/MT5
• Broker API integrations — Fyers, Zerodha Kite, MetaTrader 5, Binance, Alpaca
• Prop firm systems — risk controls, daily loss guards, position sizing
• Discord / Telegram bots — alert forwarding, command interfaces, real-time notifications
• Python automation — data pipelines, scheduled scripts, report generators
• VPS deployment — 24/7 uptime, auto-restart, monitoring

Background:
I've built and run a live algorithmic trading system across Indian NSE markets and Forex prop firm accounts. Real capital, real markets, production uptime. I understand what breaks in live systems and how to fix it.

Pricing:
• Quick bug fix: $49–99
• TradingView webhook: $149
• Bot debug & audit: $199
• Full integration: $299–800
• Custom system: $500–1,500

Fixed price. 7-day support. Working code, not prototypes.

Portfolio & case study: github.com/rahuldev-py
Email: zzu4309@gmail.com
```

---

### r/algotrading — Value Post (not a pitch)

```
Built a prop firm risk control system in Python — here's what I learned

I recently finished building an automated risk enforcement system for prop firm trading accounts. Thought I'd share what I built and why, since I see a lot of people asking about prop firm automation here.

The problem: prop firms have hard daily loss limits. If you rely on manual discipline to stop when you're close to the limit, you'll eventually miss it. Especially under drawdown pressure.

What I built:
- 15-second polling loop on MT5 Python API reading live P&L
- Three-tier response: warning alert → 50% lot size reduction → full daily halt
- State persisted across restarts so a crash doesn't reset the counter
- Fires BEFORE official limits (internal guards at 70% and 85% of daily limit)

The key insight: the guards need to fire before the official limit, not at it. If you stop at $200 daily loss on a $5K account, you've already lost $200. The system should slow down at $140 and stop at $170.

Anyone else building prop firm automation? Happy to discuss the architecture.

---
(I do freelance trading automation projects if you ever need something built — github.com/rahuldev-py)
```

---

### r/learnpython — Helpful Answer Strategy

Do NOT post a "hire me" post here. Instead:
1. Search for recent posts asking about MT5, Fyers, trading bots, webhooks
2. Answer the question properly and in detail
3. End with: "I work on trading automation professionally if you ever need something more complex built — happy to help."

This builds trust. 3 good answers in r/learnpython is worth more than 10 spam posts.

---

### r/FundedTrader / r/PropFirmTrading — Specific Pitch

```
Anyone struggling with prop firm automation? I've built risk management systems for MT5 accounts

Background: I built automated risk controls for my own GFT prop firm accounts — daily loss guards, lot reduction triggers, auto-halt before official limits. Zero rule violations.

If your prop firm bot is:
- Placing orders at the wrong size
- Not stopping when daily loss limit approaches
- Losing state after a restart
- Missing trades due to MT5 connection drops

I can help. DM me or email zzu4309@gmail.com. I work on fixed-price projects.

github.com/rahuldev-py
```

---

## DISCORD

### Introduction Message (for trading servers)

```
Hey everyone — just joined. I'm Rahul, a Python developer specialising in trading automation.

Background: I've built and run a live algo trading system across Indian NSE markets and Forex prop firm accounts. MT5 Python integrations, broker APIs (Fyers, Zerodha), TradingView webhooks, risk systems.

Happy to answer questions about trading automation in Python. Also open to freelance projects if anyone needs something built.

github.com/rahuldev-py
```

---

### Response to "Anyone know Python?" in trading Discord

```
I can help. What specifically are you trying to build?

(I do trading automation freelance — MT5, Fyers, Zerodha, TradingView webhooks, Discord bots. If it's a quick question, happy to answer here. If you need something built, happy to quote.)
```

---

### Prop Firm Discord — Specific Value Post

```
For anyone running MT5 bots on prop firm accounts:

The most common mistake I see is relying on the MT5 terminal's built-in stop to enforce daily loss limits. The problem: if your VPS restarts or your EA crashes during a losing streak, the counter resets.

The fix: track cumulative daily P&L in a persistent file (or SQLite), load it on startup, and add it to live P&L before making any trading decisions. That way a crash + restart doesn't give you a "fresh start" and risk another max loss day.

Happy to elaborate if anyone wants more detail.
```

---

## LINKEDIN

### DM to Trading Community Members

```
Hi [NAME],

I noticed your background in [TRADING/FINTECH/ALGO TRADING] — I'm a Python developer specialising in trading automation (MT5, Fyers API, TradingView webhooks, broker integrations).

I've been running a live algo trading system and I'm now taking freelance projects. I thought there might be some overlap with what you do.

Worth connecting?

— Rahul
```

---

### DM to Signal Providers / Trading Channel Owners

```
Hi [NAME],

I follow your [channel/signals] — good stuff.

I'm a Python developer who builds trading automation systems. A lot of signal providers I've talked to spend too much time manually executing signals or copying them to Discord/Telegram. I automate that entire workflow.

Would automation like that be useful for your setup? Happy to explain what's possible.

— Rahul
```

---

### Comment Strategy on LinkedIn

Find posts about:
- Trading bots breaking
- Prop firm challenges
- Fyers/Zerodha API issues
- TradingView automation

Leave a specific, helpful comment. Don't pitch. Just be useful.

**Example:**
Post: "Anyone know how to auto-refresh Fyers API token?"

Your comment:
```
Yes — Fyers uses OAuth2 with a short-lived access token. The fix is to store the refresh token, check token expiry before each request, and call the refresh endpoint if it's expired or close to expiry. You can wrap this in a TokenManager class that handles it transparently. Happy to share a code pattern if useful.
```

If they reply asking for more: "Happy to go deeper on this — or if you need it built, I do this kind of thing for clients."

---

## UPWORK — Follow-Up Message (after no reply in 48h)

```
Hi [CLIENT NAME],

Just following up on my proposal for [JOB TITLE]. 

If you're still looking for help with this, I'm available to start today.

Happy to answer any questions before you decide.

— Rahul
```

---

## TWITTER / X

### Bio
```
Python developer. Trading bots, broker APIs, MT5, Fyers, Zerodha. I build automation that runs on real capital. Freelance. DMs open.
```

### Post — Available announcement
```
I build trading bots and broker API integrations in Python.

MT5, Fyers, Zerodha, TradingView webhooks, Discord/Telegram alerts.

I've run live trading systems on real capital. Now taking freelance clients.

Fixed price. Fast delivery. DMs open.

github.com/rahuldev-py
```

### Post — Specific offer
```
If your TradingView alerts fire and nothing happens automatically:

I build the webhook → Discord/Telegram/MT5 pipeline.
Python, deployed 24/7 on VPS.

$149. 2 days.

DM me.
```

### Post — Engagement bait (gets replies and visibility)
```
What's the most annoying thing about trading automation that nobody talks about?

For me: token refresh. Every broker handles auth differently. Fyers, Zerodha, MT5 — all different flows, all expire at different times. Always breaks during market hours.
```

---

## INDIAN TELEGRAM GROUPS

Search for and join groups about:
- Fyers API users
- Zerodha Kite Connect developers
- NIFTY/BANKNIFTY algo trading
- Algo trading India

**Introduction message:**
```
Hi all, I'm Rahul — Python developer specialising in trading automation for Indian markets.

I've built live trading systems for NSE (Fyers API) and Forex (MT5). I help traders automate their setups — Fyers/Zerodha integration, TradingView webhooks, Telegram alert bots, risk controls.

Open to freelance projects. If anyone needs Python automation built, feel free to DM me.
```

**Rule:** Read 2 days of conversation before posting. Only post if the group allows self-promotion. Never mass-post the same message across 10 groups on the same day.

---

## FREELANCER.COM

Same proposal templates as Upwork. Key differences:
- Freelancer has a bidding system — bid at the mid-range of their budget, not the maximum
- Include "I can start today" if you can — freelancer buyers often want instant response
- Response time is crucial on Freelancer — reply within 1 hour of any employer message
