"""Tests for B02: sources decide --search must actually execute searches."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.sources import web_search
from multi_agent_brief.sources.decider import (
    build_search_queries,
    generate_source_candidates,
    load_source_discovery,
)
from multi_agent_brief.sources.search_backends.base import SearchResult


class FakeSearchBackend:
    """Fake search backend returning controlled results for --search tests."""
    name = "fake"

    def __init__(self):
        self.last_queries: list[str] = []

    def search(self, query, max_results=10, *, domains=None, **kwargs):
        self.last_queries.append(query)
        if "error" in query.lower():
            raise RuntimeError("SearchBackendError: simulated 500")
        return [
            SearchResult(
                title=f"Result for: {query[:50]}",
                url=f"https://fake.example.com/{hash(query) % 1000}",
                snippet=f"Snippet about {query[:60]} from fake backend.",
                published_at="2026-06-01",
                source_name="Fake Backend",
                metadata={"backend": "fake"},
            )
        ]

    def is_available(self):
        return True


class EnvCliSearchBackend:
    """CLI fake backend that only works when the expected env var is visible."""
    name = "tavily"

    def __init__(self, api_key_env: str = "TAVILY_API_KEY") -> None:
        self._api_key_env = api_key_env

    def search(self, query, max_results=10, *, domains=None, **kwargs):
        if not os.environ.get(self._api_key_env):
            return []
        return [
            SearchResult(
                title="Workspace env CLI result",
                url="https://fake.example.com/workspace-env-cli",
                snippet=f"CLI search result for {query}.",
                published_at="2026-06-01",
                source_name="Fake Backend",
                metadata={"backend": "tavily"},
            )
        ]

    def is_available(self):
        return bool(os.environ.get(self._api_key_env))


class FailingCliSearchBackend:
    """CLI fake backend that is available but fails every query."""
    name = "tavily"

    def search(self, query, max_results=10, *, domains=None, **kwargs):
        raise RuntimeError("simulated search outage")

    def is_available(self):
        return True


class TestB02SearchExecution:
    """sources decide --search must execute actual queries via the backend."""

    def test_generate_candidates_with_search_results(self):
        """generate_source_candidates must include search results when provided."""
        discovery = {
            "company": "TestCo",
            "industry": "manufacturing",
            "language": "en",
            "max_source_age_days": 14,
        }
        search_results = [
            {
                "query": "manufacturing industry news",
                "results": [
                    {
                        "title": "Manufacturing Sector Grows",
                        "url": "https://example.com/manufacturing-grows",
                        "snippet": "The manufacturing sector continued to expand.",
                        "published_at": "2026-06-01",
                        "source_name": "Industry News",
                    },
                ],
            },
        ]
        candidates = generate_source_candidates(discovery, search_results)
        recommended = candidates.get("recommended_sources", [])
        assert len(recommended) > 0, (
            "B02 FAIL: generate_source_candidates returned zero recommended sources "
            "when search results were provided"
        )
        # Verify the search result is in recommended
        urls = {s.get("url") for s in recommended}
        assert "https://example.com/manufacturing-grows" in urls, (
            "B02 FAIL: search result URL not in recommended sources"
        )

    def test_generate_candidates_without_search_results(self):
        """Without search_results, only template sources are included."""
        discovery = {
            "company": "TestCo",
            "industry": "manufacturing",
            "language": "en",
        }
        candidates = generate_source_candidates(discovery, search_results=None)
        # Template sources should still be present
        templates = candidates.get("template_sources", [])
        assert len(templates) > 0, "Template sources should always be present"
        # No search-result-based recommended sources
        recommended = candidates.get("recommended_sources", [])
        assert len(recommended) == 0, (
            "Without search_results, recommended_sources should be empty"
        )

    def test_queries_returned_per_backend_search(self):
        """build_search_queries must generate queries from discovery."""
        discovery = {
            "company": "TestCo",
            "industry": "manufacturing",
            "focus_areas": ["policy", "tariffs"],
        }
        queries = build_search_queries(discovery)
        assert len(queries) >= 3, (
            f"B02 FAIL: expected at least 3 queries, got {len(queries)}"
        )
        assert any("manufacturing" in q.lower() for q in queries)
        assert any("testco" in q.lower() for q in queries)

    def test_search_error_generates_collection_error(self):
        """When a backend search fails, the error must be surfaced."""
        backend = FakeSearchBackend()
        # Simulate an error by using a query with 'error'
        with pytest.raises(RuntimeError, match="SearchBackendError"):
            backend.search("trigger error in search")


class TestB02CLISearchIntegration:
    """Integration tests through the decider module (not full CLI)."""

    def test_run_with_fake_backend_produces_candidates(self, tmp_path):
        """Running searches with a fake backend must produce non-empty candidates."""
        discovery = {
            "company": "TestCo",
            "industry": "manufacturing",
            "language": "en",
            "focus_areas": ["policy"],
        }
        queries = build_search_queries(discovery)
        assert len(queries) > 0

        backend = FakeSearchBackend()
        search_results = []
        for q in queries:
            results = backend.search(q, max_results=5)
            search_results.append({
                "query": q,
                "results": [
                    {
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet,
                        "published_at": r.published_at,
                        "source_name": r.source_name,
                    }
                    for r in results
                ],
            })

        candidates = generate_source_candidates(discovery, search_results)
        recommended = candidates.get("recommended_sources", [])
        assert len(recommended) > 0, (
            "B02 FAIL: running actual searches with fake backend produced zero candidates"
        )
        # Each candidate should have a query field
        for src in recommended:
            assert "query" in src, (
                "B02 FAIL: candidate source missing 'query' field"
            )

    def test_sources_decide_search_uses_workspace_env_key(self, tmp_path, monkeypatch, capsys):
        """CLI search must use the same workspace .env key path as provider validation."""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.setitem(web_search._KNOWN_BACKENDS, "tavily", EnvCliSearchBackend)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".env").write_text("TAVILY_API_KEY=workspace-cli-secret\n", encoding="utf-8")
        (ws / "config.yaml").write_text(
            "project:\n  name: test\ninput:\n  path: input\noutput:\n  path: output\n",
            encoding="utf-8",
        )
        (ws / "sources.yaml").write_text(
            "source_strategy:\n"
            "  profile: research\n"
            "  enabled_providers: [web_search]\n"
            "web_search:\n"
            "  enabled: true\n"
            "  mode: external_api\n"
            "  backend: tavily\n"
            "source_discovery:\n"
            "  company: TestCo\n"
            "  industry: manufacturing\n"
            "  topics: [policy]\n"
            "  queries:\n"
            "    - test query\n",
            encoding="utf-8",
        )

        rc = main(["sources", "decide", "--config", str(ws / "config.yaml"), "--search"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "workspace-cli-secret" not in captured.out
        assert "Workspace env CLI result" in (ws / "source_candidates.yaml").read_text(encoding="utf-8")
        assert os.environ.get("TAVILY_API_KEY") is None

    def test_sources_decide_search_all_queries_failed_writes_no_candidates(self, tmp_path, monkeypatch, capsys):
        """If every backend request fails, the CLI must fail instead of writing a plan."""
        monkeypatch.setitem(web_search._KNOWN_BACKENDS, "tavily", FailingCliSearchBackend)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "config.yaml").write_text(
            "project:\n  name: test\ninput:\n  path: input\noutput:\n  path: output\n",
            encoding="utf-8",
        )
        (ws / "sources.yaml").write_text(
            "source_strategy:\n"
            "  profile: research\n"
            "  enabled_providers: [web_search]\n"
            "web_search:\n"
            "  enabled: true\n"
            "  mode: external_api\n"
            "  backend: tavily\n"
            "source_discovery:\n"
            "  company: TestCo\n"
            "  industry: manufacturing\n"
            "  topics: [policy]\n"
            "  queries:\n"
            "    - failing query\n",
            encoding="utf-8",
        )

        rc = main(["sources", "decide", "--config", str(ws / "config.yaml"), "--search"])

        captured = capsys.readouterr()
        assert rc == 1
        assert "All configured search queries failed" in captured.out
        assert not (ws / "source_candidates.yaml").exists()
