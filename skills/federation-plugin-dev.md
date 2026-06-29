# Federation Plugin Development

How to write a bank plugin for the federated search MCP server. Each plugin translates a backend's native query/response format into the universal `FederatedResult` envelope.

## Plugin Contract

Every plugin extends `BankPlugin` and implements two methods:

```python
from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

class MyBankPlugin(BankPlugin):
    async def search(self, query: str, limit: int = 10) -> list[FederatedResult]:
        """Translate fed_search into native queries, return results."""

    async def health_check(self) -> BankStatus:
        """Return HEALTHY, DEGRADED, or DOWN."""
```

Optional overrides:
- `initialize()` — session setup, auth handshake, connection pooling
- `shutdown()` — cleanup on server stop

## The FederatedResult Envelope

Every result from every bank gets packed into this shape:

| Field | Type | Purpose |
|-------|------|---------|
| `bank` | str | Bank ID from config (e.g., `"knowledge_graph"`) |
| `bank_label` | str | Human-readable label (e.g., `"Curated Knowledge"`) |
| `source_type` | str | What kind of thing matched: `"entity"`, `"chunk"`, `"web"`, `"event"`, etc. |
| `title` | str | What matched — the primary identifier |
| `snippet` | str | Why it matched — preview text, max ~300 chars |
| `relevance` | float | 0.0–1.0, bank-local relevance. Used for sorting within a priority tier. |
| `priority` | int | Bank priority from config. Lower = sorted first. Don't set this manually — it comes from `self.config.priority`. |
| `drill` | str | How to get more. A command/URL the agent can follow for the full content. |
| `metadata` | dict | Anything else. Bank-specific context that might be useful for follow-up. |

## Writing a Plugin: Step by Step

### 1. Create the file

```
federation/plugins/my_backend.py
```

### 2. Implement the class

```python
from __future__ import annotations
import httpx
from federation.plugins.base import BankPlugin
from federation.types import BankConfig, BankStatus, FederatedResult

class MyBackendPlugin(BankPlugin):
    def __init__(self, config: BankConfig) -> None:
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def search(self, query: str, limit: int = 10) -> list[FederatedResult]:
        try:
            # 1. Call your backend's native API
            client = await self._ensure_client()
            resp = await client.get(f"{self.config.url}/search", params={"q": query})
            resp.raise_for_status()
            data = resp.json()

            # 2. Pack each result into a FederatedResult
            results = []
            for i, item in enumerate(data["results"][:limit]):
                results.append(FederatedResult(
                    bank=self.id,
                    bank_label=self.config.label,
                    source_type="document",          # your source type
                    title=item["title"],
                    snippet=item["body"][:300],
                    relevance=max(0.1, 1.0 - i * 0.1),  # positional decay
                    priority=self.config.priority,   # from config, don't override
                    drill=item["url"],               # how to get the full thing
                    metadata={"doc_id": item["id"]}, # anything useful for follow-up
                ))
            self._status = BankStatus.HEALTHY
            return results

        except Exception as e:
            self._status = BankStatus.DEGRADED
            return []  # federation handles graceful degradation

    async def health_check(self) -> BankStatus:
        try:
            client = await self._ensure_client()
            resp = await client.get(f"{self.config.url}/health")
            self._status = BankStatus.HEALTHY if resp.status_code == 200 else BankStatus.DEGRADED
        except Exception:
            self._status = BankStatus.DOWN
        return self._status

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
```

### 3. Register the plugin type

In `federation/federation.py`, add to the registry:

```python
from federation.plugins.my_backend import MyBackendPlugin

PLUGIN_REGISTRY: dict[str, type[BankPlugin]] = {
    "kg": KGBankPlugin,
    "flex": FlexBankPlugin,
    "searxng": SearXNGBankPlugin,
    "my_backend": MyBackendPlugin,  # ← add here
}
```

### 4. Add to config

```yaml
banks:
  - id: my_docs
    type: my_backend          # ← matches registry key
    label: "Document Store"
    description: "Internal document archive"
    priority: 4
    default: true
    url: "http://127.0.0.1:9000"
```

That's it. The federation engine handles fan-out, timeout, merge, and ranking. Your plugin only needs to speak its backend's language.

## Config Available to Plugins

Every plugin gets `self.config` (a `BankConfig`):

| Field | Type | Always present | Notes |
|-------|------|---------------|-------|
| `id` | str | yes | Bank ID |
| `type` | str | yes | Plugin type (registry key) |
| `label` | str | yes | Human-readable name |
| `description` | str | yes | What this bank contains |
| `priority` | int | yes | Sort order in results |
| `default` | bool | yes | Searched when no `db=` specified |
| `url` | str | yes | Backend endpoint |
| `auth` | str | no | `"Bearer <token>"` if needed |
| `cell` | str | no | Flex cell name |
| `synthesis` | bool | no | SearXNG AI summary toggle |
| `extra` | dict | yes | Any unrecognized YAML keys land here |

The `extra` dict is how you pass plugin-specific config without changing the BankConfig class:

```yaml
banks:
  - id: calendar
    type: google_calendar
    # ... standard fields ...
    calendar_id: "primary"        # ← lands in config.extra["calendar_id"]
    lookahead_days: 14            # ← lands in config.extra["lookahead_days"]
```

Access in code: `self.config.extra.get("calendar_id", "primary")`

## Patterns from Existing Plugins

### KG Plugin — MCP over Streamable HTTP

- Maintains an MCP session (initialize → notifications/initialized → tools/call)
- Parses SSE-framed responses (`data: {json}`)
- Double-deserialization: `json.loads(result["content"][0]["text"])`
- Session expires after 10 min — re-initialize on next search
- Auth via Bearer token in headers

### Flex Plugin — MCP Stateless

- Also MCP Streamable HTTP, but server is stateless (no session persistence)
- Still must initialize per-call for MCP protocol compliance
- Response has a `[N rows, ~M tok]` header before JSON — strip it
- `>>>highlight<<<` markers in snippets — clean them
- Deduplicate results by session ID, keep highest-ranked chunk per session

### SearXNG Plugin — Direct HTTP API (no MCP)

- Hits the SearXNG JSON API directly, no MCP wrapper needed
- Optional DeepSeek synthesis: separate API call with search results as context
- Synthesis result inserted at position 0 as `source_type="synthesis"`

## Rules

1. **Never raise from `search()`.** Return `[]` on failure. Set `self._status = BankStatus.DEGRADED`. Federation logs the warning and continues with other banks.
2. **Relevance is bank-local.** 0.0–1.0 within your results. Don't try to normalize against other banks — federation uses priority for cross-bank ordering.
3. **Drill must be actionable.** The agent should be able to copy-paste it to get full content. Entity names for KG, chunk IDs for flex, URLs for web, etc.
4. **Snippet max ~300 chars.** Enough to be useful, short enough to scan.
5. **source_type is free-form but consistent.** Use the same string for the same kind of result. The agent may filter or group by it.
6. **Use `httpx.AsyncClient` for HTTP backends.** Connection pooling, async, timeouts built in. Create once in `_ensure_client()`, close in `shutdown()`.
7. **Health checks should be fast.** Hit a lightweight endpoint (e.g., `/health`, `/config`). Don't run a full query.
