"""MABW-080 experiment harness validation and metadata registration.

080 validates whether approved Improvement Memory guidance manifests under a
frozen fact layer. Schema validators are side-effect free. ``register-run``
and ``score-run`` write only the requested experiment metadata outputs and must
not mutate workspace runtime state, archive files, case files, agent assets, or
Improvement Ledger files.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from multi_agent_brief.orchestrator.run_integrity import (
    PERSISTED_RUN_INTEGRITY_STATUSES,
    interpret_run_integrity,
    project_for_read,
)


EXPERIMENT_080_ID = "MABW-080"

CASE_MANIFEST_SCHEMA = "mabw.experiment_080.case.v1"
FROZEN_FACT_LAYER_SCHEMA = "mabw.experiment_080.frozen_fact_layer.v1"
GUIDANCE_SET_SCHEMA = "mabw.experiment_080.guidance_set.v1"
RUN_RECORD_SCHEMA = "mabw.experiment_080.run_record.v1"
SCORECARD_SCHEMA = "mabw.experiment_080.scorecard.v1"
CASE_VALIDATION_SCHEMA = "mabw.experiment_080.case_validation.v1"
RUN_ARCHIVE_SCHEMA = "mabw.run_archive.v1"

ALLOWED_CONDITIONS = {"baseline", "memory", "prompt_only"}
ALLOWED_VALIDITY_CLASSES = {
    "A_controlled",
    "B_integration",
    "invalid_contaminated",
    "invalid_incomplete",
    "invalid_fact_layer_mismatch",
}
ALLOWED_RUN_INTEGRITY_STATUSES = PERSISTED_RUN_INTEGRITY_STATUSES
ALLOWED_GUIDANCE_SOURCES = {"improvement_ledger", "manual", "prompt_only"}
ALLOWED_ASSESSMENT_METHODS = {"human", "llm_assisted_human_review", "llm_only"}
A_CONTROLLED_ASSESSMENT_METHODS = {"human", "llm_assisted_human_review"}
ALLOWED_SCORECARD_ASSESSMENT_STATUSES = {"assessed", "needs_assessment"}

REQUIRED_FACT_ARTIFACT_IDS = {
    "durable_source_evidence_or_source_pack",
    "input_classification",
    "candidate_claims",
    "screened_candidates",
    "claim_ledger",
}

SOURCE_PLAN_ARTIFACT_IDS = {
    "source_candidates",
    "source_candidate_plan",
    "source_plan",
}

_CASE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,79}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,159}$")
_GUIDANCE_ENTRY_ID_RE = re.compile(r"^AG-\d{4,}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_LOCAL_PATH_PATTERNS = [
    re.compile(r"/Users/(?!example(?:/|$)|user(?:/|$)|you(?:/|$)|name(?:/|$)|<[^/]+>)"),
    re.compile(r"/home/(?!example(?:/|$)|user(?:/|$)|you(?:/|$)|name(?:/|$)|<[^/]+>)"),
    re.compile(r"[A-Za-z]:\\Users\\"),
    re.compile(r"file://", re.IGNORECASE),
]


@dataclass(frozen=True)
class Experiment080Diagnostic:
    """One validation diagnostic."""

    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message}
        if self.path:
            payload["path"] = self.path
        return payload


class Experiment080Error(Exception):
    """Raised when experiment metadata cannot be read or validated."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": str(self), "details": self.details}


def validate_case_manifest(payload: dict[str, Any]) -> list[Experiment080Diagnostic]:
    """Validate ``case_manifest.json``."""

    diagnostics: list[Experiment080Diagnostic] = []
    _require_schema(
        payload,
        expected=CASE_MANIFEST_SCHEMA,
        label="case_manifest",
        diagnostics=diagnostics,
    )
    if payload.get("experiment_id") != EXPERIMENT_080_ID:
        diagnostics.append(_diag(
            "invalid_experiment_id",
            f"case_manifest.experiment_id must be {EXPERIMENT_080_ID}.",
        ))

    case_id = payload.get("case_id")
    if not isinstance(case_id, str) or not _CASE_ID_RE.match(case_id):
        diagnostics.append(_diag(
            "invalid_case_id",
            "case_manifest.case_id must be a stable lowercase id.",
        ))

    _require_non_empty_string(
        payload.get("case_title"),
        diagnostics,
        path="case_manifest.case_title",
    )
    if not isinstance(payload.get("public_safe"), bool):
        diagnostics.append(_diag(
            "invalid_public_safe",
            "case_manifest.public_safe must be a boolean.",
        ))
    _require_non_empty_string(
        payload.get("created_at"),
        diagnostics,
        path="case_manifest.created_at",
    )
    _require_non_empty_string(
        payload.get("repo_commit"),
        diagnostics,
        path="case_manifest.repo_commit",
    )

    conditions = payload.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        diagnostics.append(_diag(
            "invalid_conditions",
            "case_manifest.conditions must be a non-empty list.",
        ))
        conditions = []
    seen_conditions: set[str] = set()
    for idx, condition in enumerate(conditions):
        path = f"case_manifest.conditions[{idx}]"
        if condition not in ALLOWED_CONDITIONS:
            diagnostics.append(_diag(
                "unknown_condition",
                f"{path} must be one of {sorted(ALLOWED_CONDITIONS)}.",
                path=path,
            ))
            continue
        if condition in seen_conditions:
            diagnostics.append(_diag("duplicate_condition", f"{path} is duplicated.", path=path))
        seen_conditions.add(str(condition))
    missing_measurement_conditions = {"baseline", "memory"} - seen_conditions
    if missing_measurement_conditions:
        diagnostics.append(_diag(
            "missing_measurement_condition",
            "MABW-080 manifestation measurement requires at least baseline and memory conditions.",
            path="case_manifest.conditions",
        ))

    diagnostics.extend(_validate_relative_path_ref(
        payload.get("frozen_fact_layer"),
        key="manifest_path",
        expected="frozen_fact_layer.json",
        path="case_manifest.frozen_fact_layer.manifest_path",
    ))
    diagnostics.extend(_validate_relative_path_ref(
        payload.get("guidance_set"),
        key="path",
        expected="guidance_set.json",
        path="case_manifest.guidance_set.path",
    ))
    allowed_claims = payload.get("allowed_claims", {})
    if allowed_claims is not None and not isinstance(allowed_claims, dict):
        diagnostics.append(_diag(
            "invalid_allowed_claims",
            "case_manifest.allowed_claims must be an object when present.",
        ))
        allowed_claims = {}
    if isinstance(allowed_claims, dict) and allowed_claims.get("memory_mechanism_adds_over_prompt") is True:
        missing_prompt_conditions = {"baseline", "memory", "prompt_only"} - seen_conditions
        if missing_prompt_conditions:
            diagnostics.append(_diag(
                "mechanism_claim_requires_prompt_only",
                "memory_mechanism_adds_over_prompt requires baseline, memory, and prompt_only conditions.",
                path="case_manifest.allowed_claims.memory_mechanism_adds_over_prompt",
            ))
    return diagnostics


def validate_frozen_fact_layer(payload: dict[str, Any]) -> list[Experiment080Diagnostic]:
    """Validate ``frozen_fact_layer.json``."""

    diagnostics: list[Experiment080Diagnostic] = []
    _require_schema(
        payload,
        expected=FROZEN_FACT_LAYER_SCHEMA,
        label="frozen_fact_layer",
        diagnostics=diagnostics,
    )
    _require_non_empty_string(
        payload.get("source_run_id"),
        diagnostics,
        path="frozen_fact_layer.source_run_id",
    )
    source_archive = payload.get("source_archive_path")
    if source_archive is not None:
        diagnostics.extend(_validate_safe_relative_path(
            source_archive,
            path="frozen_fact_layer.source_archive_path",
        ))

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        diagnostics.append(_diag(
            "invalid_frozen_artifacts",
            "frozen_fact_layer.artifacts must be a non-empty list.",
        ))
        artifacts = []

    seen_ids: set[str] = set()
    for idx, artifact in enumerate(artifacts):
        path = f"frozen_fact_layer.artifacts[{idx}]"
        if not isinstance(artifact, dict):
            diagnostics.append(_diag("invalid_artifact", f"{path} must be an object.", path=path))
            continue
        artifact_id = artifact.get("artifact_id")
        artifact_id_text = str(artifact_id or "")
        if not artifact_id_text:
            diagnostics.append(_diag("missing_artifact_id", f"{path}.artifact_id is required.", path=path))
        elif artifact_id_text in seen_ids:
            diagnostics.append(_diag(
                "duplicate_artifact_id",
                f"{path}.artifact_id is duplicated: {artifact_id_text}.",
                path=path,
            ))
        seen_ids.add(artifact_id_text)
        if artifact_id_text in SOURCE_PLAN_ARTIFACT_IDS:
            diagnostics.append(_diag(
                "source_plan_not_evidence",
                "source_candidates/source plans cannot satisfy the frozen fact layer.",
                path=f"{path}.artifact_id",
            ))

        rel_path = artifact.get("path")
        diagnostics.extend(_validate_safe_relative_path(rel_path, path=f"{path}.path"))
        if isinstance(rel_path, str) and rel_path.endswith("source_candidates.yaml"):
            diagnostics.append(_diag(
                "source_plan_not_evidence",
                "source_candidates.yaml is planning only and cannot be frozen fact evidence.",
                path=f"{path}.path",
            ))

        sha = artifact.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            diagnostics.append(_diag(
                "invalid_artifact_sha256",
                f"{path}.sha256 must be a lowercase SHA-256 hex digest.",
                path=f"{path}.sha256",
            ))

    missing = sorted(REQUIRED_FACT_ARTIFACT_IDS - seen_ids)
    if missing:
        diagnostics.append(_diag(
            "missing_required_fact_artifacts",
            f"frozen_fact_layer.artifacts is missing required artifact ids: {missing}.",
        ))
    return diagnostics


def validate_guidance_set(payload: dict[str, Any]) -> list[Experiment080Diagnostic]:
    """Validate ``guidance_set.json``."""

    diagnostics: list[Experiment080Diagnostic] = []
    _require_schema(
        payload,
        expected=GUIDANCE_SET_SCHEMA,
        label="guidance_set",
        diagnostics=diagnostics,
    )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        diagnostics.append(_diag(
            "invalid_guidance_entries",
            "guidance_set.entries must be a list.",
        ))
        entries = []
    elif not entries:
        diagnostics.append(_diag(
            "empty_guidance_entries",
            "guidance_set.entries must contain at least one approved guidance entry.",
            path="guidance_set.entries",
        ))
    seen: set[str] = set()
    has_improvement_ledger_entry = False
    for idx, entry in enumerate(entries):
        path = f"guidance_set.entries[{idx}]"
        if not isinstance(entry, dict):
            diagnostics.append(_diag("invalid_guidance_entry", f"{path} must be an object.", path=path))
            continue
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or not _GUIDANCE_ENTRY_ID_RE.match(entry_id):
            diagnostics.append(_diag(
                "invalid_guidance_entry_id",
                f"{path}.entry_id must match AG-0001 style.",
                path=f"{path}.entry_id",
            ))
        elif entry_id in seen:
            diagnostics.append(_diag(
                "duplicate_guidance_entry_id",
                f"{path}.entry_id is duplicated: {entry_id}.",
                path=f"{path}.entry_id",
            ))
        seen.add(str(entry_id or ""))
        for key in ("guidance_text", "expected_manifestation", "relevance_rule"):
            _require_non_empty_string(
                entry.get(key),
                diagnostics,
                path=f"{path}.{key}",
            )
        source = entry.get("source")
        if source not in ALLOWED_GUIDANCE_SOURCES:
            diagnostics.append(_diag(
                "invalid_guidance_source",
                f"{path}.source must be one of {sorted(ALLOWED_GUIDANCE_SOURCES)}.",
                path=f"{path}.source",
            ))
        elif source == "improvement_ledger":
            has_improvement_ledger_entry = True
    if entries and not has_improvement_ledger_entry:
        diagnostics.append(_diag(
            "missing_improvement_ledger_guidance",
            "MABW-080 manifestation measurement requires at least one improvement_ledger guidance entry.",
            path="guidance_set.entries",
        ))
    return diagnostics


def validate_run_record(payload: dict[str, Any]) -> list[Experiment080Diagnostic]:
    """Validate ``run_record.json``."""

    diagnostics: list[Experiment080Diagnostic] = []
    _require_schema(payload, expected=RUN_RECORD_SCHEMA, label="run_record", diagnostics=diagnostics)
    if payload.get("experiment_id", EXPERIMENT_080_ID) != EXPERIMENT_080_ID:
        diagnostics.append(_diag(
            "invalid_experiment_id",
            f"run_record.experiment_id must be {EXPERIMENT_080_ID} when present.",
        ))
    _validate_case_id_field(payload.get("case_id"), diagnostics, path="run_record.case_id")
    _validate_condition(payload.get("condition"), diagnostics, path="run_record.condition")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        diagnostics.append(_diag("invalid_run_id", "run_record.run_id is required.", path="run_record.run_id"))
    for key in ("workspace_path", "run_archive_path", "repo_commit", "runtime"):
        _require_non_empty_string(payload.get(key), diagnostics, path=f"run_record.{key}")
    model = payload.get("model")
    if model is not None:
        if not isinstance(model, dict):
            diagnostics.append(_diag("invalid_model", "run_record.model must be an object.", path="run_record.model"))
        else:
            if model.get("epistemic_status") != "operator_reported":
                diagnostics.append(_diag(
                    "invalid_model_epistemic_status",
                    "run_record.model.epistemic_status must be operator_reported.",
                    path="run_record.model.epistemic_status",
                ))
            _require_non_empty_string(model.get("value"), diagnostics, path="run_record.model.value")
    _validate_run_integrity(payload.get("run_integrity"), diagnostics, path="run_record.run_integrity")
    timing = payload.get("timing")
    if not isinstance(timing, dict):
        diagnostics.append(_diag(
            "invalid_timing",
            "run_record.timing must be an object.",
            path="run_record.timing",
        ))
    else:
        _require_non_empty_string(timing.get("schema_version"), diagnostics, path="run_record.timing.schema_version")
        _require_non_empty_string(timing.get("status"), diagnostics, path="run_record.timing.status")
    imported = payload.get("imported_fact_layer")
    if not isinstance(imported, dict):
        diagnostics.append(_diag(
            "invalid_imported_fact_layer",
            "run_record.imported_fact_layer must be an object.",
            path="run_record.imported_fact_layer",
        ))
    elif not isinstance(imported.get("matches_case_frozen_fact_layer"), bool):
        diagnostics.append(_diag(
            "invalid_imported_fact_layer_match",
            "run_record.imported_fact_layer.matches_case_frozen_fact_layer must be a boolean.",
            path="run_record.imported_fact_layer.matches_case_frozen_fact_layer",
        ))
    return diagnostics


def register_run_record(
    *,
    case_dir: str | Path,
    condition: str,
    workspace: str | Path,
    output: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Register a completed workspace run into an 080 case.

    This writes only the requested run record. It does not mutate workspace
    runtime state, archive files, case files, or normal workflow artifacts.
    """

    case_root = Path(case_dir).expanduser().resolve()
    ws = Path(workspace).expanduser().resolve()
    output_path = Path(output).expanduser()
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    else:
        output_path = output_path.resolve()

    case_validation = validate_case_dir(case_root)
    if not case_validation.get("ok"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_CASE_INVALID",
            "MABW-080 case validation failed.",
            errors=case_validation.get("errors") or [],
            warnings=case_validation.get("warnings") or [],
        )

    case_manifest = _load_json_object(case_root / "case_manifest.json", label="case_manifest")
    frozen_fact_layer = _load_json_object(case_root / "frozen_fact_layer.json", label="frozen_fact_layer")
    conditions = case_manifest.get("conditions") if isinstance(case_manifest.get("conditions"), list) else []
    if condition not in conditions:
        _raise_experiment_error(
            "E_EXPERIMENT_080_CONDITION_INVALID",
            f"Condition {condition!r} is not declared by case_manifest.conditions.",
            condition=condition,
            allowed_conditions=[item for item in conditions if item in ALLOWED_CONDITIONS],
        )

    intermediate = ws / "output" / "intermediate"
    runtime_manifest = _load_json_object(intermediate / "runtime_manifest.json", label="runtime_manifest")
    workflow_state = _load_json_object(intermediate / "workflow_state.json", label="workflow_state")
    run_id = _require_text(runtime_manifest.get("run_id"), "runtime_manifest.run_id")
    workflow_run_id = _require_text(workflow_state.get("run_id"), "workflow_state.run_id")
    if workflow_run_id != run_id:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_ID_MISMATCH",
            "runtime_manifest.run_id and workflow_state.run_id do not match.",
            runtime_manifest_run_id=run_id,
            workflow_state_run_id=workflow_run_id,
        )

    _validate_terminal_workflow(workflow_state)
    archive_manifest_path = ws / "output" / "runs" / run_id / "manifest.json"
    archive_manifest = _load_json_object(archive_manifest_path, label="run_archive_manifest")
    _validate_archive_manifest_ids(archive_manifest, run_id=run_id)

    workflow_integrity = _registered_run_integrity(
        workflow_state,
        path="workflow_state.run_integrity",
    )
    archive_integrity = _registered_run_integrity(
        archive_manifest,
        path="run_archive_manifest.run_integrity",
    )
    _validate_run_integrity_consistency(workflow_integrity, archive_integrity)

    archive_fact_layer = archive_manifest.get("fact_layer")
    if not isinstance(archive_fact_layer, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_INVALID",
            "run archive manifest.fact_layer must be an object.",
        )
    if archive_fact_layer.get("status") != "complete":
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_INCOMPLETE",
            "run archive fact_layer.status must be complete.",
            status=archive_fact_layer.get("status"),
        )
    imported_fact_layer = _compare_case_fact_layer_to_archive(
        frozen_fact_layer=frozen_fact_layer,
        archive_fact_layer=archive_fact_layer,
        archive_root=archive_manifest_path.parent,
    )

    timing = archive_manifest.get("timing")
    if not isinstance(timing, dict) or not timing.get("schema_version") or not timing.get("status"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_TIMING_MISSING",
            "run archive manifest.timing must contain schema_version and status.",
        )

    repo_commit, repo_commit_source = _registration_repo_commit(
        case_manifest=case_manifest,
        repo_workdir=repo_workdir,
    )
    runtime = _require_text(runtime_manifest.get("runtime"), "runtime_manifest.runtime")

    run_record: dict[str, Any] = {
        "schema_version": RUN_RECORD_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "condition": condition,
        "run_id": run_id,
        "workspace_path": "<redacted-workspace>",
        "run_archive_path": _workspace_relative(ws, archive_manifest_path),
        "repo_commit": repo_commit,
        "repo_commit_source": repo_commit_source,
        "runtime": runtime,
        "run_integrity": workflow_integrity,
        "timing": timing,
        "imported_fact_layer": imported_fact_layer,
    }
    model = _model_identity(runtime_manifest, workflow_state)
    if model is not None:
        run_record["model"] = model

    diagnostics = validate_run_record(run_record)
    if diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_RECORD_INVALID",
            "Generated run_record.json failed schema validation.",
            errors=[diagnostic.to_dict() for diagnostic in diagnostics],
        )

    record_bytes = _json_bytes(run_record)
    written = _write_run_record_idempotently(output_path, record_bytes)
    return {
        "ok": True,
        "schema_version": RUN_RECORD_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "condition": condition,
        "run_id": run_id,
        "output": str(output_path),
        "written": written,
        "run_record": run_record,
    }


def score_run_record(
    *,
    case_dir: str | Path,
    run_record: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    """Build a deterministic MABW-080 scorecard draft from a registered run.

    The builder fills only deterministic control/readiness fields. It does not
    judge whether guidance manifested, prose improved, or factual regressions
    occurred. Until a human/assisted assessment is imported, the generated
    scorecard remains ``assessment_status=needs_assessment`` and cannot be
    A-grade evidence.
    """

    case_root = Path(case_dir).expanduser().resolve()
    record_path = Path(run_record).expanduser()
    if not record_path.is_absolute():
        record_path = (Path.cwd() / record_path).resolve()
    else:
        record_path = record_path.resolve()
    output_path = Path(output).expanduser()
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    else:
        output_path = output_path.resolve()

    case_validation = validate_case_dir(case_root)
    if not case_validation.get("ok"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_CASE_INVALID",
            "MABW-080 case validation failed.",
            errors=case_validation.get("errors") or [],
            warnings=case_validation.get("warnings") or [],
        )
    case_manifest = _load_json_object(case_root / "case_manifest.json", label="case_manifest")
    guidance_set = _load_json_object(case_root / "guidance_set.json", label="guidance_set")
    record = _load_json_object(record_path, label="run_record")
    run_record_diagnostics = validate_run_record(record)
    if run_record_diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_RECORD_INVALID",
            "run_record.json failed schema validation.",
            errors=[diagnostic.to_dict() for diagnostic in run_record_diagnostics],
        )
    if record.get("case_id") != case_manifest.get("case_id"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_CASE_MISMATCH",
            "run_record.case_id does not match case_manifest.case_id.",
            run_record_case_id=record.get("case_id"),
            case_manifest_case_id=case_manifest.get("case_id"),
        )
    conditions = case_manifest.get("conditions") if isinstance(case_manifest.get("conditions"), list) else []
    if record.get("condition") not in conditions:
        _raise_experiment_error(
            "E_EXPERIMENT_080_CONDITION_INVALID",
            "run_record.condition is not declared by case_manifest.conditions.",
            condition=record.get("condition"),
            allowed_conditions=[item for item in conditions if item in ALLOWED_CONDITIONS],
        )

    archive_projection = _scorecard_archive_projection(
        case_root=case_root,
        run_record_path=record_path,
        run_record=record,
    )
    scorecard = _build_scorecard_draft(
        case_manifest=case_manifest,
        guidance_set=guidance_set,
        run_record=record,
        archive_projection=archive_projection,
    )
    diagnostics = validate_scorecard(scorecard)
    if diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "Generated scorecard.json failed schema validation.",
            errors=[diagnostic.to_dict() for diagnostic in diagnostics],
        )

    record_bytes = _json_bytes(scorecard)
    written = _write_experiment_output_idempotently(
        output_path,
        record_bytes,
        artifact_label="scorecard",
    )
    return {
        "ok": True,
        "schema_version": SCORECARD_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": scorecard["case_id"],
        "condition": scorecard["condition"],
        "run_id": scorecard["run_id"],
        "validity_class": scorecard["validity_class"],
        "assessment_status": scorecard["assessment_status"],
        "output": str(output_path),
        "written": written,
        "scorecard": scorecard,
    }


def validate_scorecard(payload: dict[str, Any]) -> list[Experiment080Diagnostic]:
    """Validate ``scorecard.json``."""

    diagnostics: list[Experiment080Diagnostic] = []
    _require_schema(payload, expected=SCORECARD_SCHEMA, label="scorecard", diagnostics=diagnostics)
    if payload.get("experiment_id") != EXPERIMENT_080_ID:
        diagnostics.append(_diag(
            "invalid_experiment_id",
            f"scorecard.experiment_id must be {EXPERIMENT_080_ID}.",
            path="scorecard.experiment_id",
        ))
    _validate_case_id_field(payload.get("case_id"), diagnostics, path="scorecard.case_id")
    _validate_condition(payload.get("condition"), diagnostics, path="scorecard.condition")
    _require_non_empty_string(payload.get("run_id"), diagnostics, path="scorecard.run_id")

    validity = payload.get("validity_class")
    if validity not in ALLOWED_VALIDITY_CLASSES:
        diagnostics.append(_diag(
            "invalid_validity_class",
            f"scorecard.validity_class must be one of {sorted(ALLOWED_VALIDITY_CLASSES)}.",
            path="scorecard.validity_class",
        ))
    assessment_status = payload.get("assessment_status")
    if assessment_status is not None and assessment_status not in ALLOWED_SCORECARD_ASSESSMENT_STATUSES:
        diagnostics.append(_diag(
            "invalid_assessment_status",
            f"scorecard.assessment_status must be one of {sorted(ALLOWED_SCORECARD_ASSESSMENT_STATUSES)} when present.",
            path="scorecard.assessment_status",
        ))

    control = payload.get("control_integrity")
    if not isinstance(control, dict):
        diagnostics.append(_diag(
            "invalid_control_integrity",
            "scorecard.control_integrity must be an object.",
            path="scorecard.control_integrity",
        ))
        control = {}
    fact_layer = payload.get("frozen_fact_layer")
    if not isinstance(fact_layer, dict):
        diagnostics.append(_diag(
            "invalid_scorecard_fact_layer",
            "scorecard.frozen_fact_layer must be an object.",
            path="scorecard.frozen_fact_layer",
        ))
        fact_layer = {}
    reader_clean = payload.get("reader_clean")
    if not isinstance(reader_clean, dict):
        diagnostics.append(_diag(
            "invalid_reader_clean",
            "scorecard.reader_clean must be an object.",
            path="scorecard.reader_clean",
        ))
        reader_clean = {}

    guidance_scores = payload.get("guidance_scores")
    if not isinstance(guidance_scores, list):
        diagnostics.append(_diag(
            "invalid_guidance_scores",
            "scorecard.guidance_scores must be a list.",
            path="scorecard.guidance_scores",
        ))
        guidance_scores = []
    elif not guidance_scores and assessment_status != "needs_assessment":
        diagnostics.append(_diag(
            "empty_guidance_scores",
            "scorecard.guidance_scores must contain at least one guidance score unless assessment_status is needs_assessment.",
            path="scorecard.guidance_scores",
        ))
    seen_guidance_score_ids: set[str] = set()
    for idx, score in enumerate(guidance_scores):
        path = f"scorecard.guidance_scores[{idx}]"
        if not isinstance(score, dict):
            diagnostics.append(_diag("invalid_guidance_score", f"{path} must be an object.", path=path))
            continue
        entry_id = score.get("entry_id")
        if isinstance(entry_id, str):
            if entry_id in seen_guidance_score_ids:
                diagnostics.append(_diag(
                    "duplicate_guidance_score_entry_id",
                    f"{path}.entry_id is duplicated: {entry_id}.",
                    path=f"{path}.entry_id",
                ))
            seen_guidance_score_ids.add(entry_id)
        _validate_guidance_score(score, diagnostics, path=path)

    if validity == "A_controlled":
        if assessment_status == "needs_assessment":
            diagnostics.append(_diag(
                "a_controlled_requires_assessment",
                "A_controlled scorecards cannot be marked needs_assessment.",
                path="scorecard.assessment_status",
            ))
        _validate_a_controlled_scorecard(
            control=control,
            fact_layer=fact_layer,
            guidance_scores=guidance_scores,
            reader_clean=reader_clean,
            diagnostics=diagnostics,
        )
    return diagnostics


def validate_case_dir(case_dir: str | Path) -> dict[str, Any]:
    """Validate an 080 case directory without writing anything."""

    root = Path(case_dir).expanduser().resolve()
    errors: list[Experiment080Diagnostic] = []
    warnings: list[Experiment080Diagnostic] = []
    files: dict[str, Path] = {
        "case_manifest": root / "case_manifest.json",
        "frozen_fact_layer": root / "frozen_fact_layer.json",
        "guidance_set": root / "guidance_set.json",
    }
    payloads: dict[str, dict[str, Any]] = {}
    for label, path in files.items():
        payload, diagnostic = _read_json_object(path, root=root, label=label)
        if diagnostic is not None:
            errors.append(diagnostic)
            continue
        if payload is not None:
            payloads[label] = payload

    if "case_manifest" in payloads:
        errors.extend(validate_case_manifest(payloads["case_manifest"]))
    if "frozen_fact_layer" in payloads:
        errors.extend(validate_frozen_fact_layer(payloads["frozen_fact_layer"]))
    if "guidance_set" in payloads:
        errors.extend(validate_guidance_set(payloads["guidance_set"]))

    manifest = payloads.get("case_manifest") or {}
    case_id = manifest.get("case_id") if isinstance(manifest.get("case_id"), str) else None
    conditions = manifest.get("conditions") if isinstance(manifest.get("conditions"), list) else []
    if manifest.get("public_safe") is True:
        errors.extend(_scan_public_safe_case_files(root, files))

    return {
        "schema_version": CASE_VALIDATION_SCHEMA,
        "ok": not errors,
        "experiment_id": EXPERIMENT_080_ID,
        "case_dir": str(root),
        "case_id": case_id,
        "conditions": [condition for condition in conditions if condition in ALLOWED_CONDITIONS],
        "validated_files": [
            _relative_to_root(path, root)
            for label, path in files.items()
            if label in payloads and path.exists()
        ],
        "errors": [error.to_dict() for error in errors],
        "warnings": [warning.to_dict() for warning in warnings],
    }


def _build_scorecard_draft(
    *,
    case_manifest: dict[str, Any],
    guidance_set: dict[str, Any],
    run_record: dict[str, Any],
    archive_projection: dict[str, Any],
) -> dict[str, Any]:
    run_integrity = run_record.get("run_integrity") if isinstance(run_record.get("run_integrity"), dict) else {}
    imported_fact_layer = (
        run_record.get("imported_fact_layer")
        if isinstance(run_record.get("imported_fact_layer"), dict)
        else {}
    )
    fact_layer_matches = imported_fact_layer.get("matches_case_frozen_fact_layer") is True
    reader_clean = archive_projection["reader_clean"]
    gate_status = archive_projection["quality_gates"]
    finalize_status = archive_projection["finalize"]
    archive_status = archive_projection["archive"]
    timing_summary = _scorecard_timing_summary(run_record.get("timing"))
    control_integrity = {
        "terminal_workflow": True,
        "run_integrity_clean": run_integrity.get("status") == "clean",
        "reference_eligible": run_integrity.get("reference_eligible") is True,
        "artifact_registry_valid": archive_projection["artifact_registry_valid"],
        "quality_gates_passed": gate_status["passed"],
        "archive_present": archive_status["present"],
        "archive_schema_valid": archive_status["schema_valid"],
        "finalize_complete": finalize_status["complete"],
        "finalize_report_pass": finalize_status["report_pass"],
        "timing_available": timing_summary["status"] == "available",
    }
    validity_class = _scorecard_validity_class(
        run_integrity=run_integrity,
        fact_layer_matches=fact_layer_matches,
        control_integrity=control_integrity,
        reader_clean=reader_clean,
    )
    guidance_entries = guidance_set.get("entries") if isinstance(guidance_set.get("entries"), list) else []
    return {
        "schema_version": SCORECARD_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "condition": run_record["condition"],
        "run_id": run_record["run_id"],
        "validity_class": validity_class,
        "assessment_status": "needs_assessment",
        "assessment_boundary": (
            "python_fills_deterministic_control_fields_only; "
            "guidance_manifestation_requires_human_or_llm_assisted_human_review_import"
        ),
        "control_integrity": control_integrity,
        "frozen_fact_layer": {
            "matches_case": fact_layer_matches,
            "comparison_semantics": imported_fact_layer.get("comparison_semantics", ""),
            "mismatches": imported_fact_layer.get("mismatches") or [],
        },
        "reader_clean": reader_clean,
        "quality_gates": gate_status,
        "finalize": finalize_status,
        "archive": archive_status,
        "timing_summary": timing_summary,
        "coverage_delta": {
            "status": "not_computed",
            "reason": "deterministic_coverage_baseline_not_available_in_run_record",
        },
        "guidance_assessment": {
            "status": "needs_assessment",
            "required_methods": sorted(A_CONTROLLED_ASSESSMENT_METHODS),
            "guidance_entry_ids": [
                entry.get("entry_id")
                for entry in guidance_entries
                if isinstance(entry, dict) and isinstance(entry.get("entry_id"), str)
            ],
        },
        "guidance_scores": [],
        "regression": {
            "status": "not_assessed",
            "reason": "semantic_regression_requires_human_or_imported_assessment",
        },
        "notes": [
            "Scorecard draft is deterministic metadata only.",
            "Python does not score guidance manifestation, prose quality, taste, or factual-regression semantics.",
        ],
    }


def _scorecard_validity_class(
    *,
    run_integrity: dict[str, Any],
    fact_layer_matches: bool,
    control_integrity: dict[str, Any],
    reader_clean: dict[str, Any],
) -> str:
    if run_integrity.get("status") == "contaminated":
        return "invalid_contaminated"
    if not fact_layer_matches:
        return "invalid_fact_layer_mismatch"
    required_control_keys = (
        "terminal_workflow",
        "run_integrity_clean",
        "reference_eligible",
        "artifact_registry_valid",
        "quality_gates_passed",
        "archive_present",
        "archive_schema_valid",
        "finalize_complete",
        "finalize_report_pass",
        "timing_available",
    )
    if any(control_integrity.get(key) is not True for key in required_control_keys):
        return "invalid_incomplete"
    if reader_clean.get("pass") is not True:
        return "invalid_incomplete"
    return "invalid_incomplete"


def _scorecard_archive_projection(
    *,
    case_root: Path,
    run_record_path: Path,
    run_record: dict[str, Any],
) -> dict[str, Any]:
    archive_path = _resolve_scorecard_archive_manifest(
        case_root=case_root,
        run_record_path=run_record_path,
        run_archive_path=str(run_record.get("run_archive_path") or ""),
    )
    archive = {
        "present": archive_path is not None,
        "schema_valid": False,
        "source": "missing",
        "run_archive_path": str(run_record.get("run_archive_path") or ""),
    }
    reader_clean = {"pass": False, "status": "unknown", "source": "archive_missing"}
    quality_gates = {
        "passed": False,
        "auditor_status": "unknown",
        "finalize_status": "unknown",
        "source": "archive_missing",
    }
    finalize = {
        "complete": False,
        "report_pass": False,
        "report_status": "unknown",
        "source": "archive_missing",
    }
    artifact_registry_valid = False
    if archive_path is None:
        return {
            "archive": archive,
            "reader_clean": reader_clean,
            "quality_gates": quality_gates,
            "finalize": finalize,
            "artifact_registry_valid": artifact_registry_valid,
        }
    archive_root = archive_path.parent
    try:
        archive_manifest = _load_json_object(archive_path, label="run_archive_manifest")
    except Experiment080Error as exc:
        archive["source"] = "invalid_archive_manifest"
        archive["error"] = exc.details.get("code", "E_EXPERIMENT_080_INPUT_INVALID")
        return {
            "archive": archive,
            "reader_clean": reader_clean,
            "quality_gates": quality_gates,
            "finalize": finalize,
            "artifact_registry_valid": artifact_registry_valid,
        }
    archive["schema_valid"] = archive_manifest.get("schema_version") == RUN_ARCHIVE_SCHEMA
    archive["source"] = archive_manifest.get("source") if isinstance(archive_manifest.get("source"), str) else "unknown"
    archive["fact_layer_status"] = (
        archive_manifest.get("fact_layer", {}).get("status")
        if isinstance(archive_manifest.get("fact_layer"), dict)
        else "unknown"
    )
    _validate_archive_manifest_ids(archive_manifest, run_id=str(run_record.get("run_id") or ""))
    finalize["complete"] = archive["schema_valid"] and archive["source"] == "finalize-complete"

    finalize_report = _read_archive_json_by_original_path(
        archive_root=archive_root,
        archive_manifest=archive_manifest,
        original_path="output/intermediate/finalize_report.json",
    )
    if isinstance(finalize_report, dict):
        report_status = str(finalize_report.get("status") or "unknown")
        finalize.update({
            "source": "finalize_report",
            "report_status": report_status,
            "report_pass": report_status == "pass",
        })
        reader_clean = _scorecard_reader_clean(finalize_report)
    else:
        finalize["source"] = "finalize_report_missing"

    auditor_report = _read_archive_json_by_original_path(
        archive_root=archive_root,
        archive_manifest=archive_manifest,
        original_path="output/intermediate/gates/auditor_quality_gate_report.json",
    )
    finalize_gate_report = _read_archive_json_by_original_path(
        archive_root=archive_root,
        archive_manifest=archive_manifest,
        original_path="output/intermediate/gates/finalize_quality_gate_report.json",
    )
    auditor_status = _status_from_report(auditor_report)
    finalize_gate_status = _status_from_report(finalize_gate_report)
    quality_gates = {
        "passed": auditor_status == "pass" and finalize_gate_status == "pass",
        "auditor_status": auditor_status,
        "finalize_status": finalize_gate_status,
        "source": "archive_gate_reports",
    }

    artifact_registry = _read_archive_json_by_original_path(
        archive_root=archive_root,
        archive_manifest=archive_manifest,
        original_path="output/intermediate/artifact_registry.json",
    )
    artifact_registry_valid = (
        isinstance(artifact_registry, dict)
        and isinstance(artifact_registry.get("artifacts"), dict)
    )
    return {
        "archive": archive,
        "reader_clean": reader_clean,
        "quality_gates": quality_gates,
        "finalize": finalize,
        "artifact_registry_valid": artifact_registry_valid,
    }


def _resolve_scorecard_archive_manifest(
    *,
    case_root: Path,
    run_record_path: Path,
    run_archive_path: str,
) -> Path | None:
    if not run_archive_path.strip():
        return None
    rel = Path(run_archive_path)
    candidates: list[Path] = []
    if rel.is_absolute():
        candidates.append(rel)
    else:
        for parent in [run_record_path.parent, *run_record_path.parents]:
            candidates.append(parent / rel)
        candidates.append(case_root / rel)
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _read_archive_json_by_original_path(
    *,
    archive_root: Path,
    archive_manifest: dict[str, Any],
    original_path: str,
) -> dict[str, Any] | None:
    path = _archive_file_by_original_path(
        archive_root=archive_root,
        archive_manifest=archive_manifest,
        original_path=original_path,
    )
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _archive_file_by_original_path(
    *,
    archive_root: Path,
    archive_manifest: dict[str, Any],
    original_path: str,
) -> Path | None:
    files = archive_manifest.get("files")
    if not isinstance(files, list):
        return None
    matches = [
        record
        for record in files
        if isinstance(record, dict) and record.get("original_path") == original_path
    ]
    if len(matches) > 1:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "run archive contains duplicate file records for original_path.",
            original_path=original_path,
        )
    if not matches:
        return None
    record = matches[0]
    normalized = _validated_scorecard_archive_file_record(
        archive_root=archive_root,
        record=record,
        context=original_path,
    )
    return archive_root / normalized["archive_path"]


def _validated_scorecard_archive_file_record(
    *,
    archive_root: Path,
    record: dict[str, Any],
    context: str,
) -> dict[str, Any]:
    archive_path = record.get("archive_path")
    original_path = record.get("original_path")
    sha256 = record.get("sha256")
    size_bytes = record.get("size_bytes")
    if not isinstance(archive_path, str) or not archive_path.strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file record archive_path is required.",
            context=context,
        )
    if not isinstance(original_path, str) or not original_path.strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file record original_path is required.",
            context=context,
        )
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file record sha256 is invalid.",
            context=context,
        )
    if not isinstance(size_bytes, int) or size_bytes < 0:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file record size_bytes is invalid.",
            context=context,
        )
    if _unsafe_relative_archive_path(archive_path):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file record archive_path must be relative and safe.",
            context=context,
            archive_path=archive_path,
        )
    file_path = (archive_root / archive_path).resolve()
    try:
        file_path.relative_to(archive_root.resolve())
    except ValueError:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file record archive_path escapes archive.",
            context=context,
            archive_path=archive_path,
        )
    if not file_path.exists() or not file_path.is_file():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file is missing.",
            context=context,
            archive_path=archive_path,
        )
    actual_size = file_path.stat().st_size
    if actual_size != size_bytes:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file size does not match manifest.",
            context=context,
            archive_path=archive_path,
            expected_size_bytes=size_bytes,
            actual_size_bytes=actual_size,
        )
    actual_sha = _sha256_file(file_path)
    if actual_sha != sha256:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID",
            "archive file hash does not match manifest.",
            context=context,
            archive_path=archive_path,
            expected_sha256=sha256,
            actual_sha256=actual_sha,
        )
    return {
        "archive_path": archive_path,
        "original_path": original_path,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def _scorecard_reader_clean(finalize_report: dict[str, Any]) -> dict[str, Any]:
    reader = finalize_report.get("reader_clean")
    if not isinstance(reader, dict):
        return {"pass": False, "status": "unknown", "source": "finalize_report.reader_clean_missing"}
    status = str(reader.get("status") or "unknown")
    return {
        "pass": status == "pass" or reader.get("pass") is True,
        "status": status,
        "source": "finalize_report.reader_clean",
        "finding_count": sum(
            int(value)
            for key, value in reader.items()
            if key.endswith("_count") and isinstance(value, int)
        ),
    }


def _status_from_report(report: dict[str, Any] | None) -> str:
    if not isinstance(report, dict):
        return "missing"
    status = report.get("status")
    return status if isinstance(status, str) and status else "unknown"


def _scorecard_timing_summary(timing: Any) -> dict[str, Any]:
    if not isinstance(timing, dict):
        return {"status": "unknown", "schema_version": "", "source": "run_record.timing"}
    stages = timing.get("stages")
    summary = {
        "schema_version": str(timing.get("schema_version") or ""),
        "status": str(timing.get("status") or "unknown"),
        "run_recipe": timing.get("run_recipe", ""),
        "source": "run_record.timing",
    }
    if isinstance(stages, list):
        summary["stage_count"] = len(stages)
        summary["completed_stage_count"] = sum(
            1
            for stage in stages
            if isinstance(stage, dict) and stage.get("status") in {"complete", "satisfied_by_topology"}
        )
    return summary


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _raise_experiment_error(
            "E_EXPERIMENT_080_INPUT_MISSING",
            f"{label} is missing.",
            path=str(path),
        )
    except json.JSONDecodeError as exc:
        _raise_experiment_error(
            "E_EXPERIMENT_080_INPUT_INVALID",
            f"{label} is not valid JSON: {exc}",
            path=str(path),
        )
    except OSError as exc:
        _raise_experiment_error(
            "E_EXPERIMENT_080_INPUT_READ_FAILED",
            f"Failed to read {label}: {exc}",
            path=str(path),
        )
    if not isinstance(payload, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_INPUT_INVALID",
            f"{label} must contain a JSON object.",
            path=str(path),
        )
    return payload


def _require_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_INPUT_INVALID",
            f"{path} must be a non-empty string.",
            path=path,
        )
    return value


def _validate_terminal_workflow(workflow_state: dict[str, Any]) -> None:
    if workflow_state.get("current_stage") is not None:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_NOT_TERMINAL",
            "Workspace run is not terminal; workflow_state.current_stage must be null.",
            current_stage=workflow_state.get("current_stage"),
        )
    statuses = workflow_state.get("stage_statuses")
    finalize = statuses.get("finalize") if isinstance(statuses, dict) and isinstance(statuses.get("finalize"), dict) else {}
    if finalize.get("status") != "complete":
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_NOT_FINALIZED",
            "Workspace run is not finalized; stage_statuses.finalize.status must be complete.",
            finalize_status=finalize.get("status"),
        )


def _validate_archive_manifest_ids(archive_manifest: dict[str, Any], *, run_id: str) -> None:
    if archive_manifest.get("schema_version") != RUN_ARCHIVE_SCHEMA:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_INVALID",
            f"run archive schema_version must be {RUN_ARCHIVE_SCHEMA}.",
            schema_version=archive_manifest.get("schema_version"),
        )
    archive_run_id = archive_manifest.get("run_id")
    runtime_manifest_run_id = archive_manifest.get("runtime_manifest_run_id")
    if archive_run_id != run_id or runtime_manifest_run_id != run_id:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_ID_MISMATCH",
            "runtime_manifest.run_id and archive run ids do not match.",
            run_record_run_id=run_id,
            archive_run_id=archive_run_id,
            archive_runtime_manifest_run_id=runtime_manifest_run_id,
        )


def _registered_run_integrity(container: dict[str, Any], *, path: str) -> dict[str, Any]:
    if "run_integrity" not in container:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_INTEGRITY_INVALID",
            f"{path} is required for run registration.",
            path=path,
        )
    verdict = interpret_run_integrity(container.get("run_integrity"), field_present=True)
    projection = project_for_read(verdict)
    if verdict.kind != "canonical" or projection.get("status") not in ALLOWED_RUN_INTEGRITY_STATUSES:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_INTEGRITY_INVALID",
            f"{path} must be a persisted run-integrity status, not malformed or unknown.",
            path=path,
            run_integrity=projection,
        )
    if projection.get("status") != "clean" and projection.get("reference_eligible") is True:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_INTEGRITY_INVALID",
            f"{path}.reference_eligible must be false for non-clean runs.",
            path=path,
        )
    return projection


def _validate_run_integrity_consistency(workflow: dict[str, Any], archive: dict[str, Any]) -> None:
    fields = ("status", "reference_eligible", "clean_single_shot")
    mismatches = [
        field
        for field in fields
        if workflow.get(field) != archive.get(field)
    ]
    if mismatches:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_INTEGRITY_MISMATCH",
            "workflow_state.run_integrity and archive run_integrity do not match.",
            mismatches=mismatches,
        )


def _compare_case_fact_layer_to_archive(
    *,
    frozen_fact_layer: dict[str, Any],
    archive_fact_layer: dict[str, Any],
    archive_root: Path,
) -> dict[str, Any]:
    case_shas = _case_fact_layer_shas(frozen_fact_layer)
    archive_shas = _archive_fact_layer_shas(archive_fact_layer, archive_root=archive_root)
    missing = sorted(REQUIRED_FACT_ARTIFACT_IDS - set(archive_shas))
    if missing:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "run archive fact_layer is missing required artifact ids.",
            missing_artifact_ids=missing,
        )
    mismatches: list[dict[str, Any]] = []
    for artifact_id in sorted(REQUIRED_FACT_ARTIFACT_IDS):
        case_sha = case_shas.get(artifact_id)
        archive_sha = archive_shas.get(artifact_id)
        if case_sha != archive_sha:
            mismatches.append({
                "artifact_id": artifact_id,
                "case_sha256": case_sha,
                "archive_sha256": archive_sha,
            })
    return {
        "matches_case_frozen_fact_layer": not mismatches,
        "comparison_semantics": "case_sha256_vs_archive_sha256_or_source_pack_sha256",
        "mismatches": mismatches,
    }


def _case_fact_layer_shas(frozen_fact_layer: dict[str, Any]) -> dict[str, str]:
    artifacts = frozen_fact_layer.get("artifacts") if isinstance(frozen_fact_layer.get("artifacts"), list) else []
    shas: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_id = artifact.get("artifact_id")
        sha = artifact.get("sha256")
        if isinstance(artifact_id, str) and isinstance(sha, str):
            shas[artifact_id] = sha
    return shas


def _archive_fact_layer_shas(archive_fact_layer: dict[str, Any], *, archive_root: Path) -> dict[str, str]:
    if archive_fact_layer.get("schema_version") != "mabw.run_archive.fact_layer.v1":
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "run archive fact_layer.schema_version is unsupported.",
            schema_version=archive_fact_layer.get("schema_version"),
        )
    artifacts = archive_fact_layer.get("artifacts")
    if not isinstance(artifacts, list):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "run archive fact_layer.artifacts must be a list.",
        )
    shas: dict[str, str] = {}
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            _raise_experiment_error(
                "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
                "run archive fact_layer.artifacts entries must be objects.",
                index=index,
            )
        artifact_id = str(artifact.get("artifact_id") or "")
        _reject_source_plan_archive_artifact(artifact, artifact_id=artifact_id, index=index)
        if not artifact_id:
            _raise_experiment_error(
                "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
                "run archive fact_layer artifact_id is required.",
                index=index,
            )
        if artifact_id not in REQUIRED_FACT_ARTIFACT_IDS:
            _raise_experiment_error(
                "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
                "run archive fact_layer contains an artifact id outside the 080 required fact layer.",
                artifact_id=artifact_id,
                index=index,
            )
        if artifact_id in seen:
            _raise_experiment_error(
                "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
                "run archive fact_layer contains duplicate artifact ids.",
                artifact_id=artifact_id,
            )
        seen.add(artifact_id)
        if artifact_id == "durable_source_evidence_or_source_pack":
            files = artifact.get("files")
            if not isinstance(files, list) or not files:
                _raise_experiment_error(
                    "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
                    "durable source evidence pack must provide files[].",
                    artifact_id=artifact_id,
                )
            normalized_files = []
            for file_index, file_record in enumerate(files):
                if not isinstance(file_record, dict):
                    _raise_experiment_error(
                        "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
                        "durable source evidence pack files[] entries must be objects.",
                        artifact_id=artifact_id,
                        file_index=file_index,
                    )
                normalized_files.append(_validated_archive_file_record(
                    archive_root=archive_root,
                    record=file_record,
                    context=f"{artifact_id}.files[{file_index}]",
                ))
            actual_pack_sha = _sha256_json(normalized_files)
            manifest_pack_sha = artifact.get("pack_sha256")
            if manifest_pack_sha != actual_pack_sha:
                _raise_experiment_error(
                    "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
                    "durable source evidence pack hash does not match files[].",
                    artifact_id=artifact_id,
                    expected_sha256=manifest_pack_sha,
                    actual_sha256=actual_pack_sha,
                )
            shas[artifact_id] = actual_pack_sha
            continue
        normalized = _validated_archive_file_record(
            archive_root=archive_root,
            record=artifact,
            context=artifact_id,
        )
        shas[artifact_id] = normalized["sha256"]
    return shas


def _validated_archive_file_record(
    *,
    archive_root: Path,
    record: dict[str, Any],
    context: str,
) -> dict[str, Any]:
    archive_path = record.get("archive_path")
    original_path = record.get("original_path")
    sha256 = record.get("sha256")
    size_bytes = record.get("size_bytes")
    if not isinstance(archive_path, str) or not archive_path.strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file record archive_path is required.",
            context=context,
        )
    if not isinstance(original_path, str) or not original_path.strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file record original_path is required.",
            context=context,
        )
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file record sha256 is invalid.",
            context=context,
        )
    if not isinstance(size_bytes, int) or size_bytes < 0:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file record size_bytes is invalid.",
            context=context,
        )
    if _unsafe_relative_archive_path(archive_path):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file record archive_path must be relative and safe.",
            context=context,
            archive_path=archive_path,
        )
    file_path = (archive_root / archive_path).resolve()
    try:
        file_path.relative_to(archive_root.resolve())
    except ValueError:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file record archive_path escapes archive.",
            context=context,
            archive_path=archive_path,
        )
    if not file_path.exists() or not file_path.is_file():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file is missing.",
            context=context,
            archive_path=archive_path,
        )
    actual_size = file_path.stat().st_size
    if actual_size != size_bytes:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file size does not match manifest.",
            context=context,
            archive_path=archive_path,
            expected_size_bytes=size_bytes,
            actual_size_bytes=actual_size,
        )
    actual_sha = _sha256_file(file_path)
    if actual_sha != sha256:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "archive fact-layer file hash does not match manifest.",
            context=context,
            archive_path=archive_path,
            expected_sha256=sha256,
            actual_sha256=actual_sha,
        )
    return {
        "archive_path": archive_path,
        "original_path": original_path,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def _unsafe_relative_archive_path(path_text: str) -> bool:
    return (
        not path_text
        or path_text.startswith("/")
        or Path(path_text).is_absolute()
        or PurePosixPath(path_text).is_absolute()
        or PureWindowsPath(path_text).is_absolute()
        or ".." in Path(path_text).parts
        or ".." in PurePosixPath(path_text).parts
        or ".." in PureWindowsPath(path_text).parts
    )


def _reject_source_plan_archive_artifact(artifact: dict[str, Any], *, artifact_id: str, index: int) -> None:
    path_values: list[str] = []
    for key in ("archive_path", "original_path", "path"):
        value = artifact.get(key)
        if isinstance(value, str):
            path_values.append(value)
    files = artifact.get("files")
    if isinstance(files, list):
        for file_record in files:
            if not isinstance(file_record, dict):
                continue
            for key in ("archive_path", "original_path", "path"):
                value = file_record.get(key)
                if isinstance(value, str):
                    path_values.append(value)
    if artifact_id in SOURCE_PLAN_ARTIFACT_IDS or any(path.endswith("source_candidates.yaml") for path in path_values):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "source_candidates/source-plan artifacts cannot be registered as frozen fact evidence.",
            artifact_id=artifact_id,
            index=index,
        )


def _registration_repo_commit(
    *,
    case_manifest: dict[str, Any],
    repo_workdir: str | Path | None,
) -> tuple[str, str]:
    if repo_workdir is not None:
        return _current_repo_commit(repo_workdir)
    return str(case_manifest.get("repo_commit") or ""), "case_manifest"


def _current_repo_commit(repo_workdir: str | Path) -> tuple[str, str]:
    root = Path(repo_workdir).expanduser().resolve()
    if not (root / "pyproject.toml").exists() or not (root / "src" / "multi_agent_brief").exists():
        _raise_experiment_error(
            "E_EXPERIMENT_080_REPO_WORKDIR_INVALID",
            "--repo-workdir must point to a MABW source checkout.",
            repo_workdir=str(root),
        )
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        _raise_experiment_error(
            "E_EXPERIMENT_080_REPO_WORKDIR_INVALID",
            "--repo-workdir must be a readable git checkout.",
            repo_workdir=str(root),
        )
    if dirty.stdout.strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_REPO_WORKDIR_DIRTY",
            "--repo-workdir must be clean before using git commit provenance.",
            repo_workdir=str(root),
        )
    commit = result.stdout.strip()
    if not commit:
        _raise_experiment_error(
            "E_EXPERIMENT_080_REPO_WORKDIR_INVALID",
            "--repo-workdir did not produce a git commit.",
            repo_workdir=str(root),
        )
    return commit, "git"


def _model_identity(*containers: dict[str, Any]) -> dict[str, str] | None:
    for container in containers:
        model = container.get("model") if isinstance(container, dict) else None
        if isinstance(model, str) and model.strip():
            return {"epistemic_status": "operator_reported", "value": model.strip()}
        if isinstance(model, dict):
            value = model.get("value")
            if isinstance(value, str) and value.strip():
                return {"epistemic_status": "operator_reported", "value": value.strip()}
    return None


def _write_run_record_idempotently(path: Path, payload: bytes) -> bool:
    return _write_experiment_output_idempotently(
        path,
        payload,
        artifact_label="run_record",
    )


def _write_experiment_output_idempotently(path: Path, payload: bytes, *, artifact_label: str) -> bool:
    if path.exists():
        if not path.is_file():
            _raise_experiment_error(
                "E_EXPERIMENT_080_OUTPUT_EXISTS",
                f"{artifact_label} output path exists but is not a file.",
                output=str(path),
            )
        existing = path.read_bytes()
        if existing == payload:
            return False
        _raise_experiment_error(
            "E_EXPERIMENT_080_OUTPUT_EXISTS",
            f"{artifact_label} output path already exists with different content.",
            output=str(path),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return True


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        _raise_experiment_error(
            "E_EXPERIMENT_080_OUTPUT_INVALID",
            "Path is not workspace-relative.",
            path=str(path),
        )


def _raise_experiment_error(code: str, message: str, **details: Any) -> None:
    raise Experiment080Error(message, details={"code": code, **details})


def _validate_a_controlled_scorecard(
    *,
    control: dict[str, Any],
    fact_layer: dict[str, Any],
    guidance_scores: list[Any],
    reader_clean: dict[str, Any],
    diagnostics: list[Experiment080Diagnostic],
) -> None:
    required_values = {
        "control_integrity.terminal_workflow": control.get("terminal_workflow"),
        "control_integrity.run_integrity_clean": control.get("run_integrity_clean"),
        "control_integrity.artifact_registry_valid": control.get("artifact_registry_valid"),
        "control_integrity.quality_gates_passed": control.get("quality_gates_passed"),
        "control_integrity.archive_present": control.get("archive_present"),
        "frozen_fact_layer.matches_case": fact_layer.get("matches_case"),
        "reader_clean.pass": reader_clean.get("pass"),
    }
    invalid_types = [field for field, value in required_values.items() if not isinstance(value, bool)]
    if invalid_types:
        diagnostics.append(_diag(
            "invalid_a_controlled_requirement_type",
            f"A_controlled requirement fields must be booleans: {invalid_types}.",
            path="scorecard.validity_class",
        ))
    required_true = {field: value is True for field, value in required_values.items()}
    failed = [field for field, ok in required_true.items() if not ok]
    if failed:
        diagnostics.append(_diag(
            "a_controlled_requirements_not_met",
            f"A_controlled scorecards require these fields to be true: {failed}.",
            path="scorecard.validity_class",
        ))
    invalid_methods: list[str] = []
    for idx, score in enumerate(guidance_scores):
        if not isinstance(score, dict):
            continue
        if score.get("relevant") is not True:
            continue
        method = score.get("assessment_method")
        if method not in A_CONTROLLED_ASSESSMENT_METHODS:
            invalid_methods.append(f"guidance_scores[{idx}].assessment_method={method!r}")
    if invalid_methods:
        diagnostics.append(_diag(
            "a_controlled_requires_human_assessment",
            "A_controlled scorecards require relevant guidance scores to use human or llm_assisted_human_review assessment.",
            path="scorecard.guidance_scores",
        ))


def _validate_guidance_score(
    score: dict[str, Any],
    diagnostics: list[Experiment080Diagnostic],
    *,
    path: str,
) -> None:
    entry_id = score.get("entry_id")
    if not isinstance(entry_id, str) or not _GUIDANCE_ENTRY_ID_RE.match(entry_id):
        diagnostics.append(_diag(
            "invalid_guidance_entry_id",
            f"{path}.entry_id must match AG-0001 style.",
            path=f"{path}.entry_id",
        ))
    if not isinstance(score.get("relevant"), bool):
        diagnostics.append(_diag(
            "invalid_guidance_relevance",
            f"{path}.relevant must be a boolean.",
            path=f"{path}.relevant",
        ))
    manifestation = score.get("manifestation_score")
    if not isinstance(manifestation, int) or manifestation not in {0, 1, 2, 3}:
        diagnostics.append(_diag(
            "invalid_manifestation_score",
            f"{path}.manifestation_score must be one of 0, 1, 2, 3.",
            path=f"{path}.manifestation_score",
        ))
    if not isinstance(score.get("overapplication"), bool):
        diagnostics.append(_diag(
            "invalid_overapplication",
            f"{path}.overapplication must be a boolean.",
            path=f"{path}.overapplication",
        ))
    elif isinstance(manifestation, int) and manifestation in {0, 1, 2, 3}:
        overapplication = bool(score.get("overapplication"))
        if manifestation == 3 and not overapplication:
            diagnostics.append(_diag(
                "overapplication_score_mismatch",
                f"{path}.overapplication must be true when manifestation_score is 3.",
                path=f"{path}.overapplication",
            ))
        if overapplication and manifestation != 3:
            diagnostics.append(_diag(
                "overapplication_score_mismatch",
                f"{path}.manifestation_score must be 3 when overapplication is true.",
                path=f"{path}.manifestation_score",
            ))
    method = score.get("assessment_method")
    if method not in ALLOWED_ASSESSMENT_METHODS:
        diagnostics.append(_diag(
            "invalid_assessment_method",
            f"{path}.assessment_method must be one of {sorted(ALLOWED_ASSESSMENT_METHODS)}.",
            path=f"{path}.assessment_method",
        ))


def _validate_run_integrity(
    value: Any,
    diagnostics: list[Experiment080Diagnostic],
    *,
    path: str,
) -> None:
    if not isinstance(value, dict):
        diagnostics.append(_diag("invalid_run_integrity", f"{path} must be an object.", path=path))
        return
    status = value.get("status")
    if status not in ALLOWED_RUN_INTEGRITY_STATUSES:
        diagnostics.append(_diag(
            "invalid_run_integrity_status",
            f"{path}.status must be one of {sorted(ALLOWED_RUN_INTEGRITY_STATUSES)}.",
            path=f"{path}.status",
        ))
    if "reference_eligible" in value and not isinstance(value.get("reference_eligible"), bool):
        diagnostics.append(_diag(
            "invalid_reference_eligible",
            f"{path}.reference_eligible must be a boolean when present.",
            path=f"{path}.reference_eligible",
        ))
    if status != "clean" and value.get("reference_eligible") is True:
        diagnostics.append(_diag(
            "contaminated_run_reference_eligible",
            f"{path}.reference_eligible must be false or omitted when status is not clean.",
            path=f"{path}.reference_eligible",
        ))


def _validate_condition(
    value: Any,
    diagnostics: list[Experiment080Diagnostic],
    *,
    path: str,
) -> None:
    if value not in ALLOWED_CONDITIONS:
        diagnostics.append(_diag(
            "unknown_condition",
            f"{path} must be one of {sorted(ALLOWED_CONDITIONS)}.",
            path=path,
        ))


def _validate_case_id_field(
    value: Any,
    diagnostics: list[Experiment080Diagnostic],
    *,
    path: str,
) -> None:
    if not isinstance(value, str) or not _CASE_ID_RE.match(value):
        diagnostics.append(_diag("invalid_case_id", f"{path} must be a stable lowercase id.", path=path))


def _read_json_object(
    path: Path,
    *,
    root: Path,
    label: str,
) -> tuple[dict[str, Any] | None, Experiment080Diagnostic | None]:
    if not path.exists():
        return None, _diag("missing_case_file", f"{label} is missing.", path=_relative_to_root(path, root))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, _diag(
            "invalid_json",
            f"{label} is not valid JSON: {exc}",
            path=_relative_to_root(path, root),
        )
    except OSError as exc:
        return None, _diag(
            "case_file_read_failed",
            f"Failed to read {label}: {exc}",
            path=_relative_to_root(path, root),
        )
    if not isinstance(payload, dict):
        return None, _diag(
            "invalid_json_object",
            f"{label} must contain a JSON object.",
            path=_relative_to_root(path, root),
        )
    return payload, None


def _scan_public_safe_case_files(root: Path, files: dict[str, Path]) -> list[Experiment080Diagnostic]:
    diagnostics: list[Experiment080Diagnostic] = []
    for path in files.values():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern in _LOCAL_PATH_PATTERNS:
                match = pattern.search(line)
                if match:
                    diagnostics.append(_diag(
                        "public_safe_private_path",
                        "public-safe 080 case files must not contain local/private paths.",
                        path=f"{_relative_to_root(path, root)}:{line_no}",
                    ))
                    break
    return diagnostics


def _validate_relative_path_ref(
    value: Any,
    *,
    key: str,
    expected: str,
    path: str,
) -> list[Experiment080Diagnostic]:
    diagnostics: list[Experiment080Diagnostic] = []
    if not isinstance(value, dict):
        return [_diag("invalid_path_ref", f"{path.rsplit('.', 1)[0]} must be an object.", path=path)]
    actual = value.get(key)
    diagnostics.extend(_validate_safe_relative_path(actual, path=path))
    if actual != expected:
        diagnostics.append(_diag(
            "unexpected_case_file_ref",
            f"{path} must be {expected!r} for the MABW-080 case layout.",
            path=path,
        ))
    return diagnostics


def _validate_safe_relative_path(value: Any, *, path: str) -> list[Experiment080Diagnostic]:
    if not isinstance(value, str) or not value.strip():
        return [_diag("invalid_relative_path", f"{path} must be a non-empty relative path.", path=path)]
    if value.lower().startswith("file://"):
        return [_diag("unsafe_path", f"{path} must not be a file:// path.", path=path)]
    if (
        Path(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        return [_diag("unsafe_path", f"{path} must be relative, not absolute.", path=path)]
    if (
        ".." in Path(value).parts
        or ".." in PurePosixPath(value).parts
        or ".." in PureWindowsPath(value).parts
    ):
        return [_diag("unsafe_path", f"{path} must not contain path traversal.", path=path)]
    return []


def _require_schema(
    payload: dict[str, Any],
    *,
    expected: str,
    label: str,
    diagnostics: list[Experiment080Diagnostic],
) -> None:
    if payload.get("schema_version") != expected:
        diagnostics.append(_diag(
            "unsupported_schema_version",
            f"{label}.schema_version must be {expected}.",
            path=f"{label}.schema_version",
        ))


def _require_non_empty_string(
    value: Any,
    diagnostics: list[Experiment080Diagnostic],
    *,
    path: str,
) -> None:
    if not isinstance(value, str) or not value.strip():
        diagnostics.append(_diag("missing_required_string", f"{path} must be a non-empty string.", path=path))


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _diag(code: str, message: str, *, path: str = "") -> Experiment080Diagnostic:
    return Experiment080Diagnostic(code=code, message=message, path=path)
