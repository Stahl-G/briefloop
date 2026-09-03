"""Tests for web search task metadata preservation through to SourceItem.metadata."""

from __future__ import annotations

from multi_agent_brief.sources.base import SourceQuery
from multi_agent_brief.sources.web_search import WebSearchProvider


class TestBuildQueriesMetadata:
    def test_preserves_task_metadata(self):
        provider = WebSearchProvider()
        config = {
            "enabled": True,
            "search_tasks": [
                {
                    "query": "shopee đánh giá việt nam",
                    "domains": None,
                    "topic": "consumer_signal",
                    "market": "vietnam",
                    "language": "vi",
                    "platform_group": "ecommerce",
                    "signal_type": "consumer_discussion",
                },
            ],
        }
        queries, task_meta = provider._build_queries(SourceQuery(), config)

        assert len(queries) == 1
        assert queries[0][0] == "shopee đánh giá việt nam"
        assert "shopee đánh giá việt nam" in task_meta
        meta = task_meta["shopee đánh giá việt nam"]
        assert meta["topic"] == "consumer_signal"
        assert meta["market"] == "vietnam"
        assert meta["language"] == "vi"
        assert meta["platform_group"] == "ecommerce"
        assert meta["signal_type"] == "consumer_discussion"
