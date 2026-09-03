"""Tests for the Firecrawl search backend."""
from __future__ import annotations

import json
from unittest.mock import patch

from multi_agent_brief.sources.search_backends.firecrawl import (
    FirecrawlBackend,
    DEFAULT_API_KEY_ENV,
)


class TestFirecrawlBackend:
    """Unit tests for FirecrawlBackend."""

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
