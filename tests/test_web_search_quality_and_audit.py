"""Tests for web search quality filtering, date metadata, and audit policy.

Covers B1/B2/B3/B4/B7 fixes.
No real network calls — all backends are mocked.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim, PipelineContext
from multi_agent_brief.sources.base import SourceItem, SourceQuery
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.tavily import TavilyBackend, DEFAULT_API_KEY_ENV
from multi_agent_brief.sources.web_search import WebSearchProvider
from multi_agent_brief.sources.content_quality import is_low_quality_snippet, sanitize_snippet
from multi_agent_brief.sources.industry_packs import get_industry_pack, list_industries


# ---------------------------------------------------------------------------
# B1: Tavily date metadata
# ---------------------------------------------------------------------------

class TestTavilyDateMetadata:

    def test_result_without_published_date(self, monkeypatch):
        """Tavily result without published_date should have missing_published_at metadata."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")

        mock_response = {
            "results": [
                {
                    "title": "Trade Journal Report",
                    "url": "https://trade-journal.com/report",
                    "content": "Manufacturing capacity expanded.",
                    "score": 0.87,
                    # No published_date
                },
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            body = json.dumps(mock_response).encode("utf-8")
            resp = io.BytesIO(body)
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = TavilyBackend()
            results = backend.search("test query", max_results=5)

        assert len(results) == 1
        assert results[0].published_at == ""
        assert results[0].metadata["date_status"] == "missing_published_at"
        assert results[0].metadata["source_temporality"] == "retrieved_only"
        assert results[0].metadata["backend"] == "tavily"
        assert results[0].metadata["query"] == "test query"

    def test_result_with_published_date(self, monkeypatch):
        """Tavily result with published_date should have published_at_present metadata."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")

        mock_response = {
            "results": [
                {
                    "title": "Policy Update",
                    "url": "https://gov-policy.org/update",
                    "content": "New regulations announced.",
                    "published_date": "2026-06-01",
                    "score": 0.95,
                },
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            body = json.dumps(mock_response).encode("utf-8")
            resp = io.BytesIO(body)
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            backend = TavilyBackend()
            results = backend.search("test query", max_results=5)

        assert len(results) == 1
        assert results[0].published_at == "2026-06-01"
        assert results[0].metadata["date_status"] == "published_at_present"
        assert results[0].metadata["source_temporality"] == "published"

    def test_web_search_provider_passes_date_metadata(self, monkeypatch):
        """WebSearchProvider should pass date_status and source_temporality to SourceItem."""
        monkeypatch.setenv(DEFAULT_API_KEY_ENV, "tvly-test-key")

        mock_response = {
            "results": [
                {
                    "title": "No Date Result",
                    "url": "https://example.com/no-date",
                    "content": "Some content without a date.",
                    "score": 0.8,
                },
            ]
        }

        def mock_urlopen(req, timeout=30):
            import io
            body = json.dumps(mock_response).encode("utf-8")
            resp = io.BytesIO(body)
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", mock_urlopen):
            provider = WebSearchProvider()
            config = {"enabled": True, "backend": "tavily", "search_tasks": [{"query": "test"}]}
            items = provider.collect(SourceQuery(), config)

        assert len(items) == 1
        assert items[0].published_at == ""
        assert items[0].metadata["date_status"] == "missing_published_at"
        assert items[0].metadata["source_temporality"] == "retrieved_only"
        assert items[0].metadata["backend"] == "tavily"
        assert "retrieved_at" in items[0].metadata


# ---------------------------------------------------------------------------
# B2/B3: Content quality filtering and Scout handling
# ---------------------------------------------------------------------------

class TestContentQuality:

    def test_cookie_snippet_is_low_quality(self):
        """Cookie/privacy snippets should be flagged as low quality."""
        assert is_low_quality_snippet("We use cookies to improve your experience. Privacy Policy")
        assert is_low_quality_snippet("This site uses cookies. By continuing, you agree to our Terms of Use.")
        assert is_low_quality_snippet("Subscribe to our newsletter for updates")
        assert is_low_quality_snippet("Sign in or Login to continue")
        assert is_low_quality_snippet("All rights reserved. Contact us for more info.")

    def test_chinese_boilerplate_is_low_quality(self):
        """Chinese boilerplate should be flagged as low quality."""
        assert is_low_quality_snippet("本网站使用隐私政策和用户协议")
        assert is_low_quality_snippet("请登录或注册以继续浏览")
        assert is_low_quality_snippet("免责声明：本文仅供参考")

    def test_toc_snippet_is_low_quality(self):
        """Table-of-contents-like snippets should be flagged as low quality."""
        assert is_low_quality_snippet("Chapter 1 | Chapter 2 | Chapter 3 | Chapter 4")
        assert is_low_quality_snippet("Section A > Section B > Section C > Section D")

    def test_url_only_snippet_is_low_quality(self):
        """URL-only snippets should be flagged as low quality."""
        assert is_low_quality_snippet("https://example.com/full-article")

    def test_short_snippet_is_low_quality(self):
        """Very short snippets should be flagged as low quality."""
        assert is_low_quality_snippet("Hi")
        assert is_low_quality_snippet("")

    def test_valid_snippet_is_not_low_quality(self):
        """Valid business content should not be flagged."""
        assert not is_low_quality_snippet(
            "Manufacturing PMI rose to 52.3 in May 2026, indicating expansion in the sector."
        )
        assert not is_low_quality_snippet(
            "The company announced a $2.5 billion investment in new battery manufacturing capacity."
        )

    def test_sanitize_snippet_collapses_whitespace(self):
        """sanitize_snippet should collapse internal whitespace."""
        result = sanitize_snippet("  Multiple   spaces   between   words  here for testing. ")
        assert "  " not in result
        assert len(result) > 0

    def test_sanitize_snippet_returns_empty_for_short(self):
        """sanitize_snippet should return empty for too-short results."""
        assert sanitize_snippet("Hi") == ""
        assert sanitize_snippet("") == ""


class TestScoutWebSearch:

    def test_cookie_snippet_produces_zero_claims(self):
        """Web search cookie/privacy snippet should produce zero claims."""
        from multi_agent_brief.agents.scout import ScoutAgent

        source = SourceItem(
            source_id="WS_COOKIE",
            source_name="web_search",
            source_type="web_search",
            title="Cookie Notice",
            content="We use cookies to improve your experience. Privacy Policy Terms of Use.",
            url="https://example.com",
        )

        ledger = ClaimLedger()
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/nonexistent",
            output_dir="/tmp/nonexistent",
        )
        context.sources = [source]

        agent = ScoutAgent()
        agent.run(context, ledger)

        assert len(context.candidates) == 0
        assert len(ledger) == 0

    def test_toc_snippet_produces_zero_claims(self):
        """Web search table-of-contents snippet should produce zero claims."""
        from multi_agent_brief.agents.scout import ScoutAgent

        source = SourceItem(
            source_id="WS_TOC",
            source_name="web_search",
            source_type="web_search",
            title="Report Contents",
            content="Chapter 1 | Chapter 2 | Chapter 3 | Chapter 4 | Chapter 5",
            url="https://example.com/report",
        )

        ledger = ClaimLedger()
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/nonexistent",
            output_dir="/tmp/nonexistent",
        )
        context.sources = [source]

        agent = ScoutAgent()
        agent.run(context, ledger)

        assert len(context.candidates) == 0
        assert len(ledger) == 0

    def test_valid_web_search_produces_one_claim(self):
        """Valid web search snippet should produce at most one claim."""
        from multi_agent_brief.agents.scout import ScoutAgent

        source = SourceItem(
            source_id="WS_VALID",
            source_name="web_search",
            source_type="web_search",
            title="Manufacturing PMI Update",
            content="Manufacturing PMI rose to 52.3 in May 2026, indicating expansion in the sector.",
            url="https://example.com/pmi",
        )

        ledger = ClaimLedger()
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/nonexistent",
            output_dir="/tmp/nonexistent",
        )
        context.sources = [source]

        agent = ScoutAgent()
        agent.run(context, ledger)

        assert len(context.candidates) == 1
        assert len(ledger) == 1

    def test_is_placeholder_skips_error_type(self):
        """_is_placeholder should skip sources with error_type metadata."""
        from multi_agent_brief.agents.scout import _is_placeholder

        source = SourceItem(
            source_id="WS_ERR",
            source_name="web_search",
            source_type="web_search",
            title="Error",
            content="",
            metadata={"error_type": "fetch_failed"},
        )
        assert _is_placeholder(source)

    def test_is_placeholder_skips_source_type_ending_error(self):
        """_is_placeholder should skip source_type ending with '_error'."""
        from multi_agent_brief.agents.scout import _is_placeholder

        source = SourceItem(
            source_id="RSS_ERR",
            source_name="rss",
            source_type="rss_error",
            title="Error",
            content="",
        )
        assert _is_placeholder(source)

    def test_is_placeholder_skips_low_quality(self):
        """_is_placeholder should skip sources with low_quality metadata."""
        from multi_agent_brief.agents.scout import _is_placeholder

        source = SourceItem(
            source_id="WS_LQ",
            source_name="web_search",
            source_type="web_search",
            title="Low Quality",
            content="",
            metadata={"low_quality": True},
        )
        assert _is_placeholder(source)

    def test_is_placeholder_skips_filtered_reason(self):
        """_is_placeholder should skip sources with filtered_reason metadata."""
        from multi_agent_brief.agents.scout import _is_placeholder

        source = SourceItem(
            source_id="WS_FR",
            source_name="web_search",
            source_type="web_search",
            title="Filtered",
            content="",
            metadata={"filtered_reason": "boilerplate"},
        )
        assert _is_placeholder(source)

    def test_is_placeholder_skips_requires_fetch(self):
        """_is_placeholder should still skip requires_fetch."""
        from multi_agent_brief.agents.scout import _is_placeholder

        source = SourceItem(
            source_id="WS_RF",
            source_name="web_search",
            source_type="manual_url",
            title="Placeholder",
            content="Manual URL source: https://example.com",
            metadata={"requires_fetch": True},
        )
        assert _is_placeholder(source)


# ---------------------------------------------------------------------------
# B4: Automotive industry pack
# ---------------------------------------------------------------------------

class TestAutomotivePack:

    def test_automotive_pack_returns_non_empty_search_tasks(self):
        """Automotive pack should have non-empty search_tasks."""
        pack = get_industry_pack("automotive")
        assert pack is not None
        assert len(pack["search_tasks"]) > 0
        assert all(task["query"] for task in pack["search_tasks"])

    def test_automotive_aliases(self):
        """Automotive aliases should resolve to the automotive pack."""
        for alias in ["auto", "mobility", "vehicle", "ev"]:
            pack = get_industry_pack(alias)
            assert pack is not None, f"Alias '{alias}' should resolve to automotive pack"
            assert pack["name"] == "Automotive / Mobility"

    def test_automotive_in_list_industries(self):
        """Automotive should appear in list_industries()."""
        industries = list_industries()
        assert "automotive" in industries

    def test_automotive_pack_has_required_topics(self):
        """Automotive pack should cover production, EV, regulation, autonomous driving."""
        pack = get_industry_pack("automotive")
        queries = " ".join(task["query"] for task in pack["search_tasks"])
        assert "production" in queries or "sales" in queries
        assert "EV" in queries or "battery" in queries
        assert "regulation" in queries or "tariff" in queries
        assert "autonomous" in queries or "software defined" in queries


# ---------------------------------------------------------------------------
# B7: Audit policy for web_search missing published_at
# ---------------------------------------------------------------------------

class TestAuditWebSearchDatePolicy:

    def test_web_search_missing_published_at_does_not_fail_audit(self):
        """Audit should not fail solely due to web_search missing published_at."""
        ledger = ClaimLedger(
            [
                Claim(
                    claim_id="WS_ABCDEF",
                    statement="Manufacturing PMI rose to 52.3.",
                    source_id="WS_TEST",
                    evidence_text="Manufacturing PMI rose to 52.3.",
                    source_type="web_search",
                    metadata={"published_at": ""},
                )
            ]
        )
        markdown = "- Manufacturing PMI rose to 52.3. [src:WS_ABCDEF]"

        report = run_deterministic_audit(
            markdown,
            ledger,
            report_date="2026-06-02",
            max_source_age_days=14,
        )

        # Should not fail — web_search missing published_at is recorded in metadata
        assert report.audit_status != "fail"
        assert report.metadata.get("web_search_missing_published_at", 0) == 1

    def test_web_search_missing_published_at_no_medium_finding(self):
        """web_search missing published_at should not create medium severity finding."""
        ledger = ClaimLedger(
            [
                Claim(
                    claim_id="WS_ABCDEF",
                    statement="A web search result without date.",
                    source_id="WS_TEST",
                    evidence_text="A web search result without date.",
                    source_type="web_search",
                    metadata={"published_at": ""},
                )
            ]
        )
        markdown = "- A web search result without date. [src:WS_ABCDEF]"

        report = run_deterministic_audit(
            markdown,
            ledger,
            report_date="2026-06-02",
            max_source_age_days=14,
        )

        date_findings = [f for f in report.findings if f.finding_type == "missing_source_date"]
        assert len(date_findings) == 0

    def test_local_file_missing_published_at_still_warns(self):
        """local_file missing published_at should still get medium severity warning."""
        ledger = ClaimLedger(
            [
                Claim(
                    claim_id="LF_ABCDEF",
                    statement="A local file source without date.",
                    source_id="LF_TEST",
                    evidence_text="A local file source without date.",
                    source_type="local_file",
                    metadata={"published_at": ""},
                )
            ]
        )
        markdown = "- A local file source without date. [src:LF_ABCDEF]"

        report = run_deterministic_audit(
            markdown,
            ledger,
            report_date="2026-06-02",
            max_source_age_days=14,
        )

        date_findings = [f for f in report.findings if f.finding_type == "missing_source_date"]
        assert len(date_findings) == 1
        assert date_findings[0].severity == "medium"

    def test_stale_source_only_when_parseable(self):
        """Stale source check should only apply when published_at is parseable."""
        ledger = ClaimLedger(
            [
                Claim(
                    claim_id="WS_STALE",
                    statement="An old web search result.",
                    source_id="WS_TEST",
                    evidence_text="An old web search result.",
                    source_type="web_search",
                    metadata={"published_at": "2026-01-01"},
                ),
                Claim(
                    claim_id="WS_NODATE",
                    statement="A web search result without date.",
                    source_id="WS_TEST2",
                    evidence_text="A web search result without date.",
                    source_type="web_search",
                    metadata={"published_at": ""},
                ),
            ]
        )
        markdown = (
            "- An old web search result. [src:WS_STALE]\n"
            "- A web search result without date. [src:WS_NODATE]"
        )

        report = run_deterministic_audit(
            markdown,
            ledger,
            report_date="2026-06-02",
            max_source_age_days=14,
            fail_on_stale_source=True,
        )

        stale_findings = [f for f in report.findings if f.finding_type == "stale_source"]
        assert len(stale_findings) == 1
        assert stale_findings[0].related_claim_id == "WS_STALE"
