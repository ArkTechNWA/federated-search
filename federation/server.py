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
from federation.types import SearchRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("federation")

# Globals — set during lifespan
_engine: FederationEngine | None = None
_agent_name: str = "unknown"


def _get_engine() -> FederationEngine:
    if _engine is None:
        raise RuntimeError("Federation engine not initialized")
    return _engine


# --- MCP Server ---

mcp = FastMCP(
    "federated-search",
    version="0.1.0",
    description="Federated memory search — one query, all knowledge",
)


@mcp.tool()
async def fed_search(
    query: str,
    db: str | None = None,
    limit: int = 10,
) -> str:
    """Search across all subscribed memory banks.

    Returns results from registered knowledge sources, ranked by priority and relevance.

    Args:
        query: What to search for.
        db: Optional. Bank ID or comma-separated bank IDs to search.
              Omit to search all default banks.
              Example: db="knowledge_graph" or db="flex,web"
        limit: Max results to return. Default 10. Use -1 for unlimited.
    """
    engine = _get_engine()

    # Parse db argument
    db_list: list[str] | None = None
    if db:
        db_list = [b.strip() for b in db.split(",")]

    request = SearchRequest(query=query, db=db_list, limit=limit)
    result = await engine.search(request)
    return json.dumps(result, indent=2)


@mcp.tool()
async def fed_banks() -> str:
    """Discover registered memory banks, their priorities, and health status.

    Returns metadata for all banks configured for this agent endpoint.
    """
    engine = _get_engine()
    banks = await engine.check_all_health()
    return json.dumps([
        {
            "id": b.id,
            "type": b.type,
            "label": b.label,
            "description": b.description,
            "priority": b.priority,
            "default": b.default,
            "status": b.status.value,
        }
        for b in banks
    ], indent=2)


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(description="Federated Search MCP Server")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to federation config YAML")
    parser.add_argument("--agent", type=str, required=True,
                        help="Agent name (must match a key in config.agents)")
    parser.add_argument("--http", action="store_true",
                        help="Use Streamable HTTP transport")
    parser.add_argument("--port", type=int, default=4001,
                        help="HTTP port (default: from agent config or 4001)")
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
    global _agent_name
    _agent_name = args.agent

    port = args.port or agent_config.port

    # Initialize engine
    global _engine
    engine = FederationEngine(agent_config)

    async def run():
        global _engine
        _engine = engine
        await engine.initialize()
        logger.info("Federation engine ready for agent '%s' with %d banks",
                     args.agent, len(engine._plugins))

        try:
            if args.http:
                mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
            else:
                mcp.run(transport="stdio")
        finally:
            await engine.shutdown()

    asyncio.run(run())


if __name__ == "__main__":
    main()
