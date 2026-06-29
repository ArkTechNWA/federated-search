"""Core federation logic — fan-out, merge, rank."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from federation.config import AgentConfig
from federation.plugins.base import BankPlugin
from federation.plugins.flex import FlexBankPlugin
from federation.plugins.kg import KGBankPlugin
from federation.plugins.searxng import SearXNGBankPlugin
from federation.types import BankConfig, BankInfo, BankStatus, FederatedResult, SearchRequest

logger = logging.getLogger(__name__)

# Plugin registry — maps bank type strings to plugin classes
PLUGIN_REGISTRY: dict[str, type[BankPlugin]] = {
    "kg": KGBankPlugin,
    "flex": FlexBankPlugin,
    "searxng": SearXNGBankPlugin,
}


class FederationEngine:
    """Orchestrates search across multiple memory banks."""

    def __init__(self, agent_config: AgentConfig) -> None:
        self.agent_config = agent_config
        self._plugins: dict[str, BankPlugin] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Create and initialize all bank plugins."""
        for bank_cfg in self.agent_config.banks:
            plugin_cls = PLUGIN_REGISTRY.get(bank_cfg.type)
            if plugin_cls is None:
                logger.warning("Unknown bank type '%s' for bank '%s', skipping",
                               bank_cfg.type, bank_cfg.id)
                continue
            plugin = plugin_cls(bank_cfg)
            try:
                await plugin.initialize()
                logger.info("Initialized bank: %s (%s)", bank_cfg.id, bank_cfg.type)
            except Exception as e:
                logger.warning("Failed to initialize bank %s: %s", bank_cfg.id, e)
                plugin._status = BankStatus.DEGRADED
            self._plugins[bank_cfg.id] = plugin
        self._initialized = True

    async def shutdown(self) -> None:
        """Shut down all plugins."""
        for plugin in self._plugins.values():
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.warning("Error shutting down bank %s: %s", plugin.id, e)

    def _resolve_banks(self, db: list[str] | None) -> list[BankPlugin]:
        """Resolve which banks to query based on db= argument."""
        if db is None:
            # All default banks, sorted by priority
            plugins = [p for p in self._plugins.values()
                       if p.config.default and p.status != BankStatus.DOWN]
        else:
            # Specific banks in the order requested
            plugins = []
            for bank_id in db:
                if bank_id in self._plugins:
                    p = self._plugins[bank_id]
                    if p.status != BankStatus.DOWN:
                        plugins.append(p)
                    else:
                        logger.warning("Bank '%s' is DOWN, skipping", bank_id)
                else:
                    logger.warning("Unknown bank '%s' requested", bank_id)

        return sorted(plugins, key=lambda p: p.priority)

    def _distribute_limits(self, banks: list[BankPlugin], total_limit: int) -> dict[str, int]:
        """Distribute result limit across banks proportionally by priority.

        Higher priority (lower number) gets more slots.
        """
        if total_limit < 0:
            return {b.id: 999 for b in banks}  # unlimited

        if not banks:
            return {}

        # Inverse priority weights: priority 1 → weight 10, priority 99 → weight 1
        weights = {b.id: max(1, 100 - b.priority) for b in banks}
        total_weight = sum(weights.values())

        limits = {}
        remaining = total_limit
        for i, bank in enumerate(banks):
            if i == len(banks) - 1:
                # Last bank gets remainder
                limits[bank.id] = max(1, remaining)
            else:
                share = max(1, int(total_limit * weights[bank.id] / total_weight))
                limits[bank.id] = share
                remaining -= share

        return limits

    async def search(self, request: SearchRequest) -> dict[str, Any]:
        """Execute federated search — fan-out to banks, merge results."""
        banks = self._resolve_banks(request.db)

        if not banks:
            return {
                "query": request.query,
                "results": [],
                "banks_queried": [],
                "hint": self._available_banks_hint(),
            }

        limits = self._distribute_limits(banks, request.limit)

        # Fan out — all banks in parallel
        tasks = {
            bank.id: asyncio.create_task(
                bank.search(request.query, limits.get(bank.id, 5))
            )
            for bank in banks
        }

        # Gather results with timeout
        all_results: list[FederatedResult] = []
        banks_queried: list[dict[str, Any]] = []

        done, pending = await asyncio.wait(
            tasks.values(), timeout=15.0, return_when=asyncio.ALL_COMPLETED
        )

        # Cancel any stragglers
        for task in pending:
            task.cancel()

        for bank_id, task in tasks.items():
            plugin = self._plugins[bank_id]
            if task in done and not task.cancelled():
                try:
                    results = task.result()
                    all_results.extend(results)
                    banks_queried.append({
                        "id": bank_id,
                        "label": plugin.config.label,
                        "status": "ok",
                        "result_count": len(results),
                    })
                except Exception as e:
                    banks_queried.append({
                        "id": bank_id,
                        "label": plugin.config.label,
                        "status": "error",
                        "error": str(e),
                    })
            else:
                banks_queried.append({
                    "id": bank_id,
                    "label": plugin.config.label,
                    "status": "timeout",
                })

        # Sort: primary by priority (lower = better), secondary by relevance (higher = better)
        all_results.sort(key=lambda r: (r.priority, -r.relevance))

        # Trim to total limit
        if request.limit > 0:
            all_results = all_results[:request.limit]

        response: dict[str, Any] = {
            "query": request.query,
            "results": [self._result_to_dict(r) for r in all_results],
            "banks_queried": banks_queried,
            "total": len(all_results),
        }

        # Add hint about available banks if using defaults
        if request.db is None:
            non_default = [p for p in self._plugins.values() if not p.config.default]
            if non_default:
                response["hint"] = (
                    f"For specific results, use db='<bank>'. "
                    f"Additional banks available: "
                    + ", ".join(f"{p.id} ({p.config.description})" for p in non_default)
                )

        return response

    def get_banks(self) -> list[BankInfo]:
        """Return metadata about all registered banks."""
        return [
            BankInfo(
                id=p.id,
                type=p.config.type,
                label=p.config.label,
                description=p.config.description,
                priority=p.priority,
                default=p.config.default,
                status=p.status,
            )
            for p in sorted(self._plugins.values(), key=lambda p: p.priority)
        ]

    async def check_all_health(self) -> list[BankInfo]:
        """Run health checks on all banks and return updated statuses."""
        tasks = [p.health_check() for p in self._plugins.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.get_banks()

    def _available_banks_hint(self) -> str:
        all_banks = sorted(self._plugins.values(), key=lambda p: p.priority)
        return (
            "No banks matched. Available banks: "
            + ", ".join(f"{p.id} ({p.config.description})" for p in all_banks)
        )

    @staticmethod
    def _result_to_dict(r: FederatedResult) -> dict[str, Any]:
        d: dict[str, Any] = {
            "bank": r.bank,
            "bank_label": r.bank_label,
            "source_type": r.source_type,
            "title": r.title,
            "snippet": r.snippet,
            "relevance": round(r.relevance, 3),
            "drill": r.drill,
        }
        if r.metadata:
            d["metadata"] = r.metadata
        return d
