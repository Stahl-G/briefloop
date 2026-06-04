"""Search backends for web search provider."""
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.brave import BraveBackend
from multi_agent_brief.sources.search_backends.capabilities import (
    BRAVE_CAPABILITIES,
    EXA_CAPABILITIES,
    FIRECRAWL_CAPABILITIES,
    SERPER_CAPABILITIES,
    SearchBackendCapabilities,
    TAVILY_CAPABILITIES,
)
from multi_agent_brief.sources.search_backends.exa import ExaBackend
from multi_agent_brief.sources.search_backends.firecrawl import FirecrawlBackend
from multi_agent_brief.sources.search_backends.serper import SerperBackend
from multi_agent_brief.sources.search_backends.tavily import TavilyBackend

__all__ = [
    "BraveBackend",
    "BRAVE_CAPABILITIES",
    "ExaBackend",
    "EXA_CAPABILITIES",
    "FirecrawlBackend",
    "FIRECRAWL_CAPABILITIES",
    "SearchBackend",
    "SearchBackendCapabilities",
    "SearchResult",
    "SerperBackend",
    "SERPER_CAPABILITIES",
    "TavilyBackend",
    "TAVILY_CAPABILITIES",
]
