"""Artifact registry helpers for Orchestrator runtime state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml

from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_IDS,
    AgentArtifactId,
    IntakeResult,
    evaluate_workspace_agent_artifact_intakes,
)
from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.contracts.schemas.atomic_claim_graph import AtomicClaimGraphContract
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract
from multi_agent_brief.contracts.schemas.claim_support_matrix import ClaimSupportMatrixContract
from multi_agent_brief.contracts.schemas.evidence_span_registry import EvidenceSpanRegistryContract
from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract
from multi_agent_brief.contracts.schemas.source_evidence_pack_manifest import SourceEvidencePackManifestContract
from multi_agent_brief.contracts.source_metadata import (
    local_file_without_url_missing_identity,
    source_category_error,
    source_url_error,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.feedback.feedback_contract import optional_feedback_artifact_activated
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    artifact_paths_from_contracts,
)
from multi_agent_brief.orchestrator.runtime_state.atomic_claim_graph import (
    ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX,
    validate_atomic_claim_graph_against_ledger,
)
from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX,
    validate_claim_support_matrix_against_artifacts,
)
from multi_agent_brief.orchestrator.runtime_state.evidence_span_registry import (
    EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX,
    validate_evidence_span_registry_against_source_pack,
)
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX,
    validate_semantic_assessment_checked_inputs_for_workspace,
    validate_semantic_assessment_report_against_artifacts,
)
from multi_agent_brief.orchestrator.runtime_state.semantic_support_acceptance import (
    validate_semantic_support_acceptance_ledger_for_workspace,
)
from multi_agent_brief.orchestrator.runtime_state.source_evidence_pack import (
    SOURCE_EVIDENCE_PACK_VALIDATION_PREFIX,
    validate_source_evidence_pack_manifest,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    project_stage_completion_for_read,
    interpret_stage_completion,
    _stage_is_complete_or_skipped,
)
from multi_agent_brief.product.quality_panel import (
    QualityPanelError,
    render_quality_panel_html,
    render_quality_summary,
    validate_quality_panel_html,
    validate_quality_panel_payload,
    validate_quality_summary_markdown,
)
from multi_agent_brief.provenance.contract import provenance_artifact_activated
from multi_agent_brief.quality_gates.contract import quality_gate_artifact_activated


ARTIFACT_REGISTRY_SCHEMA = "multi-agent-brief-artifact-registry/v1"

ARTIFACT_EXPECTED = "expected"
ARTIFACT_MISSING = "missing"
ARTIFACT_PRESENT = "present"
ARTIFACT_VALID = "valid"
ARTIFACT_INVALID = "invalid"
ARTIFACT_STALE = "stale"
CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE = (
    "claim_ledger.json is frozen. Do not hand-edit metadata or synchronize hashes manually. "
    "Rebuild the fact layer or use a deterministic metadata enrichment transaction when available."
)
FROZEN_ARTIFACT_CONTROL_FILE_GUIDANCE = (
    "Do not manually update artifact_registry.json, runtime_manifest.json, workflow_state.json, "
    "event_log.jsonl, or SHA fields to hide the change."
)

_SCREENING_STATUSES = {
    "keep",
    "selected",
    "reject",
    "rejected",
    "deprioritized",
    "exclude",
    "excluded",
    "watch",
}
_SCREENING_STATUSES_REQUIRING_REASON = {
    "reject",
    "rejected",
    "deprioritized",
    "exclude",
    "excluded",
}
_SCREENING_DISCARD_REASON_CODES = {
    "capacity_capped",
    "duplicate_source",
    "low_confidence",
    "low_tier",
    "off_focus",
    "other",
    "outside_scope",
    "stale_source",
    "unsafe_evidence_boundary",
    "weak_relevance",
}
_SCREENING_DISCARD_REASON_ALIASES = {
    "capacity_cut": "capacity_capped",
    "capacity_cap": "capacity_capped",
    "capacity_capped": "capacity_capped",
    "duplicate": "duplicate_source",
    "duplicate_source": "duplicate_source",
    "duplicate_sources": "duplicate_source",
    "low_confidence": "low_confidence",
    "low_tier": "low_tier",
    "off_focus": "off_focus",
    "off_topic": "off_focus",
    "other": "other",
    "outside_scope": "outside_scope",
    "stale": "stale_source",
    "stale_source": "stale_source",
    "stale_sources": "stale_source",
    "unsafe_evidence": "unsafe_evidence_boundary",
    "unsafe_evidence_boundary": "unsafe_evidence_boundary",
    "weak_relevance": "weak_relevance",
}
_INPUT_CLASSIFICATION_BUCKETS = {"evidence", "context", "feedback", "instruction", "skipped"}
_INPUT_CLASSIFICATION_PATH_KEYS = {
    "path",
    "file",
    "source_path",
    "relative_path",
    "input_path",
    "workspace_path",
    "extracted_markdown",
}


@dataclass(frozen=True)
class FrozenArtifactIntegrityVerdict:
    """Single interpretation of frozen artifact integrity."""

    kind: str
    value: dict[str, Any]
    reasons: tuple[str, ...] = ()
    contaminates_run: bool = False


def _validate_artifact(
    path: Path,
    fmt: str,
    artifact_id: str = "",
    *,
    workspace: Path,
    intake_result: IntakeResult | None = None,
    artifact_paths: Mapping[str, Path],
) -> tuple[str, str]:
    if not path.exists():
        return ARTIFACT_EXPECTED, "not_checked"
    if not path.is_file():
        return ARTIFACT_INVALID, "not_a_file"
    if fmt == "json" and artifact_id in AGENT_ARTIFACT_IDS:
        if intake_result is None:
            return ARTIFACT_INVALID, f"{artifact_id}_intake_result_unavailable"
        status = ARTIFACT_VALID if intake_result.status == "valid" else ARTIFACT_INVALID
        return status, intake_result.validation_result
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ARTIFACT_INVALID, "decode_error"
    except OSError:
        return ARTIFACT_INVALID, "read_error"
    if not text.strip():
        return ARTIFACT_INVALID, "empty"

    try:
        if fmt == "json":
            payload = json.loads(text)
            if artifact_id == "claim_ledger":
                return _validate_claim_ledger_payload(payload)
            if artifact_id == "claim_drafts":
                return _validate_claim_drafts_payload(payload)
            if artifact_id == "atomic_claim_graph":
                return _validate_atomic_claim_graph_payload(
                    payload,
                    artifact_path=path,
                    artifact_paths=artifact_paths,
                )
            if artifact_id == "evidence_span_registry":
                return _validate_evidence_span_registry_payload(
                    payload,
                    artifact_path=path,
                    artifact_paths=artifact_paths,
                    workspace=workspace,
                )
            if artifact_id == "claim_support_matrix":
                return _validate_claim_support_matrix_payload(
                    payload,
                    artifact_path=path,
                    artifact_paths=artifact_paths,
                    workspace=workspace,
                )
            if artifact_id == "semantic_assessment_report":
                return _validate_semantic_assessment_report_payload(
                    payload,
                    artifact_path=path,
                    artifact_paths=artifact_paths,
                    workspace=workspace,
                )
            if artifact_id == "semantic_support_acceptance_ledger":
                return _validate_semantic_support_acceptance_ledger_payload(payload, artifact_path=path)
            if artifact_id == "audit_report":
                return _validate_audit_report_payload(payload)
            if artifact_id == "candidate_claims":
                return _validate_candidate_claims_payload(payload)
            if artifact_id == "screened_candidates":
                return _validate_screened_candidates_payload(
                    payload,
                    artifact_paths=artifact_paths,
                )
            if artifact_id == "input_classification":
                return _validate_input_classification_payload(payload, artifact_path=path)
            if artifact_id == "source_evidence_pack_manifest":
                return _validate_source_evidence_pack_manifest_payload(payload, artifact_path=path)
            if artifact_id == "evidence_extract_source_lock":
                return _validate_evidence_extract_source_lock_payload(payload, artifact_path=path)
            if artifact_id == "evidence_extract_page_inventory":
                return _validate_evidence_extract_page_inventory_payload(payload, artifact_path=path)
            if artifact_id == "human_approval_ledger":
                return _validate_human_approval_ledger_payload(payload, artifact_path=path)
            if artifact_id == "release_readiness_report":
                return _validate_release_readiness_report_payload(payload, artifact_path=path)
            if artifact_id == "quality_panel":
                return _validate_quality_panel_payload(payload)
        elif fmt in {"yaml", "yml"}:
            yaml.safe_load(text)
        elif fmt == "markdown":
            if artifact_id == "quality_summary":
                return _validate_quality_summary_markdown(text, artifact_path=path)
        elif fmt == "html":
            if artifact_id == "quality_panel_html":
                return _validate_quality_panel_html(text, artifact_path=path)
    except json.JSONDecodeError:
        return ARTIFACT_INVALID, "parse_error"
    except yaml.YAMLError:
        return ARTIFACT_INVALID, "parse_error"

    return ARTIFACT_VALID, "valid_minimum"


def _validate_candidate_claims_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, list):
        return ARTIFACT_INVALID, "candidate_claims_schema_error:not_list"

    seen_ids: set[str] = set()
    for idx, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}]"
        if _candidate_claim_uses_legacy_shape(candidate):
            status, result = _validate_legacy_candidate_claim(candidate, idx=idx, seen_ids=seen_ids)
        else:
            status, result = _validate_contract_candidate_claim(candidate, idx=idx, seen_ids=seen_ids)
        if status != ARTIFACT_VALID:
            return status, result

    return ARTIFACT_VALID, "valid_candidate_claims_schema"


def _candidate_claim_uses_legacy_shape(candidate: dict[str, Any]) -> bool:
    return "statement" not in candidate and ("claim" in candidate or "candidate_id" in candidate)


def _validate_legacy_candidate_claim(
    candidate: dict[str, Any],
    *,
    idx: int,
    seen_ids: set[str],
) -> tuple[str, str]:
    for field in ("candidate_id", "claim", "source_id"):
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].{field}"
    candidate_id = str(candidate["candidate_id"]).strip()
    if candidate_id in seen_ids:
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:duplicate_candidate_id:{candidate_id}"
    seen_ids.add(candidate_id)
    return ARTIFACT_VALID, "valid_candidate_claims_schema"


def _validate_contract_candidate_claim(
    candidate: dict[str, Any],
    *,
    idx: int,
    seen_ids: set[str],
) -> tuple[str, str]:
    for field in ("statement", "evidence_text", "topic", "claim_type"):
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].{field}"
    url_error = source_url_error(candidate.get("source_url"))
    if url_error:
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].source_url"
    category_error = source_category_error(candidate.get("source_category"))
    if category_error:
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].source_category"
    local_identity_error = local_file_without_url_missing_identity(candidate)
    if local_identity_error:
        return ARTIFACT_INVALID, (
            f"candidate_claims_schema_error:candidate[{idx}].{local_identity_error}"
        )
    if not _candidate_claim_has_source_identity(candidate):
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].source_url_or_source_path"
    if not _candidate_claim_has_source_date(candidate):
        return ARTIFACT_INVALID, (
            f"candidate_claims_schema_error:candidate[{idx}].published_at_or_retrieved_at"
        )
    if not _non_empty_scalar(candidate.get("confidence")):
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].confidence"
    for field in ("source_id", "source_path"):
        value = candidate.get(field)
        if value is not None and not _non_empty_string(value):
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].{field}"
    candidate_id = candidate.get("candidate_id")
    if candidate_id is not None:
        if not _non_empty_string(candidate_id):
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].candidate_id"
        normalized_id = candidate_id.strip()
        if normalized_id in seen_ids:
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:duplicate_candidate_id:{normalized_id}"
        seen_ids.add(normalized_id)
    return ARTIFACT_VALID, "valid_candidate_claims_schema"


def _candidate_claim_has_source_identity(candidate: dict[str, Any]) -> bool:
    return _non_empty_string(candidate.get("source_url")) or _non_empty_string(
        candidate.get("source_path")
    )


def _candidate_claim_has_source_date(candidate: dict[str, Any]) -> bool:
    return _non_empty_string(candidate.get("published_at")) or _non_empty_string(
        candidate.get("retrieved_at")
    )


def _non_empty_scalar(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip())) or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _validate_screened_candidates_payload(
    payload: Any,
    *,
    artifact_paths: Mapping[str, Path] | None = None,
) -> tuple[str, str]:
    if isinstance(payload, list):
        return _validate_legacy_screened_candidates(payload)
    if isinstance(payload, dict):
        status, result = _validate_contract_screened_candidates(payload)
        if status != ARTIFACT_VALID:
            return status, result
        candidate_path = (
            artifact_paths.get("candidate_claims")
            if artifact_paths is not None
            else None
        )
        if artifact_paths is not None and candidate_path is None:
            return ARTIFACT_INVALID, "screened_candidates_schema_error:candidate_claims_binding_missing"
        universe_error = _screened_candidates_candidate_universe_error(
            payload,
            candidate_path=candidate_path,
        )
        if universe_error:
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:{universe_error}"
        return status, result
    return ARTIFACT_INVALID, "screened_candidates_schema_error:not_list_or_object"


def _validate_legacy_screened_candidates(payload: list[Any]) -> tuple[str, str]:
    for idx, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}]"
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}].candidate_id"
        status = candidate.get("screening_status")
        if not isinstance(status, str) or status.strip() not in _SCREENING_STATUSES:
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}].screening_status"
        if status.strip() in _SCREENING_STATUSES_REQUIRING_REASON:
            has_reason = any(
                _non_empty_string(candidate.get(field))
                for field in ("reason", "screening_reason", "excluded_reason")
            )
            if not has_reason:
                return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}].reason"

    return ARTIFACT_VALID, "valid_screened_candidates_schema"


def _validate_contract_screened_candidates(payload: dict[str, Any]) -> tuple[str, str]:
    selected = payload.get("selected")
    if not isinstance(selected, list):
        return ARTIFACT_INVALID, "screened_candidates_schema_error:selected"
    for idx, candidate in enumerate(selected):
        validation_error = _selected_screened_candidate_error(candidate)
        if validation_error:
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:selected[{idx}].{validation_error}"

    screening_policy = payload.get("screening_policy")
    if not isinstance(screening_policy, dict) or not screening_policy:
        return ARTIFACT_INVALID, "screened_candidates_schema_error:screening_policy"

    total_candidates, total_error = _screened_candidates_total(payload, screening_policy)
    if total_error:
        return ARTIFACT_INVALID, f"screened_candidates_schema_error:{total_error}"

    has_discard_bucket = False
    for bucket in ("excluded", "deprioritized"):
        entries = payload.get(bucket)
        if entries is None:
            continue
        if not isinstance(entries, list):
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}"
        has_discard_bucket = True
        for idx, candidate in enumerate(entries):
            if not _valid_screened_candidate_entry(candidate):
                return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}[{idx}]"
            if not _screened_candidate_reason_code(candidate):
                return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}[{idx}].reason_code"
            if not _screened_candidate_has_short_explanation(candidate):
                return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}[{idx}].explanation"
    if not has_discard_bucket:
        return ARTIFACT_INVALID, "screened_candidates_schema_error:excluded_or_deprioritized"

    if total_candidates is not None:
        discard_count = _screened_candidates_discard_count(payload)
        expected_discards = total_candidates - len(selected)
        if expected_discards < 0:
            return ARTIFACT_INVALID, "screened_candidates_schema_error:total_candidates"
        if expected_discards > 0 and discard_count == 0:
            return ARTIFACT_INVALID, "screened_candidates_schema_error:discard_audit_missing"
        if len(selected) + discard_count != total_candidates:
            return ARTIFACT_INVALID, "screened_candidates_schema_error:discard_audit_count"

    return ARTIFACT_VALID, "valid_screened_candidates_schema"


def _selected_screened_candidate_error(candidate: Any) -> str | None:
    if not isinstance(candidate, dict):
        return "entry"
    for field in ("statement", "evidence_text"):
        if not _non_empty_string(candidate.get(field)):
            return field
    if source_url_error(candidate.get("source_url")):
        return "source_url"
    if source_category_error(candidate.get("source_category")):
        return "source_category"
    local_identity_error = local_file_without_url_missing_identity(candidate)
    if local_identity_error:
        return local_identity_error
    if not _screened_candidate_has_source_identity(candidate):
        return "source_id_or_source_url_or_source_path"
    if not _candidate_claim_has_source_date(candidate):
        return "published_at_or_retrieved_at"
    return None


def _screened_candidate_has_source_identity(candidate: dict[str, Any]) -> bool:
    return any(
        _non_empty_string(candidate.get(field))
        for field in ("source_id", "source_url", "source_path")
    )


def _valid_screened_candidate_entry(candidate: Any) -> bool:
    if not isinstance(candidate, dict):
        return False
    return any(_non_empty_string(candidate.get(field)) for field in ("candidate_id", "statement", "claim"))


def _screened_candidate_reason_code(candidate: dict[str, Any]) -> str:
    for field in (
        "reason_code",
        "screening_reason_code",
        "excluded_reason_code",
        "deprioritized_reason_code",
    ):
        code = _normalize_screening_reason_code(candidate.get(field))
        if code:
            return code
    return ""


def _normalize_screening_reason_code(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().lower()
    if not raw:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if normalized in _SCREENING_DISCARD_REASON_CODES:
        return normalized
    return _SCREENING_DISCARD_REASON_ALIASES.get(normalized, "")


def _screened_candidate_has_short_explanation(candidate: dict[str, Any]) -> bool:
    code = _screened_candidate_reason_code(candidate)
    for field in (
        "explanation",
        "short_explanation",
        "screening_explanation",
        "reason_explanation",
        "screening_reason",
        "excluded_reason",
        "deprioritized_reason",
    ):
        value = candidate.get(field)
        if not _non_empty_string(value):
            continue
        if _normalize_screening_reason_code(value) == code:
            continue
        return True
    return False


def _screened_candidates_total(
    payload: dict[str, Any],
    screening_policy: dict[str, Any],
) -> tuple[int | None, str | None]:
    total_values: list[int] = []
    for container, prefix in (
        (payload, ""),
        (screening_policy, "screening_policy."),
    ):
        for key in (
            "total_candidates",
            "candidate_count",
            "input_candidate_count",
            "found_candidate_count",
        ):
            value = container.get(key)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return None, f"{prefix}{key}"
            total_values.append(value)
    if not total_values:
        return None, None
    first = total_values[0]
    if any(value != first for value in total_values[1:]):
        return None, "total_candidates_mismatch"
    return first, None


def _screened_candidates_discard_count(payload: dict[str, Any]) -> int:
    count = 0
    for bucket in ("excluded", "deprioritized"):
        entries = payload.get(bucket)
        if isinstance(entries, list):
            count += len(entries)
    return count


def _screened_candidates_candidate_universe_error(
    payload: dict[str, Any],
    *,
    candidate_path: Path | None,
) -> str | None:
    if candidate_path is None:
        return None
    screening_policy = payload.get("screening_policy")
    if not isinstance(screening_policy, dict):
        return None
    declared_total, total_error = _screened_candidates_total(payload, screening_policy)
    if total_error:
        return None

    candidate_payload = _read_json_payload(candidate_path)
    if not isinstance(candidate_payload, list):
        return None
    candidate_status, _ = _validate_candidate_claims_payload(candidate_payload)
    if candidate_status != ARTIFACT_VALID:
        return None

    if declared_total is not None and declared_total != len(candidate_payload):
        return "candidate_universe_count_mismatch"

    candidate_ids = _candidate_claim_ids(candidate_payload)
    if candidate_ids is None:
        return None

    screened_ids, screened_id_error = _screened_candidate_ids(payload, candidate_ids)
    if screened_id_error:
        return screened_id_error

    if declared_total is None:
        return None

    if screened_ids != candidate_ids:
        return "candidate_universe_id_coverage_mismatch"
    return None


def _screened_candidate_ids(
    payload: dict[str, Any],
    candidate_ids: set[str],
) -> tuple[set[str], str | None]:
    screened_ids: set[str] = set()
    for bucket in ("selected", "excluded", "deprioritized"):
        entries = payload.get(bucket)
        if not isinstance(entries, list):
            continue
        for idx, candidate in enumerate(entries):
            if not isinstance(candidate, dict):
                continue
            candidate_id = candidate.get("candidate_id")
            if not _non_empty_string(candidate_id):
                return screened_ids, f"{bucket}[{idx}].candidate_id"
            normalized_id = candidate_id.strip()
            if normalized_id not in candidate_ids:
                return screened_ids, f"{bucket}[{idx}].unknown_candidate_id:{normalized_id}"
            if normalized_id in screened_ids:
                return screened_ids, f"duplicate_screened_candidate_id:{normalized_id}"
            screened_ids.add(normalized_id)
    return screened_ids, None


def _candidate_claim_ids(payload: list[Any]) -> set[str] | None:
    ids: set[str] = set()
    for candidate in payload:
        if not isinstance(candidate, dict):
            return None
        candidate_id = candidate.get("candidate_id")
        if not _non_empty_string(candidate_id):
            return None
        ids.add(candidate_id.strip())
    return ids


def _validate_input_classification_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "input_classification_schema_error:not_object"

    workspace = _workspace_root_for_input_classification(artifact_path)
    for bucket in sorted(_INPUT_CLASSIFICATION_BUCKETS):
        entries = payload.get(bucket)
        if entries is None:
            continue
        if not isinstance(entries, list):
            return ARTIFACT_INVALID, f"input_classification_schema_error:{bucket}"
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            for key, value in entry.items():
                if key not in _INPUT_CLASSIFICATION_PATH_KEYS or not isinstance(value, str):
                    continue
                if _input_classification_path_is_unsafe(value, workspace=workspace):
                    return ARTIFACT_INVALID, f"input_classification_schema_error:{bucket}[{idx}].{key}"

    return ARTIFACT_VALID, "valid_input_classification_schema"


def _validate_source_evidence_pack_manifest_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "source_evidence_pack_manifest_schema_error:not_object"
    violations = SourceEvidencePackManifestContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"source_evidence_pack_manifest_schema_error:{first.field}"

    workspace = artifact_path.parents[2]
    reason = validate_source_evidence_pack_manifest(
        manifest_payload=payload,
        workspace=workspace,
    )
    if reason:
        return ARTIFACT_INVALID, f"{SOURCE_EVIDENCE_PACK_VALIDATION_PREFIX}:{reason}"
    return ARTIFACT_VALID, "experimental_source_evidence_pack_manifest"


def _validate_evidence_extract_source_lock_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "evidence_extract_source_lock_schema_error:not_object"
    if payload.get("schema_version") != "briefloop.evidence_extract_source_lock.v1":
        return ARTIFACT_INVALID, "evidence_extract_source_lock_schema_error:schema_version"
    if payload.get("report_pack") != "evidence_extract":
        return ARTIFACT_INVALID, "evidence_extract_source_lock_schema_error:report_pack"
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        return ARTIFACT_INVALID, "evidence_extract_source_lock_schema_error:sources"
    source_count = payload.get("source_count")
    if not isinstance(source_count, int) or source_count != len(sources):
        return ARTIFACT_INVALID, "evidence_extract_source_lock_schema_error:source_count"

    workspace = artifact_path.parents[2]
    seen_ids: set[str] = set()
    for idx, source in enumerate(sources):
        if not isinstance(source, dict):
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:sources[{idx}]"
        source_id = source.get("source_id")
        if not _non_empty_string(source_id):
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:sources[{idx}].source_id"
        normalized_id = source_id.strip()
        if normalized_id in seen_ids:
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:duplicate_source_id:{normalized_id}"
        seen_ids.add(normalized_id)

        rel_path = source.get("path")
        if not _non_empty_string(rel_path):
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:{normalized_id}.path"
        source_path, path_reason = _evidence_extract_locked_source_path(
            workspace=workspace,
            rel_path=rel_path.strip(),
        )
        if path_reason:
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_validation_error:{path_reason}:{normalized_id}"
        assert source_path is not None
        if not source_path.exists() or not source_path.is_file():
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_validation_error:source_file_missing:{normalized_id}"

        expected_sha = source.get("source_sha256")
        if not _non_empty_string(expected_sha):
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:{normalized_id}.source_sha256"
        if _sha256_file(source_path) != expected_sha.strip():
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_validation_error:source_sha256_mismatch:{normalized_id}"

        expected_size = source.get("source_size_bytes")
        if not isinstance(expected_size, int) or expected_size < 0:
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:{normalized_id}.source_size_bytes"
        try:
            actual_size = source_path.stat().st_size
        except OSError:
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_validation_error:source_file_unreadable:{normalized_id}"
        if actual_size != expected_size:
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_validation_error:source_size_mismatch:{normalized_id}"

        if source.get("lock_status") != "locked_source_bytes":
            return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:{normalized_id}.lock_status"

        derived = source.get("derived_markdown")
        if derived is not None:
            derived_reason = _evidence_extract_derived_markdown_error(
                workspace=workspace,
                source=source,
                normalized_id=normalized_id,
            )
            if derived_reason:
                return ARTIFACT_INVALID, f"evidence_extract_source_lock_validation_error:{derived_reason}:{normalized_id}"
            if source.get("registered_only") is True:
                return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:{normalized_id}.registered_only"
            if source.get("text_evidence_basis") != "mineru_derived_markdown":
                return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:{normalized_id}.text_evidence_basis"
            if source.get("text_evidence_path") != derived.get("path"):
                return ARTIFACT_INVALID, f"evidence_extract_source_lock_schema_error:{normalized_id}.text_evidence_path"

    return ARTIFACT_VALID, "experimental_evidence_extract_source_lock"


def _evidence_extract_derived_markdown_error(
    *,
    workspace: Path,
    source: dict[str, Any],
    normalized_id: str,
) -> str | None:
    derived = source.get("derived_markdown")
    if not isinstance(derived, dict):
        return "derived_markdown"
    if derived.get("derivation") != "mineru_adjacent_markdown":
        return "derived_markdown_derivation"
    if derived.get("extractor") != "mineru":
        return "derived_markdown_extractor"
    if derived.get("source_path") != source.get("path"):
        return "derived_markdown_source_path"
    rel_path = derived.get("path")
    if not _non_empty_string(rel_path):
        return "derived_markdown_path"
    derived_path, path_reason = _evidence_extract_locked_source_path(
        workspace=workspace,
        rel_path=rel_path.strip(),
    )
    if path_reason:
        return "derived_markdown_path_unsafe"
    assert derived_path is not None
    if derived_path.suffix.lower() != ".md":
        return "derived_markdown_extension"
    if not derived_path.exists() or not derived_path.is_file():
        return "derived_markdown_missing"
    expected_sha = derived.get("sha256")
    if not _non_empty_string(expected_sha):
        return "derived_markdown_sha256"
    if _sha256_file(derived_path) != expected_sha.strip():
        return "derived_markdown_sha256_mismatch"
    expected_size = derived.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size < 0:
        return "derived_markdown_size_bytes"
    try:
        actual_size = derived_path.stat().st_size
    except OSError:
        return "derived_markdown_unreadable"
    if actual_size != expected_size:
        return "derived_markdown_size_mismatch"
    filename = derived.get("filename")
    if _non_empty_string(filename) and filename.strip() != derived_path.name:
        return "derived_markdown_filename"
    _ = normalized_id
    return None


def _evidence_extract_locked_source_path(*, workspace: Path, rel_path: str) -> tuple[Path | None, str | None]:
    normalized = rel_path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(rel_path)
    if rel_path.startswith("~") or Path(rel_path).is_absolute() or windows_path.drive:
        return None, "source_path_unsafe"
    if ".." in posix_path.parts or not normalized.startswith("input/sources/evidence_extract/"):
        return None, "source_path_unsafe"
    workspace_root = workspace.resolve(strict=False)
    input_root = workspace / "input"
    sources_root = input_root / "sources"
    evidence_root = sources_root / "evidence_extract"
    for root in (input_root, sources_root, evidence_root):
        if root.is_symlink():
            return None, "source_path_unsafe"
        try:
            root.resolve(strict=False).relative_to(workspace_root)
        except ValueError:
            return None, "source_path_unsafe"
    raw_source_path = workspace / normalized
    if raw_source_path.is_symlink():
        return None, "source_path_unsafe"
    source_path = raw_source_path.resolve(strict=False)
    try:
        source_path.relative_to(evidence_root.resolve(strict=False))
        source_path.relative_to(workspace_root)
    except ValueError:
        return None, "source_path_unsafe"
    return source_path, None


def _validate_evidence_extract_page_inventory_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:not_object"
    if payload.get("schema_version") != "briefloop.evidence_extract_page_inventory.v1":
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:schema_version"
    if payload.get("report_pack") != "evidence_extract":
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:report_pack"
    if payload.get("source_lock_path") != "output/intermediate/evidence_extract_source_lock.json":
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:source_lock_path"

    lock_path = artifact_path.with_name("evidence_extract_source_lock.json")
    try:
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_validation_error:source_lock_missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_validation_error:source_lock_unreadable"
    lock_status, lock_reason = _validate_evidence_extract_source_lock_payload(lock_payload, artifact_path=lock_path)
    if lock_status != ARTIFACT_VALID:
        return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:source_lock_invalid:{lock_reason}"

    expected_lock_sha = payload.get("source_lock_sha256")
    if not _non_empty_string(expected_lock_sha):
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:source_lock_sha256"
    if _sha256_file(lock_path) != expected_lock_sha.strip():
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_validation_error:source_lock_sha256_mismatch"

    lock_sources = lock_payload.get("sources")
    if not isinstance(lock_sources, list):
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_validation_error:source_lock_sources"
    lock_by_id = {
        str(source.get("source_id") or "").strip(): source
        for source in lock_sources
        if isinstance(source, dict) and _non_empty_string(source.get("source_id"))
    }
    workspace = artifact_path.parents[2]

    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:sources"
    if payload.get("source_count") != len(sources):
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:source_count"
    seen_source_ids: set[str] = set()
    total_pages = 0
    inventory_sources = 0
    for idx, source in enumerate(sources):
        if not isinstance(source, dict):
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:sources[{idx}]"
        source_id = source.get("source_id")
        if not _non_empty_string(source_id):
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:sources[{idx}].source_id"
        normalized_id = source_id.strip()
        if normalized_id in seen_source_ids:
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:duplicate_source_id:{normalized_id}"
        seen_source_ids.add(normalized_id)
        lock_source = lock_by_id.get(normalized_id)
        if lock_source is None:
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:unknown_source_id:{normalized_id}"
        if source.get("source_path") != lock_source.get("path"):
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:source_path_mismatch:{normalized_id}"
        if source.get("source_sha256") != lock_source.get("source_sha256"):
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:source_sha256_mismatch:{normalized_id}"
        locked_path, path_reason = _evidence_extract_locked_source_path(
            workspace=workspace,
            rel_path=str(lock_source.get("path") or ""),
        )
        if path_reason:
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:{path_reason}:{normalized_id}"
        assert locked_path is not None
        locked_source_text = ""
        locked_text_reason: str | None = None
        text_basis = "utf8_text_file"
        text_source_path = str(lock_source.get("path") or "")
        text_source_sha256 = str(lock_source.get("source_sha256") or "")
        text_needs_visual_inspection = False
        if lock_source.get("registered_only") is not True:
            (
                locked_source_text,
                locked_text_reason,
                text_basis,
                text_source_path,
                text_source_sha256,
                text_needs_visual_inspection,
            ) = _evidence_extract_inventory_text_binding(
                workspace=workspace,
                lock_source=lock_source,
                locked_path=locked_path,
            )
            if lock_source.get("derived_markdown") is not None or source.get("text_source_path") is not None:
                if source.get("text_source_path") != text_source_path:
                    return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:text_source_path_mismatch:{normalized_id}"
                if source.get("text_source_sha256") != text_source_sha256:
                    return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:text_source_sha256_mismatch:{normalized_id}"
                if source.get("text_evidence_basis") != text_basis:
                    return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:text_evidence_basis_mismatch:{normalized_id}"

        status = source.get("inventory_status")
        if status not in {
            "text_logical_page_seeded",
            "text_empty_no_pages",
            "unsupported_source_format_registered_only",
        }:
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.inventory_status"
        pages = source.get("pages")
        if not isinstance(pages, list):
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.pages"
        page_count = source.get("page_count")
        if not isinstance(page_count, int) or page_count != len(pages):
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.page_count"
        if status != "text_logical_page_seeded" and pages:
            return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.pages_for_unsupported_source"
        if status == "text_logical_page_seeded":
            if len(pages) != 1:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.logical_page_count"
            inventory_sources += 1
            if locked_text_reason:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:{locked_text_reason}:{normalized_id}"
            if not locked_source_text.strip():
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:source_text_empty:{normalized_id}"
        elif status == "text_empty_no_pages":
            if locked_text_reason:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:{locked_text_reason}:{normalized_id}"
            if locked_source_text.strip():
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:source_text_not_empty:{normalized_id}"
            if source.get("needs_external_extraction_tool") is not False:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.needs_external_extraction_tool"
            if source.get("visual_inspection_required") is not text_needs_visual_inspection:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.visual_inspection_required"
        elif status == "unsupported_source_format_registered_only":
            if lock_source.get("registered_only") is not True and not locked_text_reason:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_validation_error:source_text_supported:{normalized_id}"
            if source.get("needs_external_extraction_tool") is not True:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.needs_external_extraction_tool"
            if source.get("visual_inspection_required") is not True:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.visual_inspection_required"
        total_pages += len(pages)
        seen_page_ids: set[str] = set()
        for page_idx, page in enumerate(pages):
            page_reason = _evidence_extract_page_entry_error(
                page,
                source_id=normalized_id,
                page_idx=page_idx,
                seen_page_ids=seen_page_ids,
                expected_char_end=len(locked_source_text) if status == "text_logical_page_seeded" else None,
                expected_page_basis=text_basis if status == "text_logical_page_seeded" else None,
                expected_needs_visual_inspection=(
                    text_needs_visual_inspection if status == "text_logical_page_seeded" else None
                ),
            )
            if page_reason:
                return ARTIFACT_INVALID, f"evidence_extract_page_inventory_schema_error:{normalized_id}.{page_reason}"

    if seen_source_ids != set(lock_by_id):
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_validation_error:source_universe_mismatch"
    if payload.get("page_count") != total_pages:
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:page_count"
    if payload.get("inventory_source_count") != inventory_sources:
        return ARTIFACT_INVALID, "evidence_extract_page_inventory_schema_error:inventory_source_count"
    return ARTIFACT_VALID, "experimental_evidence_extract_page_inventory"


def _evidence_extract_page_entry_error(
    page: Any,
    *,
    source_id: str,
    page_idx: int,
    seen_page_ids: set[str],
    expected_char_end: int | None,
    expected_page_basis: str | None = None,
    expected_needs_visual_inspection: bool | None = None,
) -> str | None:
    if not isinstance(page, dict):
        return f"pages[{page_idx}]"
    page_id = page.get("page_id")
    expected_page_id = f"PAGE-{source_id}-{page_idx + 1:03d}"
    if page_id != expected_page_id:
        return f"pages[{page_idx}].page_id"
    if page_id in seen_page_ids:
        return f"duplicate_page_id:{page_id}"
    seen_page_ids.add(page_id)
    if page.get("page_number") != page_idx + 1:
        return f"pages[{page_idx}].page_number"
    if page.get("page_label") != f"logical-page-{page_idx + 1}":
        return f"pages[{page_idx}].page_label"
    if expected_page_basis is not None and page.get("page_basis") != expected_page_basis:
        return f"pages[{page_idx}].page_basis"
    if page.get("has_searchable_text") is not True:
        return f"pages[{page_idx}].has_searchable_text"
    if (
        expected_needs_visual_inspection is not None
        and page.get("needs_visual_inspection") is not expected_needs_visual_inspection
    ):
        return f"pages[{page_idx}].needs_visual_inspection"
    char_start = page.get("char_start")
    char_end = page.get("char_end")
    if not isinstance(char_start, int) or char_start < 0:
        return f"pages[{page_idx}].char_start"
    if not isinstance(char_end, int) or char_end < char_start:
        return f"pages[{page_idx}].char_end"
    if expected_char_end is not None and (char_start != 0 or char_end != expected_char_end):
        return f"pages[{page_idx}].char_range"
    return None


def _evidence_extract_inventory_text_binding(
    *,
    workspace: Path,
    lock_source: dict[str, Any],
    locked_path: Path,
) -> tuple[str, str | None, str, str, str, bool]:
    derived = lock_source.get("derived_markdown")
    if isinstance(derived, dict):
        rel_path = str(derived.get("path") or "")
        derived_path, path_reason = _evidence_extract_locked_source_path(
            workspace=workspace,
            rel_path=rel_path,
        )
        if path_reason or derived_path is None:
            return "", "derived_markdown_path_unsafe", "mineru_derived_markdown", rel_path, "", True
        text, reason = _evidence_extract_inventory_text_for_source(derived_path)
        return (
            text,
            reason,
            "mineru_derived_markdown",
            rel_path,
            str(derived.get("sha256") or ""),
            True,
        )

    text, reason = _evidence_extract_inventory_text_for_source(locked_path)
    return (
        text,
        reason,
        "utf8_text_file",
        str(lock_source.get("path") or ""),
        str(lock_source.get("source_sha256") or ""),
        False,
    )


def _evidence_extract_inventory_text_for_source(path: Path) -> tuple[str, str | None]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "", "source_text_unreadable"
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text, None
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            return payload["content"], None
    return raw_text, None


def _validate_human_approval_ledger_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    from multi_agent_brief.product.release_approval import (
        validate_human_approval_ledger_event_links,
        validate_human_approval_ledger_payload,
    )

    reason = validate_human_approval_ledger_payload(payload)
    if reason:
        return ARTIFACT_INVALID, reason
    workspace = artifact_path.parents[2]
    link_reason = validate_human_approval_ledger_event_links(payload, workspace=workspace)
    if link_reason:
        return ARTIFACT_INVALID, link_reason
    return ARTIFACT_VALID, "experimental_human_approval_ledger"


def _validate_release_readiness_report_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    from multi_agent_brief.product.release_approval import (
        validate_release_readiness_report_event_link,
        validate_release_readiness_report_payload,
    )

    reason = validate_release_readiness_report_payload(payload)
    if reason:
        return ARTIFACT_INVALID, reason
    workspace = artifact_path.parents[2]
    link_reason = validate_release_readiness_report_event_link(payload, workspace=workspace)
    if link_reason:
        return ARTIFACT_INVALID, link_reason
    return ARTIFACT_VALID, "experimental_release_readiness_report"


def _validate_quality_panel_payload(payload: Any) -> tuple[str, str]:
    reason = validate_quality_panel_payload(payload)
    if reason:
        return ARTIFACT_INVALID, reason
    return ARTIFACT_VALID, "experimental_quality_panel"


def _validate_quality_summary_markdown(text: str, *, artifact_path: Path) -> tuple[str, str]:
    reason = validate_quality_summary_markdown(text)
    if reason:
        return ARTIFACT_INVALID, reason
    panel_path = artifact_path.with_name("quality_panel.json")
    if not panel_path.exists():
        return ARTIFACT_INVALID, "quality_summary_validation_error:quality_panel_missing"
    try:
        panel_payload = json.loads(panel_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ARTIFACT_INVALID, "quality_summary_validation_error:quality_panel_unreadable"
    except json.JSONDecodeError:
        return ARTIFACT_INVALID, "quality_summary_validation_error:quality_panel_parse_error"
    if not isinstance(panel_payload, dict):
        return ARTIFACT_INVALID, "quality_summary_validation_error:quality_panel_invalid:not_object"
    panel_reason = validate_quality_panel_payload(panel_payload)
    if panel_reason:
        return ARTIFACT_INVALID, f"quality_summary_validation_error:quality_panel_invalid:{panel_reason}"
    try:
        expected = render_quality_summary(panel_payload, quality_panel_sha256=_sha256_file(panel_path))
    except QualityPanelError as exc:
        return ARTIFACT_INVALID, f"quality_summary_validation_error:render:{exc}"
    if text != expected:
        return ARTIFACT_INVALID, "quality_summary_validation_error:stale_or_hand_edited"
    return ARTIFACT_VALID, "experimental_quality_summary_markdown"


def _validate_quality_panel_html(text: str, *, artifact_path: Path) -> tuple[str, str]:
    reason = validate_quality_panel_html(text)
    if reason:
        return ARTIFACT_INVALID, reason
    panel_path = artifact_path.with_name("quality_panel.json")
    if not panel_path.exists():
        return ARTIFACT_INVALID, "quality_panel_html_validation_error:quality_panel_missing"
    try:
        panel_payload = json.loads(panel_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ARTIFACT_INVALID, "quality_panel_html_validation_error:quality_panel_unreadable"
    except json.JSONDecodeError:
        return ARTIFACT_INVALID, "quality_panel_html_validation_error:quality_panel_parse_error"
    if not isinstance(panel_payload, dict):
        return ARTIFACT_INVALID, "quality_panel_html_validation_error:quality_panel_invalid:not_object"
    panel_reason = validate_quality_panel_payload(panel_payload)
    if panel_reason:
        return ARTIFACT_INVALID, f"quality_panel_html_validation_error:quality_panel_invalid:{panel_reason}"
    try:
        expected = render_quality_panel_html(panel_payload, quality_panel_sha256=_sha256_file(panel_path))
    except QualityPanelError as exc:
        return ARTIFACT_INVALID, f"quality_panel_html_validation_error:render:{exc}"
    if text != expected:
        return ARTIFACT_INVALID, "quality_panel_html_validation_error:stale_or_hand_edited"
    return ARTIFACT_VALID, "experimental_quality_panel_html"


def _workspace_root_for_input_classification(artifact_path: Path) -> Path | None:
    if artifact_path.name == "input_classification.json" and artifact_path.parent.name == "output":
        return artifact_path.parent.parent
    return None


def _input_classification_path_is_unsafe(value: str, *, workspace: Path | None) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if raw.startswith("~"):
        return True
    normalized = raw.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(raw)
    if ".." in posix_path.parts or ".." in windows_path.parts:
        return True
    path = Path(raw)
    if path.is_absolute():
        if workspace is None:
            return True
        try:
            path.resolve(strict=False).relative_to(workspace.resolve(strict=False))
        except ValueError:
            return True
        return False
    if windows_path.drive:
        return True
    return False


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_claim_ledger_payload(payload: Any) -> tuple[str, str]:
    try:
        claims = ClaimLedger._claim_items_from_json(payload)
    except ValueError as exc:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{exc}"

    seen_ids: set[str] = set()
    for idx, claim in enumerate(claims):
        for field in ("claim_id", "statement", "source_id", "evidence_text"):
            value = claim.get(field)
            if not isinstance(value, str) or not value.strip():
                return ARTIFACT_INVALID, f"claim_ledger_schema_error:claim[{idx}].{field}"
        claim_id = str(claim["claim_id"]).strip()
        if claim_id in seen_ids:
            return ARTIFACT_INVALID, f"claim_ledger_schema_error:duplicate_claim_id:{claim_id}"
        seen_ids.add(claim_id)
        violations = ClaimContract.validate(claim)
        errors = [violation for violation in violations if violation.severity == "error"]
        if errors:
            first = errors[0]
            return ARTIFACT_INVALID, f"claim_ledger_schema_error:claim[{idx}].{first.field}"

    try:
        ledger = ClaimLedger([Claim.from_dict(item) for item in claims])
    except (TypeError, ValueError) as exc:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{exc}"
    errors = ledger.validate_claims()
    if errors:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{errors[0]}"
    return ARTIFACT_VALID, "valid_claim_ledger_schema"


def _validate_claim_drafts_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "claim_drafts_schema_error:not_object"
    violations = ClaimDraftContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"claim_drafts_schema_error:{first.field}"
    return ARTIFACT_VALID, "valid_claim_drafts_schema"


def _validate_atomic_claim_graph_payload(
    payload: Any,
    *,
    artifact_path: Path,
    artifact_paths: Mapping[str, Path] | None = None,
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "atomic_claim_graph_schema_error:not_object"
    violations = AtomicClaimGraphContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"atomic_claim_graph_schema_error:{first.field}"

    ledger_path = (
        artifact_paths.get("claim_ledger")
        if artifact_paths is not None
        else artifact_path.with_name("claim_ledger.json")
    )
    if ledger_path is None:
        return ARTIFACT_INVALID, f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:claim_ledger_binding_missing"
    try:
        ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger_claims = ClaimLedger._claim_items_from_json(ledger_payload)
    except FileNotFoundError:
        return ARTIFACT_INVALID, f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:claim_ledger_missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return ARTIFACT_INVALID, f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:claim_ledger_unreadable:{exc}"

    reason = validate_atomic_claim_graph_against_ledger(
        graph_payload=payload,
        ledger_claims=ledger_claims,
    )
    if reason:
        return ARTIFACT_INVALID, f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:{reason}"

    return ARTIFACT_VALID, "experimental_atomic_claim_graph_schema"


def _validate_evidence_span_registry_payload(
    payload: Any,
    *,
    artifact_path: Path,
    artifact_paths: Mapping[str, Path] | None = None,
    workspace: Path | None = None,
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "evidence_span_registry_schema_error:not_object"
    violations = EvidenceSpanRegistryContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"evidence_span_registry_schema_error:{first.field}"

    workspace = workspace or artifact_path.parents[2]
    if artifact_paths is not None:
        page_inventory_path = artifact_paths.get("evidence_extract_page_inventory")
        if page_inventory_path is None:
            return (
                ARTIFACT_INVALID,
                f"{EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX}:"
                "evidence_extract_page_inventory_binding_missing",
            )
    else:
        page_inventory_path = artifact_path.with_name("evidence_extract_page_inventory.json")
    page_inventory_payload = _valid_evidence_extract_page_inventory_payload_for_span_registry(
        page_inventory_path
    )
    reason = validate_evidence_span_registry_against_source_pack(
        registry_payload=payload,
        workspace=workspace,
        page_inventory_payload=page_inventory_payload,
    )
    if reason:
        return ARTIFACT_INVALID, f"{EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX}:{reason}"

    return ARTIFACT_VALID, "experimental_evidence_span_registry_schema"


def _valid_evidence_extract_page_inventory_payload_for_span_registry(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    status, _validation_result = _validate_evidence_extract_page_inventory_payload(payload, artifact_path=path)
    if status != ARTIFACT_VALID:
        return None
    return payload if isinstance(payload, dict) else None


def _validate_claim_support_matrix_payload(
    payload: Any,
    *,
    artifact_path: Path,
    artifact_paths: Mapping[str, Path] | None = None,
    workspace: Path | None = None,
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "claim_support_matrix_schema_error:not_object"
    violations = ClaimSupportMatrixContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"claim_support_matrix_schema_error:{first.field}"

    if artifact_paths is not None:
        dependency_paths = {
            artifact_id: artifact_paths.get(artifact_id)
            for artifact_id in (
                "claim_ledger",
                "atomic_claim_graph",
                "evidence_span_registry",
            )
        }
        missing_binding = next(
            (artifact_id for artifact_id, path in dependency_paths.items() if path is None),
            None,
        )
        if missing_binding is not None:
            return (
                ARTIFACT_INVALID,
                f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{missing_binding}_binding_missing",
            )
        ledger_path = dependency_paths["claim_ledger"]
        graph_path = dependency_paths["atomic_claim_graph"]
        evidence_path = dependency_paths["evidence_span_registry"]
    else:
        ledger_path = artifact_path.with_name("claim_ledger.json")
        graph_path = artifact_path.with_name("atomic_claim_graph.json")
        evidence_path = artifact_path.with_name("evidence_span_registry.json")
    assert ledger_path is not None
    assert graph_path is not None
    assert evidence_path is not None
    ledger_claims, reason = _claim_support_matrix_ledger_claims(ledger_path)
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"
    graph_payload, reason = _claim_support_matrix_atomic_graph_payload(
        graph_path,
        artifact_paths=artifact_paths,
    )
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"
    evidence_payload, reason = _claim_support_matrix_evidence_span_registry_payload(
        evidence_path,
        artifact_paths=artifact_paths,
        workspace=workspace,
    )
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"

    reason = validate_claim_support_matrix_against_artifacts(
        matrix_payload=payload,
        ledger_claims=ledger_claims or [],
        graph_payload=graph_payload or {},
        evidence_span_registry_payload=evidence_payload or {},
    )
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"
    return ARTIFACT_VALID, "experimental_claim_support_matrix_schema"


def _validate_semantic_assessment_report_payload(
    payload: Any,
    *,
    artifact_path: Path,
    artifact_paths: Mapping[str, Path] | None = None,
    workspace: Path | None = None,
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "semantic_assessment_report_schema_error:not_object"
    violations = SemanticAssessmentReportContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"semantic_assessment_report_schema_error:{first.field}"

    if artifact_paths is not None:
        dependency_paths = {
            artifact_id: artifact_paths.get(artifact_id)
            for artifact_id in (
                "claim_ledger",
                "atomic_claim_graph",
                "evidence_span_registry",
            )
        }
        missing_binding = next(
            (artifact_id for artifact_id, path in dependency_paths.items() if path is None),
            None,
        )
        if missing_binding is not None:
            return (
                ARTIFACT_INVALID,
                f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:"
                f"{missing_binding}_binding_missing",
            )
        ledger_path = dependency_paths["claim_ledger"]
        graph_path = dependency_paths["atomic_claim_graph"]
        evidence_path = dependency_paths["evidence_span_registry"]
    else:
        ledger_path = artifact_path.with_name("claim_ledger.json")
        graph_path = artifact_path.with_name("atomic_claim_graph.json")
        evidence_path = artifact_path.with_name("evidence_span_registry.json")
    assert ledger_path is not None
    assert graph_path is not None
    assert evidence_path is not None
    ledger_claims, reason = _claim_support_matrix_ledger_claims(ledger_path)
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"
    graph_payload, reason = _claim_support_matrix_atomic_graph_payload(
        graph_path,
        artifact_paths=artifact_paths,
    )
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"
    evidence_payload, reason = _claim_support_matrix_evidence_span_registry_payload(
        evidence_path,
        artifact_paths=artifact_paths,
        workspace=workspace,
    )
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"

    reason = validate_semantic_assessment_report_against_artifacts(
        report_payload=payload,
        ledger_claims=ledger_claims or [],
        graph_payload=graph_payload or {},
        evidence_span_registry_payload=evidence_payload or {},
    )
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"
    workspace = workspace or (
        artifact_path.parent.parent.parent
        if artifact_path.parent.name == "intermediate"
        else artifact_path.parent
    )
    reason = validate_semantic_assessment_checked_inputs_for_workspace(
        report_payload=payload,
        workspace=workspace,
    )
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"
    return ARTIFACT_VALID, "experimental_semantic_assessment_report_schema"


def _validate_semantic_support_acceptance_ledger_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    reason = validate_semantic_support_acceptance_ledger_for_workspace(payload, artifact_path=artifact_path)
    if reason:
        return ARTIFACT_INVALID, f"semantic_support_acceptance_ledger_schema_error:{reason}"
    return ARTIFACT_VALID, "experimental_semantic_support_acceptance_ledger"


def _claim_support_matrix_ledger_claims(path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "claim_ledger_missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"claim_ledger_unreadable:{exc}"
    status, validation_result = _validate_claim_ledger_payload(payload)
    if status != ARTIFACT_VALID:
        return None, _dependency_invalid_reason(
            "claim_ledger",
            validation_result,
            prefixes=("claim_ledger_schema_error",),
        )
    try:
        claims = ClaimLedger._claim_items_from_json(payload)
    except ValueError as exc:
        return None, f"claim_ledger_unreadable:{exc}"
    return claims, None


def _claim_support_matrix_atomic_graph_payload(
    path: Path,
    *,
    artifact_paths: Mapping[str, Path] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    payload, reason = _read_claim_support_matrix_json(path, missing_reason="atomic_claim_graph_missing")
    if reason:
        return None, reason
    assert payload is not None
    status, validation_result = _validate_atomic_claim_graph_payload(
        payload,
        artifact_path=path,
        artifact_paths=artifact_paths,
    )
    if status != ARTIFACT_VALID:
        return None, _dependency_invalid_reason(
            "atomic_claim_graph",
            validation_result,
            prefixes=("atomic_claim_graph_schema_error", ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX),
        )
    return payload, None


def _claim_support_matrix_evidence_span_registry_payload(
    path: Path,
    *,
    artifact_paths: Mapping[str, Path] | None = None,
    workspace: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    payload, reason = _read_claim_support_matrix_json(path, missing_reason="evidence_span_registry_missing")
    if reason:
        return None, reason
    assert payload is not None
    status, validation_result = _validate_evidence_span_registry_payload(
        payload,
        artifact_path=path,
        artifact_paths=artifact_paths,
        workspace=workspace,
    )
    if status != ARTIFACT_VALID:
        return None, _dependency_invalid_reason(
            "evidence_span_registry",
            validation_result,
            prefixes=("evidence_span_registry_schema_error", EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX),
        )
    return payload, None


def _read_claim_support_matrix_json(path: Path, *, missing_reason: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, missing_reason
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{missing_reason.removesuffix('_missing')}_unreadable:{exc}"


def _dependency_invalid_reason(label: str, validation_result: str, *, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        marker = f"{prefix}:"
        if validation_result.startswith(marker):
            return f"{label}_invalid:{validation_result.removeprefix(marker)}"
    return f"{label}_invalid:{validation_result}"


def _validate_audit_report_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "audit_report_schema_error:not_object"
    violations = AuditReportContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"audit_report_schema_error:{first.field}"
    findings = payload.get("findings")
    if findings is not None and not isinstance(findings, list):
        return ARTIFACT_INVALID, "audit_report_schema_error:findings"
    for idx, finding in enumerate(findings or []):
        if not isinstance(finding, dict):
            return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}]"
        for field in ("finding_id", "severity", "finding_type", "description"):
            value = finding.get(field)
            if not isinstance(value, str) or not value.strip():
                return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}].{field}"
        if finding.get("severity") not in {"low", "medium", "high"}:
            return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}].severity"
    return ARTIFACT_VALID, "valid_audit_report_schema"


def _artifact_record(
    *,
    workspace: Path,
    artifact: dict[str, Any],
    workflow: dict[str, Any],
    recovery_state: Mapping[str, Any] | None = None,
    intake_result: IntakeResult | None = None,
    artifact_paths: Mapping[str, Path],
) -> dict[str, Any]:
    artifact_id = str(artifact.get("artifact_id") or "")
    rel_path = str(artifact.get("path") or "")
    fmt = str(artifact.get("format") or "")
    producer_stage = str(artifact.get("producer_stage") or "")
    path = artifact_paths[artifact_id]
    status, validation_result = _validate_artifact(
        path,
        fmt,
        artifact_id,
        workspace=workspace,
        intake_result=intake_result,
        artifact_paths=artifact_paths,
    )

    activated_optional = optional_feedback_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    ) or quality_gate_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    ) or provenance_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    )
    if (
        status == ARTIFACT_EXPECTED
        and _stage_is_complete_or_skipped(workflow, producer_stage)
        and (bool(artifact.get("required", False)) or activated_optional)
    ):
        status = ARTIFACT_MISSING
        validation_result = "missing"

    blocking_reason = ""
    if status == ARTIFACT_MISSING:
        blocking_reason = f"Producer stage '{producer_stage}' completed but '{rel_path}' is missing."
    elif status == ARTIFACT_INVALID:
        blocking_reason = f"Artifact '{rel_path}' failed minimum {fmt} validation."

    size_bytes = path.stat().st_size if path.exists() and path.is_file() else None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat() if path.exists() else None
    sha256 = _sha256_file(path) if path.exists() and path.is_file() else None
    if intake_result is not None and sha256 != intake_result.raw_sha256:
        status = ARTIFACT_INVALID
        validation_result = "intake_projection_raw_sha_mismatch"
        blocking_reason = (
            f"Artifact '{rel_path}' changed while deterministic intake was being evaluated."
        )
    stale_metadata = _recovery_stale_metadata(
        recovery_state=recovery_state,
        artifact_id=artifact_id,
    )
    stale_baseline_sha256 = None
    baseline = stale_metadata.get("baseline") if stale_metadata else None
    if isinstance(baseline, Mapping):
        baseline_sha = baseline.get("sha256")
        if isinstance(baseline_sha, str) and baseline_sha:
            stale_baseline_sha256 = baseline_sha
    if (
        stale_metadata
        and stale_baseline_sha256
        and sha256 == stale_baseline_sha256
        and path.exists()
        and path.is_file()
        and status == ARTIFACT_VALID
    ):
        status = ARTIFACT_STALE
        stale_after_supersede = (
            stale_metadata.get("recovery_event_type") == "repair_stage_superseded"
        )
        validation_result = "stale_after_supersede" if stale_after_supersede else "stale_after_repair"
        revision_tx = (
            stale_metadata.get("recovery_transaction_id")
            or "<unknown>"
        )
        revision_owner = (
            stale_metadata.get("owner_stage")
            or "<unknown>"
        )
        revision_kind = "supersede" if stale_after_supersede else "repair"
        blocking_reason = (
            f"Artifact '{rel_path}' was produced before owner-stage {revision_kind} "
            f"{revision_tx} by '{revision_owner}'; rerun producer stage '{producer_stage}' "
            "before consuming it."
        )
    record = {
        "artifact_id": artifact_id,
        "path": rel_path,
        "format": fmt,
        "required": bool(artifact.get("required", False)),
        "producer_stage": producer_stage,
        "producer_role": artifact.get("producer_role", ""),
        "consumer_stages": artifact.get("consumer_stages", []),
        "status": status,
        "validation_result": validation_result,
        "blocking_reason": blocking_reason,
        "allowed_decisions": artifact.get("allowed_decisions", []),
        "retry_or_human_review_decision": artifact.get("retry_or_human_review_decision", ""),
        "size_bytes": size_bytes,
        "mtime": mtime,
        "sha256": sha256,
    }
    if status == ARTIFACT_STALE and stale_baseline_sha256:
        record["stale_baseline_sha256"] = stale_baseline_sha256
    if intake_result is not None:
        record["intake_projection"] = intake_result.projection()
    return record


def _recovery_stale_metadata(
    *,
    recovery_state: Mapping[str, Any] | None,
    artifact_id: str,
) -> dict[str, Any] | None:
    if not isinstance(recovery_state, Mapping):
        return None
    owner_revision = recovery_state.get("owner_revision")
    authority = owner_revision if isinstance(owner_revision, Mapping) else recovery_state
    baselines = authority.get("stale_artifact_baselines")
    if not isinstance(baselines, Mapping):
        return None
    baseline = baselines.get(artifact_id)
    if not isinstance(baseline, Mapping):
        return None
    return {
        "baseline": baseline,
        "recovery_event_type": authority.get("event_type") or recovery_state.get("recovery_event_type"),
        "recovery_transaction_id": authority.get("transaction_id") or recovery_state.get("recovery_transaction_id"),
        "owner_stage": authority.get("owner_stage") or recovery_state.get("owner_stage"),
    }


def _build_artifact_registry(
    *,
    workspace: Path,
    run_id: str,
    artifacts: list[dict[str, Any]],
    workflow: dict[str, Any],
    updated_at: str,
    recovery_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if recovery_state is None:
        from multi_agent_brief.orchestrator.recovery_state import (
            evaluate_recovery_state,
        )

        recovery_state = evaluate_recovery_state(workspace=workspace)
    artifacts_by_id = {
        str(artifact.get("artifact_id")): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
    artifact_paths = artifact_paths_from_contracts(workspace, artifacts_by_id)
    intake_results = _agent_intake_results(
        workspace=workspace,
        artifact_paths=artifact_paths,
    )
    records = {
        str(artifact.get("artifact_id")): _artifact_record(
            workspace=workspace,
            artifact=artifact,
            workflow=workflow,
            recovery_state=recovery_state,
            intake_result=intake_results.get(str(artifact.get("artifact_id") or "")),
            artifact_paths=artifact_paths,
        )
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
    return {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "run_id": run_id,
        "updated_at": updated_at,
        "artifacts": records,
    }


def _agent_intake_results(
    *,
    workspace: Path,
    artifact_paths: Mapping[str, Path],
) -> dict[str, IntakeResult]:
    artifact_ids: tuple[AgentArtifactId, ...] = (
        "candidate_claims",
        "screened_candidates",
        "claim_drafts",
    )
    bundle = evaluate_workspace_agent_artifact_intakes(
        workspace,
        artifact_paths={
            artifact_id: artifact_paths[artifact_id]
            for artifact_id in artifact_ids
            if artifact_id in artifact_paths
        },
    )
    return {
        artifact_id: result
        for artifact_id in artifact_ids
        if (result := bundle.get(artifact_id)) is not None
    }


def _read_json_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def interpret_frozen_artifact_integrity(
    *,
    old_registry: dict[str, Any] | None,
    registry: dict[str, Any],
    workflow: dict[str, Any],
    artifacts: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    mutating_stage: str | None = None,
    exempt_artifact_ids: set[str] | None = None,
) -> FrozenArtifactIntegrityVerdict:
    reasons: list[str] = []
    if old_registry is not None:
        old_records_raw = old_registry.get("artifacts")
        if not isinstance(old_records_raw, dict):
            return _degraded_frozen_artifact_integrity(
                "artifact_registry.json artifacts must be an object before frozen integrity can be verified."
            )
        old_records = old_records_raw
    else:
        old_records = {}
    new_records = registry.get("artifacts")
    if not isinstance(new_records, dict):
        return _degraded_frozen_artifact_integrity(
            "artifact_registry.json artifacts must be an object before frozen integrity can be verified."
        )
    mutating_stage_produces = {
        str(item)
        for stage in stages
        if str(stage.get("stage_id") or "") == str(mutating_stage or "")
        for item in (stage.get("produces") or [])
    }
    mutation_exempt_artifacts = {
        *mutating_stage_produces,
        *(str(item) for item in (exempt_artifact_ids or set()) if item),
    }
    for artifact in artifacts:
        artifact_id = str(artifact.get("artifact_id") or "")
        if not artifact_id:
            continue
        if _artifact_is_non_workflow_projection(artifact):
            continue
        if artifact_id in mutation_exempt_artifacts:
            continue
        producer_stage = str(artifact.get("producer_stage") or "")
        old_record = old_records.get(artifact_id) or {}
        old_sha = old_record.get("sha256")
        if not old_sha:
            continue
        producer_verdict = interpret_stage_completion(workflow, producer_stage)
        if producer_verdict.kind != "canonical":
            return _degraded_frozen_artifact_integrity(
                f"Cannot verify frozen artifact '{artifact_id}' because producer stage "
                f"'{producer_stage}' status is malformed: {' '.join(producer_verdict.reasons)}"
            )
        producer_projection = project_stage_completion_for_read(producer_verdict)
        if producer_projection.get("complete_or_skipped") is not True:
            continue
        new_record = new_records.get(artifact_id) or {}
        new_sha = new_record.get("sha256")
        path = str(new_record.get("path") or old_record.get("path") or artifact.get("path") or artifact_id)
        if new_record.get("status") == ARTIFACT_MISSING or not new_sha:
            reasons.append(
                f"Frozen artifact '{path}' from owner stage '{producer_stage}' is missing after stage-complete; route repair back to the owner stage."
                f" {FROZEN_ARTIFACT_CONTROL_FILE_GUIDANCE}"
            )
        elif new_sha != old_sha:
            reason = (
                f"Frozen artifact '{path}' from owner stage '{producer_stage}' changed after stage-complete; "
                "route repair back to the owner stage instead of downstream in-place conversion. "
                f"{FROZEN_ARTIFACT_CONTROL_FILE_GUIDANCE}"
            )
            if artifact_id == "claim_ledger":
                reason = f"{reason} {CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE}"
            reasons.append(reason)
    if reasons:
        return FrozenArtifactIntegrityVerdict(
            kind="degraded",
            value={"status": "changed", "matched": False, "contaminates_run": True, "reasons": reasons},
            reasons=tuple(reasons),
            contaminates_run=True,
        )
    return FrozenArtifactIntegrityVerdict(
        kind="canonical",
        value={"status": "matched", "matched": True, "contaminates_run": False, "reasons": []},
    )


def _artifact_is_non_workflow_projection(artifact: Mapping[str, Any]) -> bool:
    if str(artifact.get("producer_kind") or "workflow_stage") == "workflow_stage":
        return False
    if bool(artifact.get("required", False)):
        return False
    consumers = artifact.get("consumer_stages")
    return not isinstance(consumers, list) or not consumers


def project_frozen_artifact_integrity_for_read(verdict: FrozenArtifactIntegrityVerdict) -> dict[str, Any]:
    """Return the read-side projection for frozen artifact integrity."""

    return dict(verdict.value)


def require_frozen_artifact_integrity_pass(verdict: FrozenArtifactIntegrityVerdict) -> list[str]:
    """Return integrity blockers for write paths; empty means pass."""

    if verdict.kind == "canonical":
        return []
    return list(verdict.reasons)


def _degraded_frozen_artifact_integrity(reason: str) -> FrozenArtifactIntegrityVerdict:
    return FrozenArtifactIntegrityVerdict(
        kind="degraded",
        value={"status": "unknown", "matched": False, "contaminates_run": False, "reasons": [reason]},
        reasons=(reason,),
    )


def _changed_artifact_events(
    *,
    old_registry: dict[str, Any] | None,
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    old_records = ((old_registry or {}).get("artifacts") or {})
    events: list[dict[str, Any]] = []
    for artifact_id, record in (registry.get("artifacts") or {}).items():
        old_record = old_records.get(artifact_id) or {}
        observed_changed = (
            record.get("status") in {ARTIFACT_VALID, ARTIFACT_INVALID}
            and (
                old_record.get("status") != record.get("status")
                or old_record.get("size_bytes") != record.get("size_bytes")
                or old_record.get("mtime") != record.get("mtime")
            )
        )
        if observed_changed:
            events.append({
                "event_type": "artifact_observed",
                "artifact_id": str(artifact_id),
                "metadata": {
                    "path": record.get("path"),
                    "size_bytes": record.get("size_bytes"),
                    "mtime": record.get("mtime"),
                },
            })

        validated_changed = (
            record.get("status") in {
                ARTIFACT_PRESENT,
                ARTIFACT_VALID,
                ARTIFACT_INVALID,
                ARTIFACT_MISSING,
                ARTIFACT_STALE,
            }
            and (
                old_record.get("status") != record.get("status")
                or old_record.get("validation_result") != record.get("validation_result")
                or old_record.get("blocking_reason") != record.get("blocking_reason")
            )
        )
        if validated_changed:
            events.append({
                "event_type": "artifact_validated",
                "artifact_id": str(artifact_id),
                "reason": str(record.get("blocking_reason") or ""),
                "metadata": {
                    "path": record.get("path"),
                    "status": record.get("status"),
                    "validation_result": record.get("validation_result"),
                },
            })
    return events


def _artifact_registry_sha(
    registry: dict[str, Any],
    artifact_id: str,
) -> str:
    record = ((registry.get("artifacts") or {}).get(artifact_id) or {})
    sha256 = str(record.get("sha256") or "")
    if not sha256:
        path = str(record.get("path") or artifact_id)
        raise RuntimeStateError(
            f"Artifact '{artifact_id}' has no frozen sha256 in artifact_registry.json.",
            details={"artifact_id": artifact_id, "path": path},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return sha256


def _artifact_registry_path(
    registry: dict[str, Any],
    artifact_id: str,
    default: str,
) -> str:
    record = ((registry.get("artifacts") or {}).get(artifact_id) or {})
    return str(record.get("path") or default)
