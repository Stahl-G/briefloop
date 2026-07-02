"""Read-only support-calibrated wording diagnostics.

This module surfaces deterministic reader-facing wording risks from already
recorded source/support metadata. It does not judge claim truth, generate or
accept Claim-Support Matrix rows, run gates, approve delivery, or decide release
readiness.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.core.claim_ledger import ClaimLedger


SUPPORT_WORDING_SCHEMA_VERSION = "briefloop.support_wording.v1"
SUPPORT_WORDING_BOUNDARY = "support_wording_projection_only_not_support_truth_or_gate_authority"
SUPPORT_WORDING_RUNTIME_EFFECT = "none"

SUPPORT_WORDING_STATUSES = {
    "checked",
    "invalid_claim_ledger",
    "no_reader_targets",
    "not_available",
}
SUPPORT_WORDING_FINDING_TYPES = {
    "unsupported_claim_reaches_reader",
    "weak_support_strong_wording",
    "inference_without_framing",
    "source_class_strong_wording",
}
SUPPORT_WORDING_ACTIONS = {"review_support_wording_warnings", "request_human_review"}
SUPPORT_WORDING_NON_GOALS = {
    "semantic_truth_proof",
    "support_truth_assessment",
    "claim_support_matrix_generation",
    "semantic_assessment_acceptance",
    "gate_decision",
    "delivery_approval",
    "release_authority",
    "quality_score",
}
SUPPORT_WORDING_SUMMARY_FIELDS = {
    "target_count",
    "present_target_count",
    "unreadable_target_count",
    "finding_count",
    "unsupported_reader_claim_count",
    "weak_support_strong_wording_count",
    "inference_without_framing_count",
    "source_class_strong_wording_count",
}

_INTERMEDIATE = Path("output/intermediate")
_READER_TARGETS = ("output/brief.md", "output/delivery/brief.md")
_AUTHORITY_KEYS = {
    "approve_delivery",
    "approved_for_delivery",
    "accepted_support_truth",
    "claim_support_matrix_generation",
    "delivery_approval",
    "gate_decision",
    "quality_score",
    "release_authority",
    "semantic_truth_proof",
    "state_transition",
    "support_truth_assessment",
}
_STRONG_WORDING_RE = re.compile(
    r"\b(will|must|guarantee(?:s|d)?|prove(?:s|d)?|confirm(?:s|ed)?|demonstrate(?:s|d)?|"
    r"certain(?:ly)?|undeniabl(?:e|y)|officially\s+confirmed)\b|"
    r"(必然|一定|保证|证明|证实|确认|将会|毫无疑问)",
    re.IGNORECASE,
)
_FRAMING_RE = re.compile(
    r"\b(may|might|could|suggest(?:s|ed)?|appear(?:s|ed)?|report(?:s|ed)?|according\s+to|"
    r"indicat(?:e|es|ed)|estimate(?:s|d)?|scenario|possible|potential)\b|"
    r"(可能|或许|据|显示|表明|估计|情景|潜在|报道称|报告称)",
    re.IGNORECASE,
)
_ATTRIBUTION_RE = re.compile(
    r"\b(according\s+to|reported\s+by|the\s+report\s+(?:said|says)|sources?\s+(?:said|say))\b|"
    r"(据|报道称|报告称|来源称)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", re.IGNORECASE)


def project_workspace_support_wording(
    workspace: str | Path,
    *,
    claim_support_matrix: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project reader wording risks without writing workspace state."""

    ws = Path(workspace).expanduser().resolve()
    base = _base_projection()
    claims, reason = _read_claims(ws / _INTERMEDIATE / "claim_ledger.json")
    if reason:
        status = "not_available" if reason == "claim_ledger_missing" else "invalid_claim_ledger"
        return {
            **base,
            "status": status,
            "reason": reason,
            "claim_count": 0,
            "support_artifact_status": "not_available",
            "targets": _missing_targets(ws),
            "summary_counts": _summary_counts([]),
        }

    if isinstance(claim_support_matrix, Mapping):
        support_projection = dict(claim_support_matrix)
    else:
        from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
            project_claim_support_matrix_from_workspace,
        )

        support_projection = project_claim_support_matrix_from_workspace(ws)
    support_artifact_status = _support_artifact_status(support_projection)
    support_index = _support_index(support_projection) if support_artifact_status == "valid" else {}
    targets = [_project_target(ws, rel_path, claims, support_index) for rel_path in _READER_TARGETS]
    present_targets = [target for target in targets if target.get("status") not in {"missing", "unreadable"}]
    unreadable_target_count = sum(1 for target in targets if target.get("status") == "unreadable")
    findings = [finding for target in targets for finding in target.get("findings", []) if isinstance(finding, dict)]
    status = "checked" if present_targets else "not_available" if unreadable_target_count else "no_reader_targets"
    reason = None
    if not present_targets:
        reason = "reader_targets_unreadable" if unreadable_target_count else "reader_targets_missing"
    return {
        **base,
        "status": status,
        "reason": reason,
        "claim_count": len(claims),
        "support_artifact_status": support_artifact_status,
        "targets": targets,
        "findings": findings[:50],
        "summary_counts": _summary_counts(
            findings,
            present_target_count=len(present_targets),
            unreadable_target_count=unreadable_target_count,
        ),
        "recommended_actions": _recommended_actions(findings),
    }


def validate_support_wording_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "support_wording_schema_error:not_object"
    if _contains_authority_key(payload):
        return "support_wording_schema_error:authority_field"
    if payload.get("schema_version") != SUPPORT_WORDING_SCHEMA_VERSION:
        return "support_wording_schema_error:schema_version"
    if payload.get("boundary") != SUPPORT_WORDING_BOUNDARY:
        return "support_wording_schema_error:boundary"
    if payload.get("runtime_effect") != SUPPORT_WORDING_RUNTIME_EFFECT:
        return "support_wording_schema_error:runtime_effect"
    if payload.get("read_only") is not True:
        return "support_wording_schema_error:read_only"
    if payload.get("status") not in SUPPORT_WORDING_STATUSES:
        return "support_wording_schema_error:status"
    if not SUPPORT_WORDING_NON_GOALS.issubset({str(item) for item in payload.get("non_goals", [])}):
        return "support_wording_schema_error:non_goals"
    for field in ("targets", "findings", "recommended_actions", "non_goals"):
        if not isinstance(payload.get(field), list):
            return f"support_wording_schema_error:{field}"
    summary = payload.get("summary_counts")
    if not isinstance(summary, dict):
        return "support_wording_schema_error:summary_counts"
    for field in SUPPORT_WORDING_SUMMARY_FIELDS:
        if not isinstance(summary.get(field), int) or summary.get(field, 0) < 0:
            return f"support_wording_schema_error:summary_counts.{field}"
    for target in payload.get("targets", []):
        if not isinstance(target, dict):
            return "support_wording_schema_error:targets"
        if target.get("status") not in {"missing", "pass", "warning", "unreadable"}:
            return "support_wording_schema_error:targets.status"
    for finding in payload.get("findings", []):
        if not isinstance(finding, dict):
            return "support_wording_schema_error:findings"
        if str(finding.get("finding_type") or "") not in SUPPORT_WORDING_FINDING_TYPES:
            return "support_wording_schema_error:findings.finding_type"
        if str(finding.get("severity") or "") not in {"warning", "human_review"}:
            return "support_wording_schema_error:findings.severity"
    for action in payload.get("recommended_actions", []):
        if not isinstance(action, dict):
            return "support_wording_schema_error:recommended_actions"
        if str(action.get("action") or "") not in SUPPORT_WORDING_ACTIONS:
            return "support_wording_schema_error:recommended_actions.action"
    return None


def _base_projection() -> dict[str, Any]:
    return {
        "schema_version": SUPPORT_WORDING_SCHEMA_VERSION,
        "read_only": True,
        "runtime_effect": SUPPORT_WORDING_RUNTIME_EFFECT,
        "boundary": SUPPORT_WORDING_BOUNDARY,
        "semantic_boundary": "deterministic_lexical_projection_from_recorded_support_metadata_only",
        "targets": [],
        "findings": [],
        "summary_counts": _summary_counts([]),
        "recommended_actions": [],
        "non_goals": sorted(SUPPORT_WORDING_NON_GOALS),
    }


def _read_claims(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], "claim_ledger_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        claims = ClaimLedger._claim_items_from_json(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [], f"claim_ledger_unreadable:{type(exc).__name__}"
    return [dict(claim) for claim in claims], None


def _missing_targets(workspace: Path) -> list[dict[str, Any]]:
    return [
        {"target_artifact": rel_path, "status": "missing", "finding_count": 0, "findings": []}
        for rel_path in _READER_TARGETS
        if not (workspace / rel_path).exists()
    ]


def _project_target(
    workspace: Path,
    rel_path: str,
    claims: list[dict[str, Any]],
    support_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        return {"target_artifact": rel_path, "status": "missing", "finding_count": 0, "findings": []}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "target_artifact": rel_path,
            "status": "unreadable",
            "error": str(exc),
            "finding_count": 0,
            "findings": [],
        }
    findings: list[dict[str, Any]] = []
    for claim in claims:
        context = _matching_context(text, _text(claim.get("statement")))
        if not context:
            continue
        finding_base = {
            "target_artifact": rel_path,
            "claim_id": _text(claim.get("claim_id")),
            "evidence_ref": _short_text(context),
        }
        support = dict(support_index.get(_text(claim.get("claim_id"))) or _claim_intrinsic_support_risk(claim))
        source_class = _claim_source_class(claim)
        strong_wording = bool(_STRONG_WORDING_RE.search(context))
        framed = bool(_FRAMING_RE.search(context))
        attributed = bool(_ATTRIBUTION_RE.search(context))
        if support.get("blocking"):
            findings.append(_finding(
                finding_type="unsupported_claim_reaches_reader",
                severity="human_review",
                description="A claim with explicit unsupported/contradicted/insufficient support records appears in reader text.",
                recommendation="Downgrade, remove, or route the claim through support repair before relying on it in reader-facing text.",
                metadata={"support_labels": support.get("support_labels", []), "support_source": support.get("source")},
                **finding_base,
            ))
        if strong_wording and (support.get("weak") or support.get("downgrade_required")):
            findings.append(_finding(
                finding_type="weak_support_strong_wording",
                severity="warning",
                description="Reader text uses strong certainty wording for a claim recorded as weak or downgrade-required support.",
                recommendation="Use attributed, qualified wording or resolve the support record before retaining strong phrasing.",
                metadata={"support_labels": support.get("support_labels", []), "support_source": support.get("source")},
                **finding_base,
            ))
        if support.get("inference_required") and not framed:
            findings.append(_finding(
                finding_type="inference_without_framing",
                severity="warning",
                description="Reader text presents an inferred or interpretive claim without visible uncertainty/attribution framing.",
                recommendation="Frame the wording as an inference, estimate, scenario, or attributed report.",
                metadata={"support_labels": support.get("support_labels", []), "support_source": support.get("source")},
                **finding_base,
            ))
        if source_class in {"news_media", "market_report", "media_report"} and strong_wording and not attributed:
            findings.append(_finding(
                finding_type="source_class_strong_wording",
                severity="warning",
                description="Reader text uses strong wording for a claim sourced from media/report-style evidence without visible attribution.",
                recommendation="Attribute the source class or soften certainty wording unless stronger primary support is available.",
                metadata={"source_class": source_class},
                **finding_base,
            ))
    return {
        "target_artifact": rel_path,
        "status": "warning" if findings else "pass",
        "finding_count": len(findings),
        "findings": [
            {**finding, "finding_id": f"SUPWORD-{idx:03d}"}
            for idx, finding in enumerate(findings, start=1)
        ],
    }


def _support_artifact_status(projection: Mapping[str, Any]) -> str:
    status = _text(projection.get("status"))
    if status == "valid":
        return "valid"
    if status in {"invalid_matrix", "invalid"}:
        return "invalid"
    if status in {"not_available", ""}:
        return "not_available"
    return status


def _support_index(projection: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    atoms = projection.get("atoms") if isinstance(projection.get("atoms"), list) else []
    for atom in atoms:
        if not isinstance(atom, Mapping):
            continue
        claim_id = _text(atom.get("claim_id"))
        if not claim_id:
            continue
        item = index.setdefault(
            claim_id,
            {
                "source": "claim_support_matrix",
                "support_labels": set(),
                "blocking": False,
                "weak": False,
                "downgrade_required": False,
                "inference_required": False,
            },
        )
        item["blocking"] = bool(item["blocking"] or atom.get("blocking"))
        item["weak"] = bool(item["weak"] or atom.get("weak_support"))
        item["downgrade_required"] = bool(item["downgrade_required"] or atom.get("downgrade_required"))
        item["inference_required"] = bool(item["inference_required"] or atom.get("inference_framing_required"))
        labels = atom.get("support_labels") if isinstance(atom.get("support_labels"), list) else []
        item["support_labels"].update(str(label) for label in labels if str(label).strip())
    for item in index.values():
        item["support_labels"] = sorted(item["support_labels"])
    return index


def _claim_intrinsic_support_risk(claim: Mapping[str, Any]) -> dict[str, Any]:
    evidence_relation = _text(claim.get("evidence_relation"))
    epistemic_type = _text(claim.get("epistemic_type"))
    confidence = _text(claim.get("confidence"))
    claim_type = _text(claim.get("claim_type"))
    return {
        "source": "claim_ledger_metadata",
        "support_labels": [],
        "blocking": False,
        "weak": confidence == "low",
        "downgrade_required": False,
        "inference_required": evidence_relation in {"inferred", "analogous"}
        or epistemic_type in {"interpreted", "hypothesis", "analogy"}
        or claim_type in {"interpretation", "forecast", "risk"},
    }


def _claim_source_class(claim: Mapping[str, Any]) -> str:
    metadata = claim.get("metadata") if isinstance(claim.get("metadata"), Mapping) else {}
    for key in ("source_category", "underlying_evidence_type", "retrieval_source_type"):
        value = _text(metadata.get(key))
        if value:
            return value
    return _text(claim.get("source_type"))


def _matching_context(text: str, statement: str) -> str:
    if not statement.strip():
        return ""
    target_sentences = _sentences(text)
    statement_norm = _normalize_text(statement)
    for sentence in target_sentences:
        sentence_norm = _normalize_text(sentence)
        if statement_norm and statement_norm in sentence_norm:
            return sentence.strip()
    statement_tokens = _tokens(statement)
    if len(statement_tokens) < 4:
        return ""
    threshold = max(3, int(len(statement_tokens) * 0.65))
    for sentence in target_sentences:
        sentence_tokens = set(_tokens(sentence))
        if len(statement_tokens & sentence_tokens) >= threshold:
            return sentence.strip()
    return ""


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _tokens(text: str) -> set[str]:
    return set(_token_list(text))


def _normalize_text(text: str) -> str:
    return " ".join(_token_list(text))


def _token_list(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _finding(
    *,
    finding_type: str,
    severity: str,
    description: str,
    recommendation: str,
    target_artifact: str,
    claim_id: str,
    evidence_ref: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "finding_type": finding_type,
        "severity": severity,
        "target_artifact": target_artifact,
        "claim_id": claim_id,
        "description": description,
        "recommendation": recommendation,
        "evidence_ref": evidence_ref,
        "blocking": False,
        "runtime_effect": "none",
        "metadata": {
            **dict(metadata),
            "semantic_boundary": (
                "deterministic lexical warning from recorded support/source metadata; not support truth"
            ),
        },
    }


def _summary_counts(
    findings: list[Mapping[str, Any]],
    *,
    present_target_count: int = 0,
    unreadable_target_count: int = 0,
) -> dict[str, int]:
    counts = {
        "target_count": len(_READER_TARGETS),
        "present_target_count": present_target_count,
        "unreadable_target_count": unreadable_target_count,
        "finding_count": len(findings),
        "unsupported_reader_claim_count": 0,
        "weak_support_strong_wording_count": 0,
        "inference_without_framing_count": 0,
        "source_class_strong_wording_count": 0,
    }
    for finding in findings:
        finding_type = _text(finding.get("finding_type"))
        if finding_type == "unsupported_claim_reaches_reader":
            counts["unsupported_reader_claim_count"] += 1
        elif finding_type == "weak_support_strong_wording":
            counts["weak_support_strong_wording_count"] += 1
        elif finding_type == "inference_without_framing":
            counts["inference_without_framing_count"] += 1
        elif finding_type == "source_class_strong_wording":
            counts["source_class_strong_wording_count"] += 1
    return counts


def _recommended_actions(findings: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    if not findings:
        return []
    if any(_text(finding.get("severity")) == "human_review" for finding in findings):
        return [{"action": "request_human_review", "reason": "unsupported_claim_present_in_reader_text"}]
    return [{"action": "review_support_wording_warnings", "reason": "support_calibrated_wording_warning"}]


def _contains_authority_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _AUTHORITY_KEYS:
                return True
            if _contains_authority_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_authority_key(item) for item in value)
    return False


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _short_text(value: str, limit: int = 220) -> str:
    collapsed = " ".join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3].rstrip() + "..."
