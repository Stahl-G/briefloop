"""Tests for the Firecrawl search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from multi_agent_brief.sources.search_backends.firecrawl import (
    FirecrawlBackend,
    DEFAULT_API_KEY_ENV,
    MAX_CONTENT_LENGTH,
)


class TestFirecrawlBackend:
    """Unit tests for FirecrawlBackend."""

    def test_is_available_without_key(self, monkeypatch):
        """Should be unavailable without FIRECRAWL_API_KEY."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = FirecrawlBackend()
        assert backend.is_available() is False

    def test_is_available_with_key(self, monkeypatch):
        """Should be available with FIRECRAWL_API_KEY set."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        backend = FirecrawlBackend()
        assert backend.is_available() is True

    def test_is_available_with_custom_env(self, monkeypatch):
        """Should support custom api_key_env."""
        monkeypatch.setenv("MY_FIRECRAWL_KEY", "test-key")
        backend = FirecrawlBackend(api_key_env="MY_FIRECRAWL_KEY")
        assert backend.is_available() is True

    def test_capabilities(self):
        """Should return FIRECRAWL_CAPABILITIES."""
        caps = FirecrawlBackend.capabilities()
        assert caps.name == "firecrawl"
        assert caps.kind == "search_plus_extract"
        assert caps.supports_raw_content is True
        assert caps.evidence_quality == "full_text"

    def test_search_returns_empty_without_key(self, monkeypatch):
        """Should return empty list without API key."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = FirecrawlBackend()
        results = backend.search("test query")
        assert results == []

    def test_search_maps_web_result(self, monkeypatch):
        """Should map a Firecrawl web result to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "EV Battery Analysis",
                        "url": "https://example.com/ev-battery",
                        "description": "Comprehensive analysis of EV battery supply chain.",
                    }
                ]
            },
            "creditsUsed": 1,
        }

        def mock_urlopen(req, timeout=60):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            results = backend.search("EV battery", max_results=5)

        assert len(results) == 1
        assert results[0].title == "EV Battery Analysis"
        assert results[0].url == "https://example.com/ev-battery"
        assert results[0].snippet == "Comprehensive analysis of EV battery supply chain."
        assert results[0].metadata["backend"] == "firecrawl"
        assert results[0].metadata["vertical"] == "web"
        assert results[0].metadata["evidence_quality"] == "snippet"
        assert results[0].metadata["has_markdown"] is False

    def test_search_maps_markdown_result(self, monkeypatch):
        """Should prefer markdown over description when available."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Full Article",
                        "url": "https://example.com/full-article",
                        "description": "Short description.",
                        "markdown": "# Full Article\n\nThis is the full page content with detailed analysis.",
                    }
                ]
            },
            "creditsUsed": 2,
        }

        def mock_urlopen(req, timeout=60):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].snippet == "# Full Article\n\nThis is the full page content with detailed analysis."
        assert results[0].metadata["evidence_quality"] == "full_text"
        assert results[0].metadata["has_markdown"] is True

    def test_search_truncates_long_markdown(self, monkeypatch):
        """Should truncate long markdown content."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        long_markdown = "x" * 5000
        mock_response = {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Long Article",
                        "url": "https://example.com/long",
                        "description": "Short.",
                        "markdown": long_markdown,
                    }
                ]
            },
            "creditsUsed": 2,
        }

        def mock_urlopen(req, timeout=60):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert len(results[0].snippet) <= MAX_CONTENT_LENGTH + 3  # +3 for "..."
        assert results[0].snippet.endswith("...")

    def test_search_maps_missing_date(self, monkeypatch):
        """Should always report missing_published_at (Firecrawl doesn't provide dates)."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "No Date",
                        "url": "https://example.com/no-date",
                        "description": "Content.",
                    }
                ]
            },
            "creditsUsed": 1,
        }

        def mock_urlopen(req, timeout=60):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].published_at == ""
        assert results[0].metadata["date_status"] == "missing_published_at"
        assert results[0].metadata["source_temporality"] == "retrieved_only"

    def test_search_passes_domains(self, monkeypatch):
        """Should pass domains as includeDomains."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_payload = {}

        def mock_urlopen(req, timeout=60):
            import io
            captured_payload.update(json.loads(req.data.decode()))
            resp = io.BytesIO(json.dumps({"success": True, "data": {"web": []}}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"success": True, "data": {"web": []}}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            backend.search("test", domains=["reuters.com", "bloomberg.com"])

        assert captured_payload.get("includeDomains") == ["reuters.com", "bloomberg.com"]

    def test_search_enables_scrape_markdown(self, monkeypatch):
        """Should include scrapeOptions when scrape_markdown=True."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_payload = {}

        def mock_urlopen(req, timeout=60):
            import io
            captured_payload.update(json.loads(req.data.decode()))
            resp = io.BytesIO(json.dumps({"success": True, "data": {"web": []}}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"success": True, "data": {"web": []}}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            backend.search("test", scrape_markdown=True)

        assert "scrapeOptions" in captured_payload
        assert captured_payload["scrapeOptions"]["formats"] == [{"type": "markdown"}]

    def test_search_handles_api_error(self, monkeypatch):
        """Should raise SearchBackendError on API error."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        def mock_urlopen(req, timeout=60):
            raise Exception("API error")

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            from multi_agent_brief.sources.search_backends.base import SearchBackendError
            with pytest.raises(SearchBackendError, match="Firecrawl search failed"):
                backend.search("test query")

    def test_search_handles_failure_response(self, monkeypatch):
        """Should raise SearchBackendError when success=false."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {"success": False, "error": "Rate limit exceeded"}

        def mock_urlopen(req, timeout=60):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            from multi_agent_brief.sources.search_backends.base import SearchBackendError
            with pytest.raises(SearchBackendError, match="Rate limit exceeded"):
                backend.search("test query")

    def test_search_uses_bearer_auth(self, monkeypatch):
        """Should use Bearer token authentication."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "my-secret-key")
        captured_headers = {}

        def mock_urlopen(req, timeout=60):
            import io
            captured_headers.update(req.headers)
            resp = io.BytesIO(json.dumps({"success": True, "data": {"web": []}}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"success": True, "data": {"web": []}}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = FirecrawlBackend()
            backend.search("test")

        assert captured_headers.get("Authorization") == "Bearer my-secret-key"

    def test_provider_registry(self, monkeypatch):
        """WebSearchProvider should be able to instantiate FirecrawlBackend."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        from multi_agent_brief.sources.web_search import _register_known_backends, _KNOWN_BACKENDS
        _KNOWN_BACKENDS.clear()
        _register_known_backends()
        assert "firecrawl" in _KNOWN_BACKENDS
        assert _KNOWN_BACKENDS["firecrawl"] is FirecrawlBackend
