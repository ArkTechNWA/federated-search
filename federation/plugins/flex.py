"""Flex bank plugin.

Talks to flex MCP over Streamable HTTP (stateless mode).
Single tool: flex_search. Queries via keyword() for text search.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

logger = logging.getLogger(__name__)

# Flex prepends a header like "[3 rows, ~712 tok]\n" before JSON
_FLEX_HEADER_RE = re.compile(r"^\[\d+ rows?, ~[\d.]+[KMB]? tok\]\n", re.MULTILINE)


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
        return json.loads(text)

    @staticmethod
    def _strip_flex_header(text: str) -> str:
        """Remove the '[N rows, ~M tok]' header flex prepends to results."""
        return _FLEX_HEADER_RE.sub("", text, count=1).strip()

    @staticmethod
    def _clean_snippet(text: str) -> str:
        """Remove flex's >>>highlight<<< markers from snippets."""
        return text.replace(">>>", "").replace("<<<", "")

    async def _call_flex(self, query: str, cell: str | None = None) -> str:
        """Call flex_search and return the raw text result."""
        client = await self._ensure_client()

        arguments: dict[str, Any] = {"query": query}
        if cell:
            arguments["cell"] = cell
        elif self.config.cell:
            arguments["cell"] = self.config.cell

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # Initialize (flex is stateless but MCP protocol requires it)
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

        await client.post(
            self.config.url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

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

    async def search(self, query: str, limit: int = 10, mode: str = "broad", domain: str | None = None) -> list[FederatedResult]:
        cell = self.config.cell or "claude_code"

        # Escape single quotes in query for SQL safety
        safe_query = query.replace("'", "''")

        if mode == "semantic":
            # Semantic search via vec_ops — meaning-based, not term-based
            flex_query = (
                f"SELECT v.id, v.score AS rank, '' AS snippet, c.session_id, c.position, "
                f"substr(c.content, 1, 300) AS preview "
                f"FROM vec_ops('similar:{safe_query}', 'SELECT id FROM chunks') v "
                f"JOIN chunks c ON c.id = v.id "
                f"LIMIT {limit}"
            )
        elif mode == "exact":
            # Exact phrase via keyword with quoted phrase
            quoted = '"' + safe_query + '"'
            kw_inner = quoted.replace("'", "''")
            flex_query = (
                f"SELECT k.id, k.rank, k.snippet, c.session_id, c.position, "
                f"substr(c.content, 1, 300) AS preview "
                f"FROM keyword('{kw_inner}', 'SELECT id FROM chunks') k "
                f"JOIN chunks c ON c.id = k.id "
                f"LIMIT {limit}"
            )
        else:
            # Broad keyword search (default)
            flex_query = (
                f"SELECT k.id, k.rank, k.snippet, c.session_id, c.position, "
                f"substr(c.content, 1, 300) AS preview "
                f"FROM keyword('{safe_query}', 'SELECT id FROM chunks') k "
                f"JOIN chunks c ON c.id = k.id "
                f"LIMIT {limit}"
            )

        try:
            raw = await self._call_flex(f"!{flex_query}", cell)
        except Exception as e:
            logger.warning("Flex search failed for bank %s: %s", self.id, e)
            self._status = BankStatus.DEGRADED
            return []

        # Strip the "[N rows, ~M tok]" header and parse JSON array
        json_text = self._strip_flex_header(raw)

        if not json_text or json_text.startswith("{\"error"):
            # Empty results or error response
            if "error" in json_text:
                logger.warning("Flex returned error: %s", json_text[:200])
                self._status = BankStatus.DEGRADED
            return []

        results: list[FederatedResult] = []
        try:
            rows = json.loads(json_text)
            if not isinstance(rows, list):
                rows = [rows]
        except json.JSONDecodeError:
            logger.warning("Could not parse flex response as JSON: %s", json_text[:200])
            return []

        # Deduplicate by session — keep highest-ranked chunk per session
        seen_sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            sid = row.get("session_id", "unknown")
            if sid not in seen_sessions or row.get("rank", 0) > seen_sessions[sid].get("rank", 0):
                seen_sessions[sid] = row

        for i, row in enumerate(seen_sessions.values()):
            rank = row.get("rank", 0.5)
            # Normalize rank to 0-1 range (flex ranks are typically 0-1 already)
            relevance = min(1.0, max(0.1, float(rank)))

            snippet = self._clean_snippet(
                row.get("snippet", row.get("preview", ""))
            )
            session_id = row.get("session_id", "unknown")
            chunk_id = row.get("id", "unknown")

            results.append(FederatedResult(
                bank=self.id,
                bank_label=self.config.label,
                source_type="chunk",
                title=f"Session {session_id[:12]}...",
                snippet=snippet[:300],
                relevance=relevance,
                priority=self.config.priority,
                drill=f"@full id={chunk_id}",
                metadata={
                    "cell": cell,
                    "session_id": session_id,
                    "chunk_id": chunk_id,
                    "position": row.get("position"),
                    "rank": rank,
                },
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
