"""Tests for B02: sources decide --search must actually execute searches."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

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


class EmptyCliSearchBackend:
    """CLI fake backend that succeeds but returns no results."""
    name = "tavily"

    def search(self, query, max_results=10, *, domains=None, **kwargs):
        return []

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

    def test_sources_decide_public_cli_is_retired_with_zero_writes(self, tmp_path, capsys):
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
            "    - test query\n",
            encoding="utf-8",
        )
        variants = [
            ["sources", "decide", "--config", str(ws / "config.yaml")],
            ["sources", "decide", "--config", str(ws / "config.yaml"), "--search"],
            ["sources", "decide", "--config", str(ws / "config.yaml"), "--search", "--daily-news-backfill"],
            ["sources", "decide", "--config", str(ws / "config.yaml"), "--merge"],
        ]
        for args in variants:
            before = {
                path.relative_to(ws).as_posix(): path.read_bytes()
                for path in ws.rglob("*")
                if path.is_file()
            }

            rc = main(args)
            out = capsys.readouterr().out

            # LEGACY-DELETE: retired public `sources decide` command and its
            # typed rejection with zero writes.
            assert rc == 1
            assert out == "runtime_command_unsupported\n"
            after = {
                path.relative_to(ws).as_posix(): path.read_bytes()
                for path in ws.rglob("*")
                if path.is_file()
            }
            assert after == before
        assert not (ws / "source_candidates.yaml").exists()

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

    # LD-1 TD citations for the removed rows above:
    # - test_sources_decide_search_uses_workspace_env_key: TD-2, env-key path
    #   owned by test_source_providers.py web_search env/backend rows.
    # - test_sources_decide_search_rejects_invalid_web_search_modes: TD-2,
    #   mode validation owned by test_source_providers.py
    #   test_web_search_disabled_returns_empty /
    #   test_web_search_external_api_without_backend_returns_registry_error /
    #   test_web_search_runtime_tool_rejects_backend_configuration.
    # - test_sources_decide_search_all_queries_failed/zero_results_writes_no_candidates:
    #   TD-1, their subject (the retired `_sources_decide` mechanism) was
    #   deleted in LEGACY-DELETE; provider failure behavior is covered by the
    #   provider test suite.
