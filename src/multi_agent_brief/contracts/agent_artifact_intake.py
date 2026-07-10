"""Deterministic intake for agent-authored workflow artifact proposals."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from multi_agent_brief.contracts.schemas.claim import VALID_CLAIM_TYPES, VALID_CONFIDENCE
from multi_agent_brief.contracts.schemas.claim_draft import (
    CLAIM_DRAFT_ALLOWED_VALUES,
    CLAIM_DRAFT_FORBIDDEN_FIELDS,
    DRAFT_REQUIRED_FIELD_ORDER,
    ClaimDraftContract,
)
from multi_agent_brief.contracts.source_metadata import (
    local_file_without_url_missing_identity,
    normalize_source_category,
    source_category_error,
    source_url_error,
)


AgentArtifactId = Literal["candidate_claims", "screened_candidates", "claim_drafts"]

AGENT_ARTIFACT_IDS = frozenset({"candidate_claims", "screened_candidates", "claim_drafts"})
AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION = "briefloop.agent_artifact_intake.v1"
INTAKE_PROJECTION_SCHEMA_VERSION = "briefloop.intake_projection.v1"

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


@dataclass(frozen=True)
class NormalizationResult:
    """Pure-kernel result before exact-byte identity is attached."""

    normalized_payload: Any | None
    normalizations: tuple[dict[str, Any], ...] = ()
    findings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class IntakeResult:
    """One deterministic interpretation of an exact raw artifact file."""

    artifact_id: AgentArtifactId
    status: Literal["valid", "invalid"]
    transform_version: str
    raw_sha256: str
    normalized_sha256: str
    normalized_payload: Any | None
    normalizations: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]

    @property
    def normalization_count(self) -> int:
        return len(self.normalizations)

    @property
    def fatal_finding_count(self) -> int:
        return sum(1 for finding in self.findings if finding.get("severity") == "fatal")

    @property
    def validation_result(self) -> str:
        if self.status == "valid":
            suffix = "_normalized" if self.normalization_count else ""
            return f"valid_{self.artifact_id}_schema{suffix}"
        if self.findings:
            value = self.findings[0].get("validation_result")
            if isinstance(value, str) and value:
                return value
        return f"{self.artifact_id}_schema_error:invalid"

    def projection(self) -> dict[str, Any]:
        """Return the persisted projection without normalized artifact bytes."""

        return {
            "schema_version": INTAKE_PROJECTION_SCHEMA_VERSION,
            "transform_version": self.transform_version,
            "raw_sha256": self.raw_sha256,
            "normalized_sha256": self.normalized_sha256,
            "normalization_count": self.normalization_count,
            "fatal_finding_count": self.fatal_finding_count,
            "normalizations": [dict(item) for item in self.normalizations],
            "findings": [dict(item) for item in self.findings],
        }


@dataclass(frozen=True)
class WorkspaceAgentArtifactIntakes:
    """One dependency-ordered interpretation of workspace agent proposals."""

    candidate_claims: IntakeResult | None = None
    screened_candidates: IntakeResult | None = None
    claim_drafts: IntakeResult | None = None

    def get(self, artifact_id: AgentArtifactId) -> IntakeResult | None:
        """Return the intake result for one declared agent artifact."""

        return getattr(self, artifact_id)


def agent_artifact_paths_from_contracts(
    workspace: Path,
    artifacts_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[AgentArtifactId, Path]:
    """Resolve the one contract-bound path map used by intake consumers."""

    paths: dict[AgentArtifactId, Path] = {}
    for artifact_id in AGENT_ARTIFACT_IDS:
        path = artifact_path_from_contracts(
            workspace,
            artifacts_by_id,
            artifact_id=artifact_id,
        )
        if path is not None:
            paths[artifact_id] = path
    return paths


def artifact_path_from_contracts(
    workspace: Path,
    artifacts_by_id: Mapping[str, Mapping[str, Any]],
    *,
    artifact_id: str,
    default_path: Path | None = None,
) -> Path | None:
    """Resolve one artifact path from the authoritative contract map."""

    artifact = artifacts_by_id.get(artifact_id)
    if isinstance(artifact, Mapping) and _non_empty_string(artifact.get("path")):
        return workspace / str(artifact["path"])
    if default_path is not None:
        return workspace / default_path
    return None


def evaluate_agent_artifact_intake(
    path: Path,
    *,
    artifact_id: AgentArtifactId,
    candidate_universe: IntakeResult | None = None,
) -> IntakeResult:
    """Read and interpret one exact raw agent artifact proposal."""

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        return _unparsed_result(
            artifact_id,
            raw_sha256="",
            code="read_error",
            message=f"Artifact could not be read: {type(exc).__name__}.",
        )

    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return _unparsed_result(
            artifact_id,
            raw_sha256=raw_sha256,
            code="decode_error",
            message="Artifact must be UTF-8 JSON.",
        )
    if not text.strip():
        return _unparsed_result(
            artifact_id,
            raw_sha256=raw_sha256,
            code="empty",
            message="Artifact must not be empty.",
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _unparsed_result(
            artifact_id,
            raw_sha256=raw_sha256,
            code="parse_error",
            message="Artifact must contain valid JSON.",
        )

    if artifact_id == "candidate_claims":
        kernel = normalize_candidate_claims(payload)
    elif artifact_id == "screened_candidates":
        kernel = normalize_screened_candidates(payload, candidate_universe=candidate_universe)
    else:
        kernel = normalize_claim_drafts(payload)

    if kernel.normalized_payload is None:
        return IntakeResult(
            artifact_id=artifact_id,
            status="invalid",
            transform_version=AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION,
            raw_sha256=raw_sha256,
            normalized_sha256="",
            normalized_payload=None,
            normalizations=kernel.normalizations,
            findings=kernel.findings,
        )

    try:
        normalized_bytes = canonical_normalized_json_bytes(kernel.normalized_payload)
    except (TypeError, ValueError):
        finding = _finding(
            artifact_id,
            code="non_canonical_json",
            path="<root>",
            message="Normalized payload contains a non-canonical JSON value.",
        )
        return IntakeResult(
            artifact_id=artifact_id,
            status="invalid",
            transform_version=AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION,
            raw_sha256=raw_sha256,
            normalized_sha256="",
            normalized_payload=kernel.normalized_payload,
            normalizations=kernel.normalizations,
            findings=(*kernel.findings, finding),
        )

    strict_result = _strict_validation_result(
        artifact_id=artifact_id,
        payload=kernel.normalized_payload,
        candidate_universe=candidate_universe,
    )
    findings = list(kernel.findings)
    if strict_result is not None:
        findings.append(_finding_from_validation_result(artifact_id, strict_result))
    status: Literal["valid", "invalid"] = (
        "invalid" if any(item.get("severity") == "fatal" for item in findings) else "valid"
    )
    return IntakeResult(
        artifact_id=artifact_id,
        status=status,
        transform_version=AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION,
        raw_sha256=raw_sha256,
        normalized_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
        normalized_payload=kernel.normalized_payload,
        normalizations=kernel.normalizations,
        findings=tuple(findings),
    )


def evaluate_workspace_agent_artifact_intakes(
    workspace: Path,
    *,
    artifact_paths: Mapping[AgentArtifactId, Path] | None = None,
) -> WorkspaceAgentArtifactIntakes:
    """Evaluate workspace proposals once in their required dependency order.

    ``screened_candidates`` is always interpreted against the candidate result
    produced by this bundle evaluation. Callers may override contract-resolved
    paths, but they cannot provide a consumer-local candidate interpretation.
    Missing proposal files have no result; their owning artifact consumer keeps
    responsibility for missing/not-a-file status semantics.
    """

    paths: dict[AgentArtifactId, Path] = {
        "candidate_claims": workspace / "output" / "intermediate" / "candidate_claims.json",
        "screened_candidates": workspace / "output" / "intermediate" / "screened_candidates.json",
        "claim_drafts": workspace / "output" / "intermediate" / "claim_drafts.json",
    }
    if artifact_paths:
        paths.update({artifact_id: Path(path) for artifact_id, path in artifact_paths.items()})

    candidate_result = _evaluate_existing_agent_artifact(
        paths["candidate_claims"],
        artifact_id="candidate_claims",
    )
    screened_result = _evaluate_existing_agent_artifact(
        paths["screened_candidates"],
        artifact_id="screened_candidates",
        candidate_universe=candidate_result
        or _unparsed_result(
            "candidate_claims",
            raw_sha256="",
            code="missing_dependency",
            message="candidate_claims is required to validate screened_candidates.",
        ),
    )
    claim_drafts_result = _evaluate_existing_agent_artifact(
        paths["claim_drafts"],
        artifact_id="claim_drafts",
    )
    return WorkspaceAgentArtifactIntakes(
        candidate_claims=candidate_result,
        screened_candidates=screened_result,
        claim_drafts=claim_drafts_result,
    )


def _evaluate_existing_agent_artifact(
    path: Path,
    *,
    artifact_id: AgentArtifactId,
    candidate_universe: IntakeResult | None = None,
) -> IntakeResult | None:
    if not path.exists():
        return None
    return evaluate_agent_artifact_intake(
        path,
        artifact_id=artifact_id,
        candidate_universe=candidate_universe,
    )


def canonical_normalized_json_bytes(payload: Any) -> bytes:
    """Serialize the one canonical normalized artifact identity."""

    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def normalize_candidate_claims(payload: Any) -> NormalizationResult:
    """Normalize bounded mechanical drift in candidate claims."""

    normalizations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    value = copy.deepcopy(payload)
    if isinstance(value, dict):
        claims = value.get("claims")
        if not isinstance(claims, list):
            return _root_shape_failure("candidate_claims", "not_list")
        value = claims
        normalizations.append(
            _normalization("root_wrapper", "<root>.claims", "claims", "<root>")
        )
    if not isinstance(value, list):
        return _root_shape_failure("candidate_claims", "not_list")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        _normalize_record_aliases(
            item,
            path=f"candidate[{index}]",
            artifact_id="candidate_claims",
            normalizations=normalizations,
            findings=findings,
        )
    return NormalizationResult(value, tuple(normalizations), tuple(findings))


def normalize_screened_candidates(
    payload: Any,
    candidate_universe: IntakeResult | None = None,
) -> NormalizationResult:
    """Normalize bounded mechanical drift in screened candidates."""

    normalizations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    value = copy.deepcopy(payload)
    if not isinstance(value, (dict, list)):
        return _root_shape_failure("screened_candidates", "not_list_or_object")
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict):
                _normalize_record_aliases(
                    item,
                    path=f"candidate[{index}]",
                    artifact_id="screened_candidates",
                    normalizations=normalizations,
                    findings=findings,
                )
        return NormalizationResult(value, tuple(normalizations), tuple(findings))

    _apply_alias(
        value,
        canonical="selected",
        alias="selected_candidates",
        path="<root>",
        artifact_id="screened_candidates",
        normalizations=normalizations,
        findings=findings,
    )
    _apply_alias(
        value,
        canonical="excluded",
        alias="excluded_candidates",
        path="<root>",
        artifact_id="screened_candidates",
        normalizations=normalizations,
        findings=findings,
    )
    for bucket in ("selected", "excluded", "deprioritized"):
        entries = value.get(bucket)
        if not isinstance(entries, list):
            continue
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            _normalize_record_aliases(
                item,
                path=f"{bucket}[{index}]",
                artifact_id="screened_candidates",
                normalizations=normalizations,
                findings=findings,
            )
    return NormalizationResult(value, tuple(normalizations), tuple(findings))


def normalize_claim_drafts(payload: Any) -> NormalizationResult:
    """Normalize bounded mechanical drift in pre-freeze claim drafts."""

    normalizations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    value = copy.deepcopy(payload)
    if not isinstance(value, dict):
        return _root_shape_failure("claim_drafts", "not_object")
    _apply_alias(
        value,
        canonical="drafts",
        alias="claim_drafts",
        path="<root>",
        artifact_id="claim_drafts",
        normalizations=normalizations,
        findings=findings,
    )
    drafts = value.get("drafts")
    if isinstance(drafts, list):
        for index, draft in enumerate(drafts):
            if not isinstance(draft, dict):
                continue
            _normalize_record_aliases(
                draft,
                path=f"drafts[{index}]",
                artifact_id="claim_drafts",
                normalizations=normalizations,
                findings=findings,
            )
    return NormalizationResult(value, tuple(normalizations), tuple(findings))


def validate_intake_projection(
    projection: Any,
    *,
    result: IntakeResult | None = None,
) -> list[str]:
    """Validate a persisted projection and optionally bind it to an evaluator result."""

    if not isinstance(projection, dict):
        return ["intake_projection must be an object"]
    reasons: list[str] = []
    if projection.get("schema_version") != INTAKE_PROJECTION_SCHEMA_VERSION:
        reasons.append("intake_projection schema_version is unsupported")
    if projection.get("transform_version") != AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION:
        reasons.append("intake_projection transform_version is unsupported")
    for field in ("raw_sha256", "normalized_sha256"):
        if not isinstance(projection.get(field), str):
            reasons.append(f"intake_projection {field} must be a string")
    raw_sha256 = projection.get("raw_sha256")
    if isinstance(raw_sha256, str) and not re.fullmatch(r"[0-9a-f]{64}", raw_sha256):
        reasons.append("intake_projection raw_sha256 must be a lowercase SHA-256 digest")
    normalized_sha256 = projection.get("normalized_sha256")
    if (
        isinstance(normalized_sha256, str)
        and normalized_sha256
        and not re.fullmatch(r"[0-9a-f]{64}", normalized_sha256)
    ):
        reasons.append("intake_projection normalized_sha256 must be empty or a lowercase SHA-256 digest")
    for field in ("normalization_count", "fatal_finding_count"):
        value = projection.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            reasons.append(f"intake_projection {field} must be a non-negative integer")
    for field in ("normalizations", "findings"):
        if not isinstance(projection.get(field), list):
            reasons.append(f"intake_projection {field} must be a list")
    normalizations = projection.get("normalizations")
    if isinstance(normalizations, list):
        if any(not isinstance(item, dict) for item in normalizations):
            reasons.append("intake_projection normalizations entries must be objects")
        for index, item in enumerate(normalizations):
            if not isinstance(item, dict):
                continue
            for field in ("operation", "path"):
                if not _non_empty_string(item.get(field)):
                    reasons.append(
                        f"intake_projection normalizations[{index}].{field} must be a non-empty string"
                    )
            for field in ("source", "target"):
                if field not in item:
                    reasons.append(
                        f"intake_projection normalizations[{index}].{field} is required"
                    )
        normalization_count = projection.get("normalization_count")
        if isinstance(normalization_count, int) and not isinstance(normalization_count, bool):
            if normalization_count != len(normalizations):
                reasons.append("intake_projection normalization_count does not match normalizations")
    findings = projection.get("findings")
    if isinstance(findings, list):
        if any(not isinstance(item, dict) for item in findings):
            reasons.append("intake_projection findings entries must be objects")
        for index, item in enumerate(findings):
            if not isinstance(item, dict):
                continue
            if item.get("artifact_id") not in AGENT_ARTIFACT_IDS:
                reasons.append(
                    f"intake_projection findings[{index}].artifact_id is unsupported"
                )
            if item.get("severity") != "fatal":
                reasons.append(
                    f"intake_projection findings[{index}].severity must be fatal"
                )
            for field in ("code", "path", "message", "validation_result"):
                if not _non_empty_string(item.get(field)):
                    reasons.append(
                        f"intake_projection findings[{index}].{field} must be a non-empty string"
                    )
            for field in ("allowed_values", "forbidden_fields", "required_fields"):
                if field in item and not isinstance(item.get(field), list):
                    reasons.append(
                        f"intake_projection findings[{index}].{field} must be a list"
                    )
            if "hint" in item and not _non_empty_string(item.get("hint")):
                reasons.append(
                    f"intake_projection findings[{index}].hint must be a non-empty string"
                )
        fatal_count = sum(
            1
            for item in findings
            if isinstance(item, dict) and item.get("severity") == "fatal"
        )
        projected_fatal_count = projection.get("fatal_finding_count")
        if isinstance(projected_fatal_count, int) and not isinstance(projected_fatal_count, bool):
            if projected_fatal_count != fatal_count:
                reasons.append("intake_projection fatal_finding_count does not match findings")
    if result is not None:
        expected = result.projection()
        for field in (
            "schema_version",
            "transform_version",
            "raw_sha256",
            "normalized_sha256",
            "normalization_count",
            "fatal_finding_count",
            "normalizations",
            "findings",
        ):
            if projection.get(field) != expected.get(field):
                reasons.append(f"intake_projection {field} does not match current intake")
    return reasons


def validate_registry_intake_context(
    registry: Any,
    *,
    expected_run_id: str,
    artifact_id: AgentArtifactId,
    result: IntakeResult | None = None,
) -> list[str]:
    """Bind a persisted intake projection to its registry, run, and raw record."""

    if not isinstance(registry, dict):
        return ["artifact_registry must be an object"]
    reasons: list[str] = []
    if registry.get("run_id") != expected_run_id:
        reasons.append("artifact_registry run_id does not match the current run")
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, dict):
        reasons.append("artifact_registry artifacts must be an object")
        return reasons
    record = artifacts.get(artifact_id)
    if not isinstance(record, dict):
        reasons.append(f"artifact_registry is missing {artifact_id}")
        return reasons
    projection = record.get("intake_projection")
    reasons.extend(validate_intake_projection(projection, result=result))
    if isinstance(projection, dict) and record.get("sha256") != projection.get("raw_sha256"):
        reasons.append("artifact record sha256 does not match intake_projection raw_sha256")
    if isinstance(projection, dict) and isinstance(projection.get("findings"), list):
        for finding in projection["findings"]:
            if isinstance(finding, dict) and finding.get("artifact_id") != artifact_id:
                reasons.append("intake_projection finding artifact_id does not match artifact record")
                break
    fatal_finding_count = projection.get("fatal_finding_count") if isinstance(projection, dict) else None
    record_status = record.get("status")
    if record_status == "valid" and isinstance(projection, dict):
        normalized_sha256 = projection.get("normalized_sha256")
        if not isinstance(normalized_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", normalized_sha256
        ):
            reasons.append("valid artifact record requires a normalized intake digest")
        if isinstance(fatal_finding_count, int) and fatal_finding_count > 0:
            reasons.append("valid artifact record cannot contain fatal intake findings")
    elif record_status == "invalid" and isinstance(fatal_finding_count, int):
        if fatal_finding_count == 0:
            reasons.append("invalid artifact record requires a fatal intake finding")
    return reasons


def _normalize_record_aliases(
    record: dict[str, Any],
    *,
    path: str,
    artifact_id: AgentArtifactId,
    normalizations: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    _apply_alias(
        record,
        canonical="statement",
        alias="claim_statement",
        path=path,
        artifact_id=artifact_id,
        normalizations=normalizations,
        findings=findings,
    )
    _apply_alias(
        record,
        canonical="evidence_text",
        alias="source_excerpt",
        path=path,
        artifact_id=artifact_id,
        normalizations=normalizations,
        findings=findings,
    )
    confidence = record.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        if math.isfinite(float(confidence)) and 0 <= float(confidence) <= 1:
            normalized = (
                "low"
                if float(confidence) < 0.45
                else "medium"
                if float(confidence) < 0.8
                else "high"
            )
            record["confidence"] = normalized
            normalizations.append(
                _normalization(
                    "numeric_confidence_bucket",
                    f"{path}.confidence",
                    confidence,
                    normalized,
                )
            )
    category = record.get("source_category")
    if isinstance(category, str) and category.strip():
        normalized_category = normalize_source_category(category, default="")
        if normalized_category and normalized_category != category.strip():
            record["source_category"] = normalized_category
            normalizations.append(
                _normalization(
                    "source_category_alias",
                    f"{path}.source_category",
                    category,
                    normalized_category,
                )
            )


def _apply_alias(
    record: dict[str, Any],
    *,
    canonical: str,
    alias: str,
    path: str,
    artifact_id: AgentArtifactId,
    normalizations: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    if alias not in record:
        return
    alias_value = record.pop(alias)
    field_path = canonical if path == "<root>" else f"{path}.{canonical}"
    if canonical not in record:
        record[canonical] = alias_value
        normalizations.append(_normalization("field_alias", field_path, alias, canonical))
        return
    canonical_value = record.get(canonical)
    if _equivalent_alias_values(canonical_value, alias_value):
        normalizations.append(
            _normalization("redundant_field_alias", field_path, alias, canonical)
        )
        return
    findings.append(
        _finding(
            artifact_id,
            code="alias_conflict",
            path=field_path,
            message=f"Conflicting values were supplied for {canonical!r} and alias {alias!r}.",
        )
    )


def _equivalent_alias_values(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.strip() == right.strip()
    return left == right


def _normalization(operation: str, path: str, source: Any, target: Any) -> dict[str, Any]:
    return {
        "operation": operation,
        "path": path,
        "source": source,
        "target": target,
    }


def _strict_validation_result(
    *,
    artifact_id: AgentArtifactId,
    payload: Any,
    candidate_universe: IntakeResult | None,
) -> str | None:
    if artifact_id == "candidate_claims":
        return _validate_candidate_claims(payload)
    if artifact_id == "screened_candidates":
        return _validate_screened_candidates(payload, candidate_universe=candidate_universe)
    return _validate_claim_drafts(payload)


def _validate_candidate_claims(payload: Any) -> str | None:
    if not isinstance(payload, list):
        return "candidate_claims_schema_error:not_list"
    seen_ids: set[str] = set()
    for index, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            return f"candidate_claims_schema_error:candidate[{index}]"
        if "statement" not in candidate and ("claim" in candidate or "candidate_id" in candidate):
            error = _legacy_candidate_error(candidate, index=index, seen_ids=seen_ids)
        else:
            error = _contract_candidate_error(candidate, index=index, seen_ids=seen_ids)
        if error:
            return f"candidate_claims_schema_error:{error}"
    return None


def _legacy_candidate_error(
    candidate: dict[str, Any],
    *,
    index: int,
    seen_ids: set[str],
) -> str | None:
    for field in ("candidate_id", "claim", "source_id"):
        if not _non_empty_string(candidate.get(field)):
            return f"candidate[{index}].{field}"
    metadata_error = _provided_candidate_metadata_error(candidate, index=index)
    if metadata_error:
        return metadata_error
    candidate_id = str(candidate["candidate_id"]).strip()
    if candidate_id in seen_ids:
        return f"duplicate_candidate_id:{candidate_id}"
    seen_ids.add(candidate_id)
    return None


def _contract_candidate_error(
    candidate: dict[str, Any],
    *,
    index: int,
    seen_ids: set[str],
) -> str | None:
    for field in ("candidate_id", "statement", "evidence_text", "topic", "claim_type"):
        if not _non_empty_string(candidate.get(field)):
            return f"candidate[{index}].{field}"
    if source_url_error(candidate.get("source_url")):
        return f"candidate[{index}].source_url"
    if source_category_error(candidate.get("source_category")):
        return f"candidate[{index}].source_category"
    local_identity_error = local_file_without_url_missing_identity(candidate)
    if local_identity_error:
        return f"candidate[{index}].{local_identity_error}"
    if not (
        _non_empty_string(candidate.get("source_url"))
        or _non_empty_string(candidate.get("source_path"))
    ):
        return f"candidate[{index}].source_url_or_source_path"
    if not (
        _non_empty_string(candidate.get("published_at"))
        or _non_empty_string(candidate.get("retrieved_at"))
    ):
        return f"candidate[{index}].published_at_or_retrieved_at"
    confidence = candidate.get("confidence")
    if not _non_empty_string(confidence) or confidence not in VALID_CONFIDENCE:
        return f"candidate[{index}].confidence"
    if candidate.get("claim_type") not in VALID_CLAIM_TYPES:
        return f"candidate[{index}].claim_type"
    for field in ("source_id", "source_path"):
        value = candidate.get(field)
        if value is not None and not _non_empty_string(value):
            return f"candidate[{index}].{field}"
    candidate_id = str(candidate["candidate_id"]).strip()
    if candidate_id in seen_ids:
        return f"duplicate_candidate_id:{candidate_id}"
    seen_ids.add(candidate_id)
    return None


def _provided_candidate_metadata_error(
    candidate: dict[str, Any],
    *,
    index: int,
) -> str | None:
    for field, validator in (
        ("source_url", source_url_error),
        ("source_category", source_category_error),
    ):
        if field not in candidate:
            continue
        value = candidate.get(field)
        if not _non_empty_string(value) or validator(value):
            return f"candidate[{index}].{field}"
    for field in ("source_id", "source_path", "published_at", "retrieved_at"):
        if field in candidate and not _non_empty_string(candidate.get(field)):
            return f"candidate[{index}].{field}"
    return None


def _validate_screened_candidates(
    payload: Any,
    *,
    candidate_universe: IntakeResult | None,
) -> str | None:
    if isinstance(payload, list):
        for index, candidate in enumerate(payload):
            if not isinstance(candidate, dict):
                return f"screened_candidates_schema_error:candidate[{index}]"
            if not _non_empty_string(candidate.get("candidate_id")):
                return f"screened_candidates_schema_error:candidate[{index}].candidate_id"
            status = candidate.get("screening_status")
            if not isinstance(status, str) or status.strip() not in _SCREENING_STATUSES:
                return f"screened_candidates_schema_error:candidate[{index}].screening_status"
            if status.strip() in _SCREENING_STATUSES_REQUIRING_REASON and not any(
                _non_empty_string(candidate.get(field))
                for field in ("reason", "screening_reason", "excluded_reason")
            ):
                return f"screened_candidates_schema_error:candidate[{index}].reason"
        universe_error = _legacy_screened_candidate_universe_error(
            payload,
            candidate_universe=candidate_universe,
        )
        if universe_error:
            return f"screened_candidates_schema_error:{universe_error}"
        return None
    if not isinstance(payload, dict):
        return "screened_candidates_schema_error:not_list_or_object"
    selected = payload.get("selected")
    if not isinstance(selected, list):
        return "screened_candidates_schema_error:selected"
    for index, candidate in enumerate(selected):
        error = _selected_candidate_error(candidate)
        if error:
            return f"screened_candidates_schema_error:selected[{index}].{error}"
    policy = payload.get("screening_policy")
    if not isinstance(policy, dict) or not policy:
        return "screened_candidates_schema_error:screening_policy"
    total, total_error = _screened_candidates_total(payload, policy)
    if total_error:
        return f"screened_candidates_schema_error:{total_error}"
    has_discard_bucket = False
    for bucket in ("excluded", "deprioritized"):
        entries = payload.get(bucket)
        if entries is None:
            continue
        if not isinstance(entries, list):
            return f"screened_candidates_schema_error:{bucket}"
        has_discard_bucket = True
        for index, candidate in enumerate(entries):
            if not _valid_screened_candidate_entry(candidate):
                return f"screened_candidates_schema_error:{bucket}[{index}]"
            if not _screened_candidate_reason_code(candidate):
                return f"screened_candidates_schema_error:{bucket}[{index}].reason_code"
            if not _screened_candidate_has_short_explanation(candidate):
                return f"screened_candidates_schema_error:{bucket}[{index}].explanation"
    if not has_discard_bucket:
        return "screened_candidates_schema_error:excluded_or_deprioritized"
    if total is not None:
        discard_count = sum(
            len(payload.get(bucket) or [])
            for bucket in ("excluded", "deprioritized")
            if isinstance(payload.get(bucket), list)
        )
        expected_discards = total - len(selected)
        if expected_discards < 0:
            return "screened_candidates_schema_error:total_candidates"
        if expected_discards > 0 and discard_count == 0:
            return "screened_candidates_schema_error:discard_audit_missing"
        if len(selected) + discard_count != total:
            return "screened_candidates_schema_error:discard_audit_count"
    universe_error = _candidate_universe_error(
        payload,
        declared_total=total,
        candidate_universe=candidate_universe,
    )
    if universe_error:
        return f"screened_candidates_schema_error:{universe_error}"
    return None


def _selected_candidate_error(candidate: Any) -> str | None:
    if not isinstance(candidate, dict):
        return "entry"
    for field in ("candidate_id", "statement", "evidence_text"):
        if not _non_empty_string(candidate.get(field)):
            return field
    if source_url_error(candidate.get("source_url")):
        return "source_url"
    if source_category_error(candidate.get("source_category")):
        return "source_category"
    local_identity_error = local_file_without_url_missing_identity(candidate)
    if local_identity_error:
        return local_identity_error
    if not any(
        _non_empty_string(candidate.get(field))
        for field in ("source_id", "source_url", "source_path")
    ):
        return "source_id_or_source_url_or_source_path"
    if not (
        _non_empty_string(candidate.get("published_at"))
        or _non_empty_string(candidate.get("retrieved_at"))
    ):
        return "published_at_or_retrieved_at"
    claim_type = candidate.get("claim_type")
    if claim_type is not None and claim_type not in VALID_CLAIM_TYPES:
        return "claim_type"
    confidence = candidate.get("confidence")
    if confidence is not None and confidence not in VALID_CONFIDENCE:
        return "confidence"
    return None


def _valid_screened_candidate_entry(candidate: Any) -> bool:
    return isinstance(candidate, dict) and _non_empty_string(
        candidate.get("candidate_id")
    )


def _screened_candidate_reason_code(candidate: dict[str, Any]) -> str:
    for field in (
        "reason_code",
        "screening_reason_code",
        "excluded_reason_code",
        "deprioritized_reason_code",
    ):
        code = _normalize_reason_code(candidate.get(field))
        if code:
            return code
    return ""


def _normalize_reason_code(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
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
        if _normalize_reason_code(value) == code:
            continue
        return True
    return False


def _screened_candidates_total(
    payload: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[int | None, str | None]:
    values: list[int] = []
    for container, prefix in ((payload, ""), (policy, "screening_policy.")):
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
            values.append(value)
    if not values:
        return None, None
    first = values[0]
    if any(value != first for value in values[1:]):
        return None, "total_candidates_mismatch"
    return first, None


def _candidate_universe_error(
    payload: dict[str, Any],
    *,
    declared_total: int | None,
    candidate_universe: IntakeResult | None,
) -> str | None:
    candidate_ids, candidate_error = _bound_candidate_universe_ids(candidate_universe)
    if candidate_error:
        return candidate_error
    if candidate_ids is None:
        return None
    if declared_total is not None and declared_total != len(candidate_ids):
        return "candidate_universe_count_mismatch"
    screened_ids: set[str] = set()
    for bucket in ("selected", "excluded", "deprioritized"):
        entries = payload.get(bucket)
        if not isinstance(entries, list):
            continue
        for index, candidate in enumerate(entries):
            if not isinstance(candidate, dict):
                continue
            candidate_id = candidate.get("candidate_id")
            if not _non_empty_string(candidate_id):
                return f"{bucket}[{index}].candidate_id"
            normalized_id = candidate_id.strip()
            if normalized_id not in candidate_ids:
                return f"{bucket}[{index}].unknown_candidate_id:{normalized_id}"
            if normalized_id in screened_ids:
                return f"duplicate_screened_candidate_id:{normalized_id}"
            screened_ids.add(normalized_id)
    if screened_ids != candidate_ids:
        return "candidate_universe_id_coverage_mismatch"
    return None


def _legacy_screened_candidate_universe_error(
    payload: list[Any],
    *,
    candidate_universe: IntakeResult | None,
) -> str | None:
    candidate_ids, candidate_error = _bound_candidate_universe_ids(candidate_universe)
    if candidate_error:
        return candidate_error
    if candidate_ids is None:
        return None
    screened_ids: set[str] = set()
    for index, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("candidate_id")
        if not _non_empty_string(candidate_id):
            continue
        normalized_id = candidate_id.strip()
        if normalized_id not in candidate_ids:
            return f"candidate[{index}].unknown_candidate_id:{normalized_id}"
        if normalized_id in screened_ids:
            return f"duplicate_screened_candidate_id:{normalized_id}"
        screened_ids.add(normalized_id)
    if screened_ids != candidate_ids:
        return "candidate_universe_id_coverage_mismatch"
    return None


def _bound_candidate_universe_ids(
    candidate_universe: IntakeResult | None,
) -> tuple[set[str] | None, str | None]:
    if candidate_universe is None:
        return None, None
    if not isinstance(candidate_universe.normalized_payload, list):
        return None, "candidate_universe_invalid"
    candidate_ids, candidate_error = _candidate_ids(candidate_universe.normalized_payload)
    if candidate_error:
        return None, candidate_error
    if candidate_universe.status != "valid":
        return None, "candidate_universe_invalid"
    return candidate_ids, None


def _candidate_ids(payload: list[Any]) -> tuple[set[str], str | None]:
    identifiers: set[str] = set()
    for index, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            return identifiers, f"candidate_universe_entry_invalid:{index}"
        candidate_id = candidate.get("candidate_id")
        if not _non_empty_string(candidate_id):
            return identifiers, f"candidate_universe_missing_candidate_id:{index}"
        normalized = candidate_id.strip()
        if normalized in identifiers:
            return identifiers, f"candidate_universe_duplicate_candidate_id:{normalized}"
        identifiers.add(normalized)
    return identifiers, None


def _validate_claim_drafts(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "claim_drafts_schema_error:not_object"
    errors = [
        violation
        for violation in ClaimDraftContract.validate(payload)
        if violation.severity == "error"
    ]
    if errors:
        return f"claim_drafts_schema_error:{errors[0].field}"
    return None


def _root_shape_failure(artifact_id: AgentArtifactId, suffix: str) -> NormalizationResult:
    return NormalizationResult(
        normalized_payload=None,
        findings=(
            _finding(
                artifact_id,
                code="root_shape_invalid",
                path="<root>",
                message=f"{artifact_id} has an unsupported root shape.",
                validation_result=f"{artifact_id}_schema_error:{suffix}",
            ),
        ),
    )


def _unparsed_result(
    artifact_id: AgentArtifactId,
    *,
    raw_sha256: str,
    code: str,
    message: str,
) -> IntakeResult:
    return IntakeResult(
        artifact_id=artifact_id,
        status="invalid",
        transform_version=AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION,
        raw_sha256=raw_sha256,
        normalized_sha256="",
        normalized_payload=None,
        normalizations=(),
        findings=(
            _finding(
                artifact_id,
                code=code,
                path="<root>",
                message=message,
                validation_result=code,
            ),
        ),
    )


def _finding_from_validation_result(
    artifact_id: AgentArtifactId,
    validation_result: str,
) -> dict[str, Any]:
    _prefix, separator, suffix = validation_result.partition(":")
    path = suffix if separator else "<root>"
    finding = _finding(
        artifact_id,
        code="contract_invalid",
        path=path,
        message=validation_result,
        validation_result=validation_result,
    )
    if artifact_id == "claim_drafts":
        field_name = path.rsplit(".", 1)[-1]
        if field_name in CLAIM_DRAFT_ALLOWED_VALUES:
            finding["allowed_values"] = CLAIM_DRAFT_ALLOWED_VALUES[field_name]
        if field_name == "claim_id":
            finding["forbidden_fields"] = CLAIM_DRAFT_FORBIDDEN_FIELDS
            finding["hint"] = "Remove claim_id; Python assigns CL-#### during freeze."
        if field_name in DRAFT_REQUIRED_FIELD_ORDER or path == "drafts":
            finding["required_fields"] = list(DRAFT_REQUIRED_FIELD_ORDER)
    return finding


def _finding(
    artifact_id: AgentArtifactId,
    *,
    code: str,
    path: str,
    message: str,
    validation_result: str | None = None,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "severity": "fatal",
        "code": code,
        "path": path,
        "message": message,
        "validation_result": validation_result
        or f"{artifact_id}_schema_error:{path}",
    }


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
