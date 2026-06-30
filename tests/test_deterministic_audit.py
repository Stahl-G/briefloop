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


def test_audit_passes_hyphenated_claim_reference():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="CLM-001",
                statement="The company announced a 2 GW capacity expansion.",
                source_id="SRC",
                evidence_text="The company announced a 2 GW capacity expansion.",
            )
        ]
    )
    markdown = "- The company announced a 2 GW capacity expansion. [src:CLM-001]"

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


def test_audit_ignores_numbers_in_source_reference_section():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="CL-001",
                statement="A cited body fact appears before the source section.",
                source_id="SRC",
                evidence_text="A cited body fact appears before the source section.",
            )
        ]
    )
    markdown = """
- A cited body fact appears before the source section. [src:CL-001]

## **数据来源**

- PV Tech: *Meta and RWE ink 298MW Texas solar PPA*
- PV Tech: *Cypress Creek secures US$3.5 billion to fund 1.63GW/1.9GWh...*
""".strip()

    report = run_deterministic_audit(markdown, ledger)

    assert not [finding for finding in report.findings if finding.finding_type == "number_without_source"]


def test_audit_resumes_number_scan_after_source_reference_section():
    markdown = """
## 数据来源

- PV Tech: *Meta and RWE ink 298MW Texas solar PPA*

## Market Takeaways

- The benchmark price was $140 per kWh.
""".strip()

    report = run_deterministic_audit(markdown, ClaimLedger())

    assert report.audit_status == "warning"
    assert any(finding.finding_type == "number_without_source" for finding in report.findings)


def test_audit_does_not_skip_business_section_with_sources_word():
    markdown = """
## Sources of Demand

- Demand sources represented 298MW of announced capacity.
""".strip()

    report = run_deterministic_audit(markdown, ClaimLedger())

    assert any(finding.finding_type == "number_without_source" for finding in report.findings)


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


def test_audit_flags_windows_absolute_path_redaction_risk():
    report = run_deterministic_audit("Local file: C:\\Users\\analyst\\private\\brief.md", ClaimLedger())

    assert report.audit_status == "fail"
    assert any(f.finding_type == "redaction_risk" for f in report.findings)
