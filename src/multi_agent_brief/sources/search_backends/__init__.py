"""Search backends for web search provider."""
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.tavily import TavilyBackend

__all__ = ["SearchBackend", "SearchResult", "TavilyBackend"]
