from multi_agent_brief.audit.final_quality import FinalQualityAuditAgent, FinalQualityConfig
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim, PipelineContext


def test_final_quality_blocks_thin_normal_report():
    report = FinalQualityAuditAgent(FinalQualityConfig(min_markdown_chars=100)).run_audit(
        "# Brief\n\nToo short.",
        ClaimLedger(),
    )

    assert report.audit_status == "fail"
    assert any(f.finding_type == "final_report_too_thin" for f in report.findings)
    assert report.metadata["harness_protocol"] == "BRIEF_HARNESS_V2"


def test_final_quality_allows_quiet_week_depth_exception():
    report = FinalQualityAuditAgent(FinalQualityConfig(min_markdown_chars=100, quiet_week=True)).run_audit(
        "# Brief\n\nToo short.",
        ClaimLedger(),
    )

    assert not any(f.finding_type == "final_report_too_thin" for f in report.findings)


def test_final_quality_checks_summary_bullet_count():
    markdown = """# Brief

## Executive Summary

▸ One
▸ Two
"""

    report = FinalQualityAuditAgent(
        FinalQualityConfig(min_markdown_chars=0, expected_summary_bullets=5, required_metadata_labels=[])
    ).run_audit(markdown, ClaimLedger())

    assert any(f.finding_type == "summary_bullet_count_mismatch" for f in report.findings)


def test_final_quality_blocks_wide_markdown_tables():
    markdown = """# Brief

## Executive Summary

| A | B | C | D | E |
| - | - | - | - | - |
"""

    report = FinalQualityAuditAgent(
        FinalQualityConfig(min_markdown_chars=0, expected_summary_bullets=None, required_metadata_labels=[])
    ).run_audit(markdown, ClaimLedger())

    assert any(f.finding_type == "wide_markdown_table" for f in report.findings)


def test_final_quality_flags_stale_date_framed_as_current():
    context = PipelineContext(
        project_name="Demo",
        input_dir="input",
        output_dir="output",
        report_date="2026-06-02",
        max_source_age_days=14,
    )
    markdown = "This week, the 2026-04-01 policy update remains the latest development."

    report = FinalQualityAuditAgent(
        FinalQualityConfig(min_markdown_chars=0, expected_summary_bullets=None, required_metadata_labels=[])
    ).run_audit(markdown, ClaimLedger(), context)

    assert any(f.finding_type == "stale_date_framed_as_current" for f in report.findings)


def test_final_quality_requires_docx_when_configured(tmp_path):
    missing_docx = tmp_path / "missing.docx"

    report = FinalQualityAuditAgent(
        FinalQualityConfig(
            min_markdown_chars=0,
            expected_summary_bullets=None,
            required_metadata_labels=[],
            rendered_docx_path=str(missing_docx),
        )
    ).run_audit("# Brief", ClaimLedger())

    assert any(f.finding_type == "missing_rendered_docx" for f in report.findings)


# --- Quality threshold tests ---


def _make_ledger_with_claims(count: int, *, with_dates: bool = True) -> ClaimLedger:
    """Create a ClaimLedger with N claims for testing."""
    ledger = ClaimLedger()
    for i in range(count):
        metadata = {}
        if with_dates:
            metadata["published_at"] = f"2026-06-{i+1:02d}"
        claim = Claim(
            claim_id=f"TEST{i:04d}ABCD",
            statement=f"Test claim {i}",
            source_id=f"src_{i}",
            evidence_text=f"Evidence for claim {i}",
            metadata=metadata,
        )
        ledger.add_claim(claim)
    return ledger


def test_insufficient_claims_detected():
    """Weekly brief with < 20 cited claims should fail."""
    ledger = _make_ledger_with_claims(10)
    # Build markdown that cites only 10 claims
    cited_lines = "\n".join(
        f"- Claim {i} [src:TEST{i:04d}ABCD]" for i in range(10)
    )
    markdown = f"# Brief\n\n## Executive Summary\n\n{cited_lines}\n"

    config = FinalQualityConfig(
        min_markdown_chars=0,
        expected_summary_bullets=None,
        required_metadata_labels=[],
        min_selected_claims=20,
    )
    report = FinalQualityAuditAgent(config).run_audit(markdown, ledger)

    assert report.audit_status == "fail"
    assert any(f.finding_type == "insufficient_claims" for f in report.findings)


def test_sufficient_claims_passes():
    """Brief with >= 20 cited claims should pass claim count check."""
    ledger = _make_ledger_with_claims(25)
    cited_lines = "\n".join(
        f"- Claim {i} [src:TEST{i:04d}ABCD]" for i in range(25)
    )
    markdown = f"# Brief\n\n## Executive Summary\n\n{cited_lines}\n"

    config = FinalQualityConfig(
        min_markdown_chars=0,
        expected_summary_bullets=None,
        required_metadata_labels=[],
        min_selected_claims=20,
    )
    report = FinalQualityAuditAgent(config).run_audit(markdown, ledger)

    assert not any(f.finding_type == "insufficient_claims" for f in report.findings)


def test_quiet_week_exception_for_claims():
    """quiet_week should relax claim count requirement when allow_quiet_week_exception is set."""
    ledger = _make_ledger_with_claims(5)
    cited_lines = "\n".join(
        f"- Claim {i} [src:TEST{i:04d}ABCD]" for i in range(5)
    )
    markdown = f"# Brief\n\n## Executive Summary\n\n{cited_lines}\n"

    config = FinalQualityConfig(
        min_markdown_chars=0,
        expected_summary_bullets=None,
        required_metadata_labels=[],
        min_selected_claims=20,
        quiet_week=True,
        allow_quiet_week_exception=True,
    )
    report = FinalQualityAuditAgent(config).run_audit(markdown, ledger)

    assert not any(f.finding_type == "insufficient_claims" for f in report.findings)


def test_brief_too_thin_zh_detected():
    """Brief with too few Chinese characters should fail."""
    # Very short Chinese text
    markdown = "# 简报\n\n## 执行摘要\n\n- 短文本\n"

    config = FinalQualityConfig(
        min_markdown_chars=0,
        expected_summary_bullets=None,
        required_metadata_labels=[],
        min_zh_chars=3000,
    )
    report = FinalQualityAuditAgent(config).run_audit(markdown, ClaimLedger())

    assert report.audit_status == "fail"
    assert any(f.finding_type == "brief_too_thin_zh" for f in report.findings)


def test_missing_date_claim_detected():
    """Claims without dates should fail when require_dates is True."""
    ledger = _make_ledger_with_claims(3, with_dates=False)
    cited_lines = "\n".join(
        f"- Claim {i} [src:TEST{i:04d}ABCD]" for i in range(3)
    )
    markdown = f"# Brief\n\n## Executive Summary\n\n{cited_lines}\n"

    config = FinalQualityConfig(
        min_markdown_chars=0,
        expected_summary_bullets=None,
        required_metadata_labels=[],
        require_dates=True,
    )
    report = FinalQualityAuditAgent(config).run_audit(markdown, ledger)

    assert report.audit_status == "fail"
    date_findings = [f for f in report.findings if f.finding_type == "missing_date"]
    assert len(date_findings) == 3


def test_dated_claims_pass_date_check():
    """Claims with dates should pass when require_dates is True."""
    ledger = _make_ledger_with_claims(3, with_dates=True)
    cited_lines = "\n".join(
        f"- Claim {i} [src:TEST{i:04d}ABCD]" for i in range(3)
    )
    markdown = f"# Brief\n\n## Executive Summary\n\n{cited_lines}\n"

    config = FinalQualityConfig(
        min_markdown_chars=0,
        expected_summary_bullets=None,
        required_metadata_labels=[],
        require_dates=True,
    )
    report = FinalQualityAuditAgent(config).run_audit(markdown, ledger)

    assert not any(f.finding_type == "missing_date" for f in report.findings)
