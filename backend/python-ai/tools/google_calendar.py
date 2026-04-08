"""
Google Calendar integration (T-052).

Capabilities:
  - OAuth2 token refresh via Google token endpoint
  - Free/busy query to find open slots in owner's calendar
  - Create calendar event with attendee + Google Meet link

All httpx calls are async. Access token is used in Authorization header
and NEVER logged or returned to any client.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import BaseModel

from .base import IntegrationProvider, LeadProfile, MeetingBooking, MeetingSlot

logger = logging.getLogger("integration.google_calendar")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"


class GoogleCalendarConfig(BaseModel):
    client_id: str
    client_secret: str
    calendar_id: str = "primary"


class GoogleCalendarProvider(IntegrationProvider):
    """
    Google Calendar v3 — availability + booking.

    Construct with decrypted access_token + refresh_token from `integrations`
    table. Obtain client_id / client_secret from tenant-level config (not per
    visitor — these are app credentials, not user secrets).
    """

    def __init__(
        self,
        tenant_id: uuid.UUID,
        access_token: str,
        refresh_token: str,
        config: GoogleCalendarConfig,
    ) -> None:
        super().__init__(tenant_id, access_token, refresh_token)
        self._config = config

    # ------------------------------------------------------------------
    # OAuth lifecycle
    # ------------------------------------------------------------------

    async def refresh_access_token(self) -> tuple[str, str]:
        """Exchange refresh_token for new (access_token, refresh_token)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        resp.raise_for_status()
        body = resp.json()
        new_access = body["access_token"]
        # Google may issue a new refresh token; fall back to existing
        new_refresh = body.get("refresh_token", self._refresh_token)
        logger.info(json.dumps({
            "event": "token_refreshed",
            "provider": "google_calendar",
            "tenant_id": str(self._tenant_id),
        }))
        return new_access, new_refresh

    # ------------------------------------------------------------------
    # Free/busy → available slots
    # ------------------------------------------------------------------

    async def get_availability(
        self,
        start: datetime,
        end: datetime,
        slot_minutes: int = 30,
    ) -> list[MeetingSlot]:
        """
        Returns open MeetingSlots within [start, end].

        Strategy: query Google free/busy, invert busy blocks to find free
        windows, then carve slot_minutes chunks aligned to the hour.
        """
        assert start.tzinfo is not None, "start must be timezone-aware"
        assert end.tzinfo is not None, "end must be timezone-aware"

        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": self._config.calendar_id}],
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _FREEBUSY_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        resp.raise_for_status()

        calendar_data = resp.json()["calendars"].get(self._config.calendar_id, {})
        busy_blocks: list[tuple[datetime, datetime]] = []
        for block in calendar_data.get("busy", []):
            busy_start = datetime.fromisoformat(block["start"])
            busy_end = datetime.fromisoformat(block["end"])
            # Ensure timezone-aware
            if busy_start.tzinfo is None:
                busy_start = busy_start.replace(tzinfo=timezone.utc)
            if busy_end.tzinfo is None:
                busy_end = busy_end.replace(tzinfo=timezone.utc)
            busy_blocks.append((busy_start, busy_end))

        return self._carve_slots(start, end, busy_blocks, slot_minutes)

    @staticmethod
    def _carve_slots(
        window_start: datetime,
        window_end: datetime,
        busy: list[tuple[datetime, datetime]],
        slot_minutes: int,
    ) -> list[MeetingSlot]:
        slots: list[MeetingSlot] = []
        delta = timedelta(minutes=slot_minutes)
        cursor = window_start
        while cursor + delta <= window_end:
            slot_end = cursor + delta
            overlaps = any(
                b_start < slot_end and b_end > cursor
                for b_start, b_end in busy
            )
            if not overlaps:
                slots.append(MeetingSlot(start=cursor, end=slot_end))
            cursor += delta
        return slots

    # ------------------------------------------------------------------
    # Book meeting
    # ------------------------------------------------------------------

    async def book_meeting(
        self,
        slot: MeetingSlot,
        lead: LeadProfile,
        title: str = "Product Demo",
    ) -> MeetingBooking:
        """Create a Google Calendar event with the lead as attendee."""
        assert slot.start.tzinfo is not None, "slot.start must be timezone-aware"
        assert slot.end.tzinfo is not None, "slot.end must be timezone-aware"

        event_body = {
            "summary": title,
            "start": {"dateTime": slot.start.isoformat()},
            "end": {"dateTime": slot.end.isoformat()},
            "attendees": [{"email": lead.email}] if lead.email else [],
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
            "description": self._build_description(lead),
        }

        url = _EVENTS_URL.format(calendar_id=self._config.calendar_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=event_body,
                params={"conferenceDataVersion": "1"},
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        resp.raise_for_status()
        event = resp.json()

        meet_link: str | None = None
        entry_points = event.get("conferenceData", {}).get("entryPoints", [])
        for ep in entry_points:
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri")
                break

        logger.info(json.dumps({
            "event": "meeting_booked",
            "provider": "google_calendar",
            "tenant_id": str(self._tenant_id),
            "event_id": event["id"],
        }))

        return MeetingBooking(
            event_id=event["id"],
            calendar_link=event.get("htmlLink", ""),
            start=slot.start,
            end=slot.end,
            attendee_email=lead.email or "",
            meet_link=meet_link,
        )

    @staticmethod
    def _build_description(lead: LeadProfile) -> str:
        lines = [
            "Booked via AI Sales Agent.",
            f"Intent score: {lead.intent_score}/100",
        ]
        if lead.pages_viewed:
            lines.append("Pages viewed: " + ", ".join(lead.pages_viewed[:10]))
        if lead.conversation_summary:
            lines.append(f"Chat summary: {lead.conversation_summary}")
        return "\n".join(lines)
