"""Federation MCP server — one endpoint, all knowledge."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from federation.config import FederationConfig
from federation.federation import FederationEngine
from federation.formatter import format_banks, format_results
from federation.types import SearchRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("federation")

# Globals — set during startup
_engine: FederationEngine | None = None
_agent_name: str = "unknown"


def _get_engine() -> FederationEngine:
    if _engine is None:
        raise RuntimeError("Federation engine not initialized")
    return _engine


# --- MCP Server ---

mcp = FastMCP("federated-search")


@mcp.tool()
async def fed_search(
    query: str,
    db: str | None = None,
    limit: int = 10,
    mode: str = "broad",
) -> str:
    """Search across all subscribed memory banks.

    Returns results from registered knowledge sources, ranked by priority and relevance.

    Args:
        query: What to search for.
        db: Optional. Bank ID or comma-separated bank IDs to search.
              Omit to search all default banks.
              Example: db="knowledge_graph" or db="flex,web"
        limit: Max results to return. Default 10. Use -1 for unlimited.
        mode: Search mode. "broad" (default), "exact" (quoted phrase), or "semantic" (meaning-based).
    """
    engine = _get_engine()

    db_list: list[str] | None = None
    if db:
        db_list = [b.strip() for b in db.split(",")]

    request = SearchRequest(query=query, db=db_list, limit=limit, mode=mode)
    result = await engine.search(request)
    return format_results(result)


@mcp.tool()
async def fed_banks() -> str:
    """Discover registered memory banks, their priorities, and health status.

    Returns metadata for all banks configured for this agent endpoint.
    """
    engine = _get_engine()
    banks = await engine.check_all_health()
    return format_banks([{"id": b.id, "type": b.type, "label": b.label, "description": b.description, "priority": b.priority, "default": b.default, "status": b.status.value} for b in banks])


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(description="Federated Search MCP Server")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to federation config YAML")
    parser.add_argument("--agent", type=str, required=True,
                        help="Agent name (must match a key in config.agents)")
    parser.add_argument("--http", action="store_true",
                        help="Use Streamable HTTP transport")
    parser.add_argument("--port", type=int, default=None,
                        help="HTTP port (default: from agent config)")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    config = FederationConfig.load(config_path)

    if args.agent not in config.agents:
        logger.error("Agent '%s' not found in config. Available: %s",
                      args.agent, list(config.agents.keys()))
        sys.exit(1)

    agent_config = config.agents[args.agent]
    global _agent_name, _engine
    _agent_name = args.agent

    engine = FederationEngine(agent_config)
    _engine = engine

    # Initialize engine synchronously before starting MCP
    loop = asyncio.new_event_loop()
    loop.run_until_complete(engine.initialize())
    loop.close()

    logger.info("Federation engine ready for agent '%s' with %d banks",
                 args.agent, len(engine._plugins))

    if args.http:
        port = args.port or agent_config.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
