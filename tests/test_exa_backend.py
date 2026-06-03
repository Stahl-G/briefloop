"""Tests for the Exa search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from multi_agent_brief.sources.search_backends.exa import ExaBackend, DEFAULT_API_KEY_ENV


class TestExaBackend:
    """Unit tests for ExaBackend."""

    def test_is_available_without_key(self, monkeypatch):
        """Should be unavailable without EXA_API_KEY."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = ExaBackend()
        assert backend.is_available() is False

    def test_is_available_with_key(self, monkeypatch):
        """Should be available with EXA_API_KEY set."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        backend = ExaBackend()
        assert backend.is_available() is True

    def test_is_available_with_custom_env(self, monkeypatch):
        """Should support custom api_key_env."""
        monkeypatch.setenv("MY_EXA_KEY", "test-key")
        backend = ExaBackend(api_key_env="MY_EXA_KEY")
        assert backend.is_available() is True

    def test_capabilities(self):
        """Should return EXA_CAPABILITIES."""
        caps = ExaBackend.capabilities()
        assert caps.name == "exa"
        assert caps.kind == "ai_search"
        assert caps.supports_highlights is True
        assert caps.supports_research_papers is True
        assert caps.published_at_quality == "good"
        assert caps.evidence_quality == "highlight"

    def test_search_returns_empty_without_key(self, monkeypatch):
        """Should return empty list without API key."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = ExaBackend()
        results = backend.search("test query")
        assert results == []

    def test_search_maps_published_date(self, monkeypatch):
        """Should map publishedDate to published_at."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "Test Article",
                    "url": "https://example.com/article",
                    "publishedDate": "2026-06-01T00:00:00Z",
                    "summary": "Test summary content.",
                }
            ],
            "costDollars": {"total": 0.01},
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query", max_results=5)

        assert len(results) == 1
        assert results[0].title == "Test Article"
        assert results[0].url == "https://example.com/article"
        assert results[0].published_at == "2026-06-01T00:00:00Z"
        assert results[0].metadata["date_status"] == "published_at_present"
        assert results[0].metadata["source_temporality"] == "published"

    def test_search_maps_missing_date(self, monkeypatch):
        """Should set missing_published_at when publishedDate is empty."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "No Date Article",
                    "url": "https://example.com/no-date",
                    "summary": "Content without date.",
                }
            ],
            "costDollars": {"total": 0.01},
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].published_at == ""
        assert results[0].metadata["date_status"] == "missing_published_at"
        assert results[0].metadata["source_temporality"] == "retrieved_only"

    def test_search_prefers_summary_over_highlights(self, monkeypatch):
        """Should prefer summary over highlights for snippet."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "Article with both",
                    "url": "https://example.com/both",
                    "summary": "This is the summary.",
                    "highlights": ["Highlight 1", "Highlight 2"],
                }
            ],
            "costDollars": {"total": 0.01},
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].snippet == "This is the summary."
        assert results[0].metadata["evidence_quality"] == "highlight"

    def test_search_falls_back_to_highlights(self, monkeypatch):
        """Should use highlights when summary is empty."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "Highlights only",
                    "url": "https://example.com/highlights",
                    "highlights": ["First highlight.", "Second highlight."],
                }
            ],
            "costDollars": {"total": 0.01},
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].snippet == "First highlight. ... Second highlight."

    def test_search_falls_back_to_text(self, monkeypatch):
        """Should use text when summary and highlights are empty."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "Text only",
                    "url": "https://example.com/text",
                    "text": "Full page text content here.",
                }
            ],
            "costDollars": {"total": 0.01},
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].snippet == "Full page text content here."
        assert results[0].metadata["evidence_quality"] == "full_text"

    def test_search_passes_domains(self, monkeypatch):
        """Should pass domains as includeDomains."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_payload = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_payload.update(json.loads(req.data.decode()))
            resp = io.BytesIO(json.dumps({"results": [], "costDollars": {}}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"results": [], "costDollars": {}}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            backend.search("test", domains=["reuters.com", "bloomberg.com"])

        assert captured_payload.get("includeDomains") == ["reuters.com", "bloomberg.com"]

    def test_search_passes_days_as_date(self, monkeypatch):
        """Should convert days to startPublishedDate."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_payload = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_payload.update(json.loads(req.data.decode()))
            resp = io.BytesIO(json.dumps({"results": [], "costDollars": {}}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"results": [], "costDollars": {}}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            backend.search("test", days=7)

        assert "startPublishedDate" in captured_payload

    def test_search_maps_author(self, monkeypatch):
        """Should map author from result."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "Authored Article",
                    "url": "https://example.com/authored",
                    "author": "John Doe",
                    "summary": "Content.",
                }
            ],
            "costDollars": {"total": 0.01},
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].metadata["author"] == "John Doe"

    def test_search_maps_cost(self, monkeypatch):
        """Should map costDollars from response."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "results": [
                {
                    "title": "Test",
                    "url": "https://example.com/test",
                    "summary": "Content.",
                }
            ],
            "costDollars": {"total": 0.05},
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].metadata["cost_dollars"] == 0.05

    def test_search_handles_api_error(self, monkeypatch):
        """Should return empty list on API error."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        def mock_urlopen(req, timeout=30):
            raise Exception("API error")

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = ExaBackend()
            results = backend.search("test query")

        assert results == []

    def test_provider_registry(self, monkeypatch):
        """WebSearchProvider should be able to instantiate ExaBackend."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        from multi_agent_brief.sources.web_search import _register_known_backends, _KNOWN_BACKENDS
        _KNOWN_BACKENDS.clear()
        _register_known_backends()
        assert "exa" in _KNOWN_BACKENDS
        assert _KNOWN_BACKENDS["exa"] is ExaBackend
