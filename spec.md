# B2B AI SaaS Conversion System — Technical Specification

## 1. Executive Summary

An embeddable AI Sales Agent platform for B2B SaaS companies. A lightweight JS widget tracks visitor behavior, scores intent in real-time, and engages high-value prospects via a context-aware AI chatbot that can answer product questions, handle objections, and book meetings — all without human intervention. The system uses multi-tenant RAG grounded in each customer's own docs, with a split backend: Go handles high-concurrency WebSocket ingestion while Python FastAPI owns all AI and business logic using raw provider SDKs (no LangChain).

## 2. Recommended Tech Stack

| Component | Technology | Reasoning |
|:---|:---|:---|
| **Dashboard** | Next.js 14, TailwindCSS, Shadcn UI | SSR for SEO, proven dashboard ecosystem |
| **Widget (Pixel)** | Vanilla JS, < 5kb gzipped | Tracking-only payload, zero dependencies, CSP-compliant |
| **Widget (Chat)** | Preact, ~35kb gzipped, lazy-loaded | Only fetched on high-intent trigger or click — halves blast radius |
| **Widget Delivery** | Stable loader script (`/v1/loader.js`) → versioned bundles on Cloudflare R2 + CDN | Canary rollouts to 5% of tenants, instant rollback, SRI hashes for integrity |
| **Ingestion Service** | Go (`nhooyr.io/websocket`) | Single-binary WS gateway, 10k+ concurrent connections per pod, goroutines cheaper than Python async for pure relay |
| **AI / Business Logic** | Python FastAPI | Raw Anthropic/OpenAI SDKs — no LangChain. Direct prompt control, streaming, tool calling. Single hire profile for LLM-native ecosystem |
| **Database** | PostgreSQL 16 + pgvector (Supabase) | Single store for relational data AND vector embeddings. Multi-tenant via `tenant_id` + RLS. **Migration gate:** move to Qdrant when any tenant's index memory exceeds 60% of pgvector allocation |
| **Message Bus** | Redis 7 Streams (Valkey) | Event ingestion, pub/sub for WS cross-instance messaging, scoring pipeline. Migrate to Kafka only at ceiling |
| **Real-time (Dashboard)** | SSE (Server-Sent Events) | Simpler than WebSocket for unidirectional live feeds; HTTP/2 multiplexed |
| **Real-time (Chat)** | Native WebSocket via Go gateway | Bidirectional streaming for AI chat tokens |
| **LLM Routing** | Custom router in FastAPI | Claude Sonnet 4.6 for complex objection handling, GPT-4o for general Q&A, Claude Haiku 4.5/GPT-4o-mini for intent classification. Circuit breaker per provider (open after 5 consecutive failures) |
| **Integrations** | OAuth2 per-tenant (HubSpot, Salesforce, Google Calendar, Resend) | Tokens AES-256 encrypted at rest in DB, decrypted only server-side in FastAPI integration worker. **NEVER exposed to widget or client browser** |
| **Hosting** | Fly.io (Go gateway), Railway (FastAPI + workers), Vercel (Dashboard), Cloudflare R2 (widget assets), Supabase (DB), Upstash (Redis) | Managed platforms — graduate to AWS EKS only when provably needed (year-two problem) |
| **Observability** | Grafana Cloud free tier + Sentry + OpenTelemetry | 10k metrics, 50GB logs. LLM token cost tracking per conversation per tenant |

## 3. Architecture Overview

```
              Cloudflare R2 + CDN
       ┌────────────┴────────────┐
    pixel.js (5kb)          chat.js (35kb, lazy)
       │                         │
  POST /api/v1/events      WebSocket connect
       │                         │
┌──────▼──────┐          ┌───────▼───────┐
│   FastAPI   │          │  Go WS        │
│  (REST API) │          │  Gateway      │
│  + Workers  │          │  (nhooyr/ws)  │
└──────┬──────┘          └───────┬───────┘
       │                         │
┌──────▼─────────────────────────▼──────┐
│         Redis 7 (Valkey)              │
│    Streams + Pub/Sub + Cache          │
└──┬──────────┬──────────┬─────────────┘
   │          │          │
┌──▼────┐ ┌──▼────┐ ┌───▼───────────┐
│Score  │ │ RAG   │ │ Integration   │
│Worker │ │Worker │ │ Worker        │
│(Py)   │ │(Py)   │ │ (Py)         │
└──┬────┘ └──┬────┘ └──────┬────────┘
   └──────────┼─────────────┘
       ┌──────▼──────┐
       │ PostgreSQL  │
       │ + pgvector  │
       │ (Supabase)  │
       └─────────────┘
```

### Data Flow

1. **Widget pixel** streams behavioral events via `POST /api/v1/events` → FastAPI validates with Pydantic → publishes to Redis Stream `events:{tenant_id}`
2. **Scoring worker** consumes events, maintains rolling visitor state in Redis hash, recomputes intent score (<50ms). If score > threshold (default 75), publishes `high_intent` event
3. **Go gateway** relays `high_intent` event to widget via WebSocket → widget lazy-loads chat module
4. **Chat messages** flow: Widget → Go gateway (WebSocket) → Redis pub/sub → RAG worker → LLM router → streaming tokens back through gateway to widget
5. **Tool calls** (meeting booking, CRM push, email) execute server-side in integration worker using decrypted OAuth tokens — widget never sees credentials

### Multi-Tenancy Model

- Every table includes `tenant_id` column with PostgreSQL Row-Level Security (RLS) policies
- Application middleware extracts `tenant_id` from JWT, passes to all queries
- Redis keys namespaced: `{tenant_id}:*`
- pgvector similarity searches filtered by `tenant_id` — no cross-tenant leakage
- Widget JS ships zero secrets — all API calls via signed short-lived session tokens

### Security Boundaries

- **Widget:** CSP-compliant (no `eval()`, no `document.write()`, no inline styles), shadow DOM isolation, SRI hashes on bundles
- **OAuth tokens:** AES-256 encrypted in `integrations` table, decrypted only in FastAPI integration worker process. Token refresh runs as background job
- **API auth:** JWT with 15min access / 7d refresh tokens, httpOnly cookies for dashboard
- **Tool calling:** All CRM/Calendar/Email actions execute server-side only. The Go gateway is a pure message relay with zero business logic — it cannot access integration credentials

### Vector DB Migration Strategy

pgvector handles RAG for all tenants initially. Monitoring tracks per-tenant index memory usage. When any tenant's HNSW index memory exceeds 60% of allocated pgvector resources:

1. Automated alert fires
2. Migrate that tenant's embeddings to dedicated Qdrant instance
3. RAG retrieval service routes queries by tenant: pgvector for small tenants, Qdrant for graduated tenants
4. All HNSW index operations use `CREATE INDEX CONCURRENTLY` to prevent query blocking during tenant KB uploads
