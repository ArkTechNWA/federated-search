# Federated Search MCP Server

One query, all knowledge. A federation layer that sits in front of multiple memory backends (knowledge graph, flex, SearXNG, future plugins) and presents a unified search surface to agents.

## Quick Reference

```bash
source .venv/bin/activate
python test_live.py                                    # Integration test against live services
python -m federation.server --agent johnny5             # stdio mode
python -m federation.server --agent johnny5 --http --port 4001  # HTTP mode
```

## Architecture

```
federation/
  server.py          # FastMCP server — fed_search + fed_banks tools
  federation.py      # Core engine — fan-out, merge, rank
  config.py          # YAML config loader
  types.py           # FederatedResult envelope, BankConfig, SearchRequest
  plugins/
    base.py          # BankPlugin ABC
    kg.py            # Knowledge graph MCP plugin
    flex.py          # Flex session history plugin
    searxng.py       # SearXNG web search plugin (direct HTTP, not MCP)
```

## Key Concepts

- **Bank**: a registered knowledge source (KG, flex, SearXNG, calendar, ...)
- **Plugin**: translates fed_search into bank-native queries, packs results into a common envelope
- **Priority**: config-defined ordering. Lower number = results sort first.
- **Default**: banks with `default: true` are searched when no `db=` is specified
- **Envelope**: every result has: bank, bank_label, source_type, title, snippet, relevance, drill, metadata

## Config

`config.yaml` — YAML file defining agents and their bank subscriptions. Adding a bank = adding a config block.
