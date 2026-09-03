"""Tests for Local Signal Discovery (v0.5.1).

Covers:
- local_signal_samples.jsonl parsing
- audit: LOCAL_SIGNAL_CLAIM_001, LOCAL_SIGNAL_PROVENANCE_001, LOCAL_SIGNAL_PRIVACY_001
"""
from __future__ import annotations

import json

from multi_agent_brief.sources.local_signal_planner import (
    parse_local_signal_samples,
)


# ── Local Signal Samples Parser Tests ────────────────────────────────


class TestLocalSignalSamplesParser:
    """Test parse_local_signal_samples()."""

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
