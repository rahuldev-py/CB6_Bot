# CB6 Quantum — Infrastructure Scale Plan
**Agent:** ATLAS + TITAN + FORGE
**Phase:** 6 — Infrastructure Architecture
**Date:** 2026-06-05

---

## Design Philosophy

1. **Start cheap, scale deliberately** — don't over-engineer for users you don't have yet
2. **Stateless services** — every service must be horizontally scalable from Day 1
3. **Failure isolation** — a crashed ML worker must not bring down signal delivery
4. **Ops-light** — minimal manual intervention required to keep system running
5. **Security-first** — encrypted secrets, API key rotation, no credentials in code

---

## Wave 1 — Single VPS / Local Hybrid
**Target users:** 0–100  
**Cost:** ₹3,000–₹8,000/month  
**Timeline:** Launch → Month 3

### Architecture

```
[Windows 11 Local Machine — Rahul's PC]
├── CB6 Trading Engine (NSE + Forex)
├── MT5 Platform (GFT accounts)
├── ML Training Jobs
└── Telegram Bots (NSE + Forex)

[DigitalOcean / Hetzner VPS — $20–$40/month]
├── FastAPI Backend (uvicorn)
├── PostgreSQL Database
├── Redis Cache
├── Celery Worker (signal generation)
├── Next.js Frontend (static build)
└── Nginx (reverse proxy + SSL)

[Cloudflare]
└── DNS + CDN + SSL termination (free tier)
```

### What runs where
- Trading engine stays local (MT5 requires Windows, low-latency)
- Web dashboard + API on VPS (users access this)
- Telegram bots on VPS (24/7 uptime, not dependent on local PC)

### VPS Specs (Wave 1)
- CPU: 2 vCPU
- RAM: 4GB
- Disk: 80GB SSD
- OS: Ubuntu 22.04 LTS
- Cost: ~$24/month (DigitalOcean Basic) or €8/month (Hetzner CX21)

### Services
```yaml
nginx:         reverse proxy, SSL (Let's Encrypt), rate limiting
fastapi:       uvicorn, 2 workers, port 8000
postgresql:    port 5432, local socket connection
redis:         port 6379, local socket connection
celery:        1 worker, beats scheduler for cron tasks
nextjs:        built static, served by nginx
```

---

## Wave 2 — Cloud Server + Managed Database
**Target users:** 100–500  
**Cost:** ₹15,000–₹40,000/month  
**Timeline:** Month 3–8

### Changes from Wave 1

```
[Separate services begin]

[DigitalOcean Droplet — 4 vCPU, 8GB RAM — $48/month]
├── FastAPI Backend (4 uvicorn workers)
├── Celery Workers (2 workers)
└── Nginx

[DigitalOcean Managed PostgreSQL — $25/month]
└── Primary DB + automated backups

[DigitalOcean Managed Redis — $15/month]
└── Cache + Celery broker

[DigitalOcean Spaces / S3]
└── Report PDFs, model files, backups
```

### Why managed database at 100 users?
- Automated backups (point-in-time recovery)
- Failover handled by provider
- Connection pooling via PgBouncer included
- No manual DB ops for founder

### Monitoring added in Wave 2
- UptimeRobot: HTTP checks every 5 min (free tier)
- Sentry: Error tracking for Python backend + Next.js frontend
- Telegram alert: Any 5xx errors → admin Telegram DM

### Security additions
- Secrets: Environment variables via DigitalOcean App Platform
- API keys: Stored in PostgreSQL, encrypted at rest (AES-256)
- SSL: Cloudflare → Nginx → FastAPI (end-to-end)
- Rate limiting: Nginx + FastAPI middleware (100 req/min per IP)
- Fail2ban: SSH brute-force protection

---

## Wave 3 — Separated Services
**Target users:** 500–2,500  
**Cost:** ₹60,000–₹1,50,000/month  
**Timeline:** Month 8–18

### Architecture separation

```
[Load Balancer — Nginx / DigitalOcean LB]
        |
    ─────────────────────────────────
    |           |          |
[API Server]  [Worker]  [Frontend CDN]
    |           |
[PostgreSQL] [Redis]
    
[Separate microservices]
├── api-server        FastAPI, handles HTTP requests
├── signal-worker     Celery, generates + grades signals
├── ml-worker         Celery, runs ML inference jobs
├── report-worker     Celery, generates PDFs + weekly reports
├── telegram-worker   Celery, sends Telegram alerts
├── frontend          Next.js, served via Vercel or CDN
└── admin-api         Separate FastAPI instance (admin panel)
```

### Infrastructure breakdown

| Service | Spec | Cost |
|---|---|---|
| API Server (2×) | 2 vCPU, 4GB | $48 × 2 |
| Signal Worker (2×) | 2 vCPU, 4GB | $48 × 2 |
| ML Worker (1×) | 4 vCPU, 8GB (GPU optional) | $96 |
| Report Worker (1×) | 2 vCPU, 4GB | $48 |
| Telegram Worker (1×) | 1 vCPU, 2GB | $12 |
| Managed PostgreSQL | Primary + Standby | $100 |
| Managed Redis | Cluster | $60 |
| Object Storage | 100GB S3/Spaces | $25 |
| Load Balancer | DigitalOcean LB | $12 |
| Monitoring | Grafana Cloud free | $0 |
| CDN | Cloudflare Pro | $20 |
| **Total** | | **~$577/month (~₹48K)** |

### Deployment process (Wave 3)
- Docker containers for all services
- Docker Compose for local development
- GitHub Actions CI/CD pipeline
- Blue-green deployments (zero-downtime deploys)
- Rollback in < 5 minutes

---

## Wave 4 — Load Balancer + Auto-scaling
**Target users:** 2,500–10,000  
**Cost:** ₹2–5 lakh/month  
**Timeline:** Month 18–36

### Key additions

```
[Cloudflare WAF + CDN]
         |
[DigitalOcean Load Balancer]
         |
[API Server Pool — 2-10 instances auto-scaled]
         |
[PostgreSQL Read Replicas — 2 replicas for read scaling]
         |
[Redis Cluster — 3 nodes]
         |
[Celery Worker Pools — 5-20 workers per queue]
```

### Auto-scaling rules
- Scale out: CPU > 70% for 5 min → add instance
- Scale in: CPU < 30% for 15 min → remove instance
- Min instances: 2 (always-on for HA)
- Max instances: 10 (cost cap)

### Database scaling
- Primary: All writes
- Read replica 1: Dashboard queries
- Read replica 2: ML + analytics queries
- PgBouncer: Connection pooling (max 200 connections → 2000 app connections)

### CDN strategy
- All static assets (Next.js build) → Cloudflare CDN globally
- Report PDFs → Signed S3 URLs (time-limited, user-specific)
- API responses: Redis cache (60s TTL for signal data, 5min for market labels)

---

## Wave 5 — Multi-Region Reliability
**Target users:** 10,000+  
**Cost:** ₹5–15 lakh/month  
**Timeline:** Year 3–4

### Global architecture

```
[Global: Cloudflare Anycast DNS]
         |
    ─────────────────────────
    |                        |
[India Region]        [International Region]
 Mumbai DC               Frankfurt/Singapore DC
 Primary DB              Read replica + disaster recovery
 Primary workers         Low-latency for intl users
```

### Reliability targets (Wave 5)
- Uptime SLA: 99.9% (< 9 hours downtime/year)
- RPO (Recovery Point Objective): 1 hour
- RTO (Recovery Time Objective): 15 minutes
- Data backups: Hourly snapshots + daily full backup + weekly offsite

---

## Full Production Stack (Wave 3+)

| Component | Technology |
|---|---|
| Container runtime | Docker + Docker Compose |
| Container orchestration | Kubernetes (Wave 4+) or DigitalOcean App Platform |
| API framework | FastAPI (Python 3.11+) |
| Frontend | Next.js 14 + TailwindCSS |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| Task queue | Celery 5 + Redis broker |
| ML inference | Python + PyTorch/TensorFlow (existing CB6 models) |
| Object storage | DigitalOcean Spaces (S3-compatible) |
| Search | PostgreSQL full-text search (sufficient to 10K users) |
| Monitoring | Grafana + Prometheus + Loki |
| Error tracking | Sentry (self-hosted or cloud) |
| Analytics | PostHog (self-hosted) |
| CI/CD | GitHub Actions → Docker Hub → DigitalOcean |
| DNS + WAF | Cloudflare |
| SSL | Cloudflare → Let's Encrypt |
| Load balancer | Nginx (Wave 1-3) → DigitalOcean LB (Wave 4+) |
| Secrets | Doppler or DigitalOcean App Platform env vars |

---

## Security Architecture

### Authentication
- JWT tokens (15-min access, 30-day refresh)
- Refresh token rotation on use
- Device fingerprinting for suspicious login alerts
- 2FA (TOTP) for admin and Elite users (optional for others)

### Data security
- PostgreSQL: Column-level encryption for sensitive fields (broker API keys, payment tokens)
- Redis: Auth required, TLS in production
- S3/Spaces: Private buckets, signed URLs only
- Secrets: Never in code, always in env vars, rotated quarterly

### Network security
- Cloudflare WAF: Block common attack patterns (SQLi, XSS, etc.)
- Rate limiting: API (100 req/min), auth (5 attempts/15 min), signals (50 req/min)
- Fail2ban: SSH protection
- Firewall: Only ports 80, 443, 22 open; DB/Redis internal-only
- VPC: All internal services on private network (no public IPs)

### Compliance
- DPDP Act (India's data protection law): User consent, data deletion rights
- No PAN/Aadhaar storage — only broker account IDs (opaque identifiers)
- SEBI: Platform positioned as analytics tool, not registered advisor
- GST: GST-compliant invoicing for Indian users

---

## Disaster Recovery Plan

| Scenario | Detection | Response | Recovery Time |
|---|---|---|---|
| VPS crash | UptimeRobot alert → Telegram | Restart droplet / spin new instance from snapshot | < 15 min |
| Database corruption | Sentry alert | Restore from last hourly snapshot | < 1 hour |
| Celery worker stuck | Grafana alert (queue depth > 100) | Auto-restart via Docker health check | < 2 min |
| DDoS attack | Cloudflare auto-blocks | Enable Under Attack mode | Immediate |
| Telegram bot rate-limit | Bot alert failure | Switch to backup bot token | < 5 min |
| Payment provider outage | Webhook failure alert | Razorpay → Stripe fallback (Wave 2+) | < 30 min |
| ML model crash | Health check failure | Disable ML widget, continue signal delivery | < 1 min |

---

## Cost Summary by Wave

| Wave | Users | Monthly Cost | Monthly Revenue (est.) | Margin |
|---|---|---|---|---|
| Wave 1 | 0–100 | ₹8,000 | ₹1.5L | 95% |
| Wave 2 | 100–500 | ₹40,000 | ₹12L | 97% |
| Wave 3 | 500–2,500 | ₹1.5L | ₹60L | 97.5% |
| Wave 4 | 2,500–10,000 | ₹5L | ₹3 crore | 98.3% |
| Wave 5 | 10,000+ | ₹15L | ₹8 crore | 98.1% |

*SaaS gross margins improve with scale — infrastructure is the smallest cost at scale.*

---

*Report generated by ATLAS + TITAN + FORGE agents*
*Next: Trading Engine Optimization → agent_reports/trading_optimization_plan.md*
