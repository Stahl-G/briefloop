"""MABW-080 experiment harness schema validation.

080 validates whether approved Improvement Memory guidance manifests under a
frozen fact layer. These helpers are intentionally side-effect free: they do
not touch workspaces, runtime state, agent assets, or Improvement Ledger files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


EXPERIMENT_080_ID = "MABW-080"

CASE_MANIFEST_SCHEMA = "mabw.experiment_080.case.v1"
FROZEN_FACT_LAYER_SCHEMA = "mabw.experiment_080.frozen_fact_layer.v1"
GUIDANCE_SET_SCHEMA = "mabw.experiment_080.guidance_set.v1"
RUN_RECORD_SCHEMA = "mabw.experiment_080.run_record.v1"
SCORECARD_SCHEMA = "mabw.experiment_080.scorecard.v1"
CASE_VALIDATION_SCHEMA = "mabw.experiment_080.case_validation.v1"

ALLOWED_CONDITIONS = {"baseline", "memory", "prompt_only"}
ALLOWED_VALIDITY_CLASSES = {
    "A_controlled",
    "B_integration",
    "invalid_contaminated",
    "invalid_incomplete",
    "invalid_fact_layer_mismatch",
}
ALLOWED_RUN_INTEGRITY_STATUSES = {"clean", "contaminated"}
ALLOWED_GUIDANCE_SOURCES = {"improvement_ledger", "manual", "prompt_only"}
ALLOWED_ASSESSMENT_METHODS = {"human", "llm_assisted_human_review", "llm_only"}
A_CONTROLLED_ASSESSMENT_METHODS = {"human", "llm_assisted_human_review"}

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
    elif not guidance_scores:
        diagnostics.append(_diag(
            "empty_guidance_scores",
            "scorecard.guidance_scores must contain at least one guidance score.",
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
    if status == "contaminated" and value.get("reference_eligible") is True:
        diagnostics.append(_diag(
            "contaminated_run_reference_eligible",
            f"{path}.reference_eligible must be false or omitted when status is contaminated.",
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
