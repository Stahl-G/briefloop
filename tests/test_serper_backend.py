"""Tests for the Serper search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from multi_agent_brief.sources.search_backends.serper import SerperBackend, DEFAULT_API_KEY_ENV


class TestSerperBackend:
    """Unit tests for SerperBackend."""

    def test_is_available_without_key(self, monkeypatch):
        """Should be unavailable without SERPER_API_KEY."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = SerperBackend()
        assert backend.is_available() is False

    def test_is_available_with_key(self, monkeypatch):
        """Should be available with SERPER_API_KEY set."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        backend = SerperBackend()
        assert backend.is_available() is True

    def test_is_available_with_custom_env(self, monkeypatch):
        """Should support custom api_key_env."""
        monkeypatch.setenv("MY_SERPER_KEY", "test-key")
        backend = SerperBackend(api_key_env="MY_SERPER_KEY")
        assert backend.is_available() is True

    def test_capabilities(self):
        """Should return SERPER_CAPABILITIES."""
        caps = SerperBackend.capabilities()
        assert caps.name == "serper"
        assert caps.kind == "serp"
        assert caps.supports_news is True
        assert caps.supports_verticals is True
        assert caps.supports_research_papers is True
        assert caps.supports_patents is True

    def test_search_returns_empty_without_key(self, monkeypatch):
        """Should return empty list without API key."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = SerperBackend()
        results = backend.search("test query")
        assert results == []

    def test_search_maps_organic_result(self, monkeypatch):
        """Should map a Serper organic result to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "organic": [
                {
                    "title": "EV Battery Supply Chain 2026",
                    "link": "https://example.com/ev-battery",
                    "snippet": "Global EV battery supply chain faces new challenges.",
                    "position": 1,
                    "date": "Jun 1, 2026",
                }
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            results = backend.search("EV battery supply chain", max_results=5)

        assert len(results) == 1
        assert results[0].title == "EV Battery Supply Chain 2026"
        assert results[0].url == "https://example.com/ev-battery"
        assert results[0].snippet == "Global EV battery supply chain faces new challenges."
        assert results[0].published_at == "Jun 1, 2026"
        assert results[0].metadata["backend"] == "serper"
        assert results[0].metadata["vertical"] == "search"
        assert results[0].metadata["position"] == 1
        assert results[0].metadata["date_status"] == "published_at_present"

    def test_search_maps_missing_date(self, monkeypatch):
        """Should handle missing date field."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "organic": [
                {
                    "title": "No Date Article",
                    "link": "https://example.com/no-date",
                    "snippet": "Content without date.",
                    "position": 1,
                }
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            results = backend.search("test query")

        assert len(results) == 1
        assert results[0].published_at == ""
        assert results[0].metadata["date_status"] == "missing_published_at"
        assert results[0].metadata["source_temporality"] == "retrieved_only"

    def test_search_maps_news_result(self, monkeypatch):
        """Should map a Serper news result to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "news": [
                {
                    "title": "Breaking: EV Market Update",
                    "link": "https://news.example.com/ev-update",
                    "snippet": "Latest developments in EV market.",
                    "date": "2 hours ago",
                    "source": "Example News",
                    "position": 1,
                }
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            results = backend.search("EV market", max_results=5, vertical="news")

        assert len(results) == 1
        assert results[0].title == "Breaking: EV Market Update"
        assert results[0].published_at == "2 hours ago"
        assert results[0].source_name == "Example News"
        assert results[0].metadata["vertical"] == "news"

    def test_search_maps_scholar_result(self, monkeypatch):
        """Should map a Serper scholar result to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "organic": [
                {
                    "title": "Deep Learning for EV Battery Prediction",
                    "link": "https://scholar.example.com/paper",
                    "snippet": "This paper presents a deep learning approach.",
                    "publicationInfo": "Journal of AI Research, 2026",
                    "year": "2026",
                    "citedBy": 42,
                    "position": 1,
                }
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            results = backend.search("EV battery prediction", max_results=5, vertical="scholar")

        assert len(results) == 1
        assert results[0].title == "Deep Learning for EV Battery Prediction"
        assert results[0].published_at == "2026"
        assert results[0].metadata["vertical"] == "scholar"
        assert results[0].metadata["cited_by"] == 42

    def test_search_maps_patent_result(self, monkeypatch):
        """Should map a Serper patent result to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        mock_response = {
            "organic": [
                {
                    "title": "US Patent: EV Battery Cooling System",
                    "link": "https://patents.example.com/patent",
                    "snippet": "A novel cooling system for EV batteries.",
                    "publicationDate": "2026-03-15",
                    "filingDate": "2025-01-10",
                    "inventor": "John Doe",
                    "assignee": "EV Corp",
                    "publicationNumber": "US20260123456",
                    "position": 1,
                }
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            resp = io.BytesIO(json.dumps(mock_response).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps(mock_response).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            results = backend.search("EV battery patent", max_results=5, vertical="patents")

        assert len(results) == 1
        assert results[0].title == "US Patent: EV Battery Cooling System"
        assert results[0].published_at == "2026-03-15"
        assert results[0].metadata["vertical"] == "patents"
        assert results[0].metadata["inventor"] == "John Doe"
        assert results[0].metadata["assignee"] == "EV Corp"
        assert results[0].metadata["publication_number"] == "US20260123456"

    def test_search_passes_country_and_language(self, monkeypatch):
        """Should pass gl and hl parameters."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_payload = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_payload.update(json.loads(req.data.decode()))
            resp = io.BytesIO(json.dumps({"organic": []}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"organic": []}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            backend.search("test", gl="cn", hl="zh-cn")

        assert captured_payload.get("gl") == "cn"
        assert captured_payload.get("hl") == "zh-cn"

    def test_search_passes_tbs(self, monkeypatch):
        """Should pass tbs parameter for time-based search."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        captured_payload = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_payload.update(json.loads(req.data.decode()))
            resp = io.BytesIO(json.dumps({"organic": []}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"organic": []}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            backend.search("test", tbs="qdr:d")  # past day

        assert captured_payload.get("tbs") == "qdr:d"

    def test_search_handles_api_error(self, monkeypatch):
        """Should raise SearchBackendError on API error."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")

        def mock_urlopen(req, timeout=30):
            raise Exception("API error")

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            from multi_agent_brief.sources.search_backends.base import SearchBackendError
            with pytest.raises(SearchBackendError, match="Serper search failed"):
                backend.search("test query")

    def test_search_uses_x_api_key_header(self, monkeypatch):
        """Should use X-API-KEY header for authentication."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "my-secret-key")
        captured_headers = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_headers.update(req.headers)
            resp = io.BytesIO(json.dumps({"organic": []}).encode())
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            resp.read = lambda: json.dumps({"organic": []}).encode()
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = SerperBackend()
            backend.search("test")

        # Check that the API key header is present (case-insensitive)
        api_key = None
        for key, value in captured_headers.items():
            if key.lower() == "x-api-key":
                api_key = value
                break
        assert api_key == "my-secret-key"

    def test_provider_registry(self, monkeypatch):
        """WebSearchProvider should be able to instantiate SerperBackend."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "test-key")
        from multi_agent_brief.sources.web_search import _register_known_backends, _KNOWN_BACKENDS
        _KNOWN_BACKENDS.clear()
        _register_known_backends()
        assert "serper" in _KNOWN_BACKENDS
        assert _KNOWN_BACKENDS["serper"] is SerperBackend
