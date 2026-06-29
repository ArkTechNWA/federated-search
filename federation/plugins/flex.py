"""Flex bank plugin.

Talks to flex MCP over Streamable HTTP (stateless mode).
Single tool: flex_search. Queries via keyword() for text search.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

logger = logging.getLogger(__name__)


class FlexBankPlugin(BankPlugin):
    """Plugin for flex session history backends."""

    def __init__(self, config: BankConfig) -> None:
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any]:
        for line in text.strip().splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        # Flex may return plain JSON in stateless mode
        return json.loads(text)

    async def _call_flex(self, query: str, cell: str | None = None) -> str:
        """Call flex_search and return the raw text result."""
        client = await self._ensure_client()

        arguments: dict[str, Any] = {"query": query}
        if cell:
            arguments["cell"] = cell
        elif self.config.cell:
            arguments["cell"] = self.config.cell

        # Flex is stateless — no session init needed
        # But we still need to initialize for MCP protocol compliance
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # Initialize
        init_resp = await client.post(
            self.config.url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "federation", "version": "0.1"},
                },
                "id": 1,
            },
        )
        init_resp.raise_for_status()

        session_id = init_resp.headers.get("mcp-session-id")
        if session_id:
            headers["mcp-session-id"] = session_id

        # Initialized notification
        await client.post(
            self.config.url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        # Call tool
        resp = await client.post(
            self.config.url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "flex_search", "arguments": arguments},
                "id": 2,
            },
        )
        resp.raise_for_status()

        parsed = self._parse_sse(resp.text)
        return parsed["result"]["content"][0]["text"]

    async def search(self, query: str, limit: int = 10) -> list[FederatedResult]:
        cell = self.config.cell or "claude_code"

        # Use keyword search for federated queries — most reliable for term matching
        flex_query = (
            f"SELECT k.id, k.rank, k.snippet, c.session_id, c.position, "
            f"substr(c.content, 1, 300) AS preview "
            f"FROM keyword('{query}', 'SELECT id FROM chunks') k "
            f"JOIN chunks c ON c.id = k.id "
            f"LIMIT {limit}"
        )

        try:
            raw = await self._call_flex(f"!{flex_query}", cell)
        except Exception as e:
            logger.warning("Flex search failed for bank %s: %s", self.id, e)
            self._status = BankStatus.DEGRADED
            return []

        results: list[FederatedResult] = []
        # Flex returns tabular text — parse what we can
        # The raw result is text output from SQLite, format varies
        # We'll treat it as contextual search results
        lines = raw.strip().split("\n") if raw.strip() else []

        if not lines:
            return results

        # Try to parse as structured data if it's JSON-like
        try:
            parsed_rows = json.loads(raw)
            if isinstance(parsed_rows, list):
                for i, row in enumerate(parsed_rows[:limit]):
                    results.append(FederatedResult(
                        bank=self.id,
                        bank_label=self.config.label,
                        source_type="chunk",
                        title=f"Session {row.get('session_id', 'unknown')}",
                        snippet=row.get("preview", row.get("snippet", str(row)[:200])),
                        relevance=max(0.1, 1.0 - (i * 0.1)),
                        priority=self.config.priority,
                        drill=f"@full id={row.get('id', '?')}",
                        metadata={
                            "cell": cell,
                            "session_id": row.get("session_id"),
                            "position": row.get("position"),
                            "rank": row.get("rank"),
                        },
                    ))
                self._status = BankStatus.HEALTHY
                return results
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: treat the entire response as a single contextual result
        results.append(FederatedResult(
            bank=self.id,
            bank_label=self.config.label,
            source_type="chunk",
            title=f"Flex results ({cell})",
            snippet=raw[:500],
            relevance=0.5,
            priority=self.config.priority,
            drill=f"flex_search query in cell '{cell}'",
            metadata={"cell": cell, "raw_length": len(raw)},
        ))

        self._status = BankStatus.HEALTHY
        return results

    async def health_check(self) -> BankStatus:
        try:
            client = await self._ensure_client()
            base = self.config.url.rstrip("/").replace("/mcp", "")
            resp = await client.get(f"{base}/health")
            self._status = BankStatus.HEALTHY if resp.status_code == 200 else BankStatus.DEGRADED
        except Exception:
            self._status = BankStatus.DOWN
        return self._status

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
