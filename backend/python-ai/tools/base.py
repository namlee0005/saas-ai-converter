"""
Abstract integration provider contract (T-050).

All CRM, calendar, and email tools implement this interface.
OAuth tokens are NEVER passed to this layer — callers must decrypt
them from the `integrations` table before constructing a provider.
"""

from __future__ import annotations

import abc
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared data models
# ---------------------------------------------------------------------------

class LeadProfile(BaseModel):
    tenant_id: uuid.UUID
    visitor_id: uuid.UUID
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    job_title: str | None = None
    phone: str | None = None
    # Intent context — used for CRM notes and email personalisation
    pages_viewed: list[str] = Field(default_factory=list)
    intent_score: int = Field(ge=0, le=100, default=0)
    conversation_summary: str | None = None
    # Estimated deal value for CRM deal creation; Decimal — never float
    estimated_deal_value: Decimal | None = None


class MeetingSlot(BaseModel):
    start: datetime   # always timezone-aware
    end: datetime     # always timezone-aware
    calendar_event_id: str | None = None  # populated after booking


class MeetingBooking(BaseModel):
    event_id: str
    calendar_link: str
    start: datetime
    end: datetime
    attendee_email: str
    meet_link: str | None = None  # Google Meet / Zoom link if added


class EmailPayload(BaseModel):
    tenant_id: uuid.UUID
    from_address: str          # "Sales <sales@customer.com>"
    to_address: str
    subject: str
    html_body: str
    text_body: str
    # CAN-SPAM: physical address required in footer
    physical_address: str
    # Scheduling: send after this time (worker respects delay)
    send_after: datetime | None = None


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class IntegrationProvider(abc.ABC):
    """
    Base class for all third-party integrations.

    Subclasses receive decrypted access_token + refresh_token at construction
    time. They must NOT persist these in any attribute that would be logged,
    serialised, or passed to the widget.
    """

    def __init__(self, tenant_id: uuid.UUID, access_token: str, refresh_token: str) -> None:
        self._tenant_id = tenant_id
        self._access_token = access_token       # plaintext, in-memory only
        self._refresh_token = refresh_token     # plaintext, in-memory only

    # CRM
    async def sync_lead(self, lead: LeadProfile) -> str:
        """Upsert lead into CRM. Returns the external CRM record ID."""
        raise NotImplementedError

    # Calendar
    async def get_availability(
        self,
        start: datetime,
        end: datetime,
        slot_minutes: int = 30,
    ) -> list[MeetingSlot]:
        """Return open meeting slots within the window."""
        raise NotImplementedError

    async def book_meeting(
        self,
        slot: MeetingSlot,
        lead: LeadProfile,
        title: str = "Product Demo",
    ) -> MeetingBooking:
        """Book a meeting and return the confirmed event details."""
        raise NotImplementedError

    # Email
    async def send_email(self, payload: EmailPayload) -> str:
        """Send email. Returns the provider message ID."""
        raise NotImplementedError

    # OAuth lifecycle — called by integration_worker token refresh job
    @abc.abstractmethod
    async def refresh_access_token(self) -> tuple[str, str]:
        """
        Exchange refresh_token for a new (access_token, refresh_token) pair.
        Returns plaintext tokens — caller is responsible for re-encrypting
        and persisting to the `integrations` table.
        """
