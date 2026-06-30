"""Live integration test — hit real services."""

import asyncio
import json
import sys
sys.path.insert(0, ".")

from federation.config import FederationConfig
from federation.federation import FederationEngine
from federation.types import SearchRequest


async def main():
    config = FederationConfig.load("config.yaml")
    agent_name = list(config.agents.keys())[0]
    agent = config.agents[agent_name]
    engine = FederationEngine(agent)

    print("=== Initializing Federation Engine ===")
    await engine.initialize()

    print("\n=== fed_banks() ===")
    banks = engine.get_banks()
    for b in banks:
        print(f"  {b.id}: {b.label} (priority={b.priority}, default={b.default}, status={b.status.value})")

    print("\n=== fed_search('infrastructure') — all defaults ===")
    result = await engine.search(SearchRequest(query="infrastructure", limit=5))
    print(json.dumps(result, indent=2))

    print("\n=== fed_search('infrastructure', db='knowledge_graph') — KG only ===")
    result = await engine.search(SearchRequest(query="infrastructure", db=["knowledge_graph"], limit=5))
    print(json.dumps(result, indent=2))

    print("\n=== fed_search('infrastructure', db='flex') — Flex only ===")
    result = await engine.search(SearchRequest(query="infrastructure", db=["flex"], limit=5))
    print(json.dumps(result, indent=2))

    print("\n=== fed_search('python', db='web') — SearXNG ===")
    result = await engine.search(SearchRequest(query="python asyncio tutorial", db=["web"], limit=3))
    print(json.dumps(result, indent=2))

    await engine.shutdown()
    print("\n=== Done ===")


asyncio.run(main())
