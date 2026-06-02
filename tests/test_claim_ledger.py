from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def test_claim_ledger_add_get_and_validate():
    ledger = ClaimLedger()
    claim = Claim(
        claim_id="TEST_123456",
        statement="Revenue increased 10%.",
        source_id="SOURCE_A",
        evidence_text="Revenue increased 10% in the synthetic source.",
    )

    ledger.add_claim(claim)

    assert ledger.get_claim("TEST_123456") == claim
    assert ledger.validate_claims() == []


def test_claim_ledger_detects_missing_sources():
    ledger = ClaimLedger(
        [
            Claim(
                claim_id="TEST_123456",
                statement="Revenue increased 10%.",
                source_id="",
                evidence_text="",
            )
        ]
    )

    assert len(ledger.detect_missing_sources()) == 1
    assert ledger.validate_claims()

