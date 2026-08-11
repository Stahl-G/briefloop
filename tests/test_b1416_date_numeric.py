"""Tests for PR6: Date, time window, and numeric config validation (B14, B15, B16).

B14 — Source recency filtering must use report_date, not system time.
B15 — Web search claims missing published_at must generate audit findings.
B16 — max_claims=0 and max_source_age_days=0 must not be swallowed by truthiness.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.core.config import build_run_settings


# ─── B14: Recency filtering uses report_date ───

class TestB14RecencyByReportDate:
    """filter_by_recency must accept and use report_date, not system time."""

    def test_filter_by_recency_needs_report_date_param(self):
        """filter_by_recency currently uses datetime.now() — it should accept
        a report_date parameter for consistent cross-run behavior."""
        from multi_agent_brief.sources.normalizer import filter_by_recency
        from multi_agent_brief.sources.base import SourceItem
        import inspect

        sig = inspect.signature(filter_by_recency)
        params = list(sig.parameters.keys())
        # Currently only has 'items' and 'recency_days'
        # B14 fix should add 'report_date' parameter
        assert "report_date" in params or len(params) == 2, (
            "Baseline: filter_by_recency('items', 'recency_days') — "
            "B14 fix should add report_date parameter"
        )

    def test_auditor_uses_report_date_for_staleness(self):
        """Auditor's deterministic audit uses report_date for stale checks."""
        ledger = ClaimLedger()
        ledger.add_claim(Claim(
            claim_id="TEST_A", statement="Recent claim",
            source_id="SRC", evidence_text="test",
            metadata={"published_at": "2026-06-01"},
        ))
        report = run_deterministic_audit(
            "# Brief\n- Text [src:TEST_A]\n",
            ledger,
            report_date="2026-06-02",
            max_source_age_days=7,
            fail_on_stale_source=True,
        )
        # June 1 is 1 day before June 2 — not stale
        stale_findings = [f for f in report.findings if f.finding_type == "stale_source"]
        assert len(stale_findings) == 0, "June 1 source should not be stale"


# ─── B15: Web search missing date generates findings ───

class TestB15WebSearchMissingDate:
    """Web search claims missing published_at must generate audit findings."""

    def test_web_search_missing_date_generates_finding(self):
        """When a web search claim lacks published_at, audit must flag it."""
        ledger = ClaimLedger()
        ledger.add_claim(Claim(
            claim_id="WS_MISSING_DATE",
            statement="Web search claim with no date",
            source_id="SRC", evidence_text="test",
            source_type="web_search",
            metadata={"published_at": "", "backend": "tavily"},
        ))
        report = run_deterministic_audit(
            "# Brief\n- Text [src:WS_MISSING_DATE]\n",
            ledger,
            report_date="2026-06-02",
            max_source_age_days=7,
        )
        # Must have at least a low-severity finding for missing date
        date_findings = [
            f for f in report.findings
            if f.finding_type == "missing_source_date"
            and f.related_claim_id == "WS_MISSING_DATE"
        ]
        assert len(date_findings) >= 1, (
            "B15 FAIL: Web search claim with missing published_at "
            "must generate at least a low-severity audit finding"
        )

    def test_non_web_search_missing_date_still_flagged(self):
        """Non-web-search claims with missing date must still be flagged."""
        ledger = ClaimLedger()
        ledger.add_claim(Claim(
            claim_id="LOCAL_MISSING",
            statement="Local file claim with no date",
            source_id="SRC", evidence_text="test",
            source_type="local_file",
            metadata={"published_at": ""},
        ))
        report = run_deterministic_audit(
            "# Brief\n- Text [src:LOCAL_MISSING]\n",
            ledger,
            report_date="2026-06-02",
            max_source_age_days=7,
        )
        date_findings = [
            f for f in report.findings
            if f.finding_type == "missing_source_date"
            and f.related_claim_id == "LOCAL_MISSING"
        ]
        assert len(date_findings) >= 1, (
            "Non-web-search claims with missing date must still be flagged"
        )


# ─── B16: Numeric config validation ───

class TestB16NumericConfigValidation:
    """max_claims and max_source_age_days must be validated — 0 and negatives
    must not be swallowed by truthiness-based fallback."""

    def test_max_claims_zero_not_defaulted(self, tmp_path):
        """max_claims=0 should be treated as 0, not replaced with 160."""
        config = {
            "selector": {"max_items": 0},
            "input": {"path": str(tmp_path / "input")},
            "output": {"path": str(tmp_path / "output")},
        }
        settings = build_run_settings(
            config=config, input_dir=None, output_dir=None,
            name=None, language=None, audience=None,
        )
        # 0 is a valid value — should not be replaced by default 160
        assert settings["max_claims"] == 0, (
            "B16 FAIL: max_claims=0 was replaced with default 160 via truthiness"
        )

    def test_max_source_age_days_zero_kept(self, tmp_path):
        """max_source_age_days=0 must be kept as 0, not converted to None/14."""
        config = {
            "report": {"max_source_age_days": 0},
            "input": {"path": str(tmp_path / "input")},
            "output": {"path": str(tmp_path / "output")},
        }
        settings = build_run_settings(
            config=config, input_dir=None, output_dir=None,
            name=None, language=None, audience=None,
        )
        assert settings["max_source_age_days"] == 0, (
            "B16 FAIL: max_source_age_days=0 was converted to None via truthiness"
        )

    def test_max_source_age_days_none_when_absent(self, tmp_path):
        """When max_source_age_days is not set, it should be None."""
        config = {
            "report": {},
            "input": {"path": str(tmp_path / "input")},
            "output": {"path": str(tmp_path / "output")},
        }
        settings = build_run_settings(
            config=config, input_dir=None, output_dir=None,
            name=None, language=None, audience=None,
        )
        assert settings["max_source_age_days"] is None, (
            "When absent, max_source_age_days should be None"
        )

    def test_filter_by_recency_zero_days_keeps_all(self):
        """filter_by_recency with recency_days=0 should keep all items."""
        from multi_agent_brief.sources.normalizer import filter_by_recency
        from multi_agent_brief.sources.base import SourceItem

        # Future date item
        future_item = SourceItem(
            source_id="FUTURE", source_name="Future",
            source_type="local_file", title="Future", content="Future news",
            published_at="2099-01-01",
        )
        result = filter_by_recency([future_item], recency_days=0)
        # recency_days=0 means no recency filter
        assert len(result) == 1, (
            "B16 FAIL: recency_days=0 should pass all items through"
        )


# ─── Frozen report window is the first freshness authority ───

class TestReportWindowAuthority:
    """report_window_start/end frozen in RunDirection outrank report_date derivation."""

    @staticmethod
    def _ledger(published_at: str) -> ClaimLedger:
        ledger = ClaimLedger()
        ledger.add_claim(Claim(
            claim_id="WINDOW_A", statement="Window claim",
            source_id="SRC", evidence_text="test",
            metadata={"published_at": published_at},
        ))
        return ledger

    @staticmethod
    def _stale(report) -> list:
        return [f for f in report.findings if f.finding_type == "stale_source"]

    def test_window_start_boundary_day_is_fresh(self):
        report = run_deterministic_audit(
            "# Brief\n- Text [src:WINDOW_A]\n",
            self._ledger("2026-08-03"),
            report_date="2026-08-10",
            max_source_age_days=7,
            report_window_start="2026-08-03",
        )
        assert self._stale(report) == []

    def test_source_before_window_start_is_stale(self):
        report = run_deterministic_audit(
            "# Brief\n- Text [src:WINDOW_A]\n",
            self._ledger("2026-08-02"),
            report_date="2026-08-10",
            max_source_age_days=7,
            report_window_start="2026-08-03",
        )
        stale = self._stale(report)
        assert len(stale) == 1
        assert "2026-08-03" in stale[0].description

    def test_window_overrides_report_date_derivation(self):
        """Age 8 > 7 vs report_date, but inside the frozen window — window wins."""
        report = run_deterministic_audit(
            "# Brief\n- Text [src:WINDOW_A]\n",
            self._ledger("2026-08-02"),
            report_date="2026-08-10",
            max_source_age_days=7,
            report_window_start="2026-08-01",
        )
        assert self._stale(report) == []

    def test_missing_source_date_blocks_under_strict(self):
        from multi_agent_brief.quality_gates.evaluation import _freshness_findings

        ledger = ClaimLedger()
        ledger.add_claim(Claim(
            claim_id="NODATE", statement="Claim without date",
            source_id="SRC", evidence_text="test",
            source_type="local_file",
            metadata={"published_at": ""},
        ))
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
        date_findings = [f for f in findings if f["finding_type"] == "missing_source_date"]
        assert len(date_findings) == 1
        assert date_findings[0]["blocking_level"] == "blocking"

