# Federated Search

One query, all knowledge. A federation MCP server that sits in front of multiple memory backends and presents a unified search surface to AI agents.

## What It Does

Instead of an agent juggling 3-4 MCP connections and deciding which backend to query, federation handles it:

```
Agent → fed_search("Kronos") → Federation
                                   ├→ Knowledge Graph (curated entities)
                                   ├→ Flex (session history)
                                   └→ SearXNG (web, opt-in)
                                ← merged, ranked, one response
```

Results are priority-ordered by bank, relevance-ranked within each bank, and filtered for signal quality.

## Tools

### `fed_search(query, db?, limit?, mode?, domain?)`

Search across all subscribed memory banks.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `query` | required | What to search for |
| `db` | all defaults | Bank ID or comma-separated IDs. `"knowledge_graph"`, `"flex,web"` |
| `limit` | 10 | Max results. `-1` for unlimited |
| `mode` | `"broad"` | `"broad"`, `"exact"` (phrase match), or `"semantic"` (meaning-based) |
| `domain` | none | Pre-filter KG results to an index alias. `"infrastructure"`, `"api"` |

### `fed_banks()`

Returns registered banks with priorities, descriptions, and health status.

## Architecture

```
federation/
  server.py          # FastMCP server — tool definitions
  federation.py      # Core engine — fan-out, merge, rank
  config.py          # YAML config loader
  types.py           # FederatedResult envelope, BankConfig, SearchRequest
  filters.py         # Signal quality — confidence floor, adaptive count, dedup
  formatter.py       # Markdown output formatting
  plugins/
    base.py          # BankPlugin ABC
    kg.py            # Knowledge graph MCP plugin
    flex.py          # Flex session history plugin
    searxng.py       # SearXNG web search plugin
```

## Plugin System

Each backend is a plugin that translates `fed_search` into native queries and packs results into a universal envelope:

```python
class MyPlugin(BankPlugin):
    async def search(self, query, limit=10, mode="broad", domain=None):
        # Call your backend, return list[FederatedResult]

    async def health_check(self):
        # Return BankStatus.HEALTHY / DEGRADED / DOWN
```

Adding a new bank = write a plugin class + add a YAML config block. No core changes.

See `skills/federation-plugin-dev.md` for the full plugin development guide.

## Signal Quality

- **Query validation** — rejects empty, single-char, and stopword queries
- **Confidence floor** — results below 0.25 relevance get cut
- **Adaptive count** — when strong results exist, weak tail is trimmed with a note
- **Bank representation** — each bank gets at least 1 result slot
- **Cross-bank annotation** — flex chunks referencing KG entities get `overlaps_with` metadata

## Config

`config.yaml` defines agents and their bank subscriptions:

```yaml
agents:
  my_agent:
    port: 4001
    banks:
      - id: knowledge_graph
        type: kg
        label: "Curated Knowledge"
        description: "Agent-curated structured knowledge graph"
        priority: 1        # lower = results sort first
        default: true       # searched when no db= specified
        url: "http://127.0.0.1:3101/mcp"
        auth: "Bearer ${KG_AUTH_TOKEN}"
      - id: web
        type: searxng
        priority: 99
        default: false      # opt-in only
        url: "http://your-searxng:8080"
```

Copy `config.yaml` to `config.local.yaml` and fill in real values. The local config is gitignored.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
# stdio mode (for Claude Code MCP)
python -m federation.server --agent my_agent --config config.local.yaml

# HTTP mode
python -m federation.server --agent my_agent --config config.local.yaml --http --port 4001
```

### Add to Claude Code

```bash
claude mcp add fed-search -s user -- \
  /path/to/.venv/bin/python -m federation.server \
  --agent my_agent --config /path/to/config.local.yaml
```

## License

MIT
