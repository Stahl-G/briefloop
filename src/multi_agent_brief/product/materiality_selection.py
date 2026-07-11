"""Read-only materiality-aware selection diagnostic projection.

This module checks whether screened-out candidates match explicit configured
materiality or focus terms. It does not infer semantic importance, mutate
screening output, resurrect candidates, run gates, or approve delivery.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import yaml

from multi_agent_brief.contracts.agent_artifact_intake import (
    evaluate_workspace_agent_artifact_intakes,
    validate_workspace_intake_consumption_context,
)
from multi_agent_brief.product.policy_projection import project_workspace_policy_profile


MATERIALITY_SELECTION_SCHEMA_VERSION = "briefloop.materiality_selection.v1"
MATERIALITY_SELECTION_BOUNDARY = (
    "materiality_selection_projection_only_not_screening_mutation_or_semantic_importance_judgment"
)
MATERIALITY_SELECTION_RUNTIME_EFFECT = "none"
MATERIALITY_SELECTION_STATUSES = {
    "missing_screened_candidates",
    "invalid_screened_candidates",
    "legacy_not_interpreted",
    "no_materiality_policy",
    "checked",
}
MATERIALITY_SELECTION_NON_GOALS = {
    "semantic_importance_judgment",
    "screening_mutation",
    "claim_ledger_mutation",
    "candidate_resurrection",
    "gate_decision",
    "delivery_approval",
    "release_authority",
}
MATERIALITY_SELECTION_ACTIONS = {
    "review_materiality_exclusions",
    "request_human_review",
}
MATERIALITY_SELECTION_SUMMARY_COUNT_FIELDS = {
    "finding_count",
    "capacity_capped_materiality_count",
    "scope_narrowed_materiality_count",
    "weak_relevance_materiality_count",
    "human_review_recommended_count",
    "warning_count",
}

_INTERMEDIATE = Path("output/intermediate")
_CAPACITY_REASON_CODES = {"capacity_capped"}
_SCOPE_REASON_CODES = {"off_focus", "outside_scope", "weak_relevance"}
_INTERPRETED_REASON_CODES = _CAPACITY_REASON_CODES | _SCOPE_REASON_CODES
_FORBIDDEN_AUTHORITY_KEYS = {
    "approve_delivery",
    "approved_for_delivery",
    "claim_ledger_mutation",
    "delivery_approval",
    "gate_decision",
    "quality_score",
    "release_authority",
    "screening_mutation",
    "semantic_importance_score",
    "state_transition",
}


def project_workspace_materiality_selection(
    workspace: str | Path,
    *,
    policy_profile: Mapping[str, Any] | None = None,
    artifact_registry: Mapping[str, Any] | None = None,
    expected_run_id: str = "",
) -> dict[str, Any]:
    """Project materiality-aware screening diagnostics without side effects."""

    from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
        agent_artifact_paths_from_contracts,
    )

    ws = Path(workspace).expanduser().resolve()
    policy_projection = (
        dict(policy_profile)
        if isinstance(policy_profile, Mapping)
        else project_workspace_policy_profile(ws)
    )
    materiality_terms = _policy_materiality_terms(policy_projection)
    must_watch_terms = _workspace_focus_terms(ws)
    artifact_records = (
        artifact_registry.get("artifacts")
        if isinstance(artifact_registry, Mapping)
        else None
    )
    artifact_paths = agent_artifact_paths_from_contracts(
        ws,
        artifact_records if isinstance(artifact_records, Mapping) else {},
    )
    screened_path = artifact_paths.get(
        "screened_candidates",
        ws / _INTERMEDIATE / "screened_candidates.json",
    )
    base = _base_projection(
        policy_profile=policy_projection,
        materiality_terms=materiality_terms,
        must_watch_terms=must_watch_terms,
    )

    if not screened_path.exists():
        return {
            **base,
            "status": "missing_screened_candidates",
            "reason": "screened_candidates_missing",
            "screened_candidates_present": False,
        }

    bundle = evaluate_workspace_agent_artifact_intakes(
        ws,
        artifact_paths=artifact_paths,
    )
    intake = bundle.screened_candidates
    if intake is None:
        return {
            **base,
            "status": "invalid_screened_candidates",
            "reason": "screened_candidates_intake_result_unavailable",
            "screened_candidates_present": True,
        }
    if intake.status != "valid":
        return {
            **base,
            "status": "invalid_screened_candidates",
            "reason": intake.validation_result,
            "screened_candidates_present": True,
        }
    if artifact_registry is not None and expected_run_id:
        authority_reasons = validate_workspace_intake_consumption_context(
            artifact_registry,
            expected_run_id=expected_run_id,
            bundle=bundle,
            artifact_id="screened_candidates",
        )
        if authority_reasons:
            return {
                **base,
                "status": "invalid_screened_candidates",
                "reason": authority_reasons[0],
                "intake_authority_reasons": authority_reasons,
                "screened_candidates_present": True,
            }
    screened = intake.normalized_payload
    if isinstance(screened, list):
        return {
            **base,
            "status": "legacy_not_interpreted",
            "reason": "legacy_list_shape_has_no_discard_buckets",
            "screened_candidates_present": True,
        }
    if not isinstance(screened, dict):
        return {
            **base,
            "status": "invalid_screened_candidates",
            "reason": "screened_candidates_not_object_or_list",
            "screened_candidates_present": True,
        }
    selected = screened.get("selected")
    if not isinstance(selected, list):
        return {
            **base,
            "status": "invalid_screened_candidates",
            "reason": "screened_candidates_selected_missing",
            "screened_candidates_present": True,
        }
    discarded = _discarded_candidates(screened)
    if not materiality_terms and not must_watch_terms:
        return {
            **base,
            "status": "no_materiality_policy",
            "reason": "no_policy_materiality_or_focus_terms",
            "screened_candidates_present": True,
            "selected_count": len(selected),
            "discarded_count": len(discarded),
            "summary_counts": _summary_counts([]),
        }

    findings: list[dict[str, Any]] = []
    for item in discarded:
        finding = _candidate_finding(
            item,
            materiality_terms=materiality_terms,
            must_watch_terms=must_watch_terms,
        )
        if finding:
            findings.append({**finding, "finding_id": f"MATSEL-{len(findings)+1:03d}"})

    return {
        **base,
        "status": "checked",
        "screened_candidates_present": True,
        "selected_count": len(selected),
        "discarded_count": len(discarded),
        "findings": findings,
        "summary_counts": _summary_counts(findings),
        "recommended_actions": _recommended_actions(findings),
    }


def validate_materiality_selection_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "materiality_selection_schema_error:not_object"
    if _contains_forbidden_authority_key(payload):
        return "materiality_selection_schema_error:authority_field"
    if payload.get("schema_version") != MATERIALITY_SELECTION_SCHEMA_VERSION:
        return "materiality_selection_schema_error:schema_version"
    if payload.get("boundary") != MATERIALITY_SELECTION_BOUNDARY:
        return "materiality_selection_schema_error:boundary"
    if payload.get("runtime_effect") != MATERIALITY_SELECTION_RUNTIME_EFFECT:
        return "materiality_selection_schema_error:runtime_effect"
    if payload.get("read_only") is not True:
        return "materiality_selection_schema_error:read_only"
    if payload.get("status") not in MATERIALITY_SELECTION_STATUSES:
        return "materiality_selection_schema_error:status"
    for field in ("materiality_terms", "must_watch_terms", "findings", "recommended_actions", "non_goals"):
        if not isinstance(payload.get(field), list):
            return f"materiality_selection_schema_error:{field}"
    if not MATERIALITY_SELECTION_NON_GOALS.issubset({str(item) for item in payload.get("non_goals", [])}):
        return "materiality_selection_schema_error:non_goals"
    summary = payload.get("summary_counts")
    if not isinstance(summary, dict):
        return "materiality_selection_schema_error:summary_counts"
    for field in MATERIALITY_SELECTION_SUMMARY_COUNT_FIELDS:
        if not isinstance(summary.get(field), int) or summary.get(field, 0) < 0:
            return f"materiality_selection_schema_error:summary_counts.{field}"
    for field in ("selected_count", "discarded_count"):
        if field in payload and (not isinstance(payload.get(field), int) or payload.get(field, 0) < 0):
            return f"materiality_selection_schema_error:{field}"
    for finding in payload.get("findings", []):
        if not isinstance(finding, dict):
            return "materiality_selection_schema_error:findings"
        if _text(finding.get("severity")) not in {"warning", "human_review"}:
            return "materiality_selection_schema_error:findings.severity"
        if _text(finding.get("bucket")) not in {"excluded", "deprioritized"}:
            return "materiality_selection_schema_error:findings.bucket"
    for action in payload.get("recommended_actions", []):
        if not isinstance(action, dict):
            return "materiality_selection_schema_error:recommended_actions"
        if _text(action.get("action")) not in MATERIALITY_SELECTION_ACTIONS:
            return "materiality_selection_schema_error:recommended_actions.action"
    return None


def _base_projection(
    *,
    policy_profile: Mapping[str, Any],
    materiality_terms: list[str],
    must_watch_terms: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": MATERIALITY_SELECTION_SCHEMA_VERSION,
        "read_only": True,
        "runtime_effect": MATERIALITY_SELECTION_RUNTIME_EFFECT,
        "boundary": MATERIALITY_SELECTION_BOUNDARY,
        "semantic_boundary": "deterministic_keyword_match_from_config_only",
        "policy_profile_status": _text(policy_profile.get("status")) or "unknown",
        "resolved_policy_profile": _text(policy_profile.get("resolved_policy_profile")) or None,
        "screened_candidates_present": False,
        "selected_count": 0,
        "discarded_count": 0,
        "materiality_terms": materiality_terms,
        "must_watch_terms": must_watch_terms,
        "capacity_reason_codes": sorted(_CAPACITY_REASON_CODES),
        "scope_reason_codes": sorted(_SCOPE_REASON_CODES),
        "findings": [],
        "summary_counts": _summary_counts([]),
        "recommended_actions": [],
        "non_goals": sorted(MATERIALITY_SELECTION_NON_GOALS),
    }


def _policy_materiality_terms(policy_projection: Mapping[str, Any]) -> list[str]:
    if policy_projection.get("status") != "resolved":
        return []
    profile = policy_projection.get("profile") if isinstance(policy_projection.get("profile"), Mapping) else {}
    claim_policy = profile.get("claim_policy") if isinstance(profile.get("claim_policy"), Mapping) else {}
    return _string_list(claim_policy.get("materiality_terms"))


def _workspace_focus_terms(workspace: Path) -> list[str]:
    config_path = workspace / "config.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    if not isinstance(config, dict):
        return []
    focus = config.get("focus") if isinstance(config.get("focus"), Mapping) else {}
    return _string_list(focus.get("areas"))


def _discarded_candidates(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for bucket in ("excluded", "deprioritized"):
        entries = payload.get(bucket)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                result.append({**entry, "_screening_bucket": bucket})
    return result


def _candidate_finding(
    candidate: Mapping[str, Any],
    *,
    materiality_terms: list[str],
    must_watch_terms: list[str],
) -> dict[str, Any] | None:
    reason_code = _reason_code(candidate)
    if reason_code not in _INTERPRETED_REASON_CODES:
        return None
    text = _candidate_text(candidate)
    materiality_matches = _matched_terms(text, materiality_terms)
    must_watch_matches = _matched_terms(text, must_watch_terms)
    if not materiality_matches and not must_watch_matches:
        return None
    human_review = bool(must_watch_matches) or reason_code in _CAPACITY_REASON_CODES
    return {
        "candidate_id": _first_text(candidate, "candidate_id") or None,
        "bucket": _text(candidate.get("_screening_bucket")) or "excluded",
        "reason_code": reason_code,
        "severity": "human_review" if human_review else "warning",
        "matched_materiality_terms": materiality_matches,
        "matched_must_watch_terms": must_watch_matches,
        "statement": _first_text(candidate, "statement", "claim") or None,
        "source_id": _first_text(candidate, "source_id") or None,
        "explanation": _first_text(
            candidate,
            "explanation",
            "short_explanation",
            "screening_explanation",
            "reason_explanation",
            "screening_reason",
            "excluded_reason",
            "deprioritized_reason",
        )
        or None,
        "recommendation": (
            "Review this excluded/deprioritized candidate before finalizing selection; "
            "it matched configured materiality or focus terms but was dropped by capacity/scope screening."
        ),
    }


def _summary_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    reasons = Counter(_text(item.get("reason_code")) for item in findings)
    severities = Counter(_text(item.get("severity")) for item in findings)
    return {
        "finding_count": len(findings),
        "capacity_capped_materiality_count": reasons.get("capacity_capped", 0),
        "scope_narrowed_materiality_count": reasons.get("off_focus", 0) + reasons.get("outside_scope", 0),
        "weak_relevance_materiality_count": reasons.get("weak_relevance", 0),
        "human_review_recommended_count": severities.get("human_review", 0),
        "warning_count": severities.get("warning", 0),
    }


def _recommended_actions(findings: list[dict[str, Any]]) -> list[dict[str, str]]:
    counts = _summary_counts(findings)
    if counts["human_review_recommended_count"] > 0:
        return [{
            "action": "request_human_review",
            "reason": "materiality_or_focus_candidate_excluded_by_capacity_or_scope",
        }]
    if counts["warning_count"] > 0:
        return [{
            "action": "review_materiality_exclusions",
            "reason": "materiality_or_focus_candidate_deprioritized",
        }]
    return []


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "candidate_id",
        "statement",
        "claim",
        "evidence_text",
        "topic",
        "title",
        "source_title",
        "source_name",
    ):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    metadata = candidate.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("topic", "materiality", "priority", "tags"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, list):
                parts.extend(str(item).strip() for item in value if str(item).strip())
    return "\n".join(parts)


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    normalized_text = _normalize_for_match(text)
    matches: list[str] = []
    for term in terms:
        normalized_term = _normalize_for_match(term)
        if not normalized_term:
            continue
        if normalized_term in normalized_text and term not in matches:
            matches.append(term)
    return matches


def _reason_code(candidate: Mapping[str, Any]) -> str:
    return _text(candidate.get("reason_code"))


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text(item)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _contains_forbidden_authority_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _FORBIDDEN_AUTHORITY_KEYS:
                return True
            if _contains_forbidden_authority_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_authority_key(item) for item in value)
    return False
