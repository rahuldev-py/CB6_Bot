# Delivery Workflow — Rahul (rahuldev-py)

> This is how I run every project. Follow this process on every client, every time.
> Deviating from this is how scope creep, unpaid work, and bad reviews happen.

---

## The 7-Step Process

```
1. Discovery → 2. Scope Lock → 3. Payment → 4. Build → 5. Test → 6. Handover → 7. Support Window
```

---

## Step 1 — Discovery (before any commitment)

**What happens:**
- Client contacts you with a problem
- You send the Client Intake Form questions
- You read their answers carefully
- You ask 1–2 follow-up questions if anything is unclear

**Your output:** A clear understanding of what they need and what it will take.

**Time limit:** 24 hours max. Don't drag out discovery.

**Rule:** Do NOT quote a price until you have enough information to be confident in it. A wrong quote is worse than a slow quote.

---

## Step 2 — Scope Lock (written agreement before payment)

**What happens:**
- You write a scope statement in plain English
- Send it to the client before they pay
- Client confirms in writing (even a "yes" message counts)

**Scope statement template:**
```
Here is what I will deliver:
✅ [DELIVERABLE 1]
✅ [DELIVERABLE 2]
✅ [DELIVERABLE 3]

This does NOT include:
❌ [EXCLUSION 1]
❌ [EXCLUSION 2]

Timeline: [X] days from payment.
Price: $[AMOUNT] fixed.
Payment: [50% upfront / 100% upfront for under $300].

Reply "confirmed" and I'll send the payment link.
```

**Rule:** If scope is not confirmed in writing, don't start work. This is your protection against "I thought it included X."

---

## Step 3 — Payment (before work starts)

**Payment rules:**
- Under $300: 100% upfront
- $300–$800: 50% upfront, 50% on delivery
- $800+: 40% upfront, 30% on milestone, 30% on delivery
- Emergency debug: 100% upfront, no exceptions

**How to collect payment:**
- Upwork: Create a contract with milestones — client funds escrow
- Direct: Send Wise invoice or PayPal invoice
- Never start work on a "I'll pay you when it's done" promise

**If they push back on upfront payment:**
```
I take upfront payment on all projects — this is standard for fixed-price freelance work.
For Upwork contracts, your payment goes into escrow and is only released when you confirm delivery.
If that doesn't work for you, I understand, but it's a firm policy for me.
```

---

## Step 4 — Build

**Workflow:**
- Build in a clean folder, not directly on the client's live system
- Commit to git as you go (even a private repo on your account)
- Use environment variables — never hardcode credentials
- Write at least basic error logging
- Test with realistic inputs (including edge cases) as you build

**Communication rules:**
- Send a progress update every 24 hours: one sentence is enough
- If you hit a blocker, tell the client within 4 hours — don't go silent
- If scope needs to expand, stop and discuss before continuing

**"I'm stuck" message template:**
```
Quick update: I've completed [X] and am working on [Y].
I hit one issue: [DESCRIBE BLOCKER].
My plan: [HOW YOU'LL SOLVE IT].
No change to timeline/price — just keeping you posted.
```

---

## Step 5 — Test Before Delivery

**Minimum tests before sending anything:**
- [ ] Does it run without errors on a clean install?
- [ ] Does it handle a bad input gracefully (no silent failures)?
- [ ] Does it do what the scope statement said?
- [ ] Have you tested the most common error case?
- [ ] Does it work on the client's environment, not just yours?

**For trading bots specifically:**
- [ ] Paper mode test first (never test order execution on a live account)
- [ ] Confirm error messages are human-readable
- [ ] Confirm it reconnects after a simulated disconnect
- [ ] Confirm the risk guard actually stops trading when it should

---

## Step 6 — Handover

**Handover package (every project):**
- Working code in a zip or GitHub repo
- README with: how to install, how to configure, how to run, common errors
- If credentials are needed: `.env.example` file (never the actual `.env`)
- Loom video walkthrough (optional add-on, +$49)

**Handover message template:**
```
Hi [CLIENT NAME],

Your project is complete. Here's what I've delivered:

✅ [DELIVERABLE 1] — works as described in scope
✅ [DELIVERABLE 2]
✅ [DELIVERABLE 3]

Files attached / shared via [METHOD].

README is included with setup instructions.

Please test it and let me know if anything doesn't match the agreed scope.
If everything looks good, please release the payment / leave a review.

I'm available for questions for the next 7 days.

— Rahul
```

**After handover:**
- Wait for client confirmation before marking complete on Upwork
- Ask for a review: "If everything worked as expected, a review on Upwork would really help me — takes 30 seconds."

---

## Step 7 — Support Window

**Standard support:** 7 days after delivery for bug fixes related to the delivered scope.

**What's covered in support:**
- Bugs in the code I wrote
- Setup issues on their specific environment
- One clarification call (30 min max)

**What's NOT covered:**
- New features
- Changes to scope after delivery
- Issues caused by their environment (wrong Python version, missing API access)
- Anything outside the agreed deliverables

**Support window message for out-of-scope requests:**
```
That's outside the original scope, but I can add it.
Quick estimate: [PRICE] and [X] days.
Want me to proceed?
```

---

## Anti-Scope-Creep Rules

These are non-negotiable. Follow every one.

1. **Written scope before payment.** No exceptions.
2. **Any change to scope = new price discussion.** Even a "small" change.
3. **"Can you also..." is always a new line item.** Respond with an estimate, not "sure."
4. **Unlimited revisions is not a thing.** 2 revision rounds are included. After that, quote separately.
5. **Client approval closes a milestone.** Once they say "looks good," that scope is done.
6. **No free "just a quick thing."** Your time is your income. A 2-hour "quick thing" is $100.

**Script for scope creep:**
```
Happy to add that! That's outside the original scope, so there'd be an additional cost.
My estimate for that addition: $[X]. Want to add it to the project?
```

---

## Difficult Client Handling

**Client disappears after payment (no responses):**
- Message on Day 3, Day 7, Day 14
- After 21 days with no response: deliver what you have + close the contract

**Client says "it doesn't work" without detail:**
```
I want to make sure this is fixed. Can you share:
1. The exact error message
2. What steps you took when it didn't work
3. Your OS and Python version

With that I can reproduce and fix it.
```

**Client demands work outside scope:**
Refer to written scope agreement. Quote separately. Stay calm.

**Client threatens a bad review without valid reason:**
```
I'm sorry you feel that way. I delivered exactly what we agreed in the scope statement on [DATE].
I'm happy to fix any bugs in the delivered code. New features are a separate project.
```

---

## Project Tracker (use for every project)

```
Project: [CLIENT NAME] — [PROJECT NAME]
Start date: [DATE]
Deadline: [DATE]
Price: $[AMOUNT]
Paid: $[AMOUNT RECEIVED]
Scope confirmed: [YES/NO]

Progress:
[ ] Discovery complete
[ ] Scope locked and confirmed
[ ] Payment received
[ ] Build complete
[ ] Tested
[ ] Delivered
[ ] Review received
```
