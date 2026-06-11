"""Runtime state and artifact registry support for the Orchestrator.

This module deliberately does not reuse ``core.manifest``.  The core manifest
tracks historical Python pipeline output, while this module tracks the
external-runtime handoff state introduced in v0.6.1.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.feedback.feedback_contract import (
    current_stage_feedback_blocking_reasons,
    optional_feedback_artifact_activated,
)
from multi_agent_brief.quality_gates.contract import (
    QualityGateContractError,
    current_stage_quality_gate_blocking_reasons,
    load_quality_gate_report,
    quality_gate_artifact_activated,
    validate_quality_gate_report_payload,
)
from multi_agent_brief.provenance.contract import provenance_artifact_activated
from multi_agent_brief import __version__
from multi_agent_brief.orchestrator_contract import (
    CONTRACT_REFERENCES,
    DECISION_VOCABULARY,
    resolve_repo_workdir,
)
from multi_agent_brief.outputs.reader_final_gate import (
    combine_reader_final_gate_results,
    detect_reader_residue,
    detect_reader_residue_in_docx,
)


RUNTIME_MANIFEST_SCHEMA = "multi-agent-brief-runtime-manifest/v1"
WORKFLOW_STATE_SCHEMA = "multi-agent-brief-workflow-state/v1"
ARTIFACT_REGISTRY_SCHEMA = "multi-agent-brief-artifact-registry/v1"
EVENT_LOG_SCHEMA = "multi-agent-brief-event-log/v1"

RUNTIME_STATE_FILES = {
    "runtime_manifest": "output/intermediate/runtime_manifest.json",
    "workflow_state": "output/intermediate/workflow_state.json",
    "artifact_registry": "output/intermediate/artifact_registry.json",
    "event_log": "output/intermediate/event_log.jsonl",
}
PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS = ("improvement", "recipe")

EVENT_TYPES = {
    "run_initialized",
    "handoff_written",
    "artifact_observed",
    "artifact_validated",
    "stage_status_changed",
    "decision_recorded",
    "feedback_issue_created",
    "feedback_issue_planned",
    "feedback_issue_resolved",
    "repair_plan_created",
    "repair_plan_completed",
    "quality_gate_checked",
    "quality_gate_blocked",
    "quality_gate_passed",
    "provenance_graph_built",
    "provenance_graph_validated",
    "provenance_graph_invalid",
    "audience_profile_snapshot_created",
    "control_switchboard_built",
    "control_switchboard_warning",
    "control_selection_recorded",
    "control_selection_validated",
    "improvement_proposed",
    "improvement_approved",
    "improvement_rejected",
    "improvement_reverted",
    "improvement_memory_snapshot_created",
    "run_blocked",
    "run_reset",
}

ACTORS = {"cli", "orchestrator", "runtime", "system"}

STAGE_PENDING = "pending"
STAGE_READY = "ready"
STAGE_COMPLETE = "complete"
STAGE_BLOCKED = "blocked"
STAGE_SKIPPED = "skipped"

ARTIFACT_EXPECTED = "expected"
ARTIFACT_MISSING = "missing"
ARTIFACT_PRESENT = "present"
ARTIFACT_VALID = "valid"
ARTIFACT_INVALID = "invalid"

E_STAGE_ALREADY_COMPLETED = "E_STAGE_ALREADY_COMPLETED"
E_STAGE_MISMATCH = "E_STAGE_MISMATCH"
E_REQUIRED_ARTIFACT_MISSING = "E_REQUIRED_ARTIFACT_MISSING"
E_ARTIFACT_INVALID = "E_ARTIFACT_INVALID"
E_ILLEGAL_TRANSITION = "E_ILLEGAL_TRANSITION"
E_MANIFEST_EXTENSION_LOST = "E_MANIFEST_EXTENSION_LOST"
E_TRANSACTION_PARTIAL_WRITE = "E_TRANSACTION_PARTIAL_WRITE"
E_TRANSACTION_INTEGRITY = "E_TRANSACTION_INTEGRITY"
E_RUNTIME_STATE_NOT_INITIALIZED = "E_RUNTIME_STATE_NOT_INITIALIZED"
E_QUALITY_GATE_REQUIRED = "E_QUALITY_GATE_REQUIRED"
E_READER_FINAL_GATE_FAILED = "E_READER_FINAL_GATE_FAILED"
E_COMPLETION_TRANSACTION_REQUIRED = "E_COMPLETION_TRANSACTION_REQUIRED"

MAX_RUN_ID_LENGTH = 200
_RUN_ID_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RUN_ID_WINDOWS_ABSOLUTE_RE = re.compile(r"\b[A-Za-z]:[\\/]")
_RUN_ID_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
]
_RUN_ID_FORBIDDEN_PATH_FRAGMENTS = ("/Users/", "/home/", "/var/", "file://")
_RUN_ID_INJECTION_PHRASES = ("system:", "developer:", "assistant:", "ignore previous", "ignore all previous")


class RuntimeStateError(Exception):
    """Raised when runtime state cannot be read or written safely."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details or {}
        self.error_code = error_code

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": False,
            "error": str(self),
            "details": self.details,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"mabw-{stamp}-{uuid.uuid4().hex[:8]}"


def _validate_runtime_run_id(value: Any, *, path: Path | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeStateError(
            "runtime run_id is required.",
            details={"path": str(path) if path is not None else None},
        )
    text = value.strip()
    if _unsafe_runtime_run_id(text):
        raise RuntimeStateError(
            "runtime run_id is unsafe.",
            details={"path": str(path) if path is not None else None},
        )
    return text


def _safe_previous_run_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if _unsafe_runtime_run_id(text):
        return "unsafe-run-id"
    return text


def _unsafe_runtime_run_id(text: str) -> bool:
    lower = text.lower()
    return (
        len(text) > MAX_RUN_ID_LENGTH
        or "\n" in text
        or "\r" in text
        or "/" in text
        or "\\" in text
        or text.lstrip().startswith("#")
        or "```" in text
        or "~~~" in text
        or "<!--" in text
        or "-->" in text
        or bool(_RUN_ID_CONTROL_CHAR_RE.search(text))
        or bool(_RUN_ID_WINDOWS_ABSOLUTE_RE.search(text))
        or any(fragment.lower() in lower for fragment in _RUN_ID_FORBIDDEN_PATH_FRAGMENTS)
        or any(pattern.search(text) for pattern in _RUN_ID_TOKEN_PATTERNS)
        or any(phrase in lower for phrase in _RUN_ID_INJECTION_PHRASES)
    )


def _source_or_package_version() -> str:
    for parent in Path(__file__).resolve().parents:
        version_file = parent / "VERSION"
        if version_file.exists():
            text = version_file.read_text(encoding="utf-8").strip()
            if text:
                return text
    return __version__


def runtime_state_paths(workspace: str | Path) -> dict[str, Path]:
    ws = Path(workspace).expanduser().resolve()
    return {key: ws / rel_path for key, rel_path in RUNTIME_STATE_FILES.items()}


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise RuntimeStateError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeStateError(
            f"Invalid JSON state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeStateError(
            f"State file must contain a JSON object: {path}",
            details={"path": str(path)},
        )
    return data


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    text += "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeStateError(
            f"Failed to write state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to append event log: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _read_event_log_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read event log: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if raw and not raw.endswith(b"\n"):
        raise RuntimeStateError(
            f"Event log is not newline-terminated: {path}",
            details={"path": str(path)},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeStateError(
                f"Invalid JSON event log line {lineno}: {path}",
                details={"path": str(path), "line": lineno, "reason": str(exc)},
                error_code=E_TRANSACTION_INTEGRITY,
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeStateError(
                f"Event log line {lineno} must contain an object: {path}",
                details={"path": str(path), "line": lineno},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        records.append(payload)
    return records


def _preflight_transaction_files(paths: dict[str, Path]) -> list[dict[str, Any]]:
    paths["runtime_manifest"].parent.mkdir(parents=True, exist_ok=True)
    for key in ("runtime_manifest", "workflow_state"):
        if not paths[key].exists():
            raise RuntimeStateError(
                "Runtime state is not initialized. Run `multi-agent-brief state init --workspace <workspace>` first.",
                details={"missing": str(paths[key])},
                error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
            )
    for key in ("runtime_manifest", "workflow_state", "artifact_registry"):
        path = paths[key]
        if path.exists():
            _read_json(path)
    return _read_event_log_records(paths["event_log"])


def _completion_transaction_event_exists(
    *,
    event_records: list[dict[str, Any]],
    transaction_id: str,
) -> bool:
    for event in event_records:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if (
            event.get("event_type") == "decision_recorded"
            and metadata.get("transaction_id") == transaction_id
        ):
            return True
    return False


def _completion_transaction_integrity_reason(
    *,
    paths: dict[str, Path],
    workflow: dict[str, Any],
) -> str:
    transaction = workflow.get("last_completion_transaction")
    if not isinstance(transaction, dict):
        return ""
    transaction_id = str(transaction.get("transaction_id") or "")
    if not transaction_id:
        return ""
    records = _read_event_log_records(paths["event_log"])
    if _completion_transaction_event_exists(event_records=records, transaction_id=transaction_id):
        return ""
    return (
        "Last completion transaction is missing its decision_recorded event: "
        f"{transaction_id}."
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeStateError(
            f"Invalid YAML contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeStateError(
            f"Contract file must contain a mapping: {path}",
            details={"path": str(path)},
        )
    return data


def _contract_file(repo_workdir: Path, rel_path: str) -> Path:
    path = repo_workdir / rel_path
    if not path.exists():
        raise RuntimeStateError(
            f"Contract file not found: {path}",
            details={"contract": rel_path, "repo_workdir": str(repo_workdir)},
        )
    return path


def load_stage_specs(repo_workdir: str | Path) -> list[dict[str, Any]]:
    repo = Path(repo_workdir).expanduser().resolve()
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["stage_specs"]))
    stages = ((data.get("workflow") or {}).get("stages") or [])
    if not isinstance(stages, list):
        raise RuntimeStateError("stage_specs.yaml workflow.stages must be a list")
    return [stage for stage in stages if isinstance(stage, dict)]


def load_artifact_contracts(repo_workdir: str | Path) -> list[dict[str, Any]]:
    repo = Path(repo_workdir).expanduser().resolve()
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["artifact_contracts"]))
    artifacts = data.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise RuntimeStateError("artifact_contracts.yaml artifacts must be a list")
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


def _stage_ids(stages: list[dict[str, Any]]) -> list[str]:
    return [str(stage["stage_id"]) for stage in stages if stage.get("stage_id")]


def _artifact_ids(artifacts: list[dict[str, Any]]) -> set[str]:
    return {
        str(artifact["artifact_id"])
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _artifact_map(artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(artifact["artifact_id"]): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _initial_stage_statuses(stages: list[dict[str, Any]], *, now: str) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    first = True
    for stage_id in _stage_ids(stages):
        statuses[stage_id] = {
            "status": STAGE_READY if first else STAGE_PENDING,
            "reason": "",
            "updated_at": now,
        }
        first = False
    return statuses


def _initial_workflow_state(
    *,
    run_id: str,
    stages: list[dict[str, Any]],
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    stage_statuses = _initial_stage_statuses(stages, now=updated_at)
    current_stage = _stage_ids(stages)[0] if stages else None
    return {
        "schema_version": WORKFLOW_STATE_SCHEMA,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "current_stage": current_stage,
        "blocked": False,
        "blocking_reason": "",
        "stage_statuses": stage_statuses,
        "last_decision": None,
        "next_allowed_decisions": _allowed_decisions_for_stage(stages, current_stage),
    }


def _allowed_decisions_for_stage(
    stages: list[dict[str, Any]],
    stage_id: str | None,
) -> list[str]:
    if stage_id is None:
        return []
    for stage in stages:
        if stage.get("stage_id") == stage_id:
            decisions = stage.get("allowed_decisions") or []
            return [str(decision) for decision in decisions]
    return []


def _runtime_manifest(
    *,
    run_id: str,
    created_at: str,
    updated_at: str,
    runtime: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = {
        "schema_version": RUNTIME_MANIFEST_SCHEMA,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "workspace": ".",
        "runtime": runtime,
        "mabw_version": _source_or_package_version(),
        "contract_references": dict(CONTRACT_REFERENCES),
        "runtime_state_files": dict(RUNTIME_STATE_FILES),
        "stage_order": _stage_ids(stages),
        "expected_artifacts": [
            {
                "artifact_id": artifact.get("artifact_id", ""),
                "path": artifact.get("path", ""),
                "required": bool(artifact.get("required", False)),
                "producer_stage": artifact.get("producer_stage", ""),
                "consumer_stages": artifact.get("consumer_stages", []),
            }
            for artifact in artifacts
        ],
    }
    return manifest


def initialize_runtime_state(
    *,
    workspace: str | Path,
    runtime: str = "hermes",
    repo_workdir: str | Path | None = None,
    reset_state: bool = False,
    actor: str = "cli",
    recipe: str | None = None,
) -> dict[str, Any]:
    """Initialize runtime control files for a workspace."""
    ws = _require_workspace(workspace)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    paths = runtime_state_paths(ws)
    paths["runtime_manifest"].parent.mkdir(parents=True, exist_ok=True)

    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)

    if reset_state:
        try:
            old_manifest = _read_json_if_exists(paths["runtime_manifest"])
        except RuntimeStateError:
            old_manifest = None
        old_workflow = None
    else:
        old_manifest = _read_json_if_exists(paths["runtime_manifest"])
        old_workflow = _read_json_if_exists(paths["workflow_state"])
    now = utc_now()
    created = old_manifest is None or reset_state
    previous_run_id = _safe_previous_run_id((old_manifest or {}).get("run_id")) if reset_state else None
    archived_event_log: str | None = None

    if reset_state:
        old_run_id = previous_run_id or "unknown"
        if paths["event_log"].exists():
            archive = paths["event_log"].with_name(f"event_log.{old_run_id}.jsonl")
            if archive.exists():
                archive = paths["event_log"].with_name(
                    f"event_log.{old_run_id}.{uuid.uuid4().hex[:8]}.jsonl"
                )
            os.replace(paths["event_log"], archive)
            archived_event_log = _workspace_relative(ws, archive)
    elif old_manifest and old_manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeStateError(
            "Existing runtime_manifest.json has an unsupported schema. "
            "Use --reset-state to start a new runtime state.",
            details={
                "path": str(paths["runtime_manifest"]),
                "schema_version": old_manifest.get("schema_version"),
            },
        )

    if old_manifest and not reset_state:
        run_id = _validate_runtime_run_id(
            old_manifest.get("run_id") or new_run_id(),
            path=paths["runtime_manifest"],
        )
        created_at = str(old_manifest.get("created_at") or now)
    else:
        run_id = _validate_runtime_run_id(new_run_id())
        created_at = now

    manifest = _runtime_manifest(
        run_id=run_id,
        created_at=created_at,
        updated_at=now,
        runtime=runtime,
        stages=stages,
        artifacts=artifacts,
    )
    if old_manifest and not reset_state:
        for key in PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS:
            if key in old_manifest:
                manifest[key] = old_manifest[key]
    if recipe is not None:
        manifest["recipe"] = str(recipe)

    if old_workflow and not reset_state:
        if old_workflow.get("schema_version") != WORKFLOW_STATE_SCHEMA:
            raise RuntimeStateError(
                "Existing workflow_state.json has an unsupported schema. "
                "Use --reset-state to start a new runtime state.",
                details={
                    "path": str(paths["workflow_state"]),
                    "schema_version": old_workflow.get("schema_version"),
                },
            )
        workflow = dict(old_workflow)
        workflow["updated_at"] = now
        workflow["run_id"] = run_id
    else:
        workflow = _initial_workflow_state(
            run_id=run_id,
            stages=stages,
            created_at=created_at,
            updated_at=now,
        )

    _write_json_atomic(paths["runtime_manifest"], manifest)
    _write_json_atomic(paths["workflow_state"], workflow)

    if created:
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="run_reset" if reset_state else "run_initialized",
            actor=actor,
            reason="Runtime state reset." if reset_state else "Runtime state initialized.",
            metadata={
                "runtime": runtime,
                "previous_run_id": previous_run_id,
                "archived_event_log": archived_event_log,
            } if reset_state else {"runtime": runtime},
        )

    return show_runtime_state(workspace=ws)


def _load_manifest_and_workflow(workspace: str | Path) -> tuple[Path, dict[str, Path], dict[str, Any], dict[str, Any]]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    manifest = _read_json_if_exists(paths["runtime_manifest"])
    workflow = _read_json_if_exists(paths["workflow_state"])
    if manifest is None or workflow is None:
        raise RuntimeStateError(
            "Runtime state is not initialized. Run `multi-agent-brief state init --workspace <workspace>` first.",
            details={"workspace": str(ws)},
        )
    if manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeStateError(
            "runtime_manifest.json has an unsupported schema.",
            details={"path": str(paths["runtime_manifest"]), "schema_version": manifest.get("schema_version")},
        )
    manifest["run_id"] = _validate_runtime_run_id(
        manifest.get("run_id"),
        path=paths["runtime_manifest"],
    )
    if workflow.get("schema_version") != WORKFLOW_STATE_SCHEMA:
        raise RuntimeStateError(
            "workflow_state.json has an unsupported schema.",
            details={"path": str(paths["workflow_state"]), "schema_version": workflow.get("schema_version")},
        )
    if workflow.get("run_id") is not None:
        workflow["run_id"] = _validate_runtime_run_id(
            workflow.get("run_id"),
            path=paths["workflow_state"],
        )
    return ws, paths, manifest, workflow


def show_runtime_state(*, workspace: str | Path) -> dict[str, Any]:
    ws, paths, manifest, workflow = _load_manifest_and_workflow(workspace)
    registry = _read_json_if_exists(paths["artifact_registry"])
    event_count = 0
    if paths["event_log"].exists():
        try:
            event_count = sum(1 for _ in paths["event_log"].open(encoding="utf-8"))
        except OSError:
            event_count = 0
    return {
        "ok": True,
        "workspace": str(ws),
        "runtime_state_files": dict(RUNTIME_STATE_FILES),
        "manifest": manifest,
        "workflow_state": workflow,
        "artifact_registry": registry,
        "event_count": event_count,
    }


def append_event(
    *,
    workspace: str | Path,
    run_id: str,
    event_type: str,
    actor: str,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    decision: str | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise RuntimeStateError(
            f"Unknown event type: {event_type}",
            details={"event_type": event_type},
        )
    if actor not in ACTORS:
        raise RuntimeStateError(
            f"Unknown event actor: {actor}",
            details={"actor": actor},
        )
    safe_run_id = _validate_runtime_run_id(run_id)
    ws = Path(workspace).expanduser().resolve()
    event = {
        "schema_version": EVENT_LOG_SCHEMA,
        "event_id": uuid.uuid4().hex,
        "run_id": safe_run_id,
        "created_at": utc_now(),
        "event_type": event_type,
        "actor": actor,
        "stage_id": stage_id,
        "artifact_id": artifact_id,
        "decision": decision,
        "reason": reason,
        "metadata": metadata or {},
    }
    _append_jsonl(runtime_state_paths(ws)["event_log"], event)
    return event


def record_handoff_written(
    *,
    workspace: str | Path,
    handoff_markdown: str | Path,
    handoff_json: str | Path,
    actor: str = "cli",
) -> dict[str, Any]:
    ws, _paths, manifest, _workflow = _load_manifest_and_workflow(workspace)
    run_id = str(manifest["run_id"])
    return append_event(
        workspace=ws,
        run_id=run_id,
        event_type="handoff_written",
        actor=actor,
        reason="Runtime handoff artifacts written.",
        metadata={
            "handoff_markdown": _workspace_relative(ws, Path(handoff_markdown)),
            "handoff_json": _workspace_relative(ws, Path(handoff_json)),
        },
    )


def _workspace_relative(workspace: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _validate_artifact(path: Path, fmt: str) -> tuple[str, str]:
    if not path.exists():
        return ARTIFACT_EXPECTED, "not_checked"
    if not path.is_file():
        return ARTIFACT_INVALID, "not_a_file"
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
            json.loads(text)
        elif fmt in {"yaml", "yml"}:
            yaml.safe_load(text)
        elif fmt == "markdown":
            pass
    except json.JSONDecodeError:
        return ARTIFACT_INVALID, "parse_error"
    except yaml.YAMLError:
        return ARTIFACT_INVALID, "parse_error"

    return ARTIFACT_VALID, "valid_minimum"


def _current_stage_index(stages: list[dict[str, Any]], stage_id: str | None) -> int | None:
    ids = _stage_ids(stages)
    if stage_id in ids:
        return ids.index(str(stage_id))
    return None


def _next_stage_id(stages: list[dict[str, Any]], stage_id: str) -> str | None:
    ids = _stage_ids(stages)
    if stage_id not in ids:
        return None
    idx = ids.index(stage_id)
    if idx + 1 >= len(ids):
        return None
    return ids[idx + 1]


def _stage_status(workflow: dict[str, Any], stage_id: str) -> str:
    stage = (workflow.get("stage_statuses") or {}).get(stage_id) or {}
    return str(stage.get("status") or STAGE_PENDING)


def _stage_is_complete_or_skipped(workflow: dict[str, Any], stage_id: str) -> bool:
    return _stage_status(workflow, stage_id) in {STAGE_COMPLETE, STAGE_SKIPPED}


def _artifact_record(
    *,
    workspace: Path,
    artifact: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, Any]:
    artifact_id = str(artifact.get("artifact_id") or "")
    rel_path = str(artifact.get("path") or "")
    fmt = str(artifact.get("format") or "")
    producer_stage = str(artifact.get("producer_stage") or "")
    status, validation_result = _validate_artifact(workspace / rel_path, fmt)

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

    path = workspace / rel_path
    size_bytes = path.stat().st_size if path.exists() and path.is_file() else None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat() if path.exists() else None

    return {
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
    }


def _build_artifact_registry(
    *,
    workspace: Path,
    run_id: str,
    artifacts: list[dict[str, Any]],
    workflow: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    records = {
        str(artifact.get("artifact_id")): _artifact_record(
            workspace=workspace,
            artifact=artifact,
            workflow=workflow,
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
            record.get("status") in {ARTIFACT_PRESENT, ARTIFACT_VALID, ARTIFACT_INVALID, ARTIFACT_MISSING}
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


def _stage_entry(workflow: dict[str, Any], stage_id: str | None) -> dict[str, Any]:
    if stage_id is None:
        return {}
    return ((workflow.get("stage_statuses") or {}).get(stage_id) or {})


def _changed_workflow_events(
    *,
    old_workflow: dict[str, Any],
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_stage = workflow.get("current_stage")
    old_current_stage = old_workflow.get("current_stage")
    old_entry = _stage_entry(old_workflow, str(current_stage) if current_stage else None)
    new_entry = _stage_entry(workflow, str(current_stage) if current_stage else None)
    stage_changed = (
        current_stage != old_current_stage
        or old_entry.get("status") != new_entry.get("status")
        or old_entry.get("reason") != new_entry.get("reason")
    )
    if current_stage and stage_changed:
        events.append({
            "event_type": "stage_status_changed",
            "stage_id": str(current_stage),
            "reason": str(workflow.get("blocking_reason") or ""),
            "metadata": {"status": new_entry.get("status")},
        })

    run_block_changed = (
        bool(workflow.get("blocked")) is True
        and (
            bool(old_workflow.get("blocked")) is not True
            or old_workflow.get("blocking_reason") != workflow.get("blocking_reason")
            or old_current_stage != current_stage
        )
    )
    if run_block_changed:
        events.append({
            "event_type": "run_blocked",
            "stage_id": str(current_stage) if current_stage else None,
            "reason": str(workflow.get("blocking_reason") or ""),
            "metadata": {},
        })
    return events


def _required_consumed_artifacts(
    *,
    stage: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    consumed = stage.get("consumes") or []
    required: list[str] = []
    for item in consumed:
        artifact_id = str(item)
        contract = artifacts_by_id.get(artifact_id)
        if contract and bool(contract.get("required", False)):
            required.append(artifact_id)
    return required


def _status_entry(status: str, reason: str, updated_at: str) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "updated_at": updated_at,
    }


def _completion_artifact_gate_reasons(
    *,
    workspace: Path,
    stage: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for artifact_id in stage.get("expected_artifacts") or []:
        contract = artifacts_by_id.get(str(artifact_id))
        if not contract:
            continue
        rel_path = str(contract.get("path") or "")
        fmt = str(contract.get("format") or "")
        status, validation_result = _validate_artifact(workspace / rel_path, fmt)
        required = bool(contract.get("required", False))
        if required and status != ARTIFACT_VALID:
            reasons.append(
                f"Required expected artifact '{artifact_id}' at '{rel_path}' is {status} ({validation_result})."
            )
        elif not required and status == ARTIFACT_INVALID:
            reasons.append(
                f"Optional expected artifact '{artifact_id}' at '{rel_path}' is invalid ({validation_result})."
            )
    return reasons


def _completion_decision_gate_reasons(
    *,
    workspace: Path,
    stage: dict[str, Any],
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    stage_id = str(stage.get("stage_id") or "")
    reasons = _completion_artifact_gate_reasons(
        workspace=workspace,
        stage=stage,
        artifacts_by_id=_artifact_map(artifacts),
    )
    reasons.extend(
        current_stage_feedback_blocking_reasons(
            workspace=workspace,
            current_stage=stage_id,
            stages=stages,
            artifacts=artifacts,
        )
    )
    reasons.extend(
        current_stage_quality_gate_blocking_reasons(
            workspace=workspace,
            current_stage=stage_id,
            stages=stages,
            artifacts=artifacts,
        )
    )
    return reasons


def _quality_gate_pass_reasons(
    *,
    workspace: Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    try:
        payload = load_quality_gate_report(workspace)
    except QualityGateContractError as exc:
        return [f"Quality gate report is invalid: {exc}"]
    if payload is None:
        return ["quality_gate_report.json is required before completing this stage."]

    errors = validate_quality_gate_report_payload(payload, stages=stages, artifacts=artifacts)
    if errors:
        return [f"Quality gate report is invalid: {' '.join(errors)}"]
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    gate_stage_id = str(metadata.get("gate_stage_id") or metadata.get("stage_id") or "")
    if gate_stage_id != "auditor":
        return [
            "Quality gate report must be generated for auditor completion "
            f"(metadata.gate_stage_id='auditor'); got {gate_stage_id or '<missing>'}."
        ]
    expected_brief = "output/intermediate/audited_brief.md"
    expected_ledger = "output/intermediate/claim_ledger.json"
    brief_ref = str(metadata.get("brief") or metadata.get("audited_brief") or "")
    ledger_ref = str(metadata.get("ledger") or metadata.get("claim_ledger") or "")
    if brief_ref != expected_brief:
        return [f"Quality gate report brief metadata must be {expected_brief}; got {brief_ref}."]
    if ledger_ref != expected_ledger:
        return [f"Quality gate report ledger metadata must be {expected_ledger}; got {ledger_ref}."]
    gate_ids = {
        str(result.get("gate_id") or "")
        for result in payload.get("gate_results") or []
        if isinstance(result, dict)
    }
    required_gate_ids = {"material_fact", "freshness", "target_relevance"}
    missing_gate_ids = sorted(required_gate_ids - gate_ids)
    if missing_gate_ids:
        return [
            "Quality gate report must include material_fact, freshness, and target_relevance gate_results; "
            f"missing: {', '.join(missing_gate_ids)}."
        ]
    if payload.get("status") == "fail":
        return ["Quality gate report status is fail."]
    failed_gate_ids = sorted(
        str(result.get("gate_id") or "")
        for result in payload.get("gate_results") or []
        if isinstance(result, dict) and result.get("status") == "fail"
    )
    if failed_gate_ids:
        return [f"Quality gate report has failing gate_results: {', '.join(failed_gate_ids)}."]
    blocking_findings = [
        str(finding.get("finding_id") or "")
        for finding in payload.get("findings") or []
        if isinstance(finding, dict) and finding.get("blocking_level") == "blocking"
    ]
    if blocking_findings:
        return [
            "Quality gate report has blocking findings: "
            + ", ".join(finding for finding in blocking_findings if finding)
        ]
    return []


def _resolve_report_artifact_path(workspace: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _finalize_report_reader_artifact_paths(workspace: Path, report: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    required_brief = workspace / "output" / "brief.md"
    paths.append(required_brief.resolve())
    for key in ("reader_brief", "named_reader_brief", "reader_docx", "named_reader_docx", "source_appendix"):
        path = _resolve_report_artifact_path(workspace, report.get(key))
        if path is not None:
            paths.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        marker = str(path)
        if marker not in seen:
            seen.add(marker)
            unique.append(path)
    return unique


def _finalize_completion_reasons(workspace: Path) -> list[str]:
    reasons: list[str] = []
    report_path = workspace / "output" / "intermediate" / "finalize_report.json"
    if not report_path.exists():
        return ["finalize_report.json is required before finalize-complete."]
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"finalize_report.json is invalid JSON: {exc}"]
    except OSError as exc:
        return [f"finalize_report.json could not be read: {exc}"]
    if not isinstance(report, dict):
        return ["finalize_report.json must contain an object."]
    if report.get("status") != "pass":
        reasons.append("finalize_report.json status must be pass.")
    reader_clean = report.get("reader_clean")
    if not isinstance(reader_clean, dict) or reader_clean.get("status") != "pass":
        reasons.append("finalize_report.json reader_clean.status must be pass.")

    artifact_paths = _finalize_report_reader_artifact_paths(workspace, report)
    missing = [path for path in artifact_paths if not path.exists()]
    if missing:
        reasons.append(
            "finalize_report.json references missing reader artifacts: "
            + ", ".join(str(path) for path in missing)
        )
        return reasons

    gate_results = []
    for path in artifact_paths:
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            try:
                gate_results.append(
                    detect_reader_residue(path.read_text(encoding="utf-8"), artifact=str(path))
                )
            except OSError as exc:
                reasons.append(f"Reader artifact could not be read: {path}: {exc}")
        elif suffix == ".docx":
            gate_results.append(detect_reader_residue_in_docx(path, artifact=str(path)))
    if gate_results:
        reader_gate = combine_reader_final_gate_results(gate_results)
        if reader_gate.status == "fail":
            reasons.append(
                "Current reader artifacts fail reader final gate: "
                f"{sum(reader_gate.counts.values())} residue findings."
            )
    return reasons


def _raise_completion_reasons(
    *,
    message: str,
    reasons: list[str],
    error_code: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload = dict(details or {})
    payload["blocking_reasons"] = reasons
    raise RuntimeStateError(
        f"{message}: {' '.join(reasons)}",
        details=payload,
        error_code=error_code,
    )


def _recompute_stage_state(
    *,
    workspace: Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    registry: dict[str, Any],
    previous_workflow: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    previous_statuses = previous_workflow.get("stage_statuses") or {}
    artifact_records = registry.get("artifacts") or {}
    artifacts_by_id = _artifact_map(artifacts)
    new_statuses: dict[str, dict[str, Any]] = {}
    current_stage: str | None = None
    blocked = False
    blocking_reason = ""

    for stage in stages:
        stage_id = str(stage.get("stage_id") or "")
        if not stage_id:
            continue

        previous = previous_statuses.get(stage_id) or {}
        previous_status = str(previous.get("status") or STAGE_PENDING)
        if previous_status in {STAGE_COMPLETE, STAGE_SKIPPED}:
            new_statuses[stage_id] = _status_entry(
                previous_status,
                str(previous.get("reason") or ""),
                str(previous.get("updated_at") or updated_at),
            )
            continue

        if current_stage is not None:
            new_statuses[stage_id] = _status_entry(STAGE_PENDING, "", updated_at)
            continue

        last_decision = previous_workflow.get("last_decision") or {}
        if (
            previous_status == STAGE_BLOCKED
            and last_decision.get("stage_id") == stage_id
            and last_decision.get("decision") in {"request_human_review", "block_run"}
        ):
            current_stage = stage_id
            blocked = True
            blocking_reason = str(previous.get("reason") or last_decision.get("reason") or "")
            new_statuses[stage_id] = _status_entry(STAGE_BLOCKED, blocking_reason, updated_at)
            continue

        reasons: list[str] = []
        for artifact_id in _required_consumed_artifacts(stage=stage, artifacts_by_id=artifacts_by_id):
            record = artifact_records.get(artifact_id) or {}
            if record.get("status") != ARTIFACT_VALID:
                reasons.append(
                    f"Required artifact '{artifact_id}' is {record.get('status', ARTIFACT_EXPECTED)}."
                )

        for artifact_id in stage.get("expected_artifacts") or []:
            record = artifact_records.get(str(artifact_id)) or {}
            if record.get("status") == ARTIFACT_INVALID:
                reasons.append(
                    f"Expected output artifact '{artifact_id}' is invalid."
                )

        reasons.extend(
            current_stage_feedback_blocking_reasons(
                workspace=workspace,
                current_stage=stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )
        reasons.extend(
            current_stage_quality_gate_blocking_reasons(
                workspace=workspace,
                current_stage=stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )

        if reasons:
            current_stage = stage_id
            blocked = True
            blocking_reason = " ".join(reasons)
            new_statuses[stage_id] = _status_entry(STAGE_BLOCKED, blocking_reason, updated_at)
        else:
            current_stage = stage_id
            new_statuses[stage_id] = _status_entry(STAGE_READY, "", updated_at)

    workflow = dict(previous_workflow)
    workflow["updated_at"] = updated_at
    workflow["current_stage"] = current_stage
    workflow["blocked"] = blocked
    workflow["blocking_reason"] = blocking_reason
    workflow["stage_statuses"] = new_statuses
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)
    return workflow


def check_runtime_state(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    """Refresh artifact registry and stage readiness without running stages."""
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    if not paths["runtime_manifest"].exists() or not paths["workflow_state"].exists():
        initialize_runtime_state(workspace=ws, repo_workdir=repo_workdir, actor=actor)

    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    old_registry = _read_json_if_exists(paths["artifact_registry"])
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    now = utc_now()
    run_id = str(manifest["run_id"])

    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=workflow,
        updated_at=now,
    )
    refreshed_workflow = _recompute_stage_state(
        workspace=ws,
        stages=stages,
        artifacts=artifacts,
        registry=registry,
        previous_workflow=workflow,
        updated_at=now,
    )
    transaction_integrity_warning = _completion_transaction_integrity_reason(
        paths=paths,
        workflow=refreshed_workflow,
    )
    if transaction_integrity_warning:
        refreshed_workflow["blocked"] = True
        refreshed_workflow["blocking_reason"] = transaction_integrity_warning
        current_stage = refreshed_workflow.get("current_stage")
        if current_stage:
            statuses = dict(refreshed_workflow.get("stage_statuses") or {})
            statuses[str(current_stage)] = _status_entry(
                STAGE_BLOCKED,
                transaction_integrity_warning,
                now,
            )
            refreshed_workflow["stage_statuses"] = statuses

    planned_events = [
        *_changed_artifact_events(old_registry=old_registry, registry=registry),
        *_changed_workflow_events(old_workflow=workflow, workflow=refreshed_workflow),
    ]
    for event in planned_events:
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type=str(event["event_type"]),
            actor=actor,
            stage_id=event.get("stage_id"),
            artifact_id=event.get("artifact_id"),
            reason=str(event.get("reason") or ""),
            metadata=event.get("metadata") or {},
        )

    _write_json_atomic(paths["artifact_registry"], registry)
    _write_json_atomic(paths["workflow_state"], refreshed_workflow)

    control_switchboard_warning: dict[str, Any] | None = None

    try:
        from multi_agent_brief.controls.contract import ControlSwitchboardError
        from multi_agent_brief.controls.switchboard import refresh_control_switchboard_if_stale

        try:
            refresh_control_switchboard_if_stale(
                workspace=ws,
                repo_workdir=repo,
                actor=actor,
            )
        except ControlSwitchboardError as exc:
            control_switchboard_warning = {
                "error": str(exc),
                "details": exc.details,
            }
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="control_switchboard_warning",
                actor=actor,
                reason=str(exc),
                metadata=exc.details,
            )
    except ImportError:
        pass

    state = show_runtime_state(workspace=ws)
    if control_switchboard_warning is not None:
        state["control_switchboard_warning"] = control_switchboard_warning
    if transaction_integrity_warning:
        state["transaction_integrity_warning"] = {
            "error_code": E_TRANSACTION_INTEGRITY,
            "message": transaction_integrity_warning,
        }
    return state


def _validate_completion_target(
    *,
    stage_id: str,
    workflow: dict[str, Any],
    stage_by_id: dict[str, dict[str, Any]],
    finalize: bool,
) -> dict[str, Any]:
    if stage_id not in stage_by_id:
        raise RuntimeStateError(
            f"Unknown stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": list(stage_by_id)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    current_stage = workflow.get("current_stage")
    if current_stage is None and _stage_status(workflow, stage_id) == STAGE_COMPLETE:
        raise RuntimeStateError(
            f"Stage '{stage_id}' is already complete.",
            details={"stage_id": stage_id},
            error_code=E_STAGE_ALREADY_COMPLETED,
        )
    if stage_id != current_stage:
        if _stage_status(workflow, stage_id) == STAGE_COMPLETE:
            raise RuntimeStateError(
                f"Stage '{stage_id}' is already complete.",
                details={"stage_id": stage_id, "current_stage": current_stage},
                error_code=E_STAGE_ALREADY_COMPLETED,
            )
        raise RuntimeStateError(
            f"Completion stage '{stage_id}' does not match current stage '{current_stage}'.",
            details={"stage_id": stage_id, "current_stage": current_stage},
            error_code=E_STAGE_MISMATCH,
        )
    if finalize and stage_id != "finalize":
        raise RuntimeStateError(
            "finalize-complete can only complete the finalize stage.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if not finalize and stage_id == "finalize":
        raise RuntimeStateError(
            "stage-complete cannot complete the finalize stage; use finalize-complete.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )
    stage = stage_by_id[stage_id]
    decision = "finalize" if finalize else "continue"
    allowed = [str(item) for item in (stage.get("allowed_decisions") or [])]
    if decision not in allowed:
        raise RuntimeStateError(
            f"Decision '{decision}' is not allowed for stage '{stage_id}'.",
            details={"stage_id": stage_id, "decision": decision, "stage_allowed_decisions": allowed},
            error_code=E_ILLEGAL_TRANSITION,
        )
    return stage


def _workflow_after_completion(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    stage_id: str,
    reason: str,
    now: str,
    transaction_id: str,
    finalize: bool,
) -> dict[str, Any]:
    decision = "finalize" if finalize else "continue"
    next_stage = _next_stage_id(stages, stage_id)
    current_stage = None if finalize else next_stage
    statuses = dict(workflow.get("stage_statuses") or {})
    statuses[stage_id] = _status_entry(STAGE_COMPLETE, reason, now)
    if current_stage:
        statuses[current_stage] = _status_entry(STAGE_READY, "", now)
    updated = dict(workflow)
    updated["updated_at"] = now
    updated["current_stage"] = current_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["last_decision"] = {
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    updated["last_completion_transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)
    return updated


def _append_transaction_events(
    *,
    workspace: Path,
    run_id: str,
    actor: str,
    transaction_id: str,
    stage_id: str,
    decision: str,
    reason: str,
    next_stage: str | None,
    artifact_events: list[dict[str, Any]],
) -> None:
    try:
        for event in artifact_events:
            metadata = dict(event.get("metadata") or {})
            metadata["transaction_id"] = transaction_id
            append_event(
                workspace=workspace,
                run_id=run_id,
                event_type=str(event["event_type"]),
                actor=actor,
                stage_id=event.get("stage_id"),
                artifact_id=event.get("artifact_id"),
                reason=str(event.get("reason") or ""),
                metadata=metadata,
            )
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type="decision_recorded",
            actor=actor,
            stage_id=stage_id,
            decision=decision,
            reason=reason,
            metadata={"next_stage": next_stage, "transaction_id": transaction_id},
        )
    except RuntimeStateError as exc:
        raise RuntimeStateError(
            "Completion transaction partially wrote state but failed to append event.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "decision": decision,
                "event_error": str(exc),
                "event_details": exc.details,
            },
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc


def _preserved_manifest_extensions(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: manifest[key]
        for key in PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS
        if key in manifest
    }


def _assert_manifest_extensions_preserved(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    missing = [
        key
        for key, value in before.items()
        if key not in after or after.get(key) != value
    ]
    if missing:
        raise RuntimeStateError(
            "Registered runtime_manifest extension keys were lost.",
            details={"missing_extensions": missing},
            error_code=E_MANIFEST_EXTENSION_LOST,
        )


def _complete_stage_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    finalize: bool = False,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    stage = _validate_completion_target(
        stage_id=stage_id,
        workflow=workflow,
        stage_by_id=stage_by_id,
        finalize=finalize,
    )

    artifact_reasons = _completion_artifact_gate_reasons(
        workspace=ws,
        stage=stage,
        artifacts_by_id=_artifact_map(artifacts),
    )
    if artifact_reasons:
        code = E_REQUIRED_ARTIFACT_MISSING
        if any("invalid" in item.lower() for item in artifact_reasons):
            code = E_ARTIFACT_INVALID
        _raise_completion_reasons(
            message=f"Cannot complete stage '{stage_id}'",
            reasons=artifact_reasons,
            error_code=code,
            details={"stage_id": stage_id},
        )

    feedback_reasons = current_stage_feedback_blocking_reasons(
        workspace=ws,
        current_stage=stage_id,
        stages=stages,
        artifacts=artifacts,
    )
    if feedback_reasons:
        _raise_completion_reasons(
            message=f"Cannot complete stage '{stage_id}'",
            reasons=feedback_reasons,
            error_code=E_ILLEGAL_TRANSITION,
            details={"stage_id": stage_id},
        )

    quality_reasons = current_stage_quality_gate_blocking_reasons(
        workspace=ws,
        current_stage=stage_id,
        stages=stages,
        artifacts=artifacts,
    )
    if stage_id == "auditor":
        quality_reasons.extend(_quality_gate_pass_reasons(workspace=ws, stages=stages, artifacts=artifacts))
    if quality_reasons:
        _raise_completion_reasons(
            message=f"Cannot complete stage '{stage_id}'",
            reasons=quality_reasons,
            error_code=E_QUALITY_GATE_REQUIRED,
            details={"stage_id": stage_id},
        )

    if finalize:
        finalize_reasons = _finalize_completion_reasons(ws)
        if finalize_reasons:
            _raise_completion_reasons(
                message="Cannot complete finalize stage",
                reasons=finalize_reasons,
                error_code=E_READER_FINAL_GATE_FAILED,
                details={"stage_id": stage_id},
            )

    transaction_id = uuid.uuid4().hex
    now = utc_now()
    run_id = str(manifest["run_id"])
    preserved_extensions = _preserved_manifest_extensions(manifest)
    next_workflow = _workflow_after_completion(
        workflow=workflow,
        stages=stages,
        stage_id=stage_id,
        reason=reason,
        now=now,
        transaction_id=transaction_id,
        finalize=finalize,
    )
    old_registry = _read_json_if_exists(paths["artifact_registry"])
    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=next_workflow,
        updated_at=now,
    )
    artifact_events = _changed_artifact_events(old_registry=old_registry, registry=registry)

    state_written = False
    try:
        _write_json_atomic(paths["artifact_registry"], registry)
        state_written = True
        _write_json_atomic(paths["workflow_state"], next_workflow)
    except RuntimeStateError as exc:
        code = E_TRANSACTION_PARTIAL_WRITE if state_written else exc.error_code
        raise RuntimeStateError(
            "Completion transaction failed while writing state files.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "state_error": str(exc),
                "state_details": exc.details,
            },
            error_code=code,
        ) from exc

    _append_transaction_events(
        workspace=ws,
        run_id=run_id,
        actor=actor,
        transaction_id=transaction_id,
        stage_id=stage_id,
        decision="finalize" if finalize else "continue",
        reason=reason,
        next_stage=next_workflow.get("current_stage"),
        artifact_events=artifact_events,
    )

    current_manifest = _read_json(paths["runtime_manifest"])
    _assert_manifest_extensions_preserved(before=preserved_extensions, after=current_manifest)
    state = show_runtime_state(workspace=ws)
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": stage_id,
        "decision": "finalize" if finalize else "continue",
    }
    return state


def complete_stage_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id=stage_id,
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=False,
    )


def complete_finalize_transaction(
    *,
    workspace: str | Path,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id="finalize",
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=True,
    )


def record_decision(
    *,
    workspace: str | Path,
    stage_id: str,
    decision: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    if not paths["runtime_manifest"].exists() or not paths["workflow_state"].exists():
        initialize_runtime_state(workspace=ws, repo_workdir=repo_workdir, actor=actor)

    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    if stage_id not in stage_by_id:
        raise RuntimeStateError(
            f"Unknown stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": list(stage_by_id)},
        )
    if decision not in DECISION_VOCABULARY:
        raise RuntimeStateError(
            f"Unknown Orchestrator decision: {decision}",
            details={"decision": decision, "allowed_decisions": list(DECISION_VOCABULARY)},
        )
    stage_allowed = [str(item) for item in (stage_by_id[stage_id].get("allowed_decisions") or [])]
    if decision not in stage_allowed:
        raise RuntimeStateError(
            f"Decision '{decision}' is not allowed for stage '{stage_id}'.",
            details={"stage_id": stage_id, "decision": decision, "stage_allowed_decisions": stage_allowed},
        )
    current_stage_before = workflow.get("current_stage")
    if current_stage_before is None:
        raise RuntimeStateError(
            "Cannot record a decision because the workflow has no current stage.",
            details={"stage_id": stage_id, "decision": decision},
        )
    if stage_id != current_stage_before:
        raise RuntimeStateError(
            f"Decision stage '{stage_id}' does not match current stage '{current_stage_before}'.",
            details={
                "stage_id": stage_id,
                "current_stage": current_stage_before,
                "decision": decision,
            },
        )

    if decision in {"continue", "finalize"}:
        command = "finalize-complete" if decision == "finalize" else "stage-complete"
        raise RuntimeStateError(
            (
                f"Decision '{decision}' must be recorded with `multi-agent-brief state {command}`. "
                "`state decide` is reserved for retry_stage, delegate_repair, request_human_review, and block_run."
            ),
            details={
                "stage_id": stage_id,
                "decision": decision,
                "required_command": command,
            },
            error_code=E_COMPLETION_TRANSACTION_REQUIRED,
        )

    now = utc_now()
    statuses = dict(workflow.get("stage_statuses") or {})
    blocked = False
    blocking_reason = ""
    current_stage: str | None = stage_id

    if decision in {"continue", "finalize"}:
        statuses[stage_id] = _status_entry(STAGE_COMPLETE, reason, now)
        next_stage = _next_stage_id(stages, stage_id)
        if next_stage and decision != "finalize":
            statuses[next_stage] = _status_entry(STAGE_READY, "", now)
            current_stage = next_stage
        else:
            current_stage = None
    elif decision in {"retry_stage", "delegate_repair"}:
        statuses[stage_id] = _status_entry(STAGE_READY, reason, now)
    elif decision in {"request_human_review", "block_run"}:
        statuses[stage_id] = _status_entry(STAGE_BLOCKED, reason, now)
        blocked = True
        blocking_reason = reason

    workflow["updated_at"] = now
    workflow["current_stage"] = current_stage
    workflow["blocked"] = blocked
    workflow["blocking_reason"] = blocking_reason
    workflow["stage_statuses"] = statuses
    workflow["last_decision"] = {
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)

    append_event(
        workspace=ws,
        run_id=str(manifest["run_id"]),
        event_type="decision_recorded",
        actor=actor,
        stage_id=stage_id,
        decision=decision,
        reason=reason,
        metadata={"next_stage": current_stage},
    )
    _write_json_atomic(paths["workflow_state"], workflow)
    return show_runtime_state(workspace=ws)
