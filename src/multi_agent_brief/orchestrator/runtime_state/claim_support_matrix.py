"""Pure policy projection helpers for Claim-Support Matrix rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


CLAIM_SUPPORT_MATRIX_POLICY_PROJECTION_SCHEMA_VERSION = "mabw.claim_support_matrix.policy_projection.v1"
CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX = "claim_support_matrix_validation_error"

BLOCKING_SUPPORT_LABELS = {"unsupported", "contradicted", "insufficient_evidence"}
WEAK_SUPPORT_LABELS = {"weak_support"}
INFERENCE_SUPPORT_LABELS = {"inferential_support"}
DOWNGRADE_ACTIONS = {"downgrade_wording", "remove_claim"}
ADJUDICATION_ACTIONS = {"human_adjudication"}
INFERENCE_ACTIONS = {"mark_as_inference", "clarify_inference"}
SUPPORT_LABELS_ALLOWING_NULL_SPAN = {"unsupported", "insufficient_evidence", "not_applicable"}


def project_claim_support_policy(
    *,
    rows: Iterable[Mapping[str, Any]],
    atom_materiality: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Project row-level support records into atom-level policy signals.

    This is a deterministic policy projection only. It does not assess whether
    an evidence span semantically supports an atom and it does not write
    workspace state.
    """

    materiality_by_atom = {
        str(atom_id).strip(): str(materiality).strip()
        for atom_id, materiality in (atom_materiality or {}).items()
        if str(atom_id).strip() and str(materiality).strip()
    }
    atoms: dict[str, dict[str, Any]] = {}
    row_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        atom_id = _text(row.get("atom_id"))
        if not atom_id:
            continue
        row_count += 1
        atom = atoms.setdefault(atom_id, _empty_atom_projection(atom_id, materiality_by_atom.get(atom_id, "unknown")))
        _accumulate_row(atom, row)

    atom_projections = [_finalize_atom_projection(atom) for atom in atoms.values()]
    atom_projections.sort(key=lambda item: str(item.get("atom_id") or ""))
    return {
        "schema_version": CLAIM_SUPPORT_MATRIX_POLICY_PROJECTION_SCHEMA_VERSION,
        "status": "projected" if row_count else "not_available",
        "semantic_boundary": "deterministic_policy_projection_only_not_support_assessment",
        "row_count": row_count,
        "atom_count": len(atom_projections),
        "summary_counts": _summary_counts(atom_projections),
        "atoms": atom_projections,
    }


def project_claim_support_matrix_policy(
    matrix_payload: Mapping[str, Any],
    *,
    atom_materiality: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Project a schema-valid Claim-Support Matrix payload."""

    rows = matrix_payload.get("rows") if isinstance(matrix_payload, Mapping) else None
    return project_claim_support_policy(
        rows=rows if isinstance(rows, list) else [],
        atom_materiality=atom_materiality,
    )


def validate_claim_support_matrix_against_artifacts(
    *,
    matrix_payload: Mapping[str, Any],
    ledger_claims: Iterable[Mapping[str, Any]],
    graph_payload: Mapping[str, Any],
    evidence_span_registry_payload: Mapping[str, Any],
) -> str | None:
    """Return the first deterministic matrix cross-artifact validation reason.

    This validates only machine-checkable references and coverage. It does not
    judge whether a span semantically supports an atom and it does not decide
    release eligibility.
    """

    rows = [row for row in matrix_payload.get("rows", []) if isinstance(row, Mapping)]
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

    unknown_span_ids = sorted(
        {
            evidence_span_id
            for row in rows
            if (evidence_span_id := _nullable_text(row.get("evidence_span_id"))) is not None
            and evidence_span_id not in evidence_span_ids
        }
    )
    if unknown_span_ids:
        return f"unknown_evidence_span_reference:{unknown_span_ids[0]}"

    high_materiality_atom_ids = sorted(
        atom_id
        for atom_id, atom in atom_index.items()
        if atom.get("materiality") == "high"
    )
    row_atom_ids = {_text(row.get("atom_id")) for row in rows if _text(row.get("atom_id"))}
    missing_high_materiality_atoms = sorted(set(high_materiality_atom_ids) - row_atom_ids)
    if missing_high_materiality_atoms:
        return f"high_materiality_atom_missing_row:{missing_high_materiality_atoms[0]}"

    support_rows_without_span = sorted(
        _text(row.get("row_id")) or _text(row.get("atom_id")) or "<unknown_row>"
        for row in rows
        if row.get("evidence_span_id") is None
        and _text(row.get("support_label"))
        and _text(row.get("support_label")) not in SUPPORT_LABELS_ALLOWING_NULL_SPAN
    )
    if support_rows_without_span:
        return f"support_label_requires_span:{support_rows_without_span[0]}"

    return None


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


def _empty_atom_projection(atom_id: str, materiality: str) -> dict[str, Any]:
    return {
        "atom_id": atom_id,
        "claim_id": "",
        "materiality": materiality,
        "row_ids": [],
        "support_labels": set(),
        "support_strengths": set(),
        "required_actions": set(),
        "repair_owners": set(),
        "decision_sources": set(),
        "blocking_rows": [],
        "weak_rows": [],
        "downgrade_required_rows": [],
        "adjudication_required_rows": [],
        "inference_framing_required_rows": [],
    }


def _accumulate_row(atom: dict[str, Any], row: Mapping[str, Any]) -> None:
    row_id = _text(row.get("row_id"))
    if row_id:
        atom["row_ids"].append(row_id)
    if not atom["claim_id"]:
        atom["claim_id"] = _text(row.get("claim_id"))

    support_label = _text(row.get("support_label"))
    support_strength = _text(row.get("support_strength"))
    required_action = _text(row.get("required_action"))
    repair_owner = _text(row.get("repair_owner"))
    decision_source = _text(row.get("decision_source"))
    for field, value in (
        ("support_labels", support_label),
        ("support_strengths", support_strength),
        ("required_actions", required_action),
        ("repair_owners", repair_owner),
        ("decision_sources", decision_source),
    ):
        if value:
            atom[field].add(value)

    row_summary = _row_summary(row)
    materiality = _text(atom.get("materiality"))
    if required_action == "block_release" or (materiality == "high" and support_label in BLOCKING_SUPPORT_LABELS):
        atom["blocking_rows"].append(row_summary)
    if support_label in WEAK_SUPPORT_LABELS:
        atom["weak_rows"].append(row_summary)
    if support_label in WEAK_SUPPORT_LABELS or required_action in DOWNGRADE_ACTIONS:
        atom["downgrade_required_rows"].append(row_summary)
    if required_action in ADJUDICATION_ACTIONS:
        atom["adjudication_required_rows"].append(row_summary)
    if support_label in INFERENCE_SUPPORT_LABELS or required_action in INFERENCE_ACTIONS:
        atom["inference_framing_required_rows"].append(row_summary)


def _finalize_atom_projection(atom: dict[str, Any]) -> dict[str, Any]:
    atom["row_ids"] = sorted(atom["row_ids"])
    for key in (
        "blocking_rows",
        "weak_rows",
        "downgrade_required_rows",
        "adjudication_required_rows",
        "inference_framing_required_rows",
    ):
        atom[key] = sorted(atom[key], key=lambda item: str(item.get("row_id") or ""))
    for key in ("support_labels", "support_strengths", "required_actions", "repair_owners", "decision_sources"):
        atom[key] = sorted(atom[key])

    atom["blocking"] = bool(atom["blocking_rows"])
    atom["weak_support"] = bool(atom["weak_rows"])
    atom["downgrade_required"] = bool(atom["downgrade_required_rows"])
    atom["adjudication_required"] = bool(atom["adjudication_required_rows"])
    atom["inference_framing_required"] = bool(atom["inference_framing_required_rows"])
    atom["verdict"] = _atom_verdict(atom)
    return atom


def _atom_verdict(atom: Mapping[str, Any]) -> str:
    if atom.get("blocking"):
        return "blocking"
    if atom.get("adjudication_required"):
        return "adjudication_required"
    if atom.get("downgrade_required"):
        return "downgrade_required"
    if atom.get("inference_framing_required"):
        return "inference_framing_required"
    if atom.get("weak_support"):
        return "weak_support"
    return "recorded"


def _summary_counts(atoms: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "blocking_atom_count": sum(1 for atom in atoms if atom.get("blocking")),
        "blocking_row_count": sum(len(atom.get("blocking_rows") or []) for atom in atoms),
        "weak_atom_count": sum(1 for atom in atoms if atom.get("weak_support")),
        "weak_row_count": sum(len(atom.get("weak_rows") or []) for atom in atoms),
        "downgrade_required_atom_count": sum(1 for atom in atoms if atom.get("downgrade_required")),
        "adjudication_required_atom_count": sum(1 for atom in atoms if atom.get("adjudication_required")),
        "inference_framing_required_atom_count": sum(1 for atom in atoms if atom.get("inference_framing_required")),
    }


def _row_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "row_id": _text(row.get("row_id")),
        "claim_id": _text(row.get("claim_id")),
        "atom_id": _text(row.get("atom_id")),
        "evidence_span_id": _nullable_text(row.get("evidence_span_id")),
        "support_label": _text(row.get("support_label")),
        "support_strength": _text(row.get("support_strength")),
        "required_action": _text(row.get("required_action")),
        "repair_owner": _text(row.get("repair_owner")),
        "decision_source": _text(row.get("decision_source")),
    }


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    return _text(value)
