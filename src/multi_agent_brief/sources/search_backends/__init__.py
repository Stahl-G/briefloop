"""Search backends for web search provider."""
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.capabilities import (
    EXA_CAPABILITIES,
    SearchBackendCapabilities,
    TAVILY_CAPABILITIES,
)
from multi_agent_brief.sources.search_backends.exa import ExaBackend
from multi_agent_brief.sources.search_backends.tavily import TavilyBackend

__all__ = [
    "ExaBackend",
    "EXA_CAPABILITIES",
    "SearchBackend",
    "SearchBackendCapabilities",
    "SearchResult",
    "TavilyBackend",
    "TAVILY_CAPABILITIES",
]
