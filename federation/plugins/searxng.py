"""SearXNG bank plugin.

Hits SearXNG JSON API directly (no MCP wrapper needed).
Optional DeepSeek synthesis for AI-summarized results.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

logger = logging.getLogger(__name__)


class SearXNGBankPlugin(BankPlugin):
    """Plugin for SearXNG web search backends."""

    def __init__(self, config: BankConfig) -> None:
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._deepseek_key: str | None = None
        if config.deepseek_api_key_env:
            self._deepseek_key = os.environ.get(config.deepseek_api_key_env)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _get_synthesis(self, query: str, results: list[dict[str, Any]]) -> str | None:
        """Get DeepSeek AI synthesis of search results."""
        if not self._deepseek_key or not results:
            return None

        client = await self._ensure_client()
        context = "\n\n".join(
            f"[{i+1}] {r.get('title', 'No title')}\n{r.get('url', '')}\n{r.get('content', '')}"
            for i, r in enumerate(results[:10])
        )

        try:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._deepseek_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [
                        {
                            "role": "system",
                            "content": "Synthesize search results concisely. Cite sources with [n].",
                        },
                        {
                            "role": "user",
                            "content": f"Query: {query}\n\nResults:\n{context}",
                        },
                    ],
                    "max_tokens": 500,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("DeepSeek synthesis failed: %s", e)
            return None

    async def search(self, query: str, limit: int = 10) -> list[FederatedResult]:
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.config.url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("SearXNG search failed for bank %s: %s", self.id, e)
            self._status = BankStatus.DEGRADED
            return []

        raw_results = data.get("results", [])[:limit]
        results: list[FederatedResult] = []

        for i, r in enumerate(raw_results):
            results.append(FederatedResult(
                bank=self.id,
                bank_label=self.config.label,
                source_type="web",
                title=r.get("title", "Untitled"),
                snippet=r.get("content", "")[:300],
                relevance=max(0.1, 1.0 - (i * 0.05)),
                priority=self.config.priority,
                drill=r.get("url", ""),
                metadata={
                    "url": r.get("url"),
                    "engine": r.get("engine"),
                    "published": r.get("publishedDate"),
                },
            ))

        # Add synthesis as a bonus result at the top if enabled
        if self.config.synthesis and raw_results:
            synthesis = await self._get_synthesis(query, raw_results)
            if synthesis:
                results.insert(0, FederatedResult(
                    bank=self.id,
                    bank_label=self.config.label,
                    source_type="synthesis",
                    title=f"AI Summary: {query}",
                    snippet=synthesis,
                    relevance=1.0,
                    priority=self.config.priority,
                    drill="(synthesized from web results)",
                    metadata={"type": "deepseek_synthesis"},
                ))

        self._status = BankStatus.HEALTHY
        return results

    async def health_check(self) -> BankStatus:
        try:
            client = await self._ensure_client()
            resp = await client.get(f"{self.config.url}/config")
            self._status = BankStatus.HEALTHY if resp.status_code == 200 else BankStatus.DEGRADED
        except Exception:
            self._status = BankStatus.DOWN
        return self._status

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
