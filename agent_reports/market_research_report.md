# CB6 Quantum — Market Research Report
**Agent:** REACH + ECHO + LEDGER + NEXUS
**Phase:** 1 — Competitive Intelligence
**Date:** 2026-06-05
**Sources:** Live web research (June 2026) + platform analysis

---

## Executive Summary

The Indian algo trading software market is estimated at ₹500–₹2,000 crore annually and growing rapidly post-SEBI's April 2026 retail algo trading regulations. The dominant players (Tradetron, AlgoTest, Streak) are **tool vendors** — they sell builders, backtests, and execution pipes. **None sell intelligence, risk governance, or prop-firm discipline systems.** This is CB6 Quantum's market entry vector.

---

## 1. TRADETRON (tradetron.tech)

### What They Claim
Cloud-native, no-code algo strategy builder and marketplace. Supports multi-asset trading (equities, options, futures, commodities, crypto, currencies) across 8 exchanges and 35+ brokers. Claims: 1 lakh+ traders, 11,000+ strategies deployed, 1.5 million trades/month.

### Pricing
| Plan | Price |
|------|-------|
| Free | 10 strategies, paper trading only |
| Starter | ~₹300/month |
| Retail / Retail+ | ~₹1,000–₹2,000/month |
| Creator / Creator+ | Up to ₹15,000/month |
| Annual discount | 25% off |

### User Base
1 lakh+ (100,000+) self-reported traders. Revenue estimated ~$5.9M/year (Growjo). 35 employees. Bootstrapped.

### Weak Points
- **SEBI regulatory fire:** 120+ brokers received show-cause notices for Tradetron API integrations (2024); escalated to adjudication orders March 2026 — live deployment now at legal risk for many users
- Paper vs. live execution gap — documented user complaints of profitable paper trades with simultaneous live losses on same strategy
- No charting tools — purely a builder
- Cannot place limit orders — market orders only
- No risk calculator, position sizing, or diversification analysis
- No risk enforcement (daily DD, kill switches)
- Marketplace accountability gap: losses from third-party strategies → user redirected to creator, not platform

### Positioning
"Algo trading made easy for everyone" — strategy marketplace and builder, breadth over depth.

### Onboarding
Web sign-up → broker OAuth → choose/build strategy → paper → live.

### Trust Strategy
Testimonial-heavy, live trade counters (1.5M/month), broker partner badges, CEO in marketing.

### Disclaimers
Standard financial disclaimers. No platform-level accountability for marketplace losses. SEBI compliance under regulatory pressure.

### MRR Estimate
~$490K/month (~₹4 crore/month)

### CB6 Attack Vectors
- Zero risk governance (no kill switches, no drawdown enforcement)
- Marketplace trust liability (third-party losses, no recourse)
- SEBI compliance risk creates immediate switching intent
- No ML, no intelligence layer, no prop-firm tools

---

## 2. ALGOTEST (algotest.in)

### What They Claim
No-code algo platform for options backtesting, paper trading, and live execution. 20M+ backtests run, 12.5M+ live trades executed. YC S22 company. Visual drag-and-drop strategy builder.

### Pricing
| Plan | Price |
|------|-------|
| Starter | ₹499/month |
| Mid-tier | ~₹1,000–₹2,500/month |
| Full featured | ₹2,500–₹4,000/month (real all-in cost) |
| Backtest credits | 100 credits = 100 backtests; 25 free/week |

### User Base
YC-backed, founded 2021. Revenue ₹5.32 Cr/year (Mar 2025 filing). Estimated 46-person team. 20M+ backtests as key credibility metric.

### Weak Points
- No mobile app — desktop/browser only
- ₹499 headline price is misleading — real cost ₹2,500–₹4,000/month when credits included
- Options-centric — futures and other assets secondary
- No risk governance — no daily DD enforcement, no kill switches
- No ML/AI predictions — purely rule-based
- No prop-firm specific tooling

### Positioning
"Most competitively priced serious algo platform in India" — best price-to-feature for retail options traders who want backtesting + live execution without coding.

### Onboarding
Web sign-up → broker connect → drag-and-drop builder → backtest → paper → live.

### Trust Strategy
YC badge, transparent pricing docs, detailed backtest metrics, active blog.

### Disclaimers
Standard algo trading disclaimers. SEBI-compliant post-April 2026 regulations.

### MRR Estimate
~₹44 lakh/month (~$53K/month) based on annual filing.

### CB6 Attack Vectors
- Credit model hidden cost is a trust vulnerability
- No risk enforcement or account-level kill switches
- Mobile-absent — CB6's Telegram command plane is a direct differentiator
- No prop-firm awareness
- No ML memory or adaptive learning

---

## 3. TRADINGVIEW (tradingview.com)

### What They Claim
Global charting, social trading, and technical analysis platform. 50M+ registered users globally. Live charts for NSE, BSE, Nifty 50, Sensex, BankNifty.

### Pricing
| Plan | India Price |
|------|-------------|
| Free | Limited, ads |
| Essential | ~₹1,295/month |
| Plus | ~₹2,000/month |
| Premium | ~₹3,000–₹5,000/month |
| Annual discount | ~20–30% |

### User Base
50M+ registered globally. India is one of the largest markets. Revenue: hundreds of millions USD (not disclosed). Reportedly profitable.

### Weak Points
- Not an execution platform — by itself places no live trades in India
- Pine Script is its own language — coding barrier
- No risk governance
- No algo automation natively
- No ML/AI — indicator-based only
- NSE real-time data requires paid subscription (free is delayed)
- No F&O specific tools (Greeks, OI, payoff diagrams)

### Positioning
"The world's charting platform" — visualization and social analysis layer.

### Onboarding
Free sign-up → chart → indicators → alerts → optional broker connect.

### Trust Strategy
Massive community (50M+ users), published ideas, public performance records. Brand is the moat.

### Disclaimers
Standard financial disclaimers. Community content is user-generated.

### MRR Estimate
~$8–$15M/month (estimated).

### CB6 Attack Vectors
- No execution, no risk governance, no ML — CB6 is a complete layer above what TradingView provides
- Indians pay for global features they don't need
- No prop-firm tooling — complete blind spot

---

## 4. UTRADE ALGOS (utradealgos.com)

### What They Claim
"14 years of institutional pedigree." Multi-asset algo platform. No-code strategy builder (uTrade Originals pre-built strategies). AI-powered tools, exchange-approved strategies. Web + mobile.

### Pricing
Opaque — not publicly listed. Estimated ₹2,000–₹8,000/month. Requires demo/inquiry.

### User Base
Not publicly disclosed. Institutional heritage claim, but actual retail user count unknown. Smaller retail footprint vs. Tradetron/AlgoTest.

### Weak Points
- Opaque pricing forces sales conversation — kills self-serve conversion
- "AI tools" are vague in marketing — "data-driven insights" not well-defined
- No ML memory or adaptive learning
- No prop-firm specific tools or challenge-phase tracking
- Limited community vs. Tradetron's ecosystem
- Institutional complexity alienates retail users

### Positioning
"Institutional-grade algo trading for retail Indians" — credibility via institutional heritage.

### MRR Estimate
Unknown. Estimated ₹50–₹200 lakh/month based on niche positioning.

### CB6 Attack Vectors
- Opaque pricing is a conversion killer — CB6's transparent tier model wins
- "AI tools" claim is unsubstantiated — CB6's DNN+CNN+RNN shadow system is real
- No prop-firm tooling
- Institutional complexity vs. CB6's trader-first simplicity

---

## 5. STREAK (Zerodha-integrated)

### What They Claim
No-code algo platform integrated into Zerodha Kite. 100+ technical indicators. Strategy builder, backtesting, paper trading, live alerts and execution.

### Pricing
| Plan | Price |
|------|-------|
| Basic | ₹350–₹550/month |
| Premium | ₹630–₹900/month |
| Ultimate | ~₹1,400/month |
| Free trial | 7 days |

### User Base
Integrated with Zerodha (~9M customers) — largest distribution advantage of any Indian algo platform.

### Weak Points
- **Zerodha-only** — zero multi-broker support
- Indicator-based only — no ICT, no price action, no structure logic
- No ML/AI — purely rule-based
- No risk governance
- No prop-firm support — MT5 not possible
- Strategy complexity ceiling — complex multi-leg or multi-market strategies are difficult

### Positioning
"Algo trading for Zerodha users without coding" — distribution moat via broker tie-in.

### CB6 Attack Vectors
- Complete broker lock-in — CB6 supports Fyers + MT5 natively
- No ICT/price action strategies — massive gap for structure traders
- No risk governance automation
- No prop-firm or funded account tooling

---

## 6. SENSIBULL

### What They Claim
India's largest options analytics platform. 500,000+ monthly active users. P&L curves, Greeks, IV analysis, OI data, payoff diagrams, virtual trading. Free for all Zerodha users (Feb 2024).

### Pricing
| Plan | Price |
|------|-------|
| Free | Basic OC, watchlist, FII/DII data |
| Lite | ~₹480–₹800/month |
| Pro | ~₹800–₹1,300/month |
| Zerodha users | Free (Zerodha covers cost) |

### User Base
500,000+ monthly active users — largest options analytics platform in India.

### Weak Points
- NSE options only — no futures, no forex, no commodities, no international
- No automation — analytics only
- No ML/AI adaptive models
- No risk governance automation
- Zero prop-firm relevance

### CB6 Attack Vectors
- Pure analytics vs. CB6's execution + governance + intelligence
- No automation whatsoever
- Zerodha dependency creates platform risk for non-Zerodha traders

---

## 7. QUANTMAN (quantman.in)

### What They Claim
Multi-asset algo platform (futures, options, equity, indices). Supports 60+ brokers. Execution under 100ms. Strategy builder + backtesting. Beginner-friendly.

### Pricing
| Plan | Price |
|------|-------|
| Basic | ~₹1,084–₹1,300/month |
| Premium | ~₹3,300/month |
| Free trial | 7 days |

### User Base
Smaller than Tradetron. Growing. One of the better SEBI-compliant platforms post-April 2026.

### Weak Points
- Smaller community = fewer marketplace strategies = less social proof
- No ML/AI
- No risk governance automation
- No prop-firm tooling

### CB6 Attack Vectors
- All the same gaps as Tradetron/AlgoTest — no intelligence, no risk governance, no prop-firm
- Smaller community than Tradetron = easier to pull users with superior product

---

## 8. BULL8 (bull8.ai)

### What They Claim
"Institutional-grade algorithmic trading for India's retail investors." SEBI-approved framework. Pre-built professionally-tested strategies. Newer entrant (~Sept 2025 public launch).

### Pricing
Not publicly listed. Estimated ₹1,000–₹5,000/month.

### Weak Points
- Very early stage — unproven at scale
- Opaque pricing
- "Big data intelligence" is marketing language, not genuine ML
- No prop-firm tooling
- Press release-driven marketing vs. organic community trust

### CB6 Attack Vectors
- All credibility gaps of a new entrant
- CB6 has real trading performance (GFT + NSE) — Bull8 has press releases

---

## 9. QUANTCONNECT (quantconnect.com)

### What They Claim
Open-source quant research and live trading platform. 400TB+ historical data, 40+ data vendors, 483,000 registered users globally, $45B notional volume/month. Python-only (LEAN engine).

### Pricing
| Plan | Price |
|------|-------|
| Free | Unlimited backtesting |
| Researcher | ~$60/month |
| Institutional | From ~$80/month/user |
| Live nodes | $24–$1,000/month |

### User Base
483,000 registered users, 50,000 monthly active, $45B notional monthly volume.

### Weak Points
- Requires Python coding — excludes 95%+ of retail traders
- No Indian exchange support — NSE/BSE not natively supported
- No prop-firm specific features
- US-centric
- 20–30 second backtest startup delay

### CB6 Attack Vectors
- QuantConnect has zero Indian market coverage — complete non-competitor for India
- However: the 1% of Indian traders who use QuantConnect are sophisticated — CB6's intelligence layer (without coding requirement) could attract them

---

## 10. TRADE IDEAS (trade-ideas.com)

### What They Claim
US-market AI stock scanner. Holly AI system generates ~60 curated trade ideas before market open daily. 300+ pre-built scan strategies.

### Pricing
| Plan | Price |
|------|-------|
| Basic | ~$89–$127/month |
| Premium (Holly AI) | ~$178–$254/month |

### Weak Points
- US markets only — zero Indian market relevance
- $25K+ account requirement to make economic sense
- No risk governance tied to prop-firm rules
- Not suitable for options traders

### CB6 Attack Vectors
- Zero competitive overlap — Trade Ideas doesn't serve Indian markets
- However: Holly AI's "overnight scan → morning signal list" concept is worth studying for CB6's morning bias report feature

---

## Gap Analysis — The 10 Market Gaps CB6 Quantum Attacks

### GAP 1: Risk Governance Void (Largest Gap)
Every Indian platform — Tradetron, AlgoTest, Streak, QuantMan, Sensibull, uTrade, Bull8 — lacks automated, real-time risk governance tied to account drawdown limits. None enforce daily loss limits, reduce position sizing at warning thresholds, or kill trading when the account hits a hard stop.

**CB6 Answer:** Hard-coded daily DD limits (warn/$100, reduce/$140, halt/$170 for GFT $5K), total drawdown tracking, kill switches that fire before official limits are breached. **This is the single most defensible differentiator in the market.**

### GAP 2: ML Memory Gap
No Indian algo platform has a genuine machine learning layer that learns from the trader's own trade history, adapts signal quality scoring over time, and logs predictions in shadow mode.

**CB6 Answer:** DNN + CNN + RNN shadow system, auto-retraining every 20 trades/7 days, predictions logged per trade. **Architecturally unique in this market.**

### GAP 3: Prop-Firm Discipline Vacuum
Zero Indian platforms address the growing prop-firm community (GFT, FTMO, Funded Engineer, etc.). No software understands challenge phase rules, tracks phase targets, or enforces daily DD limits relative to funded account size.

**CB6 Answer:** Phase 1/2 tracking, internal guard alerts, trading day counter, drawdown pacing chart, best-day cap monitoring. **Entire niche is uncontested.**

### GAP 4: Telegram Command Plane
Most platforms offer a web dashboard or mobile app. No competitor offers a Telegram-based control plane with this depth.

**CB6 Answer:** 29 NSE commands + 17 Forex commands — monitor, intervene, pause, resume, check status, receive alerts on Telegram. **India's traders live on Telegram.**

### GAP 5: Multi-Market Coherence
No Indian platform simultaneously manages NSE index futures/options + forex prop firm accounts + ML models for both under one interface.

**CB6 Answer:** Fyers (NSE) + MT5 (GFT prop firm) + ML shadow system in one codebase. **Genuine multi-market intelligence.**

### GAP 6: ICT/Smart Money Strategy Gap
Streak, QuantMan, AlgoTest, and Tradetron are all indicator-based (RSI, MACD, EMA crossovers). None implement ICT concepts — CHoCH, BOS, FVG, order blocks, liquidity sweeps, kill zone timing, DOL targeting.

**CB6 Answer:** Silver Bullet + sweep+CHoCH+FVG combo natively built. **Serves the rapidly growing ICT community in India that no tool currently serves.**

### GAP 7: SEBI Compliance Uncertainty Creates Switching Intent
Tradetron's SEBI adjudication orders (120+ broker show-cause notices, March 2026) have created real uncertainty. Traders whose brokers are under action cannot use Tradetron for live deployment.

**CB6 Answer:** Direct broker connection (Fyers + MT5 direct, not through a third-party algo marketplace). No marketplace liability. SEBI-aware positioning as analytics/education tool.

### GAP 8: Marketplace vs. Transparency Contrast
Tradetron's marketplace has no accountability for third-party strategy losses. This is a documented trust problem.

**CB6 Answer:** Personal trading command center — not a marketplace of other people's black boxes. Traders trade their own strategies with full transparency. "Your strategies, your control, your risk governance."

### GAP 9: Transparent Pricing Attack
When hidden costs are included, AlgoTest charges ₹2,500–₹4,000/month. Tradetron Creator+ is ₹15,000/month. uTrade is opaque.

**CB6 Answer:** Clear transparent pricing (₹999/₹2,999/₹7,999/₹14,999) with well-defined feature differences. No credit games. No hidden per-trade costs.

### GAP 10: Tools vs. Intelligence Positioning
The entire Indian algo market sells tools — builders, backtests, indicators, execution pipes. No competitor sells intelligence.

**CB6 Answer:** Adaptive ML models, agent-based analysis, risk governance with memory, prop-firm phase awareness, A+ setup scoring with similarity matching. **Category of one in the Indian market.**

---

## Market Size Estimates

| Segment | India Users | Addressable |
|---------|-------------|-------------|
| Active F&O traders | ~10 million | CB6's primary market |
| Algo platform users | ~1.5–2 million | Direct competitive market |
| Prop-firm challengers (India) | ~50,000–100,000 | CB6 Elite target |
| ICT/Smart Money community | ~200,000–500,000 | CB6 strategy differentiator |
| International prop-firm traders | ~2 million | CB6 International target |

**TAM (India F&O analytics/algo tools):** ~₹1,000–₹3,000 crore/year  
**SAM (CB6's realistic addressable market, 5 years):** ~₹100–₹500 crore/year  
**SOM (CB6's target, Year 3):** ₹30–₹50 crore/year

---

## Competitor Revenue Summary

| Platform | Est. MRR | Users |
|----------|---------|-------|
| Tradetron | ~₹4 crore | 1L+ |
| TradingView | ~₹65 crore (global) | 50M+ global |
| AlgoTest | ~₹44 lakh | Unknown |
| Sensibull | ~₹2–8 crore | 500K MAU |
| Streak | ~₹5–15 crore (Zerodha ecosystem) | 9M potential |
| QuantMan | ~₹30–80 lakh | Unknown |
| uTrade | ~₹50L–2 crore | Unknown |
| Bull8 | < ₹10 lakh | Early stage |

---

## Strategic Conclusion

**The Indian algo trading market is a mature tools market with no intelligence player.**

Every platform sells the same thing in different packaging: build a strategy, backtest it, run it live. None of them govern risk. None of them learn from trades. None of them serve prop-firm traders. None of them implement ICT/smart money methodology.

CB6 Quantum does not need to out-feature Tradetron's 35 broker integrations or AlgoTest's 20M backtests.

CB6 Quantum needs to own three categories that no competitor even participates in:
1. **Risk governance** (the killer feature no one has)
2. **ML memory** (the intelligence layer no one has)
3. **Prop-firm discipline** (the niche no one serves)

Win those three, and CB6 builds a defensible moat before any competitor can respond.

---

*Sources: Tradetron, AlgoTest, TradingView, uTrade, Streak, Sensibull, QuantMan, Bull8, QuantConnect, Trade Ideas — live web research June 2026 via REACH agent web search*

*Next: Product Positioning → agent_reports/product_positioning.md*
