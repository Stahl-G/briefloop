"""Tests for Limitation Hygiene audit (v0.5.3 PR 4)."""
from __future__ import annotations

from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.audit.limitation_hygiene import audit_limitation_hygiene
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


def test_same_limitation_3_times_triggers_warning():
    claims = [
        _make_claim(f"L{i}", limitations=["Data source is outdated"]) for i in range(4)
    ]
    ledger = ClaimLedger(claims)
    blocks = build_analysis_blocks(ledger)
    report = audit_limitation_hygiene(blocks, ledger)
    assert any(f.finding_type == "repeated_limitation" for f in report.findings)


def test_substantive_limitation_without_path_triggers_warning():
    claim = _make_claim("V001", limitations=["Revenue data is estimated, not audited"])
    ledger = ClaimLedger([claim])
    blocks = build_analysis_blocks(ledger)
    report = audit_limitation_hygiene(blocks, ledger)
    assert any(f.finding_type == "missing_verification_path" for f in report.findings)
