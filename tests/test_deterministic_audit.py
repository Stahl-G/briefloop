from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def test_audit_passes_valid_reference():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="SRC_ABCDEF",
                statement="The company announced a 2 GW capacity expansion.",
                source_id="SRC",
                evidence_text="The company announced a 2 GW capacity expansion.",
            )
        ]
    )
    markdown = "- The company announced a 2 GW capacity expansion. [src:SRC_ABCDEF]"

    report = run_deterministic_audit(markdown, ledger)

    assert report.audit_status == "pass"
    assert report.findings == []


def test_audit_flags_orphan_reference():
    ledger = ClaimLedger()
    markdown = "- A claim appears here. [src:SRC_MISSING]"

    report = run_deterministic_audit(markdown, ledger)

    assert report.audit_status == "fail"
    assert report.findings[0].finding_type == "missing_claim"


def test_audit_flags_number_without_source():
    ledger = ClaimLedger()
    markdown = "- The benchmark price was $140 per kWh."

    report = run_deterministic_audit(markdown, ledger)

    assert report.audit_status == "warning"
    assert report.findings[0].finding_type == "number_without_source"


def test_audit_fails_stale_source_when_reporting_window_is_strict():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="OLD_ABCDEF",
                statement="A three-month-old source should not appear as a weekly item.",
                source_id="OLD",
                evidence_text="A three-month-old source should not appear as a weekly item.",
                metadata={"published_at": "2026-03-01"},
            )
        ]
    )
    markdown = "- A three-month-old source should not appear as a weekly item. [src:OLD_ABCDEF]"

    report = run_deterministic_audit(
        markdown,
        ledger,
        report_date="2026-06-02",
        max_source_age_days=14,
        fail_on_stale_source=True,
    )

    assert report.audit_status == "fail"
    assert report.findings[0].finding_type == "stale_source"
