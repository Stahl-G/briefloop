"""Tests for the Exa search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

from multi_agent_brief.sources.search_backends.exa import ExaBackend, DEFAULT_API_KEY_ENV


class TestExaBackend:
    """Unit tests for ExaBackend."""

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
