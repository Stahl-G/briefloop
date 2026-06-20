"""Runtime validation helpers for Atomic Claim Graph artifacts."""

from __future__ import annotations

from collections import Counter
from typing import Any


ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX = "atomic_claim_graph_validation_error"


def validate_atomic_claim_graph_against_ledger(
    *,
    graph_payload: dict[str, Any],
    ledger_claims: list[dict[str, Any]],
) -> str | None:
    """Return the first deterministic graph/ledger validation reason, if any.

    This helper validates machine-checkable coverage and type consistency only.
    It does not infer atomization quality or judge support sufficiency.
    """

    graph_claims = [claim for claim in graph_payload.get("claims", []) if isinstance(claim, dict)]
    graph_claim_ids = [
        str(claim.get("claim_id")).strip()
        for claim in graph_claims
        if isinstance(claim.get("claim_id"), str) and claim.get("claim_id").strip()
    ]
    ledger_by_id = {
        str(claim.get("claim_id")).strip(): claim
        for claim in ledger_claims
        if isinstance(claim, dict)
        and isinstance(claim.get("claim_id"), str)
        and claim.get("claim_id").strip()
    }
    ledger_ids = set(ledger_by_id)

    duplicate_ids = sorted(claim_id for claim_id, count in Counter(graph_claim_ids).items() if count > 1)
    if duplicate_ids:
        return f"duplicate_claim_coverage:{duplicate_ids[0]}"

    unknown_ids = sorted(claim_id for claim_id in graph_claim_ids if claim_id not in ledger_ids)
    if unknown_ids:
        return f"unknown_claim_reference:{unknown_ids[0]}"

    missing_ids = sorted(ledger_ids - set(graph_claim_ids))
    if missing_ids:
        return f"missing_claim_coverage:{missing_ids[0]}"

    graph_by_claim_id = {
        str(claim.get("claim_id")).strip(): claim
        for claim in graph_claims
        if isinstance(claim.get("claim_id"), str) and claim.get("claim_id").strip()
    }
    for claim_id in sorted(ledger_ids):
        reason = _type_consistency_reason(
            claim_id=claim_id,
            ledger_claim=ledger_by_id[claim_id],
            graph_claim=graph_by_claim_id.get(claim_id) or {},
        )
        if reason:
            return reason

    return None


def _type_consistency_reason(
    *,
    claim_id: str,
    ledger_claim: dict[str, Any],
    graph_claim: dict[str, Any],
) -> str | None:
    roles = {
        str(atom.get("claim_role")).strip()
        for atom in graph_claim.get("atoms", [])
        if isinstance(atom, dict)
        and isinstance(atom.get("claim_role"), str)
        and atom.get("claim_role").strip()
    }
    claim_type = str(ledger_claim.get("claim_type") or "fact").strip()
    if claim_type == "number" and "numeric_fact" not in roles:
        return f"claim_type_number_missing_numeric_fact:{claim_id}"
    if claim_type == "forecast" and "forward_looking_inference" not in roles:
        return f"claim_type_forecast_missing_forward_looking_inference:{claim_id}"
    if claim_type == "risk" and "risk_or_limitation" not in roles:
        return f"claim_type_risk_missing_risk_atom:{claim_id}"
    if _has_limitations(ledger_claim) and "risk_or_limitation" not in roles:
        return f"claim_limitations_missing_risk_atom:{claim_id}"
    return None


def _has_limitations(claim: dict[str, Any]) -> bool:
    limitations = claim.get("limitations")
    return isinstance(limitations, list) and any(
        isinstance(item, str) and item.strip() for item in limitations
    )
