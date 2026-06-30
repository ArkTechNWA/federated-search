"""Core types for federated search."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BankStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class BankConfig:
    """Configuration for a single memory bank."""
    id: str
    type: str  # kg, flex, searxng, calendar, ...
    label: str
    description: str
    priority: int
    default: bool
    url: str
    # Optional fields depending on bank type
    auth: str | None = None
    cell: str | None = None          # flex cell name
    synthesis: bool = False          # searxng AI synthesis
    deepseek_api_key_env: str | None = None
    min_relevance: float = 0.0      # results below this floor get cut
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BankConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in data.items() if k not in known}
        base = {k: v for k, v in data.items() if k in known}
        return cls(**base, extra=extra)


@dataclass
class FederatedResult:
    """One result from any bank — the universal envelope."""
    bank: str            # bank id
    bank_label: str      # human-readable bank label
    source_type: str     # "entity", "chunk", "web", "event", ...
    title: str           # what matched
    snippet: str         # why it matched — preview text
    relevance: float     # 0.0–1.0, bank-local relevance
    priority: int        # bank priority (from config)
    drill: str           # how to get more: "open_nodes(['X'])" or "@full id=Y"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BankInfo:
    """Bank metadata for fed_banks() discovery."""
    id: str
    type: str
    label: str
    description: str
    priority: int
    default: bool
    status: BankStatus = BankStatus.HEALTHY


@dataclass
class SearchRequest:
    """Parsed fed_search arguments."""
    query: str
    db: list[str] | None = None  # None = all defaults
    limit: int = 10
    mode: str = "broad"           # broad, exact, semantic
    domain: str | None = None    # pre-filter to a KG index alias
