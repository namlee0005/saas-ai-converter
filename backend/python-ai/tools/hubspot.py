"""
HubSpot CRM integration (T-051).

Capabilities:
  - OAuth2 token refresh via HubSpot token endpoint
  - Upsert contact by email (create or update)
  - Create deal associated to contact
  - Log conversation activity note on contact

All API calls use HubSpot v3 private-app / OAuth bearer tokens.
Field mapping is tenant-configurable (passed at construction).
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal

import httpx

from .base import IntegrationProvider, LeadProfile

logger = logging.getLogger("integration.hubspot")

_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
_CONTACTS_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
_DEALS_URL = "https://api.hubapi.com/crm/v3/objects/deals"
_ASSOCIATIONS_URL = "https://api.hubapi.com/crm/v3/associations/{from_type}/{to_type}/batch/create"
_NOTES_URL = "https://api.hubapi.com/crm/v3/objects/notes"


class HubSpotProvider(IntegrationProvider):
    """
    HubSpot CRM v3 — contact upsert, deal creation, activity notes.

    field_mapping: maps our canonical field names to HubSpot property names.
    Default maps cover standard HubSpot schema; tenants override for custom props.

    Example override:
        field_mapping={"job_title": "jobtitle", "company": "company", "phone": "phone"}
    """

    DEFAULT_FIELD_MAP: dict[str, str] = {
        "email": "email",
        "first_name": "firstname",
        "last_name": "lastname",
        "company": "company",
        "job_title": "jobtitle",
        "phone": "phone",
    }

    def __init__(
        self,
        tenant_id: uuid.UUID,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        field_mapping: dict[str, str] | None = None,
    ) -> None:
        super().__init__(tenant_id, access_token, refresh_token)
        self._client_id = client_id
        self._client_secret = client_secret
        self._field_map = {**self.DEFAULT_FIELD_MAP, **(field_mapping or {})}

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # OAuth lifecycle
    # ------------------------------------------------------------------

    async def refresh_access_token(self) -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        body = resp.json()
        logger.info(json.dumps({
            "event": "token_refreshed",
            "provider": "hubspot",
            "tenant_id": str(self._tenant_id),
        }))
        return body["access_token"], body["refresh_token"]

    # ------------------------------------------------------------------
    # Lead sync
    # ------------------------------------------------------------------

    async def sync_lead(self, lead: LeadProfile) -> str:
        """
        Upsert contact by email. Creates deal + activity note if contact is new.
        Returns HubSpot contact ID (string).
        """
        if not lead.email:
            raise ValueError("HubSpot sync requires lead.email")

        contact_id, is_new = await self._upsert_contact(lead)

        if is_new:
            deal_id = await self._create_deal(lead, contact_id)
            logger.info(json.dumps({
                "event": "deal_created",
                "provider": "hubspot",
                "tenant_id": str(self._tenant_id),
                "contact_id": contact_id,
                "deal_id": deal_id,
            }))

        await self._log_activity(lead, contact_id)

        logger.info(json.dumps({
            "event": "lead_synced",
            "provider": "hubspot",
            "tenant_id": str(self._tenant_id),
            "contact_id": contact_id,
            "is_new": is_new,
        }))
        return contact_id

    async def _upsert_contact(self, lead: LeadProfile) -> tuple[str, bool]:
        """Returns (contact_id, is_new)."""
        properties = self._build_contact_properties(lead)
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try to find existing contact by email
            search_resp = await client.post(
                f"{_CONTACTS_URL}/search",
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": lead.email,
                        }]
                    }],
                    "properties": ["email"],
                    "limit": 1,
                },
                headers=self._auth_headers(),
            )
            search_resp.raise_for_status()
            results = search_resp.json().get("results", [])

            if results:
                contact_id = results[0]["id"]
                # Update existing contact
                patch_resp = await client.patch(
                    f"{_CONTACTS_URL}/{contact_id}",
                    json={"properties": properties},
                    headers=self._auth_headers(),
                )
                patch_resp.raise_for_status()
                return contact_id, False
            else:
                # Create new contact
                create_resp = await client.post(
                    _CONTACTS_URL,
                    json={"properties": properties},
                    headers=self._auth_headers(),
                )
                create_resp.raise_for_status()
                contact_id = create_resp.json()["id"]
                return contact_id, True

    def _build_contact_properties(self, lead: LeadProfile) -> dict[str, str]:
        props: dict[str, str] = {}
        for our_field, hs_field in self._field_map.items():
            val = getattr(lead, our_field, None)
            if val is not None:
                props[hs_field] = str(val)
        return props

    async def _create_deal(self, lead: LeadProfile, contact_id: str) -> str:
        company_name = lead.company or "Unknown Company"
        amount = str(lead.estimated_deal_value) if lead.estimated_deal_value else "0"

        async with httpx.AsyncClient(timeout=10.0) as client:
            deal_resp = await client.post(
                _DEALS_URL,
                json={
                    "properties": {
                        "dealname": f"{company_name} — AI Chat Lead",
                        "dealstage": "appointmentscheduled",
                        "pipeline": "default",
                        "amount": amount,
                    }
                },
                headers=self._auth_headers(),
            )
            deal_resp.raise_for_status()
            deal_id = deal_resp.json()["id"]

            # Associate deal → contact
            assoc_url = _ASSOCIATIONS_URL.format(from_type="deals", to_type="contacts")
            await client.post(
                assoc_url,
                json={"inputs": [{"from": {"id": deal_id}, "to": {"id": contact_id}, "type": "deal_to_contact"}]},
                headers=self._auth_headers(),
            )

        return deal_id

    async def _log_activity(self, lead: LeadProfile, contact_id: str) -> None:
        note_body = self._build_note(lead)
        async with httpx.AsyncClient(timeout=10.0) as client:
            note_resp = await client.post(
                _NOTES_URL,
                json={
                    "properties": {
                        "hs_note_body": note_body,
                        "hs_timestamp": str(int(__import__("time").time() * 1000)),
                    }
                },
                headers=self._auth_headers(),
            )
            note_resp.raise_for_status()
            note_id = note_resp.json()["id"]

            # Associate note → contact
            assoc_url = _ASSOCIATIONS_URL.format(from_type="notes", to_type="contacts")
            await client.post(
                assoc_url,
                json={"inputs": [{"from": {"id": note_id}, "to": {"id": contact_id}, "type": "note_to_contact"}]},
                headers=self._auth_headers(),
            )

    @staticmethod
    def _build_note(lead: LeadProfile) -> str:
        lines = [
            f"Intent score: {lead.intent_score}/100",
            f"Pages viewed: {', '.join(lead.pages_viewed[:10]) or 'none'}",
        ]
        if lead.conversation_summary:
            lines.append(f"Chat summary: {lead.conversation_summary}")
        return "\n".join(lines)
