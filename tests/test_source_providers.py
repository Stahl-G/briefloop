"""Tests for the Source Provider system."""

from __future__ import annotations

import hashlib
import json
import os

from multi_agent_brief.sources.base import (
    SourceConfig,
    SourceQuery,
)
from multi_agent_brief.sources.search_backends.base import (
    SearchBackendError,
    SearchResult,
)
from multi_agent_brief.sources.search_backends.tavily import (
    TavilyBackend,
)
from multi_agent_brief.sources.manual import ManualProvider
from multi_agent_brief.sources.web_search import WebSearchProvider
from multi_agent_brief.sources.registry import (
    collect_all_sources,
)


class EnvSearchBackend:
    """Fake backend that behaves like real backends by reading os.environ."""

    name = "env_fake"

    def __init__(self, api_key_env: str = "TAVILY_API_KEY") -> None:
        self._api_key_env = api_key_env

    def search(self, query, max_results=10, *, domains=None, **kwargs):
        if not os.environ.get(self._api_key_env):
            return []
        return [
            SearchResult(
                title="Workspace env result",
                url="https://example.com/workspace-env",
                snippet="Workspace .env backed search result.",
                published_at="2026-06-01",
                source_name="Env Fake Search",
            )
        ]

    def is_available(self):
        return bool(os.environ.get(self._api_key_env))


# --- ManualProvider ---


def test_manual_provider_loads_local_files(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "news.md").write_text(
        "- Manufacturing demand grew 10% in Q1.\n- New tariff announced.\n",
        encoding="utf-8",
    )

    provider = ManualProvider()
    config = {"sources": [{"name": "Test", "path": str(input_dir)}]}
    query = SourceQuery()
    items = provider.collect(query, config)

    assert len(items) == 1
    assert items[0].source_type == "local_file"
    assert "Manufacturing demand" in items[0].content


# --- WebSearchProvider with injected backend ---


def test_web_search_runtime_tool_collects_no_python_sources_without_error():
    """runtime_tool search is provided by the Orchestrator, not Python provider collection."""
    config = SourceConfig(
        enabled_providers=["web_search"],
        web_search={"enabled": True, "mode": "runtime_tool"},
    )
    items, errors = collect_all_sources(config)
    assert items == []
    assert errors == []


def test_web_search_runtime_tool_rejects_backend_configuration():
    errors = WebSearchProvider().validate_config(
        {"enabled": True, "mode": "runtime_tool", "backend": "tavily"}
    )

    assert errors
    assert "runtime_tool must not configure backend" in errors[0]


def test_web_search_collect_uses_workspace_env_for_known_backend_key(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "TAVILY_API_KEY=workspace-secret-for-collect\n",
        encoding="utf-8",
    )
    provider = WebSearchProvider(backend=EnvSearchBackend())

    items = provider.collect(
        SourceQuery(keywords=["manufacturing"]),
        {
            "enabled": True,
            "mode": "external_api",
            "backend": "tavily",
            "_workspace_dir": str(tmp_path),
        },
    )

    assert len(items) == 1
    assert items[0].metadata["backend"] == "env_fake"
    assert os.environ.get("TAVILY_API_KEY") is None


def test_tavily_normalizes_strict_dates_and_preserves_provider_value(monkeypatch):
    sentinel = "test-only-tavily-key"
    cases = (
        ("Thu, 23 Jul 2026 22:59:50 GMT", "2026-07-23"),
        ("Wed, 22 Jul 2026 05:30:00 GMT", "2026-07-22"),
        ("2026-07-23", "2026-07-23"),
        ("2026-07-23T23:30:00-02:00", "2026-07-24"),
        ("2026-07-23T23:30:00", "2026-07-23"),
        ("Thu, 23 Jul 2026 00:30:00 +1400", "2026-07-22"),
        ("Thu, 23 Jul 2026 22:59:50", ""),
        ("Fri, 23 Jul 2026 22:59:50 GMT", ""),
        ("July 23, 2026", ""),
        ("2 days ago", ""),
        (" 2026-07-23", ""),
        ("2026-02-30", ""),
    )
    current_published_date = ""
    response_bytes = b""

    class _FakeResponse:
        status = 200

        def read(self, _limit=-1):
            return response_bytes

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _urlopen(_request, timeout=30):
        assert timeout == 30
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setenv("TAVILY_API_KEY", sentinel)

    for current_published_date, expected in cases:
        response_bytes = json.dumps(
            {
                "results": [
                    {
                        "title": "Dated result",
                        "url": "https://example.com/dated",
                        "content": "search snippet",
                        "raw_content": "retrieved durable page extract",
                        "published_date": current_published_date,
                        "score": 0.9,
                    }
                ]
            }
        ).encode("utf-8")

        response = TavilyBackend().search_response("test query", max_results=1)
        result = response.results[0]

        assert response.raw_response == response_bytes
        assert result.published_at == expected
        assert result.raw_projection["published_date"] == current_published_date
        assert result.metadata["date_status"] == (
            "published_at_present" if expected else "missing_published_at"
        )
        assert result.metadata["source_temporality"] == (
            "published" if expected else "retrieved_only"
        )
        assert sentinel not in repr(result)


def test_tavily_transport_failure_is_stable_and_value_free(monkeypatch):
    sentinel = "tvly-secret-must-not-escape"

    def _raise_transport_error(request, timeout=30):
        raise RuntimeError(sentinel)

    monkeypatch.setattr("urllib.request.urlopen", _raise_transport_error)
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-tavily-key")

    try:
        TavilyBackend().search("test query")
    except SearchBackendError as exc:
        assert str(exc) == "Tavily search failed"
        assert exc.backend == "tavily"
        assert exc.__cause__ is None
        assert exc.__context__ is None
        assert sentinel not in str(exc)
        assert sentinel not in repr(exc)
    else:
        raise AssertionError("transport failure must remain a typed failure")


def test_tavily_rejects_secret_or_secret_hash_in_ignored_response_field(
    monkeypatch,
):
    sentinel = "tvly-response-echo-sentinel"
    sentinel_hash = hashlib.sha256(sentinel.encode("utf-8")).hexdigest()
    echoed_values = (sentinel, sentinel_hash.upper())
    calls = 0

    class _FakeResponse:
        status = 200

        def __init__(self, echoed: str) -> None:
            self._echoed = echoed

        def read(self, _limit=-1):
            return json.dumps(
                {
                    "ignored_diagnostic": self._echoed,
                    "results": [
                        {
                            "title": "Durable result",
                            "url": "https://example.com/durable",
                            "content": "search snippet",
                            "raw_content": "retrieved durable page extract",
                            "score": 0.9,
                        }
                    ],
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    current_echo = ""

    def _urlopen(_request, timeout=30):
        nonlocal calls
        assert timeout == 30
        calls += 1
        return _FakeResponse(current_echo)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setenv("TAVILY_API_KEY", sentinel)

    for index, echoed in enumerate(echoed_values, start=1):
        current_echo = echoed
        try:
            TavilyBackend().search_response("test query", max_results=1)
        except SearchBackendError as exc:
            assert str(exc) == "Tavily search failed"
            assert exc.backend == "tavily"
            assert exc.__cause__ is None
            assert exc.__context__ is None
            assert sentinel not in str(exc)
            assert sentinel not in repr(exc)
            assert sentinel_hash not in str(exc).lower()
            assert sentinel_hash not in repr(exc).lower()
        else:
            raise AssertionError("credential echo must remain a typed failure")
        assert calls == index
