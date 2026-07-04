"""Runtime validation helpers for Semantic Assessment Report artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from multi_agent_brief.audit.semantic import (
    SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY,
    SEMANTIC_SUPPORT_INVALID_CALIBRATION_LABEL,
    findings_from_semantic_proposal_rows,
    normalize_calibration_label,
)
from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract
from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    _read_json_mapping,
    _schema_error_reason,
    _workspace_atomic_graph_payload,
    _workspace_evidence_span_registry_payload,
    _workspace_ledger_claims,
)


SEMANTIC_ASSESSMENT_PROPOSAL_PROJECTION_SCHEMA_VERSION = (
    "mabw.semantic_assessment_report.proposal_projection.v1"
)
SEMANTIC_ASSESSMENT_WORKSPACE_PROJECTION_SCHEMA_VERSION = (
    "mabw.semantic_assessment_report.workspace_projection.v1"
)
SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX = "semantic_assessment_report_validation_error"
UNRESOLVED_SEMANTIC_ASSESSMENT_LEVELS = {"high", "unknown"}


def project_semantic_assessment_report_from_workspace(workspace: str | Path) -> dict[str, Any]:
    """Read and project a present, valid Semantic Assessment Report.

    This is a read-only status surface. It validates machine-checkable sibling
    artifact bindings before projecting proposal rows, but it does not accept
    support truth, mutate the Claim-Support Matrix, create adjudication queue
    items, write workspace state, gate delivery, or decide release eligibility.
    """

    ws = Path(workspace).expanduser().resolve()
    intermediate = ws / "output" / "intermediate"
    report_path = intermediate / "semantic_assessment_report.json"
    base = _workspace_projection_base(workspace=ws, report_path=report_path)
    if not report_path.exists():
        return {
            **base,
            "status": "not_available",
            "report_present": False,
            "reason": "semantic_assessment_report_missing",
            "proposal_projection": _empty_proposal_projection(),
            "summary_counts": {},
            "proposed_claim_support_rows": [],
        }

    report_payload, reason = _read_json_mapping(report_path, label="semantic_assessment_report")
    if reason:
        return _invalid_workspace_projection(base, reason=reason)
    assert report_payload is not None

    reason = _schema_error_reason(
        SemanticAssessmentReportContract.validate(report_payload),
        prefix="semantic_assessment_report_schema_error",
    )
    if reason:
        return _invalid_workspace_projection(base, reason=reason)

    ledger_claims, reason = _workspace_ledger_claims(intermediate / "claim_ledger.json")
    if reason:
        return _invalid_workspace_projection(base, reason=f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}")
    graph_payload, reason = _workspace_atomic_graph_payload(
        intermediate / "atomic_claim_graph.json",
        ledger_claims=ledger_claims or [],
    )
    if reason:
        return _invalid_workspace_projection(base, reason=f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}")
    evidence_payload, reason = _workspace_evidence_span_registry_payload(
        intermediate / "evidence_span_registry.json",
        workspace=ws,
    )
    if reason:
        return _invalid_workspace_projection(base, reason=f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}")

    reason = validate_semantic_assessment_report_against_artifacts(
        report_payload=report_payload,
        ledger_claims=ledger_claims or [],
        graph_payload=graph_payload or {},
        evidence_span_registry_payload=evidence_payload or {},
    )
    if reason:
        return _invalid_workspace_projection(
            base,
            reason=f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}",
        )

    proposal_projection = project_semantic_assessment_proposals(report_payload)
    return {
        **base,
        "status": "valid",
        "report_present": True,
        "reason": None,
        "proposal_projection": proposal_projection,
        "summary_counts": proposal_projection.get("summary_counts") or {},
        "proposed_claim_support_rows": proposal_projection.get("proposed_claim_support_rows")
        if isinstance(proposal_projection.get("proposed_claim_support_rows"), list)
        else [],
    }


def project_semantic_assessment_proposals(report_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project report rows into proposal-only Claim-Support Matrix deltas.

    This helper is intentionally read-only and non-authoritative. It does not
    create accepted Claim-Support Matrix rows, write workspace state, create
    adjudication records, judge semantic support, or decide release eligibility.
    """

    rows = [row for row in report_payload.get("rows", []) if isinstance(row, Mapping)]
    assessor_methods = _assessor_method_index(report_payload)
    assessor_labels = _assessor_label_index(report_payload)
    proposed_rows = [
        _project_report_row(row, assessor_methods=assessor_methods, assessor_labels=assessor_labels)
        for row in rows
    ]
    proposed_rows.sort(key=lambda item: str(item.get("proposal_id") or ""))
    summary_counts = _proposal_summary_counts(proposed_rows)
    return {
        "schema_version": SEMANTIC_ASSESSMENT_PROPOSAL_PROJECTION_SCHEMA_VERSION,
        "status": "projected" if proposed_rows else "not_available",
        "semantic_boundary": "proposal_projection_only_not_accepted_support_truth",
        "source_report_schema_version": _text(report_payload.get("schema_version")),
        "proposal_count": len(proposed_rows),
        "summary_counts": summary_counts,
        "proposed_claim_support_rows": proposed_rows,
        "proposed_csm_delta": {
            "status": "proposal_only" if proposed_rows else "not_available",
            "accepted_csm_rows": [],
            "candidate_rows": deepcopy(proposed_rows),
        },
    }


def semantic_support_findings_from_schema_valid_report(report_payload: Any) -> list[Any]:
    """Adapt a schema-valid Semantic Assessment Report into advisory findings.

    Returns ``list[AuditFinding]`` with ``finding_type="semantic_support_proposal"``.
    An invalid or non-mapping report yields no findings, keeping its invalid
    status visible through the existing registry/status path.

    This validates report SHAPE only (``SemanticAssessmentReportContract``). It
    does NOT validate cross-artifact bindings: a schema-valid report that
    references a claim_id/atom_id/evidence_span that does not exist in the
    workspace artifacts still produces findings here. Callers that need binding
    validation must use ``project_semantic_assessment_report_from_workspace``,
    which reads the Claim Ledger / Atomic Claim Graph / Evidence Span Registry
    and rejects unknown references before projecting.

    This is pure conversion: it never writes ``audit_report.json``, the
    Claim-Support Matrix, workflow state, gate reports, or delivery files, and
    the findings carry no gate or release authority.
    """

    if not isinstance(report_payload, Mapping):
        return []
    violations = SemanticAssessmentReportContract.validate(dict(report_payload))
    if any(violation.severity == "error" for violation in violations):
        return []
    projection = project_semantic_assessment_proposals(report_payload)
    proposal_rows = projection.get("proposed_claim_support_rows")
    if not isinstance(proposal_rows, list):
        return []
    return findings_from_semantic_proposal_rows(proposal_rows)


def _workspace_projection_base(*, workspace: Path, report_path: Path) -> dict[str, Any]:
    try:
        rendered_path = report_path.relative_to(workspace).as_posix()
    except ValueError:
        rendered_path = report_path.name
    return {
        "schema_version": SEMANTIC_ASSESSMENT_WORKSPACE_PROJECTION_SCHEMA_VERSION,
        "semantic_boundary": "proposal_projection_only_not_accepted_support_truth",
        "report_path": rendered_path,
    }


def _invalid_workspace_projection(base: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        **dict(base),
        "status": "invalid_report",
        "report_present": True,
        "reason": reason,
        "proposal_projection": _empty_proposal_projection(),
        "summary_counts": {},
        "proposed_claim_support_rows": [],
    }


def _empty_proposal_projection() -> dict[str, Any]:
    return project_semantic_assessment_proposals(
        {
            "schema_version": "",
            "assessors": [],
            "rows": [],
        }
    )


def validate_semantic_assessment_report_against_artifacts(
    *,
    report_payload: Mapping[str, Any],
    ledger_claims: Iterable[Mapping[str, Any]],
    graph_payload: Mapping[str, Any],
    evidence_span_registry_payload: Mapping[str, Any],
) -> str | None:
    """Return the first deterministic report cross-artifact validation reason.

    This validates only machine-checkable references and proposal authority
    flags. It does not judge semantic support, mutate the Claim-Support Matrix,
    create adjudication records, or decide release eligibility.
    """

    rows = [row for row in report_payload.get("rows", []) if isinstance(row, Mapping)]
    assessor_methods = _assessor_method_index(report_payload)
    ledger_claim_ids = _ledger_claim_ids(ledger_claims)
    atom_index = _graph_atom_index(graph_payload)
    evidence_span_ids = _evidence_span_ids(evidence_span_registry_payload)

    unknown_claim_ids = sorted(
        {
            claim_id
            for row in rows
            if (claim_id := _text(row.get("claim_id"))) and claim_id not in ledger_claim_ids
        }
    )
    if unknown_claim_ids:
        return f"unknown_claim_reference:{unknown_claim_ids[0]}"

    unknown_atom_ids = sorted(
        {
            atom_id
            for row in rows
            if (atom_id := _text(row.get("atom_id"))) and atom_id not in atom_index
        }
    )
    if unknown_atom_ids:
        return f"unknown_atom_reference:{unknown_atom_ids[0]}"

    atom_claim_mismatches = sorted(
        (
            atom_id,
            _text(row.get("claim_id")),
            str(atom_index[atom_id].get("claim_id") or ""),
        )
        for row in rows
        if (atom_id := _text(row.get("atom_id"))) in atom_index
        and _text(row.get("claim_id"))
        and _text(row.get("claim_id")) != atom_index[atom_id].get("claim_id")
    )
    if atom_claim_mismatches:
        atom_id, row_claim_id, graph_claim_id = atom_claim_mismatches[0]
        return f"atom_claim_mismatch:{atom_id}:{row_claim_id}:{graph_claim_id}"

    unknown_span_ids = sorted(_unknown_span_ids(rows=rows, evidence_span_ids=evidence_span_ids))
    if unknown_span_ids:
        return f"unknown_evidence_span_reference:{unknown_span_ids[0]}"

    method_mismatches = sorted(
        (
            _text(row.get("row_id")) or _text(row.get("atom_id")) or "<unknown_row>",
            _text(row.get("assessor_id")),
            _text(row.get("assessment_method")),
            assessor_methods[_text(row.get("assessor_id"))],
        )
        for row in rows
        if (assessor_id := _text(row.get("assessor_id"))) in assessor_methods
        and _text(row.get("assessment_method"))
        and _text(row.get("assessment_method")) != assessor_methods[assessor_id]
    )
    if method_mismatches:
        row_id, assessor_id, row_method, declared_method = method_mismatches[0]
        return f"assessment_method_mismatch:{row_id}:{assessor_id}:{row_method}:{declared_method}"

    missing_adjudication_flags = sorted(
        _text(row.get("row_id")) or _text(row.get("atom_id")) or "<unknown_row>"
        for row in rows
        if _requires_llm_only_high_materiality_adjudication(
            row=row,
            assessor_methods=assessor_methods,
            atom_index=atom_index,
        )
    )
    if missing_adjudication_flags:
        return f"llm_only_high_materiality_requires_human_adjudication:{missing_adjudication_flags[0]}"

    return None


def _project_report_row(
    row: Mapping[str, Any],
    *,
    assessor_methods: Mapping[str, str],
    assessor_labels: Mapping[str, str],
) -> dict[str, Any]:
    assessor_id = _text(row.get("assessor_id"))
    evidence_span_id = _text(row.get("evidence_span_id")) or None
    candidate_span_ids = _candidate_span_ids(row)
    relation_status = "single_span" if evidence_span_id else "candidate_spans"
    assessment_method = assessor_methods.get(assessor_id) or _text(row.get("assessment_method"))
    return {
        "proposal_id": _text(row.get("row_id")),
        "source_row_id": _text(row.get("row_id")),
        "claim_id": _text(row.get("claim_id")),
        "atom_id": _text(row.get("atom_id")),
        "evidence_span_id": evidence_span_id,
        "candidate_evidence_span_ids": candidate_span_ids,
        "relation_status": relation_status,
        "proposed_support_label": _text(row.get("proposed_support_label")),
        "proposed_support_reason": _text(row.get("rationale")),
        "confidence": row.get("confidence") if isinstance(row.get("confidence"), (int, float)) else None,
        "uncertainty": _text(row.get("uncertainty")),
        "disagreement": _text(row.get("disagreement")),
        "requires_human_adjudication": row.get("requires_human_adjudication") is True,
        "assessor_id": assessor_id,
        "assessor_label": assessor_labels.get(assessor_id, ""),
        "assessment_method": assessment_method,
        "accepted_support_truth": False,
        "writes_claim_support_matrix": False,
        "calibration_label": normalize_calibration_label(
            row.get("metadata", {}).get(SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY)
            if isinstance(row.get("metadata"), Mapping)
            else None
        ),
        "metadata": deepcopy(row.get("metadata")) if isinstance(row.get("metadata"), Mapping) else {},
    }


def _calibration_label_counts(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = row.get("calibration_label")
        if isinstance(label, str) and label:
            counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _proposal_summary_counts(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "proposal_row_count": len(rows),
        "single_span_proposal_count": sum(1 for row in rows if row.get("relation_status") == "single_span"),
        "candidate_span_proposal_count": sum(1 for row in rows if row.get("relation_status") == "candidate_spans"),
        "requires_human_adjudication_count": sum(
            1
            for row in rows
            if row.get("requires_human_adjudication") is True
            or row.get("calibration_label") == SEMANTIC_SUPPORT_INVALID_CALIBRATION_LABEL
        ),
        "llm_only_count": sum(1 for row in rows if row.get("assessment_method") == "llm_only"),
        "high_uncertainty_count": sum(1 for row in rows if row.get("uncertainty") == "high"),
        "high_disagreement_count": sum(1 for row in rows if row.get("disagreement") == "high"),
        "calibration_label_counts": _calibration_label_counts(rows),
    }


def _unknown_span_ids(*, rows: Iterable[Mapping[str, Any]], evidence_span_ids: set[str]) -> set[str]:
    unknown: set[str] = set()
    for row in rows:
        evidence_span_id = _text(row.get("evidence_span_id"))
        if evidence_span_id and evidence_span_id not in evidence_span_ids:
            unknown.add(evidence_span_id)
        candidates = row.get("candidate_evidence_span_ids")
        for candidate in candidates if isinstance(candidates, list) else []:
            candidate_id = _text(candidate)
            if candidate_id and candidate_id not in evidence_span_ids:
                unknown.add(candidate_id)
    return unknown


def _requires_llm_only_high_materiality_adjudication(
    *,
    row: Mapping[str, Any],
    assessor_methods: Mapping[str, str],
    atom_index: Mapping[str, Mapping[str, str]],
) -> bool:
    assessor_id = _text(row.get("assessor_id"))
    effective_method = assessor_methods.get(assessor_id) or _text(row.get("assessment_method"))
    if effective_method != "llm_only":
        return False
    atom_id = _text(row.get("atom_id"))
    if not atom_id or atom_index.get(atom_id, {}).get("materiality") != "high":
        return False
    if row.get("requires_human_adjudication") is True:
        return False
    return (
        _text(row.get("uncertainty")) in UNRESOLVED_SEMANTIC_ASSESSMENT_LEVELS
        or _text(row.get("disagreement")) in UNRESOLVED_SEMANTIC_ASSESSMENT_LEVELS
    )


def _candidate_span_ids(row: Mapping[str, Any]) -> list[str]:
    candidates = row.get("candidate_evidence_span_ids")
    if not isinstance(candidates, list):
        return []
    return [candidate_id for candidate in candidates if (candidate_id := _text(candidate))]


def _assessor_method_index(report_payload: Mapping[str, Any]) -> dict[str, str]:
    assessor_methods: dict[str, str] = {}
    assessors = report_payload.get("assessors") if isinstance(report_payload, Mapping) else None
    for assessor in assessors if isinstance(assessors, list) else []:
        if not isinstance(assessor, Mapping):
            continue
        assessor_id = _text(assessor.get("assessor_id"))
        assessment_method = _text(assessor.get("assessment_method"))
        if assessor_id and assessment_method and assessor_id not in assessor_methods:
            assessor_methods[assessor_id] = assessment_method
    return assessor_methods


def _assessor_label_index(report_payload: Mapping[str, Any]) -> dict[str, str]:
    assessor_labels: dict[str, str] = {}
    assessors = report_payload.get("assessors") if isinstance(report_payload, Mapping) else None
    for assessor in assessors if isinstance(assessors, list) else []:
        if not isinstance(assessor, Mapping):
            continue
        assessor_id = _text(assessor.get("assessor_id"))
        label = _text(assessor.get("label"))
        if assessor_id and label and assessor_id not in assessor_labels:
            assessor_labels[assessor_id] = label
    return assessor_labels


def _ledger_claim_ids(ledger_claims: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        claim_id
        for claim in ledger_claims
        if (claim_id := _text(claim.get("claim_id")))
    }


def _graph_atom_index(graph_payload: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    atom_index: dict[str, dict[str, str]] = {}
    claims = graph_payload.get("claims") if isinstance(graph_payload, Mapping) else None
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, Mapping):
            continue
        claim_id = _text(claim.get("claim_id"))
        atoms = claim.get("atoms")
        for atom in atoms if isinstance(atoms, list) else []:
            if not isinstance(atom, Mapping):
                continue
            atom_id = _text(atom.get("atom_id"))
            if not atom_id:
                continue
            atom_index[atom_id] = {
                "claim_id": claim_id,
                "materiality": _text(atom.get("materiality")),
            }
    return atom_index


def _evidence_span_ids(evidence_span_registry_payload: Mapping[str, Any]) -> set[str]:
    span_ids: set[str] = set()
    sources = (
        evidence_span_registry_payload.get("sources")
        if isinstance(evidence_span_registry_payload, Mapping)
        else None
    )
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, Mapping):
            continue
        spans = source.get("spans")
        for span in spans if isinstance(spans, list) else []:
            if not isinstance(span, Mapping):
                continue
            span_id = _text(span.get("span_id"))
            if span_id:
                span_ids.add(span_id)
    return span_ids


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
