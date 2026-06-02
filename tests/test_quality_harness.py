from multi_agent_brief.audit.harness import QualityHarnessAuditAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def test_quality_harness_flags_placeholder_text():
    report = QualityHarnessAuditAgent().run_audit("TBD: source to be completed", ClaimLedger())

    assert report.findings
    assert report.findings[0].finding_type == "placeholder_text"


def test_quality_harness_blocks_needs_recrawl_claims():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="BAD_ABCDEF",
                statement="This source failed collection and needs recrawl.",
                source_id="BAD",
                evidence_text="Collection failed.",
                claim_type="needs_recrawl",
            )
        ]
    )

    report = QualityHarnessAuditAgent().run_audit("- Failed source used. [src:BAD_ABCDEF]", ledger)

    assert any(f.finding_type == "needs_recrawl_claim_used" for f in report.findings)


def test_quality_harness_blocks_t5_sources():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="LOW_ABCDEF",
                statement="Low-confidence source should not be used.",
                source_id="LOW",
                evidence_text="Low-confidence source should not be used.",
                metadata={"source_tier": "T5"},
            )
        ]
    )

    report = QualityHarnessAuditAgent().run_audit("- Low-confidence source should not be used. [src:LOW_ABCDEF]", ledger)

    assert any(f.finding_type == "low_confidence_source_used" for f in report.findings)

