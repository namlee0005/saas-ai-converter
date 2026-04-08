"""
Resend email integration (T-053).

Sends AI-personalised follow-up emails via the Resend HTTP API.
CAN-SPAM compliance: physical address required in every payload.
Resend does not have an official Python SDK pinned here — we call
the REST API directly via httpx to avoid unpinned transitive deps.
"""

from __future__ import annotations

import json
import logging
import uuid

import httpx

from .base import EmailPayload, IntegrationProvider

logger = logging.getLogger("integration.resend")

_SEND_URL = "https://api.resend.com/emails"


class ResendEmailProvider(IntegrationProvider):
    """
    Resend email sender.

    Resend uses API key auth, not OAuth2. The api_key is stored encrypted
    in the `integrations` table alongside OAuth tokens and decrypted server-side.
    refresh_access_token is a no-op (API keys don't expire).
    """

    def __init__(
        self,
        tenant_id: uuid.UUID,
        api_key: str,
    ) -> None:
        # Resend is key-auth, not OAuth — pass api_key as access_token
        super().__init__(tenant_id, access_token=api_key, refresh_token="")
        self._api_key = api_key

    async def refresh_access_token(self) -> tuple[str, str]:
        # API keys do not expire — nothing to refresh
        return self._api_key, ""

    async def send_email(self, payload: EmailPayload) -> str:
        """
        Send email via Resend. Returns Resend message ID.

        CAN-SPAM requirements enforced:
          - physical_address must be present in EmailPayload (validated by Pydantic)
          - Unsubscribe link must be present in html_body / text_body (caller's responsibility)
        """
        if not payload.physical_address:
            raise ValueError("CAN-SPAM: physical_address is required in EmailPayload")

        body = {
            "from": payload.from_address,
            "to": [payload.to_address],
            "subject": payload.subject,
            "html": payload.html_body,
            "text": payload.text_body,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _SEND_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )

        resp.raise_for_status()
        message_id: str = resp.json()["id"]

        logger.info(json.dumps({
            "event": "email_sent",
            "provider": "resend",
            "tenant_id": str(payload.tenant_id),
            "message_id": message_id,
            "to": payload.to_address,
        }))

        return message_id
