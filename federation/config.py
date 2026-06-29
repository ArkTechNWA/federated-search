"""Config loading from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from federation.types import BankConfig


@dataclass
class AgentConfig:
    """Configuration for one agent endpoint."""
    name: str
    port: int
    banks: list[BankConfig]

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> AgentConfig:
        banks = [BankConfig.from_dict(b) for b in data.get("banks", [])]
        return cls(name=name, port=data["port"], banks=banks)

    @property
    def default_banks(self) -> list[BankConfig]:
        return [b for b in self.banks if b.default]

    def get_bank(self, bank_id: str) -> BankConfig | None:
        return next((b for b in self.banks if b.id == bank_id), None)


@dataclass
class FederationConfig:
    """Top-level federation configuration."""
    agents: dict[str, AgentConfig] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> FederationConfig:
        path = Path(path)
        with open(path) as f:
            raw = yaml.safe_load(f)

        agents = {}
        for name, agent_data in raw.get("agents", {}).items():
            agents[name] = AgentConfig.from_dict(name, agent_data)

        return cls(agents=agents)
