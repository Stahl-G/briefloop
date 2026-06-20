"""Contract for experimental Atomic Claim Graph artifacts."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

ATOMIC_CLAIM_GRAPH_SCHEMA_VERSION = "mabw.atomic_claim_graph.v1"
ATOM_ID_RE = re.compile(r"^AC-(\d{4})-\d{2}$")
CLAIM_ID_RE = re.compile(r"^CL-(\d{4})$")

VALID_ATOMIC_CLAIM_ROLES = {
    "observed_fact",
    "numeric_fact",
    "trend_interpretation",
    "causal_inference",
    "comparative_claim",
    "forward_looking_inference",
    "risk_or_limitation",
    "recommendation",
    "background_context",
}
VALID_ATOMIC_MATERIALITY = {"low", "medium", "high"}


@SchemaRegistry.register
class AtomicClaimGraphContract(Contract):
    """Validate experimental atomic decompositions of Claim Ledger claims.

    This contract validates graph shape, IDs, structural labels, and local edge
    references only. Workspace-scoped Claim Ledger reference checks belong in
    runtime artifact validation.
    """

    schema_id: ClassVar[str] = "atomic_claim_graph"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["schema_version", "claims"],
            "properties": {
                "schema_version": {
                    "type": "string",
                    "enum": [ATOMIC_CLAIM_GRAPH_SCHEMA_VERSION],
                },
                "claims": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["claim_id", "atoms"],
                        "properties": {
                            "claim_id": {"type": "string"},
                            "statement": {"type": "string"},
                            "atoms": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": [
                                        "atom_id",
                                        "text",
                                        "claim_role",
                                        "materiality",
                                    ],
                                    "properties": {
                                        "atom_id": {"type": "string", "pattern": ATOM_ID_RE.pattern},
                                        "text": {"type": "string"},
                                        "claim_role": {
                                            "type": "string",
                                            "enum": sorted(VALID_ATOMIC_CLAIM_ROLES),
                                        },
                                        "materiality": {
                                            "type": "string",
                                            "enum": sorted(VALID_ATOMIC_MATERIALITY),
                                        },
                                    },
                                    "additionalProperties": True,
                                },
                            },
                            "edges": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["from", "to", "relation"],
                                    "properties": {
                                        "from": {"type": "string"},
                                        "to": {"type": "string"},
                                        "relation": {"type": "string"},
                                    },
                                    "additionalProperties": True,
                                },
                            },
                            "metadata": {"type": "object"},
                        },
                        "additionalProperties": True,
                    },
                },
                "metadata": {"type": "object"},
            },
            "additionalProperties": True,
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        if not isinstance(data, dict):
            return [FieldViolation(field="<root>", error="must be an object")]

        violations: list[FieldViolation] = []
        schema_version = data.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version.strip():
            violations.append(FieldViolation(field="schema_version", error="required field is missing"))
        elif schema_version != ATOMIC_CLAIM_GRAPH_SCHEMA_VERSION:
            violations.append(
                FieldViolation(
                    field="schema_version",
                    error=f"must be {ATOMIC_CLAIM_GRAPH_SCHEMA_VERSION}",
                )
            )

        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            violations.append(FieldViolation(field="metadata", error="must be an object"))

        claims = data.get("claims")
        if not isinstance(claims, list):
            violations.append(FieldViolation(field="claims", error="must be a non-empty list"))
            return violations
        if not claims:
            violations.append(FieldViolation(field="claims", error="must be a non-empty list"))

        seen_atom_ids: set[str] = set()
        for claim_idx, claim in enumerate(claims):
            violations.extend(
                _validate_claim_entry(
                    claim,
                    idx=claim_idx,
                    seen_atom_ids=seen_atom_ids,
                )
            )

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _validate_claim_entry(
    claim: Any,
    *,
    idx: int,
    seen_atom_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"claims[{idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(claim, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    claim_id = claim.get("claim_id")
    canonical_claim_match: re.Match[str] | None = None
    if not _non_empty_string(claim_id):
        violations.append(FieldViolation(field=f"{prefix}.claim_id", error="must be a non-empty string"))
    else:
        claim_id = str(claim_id).strip()
        canonical_claim_match = CLAIM_ID_RE.match(claim_id)

    statement = claim.get("statement")
    if statement is not None and not _non_empty_string(statement):
        violations.append(FieldViolation(field=f"{prefix}.statement", error="must be a non-empty string"))

    metadata = claim.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        violations.append(FieldViolation(field=f"{prefix}.metadata", error="must be an object"))

    atoms = claim.get("atoms")
    if not isinstance(atoms, list):
        violations.append(FieldViolation(field=f"{prefix}.atoms", error="must be a non-empty list"))
        atoms = []
    elif not atoms:
        violations.append(FieldViolation(field=f"{prefix}.atoms", error="must be a non-empty list"))

    expected_atom_prefix = canonical_claim_match.group(1) if canonical_claim_match else None
    local_atom_ids: set[str] = set()
    for atom_idx, atom in enumerate(atoms):
        violations.extend(
            _validate_atom_entry(
                atom,
                claim_prefix=prefix,
                atom_idx=atom_idx,
                expected_atom_prefix=expected_atom_prefix,
                seen_atom_ids=seen_atom_ids,
                local_atom_ids=local_atom_ids,
            )
        )

    edges = claim.get("edges")
    if edges is None:
        return violations
    if not isinstance(edges, list):
        violations.append(FieldViolation(field=f"{prefix}.edges", error="must be a list"))
        return violations
    for edge_idx, edge in enumerate(edges):
        violations.extend(
            _validate_edge_entry(edge, claim_prefix=prefix, edge_idx=edge_idx, local_atom_ids=local_atom_ids)
        )
    return violations


def _validate_atom_entry(
    atom: Any,
    *,
    claim_prefix: str,
    atom_idx: int,
    expected_atom_prefix: str | None,
    seen_atom_ids: set[str],
    local_atom_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"{claim_prefix}.atoms[{atom_idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(atom, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    atom_id = atom.get("atom_id")
    atom_match = ATOM_ID_RE.match(atom_id.strip()) if _non_empty_string(atom_id) else None
    if not atom_match:
        violations.append(FieldViolation(field=f"{prefix}.atom_id", error="must match AC-####-##"))
    else:
        normalized_atom_id = str(atom_id).strip()
        if normalized_atom_id in seen_atom_ids:
            violations.append(
                FieldViolation(field=f"{prefix}.atom_id", error=f"duplicate atom_id:{normalized_atom_id}")
            )
        seen_atom_ids.add(normalized_atom_id)
        local_atom_ids.add(normalized_atom_id)
        if expected_atom_prefix is not None and atom_match.group(1) != expected_atom_prefix:
            violations.append(
                FieldViolation(
                    field=f"{prefix}.atom_id",
                    error=f"must use AC-{expected_atom_prefix}-## for matching claim_id",
                )
            )

    text = atom.get("text")
    if not _non_empty_string(text):
        violations.append(FieldViolation(field=f"{prefix}.text", error="must be a non-empty string"))

    role = atom.get("claim_role")
    if not _non_empty_string(role):
        violations.append(FieldViolation(field=f"{prefix}.claim_role", error="must be a non-empty string"))
    elif role not in VALID_ATOMIC_CLAIM_ROLES:
        violations.append(
            FieldViolation(
                field=f"{prefix}.claim_role",
                error=f"invalid claim_role '{role}', must be one of {sorted(VALID_ATOMIC_CLAIM_ROLES)}",
            )
        )

    materiality = atom.get("materiality")
    if not _non_empty_string(materiality):
        violations.append(FieldViolation(field=f"{prefix}.materiality", error="must be a non-empty string"))
    elif materiality not in VALID_ATOMIC_MATERIALITY:
        violations.append(
            FieldViolation(
                field=f"{prefix}.materiality",
                error=f"invalid materiality '{materiality}', must be one of {sorted(VALID_ATOMIC_MATERIALITY)}",
            )
        )

    return violations


def _validate_edge_entry(
    edge: Any,
    *,
    claim_prefix: str,
    edge_idx: int,
    local_atom_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"{claim_prefix}.edges[{edge_idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(edge, dict):
        return [FieldViolation(field=prefix, error="must be an object")]
    for field in ("from", "to"):
        value = edge.get(field)
        if not _non_empty_string(value):
            violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be a non-empty string"))
        elif str(value).strip() not in local_atom_ids:
            violations.append(
                FieldViolation(field=f"{prefix}.{field}", error=f"unknown local atom_id '{str(value).strip()}'")
            )
    relation = edge.get("relation")
    if not _non_empty_string(relation):
        violations.append(FieldViolation(field=f"{prefix}.relation", error="must be a non-empty string"))
    return violations


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
