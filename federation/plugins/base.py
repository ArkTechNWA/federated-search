"""Abstract base for bank plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod

from federation.types import BankConfig, BankStatus, FederatedResult


class BankPlugin(ABC):
    """Each bank type implements this interface.

    The federation layer doesn't know what a relation is, what a flex chunk is,
    or what a web result looks like. The plugin does. Federation just orchestrates,
    merges, and ranks.
    """

    def __init__(self, config: BankConfig) -> None:
        self.config = config
        self._status = BankStatus.HEALTHY

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def priority(self) -> int:
        return self.config.priority

    @property
    def status(self) -> BankStatus:
        return self._status

    @abstractmethod
    async def search(self, query: str, limit: int = 10, mode: str = "broad") -> list[FederatedResult]:
        """Translate a fed_search query into bank-native queries and return results."""

    @abstractmethod
    async def health_check(self) -> BankStatus:
        """Check if the backend is reachable and responding."""

    async def initialize(self) -> None:
        """Optional startup logic (session creation, auth, etc.)."""

    async def shutdown(self) -> None:
        """Optional cleanup."""
