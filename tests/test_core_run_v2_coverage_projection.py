from __future__ import annotations

from multi_agent_brief.contracts.v2 import (
    CandidateClaimsProposal,
    ScreenedCandidatesProposal,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.core_run_v2.gates import _coverage_projection
from multi_agent_brief.quality_gates.evaluation import _coverage_omission_findings

RUN_ID = "RUN-COVERAGE-001"
NOW = "2026-08-10T12:00:00Z"


def _candidates() -> CandidateClaimsProposal:
    return CandidateClaimsProposal.model_validate(
        {
            "schema_version": "briefloop.candidate_claims_proposal.v2",
            "proposal_id": "PROP-CANDIDATE-001",
            "run_id": RUN_ID,
            "created_at": NOW,
            "candidates": [
                {
                    "candidate_id": "CAND-001",
                    "source_id": "SRC-001",
                    "statement": "ExampleCo commissioned a 2 GW cell line.",
                    "evidence_text": "ExampleCo commissioned a 2 GW cell line in July.",
                    "topic": "capacity",
                    "claim_type": "fact",
                    "confidence": "high",
                },
                {
                    "candidate_id": "CAND-002",
                    "source_id": "SRC-002",
                    "statement": "ExampleCo filed a registration statement.",
                    "evidence_text": "ExampleCo filed a registration statement.",
                    "topic": "governance",
                    "claim_type": "fact",
                    "confidence": "medium",
                },
            ],
        }
    )


def _screened(priority_cand_002: str = "high") -> ScreenedCandidatesProposal:
    return ScreenedCandidatesProposal.model_validate(
        {
            "schema_version": "briefloop.screened_candidates_proposal.v2",
            "proposal_id": "PROP-SCREENED-001",
            "run_id": RUN_ID,
            "candidate_claims_proposal_id": "PROP-CANDIDATE-001",
            "created_at": NOW,
            "decisions": [
                {"candidate_id": "CAND-001", "decision": "selected", "priority": "high"},
                {
                    "candidate_id": "CAND-002",
                    "decision": "selected",
                    "priority": priority_cand_002,
                },
            ],
        }
    )


def _ledger(*claims: Claim) -> ClaimLedger:
    return ClaimLedger(list(claims))


def _claim(claim_id: str = "CLAIM-001", source_id: str = "SRC-001") -> Claim:
    return Claim.from_dict(
        {
            "claim_id": claim_id,
            "statement": "ExampleCo commissioned a 2 GW cell line.",
            "source_id": source_id,
            "evidence_text": "ExampleCo commissioned a 2 GW cell line in July.",
        }
    )


def _findings(projection: dict, *, strict: bool = True) -> list[dict]:
    return _coverage_omission_findings(
        projection=projection,
        strict=strict,
        stages=[{"stage_id": "scout"}, {"stage_id": "claim-ledger"}, {"stage_id": "editor"}],
        artifacts=[
            {"artifact_id": "screened_candidates"},
            {"artifact_id": "claim_ledger"},
            {"artifact_id": "audited_brief"},
        ],
        reader_facing_mode=False,
    )


def test_high_priority_missing_from_ledger_recorded() -> None:
    projection = _coverage_projection(
        _candidates(),
        _screened(),
        _ledger(),
        "Brief body without coverage.",
    )
    assert [item["candidate_id"] for item in projection["missing_from_ledger"]] == [
        "CAND-001",
        "CAND-002",
    ]
    assert projection["missing_from_brief"] == []
    findings = _findings(projection)
    assert findings[0]["finding_type"] == "selected_candidate_missing_from_ledger"
    assert findings[0]["blocking_level"] == "blocking"
    assert "screening revision" in findings[0]["recommendation"]


def test_high_priority_in_ledger_but_not_cited_recorded() -> None:
    projection = _coverage_projection(
        _candidates(),
        _screened(),
        _ledger(_claim()),
        "Brief body without any citation marker.",
    )
    assert [item["candidate_id"] for item in projection["missing_from_brief"]] == [
        "CAND-001"
    ]
    assert [item["candidate_id"] for item in projection["missing_from_ledger"]] == [
        "CAND-002"
    ]


def test_cited_high_priority_is_clean() -> None:
    projection = _coverage_projection(
        _candidates(),
        _screened(priority_cand_002="medium"),
        _ledger(_claim()),
        "ExampleCo commissioned a 2 GW cell line [src:CLAIM-001].",
    )
    assert projection["missing_from_ledger"] == []
    assert projection["missing_from_brief"] == []
    assert _findings(projection) == []


def test_medium_priority_is_not_checked() -> None:
    projection = _coverage_projection(
        _candidates(),
        _screened(priority_cand_002="medium"),
        _ledger(_claim()),
        "ExampleCo commissioned a 2 GW cell line [src:CLAIM-001].",
    )
    assert projection["missing_from_ledger"] == []
    assert projection["missing_from_brief"] == []


def test_prose_limitation_line_does_not_escape() -> None:
    """A Markdown 'not covered' sentence must not waive a frozen high-priority duty."""
    markdown = (
        "Brief body.\n\n"
        "## Limitations\n"
        "- Not covered this week: CAND-001 — commissioning details still pending.\n"
    )
    projection = _coverage_projection(_candidates(), _screened(), _ledger(), markdown)
    assert [item["candidate_id"] for item in projection["missing_from_ledger"]] == [
        "CAND-001",
        "CAND-002",
    ]
    findings = _findings(projection)
    assert len(findings) == 2
    assert all(finding["blocking_level"] == "blocking" for finding in findings)


def test_high_priority_capacity_exceeded_blocks() -> None:
    projection = _coverage_projection(
        _candidates(),
        _screened(),
        _ledger(_claim()),
        "ExampleCo commissioned a 2 GW cell line [src:CLAIM-001].",
        high_priority_cap=1,
    )
    assert projection["high_priority_cap"] == 1
    findings = _findings(projection)
    capacity = [f for f in findings if f["finding_type"] == "high_priority_capacity_exceeded"]
    assert len(capacity) == 1
    assert capacity[0]["blocking_level"] == "blocking"
    assert capacity[0]["metadata"]["high_priority_selected_count"] == 2


def test_high_priority_within_capacity_is_clean() -> None:
    projection = _coverage_projection(
        _candidates(),
        _screened(priority_cand_002="medium"),
        _ledger(_claim()),
        "ExampleCo commissioned a 2 GW cell line [src:CLAIM-001].",
        high_priority_cap=20,
    )
    assert _findings(projection) == []
