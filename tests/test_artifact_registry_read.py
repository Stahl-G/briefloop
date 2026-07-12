from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

import multi_agent_brief.orchestrator.runtime_state.artifact_registry_read as artifact_registry_read
from multi_agent_brief.orchestrator.recovery_state import (
    OWNER_REVISION_SCHEMA,
    evaluate_recovery_state,
    resolve_recovery_control_paths,
)
from multi_agent_brief.orchestrator.run_integrity import (
    contaminate_run_integrity_with_event_flag,
)
from multi_agent_brief.orchestrator.runtime_state import (
    check_runtime_state,
    initialize_runtime_state,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import (
    CanonicalRegistryView,
    RegistryDegradation,
    RegistryNotMaterialized,
    RegistryReadVerdict,
    RegistrySnapshotDrift,
    interpret_artifact_registry,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import append_event
from tests.helpers import write_minimal_workspace


ROOT = Path(__file__).resolve().parent.parent
_RECOVERY_INPUT_KEYS = (
    "runtime_manifest",
    "workflow_state",
    "artifact_registry",
    "event_log",
    "finalize_report",
)
_REQUIRED_RECOVERY_KEYS = {"runtime_manifest", "workflow_state", "event_log"}
_PARTIAL_RECOVERY_INPUT_CASES = tuple(
    tuple(
        key
        for index, key in enumerate(_RECOVERY_INPUT_KEYS)
        if mask & (1 << index)
    )
    for mask in range(1, 1 << len(_RECOVERY_INPUT_KEYS))
    if not _REQUIRED_RECOVERY_KEYS.issubset(
        {
            key
            for index, key in enumerate(_RECOVERY_INPUT_KEYS)
            if mask & (1 << index)
        }
    )
)


def _workspace(
    tmp_path: Path,
    *,
    name: str = "ws",
    repo_workdir: Path = ROOT,
    materialize: bool = True,
) -> Path:
    ws = write_minimal_workspace(
        tmp_path / name,
        project_name="Registry trusted-read test",
        user_text="# Registry trusted read\n",
    )
    initialize_runtime_state(
        workspace=ws,
        runtime="operator",
        repo_workdir=repo_workdir,
        actor="cli",
    )
    if materialize:
        check_runtime_state(
            workspace=ws,
            repo_workdir=repo_workdir,
            actor="cli",
        )
    return ws


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _workspace_snapshot(workspace: Path) -> dict[str, tuple[Any, ...]]:
    candidates = {workspace, *workspace.rglob("*")}
    candidates.update(runtime_state_paths(workspace).values())
    snapshot: dict[str, tuple[Any, ...]] = {}
    for path in sorted(candidates, key=lambda item: item.as_posix()):
        relative = path.relative_to(workspace).as_posix() or "."
        if path.is_symlink():
            stat = path.lstat()
            snapshot[relative] = ("symlink", os.readlink(path), stat.st_mtime_ns)
        elif not path.exists():
            snapshot[relative] = ("missing",)
        elif path.is_file():
            stat = path.stat()
            snapshot[relative] = ("file", path.read_bytes(), stat.st_mtime_ns)
        else:
            snapshot[relative] = ("directory", path.stat().st_mtime_ns)
    return snapshot


def _assert_read_only(
    workspace: Path,
    before: dict[str, tuple[Any, ...]],
) -> None:
    assert _workspace_snapshot(workspace) == before


def _assert_negative(
    verdict: RegistryReadVerdict,
    *,
    verdict_type: type[RegistryNotMaterialized]
    | type[RegistryDegradation]
    | type[RegistrySnapshotDrift],
    reason_code: str,
) -> None:
    assert isinstance(verdict, verdict_type)
    assert verdict.reason_code == reason_code
    payload = asdict(verdict)
    serialized = json.dumps(payload, sort_keys=True)
    assert set(payload) == {"kind", "reason_code"}
    assert all(not isinstance(value, (dict, list, tuple)) for value in payload.values())
    for attribute in (
        "updated_at",
        "records",
        "resolved_paths",
        "artifact_count",
        "status_counts",
    ):
        assert not hasattr(verdict, attribute)
    for forged_value in ("999999", "forged-secret"):
        assert forged_value not in serialized


def _custom_repo(tmp_path: Path, *, artifact_path: str) -> Path:
    repo = tmp_path / "contract-repo"
    shutil.copytree(ROOT / "configs", repo / "configs")
    (repo / "src" / "multi_agent_brief").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'briefloop-registry-read-test'\nversion = '0'\n",
        encoding="utf-8",
    )
    contracts_path = repo / "configs" / "artifact_contracts.yaml"
    contracts = yaml.safe_load(contracts_path.read_text(encoding="utf-8"))
    artifact = next(
        item
        for item in contracts["artifacts"]
        if item["artifact_id"] == "audited_brief"
    )
    artifact["path"] = artifact_path
    contracts_path.write_text(
        yaml.safe_dump(contracts, sort_keys=False),
        encoding="utf-8",
    )
    return repo


def _install_bound_recovery_registry(
    workspace: Path,
    *,
    event_type: str,
) -> dict[str, Any]:
    paths = runtime_state_paths(workspace)
    registry = _read_json(paths["artifact_registry"])
    workflow = _read_json(paths["workflow_state"])
    run_id = str(registry["run_id"])
    baseline = registry["artifacts"]["candidate_claims"]
    assert baseline["status"] == "valid"
    contamination_id = f"contamination-{event_type}"
    transaction_id = f"transaction-{event_type}"
    repair_start_transaction_id = (
        transaction_id if event_type == "repair_stage_superseded" else "repair-start"
    )
    repair_started_event_id = f"repair-started-{event_type}"
    now = "2026-07-12T00:00:00+00:00"
    workflow, added = contaminate_run_integrity_with_event_flag(
        workflow,
        reason_code="registry_read_recovery_fixture",
        message="Exercise current recovery-bound Registry replay.",
        created_at=now,
        event_type="run_integrity_contaminated",
        stage_id="doctor",
        artifact_id="config",
    )
    assert added is True
    workflow["current_stage"] = "source-discovery"
    statuses = workflow["stage_statuses"]
    stage_ids = list(statuses)
    current_index = stage_ids.index("source-discovery")
    for index, stage_id in enumerate(stage_ids):
        statuses[stage_id] = {
            "status": (
                "complete"
                if index < current_index
                else "ready"
                if index == current_index
                else "pending"
            ),
            "reason": "Registry trusted-read recovery fixture.",
            "updated_at": now,
        }
    workflow["last_repair_transaction"] = {
        "transaction_id": transaction_id,
        "run_id": run_id,
        "contamination_event_id": contamination_id,
        "owner_stage": "doctor",
        "artifact_id": "config",
        "rerun_start_stage": "source-discovery",
    }
    _write_json(paths["workflow_state"], workflow)
    append_event(
        workspace=workspace,
        run_id=run_id,
        event_type="run_integrity_contaminated",
        event_id=contamination_id,
        actor="system",
        stage_id="doctor",
        artifact_id="config",
        reason="Registry read recovery fixture contamination.",
        metadata={"reason_code": "registry_read_recovery_fixture"},
    )
    if event_type == "repair_completed":
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type="repair_started",
            event_id=repair_started_event_id,
            actor="system",
            stage_id="doctor",
            reason="Registry read recovery fixture repair start.",
            metadata={
                "transaction_id": repair_start_transaction_id,
                "contamination_event_id": contamination_id,
                "repair_owner": "doctor",
            },
        )
    append_event(
        workspace=workspace,
        run_id=run_id,
        event_type=event_type,
        event_id=f"owner-revision-{event_type}",
        actor="system",
        stage_id="doctor",
        artifact_id="config",
        decision=(
            "repair_complete"
            if event_type == "repair_completed"
            else "supersede_stage_artifact"
        ),
        reason="Registry read recovery fixture owner revision.",
        metadata={
            "owner_revision_schema_version": OWNER_REVISION_SCHEMA,
            "transaction_id": transaction_id,
            "repair_start_transaction_id": repair_start_transaction_id,
            "repair_started_event_id": repair_started_event_id,
            "contamination_event_id": contamination_id,
            "owner_stage": "doctor",
            "artifact_id": "config",
            "rerun_start_stage": "source-discovery",
            "stale_artifact_baselines": {
                "candidate_claims": {
                    "path": baseline["path"],
                    "sha256": baseline["sha256"],
                }
            },
        },
    )
    recovery = evaluate_recovery_state(workspace=workspace, repo_workdir=ROOT)
    assert recovery["status"] == "downstream_rerun_pending"
    updated_registry = check_runtime_state(
        workspace=workspace,
        repo_workdir=ROOT,
        actor="cli",
    )["artifact_registry"]
    stale = updated_registry["artifacts"]["candidate_claims"]
    assert stale["status"] == "stale"
    assert stale["validation_result"] == (
        "stale_after_supersede"
        if event_type == "repair_stage_superseded"
        else "stale_after_repair"
    )
    return updated_registry


@pytest.mark.parametrize(
    "layout",
    ["no-output-tree", "empty-intermediate"],
)
def test_reg_read_01_no_runtime_state_is_not_materialized_and_zero_write(
    tmp_path: Path,
    layout: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    if layout == "empty-intermediate":
        (ws / "output/intermediate").mkdir(parents=True)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryNotMaterialized,
        reason_code="artifact_registry_not_materialized",
    )
    _assert_read_only(ws, before)


def test_reg_read_02_initialized_runtime_missing_registry_stays_not_materialized(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, materialize=False)
    paths = runtime_state_paths(ws)
    assert paths["runtime_manifest"].exists()
    assert paths["workflow_state"].exists()
    assert not paths["artifact_registry"].exists()
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryNotMaterialized,
        reason_code="artifact_registry_not_materialized",
    )
    assert not paths["artifact_registry"].exists()
    _assert_read_only(ws, before)


@pytest.mark.parametrize("materialize", [False, True])
def test_reg_read_prerequisite_load_and_recovery_interpretation_run_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    materialize: bool,
) -> None:
    ws = _workspace(tmp_path, materialize=materialize)
    before = _workspace_snapshot(ws)
    calls = {"load": 0, "interpret": 0}
    real_load = artifact_registry_read.load_recovery_context_verdict
    real_interpret = artifact_registry_read.interpret_recovery_state

    def counted_load(**kwargs: Any):
        calls["load"] += 1
        return real_load(**kwargs)

    def counted_interpret(context: Any):
        calls["interpret"] += 1
        return real_interpret(context)

    monkeypatch.setattr(
        artifact_registry_read,
        "load_recovery_context_verdict",
        counted_load,
    )
    monkeypatch.setattr(
        artifact_registry_read,
        "interpret_recovery_state",
        counted_interpret,
    )

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    expected_type = CanonicalRegistryView if materialize else RegistryNotMaterialized
    assert isinstance(verdict, expected_type)
    assert calls == {"load": 1, "interpret": 1}
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    "present_keys",
    _PARTIAL_RECOVERY_INPUT_CASES,
    ids=lambda keys: "REG-READ-R03-" + "-".join(keys),
)
def test_reg_read_r03_partial_recovery_inventory_degrades_without_values(
    tmp_path: Path,
    present_keys: tuple[str, ...],
) -> None:
    ws = _workspace(tmp_path)
    control_paths = resolve_recovery_control_paths(ws)
    finalize_path = control_paths.finalize_report
    finalize_path.write_text("{}\n", encoding="utf-8")
    payloads = {
        key: getattr(control_paths, key).read_bytes()
        for key in _RECOVERY_INPUT_KEYS
    }
    for key in _RECOVERY_INPUT_KEYS:
        getattr(control_paths, key).unlink()
    for key in present_keys:
        getattr(control_paths, key).write_bytes(payloads[key])
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_recovery_context_invalid",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    ("case_id", "reason_code"),
    [
        ("workflow_status", "artifact_registry_workflow_stage_status_invalid"),
        ("current_stage", "artifact_registry_workflow_stage_status_invalid"),
        ("manifest_run_id", "artifact_registry_manifest_run_id_invalid"),
        ("workflow_run_id", "artifact_registry_workflow_run_id_invalid"),
        ("run_id_mismatch", "artifact_registry_workflow_run_id_mismatch"),
        ("manifest_contract", "artifact_registry_manifest_contract_mismatch"),
        ("manifest_type_alias", "artifact_registry_manifest_contract_mismatch"),
        ("finalize_present", "artifact_registry_recovery_context_invalid"),
    ],
    ids=lambda value: str(value),
)
def test_reg_read_missing_registry_requires_validated_control_prerequisites(
    tmp_path: Path,
    case_id: str,
    reason_code: str,
) -> None:
    ws = _workspace(tmp_path, materialize=False)
    paths = runtime_state_paths(ws)
    manifest = _read_json(paths["runtime_manifest"])
    workflow = _read_json(paths["workflow_state"])
    if case_id == "workflow_status":
        workflow["stage_statuses"]["doctor"]["status"] = "banana"
        _write_json(paths["workflow_state"], workflow)
    elif case_id == "current_stage":
        workflow["current_stage"] = "not-a-stage"
        _write_json(paths["workflow_state"], workflow)
    elif case_id == "manifest_run_id":
        manifest["run_id"] = "../invalid-run"
        _write_json(paths["runtime_manifest"], manifest)
    elif case_id == "workflow_run_id":
        workflow["run_id"] = "../invalid-run"
        _write_json(paths["workflow_state"], workflow)
    elif case_id == "run_id_mismatch":
        workflow["run_id"] = "another-valid-run"
        _write_json(paths["workflow_state"], workflow)
    elif case_id == "manifest_contract":
        manifest["expected_artifacts"][0]["path"] = "forged/path.json"
        _write_json(paths["runtime_manifest"], manifest)
    elif case_id == "manifest_type_alias":
        required = manifest["expected_artifacts"][0]["required"]
        assert isinstance(required, bool)
        manifest["expected_artifacts"][0]["required"] = int(required)
        _write_json(paths["runtime_manifest"], manifest)
    elif case_id == "finalize_present":
        report_path = ws / "output/intermediate/finalize_report.json"
        _write_json(report_path, {})
    else:  # pragma: no cover - parameter contract
        raise AssertionError(case_id)
    assert not paths["artifact_registry"].exists()
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code=reason_code,
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize("materialize", [False, True])
@pytest.mark.parametrize("mutation", ["missing_stage", "extra_stage"])
def test_reg_read_workflow_stage_universe_precedes_registry_presence(
    tmp_path: Path,
    materialize: bool,
    mutation: str,
) -> None:
    ws = _workspace(tmp_path, materialize=materialize)
    workflow_path = runtime_state_paths(ws)["workflow_state"]
    workflow = _read_json(workflow_path)
    if mutation == "missing_stage":
        workflow["stage_statuses"].pop("doctor")
    else:
        workflow["stage_statuses"]["forged-stage"] = {
            "status": "pending",
            "reason": "forged",
            "updated_at": "2026-07-12T00:00:00+00:00",
        }
    _write_json(workflow_path, workflow)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_workflow_stage_status_invalid",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    "case_id",
    ["self_referential_repo", "invalid_contract_path"],
)
def test_reg_read_contract_context_is_total_and_precedes_safe_absence(
    tmp_path: Path,
    case_id: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    if case_id == "self_referential_repo":
        repo = tmp_path / "self-referential-repo"
        repo.symlink_to(repo, target_is_directory=True)
        external_before = (os.readlink(repo), repo.lstat().st_mtime_ns)
    else:
        repo = _custom_repo(
            tmp_path,
            artifact_path="output/intermediate/audited_brief.md",
        )
        contract_path = repo / "configs/artifact_contracts.yaml"
        contracts = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        contracts["artifacts"][0]["path"] = ["malformed", "path"]
        contract_path.write_text(
            yaml.safe_dump(contracts, sort_keys=False),
            encoding="utf-8",
        )
        external_before = (
            contract_path.read_bytes(),
            contract_path.stat().st_mtime_ns,
        )
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=repo)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_contract_context_invalid",
    )
    _assert_read_only(ws, before)
    if case_id == "self_referential_repo":
        assert (os.readlink(repo), repo.lstat().st_mtime_ns) == external_before
    else:
        contract_path = repo / "configs/artifact_contracts.yaml"
        assert (
            contract_path.read_bytes(),
            contract_path.stat().st_mtime_ns,
        ) == external_before


def test_reg_read_24_missing_registry_preserves_invalid_recovery_precedence(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, materialize=False)
    paths = runtime_state_paths(ws)
    workflow = _read_json(paths["workflow_state"])
    workflow, added = contaminate_run_integrity_with_event_flag(
        workflow,
        reason_code="registry_missing_recovery_fixture",
        message="Missing Registry must not hide current-run contamination.",
        created_at="2026-07-12T00:00:00+00:00",
        event_type="run_integrity_contaminated",
        stage_id="doctor",
        artifact_id="config",
    )
    assert added is True
    _write_json(paths["workflow_state"], workflow)
    append_event(
        workspace=ws,
        run_id=str(workflow["run_id"]),
        event_type="run_integrity_contaminated",
        event_id="registry-missing-contamination",
        actor="system",
        stage_id="doctor",
        artifact_id="config",
        reason="Registry missing recovery precedence fixture.",
        metadata={"reason_code": "registry_missing_recovery_fixture"},
    )
    assert not paths["artifact_registry"].exists()
    recovery = evaluate_recovery_state(workspace=ws, repo_workdir=ROOT)
    assert recovery["status"] == "invalid_recovery_state"
    assert recovery["reason_code"] == "artifact_registry_missing_for_recovery"
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_recovery_context_invalid",
    )
    _assert_read_only(ws, before)


def test_reg_read_25_workspace_normalization_error_is_typed_and_value_free(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "self-referential-workspace"
    workspace.symlink_to(workspace, target_is_directory=True)
    before = (os.readlink(workspace), workspace.lstat().st_mtime_ns)

    verdict = interpret_artifact_registry(workspace=workspace, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_workspace_invalid",
    )
    assert (os.readlink(workspace), workspace.lstat().st_mtime_ns) == before


@pytest.mark.parametrize(
    ("case_id", "reason_code"),
    [
        ("registry_utf8", "artifact_registry_recovery_context_invalid"),
        ("registry_json", "artifact_registry_recovery_context_invalid"),
        ("registry_root", "artifact_registry_recovery_context_invalid"),
        ("registry_schema", "artifact_registry_recovery_context_invalid"),
        ("registry_symlink", "artifact_registry_recovery_context_invalid"),
        ("manifest_missing", "artifact_registry_recovery_context_invalid"),
        ("manifest_json", "artifact_registry_recovery_context_invalid"),
        ("manifest_root", "artifact_registry_recovery_context_invalid"),
        ("manifest_schema", "artifact_registry_recovery_context_invalid"),
        ("manifest_symlink", "artifact_registry_recovery_context_invalid"),
        ("manifest_run_id", "artifact_registry_recovery_context_invalid"),
        ("registry_run_id", "artifact_registry_recovery_context_invalid"),
        ("cross_run", "artifact_registry_recovery_context_invalid"),
    ],
    ids=lambda value: str(value),
)
def test_reg_read_03_malformed_or_unbound_registry_degrades_without_payload(
    tmp_path: Path,
    case_id: str,
    reason_code: str,
) -> None:
    ws = _workspace(tmp_path)
    paths = runtime_state_paths(ws)
    registry = _read_json(paths["artifact_registry"])
    manifest = _read_json(paths["runtime_manifest"])
    external_control: Path | None = None
    if case_id == "registry_utf8":
        paths["artifact_registry"].write_bytes(b"\xff\xfe")
    elif case_id == "registry_json":
        paths["artifact_registry"].write_text("{broken", encoding="utf-8")
    elif case_id == "registry_root":
        _write_json(paths["artifact_registry"], [])
    elif case_id == "registry_schema":
        registry["schema_version"] = "multi-agent-brief-artifact-registry/v999"
        _write_json(paths["artifact_registry"], registry)
    elif case_id == "registry_symlink":
        external_control = tmp_path / "external-artifact-registry.json"
        paths["artifact_registry"].replace(external_control)
        paths["artifact_registry"].symlink_to(external_control)
    elif case_id == "manifest_missing":
        paths["runtime_manifest"].unlink()
    elif case_id == "manifest_json":
        paths["runtime_manifest"].write_text("{broken", encoding="utf-8")
    elif case_id == "manifest_root":
        _write_json(paths["runtime_manifest"], [])
    elif case_id == "manifest_schema":
        manifest["schema_version"] = "multi-agent-brief-runtime-manifest/v999"
        _write_json(paths["runtime_manifest"], manifest)
    elif case_id == "manifest_symlink":
        external_control = tmp_path / "external-runtime-manifest.json"
        paths["runtime_manifest"].replace(external_control)
        paths["runtime_manifest"].symlink_to(external_control)
    elif case_id == "manifest_run_id":
        manifest["run_id"] = ""
        _write_json(paths["runtime_manifest"], manifest)
    elif case_id == "registry_run_id":
        registry["run_id"] = ""
        _write_json(paths["artifact_registry"], registry)
    elif case_id == "cross_run":
        registry["run_id"] = "run-from-another-workspace"
        _write_json(paths["artifact_registry"], registry)
    else:  # pragma: no cover - parameter contract
        raise AssertionError(case_id)
    before = _workspace_snapshot(ws)
    external_before = (
        (external_control.read_bytes(), external_control.stat().st_mtime_ns)
        if external_control is not None
        else None
    )

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code=reason_code,
    )
    _assert_read_only(ws, before)
    if external_control is not None:
        assert external_before == (
            external_control.read_bytes(),
            external_control.stat().st_mtime_ns,
        )


@pytest.mark.parametrize(
    ("case_id", "reason_code"),
    [
        ("root_extra", "artifact_registry_root_fields_invalid"),
        ("manifest_contract", "artifact_registry_manifest_contract_mismatch"),
        ("missing_record", "artifact_registry_artifact_universe_mismatch"),
        ("unknown_record", "artifact_registry_artifact_universe_mismatch"),
        ("record_not_object", "artifact_registry_record_not_object"),
        ("record_duplicate", "artifact_registry_record_identity_duplicate"),
        ("record_identity", "artifact_registry_record_identity_mismatch"),
        ("record_fields", "artifact_registry_record_path_invalid"),
        ("record_path", "artifact_registry_record_path_invalid"),
        ("record_contract", "artifact_registry_record_contract_mismatch"),
        ("record_status", "artifact_registry_producer_replay_mismatch"),
        ("record_status_shape", "artifact_registry_producer_replay_mismatch"),
    ],
    ids=lambda value: str(value),
)
def test_reg_read_04_contract_and_record_corruption_fail_closed(
    tmp_path: Path,
    case_id: str,
    reason_code: str,
) -> None:
    ws = _workspace(tmp_path)
    paths = runtime_state_paths(ws)
    registry = _read_json(paths["artifact_registry"])
    manifest = _read_json(paths["runtime_manifest"])
    records = registry["artifacts"]
    artifact_id = "analyst_draft_snapshot"
    record = records[artifact_id]
    if case_id == "root_extra":
        registry["valid_count"] = 999999
    elif case_id == "manifest_contract":
        manifest["expected_artifacts"][0]["path"] = "forged-secret"
        _write_json(paths["runtime_manifest"], manifest)
    elif case_id == "missing_record":
        records.pop(artifact_id)
    elif case_id == "unknown_record":
        records["forged_artifact"] = dict(record)
    elif case_id == "record_not_object":
        records[artifact_id] = "forged-secret"
    elif case_id == "record_duplicate":
        records["audited_brief"]["artifact_id"] = artifact_id
    elif case_id == "record_identity":
        record["artifact_id"] = "audited_brief"
    elif case_id == "record_fields":
        record.pop("path")
    elif case_id == "record_path":
        record["path"] = "../forged-secret"
    elif case_id == "record_contract":
        record["format"] = "json"
    elif case_id == "record_status":
        record["status"] = "banana"
    elif case_id == "record_status_shape":
        record["blocking_reason"] = "forged-secret"
    else:  # pragma: no cover - parameter contract
        raise AssertionError(case_id)
    if case_id != "manifest_contract":
        _write_json(paths["artifact_registry"], registry)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code=reason_code,
    )
    _assert_read_only(ws, before)


def test_reg_read_05_forged_intake_projection_never_reaches_a_view(
    tmp_path: Path,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    candidate_path.parent.mkdir(parents=True)
    _write_json(
        candidate_path,
        [
            {
                "candidate_id": "CAND-001",
                "claim": "A public-safe synthetic candidate.",
                "source_id": "SRC-001",
            }
        ],
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    projection = registry["artifacts"]["candidate_claims"]["intake_projection"]
    projection["normalization_count"] = 999999
    _write_json(registry_path, registry)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    "case_id",
    ["normalized_sha256", "normalizations", "finding"],
)
def test_reg_read_05_persisted_projection_must_match_current_evaluator(
    tmp_path: Path,
    case_id: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    candidate_path.parent.mkdir(parents=True)
    candidate = {
        "candidate_id": "CAND-001",
        "claim": "A public-safe synthetic candidate.",
        "source_id": "SRC-001",
    }
    if case_id == "finding":
        candidate.pop("claim")
    _write_json(candidate_path, [candidate])
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    projection = registry["artifacts"]["candidate_claims"]["intake_projection"]
    if case_id == "normalized_sha256":
        projection["normalized_sha256"] = "a" * 64
    elif case_id == "normalizations":
        projection["normalizations"] = [
            {
                "operation": "forged_operation",
                "path": "candidate_claims[0]",
                "source": "forged-source",
                "target": "forged-secret",
            }
        ]
        projection["normalization_count"] = 1
    elif case_id == "finding":
        assert projection["findings"]
        projection["findings"][0]["message"] = "forged-secret"
    else:  # pragma: no cover - parameter contract
        raise AssertionError(case_id)
    _write_json(registry_path, registry)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


def test_reg_read_06_real_writer_output_is_the_only_value_bearing_view(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    assert isinstance(verdict, CanonicalRegistryView)
    assert verdict.kind == "canonical"
    assert verdict.run_id
    assert not hasattr(verdict, "updated_at")
    assert verdict.artifact_count == len(verdict.records)
    assert verdict.artifact_count == len(verdict.resolved_paths)
    assert sum(verdict.status_counts.values()) == verdict.artifact_count
    assert verdict.records["candidate_claims"]["status"] == "expected"
    with pytest.raises(TypeError):
        verdict.records["candidate_claims"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        verdict.records["candidate_claims"]["status"] = "valid"  # type: ignore[index]
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    ("case_id", "updated_at", "expected_type"),
    [
        (
            "writer-shaped-opaque",
            "2099-01-01T00:00:00+00:00",
            CanonicalRegistryView,
        ),
        (
            "microsecond",
            "2099-01-01T00:00:00.000001+00:00",
            RegistryDegradation,
        ),
        (
            "non-utc",
            "2099-01-01T08:00:00+08:00",
            RegistryDegradation,
        ),
        (
            "non-writer-zulu",
            "2099-01-01T00:00:00Z",
            RegistryDegradation,
        ),
    ],
    ids=lambda value: str(value),
)
def test_reg_read_updated_at_is_structural_only_and_never_exposed(
    tmp_path: Path,
    case_id: str,
    updated_at: str,
    expected_type: type[CanonicalRegistryView] | type[RegistryDegradation],
) -> None:
    del case_id
    ws = _workspace(tmp_path)
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    registry["updated_at"] = updated_at
    _write_json(registry_path, registry)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    if expected_type is CanonicalRegistryView:
        assert isinstance(verdict, CanonicalRegistryView)
        assert not hasattr(verdict, "updated_at")
    else:
        _assert_negative(
            verdict,
            verdict_type=RegistryDegradation,
            reason_code="artifact_registry_updated_at_invalid",
        )
    _assert_read_only(ws, before)


def test_reg_read_07_truthful_invalid_writer_record_remains_canonical(
    tmp_path: Path,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "candidate_claims.json"
    artifact_path.parent.mkdir(parents=True)
    _write_json(
        artifact_path,
        [{"candidate_id": "CAND-001", "source_id": "SRC-001"}],
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    assert isinstance(verdict, CanonicalRegistryView)
    record = verdict.records["candidate_claims"]
    assert record["status"] == "invalid"
    assert record["intake_projection"]["fatal_finding_count"] >= 1
    assert verdict.status_counts["invalid"] >= 1


@pytest.mark.parametrize(
    ("case_id", "reason_code"),
    [
        ("size", "artifact_registry_snapshot_size_drift"),
        ("mtime", "artifact_registry_snapshot_mtime_drift"),
        ("sha256", "artifact_registry_snapshot_sha256_drift"),
    ],
    ids=lambda value: str(value),
)
def test_reg_read_08_structurally_bound_snapshot_drift_has_no_values(
    tmp_path: Path,
    case_id: str,
    reason_code: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("AAAA\n", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry = _read_json(runtime_state_paths(ws)["artifact_registry"])
    record = registry["artifacts"]["audited_brief"]
    recorded_timestamp = datetime.fromisoformat(record["mtime"]).timestamp()
    if case_id == "size":
        artifact_path.write_text("LONGER\n", encoding="utf-8")
        os.utime(artifact_path, (recorded_timestamp, recorded_timestamp))
    elif case_id == "mtime":
        os.utime(artifact_path, (recorded_timestamp + 10, recorded_timestamp + 10))
    elif case_id == "sha256":
        artifact_path.write_text("BBBB\n", encoding="utf-8")
        os.utime(artifact_path, (recorded_timestamp, recorded_timestamp))
    else:  # pragma: no cover - parameter contract
        raise AssertionError(case_id)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistrySnapshotDrift,
        reason_code=reason_code,
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize("case_id", ["presence", "file_type"])
def test_reg_read_r18_nonregular_transition_is_degradation(
    tmp_path: Path,
    case_id: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("AAAA\n", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    if case_id == "presence":
        artifact_path.unlink()
    else:
        artifact_path.unlink()
        artifact_path.mkdir()
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    "case_id",
    ["missing_size", "missing_sha256", "extra_key"],
    ids=lambda value: f"REG-READ-R24-{value}",
)
def test_reg_read_r24_incomplete_record_cannot_be_snapshot_drift(
    tmp_path: Path,
    case_id: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("AAAA\n", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    record = registry["artifacts"]["audited_brief"]
    recorded_timestamp = datetime.fromisoformat(record["mtime"]).timestamp()
    if case_id == "missing_size":
        record.pop("size_bytes")
        artifact_path.write_text("LONGER\n", encoding="utf-8")
    elif case_id == "missing_sha256":
        record.pop("sha256")
        artifact_path.write_text("BBBB\n", encoding="utf-8")
    elif case_id == "extra_key":
        record["forged_snapshot_detail"] = "forged-secret"
        artifact_path.write_text("LONGER\n", encoding="utf-8")
    else:  # pragma: no cover - parameter contract
        raise AssertionError(case_id)
    os.utime(artifact_path, (recorded_timestamp, recorded_timestamp))
    _write_json(registry_path, registry)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    ("field", "malformed_value"),
    [
        ("size_bytes", True),
        ("size_bytes", -1),
        ("size_bytes", 5.0),
        ("mtime", "2026-07-12T00:00:00Z"),
        ("mtime", "2026-07-12T00:00:00.123456+00:00"),
        ("sha256", "A" * 64),
    ],
    ids=[
        "size-bool",
        "size-negative",
        "size-float",
        "mtime-z",
        "mtime-microseconds",
        "sha-uppercase",
    ],
)
def test_reg_read_r25_malformed_physical_snapshot_cannot_be_drift(
    tmp_path: Path,
    field: str,
    malformed_value: Any,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("AAAA\n", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    record = registry["artifacts"]["audited_brief"]
    recorded_timestamp = datetime.fromisoformat(record["mtime"]).timestamp()
    record[field] = malformed_value
    _write_json(registry_path, registry)
    artifact_path.write_text("LONGER\n", encoding="utf-8")
    os.utime(artifact_path, (recorded_timestamp, recorded_timestamp))
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    "field",
    ["status", "validation_result", "blocking_reason", "stale_metadata"],
)
def test_reg_read_r26_nonphysical_record_change_precedes_physical_drift(
    tmp_path: Path,
    field: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("AAAA\n", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    record = registry["artifacts"]["audited_brief"]
    recorded_timestamp = datetime.fromisoformat(record["mtime"]).timestamp()
    if field == "stale_metadata":
        record["stale_baseline_sha256"] = "0" * 64
    else:
        record[field] = "forged-secret"
    _write_json(registry_path, registry)
    artifact_path.write_text("LONGER\n", encoding="utf-8")
    os.utime(artifact_path, (recorded_timestamp, recorded_timestamp))
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize("projection_mutation", ["add", "remove", "type_alias"])
@pytest.mark.parametrize("physical_drift", ["mtime", "size", "sha256"])
def test_reg_read_r27_intake_forgery_precedes_every_physical_drift(
    tmp_path: Path,
    projection_mutation: str,
    physical_drift: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    candidate_path.parent.mkdir(parents=True)
    candidate = {
        "candidate_id": "CAND-001",
        "claim": "Alpha",
        "source_id": "SRC-001",
    }
    _write_json(candidate_path, [candidate])
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    record = registry["artifacts"]["candidate_claims"]
    projection = record["intake_projection"]
    recorded_timestamp = datetime.fromisoformat(record["mtime"]).timestamp()
    if projection_mutation == "add":
        projection["forged_detail"] = "forged-secret"
    elif projection_mutation == "remove":
        projection.pop("normalization_count")
    elif projection_mutation == "type_alias":
        projection["normalization_count"] = float(
            projection["normalization_count"]
        )
    else:  # pragma: no cover - parameter contract
        raise AssertionError(projection_mutation)
    _write_json(registry_path, registry)

    if physical_drift == "mtime":
        os.utime(
            candidate_path,
            (recorded_timestamp + 10, recorded_timestamp + 10),
        )
    elif physical_drift == "size":
        candidate["claim"] = "A substantially longer candidate claim"
        _write_json(candidate_path, [candidate])
        os.utime(candidate_path, (recorded_timestamp, recorded_timestamp))
    elif physical_drift == "sha256":
        candidate["claim"] = "Bravo"
        _write_json(candidate_path, [candidate])
        assert candidate_path.stat().st_size == record["size_bytes"]
        os.utime(candidate_path, (recorded_timestamp, recorded_timestamp))
    else:  # pragma: no cover - parameter contract
        raise AssertionError(physical_drift)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


def test_reg_read_r28_one_nonphysical_mismatch_overrides_other_record_drift(
    tmp_path: Path,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    brief_path = ws / "output" / "intermediate" / "audited_brief.md"
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    brief_path.parent.mkdir(parents=True)
    brief_path.write_text("AAAA\n", encoding="utf-8")
    _write_json(
        candidate_path,
        [
            {
                "candidate_id": "CAND-001",
                "claim": "Alpha",
                "source_id": "SRC-001",
            }
        ],
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    brief_record = registry["artifacts"]["audited_brief"]
    recorded_timestamp = datetime.fromisoformat(brief_record["mtime"]).timestamp()
    registry["artifacts"]["candidate_claims"]["status"] = "forged-secret"
    _write_json(registry_path, registry)
    brief_path.write_text("LONGER\n", encoding="utf-8")
    os.utime(brief_path, (recorded_timestamp, recorded_timestamp))
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


def test_reg_read_09_custom_contract_path_is_canonical_and_bound(
    tmp_path: Path,
) -> None:
    repo = _custom_repo(tmp_path, artifact_path="custom/audited_brief.md")
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "custom" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Custom brief\n", encoding="utf-8")
    default_path = ws / "output" / "intermediate" / "audited_brief.md"
    default_path.parent.mkdir(parents=True)
    default_path.write_text("poison default bytes", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=repo, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=repo, actor="cli")

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=repo)

    assert isinstance(verdict, CanonicalRegistryView)
    assert verdict.records["audited_brief"]["path"] == "custom/audited_brief.md"
    assert verdict.resolved_paths["audited_brief"] == artifact_path


def test_reg_read_10_symlink_identity_drift_degrades_before_exposure(
    tmp_path: Path,
) -> None:
    repo = _custom_repo(tmp_path, artifact_path="alias/audited_brief.md")
    ws = _workspace(tmp_path, repo_workdir=repo)
    real = ws / "real"
    real.mkdir()
    (ws / "alias").symlink_to(real, target_is_directory=True)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=repo)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_path_context_invalid",
    )
    _assert_read_only(ws, before)


def test_reg_read_11_forged_invalid_to_valid_status_fails_producer_replay(
    tmp_path: Path,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    record = registry["artifacts"]["audited_brief"]
    assert record["status"] == "invalid"
    recorded_sha256 = record["sha256"]
    record["status"] = "valid"
    record["validation_result"] = "valid"
    record["blocking_reason"] = ""
    _write_json(registry_path, registry)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    assert _read_json(registry_path)["artifacts"]["audited_brief"]["sha256"] == recorded_sha256
    _assert_read_only(ws, before)


def test_reg_read_12_writer_agent_read_error_remains_canonical(
    tmp_path: Path,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "candidate_claims.json"
    artifact_path.mkdir(parents=True)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    writer_record = checked["artifact_registry"]["artifacts"]["candidate_claims"]
    assert writer_record["status"] == "invalid"
    assert writer_record["intake_projection"]["raw_sha256"] == ""
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    assert isinstance(verdict, CanonicalRegistryView)
    assert _thaw_json(verdict.records["candidate_claims"]) == writer_record
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    ("case_id", "reason_code"),
    [
        ("missing", "artifact_registry_recovery_context_invalid"),
        ("json", "artifact_registry_recovery_context_invalid"),
        ("root", "artifact_registry_recovery_context_invalid"),
        ("schema", "artifact_registry_recovery_context_invalid"),
        ("symlink", "artifact_registry_recovery_context_invalid"),
        ("run_id", "artifact_registry_workflow_run_id_invalid"),
        ("cross_run", "artifact_registry_workflow_run_id_mismatch"),
    ],
    ids=lambda value: str(value),
)
def test_reg_read_13_workflow_context_fails_closed(
    tmp_path: Path,
    case_id: str,
    reason_code: str,
) -> None:
    ws = _workspace(tmp_path)
    workflow_path = runtime_state_paths(ws)["workflow_state"]
    workflow = _read_json(workflow_path)
    external_control: Path | None = None
    if case_id == "missing":
        workflow_path.unlink()
    elif case_id == "json":
        workflow_path.write_text("{broken", encoding="utf-8")
    elif case_id == "root":
        _write_json(workflow_path, [])
    elif case_id == "schema":
        workflow["schema_version"] = "multi-agent-brief-workflow-state/v999"
        _write_json(workflow_path, workflow)
    elif case_id == "symlink":
        external_control = tmp_path / "external-workflow-state.json"
        workflow_path.replace(external_control)
        workflow_path.symlink_to(external_control)
    elif case_id == "run_id":
        workflow["run_id"] = ""
        _write_json(workflow_path, workflow)
    elif case_id == "cross_run":
        workflow["run_id"] = "run-from-another-workspace"
        _write_json(workflow_path, workflow)
    else:  # pragma: no cover - parameter contract
        raise AssertionError(case_id)
    before = _workspace_snapshot(ws)
    external_before = (
        (external_control.read_bytes(), external_control.stat().st_mtime_ns)
        if external_control is not None
        else None
    )

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code=reason_code,
    )
    _assert_read_only(ws, before)
    if external_control is not None:
        assert external_before == (
            external_control.read_bytes(),
            external_control.stat().st_mtime_ns,
        )


def test_reg_read_14_invalid_recovery_context_fails_closed(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    event_path = runtime_state_paths(ws)["event_log"]
    event_path.write_text("{broken\n", encoding="utf-8")
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_recovery_context_invalid",
    )
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    "event_type",
    ["repair_completed", "repair_stage_superseded"],
    ids=["repair", "supersede"],
)
def test_reg_read_15_current_recovery_stale_baseline_replays_exactly(
    tmp_path: Path,
    event_type: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    candidate_path.parent.mkdir(parents=True)
    _write_json(
        candidate_path,
        [
            {
                "candidate_id": "CAND-001",
                "claim": "A public-safe synthetic candidate.",
                "source_id": "SRC-001",
            }
        ],
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    writer_registry = _install_bound_recovery_registry(ws, event_type=event_type)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    assert isinstance(verdict, CanonicalRegistryView)
    assert _thaw_json(verdict.records["candidate_claims"]) == writer_registry[
        "artifacts"
    ]["candidate_claims"]
    _assert_read_only(ws, before)


@pytest.mark.parametrize(
    "path_key",
    [
        "artifact_registry",
        "runtime_manifest",
        "workflow_state",
        "event_log",
        "finalize_report",
    ],
    ids=["registry", "manifest", "workflow", "event-log", "finalize-report"],
)
def test_reg_read_16_control_path_preflight_rejects_direct_symlinks(
    tmp_path: Path,
    path_key: str,
) -> None:
    ws = _workspace(tmp_path)
    control_path = getattr(resolve_recovery_control_paths(ws), path_key)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    if not control_path.exists():
        control_path.write_text("{}\n", encoding="utf-8")
    external_control = tmp_path / f"external-{control_path.name}"
    control_path.replace(external_control)
    control_path.symlink_to(external_control)
    before = _workspace_snapshot(ws)
    external_before = (
        external_control.read_bytes(),
        external_control.stat().st_mtime_ns,
    )

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_recovery_context_invalid",
    )
    _assert_read_only(ws, before)
    assert external_before == (
        external_control.read_bytes(),
        external_control.stat().st_mtime_ns,
    )


@pytest.mark.parametrize(
    "alias_kind",
    ["float", "bool"],
    ids=["int-as-float", "int-as-bool"],
)
def test_reg_read_19_producer_replay_comparison_is_json_type_strict(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("x" if alias_kind == "bool" else "AAAA\n", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    record = registry["artifacts"]["audited_brief"]
    writer_size = record["size_bytes"]
    assert isinstance(writer_size, int) and not isinstance(writer_size, bool)
    if alias_kind == "float":
        record["size_bytes"] = float(writer_size)
    else:
        assert writer_size == 1
        record["size_bytes"] = True
    assert record["size_bytes"] == writer_size
    assert type(record["size_bytes"]) is not type(writer_size)
    _write_json(registry_path, registry)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_producer_replay_mismatch",
    )
    _assert_read_only(ws, before)


def test_reg_read_19_manifest_contract_comparison_is_json_type_strict(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    manifest_path = runtime_state_paths(ws)["runtime_manifest"]
    manifest = _read_json(manifest_path)
    required = manifest["expected_artifacts"][0]["required"]
    assert isinstance(required, bool)
    manifest["expected_artifacts"][0]["required"] = int(required)
    assert manifest["expected_artifacts"][0]["required"] == required
    assert type(manifest["expected_artifacts"][0]["required"]) is not type(required)
    _write_json(manifest_path, manifest)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_manifest_contract_mismatch",
    )
    _assert_read_only(ws, before)


def test_reg_read_20_writer_workflow_stage_validation_precedes_replay(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    workflow_path = runtime_state_paths(ws)["workflow_state"]
    workflow = _read_json(workflow_path)
    workflow["stage_statuses"]["doctor"]["status"] = "banana"
    _write_json(workflow_path, workflow)
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code="artifact_registry_workflow_stage_status_invalid",
    )
    _assert_read_only(ws, before)


def test_reg_read_21_json_comparison_ignores_object_member_order(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry = _read_json(registry_path)
    registry["artifacts"] = dict(reversed(list(registry["artifacts"].items())))
    reordered = dict(reversed(list(registry.items())))
    registry_path.write_text(
        json.dumps(reordered, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    before = _workspace_snapshot(ws)

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    assert isinstance(verdict, CanonicalRegistryView)
    assert verdict.artifact_count == len(registry["artifacts"])
    _assert_read_only(ws, before)


def _replace_workspace_after_recovery_load(
    *,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    replacement: Path,
    moved_workspace: Path,
) -> None:
    real_load_recovery_context_verdict = (
        artifact_registry_read.load_recovery_context_verdict
    )

    def replacing_load_recovery_context_verdict(**kwargs: Any):
        context = real_load_recovery_context_verdict(**kwargs)
        workspace.rename(moved_workspace)
        replacement.rename(workspace)
        return context

    monkeypatch.setattr(
        artifact_registry_read,
        "load_recovery_context_verdict",
        replacing_load_recovery_context_verdict,
    )


def _workspace_with_audited_brief(tmp_path: Path) -> tuple[Path, Path]:
    ws = _workspace(tmp_path)
    brief_path = ws / "output/intermediate/audited_brief.md"
    brief_path.write_text("# Audited brief\n\nBound content.\n", encoding="utf-8")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    return ws, brief_path


@pytest.mark.parametrize(
    "change_artifact",
    [False, True],
    ids=["REG-READ-22-byte-equivalent", "REG-READ-23-changed-artifact"],
)
def test_reg_read_22_root_substitution_uses_invocation_local_byte_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change_artifact: bool,
) -> None:
    # CanonicalRegistryView is an invocation-local semantic replay. Returned
    # paths are lexical selectors, not retained handles or future-freshness
    # receipts; byte changes observed by replay must still degrade.
    ws, brief_path = _workspace_with_audited_brief(tmp_path)
    replacement = tmp_path / "replacement-workspace"
    moved_workspace = tmp_path / "session-bound-workspace"
    shutil.copytree(ws, replacement, copy_function=shutil.copy2)
    if change_artifact:
        replacement_brief = replacement / brief_path.relative_to(ws)
        original_stat = replacement_brief.stat()
        replacement_brief.write_text(
            "# Audited brief\n\nBound content changed after Recovery loading.\n",
            encoding="utf-8",
        )
        os.utime(
            replacement_brief,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
    trusted_before = _workspace_snapshot(ws)
    replacement_before = _workspace_snapshot(replacement)
    _replace_workspace_after_recovery_load(
        monkeypatch=monkeypatch,
        workspace=ws,
        replacement=replacement,
        moved_workspace=moved_workspace,
    )

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    if change_artifact:
        _assert_negative(
            verdict,
            verdict_type=RegistrySnapshotDrift,
            reason_code="artifact_registry_snapshot_size_drift",
        )
    else:
        assert isinstance(verdict, CanonicalRegistryView)
        assert verdict.resolved_paths["audited_brief"] == brief_path
        assert all(path.is_relative_to(ws) for path in verdict.resolved_paths.values())
    assert _workspace_snapshot(moved_workspace) == trusted_before
    assert _workspace_snapshot(ws) == replacement_before
