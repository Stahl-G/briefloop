"""Tests for Local Signal Discovery (v0.5.1).

Covers:
- local_signal_planner.py: task generation, market hints, query templates
- decider.py: search queries with local language, source candidates with local tasks
- audit: LOCAL_SIGNAL_CLAIM_001, LOCAL_SIGNAL_PROVENANCE_001, LOCAL_SIGNAL_PRIVACY_001
- collector_tasks.json generation
- local_signal_samples.jsonl parsing
- local_signal_report.json generation
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.sources.local_signal_planner import (
    MARKET_PLATFORM_HINTS,
    LocalSignalTask,
    build_local_signal_tasks,
    generate_collector_tasks,
    generate_local_signal_report,
    parse_local_signal_samples,
)
from multi_agent_brief.sources.decider import (
    build_search_queries,
    build_search_tasks_with_metadata,
    generate_source_candidates,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _discovery_vietnam() -> dict:
    """A discovery config targeting Vietnam skincare market."""
    return {
        "company": "One Leaf",
        "industry": "skincare",
        "language": "zh",
        "focus_areas": ["company", "industry", "policy"],
        "local_signal_discovery": {
            "enabled": True,
            "target_markets": [
                {"market": "Vietnam", "local_languages": ["vi"]},
            ],
            "platform_groups": ["ecommerce", "social"],
            "consumer_signal_goals": ["complaints", "purchase_barriers", "price_sensitivity"],
            "execution_modes": [
                "public_web_search",
                "manual_review",
                "authorized_browser_collection",
                "opencli_local_extraction",
            ],
        },
    }


def _discovery_disabled() -> dict:
    """Discovery with local signal disabled."""
    return {
        "company": "TestCo",
        "industry": "finance",
        "language": "en",
        "local_signal_discovery": {"enabled": False},
    }


def _discovery_no_markets() -> dict:
    """Discovery with local signal enabled but no target markets."""
    return {
        "company": "TestCo",
        "industry": "finance",
        "language": "en",
        "local_signal_discovery": {"enabled": True, "target_markets": []},
    }


def _discovery_japan() -> dict:
    """Discovery targeting Japan."""
    return {
        "company": "TestCo",
        "industry": "electronics",
        "language": "en",
        "local_signal_discovery": {
            "enabled": True,
            "target_markets": ["Japan"],
            "platform_groups": ["ecommerce", "video"],
            "consumer_signal_goals": ["complaints", "product_comparison"],
        },
    }


def _discovery_china() -> dict:
    """Discovery targeting China."""
    return {
        "company": "TestCo",
        "industry": "beauty",
        "language": "zh",
        "local_signal_discovery": {
            "enabled": True,
            "target_markets": ["China"],
            "platform_groups": ["social", "forum"],
            "consumer_signal_goals": ["brand_mentions", "purchase_barriers"],
        },
    }


# ── Task Generation Tests ────────────────────────────────────────────


class TestLocalSignalTaskGeneration:
    """Test build_local_signal_tasks()."""

    def test_vietnam_generates_vi_tasks(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        assert len(tasks) > 0
        assert any(t.language == "vi" for t in tasks)

    def test_vietnam_tasks_have_correct_market(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        assert all(t.market == "Vietnam" for t in tasks)

    def test_vietnam_tasks_have_signal_type(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        assert all(t.signal_type == "consumer_discussion" for t in tasks)

    def test_vietnam_tasks_have_platform_groups(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        groups = {t.platform_group for t in tasks}
        assert "ecommerce" in groups
        assert "social" in groups

    def test_vietnam_tasks_have_suggested_platforms(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        ecommerce_tasks = [t for t in tasks if t.platform_group == "ecommerce"]
        assert any("Shopee" in t.suggested_platforms for t in ecommerce_tasks)

    def test_vietnam_tasks_have_execution_modes(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        assert all("public_web_search" in t.execution_mode for t in tasks)
        assert all("opencli_local_extraction" in t.execution_mode for t in tasks)

    def test_vietnam_tasks_have_evidence_limit(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        assert all(t.may_support_current_fact is False for t in tasks)
        assert all(t.requires_current_source is True for t in tasks)

    def test_disabled_returns_empty(self):
        tasks = build_local_signal_tasks(_discovery_disabled())
        assert tasks == []

    def test_no_markets_returns_empty(self):
        tasks = build_local_signal_tasks(_discovery_no_markets())
        assert tasks == []

    def test_japan_generates_ja_tasks(self):
        tasks = build_local_signal_tasks(_discovery_japan())
        assert any(t.language == "ja" for t in tasks)

    def test_japan_tasks_have_japanese_platforms(self):
        tasks = build_local_signal_tasks(_discovery_japan())
        ecommerce_tasks = [t for t in tasks if t.platform_group == "ecommerce"]
        assert any("Rakuten" in t.suggested_platforms for t in ecommerce_tasks)

    def test_china_generates_zh_tasks(self):
        tasks = build_local_signal_tasks(_discovery_china())
        assert any(t.language == "zh" for t in tasks)

    def test_china_tasks_have_chinese_platforms(self):
        tasks = build_local_signal_tasks(_discovery_china())
        social_tasks = [t for t in tasks if t.platform_group == "social"]
        assert any("小红书" in t.suggested_platforms for t in social_tasks)

    def test_task_id_format(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        for task in tasks:
            assert task.task_id.startswith("LS_")

    def test_task_to_dict(self):
        tasks = build_local_signal_tasks(_discovery_vietnam())
        d = tasks[0].to_dict()
        assert "task_id" in d
        assert "market" in d
        assert "evidence_limit" in d
        assert d["evidence_limit"]["may_support_current_fact"] is False

    def test_string_market_format(self):
        """Test that string market entries work (not just dict format)."""
        discovery = {
            "company": "TestCo",
            "industry": "tech",
            "local_signal_discovery": {
                "enabled": True,
                "target_markets": ["Vietnam"],
            },
        }
        tasks = build_local_signal_tasks(discovery)
        assert len(tasks) > 0
        assert tasks[0].market == "Vietnam"


# ── Market Platform Hints Tests ──────────────────────────────────────


class TestMarketPlatformHints:
    """Test MARKET_PLATFORM_HINTS structure."""

    def test_vietnam_has_languages(self):
        assert "vi" in MARKET_PLATFORM_HINTS["vietnam"]["languages"]

    def test_japan_has_languages(self):
        assert "ja" in MARKET_PLATFORM_HINTS["japan"]["languages"]

    def test_china_has_languages(self):
        assert "zh" in MARKET_PLATFORM_HINTS["china"]["languages"]

    def test_all_markets_have_platform_groups(self):
        for market, cfg in MARKET_PLATFORM_HINTS.items():
            assert "platform_groups" in cfg, f"{market} missing platform_groups"
            assert "ecommerce" in cfg["platform_groups"], f"{market} missing ecommerce"

    def test_all_markets_have_query_templates(self):
        for market, cfg in MARKET_PLATFORM_HINTS.items():
            assert "query_templates" in cfg, f"{market} missing query_templates"


# ── Collector Tasks Tests ────────────────────────────────────────────


class TestCollectorTasks:
    """Test generate_collector_tasks()."""

    def test_generates_collector_tasks(self):
        result = generate_collector_tasks(_discovery_vietnam())
        assert result["status"] == "ready"
        assert len(result["tasks"]) > 0

    def test_collector_task_has_instructions(self):
        result = generate_collector_tasks(_discovery_vietnam())
        for task in result["tasks"]:
            assert "instructions" in task
            assert len(task["instructions"]) > 0

    def test_collector_task_has_privacy_rules(self):
        result = generate_collector_tasks(_discovery_vietnam())
        for task in result["tasks"]:
            assert "privacy_rules" in task
            assert task["privacy_rules"]["do_not_collect_private_messages"] is True

    def test_disabled_returns_no_tasks(self):
        result = generate_collector_tasks(_discovery_disabled())
        assert result["status"] == "no_tasks"
        assert result["tasks"] == []


# ── Source Candidates Tests ───────────────────────────────────────────


class TestSourceCandidates:
    """Test generate_source_candidates() with local signal tasks."""

    def test_candidates_include_local_tasks(self):
        candidates = generate_source_candidates(_discovery_vietnam())
        assert "local_social_listening_tasks" in candidates
        assert len(candidates["local_social_listening_tasks"]) > 0

    def test_local_task_has_required_fields(self):
        candidates = generate_source_candidates(_discovery_vietnam())
        task = candidates["local_social_listening_tasks"][0]
        assert "task_id" in task
        assert "market" in task
        assert "language" in task
        assert "query" in task

    def test_disabled_has_no_local_tasks(self):
        candidates = generate_source_candidates(_discovery_disabled())
        assert "local_social_listening_tasks" not in candidates


# ── Search Queries Tests ─────────────────────────────────────────────


class TestSearchQueries:
    """Test build_search_queries() with local signal queries."""

    def test_queries_include_local_language(self):
        queries = build_search_queries(_discovery_vietnam())
        # Should have at least one Vietnamese query
        vi_queries = [q for q in queries if any(c in q for c in "đánh giá người dùng")]
        assert len(vi_queries) > 0

    def test_queries_deduplicated(self):
        queries = build_search_queries(_discovery_vietnam())
        assert len(queries) == len(set(queries))

    def test_disabled_has_no_local_queries(self):
        queries = build_search_queries(_discovery_disabled())
        # Standard queries only
        assert all("đánh giá" not in q for q in queries)


class TestSearchTasksWithMetadata:
    """Test build_search_tasks_with_metadata()."""

    def test_tasks_have_metadata(self):
        tasks = build_search_tasks_with_metadata(_discovery_vietnam())
        signal_tasks = [t for t in tasks if t.get("topic") == "consumer_signal"]
        assert len(signal_tasks) > 0
        for task in signal_tasks:
            assert "market" in task
            assert "language" in task
            assert "signal_type" in task

    def test_standard_queries_have_no_special_metadata(self):
        tasks = build_search_tasks_with_metadata(_discovery_vietnam())
        standard_tasks = [t for t in tasks if t.get("topic") != "consumer_signal"]
        assert len(standard_tasks) > 0


# ── Local Signal Samples Parser Tests ────────────────────────────────


class TestLocalSignalSamplesParser:
    """Test parse_local_signal_samples()."""

    def test_parse_valid_samples(self, tmp_path):
        samples_file = tmp_path / "local_signal_samples.jsonl"
        samples = [
            {
                "sample_id": "VN_001",
                "task_id": "LS_VN_001",
                "platform": "Shopee",
                "market": "Vietnam",
                "language": "vi",
                "collected_at": "2026-06-06T10:30:00+07:00",
                "access_level": "user_authorized",
                "sample_type": "screenshot_ocr",
                "contains_personal_data": False,
                "collector": "manual",
                "sample_size": 12,
                "text_excerpt": "Reviews mention price sensitivity.",
            },
        ]
        samples_file.write_text(
            "\n".join(json.dumps(s) for s in samples),
            encoding="utf-8",
        )

        result = parse_local_signal_samples(samples_file)
        assert len(result) == 1
        assert result[0]["metadata"]["source_family"] == "local_signal"
        assert result[0]["metadata"]["platform"] == "Shopee"

    def test_skip_personal_data(self, tmp_path):
        samples_file = tmp_path / "local_signal_samples.jsonl"
        samples = [
            {
                "sample_id": "VN_001",
                "task_id": "LS_VN_001",
                "platform": "Shopee",
                "market": "Vietnam",
                "language": "vi",
                "collected_at": "2026-06-06T10:30:00+07:00",
                "access_level": "user_authorized",
                "sample_type": "screenshot_ocr",
                "contains_personal_data": True,
                "collector": "manual",
            },
        ]
        samples_file.write_text(
            "\n".join(json.dumps(s) for s in samples),
            encoding="utf-8",
        )

        result = parse_local_signal_samples(samples_file)
        assert len(result) == 0

    def test_skip_missing_required_fields(self, tmp_path):
        samples_file = tmp_path / "local_signal_samples.jsonl"
        samples = [
            {"sample_id": "VN_001"},  # missing many required fields
        ]
        samples_file.write_text(
            "\n".join(json.dumps(s) for s in samples),
            encoding="utf-8",
        )

        result = parse_local_signal_samples(samples_file)
        assert len(result) == 0

    def test_missing_file_returns_empty(self, tmp_path):
        result = parse_local_signal_samples(tmp_path / "nonexistent.jsonl")
        assert result == []


# ── Local Signal Report Tests ────────────────────────────────────────


class TestLocalSignalReport:
    """Test generate_local_signal_report()."""

    def test_report_with_no_samples(self):
        discovery = _discovery_vietnam()
        tasks = build_local_signal_tasks(discovery)
        report = generate_local_signal_report(discovery, tasks, [])

        assert report["status"] == "no_samples"
        assert report["tasks_generated"] > 0
        assert len(report["data_gaps"]) > 0

    def test_report_with_samples(self):
        discovery = _discovery_vietnam()
        tasks = build_local_signal_tasks(discovery)
        samples = [
            {
                "title": "Local signal sample: Shopee Vietnam",
                "content": "Reviews mention price sensitivity.",
                "metadata": {
                    "collector_task_id": tasks[0].task_id,
                    "platform": "Shopee",
                    "market": "Vietnam",
                    "sample_size": 12,
                },
            },
        ]
        report = generate_local_signal_report(discovery, tasks, samples)

        assert report["status"] == "partial"
        assert len(report["signals_found"]) > 0
        assert report["signals_found"][0]["confidence"] == "low"

    def test_report_distinguishes_signal_types(self):
        discovery = _discovery_vietnam()
        tasks = build_local_signal_tasks(discovery)
        report = generate_local_signal_report(discovery, tasks, [])

        # All tasks should be consumer_discussion
        for gap in report["data_gaps"]:
            assert "consumer_discussion" in gap["missing_data_type"] or "consumer" in gap["missing_data_type"]

    def test_report_has_target_markets(self):
        discovery = _discovery_vietnam()
        tasks = build_local_signal_tasks(discovery)
        report = generate_local_signal_report(discovery, tasks, [])
        assert "Vietnam" in report["target_markets"]


# ── Audit Rule Tests ─────────────────────────────────────────────────


class TestLocalSignalAudit:
    """Test local signal audit rules."""

    def test_consumer_claim_without_consumer_source_fails(self):
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim, PipelineContext
        from multi_agent_brief.audit.deterministic import DeterministicAuditAgent

        ledger = ClaimLedger()
        claim = Claim(
            claim_id="TEST_001",
            statement="Consumers commonly complain about high prices.",
            source_id="NEWS_001",
            evidence_text="Industry report mentions price sensitivity.",
            source_type="web_search",
            claim_type="interpretation",
        )
        ledger.add_claim(claim)

        markdown = "Consumers commonly complain about high prices [src:TEST_001]."
        context = PipelineContext(
            project_name="test", input_dir="/tmp/in", output_dir="/tmp/out",
            report_date="2026-06-06",
            metadata={"source_discovery": {"local_signal_discovery": {"enabled": True}}},
        )
        agent = DeterministicAuditAgent()
        report = agent.run_audit(markdown, ledger, context)

        finding_types = [f.finding_type for f in report.findings]
        assert "local_signal_unsupported_claim" in finding_types

    def test_consumer_claim_with_consumer_source_passes(self):
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim, PipelineContext
        from multi_agent_brief.audit.deterministic import DeterministicAuditAgent

        ledger = ClaimLedger()
        claim = Claim(
            claim_id="TEST_001",
            statement="Consumers commonly complain about high prices.",
            source_id="LOCAL_001",
            evidence_text="Shopee reviews mention price sensitivity.",
            source_type="local_signal",
            claim_type="interpretation",
            metadata={
                "source_family": "local_signal",
                "platform": "Shopee",
                "market": "Vietnam",
                "collected_at": "2026-06-06",
                "access_level": "user_authorized",
                "sample_type": "screenshot_ocr",
                "collector": "manual",
            },
        )
        ledger.add_claim(claim)

        markdown = "Consumers commonly complain about high prices [src:TEST_001]."
        context = PipelineContext(
            project_name="test", input_dir="/tmp/in", output_dir="/tmp/out",
            report_date="2026-06-06",
            metadata={"source_discovery": {"local_signal_discovery": {"enabled": True}}},
        )
        agent = DeterministicAuditAgent()
        report = agent.run_audit(markdown, ledger, context)

        finding_types = [f.finding_type for f in report.findings]
        assert "local_signal_unsupported_claim" not in finding_types

    def test_personal_data_triggers_privacy_finding(self):
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim, PipelineContext
        from multi_agent_brief.audit.deterministic import DeterministicAuditAgent

        ledger = ClaimLedger()
        claim = Claim(
            claim_id="TEST_001",
            statement="User John said the product is bad.",
            source_id="LOCAL_001",
            evidence_text="User comment.",
            source_type="local_signal",
            metadata={
                "contains_personal_data": True,
                "source_family": "local_signal",
            },
        )
        ledger.add_claim(claim)

        markdown = "User feedback indicates issues [src:TEST_001]."
        context = PipelineContext(
            project_name="test", input_dir="/tmp/in", output_dir="/tmp/out",
            report_date="2026-06-06",
            metadata={"source_discovery": {"local_signal_discovery": {"enabled": True}}},
        )
        agent = DeterministicAuditAgent()
        report = agent.run_audit(markdown, ledger, context)

        finding_types = [f.finding_type for f in report.findings]
        assert "local_signal_privacy_violation" in finding_types

    def test_missing_provenance_triggers_finding(self):
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim, PipelineContext
        from multi_agent_brief.audit.deterministic import DeterministicAuditAgent

        ledger = ClaimLedger()
        claim = Claim(
            claim_id="TEST_001",
            statement="Reviews mention quality issues.",
            source_id="LOCAL_001",
            evidence_text="Sampled reviews.",
            source_type="local_signal",
            metadata={
                "source_family": "local_signal",
                # Missing: platform, market, collected_at, etc.
            },
        )
        ledger.add_claim(claim)

        markdown = "Reviews mention quality issues [src:TEST_001]."
        context = PipelineContext(
            project_name="test", input_dir="/tmp/in", output_dir="/tmp/out",
            report_date="2026-06-06",
            metadata={"source_discovery": {"local_signal_discovery": {"enabled": True}}},
        )
        agent = DeterministicAuditAgent()
        report = agent.run_audit(markdown, ledger, context)

        finding_types = [f.finding_type for f in report.findings]
        assert "local_signal_missing_provenance" in finding_types


# ── Reference Workflow Regression ────────────────────────────────────


class TestReferenceWorkflowRegression:
    """Ensure existing reference workflow still passes."""

    def test_disabled_local_signal_no_side_effects(self):
        """When local_signal_discovery is disabled, no new artifacts appear."""
        discovery = _discovery_disabled()
        tasks = build_local_signal_tasks(discovery)
        assert tasks == []

        candidates = generate_source_candidates(discovery)
        assert "local_social_listening_tasks" not in candidates

        collector = generate_collector_tasks(discovery)
        assert collector["status"] == "no_tasks"

    def test_standard_queries_unchanged(self):
        """Standard queries should still be generated."""
        discovery = _discovery_disabled()
        queries = build_search_queries(discovery)
        assert len(queries) > 0
        assert any("industry news" in q for q in queries)
