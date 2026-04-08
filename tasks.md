# Implementation Phases — B2B AI SaaS Conversion System

## Phase 0: Foundation & Project Scaffolding (Weeks 1–2)

- [ ] **T-001**: Initialize monorepo structure
  - `/widget/pixel` — Vanilla JS tracking (~5kb gzipped)
  - `/widget/chat` — Preact chat UI (~35kb gzipped, lazy-loaded)
  - `/gateway` — Go WebSocket gateway (single binary)
  - `/backend` — Python FastAPI + workers (`pyproject.toml`)
  - `/dashboard` — Next.js 14 + TailwindCSS + Shadcn UI
  - `/infra` — Pulumi IaC definitions
  - `/shared` — Pydantic v2 data contracts
  - Root: `docker-compose.yml` (Postgres 16 + pgvector, Redis 7/Valkey, MinIO)

- [ ] **T-002**: Define Pydantic v2 data contracts in `/shared`
  - `TrackingEvent`, `IntentScore`, `ChatMessage`, `LeadProfile`, `TenantConfig`
  - All models enforce `tenant_id: UUID` for multi-tenancy
  - Monetary fields: `Decimal` via `condecimal()` — never float

- [ ] **T-003**: PostgreSQL schema + migrations (Alembic)
  - Tables: `tenants`, `visitors`, `tracking_events`, `intent_scores`, `conversations`, `messages`, `leads`, `integrations`, `knowledge_chunks`, `billing`
  - Enable `pgvector` extension on `knowledge_chunks`
  - RLS policies per `tenant_id` on every table
  - Partition `tracking_events` by tenant + month
  - All HNSW indexes via `CREATE INDEX CONCURRENTLY`

- [ ] **T-004**: CI/CD (GitHub Actions)
  - Lint: ruff + mypy (Python), eslint + tsc (TS), golangci-lint (Go)
  - `gitleaks` as merge gate — leaked secret = company-ending event
  - Pin all GH Actions to commit SHAs
  - Feature branch CI ≤ 90s (fail-fast ordering)
  - Widget has separate release pipeline with canary phase

- [ ] **T-005**: Local dev environment
  - `docker-compose.yml` with seed script: test tenant, sample KB, fake events
  - Hot-reload: uvicorn (Python), air (Go), Next.js built-in

**Milestone:** Monorepo builds, local dev stack runs, schema migrated, CI green.

---

## Phase 1: Widget & Event Ingestion (Weeks 3–4)

- [ ] **T-010**: Build tracking pixel (`/widget/pixel`)
  - Vanilla JS, < 5kb gzipped, zero dependencies
  - Tracks: page views, scroll depth (25/50/75/100%), clicks, time-on-page
  - Batch events (100ms debounce), send via `fetch` POST; `sendBeacon` on unload
  - Graceful degradation: top-level try/catch — never break host site
  - Versioned deploys (`/v1/pixel.js`), SRI hashes, canary rollout to 5% of tenants
  - CSP-compliant: no `eval()`, no inline styles

- [ ] **T-011**: Go WebSocket gateway (`/gateway`)
  - `nhooyr.io/websocket`, target 10k+ concurrent connections per pod
  - Auth: short-lived JWT per visitor session
  - Pure message relay — zero business logic, zero access to integration credentials
  - Horizontal scaling: sticky sessions via NLB, Redis pub/sub cross-instance
  - Auto-reconnect: client sends last-seen message ID

- [ ] **T-012**: Event ingestion API (FastAPI)
  - `POST /api/v1/events` — batch `TrackingEvent[]`, Pydantic validation
  - Rate limiting per tenant (Redis `redis-cell` GCRA)
  - Publish to Redis Stream `events:{tenant_id}`, respond 202 Accepted

- [ ] **T-013**: Event persistence worker
  - Consumer group on Redis Streams, batch INSERT via `asyncpg.copy`
  - Idempotency: `(tenant_id, visitor_id, timestamp, event_type)` unique constraint
  - Dead-letter queue for failed events

**Milestone:** Widget embeds on test site, streams events through Go gateway + FastAPI to Supabase.

---

## Phase 2: Intent Scoring Engine (Weeks 5–6)

- [ ] **T-020**: Rule-based scoring (v1)
  - Configurable per tenant (stored in `tenant_scoring_rules`)
  - Defaults: pricing page +20, enterprise features +15, returning visitor +10, scroll >75% +10, time >3min +5
  - Additive, capped at 100

- [ ] **T-021**: Scoring worker (separate process, NOT in request path)
  - Subscribes to `events:{tenant_id}` Redis Stream
  - Rolling visitor state in Redis hash, recomputes < 50ms
  - Crosses threshold → publishes `high_intent` event

- [ ] **T-022**: ML scoring placeholder (v2, deferred)
  - Define feature extraction interface
  - ONNX Runtime inference (sub-10ms, in-process)
  - A/B test framework: rule-based vs ML on conversion rate

**Milestone:** Visitors scored in real-time; high-intent events trigger proactive chat.

---

## Phase 3: RAG Knowledge Base & AI Chat (Weeks 7–9)

- [ ] **T-030**: Knowledge base ingestion
  - Admin uploads docs (PDF, HTML, MD) or provides URLs to scrape
  - Chunking: 512 tokens, 50-token overlap
  - Embeddings: `text-embedding-3-small` → pgvector with `tenant_id` filter
  - Background re-scrape on schedule; `CREATE INDEX CONCURRENTLY` for HNSW rebuilds

- [ ] **T-031**: RAG retrieval service
  - Query embedding → pgvector cosine similarity (top-k=5), filtered by `tenant_id`
  - Async via `asyncpg`

- [ ] **T-032**: LLM router & conversation engine
  - **Raw Anthropic/OpenAI SDKs — no LangChain**
  - Router: Haiku/GPT-4o-mini for FAQ → Sonnet/GPT-4o for objections/deep-dive
  - Circuit breaker per provider (open after 5 consecutive failures), fallback chain
  - System prompt per tenant (company name, tone, sales methodology)
  - Conversation memory: last 20 messages in DB, summarized beyond
  - Streaming via WebSocket through Go gateway
  - Tool calling: meeting booking, email capture, CRM lookup — all server-side
  - Track LLM token cost per conversation for margin monitoring

- [ ] **T-033**: Chat UI (`/widget/chat`)
  - Lazy-loaded on `high_intent` event or click (~35kb gzipped)
  - Native WebSocket API (no Socket.io)
  - Markdown rendering, typing indicator, session-persistent history
  - Tenant-configurable: colors, avatar, welcome message, position

**Milestone:** AI chatbot answers product questions grounded in customer docs, streams responses, proactively engages high-intent visitors.

---

## Phase 4: CRM & Calendar Integrations (Weeks 10–11)

- [ ] **T-050**: Integration framework
  - Abstract `IntegrationProvider`: `sync_lead()`, `get_availability()`, `book_meeting()`, `send_email()`
  - **OAuth2 tokens AES-256 encrypted in `integrations` table, decrypted only server-side**
  - Token refresh: background job checks expiry proactively

- [ ] **T-051**: CRM integrations (HubSpot, Salesforce)
  - Create/update contacts, deals, activities
  - Tenant-configurable field mapping
  - Real-time push + daily full reconciliation

- [ ] **T-052**: Calendar integrations (Google Calendar, Calendly)
  - AI proposes available slots in chat, books on confirmation
  - OAuth2 flow, all API calls in integration worker

- [ ] **T-053**: Email follow-up engine
  - Trigger: visitor drops off after providing email
  - AI drafts personalized email referencing exact pages viewed
  - Send via Resend, configurable delay (default 2h), CAN-SPAM compliance

**Milestone:** AI books meetings, pushes leads to CRM, sends follow-up emails — all using server-side encrypted OAuth tokens.

---

## Phase 5: Dashboard & Tenant Management (Weeks 12–14)

- [ ] **T-060**: Auth & tenant provisioning (NextAuth.js, onboarding wizard)
- [ ] **T-061**: Analytics views (SSE real-time visitors, conversion funnel, intent histogram)
- [ ] **T-062**: Config panels (widget customizer, scoring rules editor, KB management, integration OAuth flows)
- [ ] **T-063**: Conversation viewer (transcripts, visitor context sidebar, human takeover)

**Milestone:** Self-serve onboarding — customer signs up, pastes widget script, AI starts converting.

---

## Phase 6: Billing & Hardening (Weeks 15–16)

- [ ] **T-070**: Stripe billing (usage-based: visitors/mo, conversations/mo, integrations)
- [ ] **T-071**: Multi-tenancy hardening (RLS audit, Redis namespacing, data export, tenant deletion)
- [ ] **T-072**: Security audit (CSP, OWASP Top 10, zero-secret widget, encrypted-at-rest tokens)

**Milestone:** Production billing, verified tenant isolation, security audit passed.

---

## Phase 7: Deploy & Observability (Weeks 17–18)

- [ ] **T-080**: Production infrastructure (Fly.io, Railway, Vercel, Supabase, Cloudflare R2, Upstash)
- [ ] **T-081**: Observability (Grafana Cloud, Sentry, OpenTelemetry, LLM cost tracking, Redis lag alerts)
- [ ] **T-082**: Disaster recovery (automated backups, PITR, runbooks, chaos testing)
- [ ] **T-083**: pgvector → Qdrant migration automation (trigger at 60% index memory per tenant)

**Milestone:** Production-ready, monitored, scalable system handling multiple paying tenants.

---

## Deferred: Website Morphing (Post-Launch)

> Requires legal review for GDPR Article 22 compliance. Ship core loop first.

- [ ] **T-090**: Legal review of automated profiling-based content personalization
- [ ] **T-091**: IP-to-company enrichment (Clearbit/ZoomInfo)
- [ ] **T-092**: Personalization rules engine + consent layer

---

## Dependency Graph

```
Phase 0 (Foundation)
  │
  ├──► Phase 1 (Widget + Events + WS Gateway)
  │       │
  │       ├──► Phase 2 (Scoring Engine)
  │       │
  │       └──► Phase 3 (RAG + AI Chat)
  │               │
  │               └──► Phase 4 (CRM/Calendar/Email)
  │
  ├──► Phase 5 (Dashboard) ◄── grows alongside Phases 1–4
  │
  ├──► Phase 6 (Billing + Hardening)
  │
  ├──► Phase 7 (Deploy + Observability)
  │
  └──► Deferred (Website Morphing) ◄── post-launch, legal clearance required
```
