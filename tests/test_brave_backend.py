"""Tests for the Brave Search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

from multi_agent_brief.sources.search_backends.brave import BraveBackend, DEFAULT_API_KEY_ENV


class TestBraveBackend:
    """Unit tests for BraveBackend."""

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
