"""Read-only Atomic Claim Graph projection for reader-facing text."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.contracts.schemas.atomic_claim_graph import AtomicClaimGraphContract
from multi_agent_brief.core.citations import extract_src_ref_ids
from multi_agent_brief.orchestrator.runtime_state.atomic_claim_graph import (
    ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX,
    validate_atomic_claim_graph_against_ledger,
)


ATOM_ID_RE = re.compile(r"(?<![A-Za-z0-9_])AC-\d{4}-\d{2}(?![A-Za-z0-9_])")
ATOMIC_PROCESS_RE = re.compile(r"\b(?:atomic_claim_graph|atomic claim graph|atom_id|atom id)\b", re.IGNORECASE)


def project_atomic_reader_text(
    *,
    graph_payload: dict[str, Any] | None,
    target_text: str,
    target_artifact: str,
) -> dict[str, Any]:
    """Project deterministic graph residue and citation coverage from text.

    This projection only checks machine-visible IDs, process wording, and
    `[src:<claim_id>]` coverage. It does not judge whether prose semantically
    expresses or supports an atom.
    """

    if not isinstance(graph_payload, dict):
        return _empty_projection(
            status="not_available",
            target_artifact=target_artifact,
            graph_present=False,
            reason="atomic_claim_graph_missing",
        )

    graph_index = _graph_index(graph_payload)
    cited_claim_ids = sorted(set(extract_src_ref_ids(target_text)))
    cited_graph_claim_ids = sorted(set(cited_claim_ids) & graph_index["claim_ids"])
    uncited_graph_claim_ids = sorted(graph_index["claim_ids"] - set(cited_claim_ids))
    uncited_high_materiality_claim_ids = sorted(graph_index["high_materiality_claim_ids"] - set(cited_claim_ids))
    findings = _atom_residue_findings(
        target_text=target_text,
        target_artifact=target_artifact,
        known_atom_ids=graph_index["atom_ids"],
        atom_to_claim_id=graph_index["atom_to_claim_id"],
    )
    status = "warning" if findings else "pass"
    return {
        "status": status,
        "target_artifact": target_artifact,
        "graph_present": True,
        "semantic_boundary": "deterministic_id_and_citation_projection_only",
        "claim_citation_coverage": {
            "graph_claim_ids": sorted(graph_index["claim_ids"]),
            "cited_claim_ids": cited_claim_ids,
            "cited_graph_claim_ids": cited_graph_claim_ids,
            "uncited_graph_claim_ids": uncited_graph_claim_ids,
            "high_materiality_claim_ids": sorted(graph_index["high_materiality_claim_ids"]),
            "uncited_high_materiality_claim_ids": uncited_high_materiality_claim_ids,
        },
        "atom_residue_findings": findings,
        "summary_counts": {
            "graph_claim_count": len(graph_index["claim_ids"]),
            "graph_atom_count": len(graph_index["atom_ids"]),
            "cited_graph_claim_count": len(cited_graph_claim_ids),
            "uncited_graph_claim_count": len(uncited_graph_claim_ids),
            "high_materiality_claim_count": len(graph_index["high_materiality_claim_ids"]),
            "uncited_high_materiality_claim_count": len(uncited_high_materiality_claim_ids),
            "atom_residue_count": sum(1 for finding in findings if finding["finding_type"] == "atom_id_residue"),
            "unknown_atom_residue_count": sum(
                1 for finding in findings if finding["finding_type"] == "unknown_atom_id_residue"
            ),
            "process_residue_count": sum(
                1 for finding in findings if finding["finding_type"] == "atomic_graph_process_residue"
            ),
        },
    }


def project_atomic_reader_text_from_workspace(
    *,
    workspace: str | Path,
    target_text: str,
    target_artifact: str,
    ledger_claims: list[dict[str, Any]] | None = None,
    artifact_paths: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Load and validate workspace graph before projecting reader text."""

    ws = Path(workspace)
    graph_path = (
        artifact_paths["atomic_claim_graph"]
        if artifact_paths is not None
        else ws / "output" / "intermediate" / "atomic_claim_graph.json"
    )
    if not graph_path.exists():
        return _empty_projection(
            status="not_available",
            target_artifact=target_artifact,
            graph_present=False,
            reason="atomic_claim_graph_missing",
        )
    try:
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_projection(
            status="invalid_graph",
            target_artifact=target_artifact,
            graph_present=True,
            reason="atomic_claim_graph_unreadable",
        )
    if not isinstance(payload, dict):
        return _empty_projection(
            status="invalid_graph",
            target_artifact=target_artifact,
            graph_present=True,
            reason="atomic_claim_graph_schema_error:<root>",
        )

    schema_errors = AtomicClaimGraphContract.validate(payload)
    if schema_errors:
        return _empty_projection(
            status="invalid_graph",
            target_artifact=target_artifact,
            graph_present=True,
            reason=f"atomic_claim_graph_schema_error:{schema_errors[0].field}",
        )

    if ledger_claims is not None:
        claims = ledger_claims
    elif artifact_paths is None:
        claims = _load_ledger_claims(ws / "output" / "intermediate" / "claim_ledger.json")
    else:
        ledger_path = artifact_paths.get("claim_ledger")
        if ledger_path is None:
            return _empty_projection(
                status="invalid_graph",
                target_artifact=target_artifact,
                graph_present=True,
                reason=f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:claim_ledger_path_binding_missing",
            )
        claims = _load_ledger_claims(ledger_path)
    if claims is None:
        return _empty_projection(
            status="invalid_graph",
            target_artifact=target_artifact,
            graph_present=True,
            reason=f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:claim_ledger_missing",
        )
    validation_reason = validate_atomic_claim_graph_against_ledger(graph_payload=payload, ledger_claims=claims)
    if validation_reason:
        return _empty_projection(
            status="invalid_graph",
            target_artifact=target_artifact,
            graph_present=True,
            reason=f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:{validation_reason}",
        )
    return project_atomic_reader_text(
        graph_payload=payload,
        target_text=target_text,
        target_artifact=target_artifact,
    )


def _empty_projection(
    *,
    status: str,
    target_artifact: str,
    graph_present: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "target_artifact": target_artifact,
        "graph_present": graph_present,
        "reason": reason,
        "semantic_boundary": "deterministic_id_and_citation_projection_only",
        "claim_citation_coverage": {
            "graph_claim_ids": [],
            "cited_claim_ids": [],
            "cited_graph_claim_ids": [],
            "uncited_graph_claim_ids": [],
            "high_materiality_claim_ids": [],
            "uncited_high_materiality_claim_ids": [],
        },
        "atom_residue_findings": [],
        "summary_counts": {
            "graph_claim_count": 0,
            "graph_atom_count": 0,
            "cited_graph_claim_count": 0,
            "uncited_graph_claim_count": 0,
            "high_materiality_claim_count": 0,
            "uncited_high_materiality_claim_count": 0,
            "atom_residue_count": 0,
            "unknown_atom_residue_count": 0,
            "process_residue_count": 0,
        },
    }


def _graph_index(graph_payload: dict[str, Any]) -> dict[str, Any]:
    claim_ids: set[str] = set()
    atom_ids: set[str] = set()
    high_materiality_claim_ids: set[str] = set()
    atom_to_claim_id: dict[str, str] = {}
    for claim in graph_payload.get("claims", []):
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            continue
        claim_id = claim_id.strip()
        claim_ids.add(claim_id)
        for atom in claim.get("atoms", []):
            if not isinstance(atom, dict):
                continue
            atom_id = atom.get("atom_id")
            if isinstance(atom_id, str) and atom_id.strip():
                normalized_atom_id = atom_id.strip()
                atom_ids.add(normalized_atom_id)
                atom_to_claim_id[normalized_atom_id] = claim_id
            if atom.get("materiality") == "high":
                high_materiality_claim_ids.add(claim_id)
    return {
        "claim_ids": claim_ids,
        "atom_ids": atom_ids,
        "high_materiality_claim_ids": high_materiality_claim_ids,
        "atom_to_claim_id": atom_to_claim_id,
    }


def _atom_residue_findings(
    *,
    target_text: str,
    target_artifact: str,
    known_atom_ids: set[str],
    atom_to_claim_id: dict[str, str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(target_text.splitlines(), start=1):
        for match in ATOM_ID_RE.finditer(line):
            atom_id = match.group(0)
            known = atom_id in known_atom_ids
            findings.append(
                {
                    "finding_type": "atom_id_residue" if known else "unknown_atom_id_residue",
                    "atom_id": atom_id,
                    "claim_id": atom_to_claim_id.get(atom_id),
                    "line": line_number,
                    "text": _shorten(atom_id),
                    "target_artifact": target_artifact,
                    "message": (
                        "Reader-facing text contains an Atomic Claim Graph atom ID; use [src:<claim_id>] citations only."
                    ),
                }
            )
        for match in ATOMIC_PROCESS_RE.finditer(line):
            findings.append(
                {
                    "finding_type": "atomic_graph_process_residue",
                    "line": line_number,
                    "text": _shorten(match.group(0)),
                    "target_artifact": target_artifact,
                    "message": "Reader-facing text contains Atomic Claim Graph process wording.",
                }
            )
    return sorted(
        findings,
        key=lambda item: (
            int(item.get("line") or 0),
            str(item.get("finding_type") or ""),
            str(item.get("text") or ""),
        ),
    )


def _load_ledger_claims(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    if isinstance(payload, dict):
        for key in ("claims", "claim_ledger", "items"):
            items = payload.get(key)
            if isinstance(items, list) and all(isinstance(item, dict) for item in items):
                return items
    return None


def _shorten(value: str, *, limit: int = 120) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"
