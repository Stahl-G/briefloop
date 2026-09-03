"""Tests for the Serper search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

from multi_agent_brief.sources.search_backends.serper import SerperBackend, DEFAULT_API_KEY_ENV


class TestSerperBackend:
    """Unit tests for SerperBackend."""

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
