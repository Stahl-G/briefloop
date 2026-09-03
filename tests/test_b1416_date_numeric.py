"""Tests for PR6: Date, time window, and numeric config validation (B14, B15, B16).

B14 — Source recency filtering must use report_date, not system time.
B15 — Web search claims missing published_at must generate audit findings.
B16 — max_claims=0 and max_source_age_days=0 must not be swallowed by truthiness.
"""

from __future__ import annotations

from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


# ─── B14: Recency filtering uses report_date ───


class TestB14RecencyByReportDate:
    """filter_by_recency must accept and use report_date, not system time."""

    def test_auditor_uses_report_date_for_staleness(self):
        """Auditor's deterministic audit uses report_date for stale checks."""
        ledger = ClaimLedger()
        ledger.add_claim(
            Claim(
                claim_id="TEST_A",
                statement="Recent claim",
                source_id="SRC",
                evidence_text="test",
                metadata={"published_at": "2026-06-01"},
            )
        )
        report = run_deterministic_audit(
            "# Brief\n- Text [src:TEST_A]\n",
            ledger,
            report_date="2026-06-02",
            max_source_age_days=7,
            fail_on_stale_source=True,
        )
        # June 1 is 1 day before June 2 — not stale
        stale_findings = [
            f for f in report.findings if f.finding_type == "stale_source"
        ]
        assert len(stale_findings) == 0, "June 1 source should not be stale"


# ─── Frozen report window is the first freshness authority ───


class TestReportWindowAuthority:
    """report_window_start/end frozen in RunDirection outrank report_date derivation."""

    def test_missing_source_date_blocks_under_strict(self):
        from multi_agent_brief.quality_gates.evaluation import _freshness_findings

        ledger = ClaimLedger()
        ledger.add_claim(
            Claim(
                claim_id="NODATE",
                statement="Claim without date",
                source_id="SRC",
                evidence_text="test",
                source_type="local_file",
                metadata={"published_at": ""},
            )
        )
        findings = _freshness_findings(
            markdown="# Brief\n- Text [src:NODATE]\n",
            ledger=ledger,
            report_date="2026-08-10",
            max_source_age_days=7,
            strict=True,
            stages=[],
            artifacts=[],
            report_window_start="2026-08-03",
        )
        date_findings = [
            f for f in findings if f["finding_type"] == "missing_source_date"
        ]
        assert len(date_findings) == 1
        assert date_findings[0]["blocking_level"] == "blocking"


# ─── typed background sources never use retrieved_at as event time ───


class TestBackgroundSourceTemporality:
    """Background context is visible but cannot establish a weekly event."""

    def test_retrieved_at_does_not_promote_legacy_undated_source(self):
        ledger = ClaimLedger()
        ledger.add_claim(
            Claim(
                claim_id="RETR_ONLY",
                statement="Undated web page claim",
                source_id="SRC",
                evidence_text="test",
                source_type="web_search",
                metadata={"published_at": "", "retrieved_at": "2026-08-09T10:00:00Z"},
            )
        )
        report = run_deterministic_audit(
            "# Brief\n- Text [src:RETR_ONLY]\n",
            ledger,
            report_date="2026-08-10",
            max_source_age_days=7,
            report_window_start="2026-08-03",
        )
        types = [f.finding_type for f in report.findings]
        assert "missing_source_date" in types
        assert "stale_source" not in types
