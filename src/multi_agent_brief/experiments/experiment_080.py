"""MABW-080 experiment harness validation and metadata registration.

080 validates whether approved Improvement Memory guidance manifests under a
frozen fact layer. Schema validators are side-effect free. ``register-run``,
``score-run``, ``import-assessment``, ``summarize``, and ``scaffold-condition``
write only the requested experiment metadata or deterministic scaffold outputs.
They must not mutate archive files, case files, agent assets, or Improvement
Ledger files; ``scaffold-condition`` requires an already initialized condition
workspace and only imports the frozen fact layer through the deterministic
fast-rerun import transaction.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import random
import re
import shlex
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from multi_agent_brief.experiments.target_contract import (
    AUDIT_BINDING_SCHEMA,
    ALLOWED_ASSESSMENT_TARGETS,
    ASSESSMENT_TARGET_CLAIM_SCOPE,
    ASSESSMENT_TARGET_EXCLUDED_CLAIM_SCOPE,
    AUDITABLE_TARGET_ARTIFACTS,
    DEFAULT_ASSESSMENT_TARGET,
    assessment_target as _target_contract_assessment_target,
    assessment_target_manifest,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.run_integrity import (
    PERSISTED_RUN_INTEGRITY_STATUSES,
    interpret_run_integrity,
    project_for_read,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.timing import derive_control_timing_from_path
from multi_agent_brief.quality_gates.contract import (
    interpret_quality_gate_binding,
    project_quality_gate_binding_for_read,
    require_quality_gate_binding_pass,
)


EXPERIMENT_080_ID = "MABW-080"

CASE_MANIFEST_SCHEMA = "mabw.experiment_080.case.v1"
FROZEN_FACT_LAYER_SCHEMA = "mabw.experiment_080.frozen_fact_layer.v1"
GUIDANCE_SET_SCHEMA = "mabw.experiment_080.guidance_set.v1"
RUN_RECORD_SCHEMA = "mabw.experiment_080.run_record.v1"
SCORECARD_SCHEMA = "mabw.experiment_080.scorecard.v1"
ASSESSMENT_SCHEMA = "mabw.experiment_080.assessment.v1"
CASE_SUMMARY_SCHEMA = "mabw.experiment_080.case_summary.v1"
SCAFFOLD_CONDITION_SCHEMA = "mabw.experiment_080.scaffold_condition.v1"
CASE_VALIDATION_SCHEMA = "mabw.experiment_080.case_validation.v1"
RUN_ARCHIVE_SCHEMA = "mabw.run_archive.v1"
BLIND_PACK_SCHEMA = "mabw.experiment_080.blind_pack.v1"
BLIND_REVEAL_MAPPING_SCHEMA = "mabw.experiment_080.blind_reveal_mapping.v1"
BLIND_ITEM_ID_RE = re.compile(r"^BI-[A-Z]$")

SCAFFOLD_METADATA_PATH = "experiment/080/condition.json"
SCAFFOLD_INSTRUCTIONS_PATH = "experiment/080/operator_instructions.md"

ALLOWED_CONDITIONS = {"baseline", "memory", "prompt_only"}
TREATMENT_VISIBILITY_SCHEMA = "mabw.experiment_080.treatment_visibility.v1"
PROMPT_GUIDANCE_BLOCK_SCHEMA = "mabw.experiment_080.prompt_guidance_block.v1"
TREATMENT_VISIBLE_MATERIALS = {
    "baseline": (),
    "memory": ("output/intermediate/improvement_memory_snapshot.md",),
    "prompt_only": ("handoff.prompt_guidance_block",),
}
TREATMENT_RUNTIME_VISIBLE_FILES = (
    "config.yaml",
    "sources.yaml",
    "user.md",
    "audience_profile.md",
    "improvement/ledger.jsonl",
    "improvement/memory.md",
    SCAFFOLD_METADATA_PATH,
    SCAFFOLD_INSTRUCTIONS_PATH,
    "output/intermediate/agent_handoff.md",
    "output/intermediate/agent_handoff.json",
    "output/intermediate/runtime_handoff.json",
    "output/intermediate/improvement_memory_snapshot.md",
)
PROMPT_ONLY_GUIDANCE_TEXT_ALLOWED_FILES = {
    SCAFFOLD_METADATA_PATH,
    SCAFFOLD_INSTRUCTIONS_PATH,
    "output/intermediate/agent_handoff.md",
    "output/intermediate/agent_handoff.json",
    "output/intermediate/runtime_handoff.json",
}
MEMORY_APPROVED_LIVE_STORE_FILES = {
    "improvement/ledger.jsonl",
    "improvement/memory.md",
}
ALLOWED_VALIDITY_CLASSES = {
    "A_controlled",
    "B_integration",
    "invalid_contaminated",
    "invalid_incomplete",
    "invalid_fact_layer_mismatch",
}
INTERPRETABLE_SCORECARD_VALIDITY_CLASSES = {"A_controlled", "B_integration"}
SUMMARY_LOW_N_DENOMINATOR_THRESHOLD = 3
ALLOWED_RUN_INTEGRITY_STATUSES = PERSISTED_RUN_INTEGRITY_STATUSES
ALLOWED_GUIDANCE_SOURCES = {"improvement_ledger", "manual", "prompt_only"}
ALLOWED_ASSESSMENT_METHODS = {"human", "llm_assisted_human_review", "llm_only"}
A_CONTROLLED_ASSESSMENT_METHODS = {"human", "llm_assisted_human_review"}
ALLOWED_SCORECARD_ASSESSMENT_STATUSES = {"assessed", "needs_assessment"}
DELIVERY_BRIEF_REQUIRED_CONTROL_KEYS = (
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
    "treatment_isolation_passed",
)
AUDITABLE_BRIEF_REQUIRED_CONTROL_KEYS = (
    "auditor_complete",
    "run_integrity_clean",
    "reference_eligible",
    "artifact_registry_valid",
    "audit_binding_valid",
    "audited_brief_frozen_valid",
    "audit_report_frozen_valid",
    "auditor_gate_report_valid",
    "auditor_gates_no_blocking",
    "fact_layer_matches",
    "treatment_isolation_passed",
)
A_CONTROLLED_REQUIRED_CONTROL_KEYS = DELIVERY_BRIEF_REQUIRED_CONTROL_KEYS
A_CONTROLLED_REQUIRED_CONTROL_KEYS_BY_TARGET = {
    "delivery_brief": DELIVERY_BRIEF_REQUIRED_CONTROL_KEYS,
    "auditable_brief": AUDITABLE_BRIEF_REQUIRED_CONTROL_KEYS,
}
AUDITABLE_TIMING_STAGE_ORDER = ["analyst", "editor", "auditor"]

REQUIRED_FACT_ARTIFACT_IDS = {
    "durable_source_evidence_or_source_pack",
    "input_classification",
    "candidate_claims",
    "screened_candidates",
    "claim_ledger",
}
FAST_RERUN_IMPORTED_UPSTREAM_STAGE_IDS = {
    "doctor",
    "source-discovery",
    "input-governance",
    "scout",
    "screener",
    "claim-ledger",
}
FAST_RERUN_DOWNSTREAM_TIMED_STAGE_IDS = {"analyst", "editor", "auditor"}

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
    _validate_assessment_target(
        payload.get("assessment_target", DEFAULT_ASSESSMENT_TARGET),
        diagnostics,
        path="case_manifest.assessment_target",
    )
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
    target = _assessment_target(payload)
    _validate_assessment_target(
        payload.get("assessment_target", DEFAULT_ASSESSMENT_TARGET),
        diagnostics,
        path="run_record.assessment_target",
    )
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        diagnostics.append(_diag("invalid_run_id", "run_record.run_id is required.", path="run_record.run_id"))
    for key in ("workspace_path", "repo_commit", "runtime"):
        _require_non_empty_string(payload.get(key), diagnostics, path=f"run_record.{key}")
    if target == "delivery_brief":
        _require_non_empty_string(payload.get("run_archive_path"), diagnostics, path="run_record.run_archive_path")
    elif "run_archive_path" in payload and payload.get("run_archive_path") is not None:
        if not isinstance(payload.get("run_archive_path"), str):
            diagnostics.append(_diag(
                "invalid_run_archive_path",
                "run_record.run_archive_path must be a string when present.",
                path="run_record.run_archive_path",
            ))
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
    target_artifacts = payload.get("target_artifacts")
    if target == "auditable_brief":
        _validate_auditable_audit_binding_schema(
            payload.get("audit_binding"),
            diagnostics,
            path="run_record.audit_binding",
        )
        if not isinstance(target_artifacts, dict):
            diagnostics.append(_diag(
                "invalid_target_artifacts",
                "auditable run_record.target_artifacts must be an object.",
                path="run_record.target_artifacts",
            ))
        else:
            for artifact_id, expected_path in AUDITABLE_TARGET_ARTIFACTS.items():
                artifact = target_artifacts.get(artifact_id)
                artifact_path = f"run_record.target_artifacts.{artifact_id}"
                if not isinstance(artifact, dict):
                    diagnostics.append(_diag(
                        "missing_target_artifact",
                        f"{artifact_path} must be an object.",
                        path=artifact_path,
                    ))
                    continue
                if artifact.get("path") != expected_path:
                    diagnostics.append(_diag(
                        "invalid_target_artifact_path",
                        f"{artifact_path}.path must be {expected_path}.",
                        path=f"{artifact_path}.path",
                    ))
                sha = artifact.get("sha256")
                if not isinstance(sha, str) or not _SHA256_RE.match(sha):
                    diagnostics.append(_diag(
                        "invalid_target_artifact_sha256",
                        f"{artifact_path}.sha256 must be a lowercase SHA-256 hex digest.",
                        path=f"{artifact_path}.sha256",
                    ))
    return diagnostics


def _validate_auditable_audit_binding_schema(
    binding: Any,
    diagnostics: list[Experiment080Diagnostic],
    *,
    path: str,
) -> None:
    if not isinstance(binding, dict):
        diagnostics.append(_diag(
            "invalid_audit_binding",
            f"{path} must be an object for auditable_brief.",
            path=path,
        ))
        return
    if binding.get("schema_version") != AUDIT_BINDING_SCHEMA:
        diagnostics.append(_diag(
            "invalid_audit_binding_schema",
            f"{path}.schema_version must be {AUDIT_BINDING_SCHEMA}.",
            path=f"{path}.schema_version",
        ))
    for field in ("claim_ledger_sha256", "audited_brief_sha256", "audit_report_sha256"):
        value = binding.get(field)
        if not isinstance(value, str) or not _SHA256_RE.match(value):
            diagnostics.append(_diag(
                "invalid_audit_binding_sha256",
                f"{path}.{field} must be a lowercase SHA-256 hex digest.",
                path=f"{path}.{field}",
            ))
    repair_ids = binding.get("relevant_repair_transaction_ids")
    if not isinstance(repair_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in repair_ids
    ):
        diagnostics.append(_diag(
            "invalid_audit_binding_repair_ids",
            f"{path}.relevant_repair_transaction_ids must be a list of non-empty strings.",
            path=f"{path}.relevant_repair_transaction_ids",
        ))
    _require_non_empty_string(
        binding.get("auditor_stage_transaction_id"),
        diagnostics,
        path=f"{path}.auditor_stage_transaction_id",
    )


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
    guidance_set = _load_json_object(case_root / "guidance_set.json", label="guidance_set")
    conditions = case_manifest.get("conditions") if isinstance(case_manifest.get("conditions"), list) else []
    if condition not in conditions:
        _raise_experiment_error(
            "E_EXPERIMENT_080_CONDITION_INVALID",
            f"Condition {condition!r} is not declared by case_manifest.conditions.",
            condition=condition,
            allowed_conditions=[item for item in conditions if item in ALLOWED_CONDITIONS],
        )
    assessment_target = _assessment_target(case_manifest)

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

    workflow_integrity = _registered_run_integrity(workflow_state, path="workflow_state.run_integrity")
    target_artifacts: dict[str, Any] | None = None
    target_workflow: dict[str, Any] | None = None
    audit_binding: dict[str, Any] | None = None
    run_archive_path = ""
    if assessment_target == "delivery_brief":
        _validate_terminal_workflow(workflow_state)
        archive_manifest_path = ws / "output" / "runs" / run_id / "manifest.json"
        archive_manifest = _load_json_object(archive_manifest_path, label="run_archive_manifest")
        _validate_archive_manifest_ids(archive_manifest, run_id=run_id)

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
        timing = _run_record_timing(timing, runtime_manifest=runtime_manifest)
        run_archive_path = _portable_run_archive_path(
            output_path=output_path,
            workspace=ws,
            archive_manifest_path=archive_manifest_path,
        )
    else:
        _validate_auditable_workflow_ready(workflow_state)
        imported_fact_layer = _auditable_imported_fact_layer_comparison(
            workspace=ws,
            case_root=case_root,
            frozen_fact_layer=frozen_fact_layer,
            runtime_manifest=runtime_manifest,
        )
        target_artifacts = _auditable_target_artifacts(workspace=ws, repo_workdir=repo_workdir)
        audit_binding = _auditable_audit_binding_projection(
            workspace=ws,
            workflow_state=workflow_state,
            target_artifacts=target_artifacts,
        )
        target_workflow = _auditable_target_workflow(workflow_state)
        timing = _run_record_timing(
            derive_control_timing_from_path(
                intermediate / "event_log.jsonl",
                workflow_state=workflow_state,
                run_integrity=workflow_integrity,
                stage_order=AUDITABLE_TIMING_STAGE_ORDER,
                expected_run_id=run_id,
            ),
            runtime_manifest=runtime_manifest,
        )

    repo_commit, repo_commit_source = _registration_repo_commit(
        case_manifest=case_manifest,
        repo_workdir=repo_workdir,
    )
    runtime = _require_text(runtime_manifest.get("runtime"), "runtime_manifest.runtime")
    treatment_isolation = _registered_treatment_isolation(
        workspace=ws,
        condition=condition,
        guidance_set=guidance_set,
    )

    run_record: dict[str, Any] = {
        "schema_version": RUN_RECORD_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "condition": condition,
        "run_id": run_id,
        "assessment_target": assessment_target,
        "assessment_target_manifest": assessment_target_manifest(assessment_target),
        "workspace_path": "<redacted-workspace>",
        "run_archive_path": run_archive_path,
        "repo_commit": repo_commit,
        "repo_commit_source": repo_commit_source,
        "runtime": runtime,
        "run_integrity": workflow_integrity,
        "timing": timing,
        "imported_fact_layer": imported_fact_layer,
        "treatment_isolation": treatment_isolation,
    }
    if target_artifacts is not None:
        run_record["target_artifacts"] = target_artifacts
    if audit_binding is not None:
        run_record["audit_binding"] = audit_binding
    if target_workflow is not None:
        run_record["target_workflow"] = target_workflow
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
    case_target = _assessment_target(case_manifest)
    record_target = _assessment_target(record)
    if record_target != case_target:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_TARGET_MISMATCH",
            "run_record.assessment_target does not match case_manifest.assessment_target.",
            run_record_assessment_target=record_target,
            case_manifest_assessment_target=case_target,
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


def export_blind_pack(
    *,
    case_dir: str | Path,
    scorecards: list[str | Path],
    output: str | Path,
    seed: str | None = None,
) -> dict[str, Any]:
    """Export a condition-blind, hash-bound assessment pack.

    The scorer-facing pack strips condition names, run IDs, workspace paths,
    treatment metadata, and scorecard control metadata. The separate reveal
    mapping binds each blind item to the source scorecard, condition, run ID,
    and audited-brief artifact hash for later deterministic assessment import.
    """

    case_root = Path(case_dir).expanduser().resolve()
    output_dir = Path(output).expanduser()
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()
    else:
        output_dir = output_dir.resolve()
    if output_dir.exists() and not output_dir.is_dir():
        _raise_experiment_error(
            "E_EXPERIMENT_080_OUTPUT_EXISTS",
            "blind pack output path exists but is not a directory.",
            output=str(output_dir),
        )
    if not scorecards:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "export-blind-pack requires at least one --scorecard path.",
        )

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
    case_target = _assessment_target(case_manifest)
    if case_target != "auditable_brief":
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_TARGET_INVALID",
            "blind assessment packs are currently supported only for assessment_target=auditable_brief.",
            assessment_target=case_target,
        )

    loaded: list[dict[str, Any]] = []
    for raw_path in scorecards:
        scorecard_path = Path(raw_path).expanduser()
        if not scorecard_path.is_absolute():
            scorecard_path = (Path.cwd() / scorecard_path).resolve()
        else:
            scorecard_path = scorecard_path.resolve()
        scorecard = _read_scorecard_file(scorecard_path)
        if scorecard.get("case_id") != case_manifest.get("case_id"):
            _raise_experiment_error(
                "E_EXPERIMENT_080_SCORECARD_INVALID",
                "scorecard.case_id does not match case_manifest.case_id.",
                scorecard_case_id=scorecard.get("case_id"),
                case_id=case_manifest.get("case_id"),
            )
        if _assessment_target(scorecard) != "auditable_brief":
            _raise_experiment_error(
                "E_EXPERIMENT_080_ASSESSMENT_TARGET_INVALID",
                "blind assessment pack scorecards must use assessment_target=auditable_brief.",
                scorecard_assessment_target=_assessment_target(scorecard),
            )
        target_readiness = (
            scorecard.get("target_readiness")
            if isinstance(scorecard.get("target_readiness"), dict)
            else {}
        )
        if target_readiness.get("ready_for_assessment_import") is not True:
            _raise_experiment_error(
                "E_EXPERIMENT_080_SCORECARD_INVALID",
                "blind assessment pack scorecards must be ready for assessment import.",
                scorecard_path=str(scorecard_path),
                target_readiness=target_readiness,
            )
        artifact_path, artifact_sha = _resolve_blind_audited_brief(
            case_root=case_root,
            scorecard_path=scorecard_path,
            scorecard=scorecard,
        )
        loaded.append({
            "path": scorecard_path,
            "scorecard": scorecard,
            "scorecard_sha256": _sha256_json(scorecard),
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha,
        })

    if len(loaded) > 26:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "export-blind-pack supports at most 26 scorecards for A-Z blind labels.",
            scorecard_count=len(loaded),
        )

    ordered = sorted(
        loaded,
        key=lambda item: (
            str(item["scorecard"].get("condition") or ""),
            str(item["scorecard"].get("run_id") or ""),
            item["scorecard_sha256"],
        ),
    )
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    shuffled = list(ordered)
    rng.shuffle(shuffled)

    output_dir.mkdir(parents=True, exist_ok=True)
    items_dir = output_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    pack_items: list[dict[str, Any]] = []
    reveal_items: list[dict[str, Any]] = []
    for idx, item in enumerate(shuffled):
        label = chr(ord("A") + idx)
        blind_item_id = f"BI-{label}"
        item_dir = items_dir / blind_item_id
        item_dir.mkdir(parents=True, exist_ok=True)
        artifact_rel = f"items/{blind_item_id}/audited_brief.md"
        artifact_bytes = item["artifact_path"].read_bytes()
        _write_experiment_output_idempotently(
            output_dir / artifact_rel,
            artifact_bytes,
            artifact_label="blind audited brief artifact",
        )
        artifact_sha = _sha256_file(output_dir / artifact_rel)
        if artifact_sha != item["artifact_sha256"]:
            _raise_experiment_error(
                "E_EXPERIMENT_080_BLIND_PACK_INVALID",
                "blind item artifact hash changed while exporting.",
                blind_item_id=blind_item_id,
            )
        guidance_ids = _scorecard_guidance_entry_ids(item["scorecard"])
        item_record = {
            "schema_version": BLIND_PACK_SCHEMA,
            "blind_item_id": blind_item_id,
            "display_label": label,
            "assessment_target": "auditable_brief",
            "artifact_role": "audited_brief",
            "artifact_path": artifact_rel,
            "artifact_sha256": artifact_sha,
            "scorecard_sha256": item["scorecard_sha256"],
            "guidance_entry_ids": guidance_ids,
            "condition_blind": True,
            "hash_bound": True,
        }
        _write_experiment_output_idempotently(
            item_dir / "item.json",
            _json_bytes(item_record),
            artifact_label="blind item metadata",
        )
        pack_items.append(item_record)
        reveal_items.append({
            "blind_item_id": blind_item_id,
            "condition": item["scorecard"]["condition"],
            "run_id": item["scorecard"]["run_id"],
            "scorecard_sha256": item["scorecard_sha256"],
            "artifact_sha256": artifact_sha,
            "guidance_entry_ids": guidance_ids,
            "scorecard": item["scorecard"],
        })

    pack = {
        "schema_version": BLIND_PACK_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "assessment_target": "auditable_brief",
        "condition_blind": True,
        "guidance_blind": False,
        "hash_bound": True,
        "rubric": _blind_pack_rubric(guidance_set),
        "items": pack_items,
        "notes": [
            "Scorer-facing pack intentionally strips condition names, run IDs, local paths, and treatment metadata.",
            "This pack is for external assessment only; it is not a delivery artifact.",
        ],
    }
    reveal = {
        "schema_version": BLIND_REVEAL_MAPPING_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "assessment_target": "auditable_brief",
        "blind_pack_schema_version": BLIND_PACK_SCHEMA,
        "randomization_seed": seed if seed is not None else None,
        "items": reveal_items,
    }
    pack_written = _write_experiment_output_idempotently(
        output_dir / "blind_pack.json",
        _json_bytes(pack),
        artifact_label="blind pack",
    )
    reveal_written = _write_experiment_output_idempotently(
        output_dir / "reveal_mapping.json",
        _json_bytes(reveal),
        artifact_label="blind reveal mapping",
    )
    return {
        "ok": True,
        "schema_version": BLIND_PACK_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "assessment_target": "auditable_brief",
        "output": str(output_dir),
        "blind_pack": str(output_dir / "blind_pack.json"),
        "reveal_mapping": str(output_dir / "reveal_mapping.json"),
        "blind_item_count": len(pack_items),
        "blind_item_ids": [item["blind_item_id"] for item in pack_items],
        "written": pack_written or reveal_written,
    }


def import_assessment(
    *,
    scorecard: str | Path | None,
    assessment: str | Path,
    output: str | Path,
    blind_pack: str | Path | None = None,
    reveal_mapping: str | Path | None = None,
) -> dict[str, Any]:
    """Import human/assisted manifestation assessment into a scorecard.

    This command validates and merges externally supplied assessment metadata.
    It does not judge whether guidance manifested, prose improved, or factual
    regressions occurred.
    """

    blind_pack_path = _optional_resolved_path(blind_pack)
    reveal_mapping_path = _optional_resolved_path(reveal_mapping)
    if (blind_pack_path is None) != (reveal_mapping_path is None):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind assessment import requires both --blind-pack and --reveal-mapping.",
        )
    if blind_pack_path is None and scorecard is None:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "import-assessment requires --scorecard unless --blind-pack and --reveal-mapping are supplied.",
        )

    scorecard_path: Path | None = None
    if scorecard is not None:
        scorecard_path = Path(scorecard).expanduser()
        if not scorecard_path.is_absolute():
            scorecard_path = (Path.cwd() / scorecard_path).resolve()
        else:
            scorecard_path = scorecard_path.resolve()
    assessment_path = Path(assessment).expanduser()
    if not assessment_path.is_absolute():
        assessment_path = (Path.cwd() / assessment_path).resolve()
    else:
        assessment_path = assessment_path.resolve()
    output_path = Path(output).expanduser()
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    else:
        output_path = output_path.resolve()

    blind_import_verified = blind_pack_path is not None and reveal_mapping_path is not None
    if blind_import_verified:
        scorecard_payload, assessment_payload = _scorecard_and_assessment_from_blind_import(
            blind_pack_path=blind_pack_path,
            reveal_mapping_path=reveal_mapping_path,
            assessment_path=assessment_path,
        )
    else:
        assert scorecard_path is not None
        scorecard_payload = _load_json_object(scorecard_path, label="scorecard")
        assessment_payload = _load_json_object(assessment_path, label="assessment")
        _reject_unverified_blind_assessment_metadata(assessment_payload)

    scorecard_diagnostics = validate_scorecard(scorecard_payload)
    if scorecard_diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "scorecard.json failed schema validation.",
            errors=[diagnostic.to_dict() for diagnostic in scorecard_diagnostics],
        )
    assessment_diagnostics = validate_assessment(assessment_payload)
    if assessment_diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_INVALID",
            "assessment.json failed schema validation.",
            errors=[diagnostic.to_dict() for diagnostic in assessment_diagnostics],
        )
    _validate_assessment_identity(scorecard_payload, assessment_payload)
    guidance_scores = _assessment_guidance_scores_for_scorecard(
        scorecard_payload=scorecard_payload,
        assessment=assessment_payload,
    )
    assessed_scorecard = _scorecard_with_imported_assessment(
        scorecard=scorecard_payload,
        assessment=assessment_payload,
        guidance_scores=guidance_scores,
        blind_import_verified=blind_import_verified,
    )
    diagnostics = validate_scorecard(assessed_scorecard)
    if diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "Assessed scorecard.json failed schema validation.",
            errors=[diagnostic.to_dict() for diagnostic in diagnostics],
        )

    record_bytes = _json_bytes(assessed_scorecard)
    written = _write_experiment_output_idempotently(
        output_path,
        record_bytes,
        artifact_label="scorecard",
    )
    return {
        "ok": True,
        "schema_version": SCORECARD_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": assessed_scorecard["case_id"],
        "condition": assessed_scorecard["condition"],
        "run_id": assessed_scorecard["run_id"],
        "validity_class": assessed_scorecard["validity_class"],
        "assessment_status": assessed_scorecard["assessment_status"],
        "output": str(output_path),
        "written": written,
        "scorecard": assessed_scorecard,
    }


def summarize_case(
    *,
    case_dir: str | Path,
    output: str | Path | None = None,
    scorecards: list[str | Path] | None = None,
) -> dict[str, Any]:
    """Summarize MABW-080 scorecards for a case.

    The summary reads scorecards only. It aggregates deterministic control and
    externally imported assessment fields; it does not judge guidance
    manifestation, prose quality, preference, or factual-regression semantics.
    """

    case_root = Path(case_dir).expanduser().resolve()
    output_path: Path | None = None
    if output is not None:
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
    scorecard_records = _discover_case_scorecards(
        case_root=case_root,
        output_path=output_path,
        scorecard_paths=scorecards or [],
    )
    summary = _build_case_summary(case_manifest=case_manifest, scorecards=scorecard_records)
    written = False
    if output_path is not None:
        written = _write_experiment_output_idempotently(
            output_path,
            _json_bytes(summary),
            artifact_label="case_summary",
        )
    return {
        "ok": True,
        "schema_version": CASE_SUMMARY_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": summary["case_id"],
        "scorecard_count": summary["scorecard_count"],
        "scorecard_paths": [record["path"] for record in scorecard_records],
        "output": str(output_path) if output_path is not None else None,
        "written": written,
        "summary": summary,
    }


def scaffold_condition(
    *,
    case_dir: str | Path,
    condition: str,
    workspace: str | Path,
    archive: str | Path | None = None,
    runtime: str = "hermes",
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Prepare an initialized 080 condition workspace using deterministic fast-rerun import.

    This helper prepares workspace state only. It does not run subagents,
    finalize output, register runs, score runs, or summarize experiments.
    """

    case_root = Path(case_dir).expanduser().resolve()
    ws = Path(workspace).expanduser().resolve()
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
    guidance_set = _load_json_object(case_root / "guidance_set.json", label="guidance_set")
    conditions = case_manifest.get("conditions") if isinstance(case_manifest.get("conditions"), list) else []
    if condition not in conditions:
        _raise_experiment_error(
            "E_EXPERIMENT_080_CONDITION_INVALID",
            "scaffold-condition condition is not declared by case_manifest.conditions.",
            condition=condition,
            allowed_conditions=[item for item in conditions if item in ALLOWED_CONDITIONS],
        )
    archive_manifest = _resolve_scaffold_archive_manifest(
        case_root=case_root,
        frozen_fact_layer=frozen_fact_layer,
        archive=archive,
    )
    archive_payload = _load_json_object(archive_manifest, label="run_archive")
    _assert_scaffold_archive_matches_case(
        frozen_fact_layer=frozen_fact_layer,
        archive_manifest=archive_payload,
        archive_manifest_path=archive_manifest,
    )
    _require_scaffold_workspace_shell(workspace=ws)
    _reject_improvement_memory_for_non_memory_condition(workspace=ws, condition=condition)
    _reject_treatment_guidance_leakage(
        workspace=ws,
        condition=condition,
        guidance_set=guidance_set,
        allowed_guidance_text_files=set(),
    )
    _reject_existing_scaffold_metadata(ws)
    removed_placeholders = _remove_scaffold_init_placeholders(ws)

    from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError, import_fact_layer_transaction

    try:
        state = import_fact_layer_transaction(
            workspace=ws,
            archive=archive_manifest,
            runtime=runtime,
            repo_workdir=repo_workdir,
            actor="cli",
        )
    except RuntimeStateError as exc:
        _restore_scaffold_init_placeholders(removed_placeholders)
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCAFFOLD_IMPORT_FAILED",
            str(exc),
            runtime_error_code=getattr(exc, "error_code", ""),
            runtime_error_details=getattr(exc, "details", {}),
        )

    metadata = _scaffold_condition_metadata(
        case_manifest=case_manifest,
        guidance_set=guidance_set,
        condition=condition,
        workspace=ws,
        archive_manifest=archive_manifest,
        state=state,
    )
    instructions = _scaffold_condition_instructions(metadata)
    metadata_path = ws / SCAFFOLD_METADATA_PATH
    instructions_path = ws / SCAFFOLD_INSTRUCTIONS_PATH
    _write_scaffold_metadata_files(
        metadata_path=metadata_path,
        metadata=metadata,
        instructions_path=instructions_path,
        instructions=instructions,
    )
    return {
        "ok": True,
        "schema_version": SCAFFOLD_CONDITION_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": metadata["case_id"],
        "condition": metadata["condition"],
        "workspace": str(ws),
        "source_archive_manifest": metadata["source_archive_manifest"],
        "metadata_path": SCAFFOLD_METADATA_PATH,
        "operator_instructions_path": SCAFFOLD_INSTRUCTIONS_PATH,
        "next_command": metadata["next_command"],
        "fact_layer_import": metadata["fact_layer_import"],
        "metadata": metadata,
    }


def _resolve_scaffold_archive_manifest(
    *,
    case_root: Path,
    frozen_fact_layer: dict[str, Any],
    archive: str | Path | None,
) -> Path:
    raw = archive if archive is not None else frozen_fact_layer.get("source_archive_path")
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_MISSING",
            "scaffold-condition requires --archive or frozen_fact_layer.source_archive_path.",
        )
    raw_path = Path(raw).expanduser()
    candidates: list[Path]
    if raw_path.is_absolute():
        candidates = [raw_path]
    else:
        candidates = [
            case_root / raw_path,
            case_root.parent / raw_path,
            Path.cwd() / raw_path,
        ]
    for candidate in candidates:
        path = candidate / "manifest.json" if candidate.is_dir() else candidate
        if path.exists() and path.is_file():
            return path.resolve()
    _raise_experiment_error(
        "E_EXPERIMENT_080_ARCHIVE_MISSING",
        "scaffold-condition could not find a run archive manifest.",
        archive=str(raw),
        searched=[str(candidate) for candidate in candidates],
    )


def _assert_scaffold_archive_matches_case(
    *,
    frozen_fact_layer: dict[str, Any],
    archive_manifest: dict[str, Any],
    archive_manifest_path: Path,
) -> None:
    if archive_manifest.get("schema_version") != RUN_ARCHIVE_SCHEMA:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_INVALID",
            f"run archive manifest schema_version must be {RUN_ARCHIVE_SCHEMA}.",
            archive=str(archive_manifest_path),
            schema_version=archive_manifest.get("schema_version"),
        )
    fact_layer = archive_manifest.get("fact_layer")
    if not isinstance(fact_layer, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID",
            "run archive manifest must contain a fact_layer object.",
            archive=str(archive_manifest_path),
        )
    comparison = _compare_case_fact_layer_to_archive(
        frozen_fact_layer=frozen_fact_layer,
        archive_fact_layer=fact_layer,
        archive_root=archive_manifest_path.parent,
    )
    if comparison.get("matches_case_frozen_fact_layer") is not True:
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_MISMATCH",
            "scaffold-condition archive fact layer does not match case frozen_fact_layer.json.",
            archive=str(archive_manifest_path),
            mismatches=comparison.get("mismatches", []),
        )


def _require_scaffold_workspace_shell(*, workspace: Path) -> None:
    if workspace.exists() and not workspace.is_dir():
        _raise_experiment_error(
            "E_EXPERIMENT_080_WORKSPACE_INVALID",
            "scaffold-condition workspace path exists but is not a directory.",
            workspace=str(workspace),
        )
    required_files = ("config.yaml", "sources.yaml", "user.md", "audience_profile.md")
    missing = [rel_path for rel_path in required_files if not (workspace / rel_path).is_file()]
    if missing:
        _raise_experiment_error(
            "E_EXPERIMENT_080_WORKSPACE_INVALID",
            (
                "scaffold-condition requires an initialized condition workspace. "
                "Copy or initialize a seed/template workspace first so config, user, audience, "
                "source policy, report date, and freshness controls remain experiment constants."
            ),
            workspace=str(workspace),
            missing_files=missing,
        )


def _reject_improvement_memory_for_non_memory_condition(*, workspace: Path, condition: str) -> None:
    if condition == "memory":
        return
    improvement_dir = workspace / "improvement"
    existing: list[str] = []
    for rel_path in (
        "improvement/ledger.jsonl",
        "improvement/memory.md",
        "output/intermediate/improvement_memory_snapshot.md",
    ):
        path = workspace / rel_path
        if path.exists():
            existing.append(rel_path)
    if improvement_dir.exists() and improvement_dir.is_dir():
        for path in sorted(improvement_dir.rglob("*"), key=lambda item: item.relative_to(workspace).as_posix()):
            if not path.is_file() or path.name.startswith("."):
                continue
            rel_path = path.relative_to(workspace).as_posix()
            if rel_path in existing:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 1
            if size > 0:
                existing.append(rel_path)
    for rel_path in (
        "output/intermediate/runtime_manifest.json",
        "output/intermediate/agent_handoff.json",
        "output/intermediate/runtime_handoff.json",
    ):
        path = workspace / rel_path
        if rel_path in existing or not path.exists() or not path.is_file():
            continue
        residue = _improvement_residue_in_control_json(path, rel_path=rel_path)
        if residue:
            existing.append(residue)
    if existing:
        _raise_experiment_error(
            "E_EXPERIMENT_080_TREATMENT_CONTAMINATION",
            (
                "scaffold-condition baseline and prompt_only workspaces must not contain "
                "Improvement Memory files or runtime residues. Remove improvement artifacts, "
                "snapshot/handoff residues, or use the memory condition."
            ),
            workspace=str(workspace),
            condition=condition,
            existing_improvement_files=existing,
        )


def _improvement_residue_in_control_json(path: Path, *, rel_path: str) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return f"{rel_path}:unreadable"
    if not isinstance(payload, dict):
        return None
    if rel_path.endswith("runtime_manifest.json"):
        improvement = payload.get("improvement")
        if isinstance(improvement, dict) and _runtime_manifest_improvement_has_residue(improvement):
            return f"{rel_path}:improvement"
    if rel_path.endswith("agent_handoff.json") or rel_path.endswith("runtime_handoff.json"):
        improvement_files = payload.get("improvement_memory_files")
        if isinstance(improvement_files, dict) and any(
            isinstance(value, str) and value.strip() for value in improvement_files.values()
        ):
            return f"{rel_path}:improvement_memory_files"
    return None


def _runtime_manifest_improvement_has_residue(improvement: dict[str, Any]) -> bool:
    materialized = improvement.get("materialized_entry_ids")
    if isinstance(materialized, list) and materialized:
        return True
    for key in ("ledger_sha256", "snapshot_path", "snapshot_sha256"):
        value = improvement.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _guidance_leak_markers(guidance_set: dict[str, Any]) -> list[dict[str, str]]:
    markers: list[dict[str, str]] = []
    for entry in guidance_set.get("entries", []):
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("entry_id") or "")
        for field in ("guidance_text", "expected_manifestation"):
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                markers.append({
                    "entry_id": entry_id,
                    "field": field,
                    "text": value.strip(),
                })
    return markers


def _runtime_visible_treatment_files(workspace: Path) -> list[Path]:
    files: list[Path] = []
    for rel_path in TREATMENT_RUNTIME_VISIBLE_FILES:
        path = workspace / rel_path
        if path.is_file():
            files.append(path)
    return files


def _reject_treatment_guidance_leakage(
    *,
    workspace: Path,
    condition: str,
    guidance_set: dict[str, Any],
    allowed_guidance_text_files: set[str],
) -> None:
    leaks = _treatment_guidance_leaks(
        workspace=workspace,
        condition=condition,
        guidance_set=guidance_set,
        allowed_guidance_text_files=allowed_guidance_text_files,
    )
    if leaks:
        _raise_experiment_error(
            "E_EXPERIMENT_080_TREATMENT_CONTAMINATION",
            (
                "080 condition workspace exposes treatment guidance outside the "
                "allowed condition-specific surface."
            ),
            workspace=str(workspace),
            condition=condition,
            treatment_guidance_leaks=leaks,
        )


def _treatment_guidance_leaks(
    *,
    workspace: Path,
    condition: str,
    guidance_set: dict[str, Any],
    allowed_guidance_text_files: set[str],
) -> list[dict[str, str]]:
    leaks: list[dict[str, str]] = []
    markers = _guidance_leak_markers(guidance_set)
    if not markers:
        return leaks
    for path in _runtime_visible_treatment_files(workspace):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel_path = path.relative_to(workspace).as_posix()
        for marker in markers:
            if marker["text"] not in text:
                continue
            if (
                condition == "prompt_only"
                and marker["field"] == "guidance_text"
                and rel_path in allowed_guidance_text_files
            ):
                continue
            if (
                condition == "memory"
                and marker["field"] == "guidance_text"
                and (
                    rel_path == "output/intermediate/improvement_memory_snapshot.md"
                    or rel_path in MEMORY_APPROVED_LIVE_STORE_FILES
                )
            ):
                continue
            leaks.append({
                "path": rel_path,
                "entry_id": marker["entry_id"],
                "field": marker["field"],
            })
    return leaks


def _treatment_visibility_contract(condition: str) -> dict[str, Any]:
    return {
        "schema_version": TREATMENT_VISIBILITY_SCHEMA,
        "condition": condition,
        "allowed_visible_treatment_materials": list(TREATMENT_VISIBLE_MATERIALS[condition]),
        "forbidden_visible_fields": ["expected_manifestation"]
        if condition == "prompt_only"
        else ["guidance_text", "expected_manifestation"],
        "semantics": (
            "exact_runtime_visibility_contract; no semantic leakage or synonym scanning"
        ),
    }


def _prompt_guidance_block(guidance_entries: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": PROMPT_GUIDANCE_BLOCK_SCHEMA,
        "source": "case_guidance_set",
        "guidance": [
            {
                "entry_id": entry["entry_id"],
                "guidance_text": entry["guidance_text"],
            }
            for entry in guidance_entries
        ],
    }


def _treatment_isolation_projection(
    *,
    workspace: Path,
    condition: str,
    guidance_set: dict[str, Any],
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    guidance_entries = _scaffold_guidance_entries(guidance_set)
    guidance_entry_ids = [entry["entry_id"] for entry in guidance_entries]
    details: list[dict[str, Any]] = []
    status = "pass"

    if metadata is None:
        return {
            "schema_version": TREATMENT_VISIBILITY_SCHEMA,
            "status": "not_checked",
            "condition": condition,
            "reason": "scaffold_condition_metadata_missing",
        }

    if metadata.get("condition") != condition:
        status = "fail"
        details.append({
            "reason": "condition_metadata_mismatch",
            "expected": condition,
            "actual": metadata.get("condition"),
        })

    visibility = metadata.get("treatment_visibility")
    if not isinstance(visibility, dict):
        status = "fail"
        details.append({"reason": "treatment_visibility_missing"})
    else:
        allowed = visibility.get("allowed_visible_treatment_materials")
        if allowed != list(TREATMENT_VISIBLE_MATERIALS[condition]):
            status = "fail"
            details.append({
                "reason": "allowed_visible_treatment_materials_mismatch",
                "expected": list(TREATMENT_VISIBLE_MATERIALS[condition]),
                "actual": allowed,
            })

    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else {}
    prompt_block = handoff.get("prompt_guidance_block") if isinstance(handoff, dict) else None
    snapshot_path = workspace / "output" / "intermediate" / "improvement_memory_snapshot.md"

    if condition == "baseline":
        if isinstance(prompt_block, dict):
            status = "fail"
            details.append({"reason": "baseline_prompt_guidance_block_present"})
        if snapshot_path.exists():
            status = "fail"
            details.append({
                "reason": "baseline_improvement_memory_snapshot_present",
                "path": "output/intermediate/improvement_memory_snapshot.md",
            })
    elif condition == "memory":
        if isinstance(prompt_block, dict):
            status = "fail"
            details.append({"reason": "memory_prompt_guidance_block_present"})
        snapshot_status = _memory_snapshot_treatment_status(
            workspace=workspace,
            guidance_entry_ids=guidance_entry_ids,
        )
        if snapshot_status["status"] != "pass":
            status = "fail"
            details.append(snapshot_status)
    elif condition == "prompt_only":
        if snapshot_path.exists():
            status = "fail"
            details.append({
                "reason": "prompt_only_improvement_memory_snapshot_present",
                "path": "output/intermediate/improvement_memory_snapshot.md",
            })
        block_status = _prompt_guidance_block_status(
            prompt_block=prompt_block,
            guidance_entries=guidance_entries,
        )
        if block_status["status"] != "pass":
            status = "fail"
            details.append(block_status)

    allowed_guidance_text_files = (
        PROMPT_ONLY_GUIDANCE_TEXT_ALLOWED_FILES if condition == "prompt_only" else set()
    )
    leaks = _treatment_guidance_leaks(
        workspace=workspace,
        condition=condition,
        guidance_set=guidance_set,
        allowed_guidance_text_files=allowed_guidance_text_files,
    )
    if leaks:
        status = "fail"
        details.append({"reason": "treatment_guidance_leakage", "leaks": leaks})

    return {
        "schema_version": TREATMENT_VISIBILITY_SCHEMA,
        "status": status,
        "condition": condition,
        "allowed_visible_treatment_materials": list(TREATMENT_VISIBLE_MATERIALS[condition]),
        "guidance_entry_ids": guidance_entry_ids,
        "details": details,
    }


def _memory_snapshot_treatment_status(*, workspace: Path, guidance_entry_ids: list[str]) -> dict[str, Any]:
    rel_path = "output/intermediate/improvement_memory_snapshot.md"
    path = workspace / rel_path
    if not path.is_file():
        return {"status": "fail", "reason": "memory_snapshot_missing", "path": rel_path}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": "fail", "reason": "memory_snapshot_unreadable", "path": rel_path, "error": str(exc)}
    if "mabw:improvement-memory-snapshot" not in text:
        return {"status": "fail", "reason": "memory_snapshot_marker_missing", "path": rel_path}
    expected = set(guidance_entry_ids)
    actual = _snapshot_selected_entry_ids(text)
    if actual != expected:
        return {
            "status": "fail",
            "reason": "memory_snapshot_guidance_entry_ids_mismatch",
            "path": rel_path,
            "missing_entry_ids": sorted(expected - actual),
            "unexpected_entry_ids": sorted(actual - expected),
        }
    return {"status": "pass", "path": rel_path}


def _snapshot_selected_entry_ids(text: str) -> set[str]:
    for line in text.splitlines():
        if not line.startswith("selected_entry_ids:"):
            continue
        raw = line.split(":", 1)[1].strip()
        if raw in {"", "none"}:
            return set()
        return {item.strip() for item in raw.split(",") if item.strip()}
    return set()


def _prompt_guidance_block_status(
    *,
    prompt_block: Any,
    guidance_entries: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(prompt_block, dict):
        return {"status": "fail", "reason": "prompt_guidance_block_missing"}
    if prompt_block.get("schema_version") != PROMPT_GUIDANCE_BLOCK_SCHEMA:
        return {
            "status": "fail",
            "reason": "prompt_guidance_block_schema_invalid",
            "schema_version": prompt_block.get("schema_version"),
        }
    guidance = prompt_block.get("guidance")
    if not isinstance(guidance, list):
        return {"status": "fail", "reason": "prompt_guidance_block_guidance_not_list"}
    expected = {entry["entry_id"]: entry["guidance_text"] for entry in guidance_entries}
    actual: dict[str, str] = {}
    duplicate_entry_ids: set[str] = set()
    for item in guidance:
        if not isinstance(item, dict):
            continue
        entry_id = item.get("entry_id")
        text = item.get("guidance_text")
        if isinstance(entry_id, str) and isinstance(text, str):
            if entry_id in actual:
                duplicate_entry_ids.add(entry_id)
            actual[entry_id] = text
    if duplicate_entry_ids:
        return {
            "status": "fail",
            "reason": "prompt_guidance_block_duplicate_guidance_entry_ids",
            "duplicate_entry_ids": sorted(duplicate_entry_ids),
        }
    unexpected = sorted(set(actual) - set(expected))
    if unexpected:
        return {
            "status": "fail",
            "reason": "prompt_guidance_block_unexpected_guidance",
            "unexpected_entry_ids": unexpected,
        }
    missing = [entry_id for entry_id in expected if actual.get(entry_id) != expected[entry_id]]
    if missing:
        return {
            "status": "fail",
            "reason": "prompt_guidance_block_missing_guidance",
            "missing_entry_ids": missing,
        }
    return {"status": "pass"}


def _registered_treatment_isolation(
    *,
    workspace: Path,
    condition: str,
    guidance_set: dict[str, Any],
) -> dict[str, Any]:
    metadata_path = workspace / SCAFFOLD_METADATA_PATH
    if not metadata_path.exists():
        return {
            "schema_version": TREATMENT_VISIBILITY_SCHEMA,
            "status": "not_checked",
            "condition": condition,
            "reason": "scaffold_condition_metadata_missing",
        }
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _raise_experiment_error(
            "E_EXPERIMENT_080_TREATMENT_CONTAMINATION",
            "080 scaffold condition metadata is unreadable; treatment isolation cannot be verified.",
            workspace=str(workspace),
            condition=condition,
            metadata_path=SCAFFOLD_METADATA_PATH,
            error=str(exc),
        )
    if not isinstance(metadata, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_TREATMENT_CONTAMINATION",
            "080 scaffold condition metadata must contain an object.",
            workspace=str(workspace),
            condition=condition,
            metadata_path=SCAFFOLD_METADATA_PATH,
        )
    projection = _treatment_isolation_projection(
        workspace=workspace,
        condition=condition,
        guidance_set=guidance_set,
        metadata=metadata,
    )
    if projection.get("status") != "pass":
        _raise_experiment_error(
            "E_EXPERIMENT_080_TREATMENT_CONTAMINATION",
            "080 condition treatment isolation failed.",
            workspace=str(workspace),
            condition=condition,
            treatment_isolation=projection,
        )
    return projection


def _reject_existing_scaffold_metadata(workspace: Path) -> None:
    existing = [
        path.relative_to(workspace).as_posix()
        for path in (workspace / SCAFFOLD_METADATA_PATH, workspace / SCAFFOLD_INSTRUCTIONS_PATH)
        if path.exists()
    ]
    if existing:
        _raise_experiment_error(
            "E_EXPERIMENT_080_WORKSPACE_INVALID",
            "scaffold-condition workspace already contains experiment scaffold metadata.",
            workspace=str(workspace),
            existing=existing,
        )


def _remove_scaffold_init_placeholders(workspace: Path) -> list[tuple[Path, bytes]]:
    """Remove known init-only source placeholders before fact-layer import.

    ``multi-agent-brief init`` creates ``input/sources/README.md`` as operator
    guidance. It is not evidence and would otherwise make the strict fast-rerun
    import reject a normal initialized condition workspace. Real source-like
    leftovers remain untouched so the import transaction can fail closed.
    """

    placeholder = workspace / "input" / "sources" / "README.md"
    removed: list[tuple[Path, bytes]] = []
    if placeholder.is_file():
        removed.append((placeholder, placeholder.read_bytes()))
        placeholder.unlink()
    return removed


def _restore_scaffold_init_placeholders(removed: list[tuple[Path, bytes]]) -> None:
    for path, content in removed:
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _scaffold_condition_metadata(
    *,
    case_manifest: dict[str, Any],
    guidance_set: dict[str, Any],
    condition: str,
    workspace: Path,
    archive_manifest: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    manifest = state.get("manifest") if isinstance(state.get("manifest"), dict) else {}
    workflow = state.get("workflow_state") if isinstance(state.get("workflow_state"), dict) else {}
    fact_import = manifest.get("fact_layer_import") if isinstance(manifest.get("fact_layer_import"), dict) else {}
    guidance_entries = _scaffold_guidance_entries(guidance_set)
    treatment: dict[str, Any] = {
        "condition": condition,
        "guidance_entry_ids": [entry["entry_id"] for entry in guidance_entries],
    }
    handoff: dict[str, Any] = {}
    if condition == "baseline":
        treatment.update({
            "improvement_memory": "disabled",
            "operator_requirement": "Do not use Improvement Memory or prompt-only guidance for this condition.",
        })
    elif condition == "memory":
        treatment.update({
            "improvement_memory": "requires_approved_snapshot",
            "memory_ready": "requires_runtime_snapshot",
            "memory_ready_check": (
                "Before registering the run, verify the runtime created "
                "output/intermediate/improvement_memory_snapshot.md from approved Improvement Memory "
                "and that the snapshot includes guidance_entry_ids."
            ),
            "operator_requirement": (
                "Expose treatment only through the approved frozen Improvement Memory snapshot. "
                "Do not add a prompt-only guidance block."
            ),
        })
    else:
        handoff["prompt_guidance_block"] = _prompt_guidance_block(guidance_entries)
        treatment.update({
            "improvement_memory": "disabled",
            "operator_requirement": (
                "Inject only the explicit handoff.prompt_guidance_block as prompt-only treatment. "
                "Do not create or use Improvement Memory."
            ),
        })
    return {
        "schema_version": SCAFFOLD_CONDITION_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": str(case_manifest.get("case_id") or ""),
        "condition": condition,
        "assessment_target": _assessment_target(case_manifest),
        "assessment_target_manifest": assessment_target_manifest(_assessment_target(case_manifest)),
        "workspace_path": "<redacted-workspace>",
        "source_archive_manifest": fact_import.get("source_archive_manifest")
        or archive_manifest.as_posix(),
        "runtime_run_id": manifest.get("run_id"),
        "current_stage": workflow.get("current_stage"),
        "fact_layer_import": {
            "schema_version": fact_import.get("schema_version"),
            "source_run_id": fact_import.get("source_run_id"),
            "source_archive_manifest": fact_import.get("source_archive_manifest"),
            "fact_layer_sha256": fact_import.get("fact_layer_sha256"),
            "satisfied_stage_ids": fact_import.get("satisfied_stage_ids", []),
            "timing_comparability": fact_import.get("timing_comparability"),
            "imported_file_count": fact_import.get("imported_file_count"),
        },
        "treatment_visibility": _treatment_visibility_contract(condition),
        "treatment": treatment,
        "handoff": handoff,
        "next_command": (
            f"multi-agent-brief run --workspace {shlex.quote(str(workspace))} "
            "--recipe fast-rerun --skip-doctor"
        ),
        "boundaries": [
            "scaffold-condition imports a frozen fact layer and writes experiment metadata only",
            "it does not run subagents, gates, finalize, register-run, score-run, or summarize",
            "source-discovery, scout, screener, and claim-ledger are satisfied by import and must not be replayed",
            "condition outputs must be registered and scored by later explicit 080 commands",
        ],
    }


def _scaffold_guidance_entries(guidance_set: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for entry in guidance_set.get("entries", []):
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("entry_id")
        guidance_text = entry.get("guidance_text")
        if not isinstance(entry_id, str) or not isinstance(guidance_text, str):
            continue
        payload = {
            "entry_id": entry_id,
            "source": str(entry.get("source") or ""),
            "guidance_text": guidance_text,
            "expected_manifestation": str(entry.get("expected_manifestation") or ""),
        }
        entries.append(payload)
    return entries


def _scaffold_condition_instructions(metadata: dict[str, Any]) -> str:
    treatment = metadata.get("treatment") if isinstance(metadata.get("treatment"), dict) else {}
    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else {}
    prompt_block = handoff.get("prompt_guidance_block") if isinstance(handoff.get("prompt_guidance_block"), dict) else {}
    lines = [
        f"# MABW-080 Condition Scaffold: {metadata.get('case_id')} / {metadata.get('condition')}",
        "",
        "This workspace has been prepared with deterministic fast-rerun fact-layer import.",
        "",
        "## What Is Already Satisfied",
        "",
        "- Source discovery, input governance, Scout, Screener, and Claim Ledger are satisfied by import.",
        "- The next executable workflow stage is Analyst.",
        "- Do not rerun source-discovery, Scout, Screener, or Claim Ledger for this condition.",
        "",
        "## Condition Treatment",
        "",
        f"- Condition: `{metadata.get('condition')}`",
        f"- Improvement Memory: `{treatment.get('improvement_memory')}`",
        f"- Operator requirement: {treatment.get('operator_requirement')}",
    ]
    prompt_guidance = prompt_block.get("guidance")
    if isinstance(prompt_guidance, list) and prompt_guidance:
        lines.extend(["", "Prompt-only guidance block (`handoff.prompt_guidance_block`):"])
        for item in prompt_guidance:
            if not isinstance(item, dict):
                continue
            entry_id = item.get("entry_id")
            text = item.get("guidance_text")
            if isinstance(entry_id, str) and isinstance(text, str):
                lines.append(f"- {entry_id}: {text}")
    memory_ready_check = treatment.get("memory_ready_check")
    if isinstance(memory_ready_check, str) and memory_ready_check:
        lines.extend([
            "",
            "Memory condition readiness check:",
            f"- {memory_ready_check}",
        ])
    lines.extend([
        "",
        "## Next Command",
        "",
        "```bash",
        str(metadata.get("next_command") or ""),
        "```",
        "",
        "This scaffold is not a completed experiment run. After runtime completion, use `register-run`, `score-run`,",
        "`import-assessment`, and `summarize` explicitly.",
        "",
    ])
    return "\n".join(lines)


def _write_scaffold_metadata_files(
    *,
    metadata_path: Path,
    metadata: dict[str, Any],
    instructions_path: Path,
    instructions: str,
) -> None:
    _write_experiment_output_idempotently(
        metadata_path,
        _json_bytes(metadata),
        artifact_label="scaffold_condition",
    )
    _write_text_output_idempotently(
        instructions_path,
        instructions if instructions.endswith("\n") else f"{instructions}\n",
        artifact_label="scaffold_instructions",
    )


def _write_text_output_idempotently(path: Path, text: str, *, artifact_label: str) -> bool:
    return _write_experiment_output_idempotently(
        path,
        text.encode("utf-8"),
        artifact_label=artifact_label,
    )


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
    target = _assessment_target(payload)
    _validate_assessment_target(
        payload.get("assessment_target", DEFAULT_ASSESSMENT_TARGET),
        diagnostics,
        path="scorecard.assessment_target",
    )

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
    if target == "auditable_brief":
        _validate_auditable_audit_binding_schema(
            payload.get("audit_binding"),
            diagnostics,
            path="scorecard.audit_binding",
        )
    treatment_isolation = (
        payload.get("treatment_isolation")
        if isinstance(payload.get("treatment_isolation"), dict)
        else {}
    )

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
            assessment_target=target,
            treatment_isolation=treatment_isolation,
            diagnostics=diagnostics,
        )
    return diagnostics


def validate_assessment(payload: dict[str, Any]) -> list[Experiment080Diagnostic]:
    """Validate imported MABW-080 manifestation assessment metadata."""

    diagnostics: list[Experiment080Diagnostic] = []
    _require_schema(payload, expected=ASSESSMENT_SCHEMA, label="assessment", diagnostics=diagnostics)
    if payload.get("experiment_id") != EXPERIMENT_080_ID:
        diagnostics.append(_diag(
            "invalid_experiment_id",
            f"assessment.experiment_id must be {EXPERIMENT_080_ID}.",
            path="assessment.experiment_id",
        ))
    _validate_case_id_field(payload.get("case_id"), diagnostics, path="assessment.case_id")
    _validate_condition(payload.get("condition"), diagnostics, path="assessment.condition")
    _require_non_empty_string(payload.get("run_id"), diagnostics, path="assessment.run_id")
    _require_non_empty_string(payload.get("assessed_at"), diagnostics, path="assessment.assessed_at")
    _require_non_empty_string(payload.get("assessed_by"), diagnostics, path="assessment.assessed_by")
    if "validity_class" in payload:
        diagnostics.append(_diag(
            "assessment_must_not_set_validity_class",
            "assessment files must not set scorecard validity_class; Python derives it from control fields and imported scores.",
            path="assessment.validity_class",
        ))

    guidance_scores = payload.get("guidance_scores")
    if not isinstance(guidance_scores, list):
        diagnostics.append(_diag(
            "invalid_guidance_scores",
            "assessment.guidance_scores must be a non-empty list.",
            path="assessment.guidance_scores",
        ))
        guidance_scores = []
    elif not guidance_scores:
        diagnostics.append(_diag(
            "empty_guidance_scores",
            "assessment.guidance_scores must contain at least one guidance score.",
            path="assessment.guidance_scores",
        ))
    seen_guidance_score_ids: set[str] = set()
    for idx, score in enumerate(guidance_scores):
        path = f"assessment.guidance_scores[{idx}]"
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
    assessment_target = _assessment_target(run_record)
    fact_layer_matches = imported_fact_layer.get("matches_case_frozen_fact_layer") is True
    reader_clean = (
        archive_projection["reader_clean"]
        if _reader_clean_required_for_target(assessment_target)
        else {
            "pass": None,
            "status": "not_required_for_target",
            "source": "assessment_target.auditable_brief",
        }
    )
    gate_status = (
        archive_projection["quality_gates"]
        if assessment_target == "delivery_brief"
        else _auditable_scorecard_gate_status(run_record)
    )
    finalize_status = (
        archive_projection["finalize"]
        if assessment_target == "delivery_brief"
        else {
            "complete": False,
            "report_pass": False,
            "report_status": "not_required_for_target",
            "source": "assessment_target.auditable_brief",
        }
    )
    archive_status = archive_projection["archive"]
    timing_summary = _scorecard_timing_summary(run_record.get("timing"))
    base_control_integrity = {
        "run_integrity_clean": run_integrity.get("status") == "clean",
        "reference_eligible": run_integrity.get("reference_eligible") is True,
        "timing_available": timing_summary["status"] in {"available", "downstream_only"},
        "fact_layer_matches": fact_layer_matches,
        "treatment_isolation_passed": _scorecard_treatment_isolation_passed(run_record),
    }
    if assessment_target == "delivery_brief":
        control_integrity = {
            **base_control_integrity,
            "terminal_workflow": True,
            "artifact_registry_valid": archive_projection["artifact_registry_valid"],
            "quality_gates_passed": gate_status["passed"],
            "archive_present": archive_status["present"],
            "archive_schema_valid": archive_status["schema_valid"],
            "finalize_complete": finalize_status["complete"],
            "finalize_report_pass": finalize_status["report_pass"],
        }
    else:
        target_projection = _auditable_scorecard_target_projection(run_record)
        control_integrity = {
            **base_control_integrity,
            "terminal_workflow": run_record.get("target_workflow", {}).get("current_stage") is None
            if isinstance(run_record.get("target_workflow"), dict)
            else False,
            "auditor_complete": _auditable_scorecard_auditor_complete(run_record),
            "artifact_registry_valid": target_projection["artifact_registry_valid"],
            "audit_binding_valid": target_projection["audit_binding_valid"],
            "audited_brief_frozen_valid": target_projection["audited_brief_frozen_valid"],
            "audit_report_frozen_valid": target_projection["audit_report_frozen_valid"],
            "auditor_gate_report_valid": target_projection["auditor_gate_report_valid"],
            "auditor_gates_no_blocking": target_projection["auditor_gates_no_blocking"],
            "quality_gates_passed": gate_status["passed"],
            "archive_present": archive_status["present"],
            "archive_schema_valid": archive_status["schema_valid"],
            "finalize_complete": finalize_status["complete"],
            "finalize_report_pass": finalize_status["report_pass"],
        }
    validity_class = _scorecard_validity_class(
        run_integrity=run_integrity,
        fact_layer_matches=fact_layer_matches,
        control_integrity=control_integrity,
        reader_clean=reader_clean,
        assessment_target=assessment_target,
    )
    target_readiness = _scorecard_target_readiness(
        run_integrity=run_integrity,
        fact_layer_matches=fact_layer_matches,
        control_integrity=control_integrity,
        reader_clean=reader_clean,
        assessment_target=assessment_target,
    )
    guidance_entries = guidance_set.get("entries") if isinstance(guidance_set.get("entries"), list) else []
    scorecard = {
        "schema_version": SCORECARD_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_manifest["case_id"],
        "condition": run_record["condition"],
        "run_id": run_record["run_id"],
        "assessment_target": assessment_target,
        "assessment_target_manifest": assessment_target_manifest(assessment_target),
        "claim_scope": ASSESSMENT_TARGET_CLAIM_SCOPE[assessment_target],
        "excluded_claim_scope": ASSESSMENT_TARGET_EXCLUDED_CLAIM_SCOPE[assessment_target],
        "validity_class": validity_class,
        "assessment_status": "needs_assessment",
        "target_readiness": target_readiness,
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
        "treatment_isolation": run_record.get("treatment_isolation", {
            "schema_version": TREATMENT_VISIBILITY_SCHEMA,
            "status": "not_checked",
            "condition": run_record["condition"],
            "reason": "run_record_treatment_isolation_missing",
        }),
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
    if assessment_target == "auditable_brief":
        scorecard["target_artifacts"] = run_record.get("target_artifacts", {})
        scorecard["audit_binding"] = run_record.get("audit_binding", {})
    return scorecard


def _scorecard_treatment_isolation_passed(run_record: dict[str, Any]) -> bool:
    treatment = run_record.get("treatment_isolation")
    return isinstance(treatment, dict) and treatment.get("status") == "pass"


def _scorecard_target_readiness(
    *,
    run_integrity: dict[str, Any],
    fact_layer_matches: bool,
    control_integrity: dict[str, Any],
    reader_clean: dict[str, Any],
    assessment_target: str,
) -> dict[str, Any]:
    required_keys = list(_control_keys_for_target(assessment_target))
    missing_control_keys = [key for key in required_keys if control_integrity.get(key) is not True]
    reasons: list[str] = []
    status = "complete"
    if run_integrity.get("status") != "clean" or run_integrity.get("reference_eligible") is False:
        status = "invalid_contaminated"
        reasons.append("run_integrity is not clean/reference-eligible")
    elif not fact_layer_matches:
        status = "invalid_fact_layer_mismatch"
        reasons.append("frozen fact layer does not match case")
    elif missing_control_keys:
        status = "incomplete"
        reasons.extend(f"missing required control: {key}" for key in missing_control_keys)
    elif _reader_clean_required_for_target(assessment_target) and reader_clean.get("pass") is not True:
        status = "incomplete"
        reasons.append("reader_clean is required for this target and did not pass")
    return {
        "schema_version": "mabw.experiment_080.target_readiness.v1",
        "assessment_target": assessment_target,
        "status": status,
        "ready_for_assessment_import": status == "complete",
        "required_control_keys": required_keys,
        "missing_control_keys": missing_control_keys,
        "reasons": reasons,
        "validity_class_semantics": (
            "validity_class remains the formal assessed-result class; target_readiness only "
            "describes deterministic target control readiness"
        ),
    }


def _scorecard_validity_class(
    *,
    run_integrity: dict[str, Any],
    fact_layer_matches: bool,
    control_integrity: dict[str, Any],
    reader_clean: dict[str, Any],
    assessment_target: str,
) -> str:
    if run_integrity.get("status") != "clean" or run_integrity.get("reference_eligible") is False:
        return "invalid_contaminated"
    if not fact_layer_matches:
        return "invalid_fact_layer_mismatch"
    if any(control_integrity.get(key) is not True for key in _control_keys_for_target(assessment_target)):
        return "invalid_incomplete"
    if _reader_clean_required_for_target(assessment_target) and reader_clean.get("pass") is not True:
        return "invalid_incomplete"
    return "invalid_incomplete"


def _validate_assessment_identity(scorecard: dict[str, Any], assessment: dict[str, Any]) -> None:
    mismatches = [
        key
        for key in ("case_id", "condition", "run_id")
        if scorecard.get(key) != assessment.get(key)
    ]
    if mismatches:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_MISMATCH",
            "assessment identity does not match scorecard.",
            mismatches=mismatches,
            scorecard_identity={
                "case_id": scorecard.get("case_id"),
                "condition": scorecard.get("condition"),
                "run_id": scorecard.get("run_id"),
            },
            assessment_identity={
                "case_id": assessment.get("case_id"),
                "condition": assessment.get("condition"),
                "run_id": assessment.get("run_id"),
            },
        )


def _reject_unverified_blind_assessment_metadata(assessment: dict[str, Any]) -> None:
    blind_fields = [
        field
        for field in ("blind_pack", "blind_item_id", "blind_artifact_sha256")
        if field in assessment
    ]
    if blind_fields:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind assessment metadata is accepted only through --blind-pack and --reveal-mapping verification.",
            blind_fields=blind_fields,
        )


def _optional_resolved_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        return (Path.cwd() / path).resolve()
    return path.resolve()


def _resolve_blind_audited_brief(
    *,
    case_root: Path,
    scorecard_path: Path,
    scorecard: dict[str, Any],
) -> tuple[Path, str]:
    target_artifacts = (
        scorecard.get("target_artifacts")
        if isinstance(scorecard.get("target_artifacts"), dict)
        else {}
    )
    audited = target_artifacts.get("audited_brief") if isinstance(target_artifacts.get("audited_brief"), dict) else {}
    rel_path = audited.get("path")
    expected_sha = audited.get("sha256")
    if not isinstance(rel_path, str) or _unsafe_relative_archive_path(rel_path):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_PACK_INVALID",
            "scorecard target_artifacts.audited_brief.path must be a safe relative path.",
            scorecard_path=str(scorecard_path),
        )
    if not isinstance(expected_sha, str) or not _SHA256_RE.match(expected_sha):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_PACK_INVALID",
            "scorecard target_artifacts.audited_brief.sha256 must be present before blind export.",
            scorecard_path=str(scorecard_path),
        )
    direct_candidates = _blind_artifact_direct_candidates(
        case_root=case_root,
        scorecard_path=scorecard_path,
        rel_path=rel_path,
    )
    mismatches: list[dict[str, str]] = []
    direct_match = _matching_blind_artifact_candidate(
        candidates=direct_candidates,
        expected_sha=expected_sha,
        mismatches=mismatches,
    )
    if direct_match is not None:
        return direct_match
    recursive_candidates = _blind_artifact_workspace_candidates(
        case_root=case_root,
        scorecard_path=scorecard_path,
        rel_path=rel_path,
    )
    recursive_match = _matching_blind_artifact_candidate(
        candidates=recursive_candidates,
        expected_sha=expected_sha,
        mismatches=mismatches,
    )
    if recursive_match is not None:
        return recursive_match
    searched = _unique_paths([*direct_candidates, *recursive_candidates])
    _raise_experiment_error(
        "E_EXPERIMENT_080_BLIND_PACK_INVALID",
        "audited brief artifact could not be resolved for blind export. Place scorecards beside the workspace root, run export from the experiment root, or keep condition workspaces near the case directory.",
        scorecard_path=str(scorecard_path),
        artifact_path=rel_path,
        expected_sha256=expected_sha,
        mismatches=mismatches,
        searched=[str(candidate) for candidate in searched],
    )


def _matching_blind_artifact_candidate(
    *,
    candidates: list[Path],
    expected_sha: str,
    mismatches: list[dict[str, str]],
) -> tuple[Path, str] | None:
    for candidate in candidates:
        if candidate.is_file():
            actual_sha = _sha256_file(candidate)
            if actual_sha == expected_sha:
                return candidate.resolve(), actual_sha
            mismatches.append({
                "artifact_path": str(candidate),
                "actual_sha256": actual_sha,
            })
    return None


def _blind_artifact_direct_candidates(*, case_root: Path, scorecard_path: Path, rel_path: str) -> list[Path]:
    direct_roots = _unique_paths([
        scorecard_path.parent,
        scorecard_path.parent.parent,
        Path.cwd().resolve(),
        case_root,
        case_root.parent,
        case_root.parent.parent,
    ])
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(resolved)

    for root in direct_roots:
        add(root / rel_path)
    return candidates


def _blind_artifact_workspace_candidates(*, case_root: Path, scorecard_path: Path, rel_path: str) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(resolved)

    for root in _blind_artifact_workspace_search_roots(case_root=case_root, scorecard_path=scorecard_path):
        if not root.exists() or not root.is_dir():
            continue
        for candidate in sorted(root.rglob(rel_path), key=lambda item: item.as_posix()):
            add(candidate)
    return candidates


def _blind_artifact_workspace_search_roots(*, case_root: Path, scorecard_path: Path) -> list[Path]:
    parents = _unique_paths([
        case_root,
        case_root.parent,
        case_root.parent.parent,
        scorecard_path.parent,
        scorecard_path.parent.parent,
    ])
    roots: list[Path] = []
    for parent in parents:
        if parent.name in {"workspace", "workspaces"}:
            roots.append(parent)
        roots.extend([parent / "workspaces", parent / "workspace"])
    return _unique_paths(roots)


def _unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _blind_pack_rubric(guidance_set: dict[str, Any]) -> dict[str, Any]:
    entries = guidance_set.get("entries") if isinstance(guidance_set.get("entries"), list) else []
    rubric_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rubric_entries.append({
            "entry_id": entry.get("entry_id"),
            "guidance_text": entry.get("guidance_text"),
            "expected_manifestation": entry.get("expected_manifestation"),
            "relevance_rule": entry.get("relevance_rule"),
        })
    return {
        "question": (
            "Assess whether each shared guidance entry manifests in the blind audited brief "
            "under the frozen fact layer and auditor gate boundary."
        ),
        "score_vocabulary": {
            "0": "not_observed",
            "1": "weak_or_partial",
            "2": "manifested",
            "3": "overapplied_or_harmful_template",
        },
        "guidance_entries": rubric_entries,
    }


def _scorecard_guidance_entry_ids(scorecard: dict[str, Any]) -> list[str]:
    guidance = scorecard.get("guidance_assessment") if isinstance(scorecard.get("guidance_assessment"), dict) else {}
    entry_ids = guidance.get("guidance_entry_ids") if isinstance(guidance.get("guidance_entry_ids"), list) else []
    return sorted(entry_id for entry_id in entry_ids if isinstance(entry_id, str))


def _scorecard_and_assessment_from_blind_import(
    *,
    blind_pack_path: Path,
    reveal_mapping_path: Path,
    assessment_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pack = _load_json_object(blind_pack_path, label="blind pack")
    reveal = _load_json_object(reveal_mapping_path, label="reveal mapping")
    assessment = _load_json_object(assessment_path, label="assessment")
    _validate_blind_pack_and_reveal(pack=pack, reveal=reveal)
    blind_item_id = assessment.get("blind_item_id")
    if not isinstance(blind_item_id, str) or not BLIND_ITEM_ID_RE.match(blind_item_id):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind assessment must reference a BI-A style blind_item_id.",
            blind_item_id=blind_item_id,
        )
    if "condition" in assessment or "run_id" in assessment:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind assessment files must not include condition or run_id; reveal mapping supplies them after hash verification.",
        )
    pack_item = _blind_item_by_id(pack, blind_item_id)
    reveal_item = _blind_item_by_id(reveal, blind_item_id)
    artifact_sha = reveal_item.get("artifact_sha256")
    if pack_item.get("artifact_sha256") != artifact_sha:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack item hash does not match reveal mapping.",
            blind_item_id=blind_item_id,
        )
    if pack_item.get("scorecard_sha256") != reveal_item.get("scorecard_sha256"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack item scorecard hash does not match reveal mapping.",
            blind_item_id=blind_item_id,
        )
    if pack_item.get("guidance_entry_ids") != reveal_item.get("guidance_entry_ids"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack item guidance entry ids do not match reveal mapping.",
            blind_item_id=blind_item_id,
        )
    if assessment.get("blind_artifact_sha256") != artifact_sha:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind assessment artifact hash does not match reveal mapping.",
            blind_item_id=blind_item_id,
            assessment_sha256=assessment.get("blind_artifact_sha256"),
            expected_sha256=artifact_sha,
        )
    artifact_path = pack_item.get("artifact_path")
    if not isinstance(artifact_path, str) or _unsafe_relative_archive_path(artifact_path):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack item artifact_path must be safe and relative.",
            blind_item_id=blind_item_id,
        )
    artifact_file = (blind_pack_path.parent / artifact_path).resolve()
    try:
        artifact_file.relative_to(blind_pack_path.parent.resolve())
    except ValueError:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack item artifact_path escapes blind pack directory.",
            blind_item_id=blind_item_id,
        )
    if not artifact_file.is_file():
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack artifact is missing.",
            blind_item_id=blind_item_id,
        )
    actual_sha = _sha256_file(artifact_file)
    if actual_sha != artifact_sha:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack artifact hash mismatch.",
            blind_item_id=blind_item_id,
            expected_sha256=artifact_sha,
            actual_sha256=actual_sha,
        )
    scorecard = reveal_item.get("scorecard") if isinstance(reveal_item.get("scorecard"), dict) else {}
    if _sha256_json(scorecard) != reveal_item.get("scorecard_sha256"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "reveal mapping scorecard hash mismatch.",
            blind_item_id=blind_item_id,
        )
    target_artifacts = (
        scorecard.get("target_artifacts")
        if isinstance(scorecard.get("target_artifacts"), dict)
        else {}
    )
    audited = target_artifacts.get("audited_brief") if isinstance(target_artifacts.get("audited_brief"), dict) else {}
    if audited.get("sha256") != artifact_sha:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "reveal mapping scorecard audited brief hash does not match blind artifact hash.",
            blind_item_id=blind_item_id,
            scorecard_audited_brief_sha256=audited.get("sha256"),
            blind_artifact_sha256=artifact_sha,
        )
    if scorecard.get("condition") != reveal_item.get("condition") or scorecard.get("run_id") != reveal_item.get("run_id"):
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "reveal mapping condition/run identity does not match embedded scorecard.",
            blind_item_id=blind_item_id,
            reveal_condition=reveal_item.get("condition"),
            scorecard_condition=scorecard.get("condition"),
            reveal_run_id=reveal_item.get("run_id"),
            scorecard_run_id=scorecard.get("run_id"),
        )
    hydrated_assessment = deepcopy(assessment)
    hydrated_assessment["condition"] = reveal_item.get("condition")
    hydrated_assessment["run_id"] = reveal_item.get("run_id")
    hydrated_assessment["case_id"] = reveal.get("case_id")
    hydrated_assessment.setdefault("experiment_id", EXPERIMENT_080_ID)
    hydrated_assessment["blind_pack"] = {
        "schema_version": pack.get("schema_version"),
        "reveal_mapping_schema_version": reveal.get("schema_version"),
        "blind_item_id": blind_item_id,
        "artifact_sha256": artifact_sha,
        "scorecard_sha256": reveal_item.get("scorecard_sha256"),
        "hash_verified": True,
    }
    return scorecard, hydrated_assessment


def _validate_blind_pack_and_reveal(*, pack: dict[str, Any], reveal: dict[str, Any]) -> None:
    if pack.get("schema_version") != BLIND_PACK_SCHEMA:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            f"blind_pack.schema_version must be {BLIND_PACK_SCHEMA}.",
        )
    if reveal.get("schema_version") != BLIND_REVEAL_MAPPING_SCHEMA:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            f"reveal_mapping.schema_version must be {BLIND_REVEAL_MAPPING_SCHEMA}.",
        )
    for key in ("experiment_id", "case_id", "assessment_target"):
        if pack.get(key) != reveal.get(key):
            _raise_experiment_error(
                "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
                "blind pack and reveal mapping identity mismatch.",
                mismatch=key,
                blind_pack_value=pack.get(key),
                reveal_mapping_value=reveal.get(key),
            )
    if pack.get("condition_blind") is not True or pack.get("hash_bound") is not True:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind pack must be condition_blind and hash_bound.",
        )


def _blind_item_by_id(container: dict[str, Any], blind_item_id: str) -> dict[str, Any]:
    items = container.get("items") if isinstance(container.get("items"), list) else []
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get("blind_item_id") == blind_item_id
    ]
    if len(matches) != 1:
        _raise_experiment_error(
            "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID",
            "blind_item_id must resolve to exactly one item.",
            blind_item_id=blind_item_id,
            match_count=len(matches),
        )
    return matches[0]


def _assessment_guidance_scores_for_scorecard(
    *,
    scorecard_payload: dict[str, Any],
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    guidance_assessment = (
        scorecard_payload.get("guidance_assessment")
        if isinstance(scorecard_payload.get("guidance_assessment"), dict)
        else {}
    )
    required = _required_guidance_entry_ids_for_assessment(guidance_assessment)
    scores = assessment.get("guidance_scores") if isinstance(assessment.get("guidance_scores"), list) else []
    score_ids = {score.get("entry_id") for score in scores if isinstance(score, dict)}
    unknown = sorted(entry_id for entry_id in score_ids if isinstance(entry_id, str) and entry_id not in required)
    missing = sorted(required - {entry_id for entry_id in score_ids if isinstance(entry_id, str)})
    if unknown or missing:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH",
            "assessment.guidance_scores must cover exactly the scorecard guidance entries.",
            missing_entry_ids=missing,
            unknown_entry_ids=unknown,
        )
    return sorted(
        [deepcopy(score) for score in scores if isinstance(score, dict)],
        key=lambda score: str(score.get("entry_id") or ""),
    )


def _required_guidance_entry_ids_for_assessment(guidance_assessment: dict[str, Any]) -> set[str]:
    if guidance_assessment.get("status") != "needs_assessment":
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH",
            "scorecard.guidance_assessment.status must be needs_assessment before assessment import.",
            guidance_assessment_status=guidance_assessment.get("status"),
        )
    entry_ids = guidance_assessment.get("guidance_entry_ids")
    if not isinstance(entry_ids, list) or not entry_ids:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH",
            "scorecard.guidance_assessment.guidance_entry_ids must be a non-empty list before assessment import.",
        )
    required: set[str] = set()
    invalid: list[Any] = []
    duplicates: list[str] = []
    for entry_id in entry_ids:
        if not isinstance(entry_id, str) or not _GUIDANCE_ENTRY_ID_RE.match(entry_id):
            invalid.append(entry_id)
            continue
        if entry_id in required:
            duplicates.append(entry_id)
        required.add(entry_id)
    if invalid or duplicates:
        _raise_experiment_error(
            "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH",
            "scorecard.guidance_assessment.guidance_entry_ids must contain unique AG-0001 style ids.",
            invalid_entry_ids=invalid,
            duplicate_entry_ids=sorted(set(duplicates)),
        )
    return required


def _scorecard_with_imported_assessment(
    *,
    scorecard: dict[str, Any],
    assessment: dict[str, Any],
    guidance_scores: list[dict[str, Any]],
    blind_import_verified: bool,
) -> dict[str, Any]:
    assessed = deepcopy(scorecard)
    assessed["assessment_status"] = "assessed"
    assessed["guidance_scores"] = guidance_scores
    existing_guidance_assessment = (
        assessed.get("guidance_assessment")
        if isinstance(assessed.get("guidance_assessment"), dict)
        else {}
    )
    imported_assessment = {
        "status": "assessed",
        "source": "imported_assessment",
        "assessment_schema_version": ASSESSMENT_SCHEMA,
        "assessed_at": assessment.get("assessed_at"),
        "assessed_by": assessment.get("assessed_by"),
        "assessment_methods": sorted({
            score.get("assessment_method")
            for score in guidance_scores
            if isinstance(score.get("assessment_method"), str)
        }),
        "guidance_entry_ids": existing_guidance_assessment.get("guidance_entry_ids"),
    }
    if "notes" in assessment:
        imported_assessment["assessment_notes_present"] = isinstance(assessment.get("notes"), list)
    if blind_import_verified and isinstance(assessment.get("blind_pack"), dict):
        imported_assessment["blind_pack"] = deepcopy(assessment["blind_pack"])
    if blind_import_verified and isinstance(assessment.get("blind_item_id"), str):
        imported_assessment["blind_item_id"] = assessment["blind_item_id"]
    if blind_import_verified and isinstance(assessment.get("blind_artifact_sha256"), str):
        imported_assessment["blind_artifact_sha256"] = assessment["blind_artifact_sha256"]
    assessed["guidance_assessment"] = imported_assessment
    assessed["assessment_boundary"] = (
        "python_validates_and_merges_imported_assessment_only; "
        "guidance_manifestation_scores_are_external_human_or_llm_assisted_inputs"
    )
    assessed["validity_class"] = _scorecard_validity_class_with_assessment(
        scorecard=assessed,
        guidance_scores=guidance_scores,
    )
    notes = assessed.get("notes") if isinstance(assessed.get("notes"), list) else []
    boundary_note = "Imported assessment was supplied externally; Python did not judge guidance manifestation."
    if boundary_note not in notes:
        notes = [*notes, boundary_note]
    assessed["notes"] = notes
    return assessed


def _scorecard_validity_class_with_assessment(
    *,
    scorecard: dict[str, Any],
    guidance_scores: list[dict[str, Any]],
) -> str:
    control_integrity = scorecard.get("control_integrity") if isinstance(scorecard.get("control_integrity"), dict) else {}
    fact_layer = scorecard.get("frozen_fact_layer") if isinstance(scorecard.get("frozen_fact_layer"), dict) else {}
    reader_clean = scorecard.get("reader_clean") if isinstance(scorecard.get("reader_clean"), dict) else {}
    assessment_target = _assessment_target(scorecard)
    if control_integrity.get("run_integrity_clean") is not True or control_integrity.get("reference_eligible") is False:
        return "invalid_contaminated"
    if fact_layer.get("matches_case") is not True:
        return "invalid_fact_layer_mismatch"
    if any(control_integrity.get(key) is not True for key in _control_keys_for_target(assessment_target)):
        return "invalid_incomplete"
    if not _scorecard_treatment_isolation_status_passed(scorecard, assessment_target=assessment_target):
        return "invalid_incomplete"
    if _reader_clean_required_for_target(assessment_target) and reader_clean.get("pass") is not True:
        return "invalid_incomplete"
    relevant_scores = [score for score in guidance_scores if score.get("relevant") is True]
    if not relevant_scores:
        return "invalid_incomplete"
    if all(score.get("assessment_method") in A_CONTROLLED_ASSESSMENT_METHODS for score in relevant_scores):
        return "A_controlled"
    return "B_integration"


def _scorecard_treatment_isolation_status_passed(
    scorecard: dict[str, Any],
    *,
    assessment_target: str,
) -> bool:
    treatment = scorecard.get("treatment_isolation")
    return isinstance(treatment, dict) and treatment.get("status") == "pass"


def _discover_case_scorecards(
    *,
    case_root: Path,
    output_path: Path | None,
    scorecard_paths: list[str | Path],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    output_resolved = output_path.resolve() if output_path is not None else None
    seen_resolved: set[Path] = set()
    for path in sorted(case_root.rglob("*.json"), key=lambda item: item.relative_to(case_root).as_posix()):
        resolved = path.resolve()
        if output_resolved is not None and resolved == output_resolved:
            continue
        if resolved in seen_resolved:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if "scorecard" in path.name:
                _raise_experiment_error(
                    "E_EXPERIMENT_080_SCORECARD_INVALID",
                    f"scorecard JSON is unreadable: {exc}",
                    path=str(path),
                )
            continue
        if not isinstance(payload, dict):
            if "scorecard" in path.name:
                _raise_experiment_error(
                    "E_EXPERIMENT_080_SCORECARD_INVALID",
                    "scorecard file must contain a JSON object.",
                    path=str(path),
                )
            continue
        schema_version = payload.get("schema_version")
        if schema_version != SCORECARD_SCHEMA:
            if "scorecard" in path.name:
                _raise_experiment_error(
                    "E_EXPERIMENT_080_SCORECARD_INVALID",
                    f"scorecard file schema_version must be {SCORECARD_SCHEMA}.",
                    path=str(path),
                    schema_version=schema_version,
                )
            continue
        diagnostics = validate_scorecard(payload)
        if diagnostics:
            _raise_experiment_error(
                "E_EXPERIMENT_080_SCORECARD_INVALID",
                "scorecard.json failed schema validation.",
                path=str(path),
                errors=[diagnostic.to_dict() for diagnostic in diagnostics],
            )
        records.append({
            "path": path.relative_to(case_root).as_posix(),
            "source_path": str(resolved),
            "scorecard": payload,
        })
        seen_resolved.add(resolved)

    for raw_path in scorecard_paths:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        if output_resolved is not None and path == output_resolved:
            _raise_experiment_error(
                "E_EXPERIMENT_080_SCORECARD_INVALID",
                "summarize --scorecard cannot point to the summary output path.",
                path=str(path),
            )
        if path in seen_resolved:
            continue
        records.append({
            "path": _scorecard_display_path(path=path, case_root=case_root),
            "source_path": str(path),
            "scorecard": _read_scorecard_file(path),
        })
        seen_resolved.add(path)
    _reject_scorecard_display_path_collisions(records)
    return records


def _read_scorecard_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "scorecard file does not exist.",
            path=str(path),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            f"scorecard JSON is unreadable: {exc}",
            path=str(path),
        )
    if not isinstance(payload, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "scorecard file must contain a JSON object.",
            path=str(path),
        )
    if payload.get("schema_version") != SCORECARD_SCHEMA:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            f"scorecard file schema_version must be {SCORECARD_SCHEMA}.",
            path=str(path),
            schema_version=payload.get("schema_version"),
        )
    diagnostics = validate_scorecard(payload)
    if diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_INVALID",
            "scorecard.json failed schema validation.",
            path=str(path),
            errors=[diagnostic.to_dict() for diagnostic in diagnostics],
        )
    return payload


def _scorecard_display_path(*, path: Path, case_root: Path) -> str:
    try:
        return path.relative_to(case_root).as_posix()
    except ValueError:
        pass
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return f"<external-scorecard>/{path.name}"


def _reject_scorecard_display_path_collisions(records: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    collisions: list[dict[str, str]] = []
    for record in records:
        display_path = str(record.get("path") or "")
        source_path = str(record.get("source_path") or "")
        if not display_path:
            continue
        first = seen.get(display_path)
        if first is not None and first != source_path:
            collisions.append({
                "display_path": display_path,
                "first_scorecard": first,
                "second_scorecard": source_path,
            })
            continue
        seen[display_path] = source_path
    if collisions:
        _raise_experiment_error(
            "E_EXPERIMENT_080_SCORECARD_PATH_COLLISION",
            (
                "summarize scorecard display paths are not unique. Move external scorecards "
                "under the case directory, run from a common parent directory, or use distinct filenames."
            ),
            collisions=collisions,
        )


def _build_case_summary(
    *,
    case_manifest: dict[str, Any],
    scorecards: list[dict[str, Any]],
) -> dict[str, Any]:
    case_id = str(case_manifest.get("case_id") or "")
    declared_conditions = [
        condition
        for condition in case_manifest.get("conditions", [])
        if isinstance(condition, str) and condition in ALLOWED_CONDITIONS
    ]
    all_conditions = sorted({
        *declared_conditions,
        *(record["scorecard"].get("condition") for record in scorecards if isinstance(record.get("scorecard"), dict)),
    })
    validity_counts = _empty_validity_counts()
    condition_counts = {
        condition: {
            "total": 0,
            "validity_class_counts": _empty_validity_counts(),
        }
        for condition in all_conditions
    }
    raw_observed_assessments = _empty_manifestation_summary(all_conditions)
    manifestation = _empty_manifestation_summary(all_conditions)
    reader_clean = _empty_rate_summary(all_conditions)
    coverage = _empty_coverage_summary(all_conditions)
    timing = _empty_timing_summary(all_conditions)
    invalid_reasons: dict[str, int] = {}
    assessment_target_counts = {target: 0 for target in sorted(ALLOWED_ASSESSMENT_TARGETS)}
    scorecard_index: list[dict[str, Any]] = []
    formal_included_scorecards: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    hardening_warning_counts: dict[str, int] = {}
    raw_observed_assessments["observations"] = []

    for record in sorted(scorecards, key=lambda item: (
        str(item["scorecard"].get("condition") or ""),
        str(item["scorecard"].get("run_id") or ""),
        item["path"],
    )):
        scorecard = record["scorecard"]
        if scorecard.get("case_id") != case_id:
            _raise_experiment_error(
                "E_EXPERIMENT_080_SCORECARD_CASE_MISMATCH",
                "scorecard.case_id does not match case_manifest.case_id.",
                path=record["path"],
                scorecard_case_id=scorecard.get("case_id"),
                case_manifest_case_id=case_id,
            )
        condition = str(scorecard.get("condition") or "")
        if condition not in condition_counts:
            condition_counts[condition] = {
                "total": 0,
                "validity_class_counts": _empty_validity_counts(),
            }
            manifestation["by_condition"][condition] = _empty_manifestation_counts()
            raw_observed_assessments["by_condition"][condition] = _empty_manifestation_counts()
            reader_clean["by_condition"][condition] = _empty_rate_counts()
            coverage["by_condition"][condition] = _empty_coverage_counts()
            timing["by_condition"][condition] = _empty_timing_counts()
        validity = str(scorecard.get("validity_class") or "invalid_incomplete")
        if validity not in validity_counts:
            validity = "invalid_incomplete"
        assessment_target = _assessment_target(scorecard)
        assessment_target_counts[assessment_target] = assessment_target_counts.get(assessment_target, 0) + 1
        validity_counts[validity] += 1
        condition_counts[condition]["total"] += 1
        condition_counts[condition]["validity_class_counts"][validity] += 1
        _accumulate_manifestation(raw_observed_assessments, condition=condition, scorecard=scorecard)
        raw_observed_assessments["observations"].extend(
            _scorecard_raw_assessment_observations(
                scorecard,
                path=record["path"],
                condition=condition,
                assessment_target=assessment_target,
                validity_class=validity,
            )
        )
        formal_exclusion_reasons = _scorecard_formal_metric_exclusion_reasons(scorecard)
        formal_interpretable = not formal_exclusion_reasons
        if formal_interpretable:
            _accumulate_manifestation(manifestation, condition=condition, scorecard=scorecard)
            _accumulate_reader_clean(reader_clean, condition=condition, scorecard=scorecard)
            _accumulate_coverage(coverage, condition=condition, scorecard=scorecard)
            _accumulate_timing(timing, condition=condition, scorecard=scorecard)
            formal_included_scorecards.append({
                "path": record["path"],
                "condition": condition,
                "run_id": scorecard.get("run_id"),
                "assessment_target": assessment_target,
                "validity_class": validity,
            })
        else:
            for reason in formal_exclusion_reasons:
                hardening_warning_counts[reason] = hardening_warning_counts.get(reason, 0) + 1
            exclusions.append({
                "path": record["path"],
                "condition": condition,
                "run_id": scorecard.get("run_id"),
                "assessment_target": assessment_target,
                "validity_class": validity,
                "reasons": formal_exclusion_reasons,
            })
        for reason in _scorecard_invalid_reasons(scorecard):
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
        scorecard_index.append({
            "path": record["path"],
            "condition": condition,
            "run_id": scorecard.get("run_id"),
            "assessment_target": assessment_target,
            "validity_class": validity,
            "assessment_status": scorecard.get("assessment_status", ""),
            "blind_assessment_verified": _scorecard_blind_assessment_verified(scorecard),
            "formal_interpretable": formal_interpretable,
        })

    _finalize_rate_summary(reader_clean)
    _finalize_coverage_summary(coverage)
    _finalize_timing_summary(timing)
    formal_denominator = len(formal_included_scorecards)
    hardening_warnings = [
        {"warning": warning, "count": count}
        for warning, count in sorted(hardening_warning_counts.items())
    ]
    if formal_denominator < SUMMARY_LOW_N_DENOMINATOR_THRESHOLD:
        hardening_warnings.append({
            "warning": "low_formal_interpretable_n",
            "count": formal_denominator,
            "threshold": SUMMARY_LOW_N_DENOMINATOR_THRESHOLD,
        })
    formal_a_grade_count = sum(
        1
        for item in formal_included_scorecards
        if item.get("validity_class") == "A_controlled"
    )
    return {
        "schema_version": CASE_SUMMARY_SCHEMA,
        "experiment_id": EXPERIMENT_080_ID,
        "case_id": case_id,
        "summary_boundary": (
            "deterministic_scorecard_aggregation_only; "
            "raw_observations_are_not_formal_proof; "
            "formal_metrics_require_blind_hash_bound_assessment; "
            "invalid_runs_excluded_from_interpretable_metrics; "
            "no_python_or_llm_quality_judgment"
        ),
        "scorecard_count": len(scorecards),
        "run_counts": {
            "total": len(scorecards),
            "validity_class_counts": validity_counts,
            "a_grade_count": formal_a_grade_count,
            "interpretable_run_denominator": formal_denominator,
            "invalid_excluded_count": sum(
                validity_counts[key]
                for key in ("invalid_contaminated", "invalid_incomplete", "invalid_fact_layer_mismatch")
            ),
        },
        "assessment_target_counts": assessment_target_counts,
        "condition_counts": condition_counts,
        "raw_observed_assessments": raw_observed_assessments,
        "valid_interpretable_metrics": {
            "denominator": formal_denominator,
            "a_grade_count": formal_a_grade_count,
            "included_scorecards": formal_included_scorecards,
            "low_n_threshold": SUMMARY_LOW_N_DENOMINATOR_THRESHOLD,
            "low_n": formal_denominator < SUMMARY_LOW_N_DENOMINATOR_THRESHOLD,
            "manifestation": manifestation,
            "reader_clean": reader_clean,
            "coverage_delta": coverage,
            "timing": timing,
        },
        "exclusions": exclusions,
        "hardening_warnings": hardening_warnings,
        "manifestation": manifestation,
        "reader_clean": reader_clean,
        "coverage_delta": coverage,
        "timing": timing,
        "invalid_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(invalid_reasons.items())
        ],
        "scorecards": scorecard_index,
    }


def _empty_validity_counts() -> dict[str, int]:
    return {validity: 0 for validity in sorted(ALLOWED_VALIDITY_CLASSES)}


def _empty_manifestation_counts() -> dict[str, int]:
    return {
        "guidance_score_count": 0,
        "relevant_guidance_score_count": 0,
        "score_2_manifested_count": 0,
        "score_3_overapplication_count": 0,
        "overapplication_count": 0,
    }


def _empty_manifestation_summary(conditions: list[str]) -> dict[str, Any]:
    summary = _empty_manifestation_counts()
    summary["by_condition"] = {condition: _empty_manifestation_counts() for condition in conditions}
    return summary


def _empty_rate_counts() -> dict[str, Any]:
    return {"pass_count": 0, "total_evaluable": 0, "pass_rate": None}


def _empty_rate_summary(conditions: list[str]) -> dict[str, Any]:
    summary = _empty_rate_counts()
    summary["by_condition"] = {condition: _empty_rate_counts() for condition in conditions}
    return summary


def _empty_coverage_counts() -> dict[str, Any]:
    return {
        "numeric_count": 0,
        "numeric_sum": 0.0,
        "numeric_min": None,
        "numeric_max": None,
        "numeric_average": None,
        "not_computed_count": 0,
        "status_counts": {},
    }


def _empty_coverage_summary(conditions: list[str]) -> dict[str, Any]:
    summary = _empty_coverage_counts()
    summary["by_condition"] = {condition: _empty_coverage_counts() for condition in conditions}
    return summary


def _empty_timing_counts() -> dict[str, Any]:
    return {
        "status_counts": {},
        "available_count": 0,
        "downstream_only_count": 0,
        "incomplete_count": 0,
        "unknown_count": 0,
        "contaminated_count": 0,
        "completed_stage_count": {"count": 0, "min": None, "max": None, "average": None},
    }


def _empty_timing_summary(conditions: list[str]) -> dict[str, Any]:
    summary = _empty_timing_counts()
    summary["by_condition"] = {condition: _empty_timing_counts() for condition in conditions}
    return summary


def _accumulate_manifestation(summary: dict[str, Any], *, condition: str, scorecard: dict[str, Any]) -> None:
    condition_summary = summary["by_condition"][condition]
    scores = scorecard.get("guidance_scores") if isinstance(scorecard.get("guidance_scores"), list) else []
    for score in scores:
        if not isinstance(score, dict):
            continue
        for target in (summary, condition_summary):
            target["guidance_score_count"] += 1
            if score.get("relevant") is True:
                target["relevant_guidance_score_count"] += 1
            if score.get("manifestation_score") == 2:
                target["score_2_manifested_count"] += 1
            if score.get("manifestation_score") == 3:
                target["score_3_overapplication_count"] += 1
            if score.get("overapplication") is True:
                target["overapplication_count"] += 1


def _accumulate_reader_clean(summary: dict[str, Any], *, condition: str, scorecard: dict[str, Any]) -> None:
    reader_clean = scorecard.get("reader_clean") if isinstance(scorecard.get("reader_clean"), dict) else {}
    if not isinstance(reader_clean.get("pass"), bool):
        return
    for target in (summary, summary["by_condition"][condition]):
        target["total_evaluable"] += 1
        if reader_clean.get("pass") is True:
            target["pass_count"] += 1


def _accumulate_coverage(summary: dict[str, Any], *, condition: str, scorecard: dict[str, Any]) -> None:
    coverage = scorecard.get("coverage_delta") if isinstance(scorecard.get("coverage_delta"), dict) else {}
    status = str(coverage.get("status") or "unknown")
    value = coverage.get("delta")
    for target in (summary, summary["by_condition"][condition]):
        status_counts = target["status_counts"]
        status_counts[status] = status_counts.get(status, 0) + 1
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            _accumulate_numeric(target, float(value))
        else:
            target["not_computed_count"] += 1


def _accumulate_timing(summary: dict[str, Any], *, condition: str, scorecard: dict[str, Any]) -> None:
    timing = scorecard.get("timing_summary") if isinstance(scorecard.get("timing_summary"), dict) else {}
    status = str(timing.get("status") or "unknown")
    completed_stage_count = timing.get("completed_stage_count")
    for target in (summary, summary["by_condition"][condition]):
        status_counts = target["status_counts"]
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "available":
            target["available_count"] += 1
        elif status == "downstream_only":
            target["available_count"] += 1
            target["downstream_only_count"] += 1
        elif status == "incomplete":
            target["incomplete_count"] += 1
        elif status == "contaminated":
            target["contaminated_count"] += 1
        else:
            target["unknown_count"] += 1
        if isinstance(completed_stage_count, int):
            _accumulate_numeric(target["completed_stage_count"], float(completed_stage_count))


def _accumulate_numeric(target: dict[str, Any], value: float) -> None:
    target["numeric_count"] = target.get("numeric_count", 0) + 1
    target["numeric_sum"] = round(float(target.get("numeric_sum", 0.0)) + value, 6)
    target["numeric_min"] = value if target.get("numeric_min") is None else min(float(target["numeric_min"]), value)
    target["numeric_max"] = value if target.get("numeric_max") is None else max(float(target["numeric_max"]), value)


def _finalize_rate_summary(summary: dict[str, Any]) -> None:
    _finalize_rate_counts(summary)
    for condition_summary in summary["by_condition"].values():
        _finalize_rate_counts(condition_summary)


def _finalize_rate_counts(counts: dict[str, Any]) -> None:
    total = counts.get("total_evaluable", 0)
    counts["pass_rate"] = round(counts["pass_count"] / total, 6) if total else None


def _finalize_coverage_summary(summary: dict[str, Any]) -> None:
    _finalize_coverage_counts(summary)
    for condition_summary in summary["by_condition"].values():
        _finalize_coverage_counts(condition_summary)


def _finalize_coverage_counts(counts: dict[str, Any]) -> None:
    if counts["numeric_count"]:
        counts["numeric_average"] = round(counts["numeric_sum"] / counts["numeric_count"], 6)
    counts["numeric_sum"] = round(counts["numeric_sum"], 6)


def _finalize_timing_summary(summary: dict[str, Any]) -> None:
    _finalize_timing_counts(summary)
    for condition_summary in summary["by_condition"].values():
        _finalize_timing_counts(condition_summary)


def _finalize_timing_counts(counts: dict[str, Any]) -> None:
    completed = counts["completed_stage_count"]
    if completed.get("numeric_count", 0):
        completed["average"] = round(completed["numeric_sum"] / completed["numeric_count"], 6)
        completed["min"] = completed.pop("numeric_min")
        completed["max"] = completed.pop("numeric_max")
        completed.pop("numeric_sum", None)
        completed["count"] = completed.pop("numeric_count")
        return
    completed.setdefault("count", 0)
    completed.setdefault("min", None)
    completed.setdefault("max", None)
    completed.setdefault("average", None)
    completed.pop("numeric_sum", None)
    completed.pop("numeric_min", None)
    completed.pop("numeric_max", None)
    completed.pop("numeric_count", None)


def _scorecard_formal_metric_exclusion_reasons(scorecard: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    validity = str(scorecard.get("validity_class") or "invalid_incomplete")
    assessment_target = _assessment_target(scorecard)
    control = scorecard.get("control_integrity") if isinstance(scorecard.get("control_integrity"), dict) else {}
    fact_layer = scorecard.get("frozen_fact_layer") if isinstance(scorecard.get("frozen_fact_layer"), dict) else {}
    reader_clean = scorecard.get("reader_clean") if isinstance(scorecard.get("reader_clean"), dict) else {}
    if validity not in INTERPRETABLE_SCORECARD_VALIDITY_CLASSES:
        reasons.append(f"validity_class.{validity}_not_interpretable")
    if scorecard.get("assessment_status") != "assessed":
        reasons.append("assessment_status_not_assessed")
    for key in _control_keys_for_target(assessment_target):
        if control.get(key) is not True:
            reasons.append(f"control_integrity.{key}_not_true")
    if fact_layer.get("matches_case") is not True:
        reasons.append("frozen_fact_layer_mismatch")
    if _reader_clean_required_for_target(assessment_target) and reader_clean.get("pass") is not True:
        reasons.append("reader_clean_not_pass")
    if not _scorecard_treatment_isolation_status_passed(scorecard, assessment_target=assessment_target):
        reasons.append("treatment_isolation_not_passed")
    if not _scorecard_audit_binding_summary_passed(scorecard, assessment_target=assessment_target):
        reasons.append("audit_binding_not_valid")
    if not _scorecard_blind_assessment_verified(scorecard):
        reasons.append("blind_assessment_not_hash_verified")
    scores = scorecard.get("guidance_scores") if isinstance(scorecard.get("guidance_scores"), list) else []
    if not any(isinstance(score, dict) and score.get("relevant") is True for score in scores):
        reasons.append("no_relevant_guidance_scores")
    return sorted(dict.fromkeys(reasons))


def _scorecard_raw_assessment_observations(
    scorecard: dict[str, Any],
    *,
    path: str,
    condition: str,
    assessment_target: str,
    validity_class: str,
) -> list[dict[str, Any]]:
    scores = scorecard.get("guidance_scores") if isinstance(scorecard.get("guidance_scores"), list) else []
    observations: list[dict[str, Any]] = []
    for score in scores:
        if not isinstance(score, dict):
            continue
        observations.append({
            "path": path,
            "condition": condition,
            "run_id": scorecard.get("run_id"),
            "assessment_target": assessment_target,
            "validity_class": validity_class,
            "assessment_status": scorecard.get("assessment_status", ""),
            "entry_id": score.get("entry_id"),
            "relevant": score.get("relevant"),
            "manifestation_score": score.get("manifestation_score"),
            "overapplication": score.get("overapplication"),
            "assessment_method": score.get("assessment_method"),
            "blind_assessment_verified": _scorecard_blind_assessment_verified(scorecard),
        })
    return observations


def _scorecard_audit_binding_summary_passed(scorecard: dict[str, Any], *, assessment_target: str) -> bool:
    if assessment_target != "auditable_brief":
        return True
    control = scorecard.get("control_integrity") if isinstance(scorecard.get("control_integrity"), dict) else {}
    binding = scorecard.get("audit_binding") if isinstance(scorecard.get("audit_binding"), dict) else {}
    return control.get("audit_binding_valid") is True and binding.get("status") == "valid"


def _scorecard_blind_assessment_verified(scorecard: dict[str, Any]) -> bool:
    guidance_assessment = (
        scorecard.get("guidance_assessment")
        if isinstance(scorecard.get("guidance_assessment"), dict)
        else {}
    )
    blind_pack = (
        guidance_assessment.get("blind_pack")
        if isinstance(guidance_assessment.get("blind_pack"), dict)
        else {}
    )
    blind_item_id = guidance_assessment.get("blind_item_id")
    blind_artifact_sha = guidance_assessment.get("blind_artifact_sha256")
    pack_blind_item_id = blind_pack.get("blind_item_id")
    pack_artifact_sha = blind_pack.get("artifact_sha256")
    pack_scorecard_sha = blind_pack.get("scorecard_sha256")
    return (
        guidance_assessment.get("source") == "imported_assessment"
        and blind_pack.get("schema_version") == BLIND_PACK_SCHEMA
        and blind_pack.get("hash_verified") is True
        and isinstance(blind_item_id, str)
        and BLIND_ITEM_ID_RE.match(blind_item_id) is not None
        and pack_blind_item_id == blind_item_id
        and isinstance(blind_artifact_sha, str)
        and _SHA256_RE.match(blind_artifact_sha) is not None
        and pack_artifact_sha == blind_artifact_sha
        and isinstance(pack_scorecard_sha, str)
        and _SHA256_RE.match(pack_scorecard_sha) is not None
    )


def _scorecard_invalid_reasons(scorecard: dict[str, Any]) -> list[str]:
    validity = scorecard.get("validity_class")
    if validity == "invalid_contaminated":
        return ["run_integrity_contaminated_or_non_reference"]
    if validity == "invalid_fact_layer_mismatch":
        return ["frozen_fact_layer_mismatch"]
    if validity != "invalid_incomplete":
        return []
    reasons: list[str] = []
    control = scorecard.get("control_integrity") if isinstance(scorecard.get("control_integrity"), dict) else {}
    assessment_target = _assessment_target(scorecard)
    for key in _control_keys_for_target(assessment_target):
        if control.get(key) is not True:
            reasons.append(f"control_integrity.{key}_not_true")
    reader_clean = scorecard.get("reader_clean") if isinstance(scorecard.get("reader_clean"), dict) else {}
    if _reader_clean_required_for_target(assessment_target) and reader_clean.get("pass") is not True:
        reasons.append("reader_clean_not_pass")
    if scorecard.get("assessment_status") == "needs_assessment":
        reasons.append("assessment_needed")
    return reasons or ["incomplete_control_or_assessment"]


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


def _auditable_scorecard_target_projection(run_record: dict[str, Any]) -> dict[str, bool]:
    target_artifacts = (
        run_record.get("target_artifacts")
        if isinstance(run_record.get("target_artifacts"), dict)
        else {}
    )
    audited = target_artifacts.get("audited_brief") if isinstance(target_artifacts.get("audited_brief"), dict) else {}
    audit = target_artifacts.get("audit_report") if isinstance(target_artifacts.get("audit_report"), dict) else {}
    gate = (
        target_artifacts.get("auditor_quality_gate_report")
        if isinstance(target_artifacts.get("auditor_quality_gate_report"), dict)
        else {}
    )
    audit_binding = run_record.get("audit_binding") if isinstance(run_record.get("audit_binding"), dict) else {}
    return {
        "artifact_registry_valid": all(
            isinstance(target_artifacts.get(artifact_id), dict)
            and target_artifacts[artifact_id].get("frozen_valid") is True
            for artifact_id in AUDITABLE_TARGET_ARTIFACTS
        ),
        "audit_binding_valid": audit_binding.get("status") == "valid",
        "audited_brief_frozen_valid": audited.get("frozen_valid") is True,
        "audit_report_frozen_valid": audit.get("frozen_valid") is True,
        "auditor_gate_report_valid": gate.get("frozen_valid") is True,
        "auditor_gates_no_blocking": gate.get("no_blocking") is True,
    }


def _auditable_scorecard_gate_status(run_record: dict[str, Any]) -> dict[str, Any]:
    target_artifacts = (
        run_record.get("target_artifacts")
        if isinstance(run_record.get("target_artifacts"), dict)
        else {}
    )
    gate = (
        target_artifacts.get("auditor_quality_gate_report")
        if isinstance(target_artifacts.get("auditor_quality_gate_report"), dict)
        else {}
    )
    auditor_status = gate.get("report_status") if isinstance(gate.get("report_status"), str) else "missing"
    return {
        "passed": auditor_status == "pass" and gate.get("no_blocking") is True,
        "auditor_status": auditor_status,
        "finalize_status": "not_required_for_target",
        "source": "run_record.target_artifacts",
    }


def _auditable_scorecard_auditor_complete(run_record: dict[str, Any]) -> bool:
    workflow = run_record.get("target_workflow") if isinstance(run_record.get("target_workflow"), dict) else {}
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    return statuses.get("auditor") == "complete"


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
    raw_status = str(timing.get("status") or "unknown")
    status = raw_status
    total_elapsed = timing.get("total_elapsed_seconds")
    timing_comparability = timing.get("timing_comparability")
    if (
        raw_status == "incomplete"
        and timing_comparability == "downstream_only"
        and isinstance(total_elapsed, (int, float))
        and not isinstance(total_elapsed, bool)
        and _timing_gaps_are_imported_upstream_only(timing)
    ):
        status = "downstream_only"
    summary = {
        "schema_version": str(timing.get("schema_version") or ""),
        "status": status,
        "raw_status": raw_status,
        "run_recipe": timing.get("run_recipe", ""),
        "timing_comparability": timing_comparability or "",
        "source": "run_record.timing",
    }
    if isinstance(total_elapsed, (int, float)) and not isinstance(total_elapsed, bool):
        summary["total_elapsed_seconds"] = float(total_elapsed)
    if isinstance(stages, list):
        summary["stage_count"] = len(stages)
        summary["completed_stage_count"] = sum(
            1
            for stage in stages
            if isinstance(stage, dict) and stage.get("status") in {"complete", "satisfied_by_topology"}
        )
    return summary


def _timing_gaps_are_imported_upstream_only(timing: dict[str, Any]) -> bool:
    stages = timing.get("stages")
    if not isinstance(stages, list):
        return False
    gap_stage_ids: list[str] = []
    stage_statuses: dict[str, Any] = {}
    for stage in stages:
        if not isinstance(stage, dict):
            return False
        stage_id = stage.get("stage_id")
        if not isinstance(stage_id, str) or not stage_id:
            return False
        status = stage.get("status")
        stage_statuses[stage_id] = status
        if status not in {"incomplete", "unknown"}:
            continue
        if stage_id not in FAST_RERUN_IMPORTED_UPSTREAM_STAGE_IDS:
            return False
        gap_stage_ids.append(stage_id)
    for stage_id in FAST_RERUN_DOWNSTREAM_TIMED_STAGE_IDS:
        if stage_statuses.get(stage_id) in {None, "incomplete", "unknown"}:
            return False
    finalize = timing.get("finalize")
    if not isinstance(finalize, dict) or finalize.get("status") != "complete":
        return False
    return bool(gap_stage_ids)


def _run_record_timing(timing: dict[str, Any], *, runtime_manifest: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(timing)
    recipe = runtime_manifest.get("recipe")
    if isinstance(recipe, str) and recipe:
        enriched.setdefault("run_recipe", recipe)
    fact_import = runtime_manifest.get("fact_layer_import")
    if isinstance(fact_import, dict):
        timing_comparability = fact_import.get("timing_comparability")
        if isinstance(timing_comparability, str) and timing_comparability:
            enriched.setdefault("timing_comparability", timing_comparability)
    return enriched


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


def _validate_auditable_workflow_ready(workflow_state: dict[str, Any]) -> None:
    active_repair = workflow_state.get("active_repair")
    if isinstance(active_repair, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ACTIVE_REPAIR_OPEN",
            "Auditable-brief registration requires no active owner-stage repair.",
            active_repair=active_repair,
        )
    current_stage = workflow_state.get("current_stage")
    if current_stage not in {"finalize", None}:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_NOT_AUDITABLE_READY",
            "Auditable-brief registration requires workflow_state.current_stage to be finalize or null.",
            current_stage=current_stage,
        )
    statuses = workflow_state.get("stage_statuses")
    if not isinstance(statuses, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_NOT_AUDITABLE_READY",
            "Auditable-brief registration requires workflow_state.stage_statuses.",
        )
    missing_or_incomplete: dict[str, Any] = {}
    for stage_id in ("analyst", "editor", "auditor"):
        status = statuses.get(stage_id) if isinstance(statuses.get(stage_id), dict) else {}
        if status.get("status") != "complete":
            missing_or_incomplete[stage_id] = status.get("status")
    if missing_or_incomplete:
        _raise_experiment_error(
            "E_EXPERIMENT_080_RUN_NOT_AUDITABLE_READY",
            "Auditable-brief registration requires analyst, editor, and auditor stages to be complete.",
            stage_statuses=missing_or_incomplete,
        )


def _auditable_target_workflow(workflow_state: dict[str, Any]) -> dict[str, Any]:
    statuses = workflow_state.get("stage_statuses") if isinstance(workflow_state.get("stage_statuses"), dict) else {}
    return {
        "current_stage": workflow_state.get("current_stage"),
        "required_complete_stage_ids": ["analyst", "editor", "auditor"],
        "stage_statuses": {
            stage_id: (
                statuses.get(stage_id, {}).get("status")
                if isinstance(statuses.get(stage_id), dict)
                else None
            )
            for stage_id in ("analyst", "editor", "auditor", "finalize")
        },
    }


def _auditable_audit_binding_projection(
    *,
    workspace: Path,
    workflow_state: dict[str, Any],
    target_artifacts: dict[str, Any],
) -> dict[str, Any]:
    statuses = workflow_state.get("stage_statuses") if isinstance(workflow_state.get("stage_statuses"), dict) else {}
    auditor_status = statuses.get("auditor") if isinstance(statuses.get("auditor"), dict) else {}
    metadata = auditor_status.get("metadata") if isinstance(auditor_status.get("metadata"), dict) else {}
    binding = metadata.get("audit_binding") if isinstance(metadata.get("audit_binding"), dict) else None
    if binding is None:
        _raise_experiment_error(
            "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
            "auditable_brief registration requires Python-owned auditor audit_binding metadata.",
        )
    diagnostics: list[Experiment080Diagnostic] = []
    _validate_auditable_audit_binding_schema(binding, diagnostics, path="workflow_state.stage_statuses.auditor.metadata.audit_binding")
    if diagnostics:
        _raise_experiment_error(
            "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
            "Auditor audit_binding metadata failed schema validation.",
            errors=[diagnostic.to_dict() for diagnostic in diagnostics],
        )

    registry = _load_json_object(
        workspace / "output" / "intermediate" / "artifact_registry.json",
        label="artifact_registry",
    )
    artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), dict) else {}
    expected = {
        "claim_ledger_sha256": _registry_artifact_sha(artifacts, "claim_ledger"),
        "audited_brief_sha256": _target_artifact_sha(target_artifacts, "audited_brief"),
        "audit_report_sha256": _target_artifact_sha(target_artifacts, "audit_report"),
    }
    mismatches: dict[str, dict[str, Any]] = {}
    for field, expected_sha in expected.items():
        actual = binding.get(field)
        if actual != expected_sha:
            mismatches[field] = {"expected": expected_sha, "actual": actual}
    event_records = _read_event_log_for_experiment(workspace / "output" / "intermediate" / "event_log.jsonl")
    run_id = _auditable_binding_run_id(workflow_state)
    expected_repair_ids = _auditable_brief_repair_transaction_ids(
        event_records,
        run_id=run_id,
    )
    actual_repair_ids = [
        str(item)
        for item in binding.get("relevant_repair_transaction_ids", [])
        if isinstance(item, str) and item.strip()
    ]
    if actual_repair_ids != expected_repair_ids:
        mismatches["relevant_repair_transaction_ids"] = {
            "expected": expected_repair_ids,
            "actual": actual_repair_ids,
        }
    auditor_event = _auditable_auditor_completion_event(
        event_records,
        run_id=run_id,
        transaction_id=str(binding["auditor_stage_transaction_id"]),
    )
    binding_stage_event = (
        binding.get("stage_completion_event")
        if isinstance(binding.get("stage_completion_event"), dict)
        else {}
    )
    binding_stage_event_tx = binding_stage_event.get("transaction_id")
    if (
        binding_stage_event_tx is not None
        and binding_stage_event_tx != binding["auditor_stage_transaction_id"]
    ):
        mismatches["stage_completion_event.transaction_id"] = {
            "expected": binding["auditor_stage_transaction_id"],
            "actual": binding_stage_event_tx,
        }
    if auditor_event is None:
        mismatches["auditor_stage_transaction_id"] = {
            "expected": "current-run auditor decision_recorded event",
            "actual": binding["auditor_stage_transaction_id"],
        }
    if mismatches:
        _raise_experiment_error(
            "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
            "Auditor audit_binding does not match current control-plane hashes or repair history.",
            mismatches=mismatches,
        )
    return {
        "schema_version": AUDIT_BINDING_SCHEMA,
        "status": "valid",
        "source": "workflow_state.stage_statuses.auditor.metadata.audit_binding",
        "claim_ledger_sha256": binding["claim_ledger_sha256"],
        "audited_brief_sha256": binding["audited_brief_sha256"],
        "audit_report_sha256": binding["audit_report_sha256"],
        "relevant_repair_transaction_ids": actual_repair_ids,
        "auditor_stage_transaction_id": binding["auditor_stage_transaction_id"],
        "stage_completion_event": auditor_event or {},
    }


def _registry_artifact_sha(artifacts: dict[str, Any], artifact_id: str) -> str:
    record = artifacts.get(artifact_id) if isinstance(artifacts.get(artifact_id), dict) else {}
    sha = record.get("sha256")
    if isinstance(sha, str) and _SHA256_RE.match(sha):
        return sha
    _raise_experiment_error(
        "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
        "Auditor audit_binding requires a valid frozen artifact sha256.",
        artifact_id=artifact_id,
    )


def _target_artifact_sha(target_artifacts: dict[str, Any], artifact_id: str) -> str:
    record = target_artifacts.get(artifact_id) if isinstance(target_artifacts.get(artifact_id), dict) else {}
    sha = record.get("sha256")
    if isinstance(sha, str) and _SHA256_RE.match(sha):
        return sha
    _raise_experiment_error(
        "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
        "Auditor audit_binding requires a valid target artifact sha256.",
        artifact_id=artifact_id,
    )


def _auditable_binding_run_id(workflow_state: dict[str, Any]) -> str:
    run_id = workflow_state.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    _raise_experiment_error(
        "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
        "Auditor audit_binding verification requires workflow_state.run_id.",
    )


def _auditable_brief_repair_transaction_ids(
    records: list[dict[str, Any]],
    *,
    run_id: str,
) -> list[str]:
    ids: list[str] = []
    artifact_path = "output/intermediate/audited_brief.md"
    for event in records:
        if event.get("run_id") != run_id:
            continue
        if event.get("event_type") != "repair_completed":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        allowed = [str(item) for item in metadata.get("allowed_artifacts") or []]
        if not any(_artifact_path_matches(pattern, artifact_path) for pattern in allowed):
            continue
        transaction_id = metadata.get("transaction_id") or metadata.get("repair_transaction_id")
        if isinstance(transaction_id, str) and transaction_id and transaction_id not in ids:
            ids.append(transaction_id)
    return ids


def _auditable_auditor_completion_event(
    records: list[dict[str, Any]],
    *,
    run_id: str,
    transaction_id: str,
) -> dict[str, Any] | None:
    for event in records:
        if event.get("run_id") != run_id:
            continue
        if event.get("event_type") != "decision_recorded":
            continue
        if event.get("stage_id") != "auditor" or event.get("decision") != "continue":
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if metadata.get("transaction_id") != transaction_id:
            continue
        return {
            "event_type": "decision_recorded",
            "event_id": event.get("event_id"),
            "created_at": event.get("created_at"),
            "stage_id": "auditor",
            "decision": "continue",
            "transaction_id": transaction_id,
        }
    return None


def _artifact_path_matches(pattern: str, path: str) -> bool:
    return bool(pattern.strip() and (path == pattern.strip() or fnmatch.fnmatch(path, pattern.strip())))


def _read_event_log_for_experiment(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        _raise_experiment_error(
            "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
            "Auditor audit_binding verification requires event_log.jsonl.",
            path=str(path),
        )
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            _raise_experiment_error(
                "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
                "event_log.jsonl contains invalid JSON.",
                path=str(path),
                line=lineno,
                error=str(exc),
            )
        if not isinstance(payload, dict):
            _raise_experiment_error(
                "E_EXPERIMENT_080_AUDIT_BINDING_INVALID",
                "event_log.jsonl records must be objects.",
                path=str(path),
                line=lineno,
            )
        records.append(payload)
    return records


def _auditable_imported_fact_layer_comparison(
    *,
    workspace: Path,
    case_root: Path,
    frozen_fact_layer: dict[str, Any],
    runtime_manifest: dict[str, Any],
) -> dict[str, Any]:
    import_record = runtime_manifest.get("fact_layer_import")
    if not isinstance(import_record, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
            "auditable_brief registration requires runtime_manifest.fact_layer_import.",
        )
    if import_record.get("schema_version") != "mabw.fact_layer_import.v1":
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
            "runtime_manifest.fact_layer_import has an unsupported schema.",
            schema_version=import_record.get("schema_version"),
        )
    _validate_fact_layer_import_files(workspace=workspace, import_record=import_record)
    archive_manifest_path = _resolve_case_source_archive_manifest(
        case_root=case_root,
        frozen_fact_layer=frozen_fact_layer,
    )
    expected_logical_manifest = _case_source_archive_logical_path(archive_manifest_path=archive_manifest_path)
    source_archive_manifest = import_record.get("source_archive_manifest")
    if source_archive_manifest != expected_logical_manifest:
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
            "runtime_manifest.fact_layer_import.source_archive_manifest does not match the case frozen fact layer.",
            expected_source_archive_manifest=expected_logical_manifest,
            actual_source_archive_manifest=source_archive_manifest,
        )
    archive_sha = _sha256_file(archive_manifest_path)
    if import_record.get("source_archive_manifest_sha256") != archive_sha:
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
            "runtime_manifest.fact_layer_import.source_archive_manifest_sha256 does not match the case source archive.",
            expected_sha256=archive_sha,
            actual_sha256=import_record.get("source_archive_manifest_sha256"),
        )
    archive_manifest = _load_json_object(archive_manifest_path, label="source_archive_manifest")
    archive_fact_layer = archive_manifest.get("fact_layer")
    if not isinstance(archive_fact_layer, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_INVALID",
            "source archive manifest.fact_layer must be an object for auditable_brief registration.",
        )
    if archive_fact_layer.get("status") != "complete":
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_INCOMPLETE",
            "source archive fact_layer.status must be complete for auditable_brief registration.",
            status=archive_fact_layer.get("status"),
        )
    expected_fact_layer_sha = _sha256_json(archive_fact_layer)
    if import_record.get("fact_layer_sha256") != expected_fact_layer_sha:
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
            "runtime_manifest.fact_layer_import.fact_layer_sha256 does not match the case source archive fact layer.",
            expected_sha256=expected_fact_layer_sha,
            actual_sha256=import_record.get("fact_layer_sha256"),
        )
    comparison = _compare_case_fact_layer_to_archive(
        frozen_fact_layer=frozen_fact_layer,
        archive_fact_layer=archive_fact_layer,
        archive_root=archive_manifest_path.parent,
    )
    comparison["source"] = "runtime_manifest.fact_layer_import"
    comparison["source_archive_manifest"] = source_archive_manifest
    comparison["source_archive_manifest_sha256"] = import_record.get("source_archive_manifest_sha256")
    comparison["fact_layer_sha256"] = import_record.get("fact_layer_sha256")
    return comparison


def _validate_fact_layer_import_files(*, workspace: Path, import_record: dict[str, Any]) -> None:
    files = import_record.get("imported_files")
    if not isinstance(files, list) or not files:
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
            "runtime_manifest.fact_layer_import.imported_files must be a non-empty list.",
        )
    expected_count = import_record.get("imported_file_count")
    if expected_count != len(files):
        _raise_experiment_error(
            "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
            "runtime_manifest.fact_layer_import.imported_file_count does not match imported_files.",
            imported_file_count=expected_count,
            actual_count=len(files),
        )
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "runtime_manifest.fact_layer_import.imported_files entries must be objects.",
                index=index,
            )
        workspace_path = item.get("workspace_path")
        sha = item.get("sha256")
        size_bytes = item.get("size_bytes")
        if not isinstance(workspace_path, str) or _unsafe_relative_archive_path(workspace_path):
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "runtime_manifest.fact_layer_import.imported_files workspace_path must be safe and relative.",
                index=index,
                workspace_path=workspace_path,
            )
        if workspace_path in seen:
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "runtime_manifest.fact_layer_import.imported_files contains duplicate workspace_path.",
                workspace_path=workspace_path,
            )
        seen.add(workspace_path)
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "runtime_manifest.fact_layer_import.imported_files sha256 is invalid.",
                index=index,
                workspace_path=workspace_path,
            )
        if not isinstance(size_bytes, int) or size_bytes < 0:
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "runtime_manifest.fact_layer_import.imported_files size_bytes is invalid.",
                index=index,
                workspace_path=workspace_path,
            )
        target = (workspace / workspace_path).resolve()
        try:
            target.relative_to(workspace.resolve())
        except ValueError:
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "runtime_manifest.fact_layer_import.imported_files workspace_path escapes workspace.",
                index=index,
                workspace_path=workspace_path,
            )
        if not target.exists() or not target.is_file():
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "imported fact-layer file is missing.",
                index=index,
                workspace_path=workspace_path,
            )
        if target.stat().st_size != size_bytes:
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "imported fact-layer file size does not match runtime_manifest.fact_layer_import.",
                index=index,
                workspace_path=workspace_path,
                expected_size_bytes=size_bytes,
                actual_size_bytes=target.stat().st_size,
            )
        actual_sha = _sha256_file(target)
        if actual_sha != sha:
            _raise_experiment_error(
                "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID",
                "imported fact-layer file hash does not match runtime_manifest.fact_layer_import.",
                index=index,
                workspace_path=workspace_path,
                expected_sha256=sha,
                actual_sha256=actual_sha,
            )


def _case_source_archive_logical_path(*, archive_manifest_path: Path) -> str:
    run_id = archive_manifest_path.parent.name
    return f"output/runs/{run_id}/manifest.json"


def _resolve_case_source_archive_manifest(*, case_root: Path, frozen_fact_layer: dict[str, Any]) -> Path:
    raw = frozen_fact_layer.get("source_archive_path")
    if not isinstance(raw, str) or not raw.strip():
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARCHIVE_MISSING",
            "auditable_brief registration requires frozen_fact_layer.source_archive_path.",
        )
    raw_path = Path(raw).expanduser()
    candidates = [raw_path] if raw_path.is_absolute() else [case_root / raw_path, case_root.parent / raw_path]
    for candidate in candidates:
        path = candidate / "manifest.json" if candidate.is_dir() else candidate
        if path.exists() and path.is_file():
            return path.resolve()
    _raise_experiment_error(
        "E_EXPERIMENT_080_ARCHIVE_MISSING",
        "auditable_brief registration could not find frozen_fact_layer.source_archive_path.",
        archive=raw,
        searched=[str(candidate) for candidate in candidates],
    )


def _auditable_target_artifacts(*, workspace: Path, repo_workdir: str | Path | None) -> dict[str, Any]:
    registry = _load_json_object(
        workspace / "output" / "intermediate" / "artifact_registry.json",
        label="artifact_registry",
    )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, dict):
        _raise_experiment_error(
            "E_EXPERIMENT_080_ARTIFACT_REGISTRY_INVALID",
            "artifact_registry.json artifacts must be an object.",
        )
    projected: dict[str, Any] = {}
    for artifact_id, expected_path in AUDITABLE_TARGET_ARTIFACTS.items():
        record = artifacts.get(artifact_id)
        if not isinstance(record, dict):
            _raise_experiment_error(
                "E_EXPERIMENT_080_TARGET_ARTIFACT_INVALID",
                "auditable_brief target artifact is missing from artifact_registry.",
                artifact_id=artifact_id,
            )
        path_text = record.get("path")
        sha = record.get("sha256")
        status = record.get("status")
        validation_result = record.get("validation_result")
        if path_text != expected_path or status != "valid" or not isinstance(sha, str) or not _SHA256_RE.match(sha):
            _raise_experiment_error(
                "E_EXPERIMENT_080_TARGET_ARTIFACT_INVALID",
                "auditable_brief target artifact must be frozen as a valid artifact.",
                artifact_id=artifact_id,
                expected_path=expected_path,
                path=path_text,
                status=status,
                validation_result=validation_result,
            )
        file_path = (workspace / expected_path).resolve()
        try:
            file_path.relative_to(workspace.resolve())
        except ValueError:
            _raise_experiment_error(
                "E_EXPERIMENT_080_TARGET_ARTIFACT_INVALID",
                "auditable_brief target artifact path escapes workspace.",
                artifact_id=artifact_id,
                path=expected_path,
            )
        if not file_path.exists() or not file_path.is_file():
            _raise_experiment_error(
                "E_EXPERIMENT_080_TARGET_ARTIFACT_INVALID",
                "auditable_brief target artifact file is missing.",
                artifact_id=artifact_id,
                path=expected_path,
            )
        actual_sha = _sha256_file(file_path)
        if actual_sha != sha:
            _raise_experiment_error(
                "E_EXPERIMENT_080_TARGET_ARTIFACT_INVALID",
                "auditable_brief target artifact hash does not match artifact_registry.",
                artifact_id=artifact_id,
                path=expected_path,
                expected_sha256=sha,
                actual_sha256=actual_sha,
            )
        projection = {
            "path": expected_path,
            "sha256": sha,
            "status": status,
            "validation_result": validation_result or "",
            "frozen_valid": True,
        }
        if artifact_id == "auditor_quality_gate_report":
            binding = _auditable_quality_gate_binding(
                workspace=workspace,
                repo_workdir=repo_workdir,
            )
            projection["report_status"] = binding["gate_status"]
            projection["binding_status"] = binding["status"]
            projection["no_blocking"] = binding["status"] == "pass"
            projection["binding_reasons"] = binding["reasons"]
        projected[artifact_id] = projection
    return projected


def _auditable_quality_gate_binding(*, workspace: Path, repo_workdir: str | Path | None) -> dict[str, Any]:
    repo = resolve_repo_workdir(repo_workdir, workspace=workspace)
    verdict = interpret_quality_gate_binding(
        workspace=workspace,
        stage_id="auditor",
        expected_brief="output/intermediate/audited_brief.md",
        expected_ledger="output/intermediate/claim_ledger.json",
        stages=load_stage_specs(repo),
        artifacts=load_artifact_contracts(repo),
    )
    reasons = require_quality_gate_binding_pass(verdict)
    if reasons:
        _raise_experiment_error(
            "E_EXPERIMENT_080_AUDITOR_GATE_BLOCKED",
            "auditable_brief registration requires a canonical passing auditor quality gate binding.",
            reasons=reasons,
        )
    projection = project_quality_gate_binding_for_read(verdict)
    return {
        "status": str(projection.get("status") or ""),
        "gate_status": str(projection.get("gate_status") or ""),
        "reasons": reasons,
    }


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


def _portable_run_archive_path(*, output_path: Path, workspace: Path, archive_manifest_path: Path) -> str:
    output_parent = output_path.parent.resolve()
    workspace_root = workspace.resolve()
    try:
        output_parent.relative_to(workspace_root)
    except ValueError:
        return Path(os.path.relpath(archive_manifest_path.resolve(), start=output_parent)).as_posix()
    return _workspace_relative(workspace, archive_manifest_path)


def _raise_experiment_error(code: str, message: str, **details: Any) -> None:
    raise Experiment080Error(message, details={"code": code, **details})


def _validate_a_controlled_scorecard(
    *,
    control: dict[str, Any],
    fact_layer: dict[str, Any],
    guidance_scores: list[Any],
    reader_clean: dict[str, Any],
    assessment_target: str,
    treatment_isolation: dict[str, Any],
    diagnostics: list[Experiment080Diagnostic],
) -> None:
    required_values = {
        **{
            f"control_integrity.{key}": control.get(key)
            for key in _control_keys_for_target(assessment_target)
        },
        "frozen_fact_layer.matches_case": fact_layer.get("matches_case"),
    }
    required_values["treatment_isolation.status_passed"] = (
        treatment_isolation.get("status") == "pass"
    )
    if _reader_clean_required_for_target(assessment_target):
        required_values["reader_clean.pass"] = reader_clean.get("pass")
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


def _assessment_target(container: dict[str, Any]) -> str:
    return _target_contract_assessment_target(container)


def _validate_assessment_target(
    value: Any,
    diagnostics: list[Experiment080Diagnostic],
    *,
    path: str,
) -> None:
    if value not in ALLOWED_ASSESSMENT_TARGETS:
        diagnostics.append(_diag(
            "invalid_assessment_target",
            f"{path} must be one of {sorted(ALLOWED_ASSESSMENT_TARGETS)}.",
            path=path,
        ))


def _control_keys_for_target(target: str) -> tuple[str, ...]:
    return A_CONTROLLED_REQUIRED_CONTROL_KEYS_BY_TARGET.get(target, DELIVERY_BRIEF_REQUIRED_CONTROL_KEYS)


def _reader_clean_required_for_target(target: str) -> bool:
    return target == "delivery_brief"


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
