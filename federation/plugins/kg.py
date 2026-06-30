"""Knowledge Graph bank plugin.

Talks to j5ed-knowledge-graph MCP over Streamable HTTP.
SSE-framed responses, Bearer auth, session-based.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

logger = logging.getLogger(__name__)


class KGBankPlugin(BankPlugin):
    """Plugin for knowledge graph MCP backends."""

    def __init__(self, config: BankConfig) -> None:
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.config.auth:
            headers["Authorization"] = self.config.auth
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any]:
        """Extract JSON from SSE data: line."""
        for line in text.strip().splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise ValueError(f"No SSE data line in response: {text[:200]}")

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _create_session(self) -> None:
        """Initialize a fresh MCP session."""
        client = await self._ensure_client()
        self._session_id = None  # clear stale session

        resp = await client.post(
            self.config.url,
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "federation", "version": "0.1"},
                },
                "id": self._next_id(),
            },
        )
        resp.raise_for_status()
        self._session_id = resp.headers.get("mcp-session-id")

        await client.post(
            self.config.url,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    async def _call_tool(self, name: str, arguments: dict[str, Any], retry: bool = True) -> Any:
        """Call an MCP tool. Re-initializes session on failure."""
        if not self._session_id:
            await self._create_session()

        client = await self._ensure_client()

        try:
            resp = await client.post(
                self.config.url,
                headers=self._headers(),
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                    "id": self._next_id(),
                },
            )
            resp.raise_for_status()
            sse = self._parse_sse(resp.text)
            content_text = sse["result"]["content"][0]["text"]
            return json.loads(content_text)
        except Exception:
            if retry:
                # Session likely expired — re-init and retry once
                logger.info("KG session expired for bank %s, re-initializing", self.id)
                await self._create_session()
                return await self._call_tool(name, arguments, retry=False)
            raise

    async def search(self, query: str, limit: int = 10, mode: str = "broad", domain: str | None = None) -> list[FederatedResult]:
        try:
            # Resolve domain to index members if specified
            domain_members: set[str] | None = None
            if domain:
                domain_map = self.config.extra.get("domain_map", {})
                index_name = domain_map.get(domain)
                if not index_name:
                    logger.warning("Unknown domain '%s' for bank %s. Available: %s",
                                   domain, self.id, list(domain_map.keys()))
                    return []
                # Get index members via open_nodes
                try:
                    index_data = await self._call_tool("open_nodes", {"names": [index_name]})
                    # Members are entities with indexed_in relations TO this index
                    relations = index_data.get("relations", [])
                    domain_members = {
                        r["from"] for r in relations
                        if r.get("relationType") == "indexed_in" and r.get("to") == index_name
                    }
                except Exception as e:
                    logger.warning("Failed to resolve domain '%s': %s", domain, e)
                    return []

            # In exact mode, wrap in quotes for FTS5 phrase matching
            search_query = '"' + query + '"' if mode == "exact" else query
            data = await self._call_tool("search_nodes", {"query": search_query})
        except Exception as e:
            logger.warning("KG search failed for bank %s: %s", self.id, e)
            self._status = BankStatus.DEGRADED
            return []

        results: list[FederatedResult] = []
        for tier_idx, tier in enumerate(data.get("tiers", [])):
            for entity in tier.get("entities", []):
                # Name matches score higher than observation-only matches
                name_match = 1 if "name" in entity.get("matchedIn", []) else 0
                obs_match = 1 if "observation" in entity.get("matchedIn", []) else 0
                type_match = 1 if "type" in entity.get("matchedIn", []) else 0
                base = 0.3 + name_match * 0.4 + obs_match * 0.15 + type_match * 0.1
                tier_penalty = tier_idx * 0.05
                relevance = min(1.0, max(0.1, base - tier_penalty))

                results.append(FederatedResult(
                    bank=self.id,
                    bank_label=self.config.label,
                    source_type="entity",
                    title=entity["name"],
                    snippet=entity.get("snippet", f"[{entity['type']}]"),
                    relevance=relevance,
                    priority=self.config.priority,
                    drill=f"open_nodes([\"{entity['name']}\"])",
                    metadata={
                        "entity_type": entity.get("type"),
                        "matched_in": entity.get("matchedIn", []),
                    },
                ))

        # Filter by domain membership if specified
        if domain_members is not None:
            results = [r for r in results if r.title in domain_members]

        # Sort by relevance descending, then truncate
        results.sort(key=lambda r: -r.relevance)
        if limit > 0:
            results = results[:limit]

        self._status = BankStatus.HEALTHY
        return results

    async def health_check(self) -> BankStatus:
        try:
            client = await self._ensure_client()
            base = self.config.url.replace("/mcp", "")
            resp = await client.get(f"{base}/health")
            if resp.status_code == 200:
                self._status = BankStatus.HEALTHY
            else:
                self._status = BankStatus.DEGRADED
        except Exception:
            self._status = BankStatus.DOWN
        return self._status

    async def initialize(self) -> None:
        # Don't create session/client here. Init runs in a different event
        # loop than FastMCP tool calls. Clients created lazily on first use.
        pass

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._session_id = None
