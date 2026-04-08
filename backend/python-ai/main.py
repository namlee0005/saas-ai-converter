"""
FastAPI entrypoint — AI / Business Logic service.

Responsibilities:
  - Event ingestion: POST /api/v1/events  (validates, publishes to Redis Stream)
  - RAG retrieval:   internal helper used by chat worker
  - LLM routing:     Claude Sonnet 4.6 for objections, GPT-4o for FAQ, Haiku/mini for scoring
  - Tool execution:  CRM push, calendar booking, email — all server-side, zero widget exposure

Workers (separate processes, same codebase):
  - scoring_worker.py   — consumes events:{tenant_id}, recomputes intent score <50ms
  - rag_worker.py       — consumes chat:in stream, runs RAG → LLM → publishes tokens to pub/sub
  - integration_worker.py — OAuth token refresh, CRM/calendar/email execution
"""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import asyncpg
import redis.asyncio as aioredis
import sentry_sdk
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    database_url: str                          # asyncpg DSN
    redis_url: str = "redis://localhost:6379"
    jwt_secret: str
    anthropic_api_key: str
    openai_api_key: str
    sentry_dsn: str = ""
    environment: str = "development"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()  # type: ignore[call-arg]  # populated from env

# ---------------------------------------------------------------------------
# Observability — init before app starts
# ---------------------------------------------------------------------------

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("python-ai")

# ---------------------------------------------------------------------------
# App lifecycle — connection pools
# ---------------------------------------------------------------------------

_db_pool: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_pool, _redis

    _db_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=5,
        max_size=20,
        command_timeout=30,
    )
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    logger.info(json.dumps({"event": "startup", "db": "connected", "redis": "connected"}))
    yield

    await _db_pool.close()
    await _redis.aclose()
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(
    title="saas-ai-converter / python-ai",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten per tenant in prod via dynamic CORS middleware
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---------------------------------------------------------------------------
# Dependency injectors
# ---------------------------------------------------------------------------

async def get_db() -> asyncpg.Connection:
    async with _db_pool.acquire() as conn:  # type: ignore[union-attr]
        yield conn


async def get_redis() -> aioredis.Redis:
    return _redis  # type: ignore[return-value]


def get_tenant_id(authorization: Annotated[str | None, Header()] = None) -> uuid.UUID:
    """
    Extract tenant_id from Bearer JWT.
    PLACEHOLDER: replace with real JWT decode using settings.jwt_secret.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token")
    # TODO: decode JWT, validate exp, extract tenant_id claim
    # For now return a fixed test UUID so the scaffold is runnable.
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


TenantDep = Annotated[uuid.UUID, Depends(get_tenant_id)]
DBDep     = Annotated[asyncpg.Connection, Depends(get_db)]
RedisDep  = Annotated[aioredis.Redis, Depends(get_redis)]

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TrackingEvent(BaseModel):
    visitor_id:  uuid.UUID
    session_id:  uuid.UUID
    event_type:  str   # "page_view" | "scroll" | "click" | "time_on_page"
    url:         str
    occurred_at: datetime
    metadata:    dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_utc(self) -> "TrackingEvent":
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware (UTC)")
        return self


class EventBatch(BaseModel):
    events: list[TrackingEvent] = Field(min_length=1, max_length=500)


class ChatSessionRequest(BaseModel):
    visitor_id: uuid.UUID


class ChatSessionResponse(BaseModel):
    session_token: str   # short-lived JWT for Go WS gateway
    ws_url: str

# ---------------------------------------------------------------------------
# Routes — Event Ingestion
# ---------------------------------------------------------------------------

@app.post("/api/v1/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(
    batch: EventBatch,
    tenant_id: TenantDep,
    rdb: RedisDep,
    request: Request,
) -> JSONResponse:
    """
    Accepts a batch of tracking events from the pixel widget.
    Validates with Pydantic, then publishes each to the tenant Redis Stream.
    Rate limiting per tenant enforced here (TODO: Redis GCRA via redis-cell).
    """
    logger.info(json.dumps({
        "event": "events_received",
        "tenant_id": str(tenant_id),
        "count": len(batch.events),
        "remote_addr": request.client.host if request.client else "unknown",
    }))

    stream_key = f"{tenant_id}:events"
    pipe = rdb.pipeline(transaction=False)

    for evt in batch.events:
        payload = evt.model_dump(mode="json")
        payload["tenant_id"] = str(tenant_id)
        pipe.xadd(stream_key, {"data": json.dumps(payload)})

    await pipe.execute()

    return JSONResponse({"accepted": len(batch.events)}, status_code=202)


# ---------------------------------------------------------------------------
# Routes — Chat Session Init
# ---------------------------------------------------------------------------

@app.post("/api/v1/chat/session", response_model=ChatSessionResponse)
async def create_chat_session(
    body: ChatSessionRequest,
    tenant_id: TenantDep,
    db: DBDep,
) -> ChatSessionResponse:
    """
    Issues a short-lived JWT for the visitor to connect to the Go WS gateway.
    Stores session in DB for conversation continuity.
    TODO: implement real JWT signing with python-jose + exp=15min.
    """
    session_id = uuid.uuid4()
    # TODO: INSERT INTO conversations (id, tenant_id, visitor_id, started_at) ...
    ws_url = os.getenv("GO_GATEWAY_WS_URL", "ws://localhost:8080/ws")

    return ChatSessionResponse(
        session_token=f"placeholder.jwt.{session_id}",
        ws_url=f"{ws_url}?token=placeholder.jwt.{session_id}",
    )


# ---------------------------------------------------------------------------
# Routes — RAG Knowledge Base (admin)
# ---------------------------------------------------------------------------

@app.post("/api/v1/kb/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_knowledge(
    tenant_id: TenantDep,
    rdb: RedisDep,
) -> JSONResponse:
    """
    PLACEHOLDER — full implementation in T-030.

    Flow:
      1. Accept PDF / HTML / MD upload or URL list.
      2. Chunk text: 512 tokens, 50-token overlap (tiktoken).
      3. Embed chunks: openai text-embedding-3-small.
      4. Upsert into pgvector `knowledge_chunks` table filtered by tenant_id.
      5. Trigger HNSW index rebuild via CREATE INDEX CONCURRENTLY.
    """
    return JSONResponse({"status": "not_implemented", "phase": "T-030"}, status_code=202)


# ---------------------------------------------------------------------------
# Internal RAG retrieval helper (used by rag_worker, not exposed via HTTP)
# ---------------------------------------------------------------------------

async def retrieve_context(
    query: str,
    tenant_id: uuid.UUID,
    db: asyncpg.Connection,
    top_k: int = 5,
) -> list[dict]:
    """
    PLACEHOLDER — full implementation in T-031.

    Flow:
      1. Embed query: openai text-embedding-3-small.
      2. pgvector cosine similarity search on knowledge_chunks WHERE tenant_id = $1.
      3. Return top-k chunks with metadata.

    Security: tenant_id filter is mandatory — no cross-tenant leakage.
    """
    # TODO:
    # embedding = await openai_client.embeddings.create(input=query, model="text-embedding-3-small")
    # rows = await db.fetch("""
    #     SELECT content, metadata
    #     FROM knowledge_chunks
    #     WHERE tenant_id = $1
    #     ORDER BY embedding <=> $2
    #     LIMIT $3
    # """, tenant_id, embedding.data[0].embedding, top_k)
    return []  # placeholder


# ---------------------------------------------------------------------------
# Internal LLM router (used by rag_worker)
# ---------------------------------------------------------------------------

async def route_llm(
    messages: list[dict],
    tenant_id: uuid.UUID,
    complexity: str = "auto",
):
    """
    PLACEHOLDER — full implementation in T-032.

    Routing strategy:
      - "simple" / FAQ:       GPT-4o-mini or Claude Haiku 4.5
      - "complex" / objection: Claude Sonnet 4.6 or GPT-4o
      - "auto":               classify via Haiku first, then route

    Circuit breaker per provider: open after 5 consecutive failures.
    Fallback chain: Anthropic → OpenAI → cached response.

    Streams tokens via Redis pub/sub → Go gateway → widget.
    Tracks token cost per conversation for margin monitoring.
    """
    # TODO: implement with raw anthropic / openai SDKs — no LangChain
    raise NotImplementedError("LLM router not yet implemented (T-032)")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz(db: DBDep, rdb: RedisDep) -> dict:
    await db.fetchval("SELECT 1")
    await rdb.ping()
    return {"status": "ok"}
