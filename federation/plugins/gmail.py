"""Gmail bank plugin.

Searches email as a memory surface.
"What did Jane say about the contract?" is a memory question.

Requires: google-auth, google-api-python-client
Config extras: credentials_path, token_path, max_age_days
"""

from __future__ import annotations

import logging
from typing import Any

from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

logger = logging.getLogger(__name__)


class GmailPlugin(BankPlugin):
    """Plugin for Gmail as a searchable memory surface."""

    def __init__(self, config: BankConfig) -> None:
        super().__init__(config)
        self._service = None

    def _get_service(self):
        """Lazy-init the Gmail API service."""
        if self._service is not None:
            return self._service

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            token_path = self.config.extra.get("token_path", "token.json")
            creds = Credentials.from_authorized_user_file(token_path)
            self._service = build("gmail", "v1", credentials=creds)
            return self._service
        except Exception as e:
            logger.warning("Failed to init Gmail service: %s", e)
            self._status = BankStatus.DOWN
            return None

    async def search(self, query: str, limit: int = 10, mode: str = "broad", domain: str | None = None) -> list[FederatedResult]:
        service = self._get_service()
        if not service:
            return []

        max_age = int(self.config.extra.get("max_age_days", 365))

        # Gmail search query — supports the same operators as the Gmail UI
        gmail_query = query
        if mode == "exact":
            gmail_query = f'"{query}"'
        if max_age:
            gmail_query += f" newer_than:{max_age}d"

        try:
            # Search for message IDs
            msg_list = (
                service.users()
                .messages()
                .list(userId="me", q=gmail_query, maxResults=limit)
                .execute()
            )
            messages = msg_list.get("messages", [])
        except Exception as e:
            logger.warning("Gmail search failed: %s", e)
            self._status = BankStatus.DEGRADED
            return []

        results: list[FederatedResult] = []
        for i, msg_stub in enumerate(messages):
            try:
                # Fetch message metadata (not full body — too expensive for search)
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_stub["id"], format="metadata",
                         metadataHeaders=["Subject", "From", "To", "Date"])
                    .execute()
                )
            except Exception:
                continue

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "No subject")
            sender = headers.get("From", "Unknown")
            date = headers.get("Date", "")
            snippet = msg.get("snippet", "")

            # Build readable snippet
            parts = []
            if sender:
                parts.append(f"From: {sender[:50]}")
            if date:
                parts.append(date[:25])
            if snippet:
                parts.append(snippet[:150])
            full_snippet = " | ".join(parts)

            results.append(FederatedResult(
                bank=self.id,
                bank_label=self.config.label,
                source_type="email",
                title=subject,
                snippet=full_snippet[:300],
                relevance=max(0.1, 1.0 - (i * 0.05)),
                priority=self.config.priority,
                drill=f"message_id:{msg_stub['id']}",
                metadata={
                    "message_id": msg_stub["id"],
                    "thread_id": msg_stub.get("threadId"),
                    "from": sender,
                    "date": date,
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
            service.users().getProfile(userId="me").execute()
            self._status = BankStatus.HEALTHY
        except Exception:
            self._status = BankStatus.DEGRADED
        return self._status

    async def shutdown(self) -> None:
        self._service = None
