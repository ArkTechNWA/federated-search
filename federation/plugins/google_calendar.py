"""Google Calendar bank plugin.

Searches calendar events as a memory surface.
"What meeting did I have about X?" is a memory question, not a scheduling question.

Requires: google-auth, google-api-python-client
Config extras: credentials_path, calendar_id (default "primary"), lookahead_days, lookback_days
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

logger = logging.getLogger(__name__)


class GoogleCalendarPlugin(BankPlugin):
    """Plugin for Google Calendar as a searchable memory surface."""

    def __init__(self, config: BankConfig) -> None:
        super().__init__(config)
        self._service = None  # google calendar API service

    def _get_service(self):
        """Lazy-init the Google Calendar API service."""
        if self._service is not None:
            return self._service

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds_path = self.config.extra.get("credentials_path", "credentials.json")
            token_path = self.config.extra.get("token_path", "token.json")

            # TODO: implement OAuth flow or load saved token
            # For now, expects a pre-authorized token.json
            creds = Credentials.from_authorized_user_file(token_path)
            self._service = build("calendar", "v3", credentials=creds)
            return self._service
        except Exception as e:
            logger.warning("Failed to init Google Calendar service: %s", e)
            self._status = BankStatus.DOWN
            return None

    async def search(self, query: str, limit: int = 10, mode: str = "broad", domain: str | None = None) -> list[FederatedResult]:
        service = self._get_service()
        if not service:
            return []

        calendar_id = self.config.extra.get("calendar_id", "primary")
        lookback = int(self.config.extra.get("lookback_days", 90))
        lookahead = int(self.config.extra.get("lookahead_days", 30))

        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(days=lookback)).isoformat()
        time_max = (now + timedelta(days=lookahead)).isoformat()

        try:
            # Google Calendar API has a built-in q= parameter for free-text search
            events_result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    q=query,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=limit,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = events_result.get("items", [])
        except Exception as e:
            logger.warning("Calendar search failed: %s", e)
            self._status = BankStatus.DEGRADED
            return []

        results: list[FederatedResult] = []
        for i, event in enumerate(events):
            summary = event.get("summary", "Untitled event")
            start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
            end = event.get("end", {}).get("dateTime", event.get("end", {}).get("date", ""))
            description = event.get("description", "")
            attendees = [a.get("email", "") for a in event.get("attendees", [])]
            location = event.get("location", "")

            # Build a useful snippet
            parts = [start]
            if attendees:
                parts.append(f"with {', '.join(attendees[:3])}")
            if location:
                parts.append(f"at {location}")
            if description:
                parts.append(description[:100])
            snippet = " | ".join(parts)

            results.append(FederatedResult(
                bank=self.id,
                bank_label=self.config.label,
                source_type="event",
                title=summary,
                snippet=snippet[:300],
                relevance=max(0.1, 1.0 - (i * 0.05)),
                priority=self.config.priority,
                drill=event.get("htmlLink", ""),
                metadata={
                    "event_id": event.get("id"),
                    "start": start,
                    "end": end,
                    "attendees": attendees[:5],
                    "calendar": calendar_id,
                },
            ))

        self._status = BankStatus.HEALTHY
        return results

    async def health_check(self) -> BankStatus:
        service = self._get_service()
        if service is None:
            self._status = BankStatus.DOWN
            return self._status
        try:
            service.calendarList().list(maxResults=1).execute()
            self._status = BankStatus.HEALTHY
        except Exception:
            self._status = BankStatus.DEGRADED
        return self._status

    async def shutdown(self) -> None:
        self._service = None
