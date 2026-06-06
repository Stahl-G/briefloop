"""Tests for Limitation Hygiene audit (v0.5.3 PR 4)."""
from __future__ import annotations

import pytest

from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.audit.limitation_hygiene import (
    LimitationHygieneReport,
    audit_limitation_hygiene,
    format_limitation_hygiene_report,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def _make_claim(
    claim_id: str,
    *,
    limitations: list[str] | None = None,
    epistemic_type: str = "observed",
    evidence_relation: str = "direct",
    metadata: dict | None = None,
    topic: str = "market",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=f"Statement for {claim_id}",
        source_id="SRC_TEST",
        evidence_text="evidence",
        epistemic_type=epistemic_type,
        evidence_relation=evidence_relation,
        limitations=limitations or [],
        metadata={"topic": topic, **(metadata or {})},
    )


# ── Rule 1: repeated limitations ──────────────────────────────────


class TestRepeatedLimitations:
    def test_same_limitation_3_times_triggers_warning(self):
        claims = [
            _make_claim(f"L{i}", limitations=["Data source is outdated"]) for i in range(4)
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert any(f.finding_type == "repeated_limitation" for f in report.findings)

    def test_same_limitation_2_times_passes(self):
        claims = [
            _make_claim(f"L{i}", limitations=["Data source is outdated"]) for i in range(2)
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert not any(f.finding_type == "repeated_limitation" for f in report.findings)

    def test_different_limitations_pass(self):
        claims = [
            _make_claim("L10", limitations=["Source A outdated"]),
            _make_claim("L11", limitations=["Source B missing"]),
            _make_claim("L12", limitations=["Source C incomplete"]),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert not any(f.finding_type == "repeated_limitation" for f in report.findings)


# ── Rule 2: missing verification_path ─────────────────────────────


class TestMissingVerificationPath:
    def test_substantive_limitation_without_path_triggers_warning(self):
        claim = _make_claim("V001", limitations=["Revenue data is estimated, not audited"])
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert any(f.finding_type == "missing_verification_path" for f in report.findings)

    def test_limitation_with_path_passes(self):
        claim = _make_claim("V002", limitations=["Revenue data is estimated"])
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        blocks[0].verification_path = "Check audited Q3 financials"
        report = audit_limitation_hygiene(blocks, ledger)
        assert not any(f.finding_type == "missing_verification_path" for f in report.findings)

    def test_no_limitations_passes(self):
        claim = _make_claim("V003")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert not any(f.finding_type == "missing_verification_path" for f in report.findings)

    def test_only_boilerplate_limitations_pass(self):
        """Boilerplate-only limitations should not trigger missing verification_path."""
        claim = _make_claim("V004", limitations=["For reference only"])
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert not any(f.finding_type == "missing_verification_path" for f in report.findings)


# ── Rule 3: boilerplate consolidation ─────────────────────────────


class TestBoilerplateConsolidation:
    def test_multiple_boilerplate_triggers_warning(self):
        claims = [
            _make_claim("B001", limitations=["For reference only"]),
            _make_claim("B002", limitations=["Not local data"]),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert any(f.finding_type == "boilerplate_limitation" for f in report.findings)

    def test_single_boilerplate_passes(self):
        claims = [
            _make_claim("B003", limitations=["For reference only"]),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert not any(f.finding_type == "boilerplate_limitation" for f in report.findings)

    def test_chinese_boilerplate_detected(self):
        claims = [
            _make_claim("B004", limitations=["仅供参考"]),
            _make_claim("B005", limitations=["不构成投资建议"]),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert any(f.finding_type == "boilerplate_limitation" for f in report.findings)


# ── Report stats ──────────────────────────────────────────────────


class TestReportStats:
    def test_report_counts(self):
        claims = [
            _make_claim("S001", limitations=["Limit A", "For reference only"]),
            _make_claim("S002", limitations=["Limit A", "Not local data"]),
            _make_claim("S003", limitations=["Limit A"]),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        assert report.total_limitations == 5
        assert report.unique_limitations >= 3  # "limit a", "for reference only", "not local data"
        assert report.boilerplate_count == 2

    def test_empty_report(self):
        report = audit_limitation_hygiene([], ClaimLedger())
        assert report.total_limitations == 0
        assert len(report.findings) == 0


# ── Report formatting ─────────────────────────────────────────────


class TestReportFormat:
    def test_format_empty(self):
        report = LimitationHygieneReport()
        text = format_limitation_hygiene_report(report)
        assert "All limitation hygiene checks passed" in text

    def test_format_with_findings(self):
        claims = [
            _make_claim("F001", limitations=["For reference only"]),
            _make_claim("F002", limitations=["Not local data"]),
            _make_claim("F003", limitations=["Not local data"]),
            _make_claim("F004", limitations=["Not local data"]),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)
        text = format_limitation_hygiene_report(report)
        assert "WARNING" in text
        assert "Total limitations:" in text


# ── Integration with builder ──────────────────────────────────────


class TestBuilderIntegration:
    def test_full_pipeline(self):
        """Build blocks then audit limitation hygiene."""
        claims = [
            _make_claim("G001", limitations=["Data is estimated"], topic="earnings"),
            _make_claim("G002", limitations=["For reference only"], topic="earnings"),
            _make_claim("G003", limitations=["Not local data"], topic="earnings"),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        report = audit_limitation_hygiene(blocks, ledger)

        # Should have boilerplate warning (2 boilerplate)
        assert any(f.finding_type == "boilerplate_limitation" for f in report.findings)
        # Should have missing verification_path (substantive limitation "data is estimated")
        assert any(f.finding_type == "missing_verification_path" for f in report.findings)
