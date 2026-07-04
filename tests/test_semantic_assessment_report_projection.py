from __future__ import annotations

from copy import deepcopy

from multi_agent_brief.audit.semantic import SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_PROPOSAL_PROJECTION_SCHEMA_VERSION,
    project_semantic_assessment_proposals,
    semantic_support_findings_from_schema_valid_report,
)


def _valid_report() -> dict:
    return {
        "schema_version": "mabw.semantic_assessment_report.v1",
        "assessors": [
            {
                "assessor_id": "ASR-001",
                "assessment_method": "llm_assisted_human",
                "label": "Reviewer A",
            },
            {
                "assessor_id": "ASR-002",
                "assessment_method": "llm_only",
                "label": "Model B",
            },
        ],
        "rows": [
            {
                "row_id": "SAR-0002",
                "claim_id": "CL-0001",
                "atom_id": "AC-0001-02",
                "candidate_evidence_span_ids": ["ESP-001-02", "ESP-001-03"],
                "proposed_support_label": "unsupported",
                "confidence": 0.38,
                "uncertainty": "high",
                "disagreement": "high",
                "requires_human_adjudication": True,
                "assessment_method": "llm_only",
                "assessor_id": "ASR-002",
                "rationale": "The candidate spans do not support the stronger atom.",
                "metadata": {"source": "fixture"},
            },
            {
                "row_id": "SAR-0001",
                "claim_id": "CL-0001",
                "atom_id": "AC-0001-01",
                "evidence_span_id": "ESP-001-01",
                "proposed_support_label": "partial_support",
                "confidence": 0.72,
                "uncertainty": "medium",
                "disagreement": "none",
                "requires_human_adjudication": False,
                "assessment_method": "llm_assisted_human",
                "assessor_id": "ASR-001",
                "rationale": "The span supports activity but not acceleration wording.",
            },
        ],
    }


def test_semantic_assessment_projection_emits_proposal_only_delta() -> None:
    report = _valid_report()

    projection = project_semantic_assessment_proposals(report)

    assert projection["schema_version"] == SEMANTIC_ASSESSMENT_PROPOSAL_PROJECTION_SCHEMA_VERSION
    assert projection["status"] == "projected"
    assert projection["semantic_boundary"] == "proposal_projection_only_not_accepted_support_truth"
    assert projection["proposal_count"] == 2
    assert projection["proposed_csm_delta"]["status"] == "proposal_only"
    assert projection["proposed_csm_delta"]["accepted_csm_rows"] == []


def test_semantic_assessment_projection_maps_rows_stably_without_accepting_truth() -> None:
    projection = project_semantic_assessment_proposals(_valid_report())
    rows = projection["proposed_claim_support_rows"]

    assert [row["proposal_id"] for row in rows] == ["SAR-0001", "SAR-0002"]
    assert rows[0] == {
        "proposal_id": "SAR-0001",
        "source_row_id": "SAR-0001",
        "claim_id": "CL-0001",
        "atom_id": "AC-0001-01",
        "evidence_span_id": "ESP-001-01",
        "candidate_evidence_span_ids": [],
        "relation_status": "single_span",
        "proposed_support_label": "partial_support",
        "proposed_support_reason": "The span supports activity but not acceleration wording.",
        "confidence": 0.72,
        "uncertainty": "medium",
        "disagreement": "none",
        "requires_human_adjudication": False,
        "assessor_id": "ASR-001",
        "assessor_label": "Reviewer A",
        "assessment_method": "llm_assisted_human",
        "accepted_support_truth": False,
        "writes_claim_support_matrix": False,
        "metadata": {},
    }
    assert rows[1]["evidence_span_id"] is None
    assert rows[1]["candidate_evidence_span_ids"] == ["ESP-001-02", "ESP-001-03"]
    assert rows[1]["relation_status"] == "candidate_spans"
    assert rows[1]["assessment_method"] == "llm_only"
    assert rows[1]["metadata"] == {"source": "fixture"}


def test_semantic_assessment_projection_counts_adjudication_and_uncertainty_signals() -> None:
    projection = project_semantic_assessment_proposals(_valid_report())

    assert projection["summary_counts"] == {
        "proposal_row_count": 2,
        "single_span_proposal_count": 1,
        "candidate_span_proposal_count": 1,
        "requires_human_adjudication_count": 1,
        "llm_only_count": 1,
        "high_uncertainty_count": 1,
        "high_disagreement_count": 1,
    }


def test_semantic_assessment_projection_does_not_mutate_report_payload() -> None:
    report = _valid_report()
    original = deepcopy(report)

    projection = project_semantic_assessment_proposals(report)
    projection["proposed_claim_support_rows"][1]["metadata"]["source"] = "changed"

    assert report == original


def test_semantic_support_findings_from_valid_report() -> None:
    report = _valid_report()

    findings = semantic_support_findings_from_schema_valid_report(report)

    assert {f.finding_id for f in findings} == {"SAR-0001", "SAR-0002"}
    assert all(f.finding_type == SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE for f in findings)
    # Advisory only: no proposal finding may carry a blocking severity/level.
    assert all(f.severity == "low" for f in findings)
    assert all(not f.blocking_level.endswith("_blocking") for f in findings)


def test_semantic_support_findings_from_invalid_report_are_empty() -> None:
    report = _valid_report()
    report["rows"][0]["row_id"] = "not-a-sar-id"  # contract violation

    assert semantic_support_findings_from_schema_valid_report(report) == []


def test_semantic_support_findings_from_non_mapping_are_empty() -> None:
    assert semantic_support_findings_from_schema_valid_report("junk") == []
    assert semantic_support_findings_from_schema_valid_report(None) == []


def test_semantic_support_findings_do_not_validate_artifact_bindings() -> None:
    # This entrypoint checks report SHAPE only. A schema-valid report that
    # references a claim/atom/span absent from the workspace still produces
    # findings here; binding validation is the workspace projection's job.
    report = _valid_report()
    report["rows"][0]["claim_id"] = "CL-9999"
    report["rows"][0]["atom_id"] = "AC-9999-02"  # still schema-valid (AC matches CL digits)

    findings = semantic_support_findings_from_schema_valid_report(report)

    assert any(f.finding_id == "SAR-0002" for f in findings)
    # These advisory findings never claim accepted support or authority.
    assert all(f.severity == "low" for f in findings)


def test_semantic_support_findings_do_not_mutate_report() -> None:
    report = _valid_report()
    original = deepcopy(report)

    semantic_support_findings_from_schema_valid_report(report)

    assert report == original


def test_semantic_assessment_projection_empty_rows_is_not_available() -> None:
    report = {"schema_version": "mabw.semantic_assessment_report.v1", "assessors": [], "rows": []}

    projection = project_semantic_assessment_proposals(report)

    assert projection["status"] == "not_available"
    assert projection["proposal_count"] == 0
    assert projection["proposed_csm_delta"] == {
        "status": "not_available",
        "accepted_csm_rows": [],
        "candidate_rows": [],
    }
