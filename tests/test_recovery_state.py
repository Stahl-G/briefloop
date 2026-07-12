from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import multi_agent_brief.orchestrator.recovery_state as recovery_state_module

from multi_agent_brief.cli.deliver_commands import (
    DeliverCommandError,
    E_DELIVERY_RUN_INTEGRITY_BLOCKED,
    _preflight_run_integrity_for_delivery,
)
from multi_agent_brief.experiments.experiment_080 import _registered_run_integrity
from multi_agent_brief.orchestrator.recovery_state import (
    OWNER_REVISION_SCHEMA,
    RECOVERY_CONTROL_INPUT_FILES,
    RECOVERY_AWAITING,
    RECOVERY_COMPLETED_NON_REFERENCE,
    RECOVERY_FINALIZE_COMPLETION_PENDING,
    RECOVERY_FINALIZE_RENDER_REQUIRED,
    RECOVERY_IN_PROGRESS,
    RECOVERY_INVALID,
    RECOVERY_NOT_APPLICABLE,
    RECOVERY_RERUN_PENDING,
    RecoveryControlPaths,
    evaluate_recovery_state,
    load_recovery_context,
    recovery_stale_artifact_baselines,
    resolve_recovery_control_paths,
)
from multi_agent_brief.orchestrator.runtime_state import build_completion_projection
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_REGISTRY_SCHEMA,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import EVENT_LOG_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA


ROOT = Path(__file__).resolve().parent.parent
LEGACY_FIXTURE_ROOT = ROOT / "tests/fixtures/legacy_recovery_main_ab9e79d7"
RUN_ID = "run-recovery-test"
CONTAMINATION_ID = "event-contamination-001"
RECOVERY_ID = "repair-complete-001"
REPAIR_START_ID = "repair-start-001"
REPAIR_STARTED_EVENT_ID = "event-repair-started-001"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _event(
    event_type: str,
    event_id: str,
    *,
    stage_id: str | None = None,
    decision: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "schema_version": EVENT_LOG_SCHEMA,
        "event_id": event_id,
        "run_id": RUN_ID,
        "created_at": "2026-07-10T00:00:00Z",
        "event_type": event_type,
        "actor": "system",
        "stage_id": stage_id,
        "artifact_id": None,
        "decision": decision,
        "reason": event_type,
        "metadata": metadata or {},
    }


def _workspace(tmp_path: Path, *, current_stage: str | None = "editor") -> Path:
    ws = tmp_path / "workspace"
    (ws / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "config.yaml").write_text("project_name: Recovery Test\n", encoding="utf-8")
    intermediate = ws / "output" / "intermediate"
    _write_json(
        intermediate / "runtime_manifest.json",
        {"schema_version": RUNTIME_MANIFEST_SCHEMA, "run_id": RUN_ID},
    )
    _write_json(
        intermediate / "workflow_state.json",
        {
            "schema_version": WORKFLOW_STATE_SCHEMA,
            "run_id": RUN_ID,
            "current_stage": current_stage,
            "blocked": False,
            "blocking_reason": "",
            "stage_statuses": {},
            "run_integrity": {
                "status": "clean",
                "reference_eligible": True,
                "clean_single_shot": True,
                "reasons": [],
            },
        },
    )
    (intermediate / "event_log.jsonl").write_text("", encoding="utf-8")
    return ws


def _read_workflow(ws: Path) -> dict:
    return json.loads((ws / "output/intermediate/workflow_state.json").read_text(encoding="utf-8"))


def _write_workflow(ws: Path, workflow: dict) -> None:
    _write_json(ws / "output/intermediate/workflow_state.json", workflow)


def _write_registry(ws: Path) -> None:
    _write_json(
        ws / "output/intermediate/artifact_registry.json",
        {
            "schema_version": ARTIFACT_REGISTRY_SCHEMA,
            "run_id": RUN_ID,
            "artifacts": {},
        },
    )


def _write_events(ws: Path, events: list[dict]) -> None:
    path = ws / "output/intermediate/event_log.jsonl"
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _mark_contaminated(ws: Path) -> dict:
    workflow = _read_workflow(ws)
    workflow["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test_contamination", "message": "changed"}],
    }
    _write_workflow(ws, workflow)
    contamination = _event("run_integrity_contaminated", CONTAMINATION_ID)
    _write_events(ws, [contamination])
    _write_registry(ws)
    return contamination


def _recovery_event(*, rerun_start_stage: str = "auditor") -> dict:
    return _event(
        "repair_completed",
        "event-repair-completed-001",
        stage_id="editor",
        decision="repair_complete",
        metadata={
            "owner_revision_schema_version": OWNER_REVISION_SCHEMA,
            "transaction_id": RECOVERY_ID,
            "repair_start_transaction_id": REPAIR_START_ID,
            "repair_started_event_id": REPAIR_STARTED_EVENT_ID,
            "contamination_event_id": CONTAMINATION_ID,
            "owner_stage": "editor",
            "artifact_id": "audited_brief",
            "rerun_start_stage": rerun_start_stage,
            "reference_eligible": False,
            "stale_artifact_baselines": {
                "audit_report": {"sha256": "old-audit"},
            },
        },
    )


def _repair_started_for(recovery: dict) -> dict:
    metadata = recovery["metadata"]
    owner_stage = str(metadata["owner_stage"])
    return _event(
        "repair_started",
        str(metadata["repair_started_event_id"]),
        stage_id=owner_stage,
        metadata={
            "transaction_id": metadata["repair_start_transaction_id"],
            "contamination_event_id": metadata["contamination_event_id"],
            "repair_owner": owner_stage,
        },
    )


def _legacy_repair_started_for(recovery: dict) -> dict:
    metadata = recovery["metadata"]
    owner_stage = str(metadata["repair_owner"])
    return _event(
        "repair_started",
        "legacy-repair-started-event",
        stage_id=owner_stage,
        metadata={
            "transaction_id": "legacy-repair-start-transaction",
            "repair_owner": owner_stage,
            "must_rerun_from": metadata["must_rerun_from"],
            "source": metadata["source"],
        },
    )


def _bind_recovery_pointer(ws: Path, *, rerun_start_stage: str = "auditor") -> None:
    workflow = _read_workflow(ws)
    workflow["last_repair_transaction"] = {
        "transaction_id": RECOVERY_ID,
        "run_id": RUN_ID,
        "contamination_event_id": CONTAMINATION_ID,
        "owner_stage": "editor",
        "artifact_id": "audited_brief",
        "rerun_start_stage": rerun_start_stage,
    }
    _write_workflow(ws, workflow)


def _write_bound_finalize_report(ws: Path) -> None:
    _write_json(
        ws / "output/intermediate/finalize_report.json",
        {
            "status": "pass",
            "finalize_transaction_id": "render-001",
            "reader_clean": {"status": "pass"},
            "delivery_promotion": "promoted",
            "recovery_binding": {
                "status": "bound_non_reference_recovery",
                "run_id": RUN_ID,
                "contamination_event_id": CONTAMINATION_ID,
                "recovery_transaction_id": RECOVERY_ID,
                "rerun_start_stage": "auditor",
                "reference_eligible": False,
            },
        },
    )


def _evaluate(ws: Path) -> dict:
    return evaluate_recovery_state(workspace=ws, repo_workdir=ROOT)


def _path_observation(path: Path) -> tuple:
    if path.is_symlink():
        stat = path.lstat()
        return ("symlink", path.readlink().as_posix(), stat.st_mtime_ns)
    if not path.exists():
        return ("missing",)
    stat = path.stat()
    return ("file", path.read_bytes(), stat.st_mtime_ns)


def _control_observations(
    control_paths: RecoveryControlPaths,
    *,
    extra_paths: tuple[Path, ...] = (),
) -> dict[str, tuple]:
    observed = {
        key: _path_observation(path)
        for key, path in _control_path_mapping(control_paths).items()
    }
    observed.update(
        {
            f"extra:{index}": _path_observation(path)
            for index, path in enumerate(extra_paths)
        }
    )
    return observed


def _control_path_mapping(
    control_paths: RecoveryControlPaths,
) -> dict[str, Path]:
    return {
        key: getattr(control_paths, key)
        for key, _relative_path in RECOVERY_CONTROL_INPUT_FILES
    }


def test_recovery_control_input_inventory_is_exact_and_resolver_is_read_only(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "empty-workspace"
    ws.mkdir()
    before = tuple(ws.rglob("*"))

    control_paths = resolve_recovery_control_paths(ws)

    assert RECOVERY_CONTROL_INPUT_FILES == (
        ("runtime_manifest", "output/intermediate/runtime_manifest.json"),
        ("workflow_state", "output/intermediate/workflow_state.json"),
        ("artifact_registry", "output/intermediate/artifact_registry.json"),
        ("event_log", "output/intermediate/event_log.jsonl"),
        ("finalize_report", "output/intermediate/finalize_report.json"),
    )
    assert tuple(_control_path_mapping(control_paths)) == tuple(
        key for key, _relative_path in RECOVERY_CONTROL_INPUT_FILES
    )
    assert tuple(ws.rglob("*")) == before == ()


@pytest.mark.parametrize(
    "control_input",
    [key for key, _relative_path in RECOVERY_CONTROL_INPUT_FILES],
    ids=lambda value: f"direct-symlink-{value}",
)
def test_recovery_rejects_direct_control_input_symlink_before_read(
    tmp_path: Path,
    control_input: str,
) -> None:
    ws = _workspace(tmp_path)
    _write_registry(ws)
    _write_bound_finalize_report(ws)
    control_paths = resolve_recovery_control_paths(ws)
    path = _control_path_mapping(control_paths)[control_input]
    external = tmp_path / f"external-{path.name}"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    before = _control_observations(control_paths, extra_paths=(external,))

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "control_context_invalid"
    assert payload["details"]["reason_code"] == "recovery_control_path_unsafe"
    assert payload["details"]["control_input"] == control_input
    assert _control_observations(control_paths, extra_paths=(external,)) == before


@pytest.mark.parametrize(
    "ancestor",
    ["output", "output/intermediate"],
    ids=lambda value: f"ancestor-{value.replace('/', '-')}",
)
def test_recovery_rejects_symlinked_control_ancestor_with_optional_report_absent(
    tmp_path: Path,
    ancestor: str,
) -> None:
    ws = _workspace(tmp_path)
    _write_registry(ws)
    control_paths = resolve_recovery_control_paths(ws)
    ancestor_path = ws / ancestor
    external_output = tmp_path / f"external-{ancestor.replace('/', '-')}"
    ancestor_path.rename(external_output)
    ancestor_path.symlink_to(external_output, target_is_directory=True)
    before = _control_observations(
        control_paths,
        extra_paths=tuple(path for path in external_output.rglob("*") if path.is_file()),
    )

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "control_context_invalid"
    assert payload["details"]["reason_code"] == "recovery_control_path_unsafe"
    assert payload["details"]["control_input"] == "runtime_manifest"
    assert _control_observations(
        control_paths,
        extra_paths=tuple(path for path in external_output.rglob("*") if path.is_file()),
    ) == before


def test_recovery_loader_rejects_path_set_from_another_workspace(
    tmp_path: Path,
) -> None:
    first = _workspace(tmp_path / "first")
    second = _workspace(tmp_path / "second")
    first_paths = resolve_recovery_control_paths(first)
    second_paths = resolve_recovery_control_paths(second)
    before = {
        "first": _control_observations(first_paths),
        "second": _control_observations(second_paths),
    }

    with pytest.raises(RuntimeStateError) as exc_info:
        load_recovery_context(
            workspace=second,
            repo_workdir=ROOT,
            control_paths=first_paths,
        )

    assert getattr(exc_info.value, "details")["reason_code"] == (
        "recovery_control_workspace_mismatch"
    )
    assert _control_observations(first_paths) == before["first"]
    assert _control_observations(second_paths) == before["second"]


@pytest.mark.parametrize(
    "path_case",
    ["wrong-sibling", "workspace-escape", "logical-identity-drift"],
    ids=lambda value: value,
)
def test_recovery_loader_rejects_noncanonical_supplied_path_set(
    tmp_path: Path,
    path_case: str,
) -> None:
    ws = _workspace(tmp_path)
    control_paths = resolve_recovery_control_paths(ws)
    forged_event_log = {
        "wrong-sibling": ws / "output/intermediate/not-the-event-log.jsonl",
        "workspace-escape": tmp_path / "external-event-log.jsonl",
        "logical-identity-drift": (
            ws / "output/intermediate/nested/../event_log.jsonl"
        ),
    }[path_case]
    forged = replace(
        control_paths,
        event_log=forged_event_log,
    )
    before = _control_observations(control_paths)

    with pytest.raises(RuntimeStateError) as exc_info:
        load_recovery_context(
            workspace=ws,
            repo_workdir=ROOT,
            control_paths=forged,
        )

    assert getattr(exc_info.value, "details")["reason_code"] == (
        "recovery_control_path_binding_invalid"
    )
    assert getattr(exc_info.value, "details")["control_input"] == "event_log"
    assert _control_observations(control_paths) == before


def test_recovery_loader_rejects_polymorphic_shadow_path_map(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path / "trusted")
    shadow_ws = _workspace(tmp_path / "shadow")
    trusted_paths = resolve_recovery_control_paths(ws)
    shadow_paths = resolve_recovery_control_paths(shadow_ws)

    class ShadowRecoveryControlPaths(RecoveryControlPaths):
        def as_mapping(self) -> dict[str, Path]:
            return _control_path_mapping(shadow_paths)

    supplied = ShadowRecoveryControlPaths(
        workspace=trusted_paths.workspace,
        **_control_path_mapping(trusted_paths),
    )
    before = {
        "trusted": _control_observations(trusted_paths),
        "shadow": _control_observations(shadow_paths),
    }

    with pytest.raises(RuntimeStateError) as exc_info:
        load_recovery_context(
            workspace=ws,
            repo_workdir=ROOT,
            control_paths=supplied,
        )

    assert exc_info.value.details["reason_code"] == "recovery_control_paths_invalid"
    assert _control_observations(trusted_paths) == before["trusted"]
    assert _control_observations(shadow_paths) == before["shadow"]


def test_recovery_loader_rejects_polymorphic_path_field_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_ws = _workspace(tmp_path / "trusted", current_stage="editor")
    shadow_ws = _workspace(tmp_path / "shadow", current_stage="auditor")
    trusted_paths = resolve_recovery_control_paths(trusted_ws)
    shadow_paths = resolve_recovery_control_paths(shadow_ws)
    concrete_path_type = type(trusted_paths.workflow_state)

    class ShadowPath(concrete_path_type):
        def __new__(cls, actual_path: Path, claimed_path: Path):
            instance = super().__new__(cls, actual_path)
            instance._claimed_path = claimed_path
            return instance

        def __eq__(self, other: object) -> bool:
            return self._claimed_path == other

        def relative_to(self, *other: object, **kwargs: object) -> Path:
            return self._claimed_path.relative_to(*other, **kwargs)

        def resolve(self, strict: bool = False) -> Path:
            return self._claimed_path.resolve(strict=strict)

    supplied = replace(
        trusted_paths,
        workflow_state=ShadowPath(
            shadow_paths.workflow_state,
            trusted_paths.workflow_state,
        ),
    )
    before = {
        "trusted": _control_observations(trusted_paths),
        "shadow": _control_observations(shadow_paths),
    }
    loader_calls: list[tuple[str, Path]] = []

    def forbidden_object_loader(path: str | Path, **_kwargs):
        loader_calls.append(("object", Path(path)))
        raise AssertionError("control object loader must not be called")

    def forbidden_event_loader(path: str | Path):
        loader_calls.append(("event", Path(path)))
        raise AssertionError("event loader must not be called")

    monkeypatch.setattr(
        recovery_state_module,
        "load_control_object",
        forbidden_object_loader,
    )
    monkeypatch.setattr(
        recovery_state_module,
        "read_event_log_records_strict",
        forbidden_event_loader,
    )

    with pytest.raises(RuntimeStateError) as exc_info:
        load_recovery_context(
            workspace=trusted_ws,
            repo_workdir=ROOT,
            control_paths=supplied,
        )

    assert exc_info.value.details["reason_code"] == (
        "recovery_control_path_binding_invalid"
    )
    assert exc_info.value.details["control_input"] == "workflow_state"
    assert loader_calls == []
    assert _control_observations(trusted_paths) == before["trusted"]
    assert _control_observations(shadow_paths) == before["shadow"]


def test_recovery_loader_repreflights_resolved_paths_before_read(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, current_stage="finalize")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
    _bind_recovery_pointer(ws)
    _write_bound_finalize_report(ws)
    control_paths = resolve_recovery_control_paths(ws)
    report_path = control_paths.finalize_report
    external = tmp_path / "external-finalize-report.json"
    external.write_bytes(report_path.read_bytes())
    report_path.unlink()
    report_path.symlink_to(external)
    before = _control_observations(control_paths, extra_paths=(external,))

    with pytest.raises(RuntimeStateError) as exc_info:
        load_recovery_context(
            workspace=ws,
            repo_workdir=ROOT,
            control_paths=control_paths,
        )

    assert getattr(exc_info.value, "details")["reason_code"] == (
        "recovery_control_path_unsafe"
    )
    assert getattr(exc_info.value, "details")["control_input"] == "finalize_report"
    payload = _evaluate(ws)
    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "control_context_invalid"
    assert payload["details"]["control_input"] == "finalize_report"
    assert _control_observations(control_paths, extra_paths=(external,)) == before


def test_recovery_loader_reads_only_the_preflighted_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _workspace(tmp_path)
    _write_registry(ws)
    control_paths = resolve_recovery_control_paths(ws)
    object_loader = recovery_state_module.load_control_object
    event_loader = recovery_state_module.read_event_log_records_strict
    read_paths: list[Path] = []

    def tracked_object_loader(path: str | Path, **kwargs):
        read_paths.append(Path(path))
        return object_loader(path, **kwargs)

    def tracked_event_loader(path: str | Path):
        read_paths.append(Path(path))
        return event_loader(Path(path))

    monkeypatch.setattr(
        recovery_state_module,
        "load_control_object",
        tracked_object_loader,
    )
    monkeypatch.setattr(
        recovery_state_module,
        "read_event_log_records_strict",
        tracked_event_loader,
    )

    context = load_recovery_context(
        workspace=ws,
        repo_workdir=ROOT,
        control_paths=control_paths,
    )

    assert context.runtime_manifest["run_id"] == RUN_ID
    assert read_paths == [
        control_paths.runtime_manifest,
        control_paths.workflow_state,
        control_paths.artifact_registry,
        control_paths.finalize_report,
        control_paths.event_log,
    ]
    assert set(read_paths) == set(_control_path_mapping(control_paths).values())


def test_recovery_state_clean_run_is_not_applicable(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert not (ws / "output/intermediate/finalize_report.json").exists()

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_NOT_APPLICABLE
    assert payload["reference_eligible"] is True
    assert payload["recovery_blocks_delivery"] is False


def test_recovery_state_current_contamination_awaits_recovery(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _mark_contaminated(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_AWAITING
    assert payload["contamination_event_id"] == CONTAMINATION_ID
    assert payload["recommended_recovery_action"] == "request_recovery_decision"


def test_recontamination_does_not_reuse_previous_cycle_owner_revision(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    second_contamination = _event(
        "run_integrity_contaminated",
        "event-contamination-002",
        stage_id="auditor",
    )
    _write_events(
        ws,
        [
            contamination,
            _repair_started_for(recovery),
            recovery,
            second_contamination,
        ],
    )
    _bind_recovery_pointer(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_AWAITING
    assert payload["contamination_event_id"] == "event-contamination-002"
    assert payload["owner_revision"]["status"] == "none"
    assert payload["stale_artifact_baselines"] == {}
    assert recovery_stale_artifact_baselines(payload) == {}


def test_recontamination_active_repair_does_not_reuse_previous_cycle_revision(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, current_stage="editor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    second_contamination_id = "event-contamination-002"
    second_repair_started_id = "event-repair-started-002"
    second_repair_start_transaction_id = "repair-start-002"
    second_contamination = _event(
        "run_integrity_contaminated",
        second_contamination_id,
        stage_id="auditor",
    )
    second_repair_started = _event(
        "repair_started",
        second_repair_started_id,
        stage_id="editor",
        metadata={
            "transaction_id": second_repair_start_transaction_id,
            "contamination_event_id": second_contamination_id,
            "repair_owner": "editor",
        },
    )
    _write_events(
        ws,
        [
            contamination,
            _repair_started_for(recovery),
            recovery,
            second_contamination,
            second_repair_started,
        ],
    )
    _bind_recovery_pointer(ws)
    workflow = _read_workflow(ws)
    workflow["active_repair"] = {
        "schema_version": "mabw.active_repair.v2",
        "run_id": RUN_ID,
        "repair_start_transaction_id": second_repair_start_transaction_id,
        "repair_started_event_id": second_repair_started_id,
        "contamination_event_id": second_contamination_id,
        "repair_owner": "editor",
        "must_rerun_from": "auditor",
        "source": {"artifact_id": "audited_brief"},
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_IN_PROGRESS
    assert payload["owner_revision"]["status"] == "none"
    assert payload["stale_artifact_baselines"] == {}
    assert recovery_stale_artifact_baselines(payload) == {}


def test_active_repair_does_not_mask_revision_bound_to_previous_cycle(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, current_stage="editor")
    first_contamination = _mark_contaminated(ws)
    stale_recovery = _recovery_event()
    second_contamination_id = "event-contamination-002"
    second_repair_started_id = "event-repair-started-002"
    second_repair_start_transaction_id = "repair-start-002"
    second_contamination = _event(
        "run_integrity_contaminated",
        second_contamination_id,
        stage_id="auditor",
    )
    second_repair_started = _event(
        "repair_started",
        second_repair_started_id,
        stage_id="editor",
        metadata={
            "transaction_id": second_repair_start_transaction_id,
            "contamination_event_id": second_contamination_id,
            "repair_owner": "editor",
        },
    )
    _write_events(
        ws,
        [
            first_contamination,
            _repair_started_for(stale_recovery),
            second_contamination,
            stale_recovery,
            second_repair_started,
        ],
    )
    _bind_recovery_pointer(ws)
    workflow = _read_workflow(ws)
    workflow["active_repair"] = {
        "schema_version": "mabw.active_repair.v2",
        "run_id": RUN_ID,
        "repair_start_transaction_id": second_repair_start_transaction_id,
        "repair_started_event_id": second_repair_started_id,
        "contamination_event_id": second_contamination_id,
        "repair_owner": "editor",
        "must_rerun_from": "auditor",
        "source": {"artifact_id": "audited_brief"},
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "recovery_event_binding_invalid"


def test_recovery_state_requires_bound_active_repair(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="editor")
    contamination = _mark_contaminated(ws)
    started = _event(
        "repair_started",
        REPAIR_STARTED_EVENT_ID,
        stage_id="editor",
        metadata={
            "transaction_id": REPAIR_START_ID,
            "contamination_event_id": CONTAMINATION_ID,
            "repair_owner": "editor",
        },
    )
    _write_events(ws, [contamination, started])
    workflow = _read_workflow(ws)
    workflow["active_repair"] = {
        "schema_version": "mabw.active_repair.v2",
        "run_id": RUN_ID,
        "repair_start_transaction_id": REPAIR_START_ID,
        "repair_started_event_id": REPAIR_STARTED_EVENT_ID,
        "contamination_event_id": CONTAMINATION_ID,
        "repair_owner": "editor",
        "must_rerun_from": "auditor",
        "source": {"artifact_id": "audited_brief"},
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)
    assert payload["status"] == RECOVERY_IN_PROGRESS

    workflow["active_repair"].pop("contamination_event_id")
    _write_workflow(ws, workflow)
    invalid = _evaluate(ws)
    assert invalid["status"] == RECOVERY_INVALID
    assert invalid["reason_code"] == "active_repair_binding_invalid"


def test_recovery_state_tracks_downstream_rerun_from_event(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
    _bind_recovery_pointer(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_RERUN_PENDING
    assert payload["rerun_start_stage"] == "auditor"
    assert payload["stale_artifact_baselines"]["audit_report"]["sha256"] == "old-audit"


def test_recovery_state_requires_current_finalize_render_and_completion(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="finalize")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
    _bind_recovery_pointer(ws)

    missing = _evaluate(ws)
    assert missing["status"] == RECOVERY_FINALIZE_RENDER_REQUIRED

    _write_bound_finalize_report(ws)
    current = _evaluate(ws)
    assert current["status"] == RECOVERY_FINALIZE_COMPLETION_PENDING
    assert current["render_transaction_id"] == "render-001"


def test_recovery_state_validates_terminal_finalize_binding(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage=None)
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    completion = _event(
        "decision_recorded",
        "event-finalize-complete-001",
        stage_id="finalize",
        decision="finalize",
        metadata={
            "transaction_id": "finalize-complete-001",
            "render_transaction_id": "render-001",
            "recovery_transaction_id": RECOVERY_ID,
            "contamination_event_id": CONTAMINATION_ID,
        },
    )
    _write_events(
        ws,
        [contamination, _repair_started_for(recovery), recovery, completion],
    )
    _bind_recovery_pointer(ws)
    workflow = _read_workflow(ws)
    workflow["last_completion_transaction"] = {
        "transaction_id": "finalize-complete-001",
        "run_id": RUN_ID,
        "stage_id": "finalize",
        "decision": "finalize",
        "render_transaction_id": "render-001",
        "recovery_transaction_id": RECOVERY_ID,
        "contamination_event_id": CONTAMINATION_ID,
    }
    _write_workflow(ws, workflow)
    _write_bound_finalize_report(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_COMPLETED_NON_REFERENCE
    assert payload["recovery_blocks_delivery"] is False
    assert payload["reference_eligible"] is False


def test_recovery_state_rejects_duplicate_event_ids(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    contamination = _mark_contaminated(ws)
    duplicate = dict(contamination)
    duplicate["event_type"] = "repair_completed"
    _write_events(ws, [contamination, duplicate])

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "control_context_invalid"
    assert "Duplicate event_id" in payload["reason"]


def test_recovery_state_rejects_unbound_legacy_repaired_status(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _read_workflow(ws)
    workflow["run_integrity"] = {
        "status": "contaminated_repaired",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [],
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "legacy_recovery_unbound"


@pytest.mark.parametrize(
    "case_id",
    [
        "persisted-clean-contamination-wins",
        "empty-event-id-invalid",
        "recovery-before-latest-contamination-ignored",
        "recovery-bound-to-older-contamination-invalid",
        "old-run-events-ignored",
        "legacy-unversioned-clean-repair-ignored",
        "legacy-unversioned-stale-metadata-migrated",
        "versioned-owner-revision-missing-binding-invalid",
        "second-recovery-latest-wins",
        "new-contamination-opens-cycle",
    ],
    ids=lambda value: value,
)
def test_recovery_event_timeline_matrix(tmp_path: Path, case_id: str) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    _write_registry(ws)
    workflow = _read_workflow(ws)
    expected_status = RECOVERY_AWAITING
    expected_reason = ""
    expected_contamination = CONTAMINATION_ID
    expected_recovery = ""

    if case_id == "persisted-clean-contamination-wins":
        events = [_event("run_integrity_contaminated", CONTAMINATION_ID)]
    elif case_id == "empty-event-id-invalid":
        events = [_event("run_integrity_contaminated", "")]
        expected_status = RECOVERY_INVALID
        expected_reason = "control_context_invalid"
        expected_contamination = ""
    elif case_id == "old-run-events-ignored":
        old = _event("run_integrity_contaminated", "old-contamination")
        old["run_id"] = "old-run"
        old_recovery = _recovery_event()
        old_recovery["event_id"] = "old-recovery"
        old_recovery["run_id"] = "old-run"
        events = [old, old_recovery]
        expected_status = RECOVERY_NOT_APPLICABLE
        expected_contamination = ""
    elif case_id == "legacy-unversioned-clean-repair-ignored":
        events = [
            _event(
                "repair_completed",
                "legacy-repair-event",
                stage_id="editor",
                decision="repair_complete",
                metadata={
                    "transaction_id": "legacy-repair-transaction",
                    "repair_owner": "editor",
                    "must_rerun_from": "auditor",
                    "next_stage": "auditor",
                    "allowed_artifacts": ["output/intermediate/audited_brief.md"],
                },
            )
        ]
        expected_status = RECOVERY_NOT_APPLICABLE
        expected_contamination = ""
    elif case_id == "legacy-unversioned-stale-metadata-migrated":
        legacy_recovery = _event(
            "repair_completed",
            "legacy-repair-event",
            stage_id="editor",
            decision="repair_complete",
            metadata={
                "transaction_id": "legacy-repair-transaction",
                "repair_owner": "editor",
                "must_rerun_from": "auditor",
                "next_stage": "auditor",
                "source": {
                    "kind": "quality_gate_report",
                    "finding_id": "LEGACY_REPAIR_001",
                    "artifact_id": "audited_brief",
                },
            },
        )
        events = [_legacy_repair_started_for(legacy_recovery), legacy_recovery]
        workflow["last_repair_transaction"] = {
            "transaction_id": "legacy-repair-transaction",
            "stage_id": "editor",
            "decision": "repair_complete",
        }
        workflow["stage_statuses"] = {
            "auditor": {
                "status": "ready",
                "metadata": {
                    "stale_after_repair": True,
                    "repair_transaction_id": "legacy-repair-transaction",
                    "repair_owner": "editor",
                    "stale_artifact_baselines": {
                        "audit_report": {"sha256": "legacy-audit-sha"},
                    },
                },
            }
        }
        expected_status = RECOVERY_NOT_APPLICABLE
        expected_contamination = ""
    elif case_id == "versioned-owner-revision-missing-binding-invalid":
        events = [
            _event(
                "repair_completed",
                "invalid-current-repair-event",
                stage_id="editor",
                decision="repair_complete",
                metadata={
                    "owner_revision_schema_version": OWNER_REVISION_SCHEMA,
                    "transaction_id": "current-repair-transaction",
                },
            )
        ]
        expected_status = RECOVERY_INVALID
        expected_reason = "owner_revision_binding_invalid"
        expected_contamination = ""
    else:
        workflow["run_integrity"] = {
            "status": "contaminated",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [{"reason_code": "test_contamination"}],
        }
        first = _event("run_integrity_contaminated", "contamination-old")
        first_recovery = _recovery_event()
        first_recovery["metadata"]["contamination_event_id"] = "contamination-old"
        first_started = _repair_started_for(first_recovery)
        second = _event("run_integrity_contaminated", CONTAMINATION_ID)
        if case_id == "recovery-before-latest-contamination-ignored":
            events = [first, first_started, first_recovery, second]
        elif case_id == "recovery-bound-to-older-contamination-invalid":
            events = [first, second, first_started, first_recovery]
            expected_status = RECOVERY_INVALID
            expected_reason = "recovery_event_binding_invalid"
            expected_contamination = ""
        elif case_id == "new-contamination-opens-cycle":
            events = [first, first_started, first_recovery, second]
        else:
            first["event_id"] = CONTAMINATION_ID
            first_recovery["metadata"]["contamination_event_id"] = CONTAMINATION_ID
            first_started = _repair_started_for(first_recovery)
            second_recovery = _recovery_event()
            second_recovery["event_id"] = "event-repair-completed-002"
            second_recovery["metadata"]["transaction_id"] = "repair-complete-002"
            second_recovery["metadata"]["repair_start_transaction_id"] = (
                "repair-start-002"
            )
            second_recovery["metadata"]["repair_started_event_id"] = (
                "event-repair-started-002"
            )
            events = [
                first,
                first_started,
                first_recovery,
                _repair_started_for(second_recovery),
                second_recovery,
            ]
            workflow["last_repair_transaction"] = {
                "transaction_id": "repair-complete-002",
                "run_id": RUN_ID,
                "contamination_event_id": CONTAMINATION_ID,
                "owner_stage": "editor",
                "artifact_id": "audited_brief",
                "rerun_start_stage": "auditor",
            }
            expected_status = RECOVERY_RERUN_PENDING
            expected_recovery = "repair-complete-002"

    _write_workflow(ws, workflow)
    _write_events(ws, events)

    payload = _evaluate(ws)

    assert payload["status"] == expected_status
    if expected_reason:
        assert payload["reason_code"] == expected_reason
    if expected_contamination:
        assert payload["contamination_event_id"] == expected_contamination
    if expected_recovery:
        assert payload["recovery_transaction_id"] == expected_recovery
    if case_id == "legacy-unversioned-clean-repair-ignored":
        assert payload["owner_revision"]["status"] == "none"
    if case_id == "legacy-unversioned-stale-metadata-migrated":
        assert payload["owner_revision"]["status"] == "legacy_migrated"
        assert payload["owner_revision"]["stale_artifact_baselines"] == {
            "audit_report": {"sha256": "legacy-audit-sha"},
        }
        assert recovery_stale_artifact_baselines(payload) == {
            "audit_report": {"sha256": "legacy-audit-sha"},
        }


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_event_type"),
    [
        ("clean-repair", RECOVERY_NOT_APPLICABLE, "repair_completed"),
        ("contaminated-repair", RECOVERY_RERUN_PENDING, "repair_completed"),
        ("supersede", RECOVERY_RERUN_PENDING, "repair_stage_superseded"),
    ],
    ids=["clean-repair", "contaminated-repair", "contaminated-supersede"],
)
def test_recovery_legacy_transaction_fixture_matrix(
    tmp_path: Path,
    scenario: str,
    expected_status: str,
    expected_event_type: str,
) -> None:
    ws = tmp_path / scenario
    shutil.copytree(LEGACY_FIXTURE_ROOT / scenario, ws)

    payload = _evaluate(ws)

    assert payload["status"] == expected_status
    assert payload["owner_revision"]["status"] == "legacy_migrated"
    assert payload["owner_revision"]["event_type"] == expected_event_type
    if expected_status == RECOVERY_RERUN_PENDING:
        assert payload["recovery_event_type"] == expected_event_type
        assert payload["rerun_start_stage"] == "auditor"
        assert payload["reference_eligible"] is False


@pytest.mark.parametrize(
    "case_id",
    [
        "supersede-pointer-missing",
        "supersede-rerun-proof-missing",
        "repair-start-lineage-ambiguous",
    ],
    ids=lambda value: value,
)
def test_recovery_legacy_migration_rejects_ambiguous_evidence(
    tmp_path: Path,
    case_id: str,
) -> None:
    scenario = "contaminated-repair" if case_id.startswith("repair-") else "supersede"
    ws = tmp_path / scenario
    shutil.copytree(LEGACY_FIXTURE_ROOT / scenario, ws)
    intermediate = ws / "output/intermediate"

    if case_id == "supersede-pointer-missing":
        workflow = json.loads(
            (intermediate / "workflow_state.json").read_text(encoding="utf-8")
        )
        workflow.pop("last_repair_transaction")
        _write_json(intermediate / "workflow_state.json", workflow)
    elif case_id == "supersede-rerun-proof-missing":
        workflow = json.loads(
            (intermediate / "workflow_state.json").read_text(encoding="utf-8")
        )
        for stage_id in ("auditor", "finalize"):
            workflow["stage_statuses"][stage_id].pop("metadata", None)
        _write_json(intermediate / "workflow_state.json", workflow)
    else:
        event_path = intermediate / "event_log.jsonl"
        events = [
            json.loads(line)
            for line in event_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        started_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "repair_started"
        )
        duplicate = dict(events[started_index])
        duplicate["event_id"] = "legacy-duplicate-repair-start"
        events.insert(started_index + 1, duplicate)
        _write_events(ws, events)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "owner_revision_binding_invalid"


def test_recovery_legacy_supersede_migrates_after_rerun_metadata_clears(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "supersede-advanced"
    shutil.copytree(LEGACY_FIXTURE_ROOT / "supersede", ws)
    workflow_path = ws / "output/intermediate/workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["current_stage"] = "finalize"
    workflow["stage_statuses"]["auditor"] = {
        "status": "complete",
        "reason": "legacy auditor rerun completed",
        "updated_at": "2026-07-10T13:30:00+00:00",
    }
    workflow["stage_statuses"]["finalize"] = {
        "status": "ready",
        "reason": "ready after legacy auditor rerun",
        "updated_at": "2026-07-10T13:30:00+00:00",
    }
    _write_json(workflow_path, workflow)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_FINALIZE_RENDER_REQUIRED
    assert payload["owner_revision"]["status"] == "legacy_migrated"
    assert payload["stale_artifact_baselines"] == {}


def test_recovery_ignores_older_legacy_revision_when_current_revision_is_bound(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    legacy = _event(
        "repair_stage_superseded",
        "legacy-supersede-event",
        stage_id="editor",
        metadata={
            "transaction_id": "legacy-supersede-transaction",
            "stage_id": "editor",
            "artifact_id": "audited_brief",
            "artifact_path": "output/intermediate/audited_brief.md",
            "old_registered_sha256": "legacy-old-sha",
            "current_bytes_sha256": "legacy-new-sha",
            "next_stage": "auditor",
            "reference_eligible": False,
            "run_integrity_status": "contaminated",
            "contamination_event_count": 1,
        },
    )
    current = _recovery_event()
    _write_events(
        ws,
        [contamination, legacy, _repair_started_for(current), current],
    )
    _bind_recovery_pointer(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_RERUN_PENDING
    assert payload["recovery_transaction_id"] == RECOVERY_ID
    assert payload["owner_revision"]["status"] == "present"


@pytest.mark.parametrize(
    "case_id",
    [
        "workflow-run-id-mismatch",
        "artifact-registry-run-id-mismatch",
        "missing-runtime-manifest",
        "malformed-workflow-control",
        "repair-completed-without-start-event",
        "repair-start-after-completion",
        "repair-start-run-mismatch",
        "repair-start-contamination-mismatch",
        "repair-start-owner-mismatch",
        "supersede-direct-transaction-mismatch",
        "recovery-transaction-id-missing",
        "repair-pointer-missing",
        "repair-pointer-mismatch",
        "later-orphan-recovery-event",
        "contamination-binding-missing",
        "rerun-stage-noncanonical",
        "current-stage-precedes-rerun",
    ],
    ids=lambda value: value,
)
def test_recovery_control_and_transaction_binding_matrix(
    tmp_path: Path,
    case_id: str,
) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
    _bind_recovery_pointer(ws)
    expected_reason = ""

    if case_id == "workflow-run-id-mismatch":
        workflow = _read_workflow(ws)
        workflow["run_id"] = "wrong-run"
        _write_workflow(ws, workflow)
        expected_reason = "workflow_run_id_mismatch"
    elif case_id == "artifact-registry-run-id-mismatch":
        registry_path = ws / "output/intermediate/artifact_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["run_id"] = "wrong-run"
        _write_json(registry_path, registry)
        expected_reason = "artifact_registry_run_id_mismatch"
    elif case_id == "missing-runtime-manifest":
        (ws / "output/intermediate/runtime_manifest.json").unlink()
        expected_reason = "control_context_invalid"
    elif case_id == "malformed-workflow-control":
        (ws / "output/intermediate/workflow_state.json").write_text("{broken", encoding="utf-8")
        expected_reason = "control_context_invalid"
    elif case_id == "repair-completed-without-start-event":
        _write_events(ws, [contamination, recovery])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "repair-start-after-completion":
        _write_events(ws, [contamination, recovery, _repair_started_for(recovery)])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "repair-start-run-mismatch":
        started = _repair_started_for(recovery)
        started["run_id"] = "wrong-run"
        _write_events(ws, [contamination, started, recovery])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "repair-start-contamination-mismatch":
        started = _repair_started_for(recovery)
        started["metadata"]["contamination_event_id"] = "wrong-contamination"
        _write_events(ws, [contamination, started, recovery])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "repair-start-owner-mismatch":
        started = _repair_started_for(recovery)
        started["stage_id"] = "analyst"
        started["metadata"]["repair_owner"] = "analyst"
        _write_events(ws, [contamination, started, recovery])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "supersede-direct-transaction-mismatch":
        supersede = _event(
            "repair_stage_superseded",
            "event-repair-stage-superseded-001",
            stage_id="editor",
            metadata={
                "owner_revision_schema_version": OWNER_REVISION_SCHEMA,
                "transaction_id": "supersede-001",
                "repair_start_transaction_id": "different-transaction",
                "contamination_event_id": CONTAMINATION_ID,
                "owner_stage": "editor",
                "artifact_id": "audited_brief",
                "rerun_start_stage": "auditor",
                "reference_eligible": False,
                "stale_artifact_baselines": {
                    "audit_report": {"sha256": "old-audit"},
                },
            },
        )
        _write_events(ws, [contamination, supersede])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "recovery-transaction-id-missing":
        recovery["metadata"]["transaction_id"] = ""
        _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
        expected_reason = "owner_revision_binding_invalid"
    elif case_id == "repair-pointer-missing":
        workflow = _read_workflow(ws)
        workflow.pop("last_repair_transaction")
        _write_workflow(ws, workflow)
        expected_reason = "repair_pointer_invalid"
    elif case_id == "repair-pointer-mismatch":
        workflow = _read_workflow(ws)
        workflow["last_repair_transaction"]["transaction_id"] = "wrong-repair"
        _write_workflow(ws, workflow)
        expected_reason = "repair_pointer_invalid"
    elif case_id == "later-orphan-recovery-event":
        orphan = _recovery_event()
        orphan["event_id"] = "event-orphan-recovery"
        orphan["metadata"]["transaction_id"] = "orphan-recovery"
        orphan["metadata"]["repair_start_transaction_id"] = "orphan-start"
        orphan["metadata"]["repair_started_event_id"] = "event-orphan-start"
        _write_events(
            ws,
            [
                contamination,
                _repair_started_for(recovery),
                recovery,
                _repair_started_for(orphan),
                orphan,
            ],
        )
        expected_reason = "repair_pointer_invalid"
    elif case_id == "contamination-binding-missing":
        recovery["metadata"]["contamination_event_id"] = ""
        _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
        expected_reason = "recovery_event_binding_invalid"
    elif case_id == "rerun-stage-noncanonical":
        recovery["metadata"]["rerun_start_stage"] = "unknown-stage"
        _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
        expected_reason = "owner_revision_binding_invalid"
    else:
        workflow = _read_workflow(ws)
        workflow["current_stage"] = "editor"
        _write_workflow(ws, workflow)
        expected_reason = "current_stage_precedes_recovery_rerun"

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == expected_reason


def test_recovery_rejects_repair_completed_without_bound_start_event(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, recovery])
    _bind_recovery_pointer(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "owner_revision_binding_invalid"
    assert "Bound repair_started event is missing" in payload["reason"]


def test_recovery_rejects_cross_run_artifact_registry(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_registry(ws)
    registry_path = ws / "output/intermediate/artifact_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["run_id"] = "old-run"
    _write_json(registry_path, registry)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "artifact_registry_run_id_mismatch"
    assert "artifact_registry.run_id" in payload["reason"]


def _terminal_recovery_workspace(tmp_path: Path) -> tuple[Path, dict, dict]:
    ws = _workspace(tmp_path, current_stage=None)
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    completion = _event(
        "decision_recorded",
        "event-finalize-complete-001",
        stage_id="finalize",
        decision="finalize",
        metadata={
            "transaction_id": "finalize-complete-001",
            "render_transaction_id": "render-001",
            "recovery_transaction_id": RECOVERY_ID,
            "contamination_event_id": CONTAMINATION_ID,
        },
    )
    _write_events(
        ws,
        [contamination, _repair_started_for(recovery), recovery, completion],
    )
    _bind_recovery_pointer(ws)
    workflow = _read_workflow(ws)
    workflow["last_completion_transaction"] = {
        "transaction_id": "finalize-complete-001",
        "run_id": RUN_ID,
        "stage_id": "finalize",
        "decision": "finalize",
        "render_transaction_id": "render-001",
        "recovery_transaction_id": RECOVERY_ID,
        "contamination_event_id": CONTAMINATION_ID,
    }
    _write_workflow(ws, workflow)
    _write_bound_finalize_report(ws)
    return ws, completion, recovery


@pytest.mark.parametrize(
    "case_id",
    [
        "old-pass-report-unbound",
        "old-failed-report-unbound",
        "current-bound-report-failed",
        "completion-transaction-id-empty",
        "finalize-event-precedes-recovery",
        "finalize-bindings-disagree",
        "terminal-decision-event-missing",
    ],
    ids=lambda value: value,
)
def test_recovery_finalize_binding_matrix(tmp_path: Path, case_id: str) -> None:
    if case_id in {
        "old-pass-report-unbound",
        "old-failed-report-unbound",
        "current-bound-report-failed",
    }:
        ws = _workspace(tmp_path, current_stage="finalize")
        contamination = _mark_contaminated(ws)
        recovery = _recovery_event()
        _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
        _bind_recovery_pointer(ws)
        if case_id == "current-bound-report-failed":
            _write_bound_finalize_report(ws)
            report = json.loads(
                (ws / "output/intermediate/finalize_report.json").read_text(encoding="utf-8")
            )
            report["status"] = "fail"
            _write_json(ws / "output/intermediate/finalize_report.json", report)
            expected_reason = "finalize_report_failed"
        else:
            _write_json(
                ws / "output/intermediate/finalize_report.json",
                {
                    "status": "pass" if case_id == "old-pass-report-unbound" else "fail",
                    "finalize_transaction_id": "old-render",
                    "reader_clean": {"status": "pass"},
                    "delivery_promotion": "promoted",
                },
            )
            expected_reason = "finalize_report_recovery_unbound"
        expected_status = RECOVERY_FINALIZE_RENDER_REQUIRED
    else:
        ws, completion, recovery = _terminal_recovery_workspace(tmp_path)
        expected_status = RECOVERY_INVALID
        expected_reason = "finalize_completion_binding_invalid"
        if case_id == "completion-transaction-id-empty":
            workflow = _read_workflow(ws)
            workflow["last_completion_transaction"]["transaction_id"] = ""
            _write_workflow(ws, workflow)
        elif case_id == "finalize-event-precedes-recovery":
            contamination = _event("run_integrity_contaminated", CONTAMINATION_ID)
            _write_events(
                ws,
                [contamination, _repair_started_for(recovery), completion, recovery],
            )
        elif case_id == "finalize-bindings-disagree":
            workflow = _read_workflow(ws)
            workflow["last_completion_transaction"]["render_transaction_id"] = "wrong-render"
            _write_workflow(ws, workflow)
        else:
            contamination = _event("run_integrity_contaminated", CONTAMINATION_ID)
            _write_events(ws, [contamination, _repair_started_for(recovery), recovery])

    payload = _evaluate(ws)

    assert payload["status"] == expected_status
    assert payload["reason_code"] == expected_reason


@pytest.mark.parametrize(
    "case_id",
    [
        "terminal-contamination-requires-new-run",
        "nonterminal-recovery-blocks-valid-bundle",
        "delivery-success-invalidated-by-new-contamination",
        "registration-preserves-non-reference-posture",
    ],
    ids=lambda value: value,
)
def test_recovered_delivery_and_reference_matrix(tmp_path: Path, case_id: str) -> None:
    if case_id == "nonterminal-recovery-blocks-valid-bundle":
        ws = _workspace(tmp_path, current_stage="auditor")
        contamination = _mark_contaminated(ws)
        recovery = _recovery_event()
        _write_events(ws, [contamination, _repair_started_for(recovery), recovery])
        _bind_recovery_pointer(ws)
        state = _evaluate(ws)
        workflow = _read_workflow(ws)

        with pytest.raises(DeliverCommandError) as excinfo:
            _preflight_run_integrity_for_delivery(
                workflow["run_integrity"],
                recovery_state=state,
                target="local",
                channel="local",
            )

        assert excinfo.value.error_code == E_DELIVERY_RUN_INTEGRITY_BLOCKED
        assert state["status"] == RECOVERY_RERUN_PENDING
        return

    ws, _completion, _recovery = _terminal_recovery_workspace(tmp_path)
    terminal = _evaluate(ws)
    assert terminal["status"] == RECOVERY_COMPLETED_NON_REFERENCE

    if case_id == "registration-preserves-non-reference-posture":
        registered = _registered_run_integrity(
            {"run_integrity": _read_workflow(ws)["run_integrity"]},
            path="workflow_state.run_integrity",
        )
        assert registered["status"] == "contaminated"
        assert registered["reference_eligible"] is False
        return

    events = [
        json.loads(line)
        for line in (ws / "output/intermediate/event_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if case_id == "delivery-success-invalidated-by-new-contamination":
        events.append(
            _event(
                "delivery_succeeded",
                "event-delivery-succeeded-001",
                metadata={
                    "render_transaction_id": "render-001",
                    "recovery_transaction_id": RECOVERY_ID,
                    "contamination_event_id": CONTAMINATION_ID,
                },
            )
        )
    events.append(
        _event(
            "run_integrity_contaminated",
            "event-contamination-terminal-002",
            stage_id="finalize",
        )
    )
    _write_events(ws, events)

    current = _evaluate(ws)
    assert current["status"] == RECOVERY_AWAITING
    assert current["recommended_recovery_action"] == "start_new_run"
    assert current["recovery_blocks_delivery"] is True
    if case_id == "delivery-success-invalidated-by-new-contamination":
        projection = build_completion_projection(workspace=ws, repo_workdir=ROOT)
        assert projection["event_truth"]["delivery_succeeded"] is False
        assert projection["event_truth"]["delivery_outcome"] == "missing"
