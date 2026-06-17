# Security & Compliance Rules — Rahul (rahuldev-py)

> These are your professional guardrails. Follow every one. Breaking them can cost you clients, reputation, or worse.

---

## 1. No Guaranteed Profit Claims

**Rule:** Never promise, imply, or suggest that your automation will produce trading profits.

**Never say:**
- "My bot will make you money"
- "This system has X% win rate on live trades"
- "You'll profit from this"
- "This strategy works in live markets"

**Instead say:**
- "I build automation infrastructure"
- "I implement the logic you specify"
- "I build tools to execute your strategy faster and more consistently"
- "Past performance of any system is not indicative of future results"

**Why:** Financial advice regulation exists in every country. Implying profit guarantees exposes you to legal liability even if unintentional.

---

## 2. Never Handle Client Private Keys or Broker Passwords

**Rule:** Do not ask for, store, or transmit client broker passwords, private keys, or secret credentials.

**The right way:**
- Client adds their API key and secret to a `.env` file on their own machine
- You provide a `.env.example` file showing the format
- You never see the actual values
- If remote debugging is needed: client pastes error logs only, not credentials

**If a client insists on sharing credentials:**
```
I need your credentials to work on this. The safest way: add them to a .env file on your machine and share the file structure (without values). I'll write the code to read from environment variables and you fill in your actual values.

I don't store credentials on my systems and I recommend you never share them over chat.
```

**Why:** If a client's account gets compromised after you handled their credentials, you are implicated regardless of what actually happened.

---

## 3. Use Environment Variables, Always

**Rule:** Never hardcode API keys, passwords, account numbers, or any credentials in delivered code.

**Pattern to use in every project:**
```python
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BROKER_API_KEY")
API_SECRET = os.getenv("BROKER_API_SECRET")
```

**Always deliver:**
- `.env.example` with placeholder values showing the format
- Never the actual `.env` file
- A note in the README explaining how to fill in credentials

---

## 4. Use Read-Only API Access When Possible

**Rule:** When a project only needs data (prices, order history), request or configure read-only API keys, not trading-enabled keys.

**When to apply:**
- Data pipelines
- Portfolio dashboards
- Backtesting scripts that read order history
- Alert systems that only read prices

**How to communicate:**
```
For this project you only need read access, not trading access. When setting up your API key, set it to "read-only" or "data access only" — this limits the damage if the key is ever exposed.
```

---

## 5. No Financial Advice Positioning

**Rule:** Your work is engineering services. You are not a financial advisor, trading coach, signal provider, or investment manager.

**Never:**
- Tell a client what strategy to trade
- Suggest what markets or instruments to use
- Review whether their trading approach is "good"
- Imply that implementing their strategy means you endorse it

**Always:**
- Implement exactly what the client specifies
- If a client asks "what do you think of this strategy?" — "I implement strategies, I don't evaluate them. That's for a trading coach or analyst."

**Why:** Providing trading advice without a financial license is illegal in most countries.

---

## 6. No Scraping Without Checking Terms of Service

**Rule:** Before building any web scraper, check the target site's ToS and robots.txt.

**How to check:**
1. Visit `[site.com]/robots.txt`
2. Check `[site.com]/terms` for scraping restrictions
3. If prohibited: tell the client you can't scrape that site

**If prohibited:**
```
That site's Terms of Service prohibit automated scraping. I can't build a scraper for it.

Alternative approaches:
- Check if they have an official API
- Check if the data is available from a third-party provider
- Use only publicly accessible, unrestricted data
```

---

## 7. No Handling of Client Live Orders Without Their Explicit Trigger

**Rule:** Never build a system that places live orders on a client's account without an explicit, deliberate trigger from the client.

**What this means:**
- Always build paper mode first, test thoroughly, then add live mode behind a flag
- Live mode should require a conscious configuration change: `LIVE_TRADING=true` in `.env`
- Never flip live mode without the client's knowledge and confirmation
- Document the switch clearly in your README

---

## 8. Prop Firm Rules — Know the Constraints

**Rule:** If a client is building automation for a prop firm account (FTMO, MyForexFunds, GFT, Apex, etc.), understand the specific rules before building.

**Common prop firm rules to be aware of:**
- Daily loss limits (hard stops must be enforced in code, not discipline)
- Maximum drawdown limits
- Minimum trading day requirements
- News blackout windows
- Weekend hold restrictions
- Prohibited strategies (grid, martingale, latency arbitrage)

**Your responsibility:** If a client gives you their prop firm rules, implement them correctly. If a client doesn't share the rules, ask for them. You don't want to build a system that blows a prop firm account because you didn't know a rule existed.

---

## 9. Delivery Security Checklist

Before sending any project to a client:

- [ ] No hardcoded credentials in the code
- [ ] `.env.example` file included
- [ ] `.gitignore` includes `.env`, `*.log`, `state.json`, `__pycache__`
- [ ] No test credentials or your own API keys accidentally left in the code
- [ ] Code doesn't log sensitive data (API keys, passwords, account numbers)
- [ ] If the project accesses a live trading account: paper mode default, live mode behind a flag

---

## 10. Client Data — What You Do and Don't Keep

**Do NOT keep:**
- Client broker credentials
- Client trade history
- Client account numbers or balances
- Screenshots of client trading accounts

**You CAN keep:**
- Your own copies of the code you wrote (useful for portfolio — sanitize before sharing)
- General architecture notes (no client-specific data)
- Code patterns you developed (these are your IP)

---

## Summary Card (stick this somewhere visible)

```
1. No profit guarantees — ever
2. No client credentials on your systems
3. .env files for everything
4. Read-only keys when possible
5. Engineering only — no trading advice
6. Check ToS before scraping
7. Live mode behind an explicit flag
8. Know the prop firm rules before building
9. Sanitize all code before delivery
10. Keep no client personal or financial data
```
