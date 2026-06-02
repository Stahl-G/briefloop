"""Search backends for web search provider."""
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.mock import MockSearchBackend

__all__ = ["SearchBackend", "SearchResult", "MockSearchBackend"]
