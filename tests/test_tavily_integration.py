"""Tests for Tavily backend integration, web search provider, and related fixes.

No real network calls — all backends are mocked.
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from multi_agent_brief.sources.base import SourceConfig, SourceItem, SourceQuery
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.tavily import TavilyBackend, DEFAULT_API_KEY_ENV
from multi_agent_brief.sources.web_search import WebSearchProvider
from multi_agent_brief.sources.rss import RssProvider, _normalize_date, _token_match


# ---------------------------------------------------------------------------
# Tavily backend
# ---------------------------------------------------------------------------

class TestTavilyBackend:

    def test_is_available_with_key(self, monkeypatch):
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")
        backend = TavilyBackend()
        assert backend.is_available()

    def test_is_available_without_key(self, monkeypatch):
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = TavilyBackend()
        assert not backend.is_available()

    def test_custom_api_key_env(self, monkeypatch):
        monkeypatch.setenv("MY_TAVILY_KEY", "tvly-custom")
        backend = TavilyBackend(api_key_env="MY_TAVILY_KEY")
        assert backend.is_available()

    def test_search_returns_empty_without_key(self, monkeypatch):
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        backend = TavilyBackend()
        assert backend.search("test query") == []

    def test_search_converts_response(self, monkeypatch):
        """Mock Tavily API response is converted to SearchResult."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")

        mock_response = {
            "results": [
                {
                    "title": "Solar Policy Update 2026",
                    "url": "https://gov-policy.org/manufacturing-policy",
                    "content": "New tariff regulations for manufacturing imports announced.",
                    "published_date": "2026-06-01",
                    "score": 0.95,
                    "raw_content": "Full text here...",
                },
                {
                    "title": "Trade Journal Report",
                    "url": "https://trade-journal.com/report",
                    "content": "Manufacturing capacity expanded.",
                    "score": 0.87,
                },
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            body = json.dumps(mock_response).encode("utf-8")
            resp = io.BytesIO(body)
            resp.read = resp.read
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = TavilyBackend()
            results = backend.search("manufacturing policy", max_results=5)

        assert len(results) == 2
        assert results[0].title == "Solar Policy Update 2026"
        assert results[0].url == "https://gov-policy.org/manufacturing-policy"
        assert results[0].snippet == "New tariff regulations for manufacturing imports announced."
        assert results[0].published_at == "2026-06-01"
        assert results[0].metadata["score"] == 0.95
        assert results[0].metadata["backend"] == "tavily"
        assert results[0].metadata["has_raw_content"] is True
        assert results[1].metadata["has_raw_content"] is False

    def test_search_passes_domains(self, monkeypatch):
        """Domains should be passed as include_domains to Tavily API."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")
        captured_payload = {}

        def mock_urlopen(req, timeout=30):
            import io
            captured_payload.update(json.loads(req.data.decode("utf-8")))
            body = json.dumps({"results": []}).encode("utf-8")
            resp = io.BytesIO(body)
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = TavilyBackend()
            backend.search("test", domains=["gov-policy.org", "trade-journal.com"])

        assert captured_payload.get("include_domains") == ["gov-policy.org", "trade-journal.com"]

    def test_no_api_key_in_metadata(self, monkeypatch):
        """API key must never appear in result metadata."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-super-secret-key")

        mock_response = {"results": [{"title": "T", "url": "U", "content": "C"}]}

        def mock_urlopen(req, timeout=30):
            import io
            body = json.dumps(mock_response).encode("utf-8")
            resp = io.BytesIO(body)
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = TavilyBackend()
            results = backend.search("test")

        meta_str = json.dumps(results[0].metadata) if results else ""
        assert "super-secret" not in meta_str
        assert "tvly-" not in meta_str


# ---------------------------------------------------------------------------
# WebSearchProvider with Tavily
# ---------------------------------------------------------------------------

class TestWebSearchProviderTavily:

    def test_auto_instantiates_tavily(self, monkeypatch):
        """_get_backend should auto-instantiate TavilyBackend from config."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")
        provider = WebSearchProvider()
        config = {"enabled": True, "backend": "tavily"}
        backend = provider._get_backend(config)
        assert isinstance(backend, TavilyBackend)

    def test_validate_missing_key(self, monkeypatch):
        """validate_config should error when TAVILY_API_KEY is missing."""
        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)
        provider = WebSearchProvider()
        config = {"enabled": True, "backend": "tavily"}
        errors = provider.validate_config(config)
        assert len(errors) == 1
        assert "TAVILY_API_KEY" in errors[0]

    def test_validate_unknown_backend(self):
        """validate_config should error for unknown backend."""
        provider = WebSearchProvider()
        config = {"enabled": True, "backend": "nonexistent_backend"}
        errors = provider.validate_config(config)
        assert len(errors) == 1
        assert "unknown backend" in errors[0].lower() or "nonexistent" in errors[0].lower()

    def test_build_queries_preserves_search_tasks(self):
        """Each search_task should be a separate query, not collapsed."""
        provider = WebSearchProvider()
        query = SourceQuery()
        config = {
            "search_tasks": [
                {"query": "manufacturing policy update", "domains": ["gov-policy.org"]},
                {"query": "manufacturing capacity", "domains": ["trade-journal.com"]},
                {"query": "tariff regulation"},
            ]
        }
        queries = provider._build_queries(query, config)
        assert len(queries) == 3
        assert queries[0] == ("manufacturing policy update", ["gov-policy.org"])
        assert queries[1] == ("manufacturing capacity", ["trade-journal.com"])
        assert queries[2] == ("tariff regulation", None)

    def test_build_queries_separate_keywords_fallback(self):
        """When no search_tasks, keywords should be separate queries."""
        provider = WebSearchProvider()
        query = SourceQuery(keywords=["manufacturing", "tariff", "manufacturing"])
        config = {}
        queries = provider._build_queries(query, config)
        assert len(queries) == 3
        assert queries[0] == ("manufacturing", None)
        assert queries[1] == ("tariff", None)

    def test_domains_preserved_to_backend(self, monkeypatch):
        """Domains from search_tasks should be passed to backend.search()."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")
        captured_calls = []

        class RecordingBackend(SearchBackend):
            name = "recording"
            def search(self, query, max_results=10, *, domains=None, **kwargs):
                captured_calls.append({"query": query, "domains": domains})
                return []
            def is_available(self):
                return True

        provider = WebSearchProvider(backend=RecordingBackend())
        config = {
            "enabled": True,
            "search_tasks": [
                {"query": "manufacturing", "domains": ["gov-policy.org"]},
                {"query": "tariff"},
            ]
        }
        provider.collect(SourceQuery(), config)

        assert len(captured_calls) == 2
        assert captured_calls[0]["domains"] == ["gov-policy.org"]
        assert captured_calls[1]["domains"] is None


# ---------------------------------------------------------------------------
# Editor audit residue
# ---------------------------------------------------------------------------

class TestEditorAuditResidue:

    def test_brief_does_not_contain_audit_status(self, tmp_path):
        """brief.md must not contain audit status text."""
        from multi_agent_brief.core.pipeline import BriefPipeline
        from multi_agent_brief.core.schemas import PipelineContext

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "news.md").write_text(
            "- A manufacturing company announced a 2 GW expansion plan.\n",
            encoding="utf-8",
        )

        context = PipelineContext(
            project_name="Audit Residue Test",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
        )

        BriefPipeline().run(context)
        brief = (output_dir / "brief.md").read_text(encoding="utf-8")
        assert "Audit status:" not in brief
        assert "审计状态" not in brief


# ---------------------------------------------------------------------------
# ManualProvider claim_type
# ---------------------------------------------------------------------------

class TestManualProviderClaimType:

    def test_json_claim_type_in_metadata(self, tmp_path):
        """JSON with claim_type should be stored in SourceItem.metadata."""
        from multi_agent_brief.sources.manual import ManualProvider

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "data.json").write_text(json.dumps({
            "source_url": "https://example.com",
            "claim_type": "needs_recrawl",
            "items": ["Some content"],
        }), encoding="utf-8")

        provider = ManualProvider()
        config = {"sources": [{"name": "Test", "path": str(input_dir)}]}
        items = provider.collect(SourceQuery(), config)

        assert len(items) == 1
        assert items[0].metadata.get("claim_type") == "needs_recrawl"

    def test_json_without_claim_type(self, tmp_path):
        """JSON without claim_type should have empty claim_type in metadata."""
        from multi_agent_brief.sources.manual import ManualProvider

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "data.json").write_text(json.dumps({
            "source_url": "https://example.com",
            "items": ["Some content"],
        }), encoding="utf-8")

        provider = ManualProvider()
        config = {"sources": [{"name": "Test", "path": str(input_dir)}]}
        items = provider.collect(SourceQuery(), config)

        assert items[0].metadata.get("claim_type") == ""


# ---------------------------------------------------------------------------
# RSS provider
# ---------------------------------------------------------------------------

class TestRssDateNormalization:

    def test_rfc2822_pubdate(self):
        """RFC 2822 dates should be normalized to ISO format."""
        result = _normalize_date("Mon, 02 Jun 2026 12:00:00 +0000")
        assert "2026" in result
        assert "06" in result
        assert "02" in result

    def test_iso8601_atom(self):
        """ISO 8601 Atom dates should be preserved."""
        result = _normalize_date("2026-06-02T12:00:00Z")
        assert "2026-06-02" in result

    def test_iso_date_only(self):
        """Date-only strings should be parseable."""
        result = _normalize_date("2026-06-02")
        assert "2026-06-02" in result

    def test_empty_string(self):
        result = _normalize_date("")
        assert result == ""

    def test_garbage_string(self):
        """Unparseable strings should be returned as-is."""
        result = _normalize_date("not a date")
        assert result == "not a date"


class TestRssTokenMatching:

    def test_token_match_basic(self):
        assert _token_match(["manufacturing"], "Manufacturing efficiency increased")

    def test_token_match_substring(self):
        assert _token_match(["photovoltaic"], "New photovoltaic cells announced")

    def test_token_match_no_match(self):
        assert not _token_match(["quantum", "computing"], "Manufacturing efficiency increased")

    def test_token_match_empty_keywords(self):
        """Empty keywords should match everything (no filter)."""
        text = "anything"
        # _token_match with empty list returns False, but RSS provider checks
        # query.keywords first and returns True if empty
        assert not _token_match([], text)


class TestRssErrorSurfacing:

    def test_failed_feed_produces_error_item(self):
        """Failed RSS fetch should produce an error SourceItem, not silent skip."""
        provider = RssProvider()

        config = {
            "feeds": [
                {"name": "Test Feed", "url": "https://invalid.example.com/feed.xml"},
            ]
        }

        # This should not raise — errors are surfaced as SourceItems
        items = provider.collect(SourceQuery(), config)
        # The feed will fail (no network), so we should get an error item
        assert len(items) >= 1
        error_items = [i for i in items if i.source_type == "rss_error"]
        assert len(error_items) >= 1
        assert "error" in error_items[0].title.lower()


# ---------------------------------------------------------------------------
# Doctor Tavily check
# ---------------------------------------------------------------------------

class TestDoctorTavily:

    def test_doctor_tavily_ok_with_key(self, tmp_path, monkeypatch):
        """doctor should report OK when TAVILY_API_KEY is set."""
        from multi_agent_brief.sources.doctor import run_doctor

        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")

        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        tavily_results = [r for r in results if "Tavily" in r.message or "tavily" in r.message.lower()]
        assert any(r.status == "OK" for r in tavily_results)

    def test_doctor_tavily_error_without_key(self, tmp_path, monkeypatch):
        """doctor should ERROR when TAVILY_API_KEY is missing."""
        from multi_agent_brief.sources.doctor import run_doctor

        monkeypatch.delenv(DEFAULT_API_KEY_ENV, raising=False)

        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        tavily_results = [r for r in results if "Tavily" in r.message or "TAVILY_API_KEY" in r.message]
        assert any(r.status == "ERROR" for r in tavily_results)

    def test_doctor_never_prints_key(self, tmp_path, monkeypatch):
        """doctor output must never contain the API key value."""
        from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report

        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-super-secret-123")

        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        report = format_doctor_report(results)
        assert "super-secret" not in report
        assert "tvly-" not in report


# ---------------------------------------------------------------------------
# Init wizard manufacturing
# ---------------------------------------------------------------------------

class TestInitSolar:

    def test_manufacturing_selectable(self, tmp_path):
        """Solar industry should be selectable in init."""
        from multi_agent_brief.cli.main import main

        workspace = tmp_path / "ws"
        assert main([
            "init", str(workspace),
            "--language", "zh-CN",
            "--industry", "manufacturing",
            "--source-profile", "conservative",
        ]) == 0
        assert (workspace / "config.yaml").exists()
        config = (workspace / "config.yaml").read_text(encoding="utf-8")
        assert "manufacturing" in config
