"""Tests for the Brave Search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from multi_agent_brief.sources.search_backends.brave import BraveBackend, DEFAULT_API_KEY_ENV


class TestBraveBackend:
    """Unit tests for BraveBackend."""

    def test_is_available_without_key(self, monkeypatch):
        """Should be unavailable without BRAVE_SEARCH_API_KEY."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = BraveBackend()
        assert backend.is_available() is False

    def test_is_available_with_key(self, monkeypatch):
        """Should be available with BRAVE_SEARCH_API_KEY set."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        backend = BraveBackend()
        assert backend.is_available() is True

    def test_is_available_with_custom_env(self, monkeypatch):
        """Should support custom api_key_env."""
        monkeypatch.setenv("MY_BRAVE_KEY", "test-key")
        backend = BraveBackend(api_key_env="MY_BRAVE_KEY")
        assert backend.is_available() is True

    def test_capabilities(self):
        """Should return BRAVE_CAPABILITIES."""
        caps = BraveBackend.capabilities()
        assert caps.name == "brave"
        assert caps.kind == "serp"
        assert caps.supports_news is True
        assert caps.supports_domains is True
        assert caps.published_at_quality == "partial"
        assert caps.evidence_quality == "snippet"

    def test_search_returns_empty_without_key(self, monkeypatch):
        """Should return empty list without API key."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = BraveBackend()
        results = backend.search("test query")
        assert results == []

    def test_search_maps_web_result(self, monkeypatch):
        """Should map a Brave web result to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "web": {
                "results": [
                    {
                        "title": "EV Battery Supply Chain 2026",
                        "url": "https://example.com/ev-battery",
                        "description": "Global EV battery supply chain faces <strong>new challenges</strong> in 2026.",
                        "age": "2 days ago",
                        "profile": {
                            "name": "Example News",
                            "long_name": "example.com",
                        },
                    }
                ]
            }
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            resp.headers = {}
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = BraveBackend()
            results = backend.search("EV battery supply chain", max_results=5)

        assert len(results) == 1
        assert results[0].title == "EV Battery Supply Chain 2026"
        assert results[0].url == "https://example.com/ev-battery"
        assert "new challenges" in results[0].snippet
        assert "<strong>" not in results[0].snippet  # HTML stripped
        assert results[0].published_at == "2 days ago"
        assert results[0].source_name == "example.com"
        assert results[0].metadata["backend"] == "brave"
        assert results[0].metadata["vertical"] == "web"
        assert results[0].metadata["date_status"] == "published_at_present"

    def test_search_maps_missing_date(self, monkeypatch):
        """Should handle missing age field."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "web": {
                "results": [
                    {
                        "title": "No Date Article",
                        "url": "https://example.com/no-date",
                        "description": "Content without date.",
                    }
                ]
            }
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            resp.headers = {}
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = BraveBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].published_at == ""
        assert results[0].metadata["date_status"] == "missing_published_at"
        assert results[0].metadata["source_temporality"] == "retrieved_only"

    def test_search_maps_news_result(self, monkeypatch):
        """Should map a Brave news result to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "Breaking: EV Market Update",
                    "url": "https://news.example.com/ev-update",
                    "description": "Latest developments in EV market.",
                    "age": "1 hour ago",
                    "page_age": "2026-06-03T10:00:00Z",
                    "meta_url": {
                        "hostname": "news.example.com",
                    },
                }
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            resp.headers = {}
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = BraveBackend()
            results = backend.search("EV market", max_results=5, vertical="news")

        assert len(results) == 1
        assert results[0].title == "Breaking: EV Market Update"
        assert results[0].published_at == "2026-06-03T10:00:00Z"  # prefers page_age
        assert results[0].metadata["vertical"] == "news"
        assert results[0].metadata["date_status"] == "published_at_present"

    def test_search_passes_domains_as_filter(self, monkeypatch):
        """Should note that Brave doesn't support include_domains directly."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_url = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_url["url"] = req.full_url
            resp = io.BytesIO(json.dumps({"web": {"results": []}}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"web": {"results": []}}).encode()
            resp.headers = {}
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = BraveBackend()
            backend.search("test", domains=["reuters.com"])

        # Brave uses GET with query params, domains are not directly supported
        # but should not cause an error
        assert "q=test" in captured_url["url"]

    def test_search_passes_freshness(self, monkeypatch):
        """Should pass freshness parameter."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_url = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_url["url"] = req.full_url
            resp = io.BytesIO(json.dumps({"web": {"results": []}}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"web": {"results": []}}).encode()
            resp.headers = {}
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = BraveBackend()
            backend.search("test", freshness="pd")  # pd = past day

        assert "freshness=pd" in captured_url["url"]

    def test_search_handles_api_error(self, monkeypatch):
        """Should raise SearchBackendError on API error."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        def mock_urlopen(req, timeout=30):
            raise Exception("API error")

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = BraveBackend()
            from multi_agent_brief.sources.search_backends.base import SearchBackendError
            with pytest.raises(SearchBackendError, match="Brave search failed"):
                backend.search("test query")

    def test_search_strips_html_from_description(self, monkeypatch):
        """Should strip HTML tags from description."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "web": {
                "results": [
                    {
                        "title": "Test",
                        "url": "https://example.com/test",
                        "description": "This is <strong>important</strong> and <em>urgent</em> news.",
                    }
                ]
            }
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            resp.headers = {}
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = BraveBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].snippet == "This is important and urgent news."

    def test_provider_registry(self, monkeypatch):
        """WebSearchProvider should be able to instantiate BraveBackend."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        from multi_agent_brief.sources.web_search import _register_known_backends, _KNOWN_BACKENDS
        _KNOWN_BACKENDS.clear()
        _register_known_backends()
        assert "brave" in _KNOWN_BACKENDS
        assert _KNOWN_BACKENDS["brave"] is BraveBackend
