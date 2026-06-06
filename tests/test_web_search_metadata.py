"""Tests for web search task metadata preservation through to SourceItem.metadata."""
from __future__ import annotations

from unittest.mock import MagicMock

from multi_agent_brief.sources.base import SourceQuery
from multi_agent_brief.sources.web_search import WebSearchProvider


def _mock_search_result(url="https://example.com/1", title="Test", snippet="content"):
    result = MagicMock()
    result.url = url
    result.title = title
    result.snippet = snippet
    result.published_at = "2026-06-01"
    result.source_name = "example"
    result.metadata = {
        "date_status": "published_at_present",
        "source_temporality": "published",
    }
    return result


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

    def test_no_metadata_for_plain_queries(self):
        provider = WebSearchProvider()
        config = {
            "enabled": True,
            "search_tasks": [
                {"query": "manufacturing trends", "domains": None},
            ],
        }
        queries, task_meta = provider._build_queries(SourceQuery(), config)

        assert len(queries) == 1
        assert "manufacturing trends" not in task_meta

    def test_no_metadata_for_keyword_queries(self):
        provider = WebSearchProvider()
        config = {"enabled": True}
        query = SourceQuery(keywords=["solar energy"])

        queries, task_meta = provider._build_queries(query, config)

        assert len(queries) == 1
        assert task_meta == {}

    def test_mixed_tasks_with_and_without_metadata(self):
        provider = WebSearchProvider()
        config = {
            "enabled": True,
            "search_tasks": [
                {"query": "standard query", "domains": None},
                {
                    "query": "local signal query",
                    "domains": None,
                    "market": "japan",
                    "language": "ja",
                },
            ],
        }
        queries, task_meta = provider._build_queries(SourceQuery(), config)

        assert len(queries) == 2
        assert "standard query" not in task_meta
        assert "local signal query" in task_meta
        assert task_meta["local signal query"]["market"] == "japan"


class TestResultToSourceItemMetadata:
    def test_task_metadata_propagated_with_prefix(self):
        provider = WebSearchProvider()
        result = _mock_search_result()
        task_metadata = {
            "topic": "consumer_signal",
            "market": "vietnam",
            "language": "vi",
            "platform_group": "ecommerce",
            "signal_type": "consumer_discussion",
        }

        item = provider._result_to_source_item(
            result, "test query", "tavily", task_metadata=task_metadata
        )

        assert item.metadata["task_topic"] == "consumer_signal"
        assert item.metadata["task_market"] == "vietnam"
        assert item.metadata["task_language"] == "vi"
        assert item.metadata["task_platform_group"] == "ecommerce"
        assert item.metadata["task_signal_type"] == "consumer_discussion"

    def test_no_task_metadata_when_none(self):
        provider = WebSearchProvider()
        result = _mock_search_result()

        item = provider._result_to_source_item(result, "test query", "tavily")

        assert "task_topic" not in item.metadata
        assert "task_market" not in item.metadata
        assert item.metadata["query"] == "test query"
        assert item.metadata["backend"] == "tavily"

    def test_task_metadata_does_not_override_result_metadata(self):
        provider = WebSearchProvider()
        result = _mock_search_result()
        result.metadata["backend"] = "tavily_original"

        item = provider._result_to_source_item(
            result, "test query", "tavily", task_metadata={"market": "vietnam"}
        )

        # task metadata uses "task_" prefix, so no collision
        assert item.metadata["task_market"] == "vietnam"
        assert "market" not in item.metadata or item.metadata.get("market") != "vietnam"
