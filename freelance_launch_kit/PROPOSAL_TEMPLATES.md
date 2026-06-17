# Proposal Templates — Rahul (rahuldev-py)

> Copy-paste ready. Replace [CLIENT_NAME], [THEIR_PROBLEM], [PRICE] with real values.
> Rule: Read the job post 3 times before sending. Reference something specific from their post.

---

## HOW TO USE THESE

1. Read the client's job post carefully
2. Pick the matching template below
3. Replace the bracketed placeholders
4. Add ONE specific detail from their post in the first line
5. Send. Do not overthink it.

**Winning formula:** Show you understand their problem → Show you've solved it before → Give a clear next step.

---

---

# 1. Python Automation

### Short (under 100 words)
```
I saw you need [THEIR_SPECIFIC_TASK]. I've built similar automation systems in Python — most recently a [RELEVANT_EXAMPLE, e.g. "scheduled data pipeline that ran daily across 3 markets"].

I can deliver a working script in [TIMELINE] with error handling, logging, and a README so you can run it yourself.

Fixed price: $[PRICE]. No hourly surprises.

Can we do a quick 10-minute call to confirm scope?

— Rahul | github.com/rahuldev-py
```

### Medium
```
Hi,

I read your post about [THEIR_PROBLEM]. This is exactly the kind of automation I build.

My background: I've built production Python automation systems including trading pipelines, scheduled data processors, and API integrations running 24/7 on VPS. I don't deliver prototypes — I deliver production-ready code with proper error handling and logging.

For your project I would:
1. [STEP 1 specific to their problem]
2. [STEP 2]
3. Test it thoroughly and hand over with a clear README

Timeline: [X] days. Fixed price: $[PRICE].

No scope creep — we agree on deliverables upfront and I stick to them.

— Rahul
github.com/rahuldev-py
```

### Technical
```
Hi [CLIENT_NAME],

Your requirement: [QUOTE THEIR EXACT REQUIREMENT].

My approach:
- [Technical approach specific to their problem, e.g. "Use schedule library + retry logic to handle API timeouts"]
- [Error handling approach]
- [Logging/monitoring approach]

I've solved similar problems in a production trading system that handles [RELEVANT SCALE, e.g. "5,000+ API calls per day"]. The code pattern you need is [PATTERN NAME] and it takes roughly [TIME] to implement correctly.

Deliverable: Working Python script + README. Tested before handover.
Timeline: [X] days.
Price: $[PRICE] fixed.

Want to see a relevant code snippet first? Happy to share.

— Rahul | github.com/rahuldev-py
```

---

# 2. Discord Bot

### Short
```
I build Discord bots for trading communities. I can have your [SPECIFIC BOT FUNCTION] working in 2–3 days.

Tech: Python + discord.py + your specific requirements. Deployed on your VPS or mine.

$[PRICE] fixed. What commands do you need first?

— Rahul | github.com/rahuldev-py
```

### Medium
```
Hi,

I've built Telegram and Discord bots for trading systems — command handlers, real-time alert forwarding, role-based access, slash commands.

For your Discord bot, I'd build:
- [FEATURE 1 from their post]
- [FEATURE 2]
- Role permission control so only authorised members trigger commands
- Deployed on VPS so it runs 24/7

Delivered in 2–3 days. $[PRICE] fixed. 7-day support after delivery.

— Rahul
```

### Technical
```
Hi,

For your Discord bot I'd use discord.py with slash commands (not prefix commands — they're deprecated for verified bots).

Key implementation points:
- Event listeners for [THEIR USE CASE]
- [on_message / slash_command] handlers
- Rate limit handling (Discord has strict limits on message sends)
- Persistent data storage: SQLite for [their data] vs in-memory for ephemeral state

I've built a Telegram bot with 30+ commands for a live trading system. Discord architecture is similar. I'll apply the same patterns here.

Timeline: 2–3 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

---

# 3. TradingView Webhook

### Short
```
TradingView → [THEIR DESTINATION: Discord/Telegram/MT5] webhooks are something I set up regularly. I can have yours working and tested within 2 days.

$149 fixed. Includes webhook server, alert parser, delivery to your channel, and a test run with live alerts.

— Rahul
```

### Medium
```
Hi,

Your TradingView alert format + webhook to [DESTINATION] is a setup I've built before. Here's exactly what I'd deliver:

1. Python Flask webhook server that receives TradingView POST requests
2. Parser for your alert JSON format (share your Pine Script alert format and I'll match it exactly)
3. Forwards to [Discord/Telegram/MT5] with your custom message format
4. Runs 24/7 on VPS (or yours if you prefer)

Tested with live alerts before handover. $149 fixed, 2 days.

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

For TradingView → [DESTINATION]:

TradingView sends a POST request to your webhook URL with the alert message as JSON or plain text (depending on your Pine Script setup). I'll build:

- Flask/FastAPI endpoint (HTTPS required — I'll set up ngrok or use your VPS domain)
- Alert parser matching your exact format (share an example alert string)
- [DESTINATION] sender using [discord.py webhook / python-telegram-bot / MT5 Python API]
- Error handling: malformed alerts don't crash the server, duplicate alerts are deduplicated

One common issue: TradingView retries failed webhooks. I'll add idempotency so duplicate orders don't fire.

Timeline: 2 days. Price: $149.

— Rahul | github.com/rahuldev-py
```

---

# 4. MT5 Issue Fix

### Short
```
MT5 + Python issues are my speciality — I run live trading systems on MT5. Tell me what error you're seeing and I'll fix it. $[PRICE], 1–2 days.

— Rahul | github.com/rahuldev-py
```

### Medium
```
Hi,

I use the MetaTrader5 Python library daily in a live trading system. Whatever your MT5 issue — connection drops, order rejection codes, symbol not found, wrong lot calculation — I've likely already debugged it.

Share your error message or code and I'll give you a quick diagnosis in my reply before you even hire me.

Fix price: $[PRICE]. Turnaround: 1–2 days.

— Rahul
```

### Technical
```
Hi,

The MT5 Python library has some well-known quirks:
- initialize() silently fails without raising — you need to check return value explicitly
- symbol_select() must be called before get_rates() or order_send()
- order_send() returns a result struct, not a bool — you must check result.retcode == TRADE_RETCODE_DONE
- Connection drops aren't surfaced as exceptions — you need a reconnect polling loop

From your description, your issue sounds like [DIAGNOSIS BASED ON THEIR POST]. I can fix this in [X] hours.

Price: $[PRICE]. Share your code and I'll start immediately.

— Rahul | github.com/rahuldev-py
```

---

# 5. Broker API Integration

### Short
```
I've integrated Fyers, Zerodha, and MT5 APIs in live trading systems. I can wire up [THEIR BROKER] for you in 3–5 days. Fixed price: $[PRICE].

— Rahul
```

### Medium
```
Hi,

Broker API integration is one of my core skills. I've built wrappers for Fyers, Zerodha Kite, and MetaTrader 5 — including auth flows, token refresh, order placement, and position management.

For [THEIR BROKER] I'd deliver:
- Auth + auto token refresh (no manual re-login)
- Order send / cancel / modify
- Position + order book fetch
- Error handling with retries for rate limits and network drops

Timeline: 3–5 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

For [BROKER] API integration I'd structure it as:

1. Auth layer: OAuth2 / API key flow + token storage in .env + auto-refresh before expiry
2. Session management: requests.Session with retry adapter (handles rate limits + timeouts)
3. API methods: place_order(), cancel_order(), get_positions(), get_order_book()
4. Error handler: maps [BROKER]'s error codes to meaningful exceptions
5. Test suite: paper trading mode to validate before going live

Common pitfall with [BROKER]: [SPECIFIC KNOWN ISSUE if I know it, else: "token expiry during market hours if refresh isn't handled correctly"].

Timeline: 3–5 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

---

# 6. Web Scraping

### Short
```
I can scrape [THEIR TARGET SITE] and deliver clean structured data in [FORMAT]. $[PRICE], [X] days.

— Rahul
```

### Medium
```
Hi,

I can build a scraper for [THEIR SITE] that:
- Runs on a schedule (daily/hourly/real-time)
- Handles pagination and dynamic content (JavaScript-rendered pages use Playwright/Selenium)
- Exports to [CSV / JSON / Google Sheet / database]
- Includes error handling and retry logic

Timeline: 2–4 days. Price: $[PRICE].

Note: I only scrape sites where automated access is permitted by their Terms of Service.

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

For [THEIR SITE]:
- Static HTML → requests + BeautifulSoup (fastest, most reliable)
- JavaScript-rendered → Playwright (chromium headless, handles SPAs)
- Logged-in scraping → session cookies or Playwright auth state

I'll check robots.txt first. If their rate limits are strict I'll add exponential backoff. Output: [FORMAT].

Timeline: 2–4 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

---

# 7. VPS Deployment

### Short
```
I can deploy your Python bot on a VPS with auto-restart, logging, and 24/7 uptime. $99, done in 1 day.

— Rahul
```

### Medium
```
Hi,

I'll take your Python bot and deploy it on a Linux VPS so it runs 24/7:

1. Server setup (DigitalOcean / Vultr / AWS Lightsail)
2. Python environment + dependencies
3. systemd service for auto-restart on crash
4. Log rotation so disk doesn't fill
5. Health check script with Telegram alert if it goes down

Done in 1–2 days. $99 fixed. You'll have SSH access and a one-page runbook.

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

For VPS deployment of your Python bot:

- systemd unit file with Restart=always + RestartSec=10
- Environment variables via /etc/systemd/system/[service].env (not hardcoded)
- logrotate config: daily rotation, 7-day retention
- Monitoring: simple cron + curl health check → Telegram alert if process dies
- Swap space configured if your bot is memory-intensive

I run my own trading bots on VPS with this exact stack. They restart automatically on crash and I get notified within 60 seconds of any downtime.

$99. 1 day. Done.

— Rahul | github.com/rahuldev-py
```

---

# 8. Bot Debugging

### Short
```
Send me your code and error logs. I'll find the bug. $[PRICE], 24–48 hours.

— Rahul | github.com/rahuldev-py
```

### Medium
```
Hi,

Trading bot debugging is something I do well — I've debugged my own live systems under pressure and found issues others missed.

Share your code, the error message or wrong behaviour, and any logs. I'll:
1. Reproduce the issue
2. Find root cause (not just the symptom)
3. Fix it
4. Test and show you evidence

Price: $[PRICE]. If it's a quick fix it might be less.

— Rahul
```

### Technical
```
Hi,

From your description, the most likely causes are:
1. [HYPOTHESIS 1 based on their post, e.g. "Race condition between order send and position check"]
2. [HYPOTHESIS 2, e.g. "State not persisted across restarts"]
3. [HYPOTHESIS 3]

To confirm, I need: the relevant code section + any error traceback + what the bot was supposed to do vs what it did.

I debug by reproducing first — if I can't reproduce it, I can't guarantee the fix holds. Give me the details and I'll start immediately.

Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

---

# 9. AI Agent Workflow

### Short
```
I build AI agent workflows using Python + LLMs. For your use case I'd connect [DATA SOURCE] → [LLM] → [OUTPUT]. $[PRICE], [X] days.

— Rahul | github.com/rahuldev-py
```

### Medium
```
Hi,

I've built AI-assisted workflows for trading research — connecting live market data to LLMs and routing outputs to Telegram and reports.

For your project:
- Data source: [THEIR SOURCE]
- LLM: Claude API / OpenAI (your choice or I'll recommend)
- Trigger: scheduled or on-event
- Output: [THEIR DESIRED OUTPUT]

Timeline: 3–5 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

For an AI agent workflow on your use case:

Architecture:
- Trigger: [cron / webhook / event]
- Data ingestion: [THEIR SOURCE] → normalised text/JSON
- LLM call: Claude API with structured output (tool_use for reliable JSON parsing)
- Output formatter → [Telegram / Discord / email / Google Sheet]

Key consideration: LLM calls cost money per token. I'll design the prompt to be concise and add token tracking so you can monitor costs.

Timeline: 3–5 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

---

# 10. Crypto Bot Fix

### Short
```
I can debug your crypto bot. Share the code and error. $[PRICE], 24–48 hours.

— Rahul
```

### Medium
```
Hi,

Crypto bot issues I've seen and fixed: wrong symbol format, WebSocket disconnects, order size below minimum, API key permission errors, timestamp drift causing signature failures.

From your description it sounds like [BEST GUESS]. Share the error and code and I'll confirm before we start.

Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

Common crypto bot failures by exchange:

Binance: timestamp drift (use /fapi/v1/time sync), recv_window too short, wrong base URL for futures vs spot
Bybit: v5 API migration breaking old endpoints, different order parameter names
Coinbase: OAuth2 scope errors, rate limit on REST (use WebSocket instead)

Your issue: [DIAGNOSIS from their description].

Fix: [SPECIFIC FIX]. I can implement this in [X] hours.

Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

---

# 11. Algo Trading System Audit

### Short
```
I can audit your trading system — code quality, risk controls, edge cases, deployment setup. Written report + fixes. $[PRICE].

— Rahul | github.com/rahuldev-py
```

### Medium
```
Hi,

A trading system audit from me covers:
- Code quality: logic errors, race conditions, missing error handling
- Risk controls: are hard stops actually hard? are they enforced in code or just "intended"?
- Data integrity: can bad data cause a wrong trade?
- Deployment: will it survive a crash, restart, or network drop?
- Edge cases: what happens at market open/close, on a news spike, if the broker rejects an order?

Delivered as a written report + fix implementation for critical issues.

Price: $[PRICE]. Timeline: 3–5 days.

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

My audit process:

1. Static analysis: read all code for logic errors, unsafe patterns (eval, hardcoded credentials, missing try/except on order sends)
2. Race condition check: any shared state accessed from multiple threads without locks?
3. Risk control validation: trace the code path from "daily loss limit" value to actual trading halt — is it enforced or just a comment?
4. State persistence test: simulate a crash mid-trade — what happens to open positions?
5. API error handling: what happens when the broker returns a 429, 500, or timeout?

Written report with severity ratings (Critical / High / Medium / Low) + fix PR.

Price: $[PRICE]. Timeline: 3–5 days.

— Rahul | github.com/rahuldev-py
```

---

# 12. Data Pipeline

### Short
```
I build data pipelines in Python — fetch, clean, store, schedule. $[PRICE], [X] days.

— Rahul
```

### Medium
```
Hi,

Data pipeline for [THEIR USE CASE]:
- Source: [THEIR DATA SOURCE — API / CSV / database / scrape]
- Transform: clean, normalise, validate
- Store: [SQLite / PostgreSQL / CSV / Google Sheet]
- Schedule: run automatically every [X]
- Alert: notify you if pipeline fails or data looks wrong

Delivered with documentation and a health check. $[PRICE], [X] days.

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

For a [FREQUENCY] data pipeline from [SOURCE]:

- Fetch layer: [requests / WebSocket / SDK] with retry + exponential backoff
- Validation: schema check on incoming data (reject malformed rows, don't silently corrupt the DB)
- Transform: pandas for normalisation + derived columns
- Storage: SQLite for <1M rows (fast, zero infra); PostgreSQL for larger or multi-user
- Scheduler: APScheduler or cron + systemd

I've built this pattern for financial market data at [SCALE]. Same principles apply to your use case.

Price: $[PRICE]. Timeline: [X] days.

— Rahul | github.com/rahuldev-py
```

---

# 13. WebSocket Issue

### Short
```
WebSocket disconnects and reconnect issues are something I've solved in live trading systems. Share your code and I'll fix it. $[PRICE].

— Rahul
```

### Medium
```
Hi,

WebSocket issues in trading systems are critical — a disconnect means missed data means missed trades.

I've built WebSocket handlers with:
- Auto-reconnect with exponential backoff
- Ping/pong keepalive (most servers close idle connections after 30–60s)
- Message queue so data isn't lost during reconnect
- Heartbeat monitoring with alert if connection is silent for too long

Fix price: $[PRICE]. 1–2 days.

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

WebSocket stability issues typically come from:
1. No ping/pong — server closes idle connections silently after timeout
2. No reconnect loop — exception thrown, process dies or freezes
3. No message queue — data during reconnect window is lost
4. Thread safety — callback is called in a background thread but writes to shared state without a lock

Fix pattern I use:
```python
while True:
    try:
        ws = create_connection(url)
        while True:
            msg = ws.recv()
            process(msg)
    except Exception as e:
        log_error(e)
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)
```

This + ping thread + message queue covers 95% of WebSocket reliability issues.

Price: $[PRICE]. 1 day.

— Rahul | github.com/rahuldev-py
```

---

# 14. Backtesting Script

### Short
```
I build backtesting scripts in Python — strategy logic, performance metrics, trade log. $[PRICE], [X] days.

— Rahul
```

### Medium
```
Hi,

I can build a backtester for your strategy:
- Feed: historical OHLCV data (CSV, API, or I can source it)
- Strategy logic: implement your entry/exit rules exactly
- Output: trade log (entry, exit, P&L per trade), equity curve, win rate, max drawdown, Sharpe ratio
- No external library required — pure Python + pandas (faster to customise)

Timeline: 3–5 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

For your backtest I'll implement it as an event-driven backtester (not vectorised) — it processes bar by bar which avoids look-ahead bias that vectorised approaches often introduce.

Key metrics delivered:
- Win rate, avg win / avg loss, profit factor
- Max drawdown (absolute + percentage)
- Sharpe ratio (annualised)
- Trade log with every entry/exit/reason

Data: [SOURCE]. Strategy: [THEIR STRATEGY]. I'll implement it in code exactly as they describe and flag any ambiguities before starting.

Timeline: 3–5 days. Price: $[PRICE].

— Rahul | github.com/rahuldev-py
```

---

# 15. Urgent Bug Fix

### Short
```
Send me the error now. I'll start immediately. Emergency rate: $299 for 24-hour turnaround.

— Rahul
```

### Medium
```
Hi,

I take emergency jobs. If your bot is broken and money is on the line, here's how I work:

1. You share code + error + context
2. I confirm I can fix it within 1 hour of receiving
3. Payment upfront (Wise or Upwork escrow)
4. I start immediately, live updates every 2 hours
5. Fixed or full refund

Emergency rate: $299 for 24-hour window. Available now.

— Rahul | github.com/rahuldev-py
```

### Technical
```
Hi,

Emergency debugging. I need 3 things from you right now:
1. The exact error message or wrong behaviour (screenshot or paste)
2. The relevant code section (not the whole codebase — just what's failing)
3. What it was doing before it broke (last working state)

With those 3 things I can give you a diagnosis in under 30 minutes. If I can fix it, we agree price and I start. If I can't, I'll tell you honestly.

Available now. Emergency rate: $299 for 24h window.

— Rahul | github.com/rahuldev-py
```
