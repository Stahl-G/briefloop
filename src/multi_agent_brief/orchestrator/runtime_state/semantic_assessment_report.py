"""Runtime validation helpers for Semantic Assessment Report artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX = "semantic_assessment_report_validation_error"


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
    return _text(row.get("uncertainty")) == "high" or _text(row.get("disagreement")) == "high"


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
