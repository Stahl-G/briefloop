"""Search backend abstract base class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class SearchBackendError(Exception):
    """Raised by search backends when a search fails (auth, rate-limit, network, etc.).
    
    Must never include API keys in the message or attributes.
    """
    def __init__(self, message: str, *, backend: str = "", status_code: int | None = None) -> None:
        super().__init__(message)
        self.backend = backend
        self.status_code = status_code


@dataclass
class SearchResult:
    """A single search result from a backend."""

    title: str
    url: str
    snippet: str
    published_at: str = ""
    source_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SearchBackend(ABC):
    """Abstract base class for web search backends."""

    name: str = "base"

    @abstractmethod
    def search(self, query: str, max_results: int = 10, *, domains: list[str] | None = None, **kwargs: Any) -> list[SearchResult]:
        """Execute a search query and return results.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.
            domains: Optional list of domains to restrict results to.
        """
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is configured and ready."""
        raise NotImplementedError
