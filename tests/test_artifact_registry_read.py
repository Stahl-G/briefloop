from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

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
from tests.helpers import write_minimal_workspace


ROOT = Path(__file__).resolve().parent.parent


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


def test_reg_read_01_no_runtime_state_is_not_materialized_and_zero_write(
    tmp_path: Path,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
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


@pytest.mark.parametrize(
    ("case_id", "reason_code"),
    [
        ("registry_utf8", "artifact_registry_unreadable"),
        ("registry_json", "artifact_registry_unreadable"),
        ("registry_root", "artifact_registry_root_invalid"),
        ("registry_schema", "artifact_registry_schema_unsupported"),
        ("manifest_missing", "artifact_registry_manifest_missing"),
        ("manifest_json", "artifact_registry_manifest_unreadable"),
        ("manifest_root", "artifact_registry_manifest_root_invalid"),
        ("manifest_schema", "artifact_registry_manifest_schema_unsupported"),
        ("manifest_run_id", "artifact_registry_manifest_run_id_invalid"),
        ("registry_run_id", "artifact_registry_run_id_invalid"),
        ("cross_run", "artifact_registry_run_id_mismatch"),
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
    if case_id == "registry_utf8":
        paths["artifact_registry"].write_bytes(b"\xff\xfe")
    elif case_id == "registry_json":
        paths["artifact_registry"].write_text("{broken", encoding="utf-8")
    elif case_id == "registry_root":
        _write_json(paths["artifact_registry"], [])
    elif case_id == "registry_schema":
        registry["schema_version"] = "multi-agent-brief-artifact-registry/v999"
        _write_json(paths["artifact_registry"], registry)
    elif case_id == "manifest_missing":
        paths["runtime_manifest"].unlink()
    elif case_id == "manifest_json":
        paths["runtime_manifest"].write_text("{broken", encoding="utf-8")
    elif case_id == "manifest_root":
        _write_json(paths["runtime_manifest"], [])
    elif case_id == "manifest_schema":
        manifest["schema_version"] = "multi-agent-brief-runtime-manifest/v999"
        _write_json(paths["runtime_manifest"], manifest)
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

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    _assert_negative(
        verdict,
        verdict_type=RegistryDegradation,
        reason_code=reason_code,
    )
    _assert_read_only(ws, before)


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
        ("record_fields", "artifact_registry_record_fields_invalid"),
        ("record_path", "artifact_registry_record_path_invalid"),
        ("record_contract", "artifact_registry_record_contract_mismatch"),
        ("record_status", "artifact_registry_record_status_invalid"),
        ("record_status_shape", "artifact_registry_record_status_shape_invalid"),
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
        reason_code="artifact_registry_intake_projection_invalid",
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
    assert verdict.artifact_count == len(verdict.records)
    assert verdict.artifact_count == len(verdict.resolved_paths)
    assert sum(verdict.status_counts.values()) == verdict.artifact_count
    assert verdict.records["candidate_claims"]["status"] == "expected"
    with pytest.raises(TypeError):
        verdict.records["candidate_claims"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        verdict.records["candidate_claims"]["status"] = "valid"  # type: ignore[index]
    _assert_read_only(ws, before)


def test_reg_read_07_truthful_invalid_writer_record_remains_canonical(
    tmp_path: Path,
) -> None:
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "output" / "intermediate" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")
    check_runtime_state(workspace=ws, repo_workdir=ROOT, actor="cli")

    verdict = interpret_artifact_registry(workspace=ws, repo_workdir=ROOT)

    assert isinstance(verdict, CanonicalRegistryView)
    assert verdict.records["audited_brief"]["status"] == "invalid"
    assert verdict.status_counts["invalid"] >= 1


@pytest.mark.parametrize(
    ("case_id", "reason_code"),
    [
        ("presence", "artifact_registry_snapshot_presence_drift"),
        ("size", "artifact_registry_snapshot_size_drift"),
        ("mtime", "artifact_registry_snapshot_mtime_drift"),
        ("sha256", "artifact_registry_snapshot_sha256_drift"),
        ("file_type", "artifact_registry_snapshot_file_type_drift"),
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
    if case_id == "presence":
        artifact_path.unlink()
    elif case_id == "size":
        artifact_path.write_text("LONGER\n", encoding="utf-8")
        os.utime(artifact_path, (recorded_timestamp, recorded_timestamp))
    elif case_id == "mtime":
        os.utime(artifact_path, (recorded_timestamp + 10, recorded_timestamp + 10))
    elif case_id == "sha256":
        artifact_path.write_text("BBBB\n", encoding="utf-8")
        os.utime(artifact_path, (recorded_timestamp, recorded_timestamp))
    elif case_id == "file_type":
        artifact_path.unlink()
        artifact_path.mkdir()
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


def test_reg_read_09_custom_contract_path_is_canonical_and_bound(
    tmp_path: Path,
) -> None:
    repo = _custom_repo(tmp_path, artifact_path="custom/audited_brief.md")
    ws = write_minimal_workspace(tmp_path / "ws")
    artifact_path = ws / "custom" / "audited_brief.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Custom brief\n", encoding="utf-8")
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
