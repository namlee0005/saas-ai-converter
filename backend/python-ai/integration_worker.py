"""
Integration Worker (T-050, T-051, T-052, T-053).

Separate process — do NOT import into main.py.

Responsibilities:
  1. Consume `integrations:tasks` Redis Stream (consumer group "int-worker")
  2. Dispatch task to correct provider tool (HubSpot, Google Calendar, Resend)
  3. Background OAuth token refresh: proactively refresh tokens expiring in <10 min
  4. Dead-letter failed tasks to `integrations:dlq` after 3 retries

Redis task envelope:
  {
    "task_type": "crm_sync" | "book_meeting" | "send_email",
    "tenant_id": "<uuid>",
    "integration_id": "<uuid>",    # row in `integrations` table
    "payload": { ... },
    "attempt": 1
  }

The worker decrypts OAuth tokens from DB using TokenCrypto — tokens are
NEVER stored in Redis or any log output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import redis.asyncio as aioredis
from pydantic import BaseModel

from tools.base import EmailPayload, LeadProfile, MeetingSlot
from tools.crypto import get_token_crypto
from tools.google_calendar import GoogleCalendarConfig, GoogleCalendarProvider
from tools.hubspot import HubSpotProvider
from tools.resend_email import ResendEmailProvider

logger = logging.getLogger("integration_worker")
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)

STREAM_KEY = "integrations:tasks"
DLQ_KEY = "integrations:dlq"
CONSUMER_GROUP = "int-worker"
CONSUMER_NAME = f"worker-{os.getpid()}"
MAX_RETRIES = 3
BATCH_SIZE = 10
BLOCK_MS = 5_000          # block on XREADGROUP for 5s before polling token refresh
TOKEN_REFRESH_INTERVAL = 300  # seconds between token refresh sweeps


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def fetch_integration(db: asyncpg.Connection, integration_id: uuid.UUID) -> dict:
    """
    Load integration row from DB.
    Returns dict with encrypted_access_token, encrypted_refresh_token, provider, config.
    """
    row = await db.fetchrow(
        """
        SELECT id, tenant_id, provider, encrypted_access_token,
               encrypted_refresh_token, token_expires_at, config
        FROM integrations
        WHERE id = $1
        """,
        integration_id,
    )
    if not row:
        raise ValueError(f"integration {integration_id} not found")
    return dict(row)


async def persist_refreshed_tokens(
    db: asyncpg.Connection,
    integration_id: uuid.UUID,
    enc_access: str,
    enc_refresh: str,
    expires_at: datetime,
) -> None:
    await db.execute(
        """
        UPDATE integrations
        SET encrypted_access_token = $2,
            encrypted_refresh_token = $3,
            token_expires_at = $4,
            updated_at = NOW()
        WHERE id = $1
        """,
        integration_id,
        enc_access,
        enc_refresh,
        expires_at,
    )


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def build_provider(
    row: dict,
    access_token: str,
    refresh_token: str,
) -> HubSpotProvider | GoogleCalendarProvider | ResendEmailProvider:
    tenant_id = uuid.UUID(str(row["tenant_id"]))
    config: dict = json.loads(row["config"]) if isinstance(row["config"], str) else (row["config"] or {})
    provider = row["provider"]

    if provider == "hubspot":
        return HubSpotProvider(
            tenant_id=tenant_id,
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            field_mapping=config.get("field_mapping"),
        )
    elif provider == "google_calendar":
        return GoogleCalendarProvider(
            tenant_id=tenant_id,
            access_token=access_token,
            refresh_token=refresh_token,
            config=GoogleCalendarConfig(**config),
        )
    elif provider == "resend":
        return ResendEmailProvider(tenant_id=tenant_id, api_key=access_token)
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Task handlers
# ---------------------------------------------------------------------------

async def handle_crm_sync(payload: dict, provider: HubSpotProvider) -> None:
    lead = LeadProfile(**payload["lead"])
    contact_id = await provider.sync_lead(lead)
    logger.info(json.dumps({"event": "crm_sync_ok", "contact_id": contact_id}))


async def handle_book_meeting(payload: dict, provider: GoogleCalendarProvider) -> None:
    slot = MeetingSlot(**payload["slot"])
    lead = LeadProfile(**payload["lead"])
    title = payload.get("title", "Product Demo")
    booking = await provider.book_meeting(slot, lead, title)
    logger.info(json.dumps({
        "event": "meeting_booked_ok",
        "event_id": booking.event_id,
        "meet_link": booking.meet_link,
    }))


async def handle_send_email(payload: dict, provider: ResendEmailProvider) -> None:
    email_payload = EmailPayload(**payload["email"])
    message_id = await provider.send_email(email_payload)
    logger.info(json.dumps({"event": "email_sent_ok", "message_id": message_id}))


TASK_DISPATCH = {
    "crm_sync": handle_crm_sync,
    "book_meeting": handle_book_meeting,
    "send_email": handle_send_email,
}


# ---------------------------------------------------------------------------
# Token refresh sweep
# ---------------------------------------------------------------------------

async def refresh_expiring_tokens(db: asyncpg.Connection) -> None:
    """
    Proactively refresh OAuth tokens expiring within 10 minutes.
    Runs as a periodic background task — does NOT block the stream consumer.
    """
    crypto = get_token_crypto()
    threshold = datetime.now(timezone.utc) + timedelta(minutes=10)

    rows = await db.fetch(
        """
        SELECT id, tenant_id, provider, encrypted_access_token,
               encrypted_refresh_token, token_expires_at, config
        FROM integrations
        WHERE token_expires_at <= $1
          AND provider != 'resend'   -- API keys don't expire
        """,
        threshold,
    )

    for row in rows:
        integration_id = uuid.UUID(str(row["id"]))
        try:
            access_token = crypto.decrypt(row["encrypted_access_token"])
            refresh_token = crypto.decrypt(row["encrypted_refresh_token"])
            provider = build_provider(dict(row), access_token, refresh_token)

            new_access, new_refresh = await provider.refresh_access_token()

            enc_access = crypto.encrypt(new_access)
            enc_refresh = crypto.encrypt(new_refresh)
            new_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

            await persist_refreshed_tokens(db, integration_id, enc_access, enc_refresh, new_expires_at)
            logger.info(json.dumps({
                "event": "token_refresh_ok",
                "integration_id": str(integration_id),
                "provider": row["provider"],
            }))
        except Exception:
            logger.exception(json.dumps({
                "event": "token_refresh_failed",
                "integration_id": str(integration_id),
            }))


# ---------------------------------------------------------------------------
# Main consumer loop
# ---------------------------------------------------------------------------

async def process_message(
    message_id: str,
    data: dict,
    db: asyncpg.Connection,
    rdb: aioredis.Redis,
) -> None:
    crypto = get_token_crypto()
    task_type: str = data["task_type"]
    integration_id = uuid.UUID(data["integration_id"])
    attempt: int = int(data.get("attempt", 1))

    try:
        row = await fetch_integration(db, integration_id)
        access_token = crypto.decrypt(row["encrypted_access_token"])
        refresh_token = crypto.decrypt(row["encrypted_refresh_token"])
        provider = build_provider(row, access_token, refresh_token)

        handler = TASK_DISPATCH.get(task_type)
        if handler is None:
            raise ValueError(f"Unknown task_type: {task_type}")

        await handler(data["payload"], provider)  # type: ignore[arg-type]
        await rdb.xack(STREAM_KEY, CONSUMER_GROUP, message_id)

    except Exception:
        logger.exception(json.dumps({
            "event": "task_failed",
            "task_type": task_type,
            "integration_id": str(integration_id),
            "attempt": attempt,
        }))
        if attempt >= MAX_RETRIES:
            # Dead-letter after max retries
            dlq_entry = {**data, "failed_message_id": message_id}
            await rdb.xadd(DLQ_KEY, {"data": json.dumps(dlq_entry)})
            await rdb.xack(STREAM_KEY, CONSUMER_GROUP, message_id)
            logger.error(json.dumps({
                "event": "task_dead_lettered",
                "task_type": task_type,
                "integration_id": str(integration_id),
            }))
        else:
            # Requeue with incremented attempt (NACK via re-add)
            retry_entry = {**data, "attempt": attempt + 1}
            await rdb.xadd(STREAM_KEY, {"data": json.dumps(retry_entry)})
            await rdb.xack(STREAM_KEY, CONSUMER_GROUP, message_id)


async def run() -> None:
    database_url = os.environ["DATABASE_URL"]
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    db_pool = await asyncpg.create_pool(database_url, min_size=2, max_size=5)
    rdb = aioredis.from_url(redis_url, decode_responses=True)

    # Ensure consumer group exists
    try:
        await rdb.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    logger.info(json.dumps({"event": "worker_started", "consumer": CONSUMER_NAME}))

    last_refresh_sweep = 0.0
    loop = asyncio.get_event_loop()

    while True:
        now = loop.time()

        # Periodic token refresh sweep
        if now - last_refresh_sweep >= TOKEN_REFRESH_INTERVAL:
            async with db_pool.acquire() as db:
                await refresh_expiring_tokens(db)
            last_refresh_sweep = now

        # Consume from stream
        messages = await rdb.xreadgroup(
            CONSUMER_GROUP,
            CONSUMER_NAME,
            {STREAM_KEY: ">"},
            count=BATCH_SIZE,
            block=BLOCK_MS,
        )
        if not messages:
            continue

        for _stream, entries in messages:
            for message_id, fields in entries:
                try:
                    data = json.loads(fields["data"])
                except (KeyError, json.JSONDecodeError):
                    logger.error(json.dumps({"event": "malformed_message", "id": message_id}))
                    await rdb.xack(STREAM_KEY, CONSUMER_GROUP, message_id)
                    continue

                async with db_pool.acquire() as db:
                    await process_message(message_id, data, db, rdb)


if __name__ == "__main__":
    asyncio.run(run())
