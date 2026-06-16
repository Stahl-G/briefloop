from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.experiments import validate_run_record, validate_scorecard


SHA = "b" * 64
ROOT = Path(__file__).resolve().parent.parent
CLEAN_FIXTURE_MANIFEST = (
    ROOT
    / "tests"
    / "fixtures"
    / "fast_rerun_clean_archive"
    / "output"
    / "runs"
    / "mabw-20260614T000000Z-public0001"
    / "manifest.json"
)
PLAN_ONLY_FIXTURE_MANIFEST = (
    ROOT
    / "tests"
    / "fixtures"
    / "fast_rerun_source_candidates_only_archive"
    / "output"
    / "runs"
    / "mabw-20260614T000000Z-planonly0001"
    / "manifest.json"
)


def _case_manifest() -> dict:
    return {
        "schema_version": "mabw.experiment_080.case.v1",
        "experiment_id": "MABW-080",
        "case_id": "weekly_public_001",
        "case_title": "Weekly public brief",
        "public_safe": True,
        "created_at": "2026-06-14T00:00:00Z",
        "repo_commit": "abc123",
        "conditions": ["baseline", "memory"],
        "frozen_fact_layer": {"manifest_path": "frozen_fact_layer.json"},
        "guidance_set": {"path": "guidance_set.json"},
        "allowed_claims": {"a_grade_requires_same_fact_layer": True},
    }


def _frozen_fact_layer() -> dict:
    return {
        "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
        "source_run_id": "mabw-20260614T000000Z-test",
        "source_archive_path": "output/runs/mabw-20260614T000000Z-test/manifest.json",
        "artifacts": [
            {
                "artifact_id": "durable_source_evidence_or_source_pack",
                "path": "input/sources/source_pack.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "input_classification",
                "path": "output/input_classification.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "candidate_claims",
                "path": "output/intermediate/candidate_claims.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "screened_candidates",
                "path": "output/intermediate/screened_candidates.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "claim_ledger",
                "path": "output/intermediate/claim_ledger.json",
                "sha256": SHA,
            },
        ],
    }


def _guidance_set() -> dict:
    return {
        "schema_version": "mabw.experiment_080.guidance_set.v1",
        "entries": [
            {
                "entry_id": "AG-0001",
                "guidance_text": "Lead with business implication before news recap.",
                "source": "improvement_ledger",
                "expected_manifestation": "Business implication appears before news recap.",
                "relevance_rule": "Applies to management-facing market briefs.",
            }
        ],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_case(case_dir: Path) -> None:
    case_dir.mkdir(parents=True)
    _write_json(case_dir / "case_manifest.json", _case_manifest())
    _write_json(case_dir / "frozen_fact_layer.json", _frozen_fact_layer())
    _write_json(case_dir / "guidance_set.json", _guidance_set())


def _write_case_from_archive(case_dir: Path, archive_manifest: Path, *, source_pack_sha: str | None = None) -> None:
    case_dir.mkdir(parents=True)
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]
    artifacts = []
    for artifact in fact_layer["artifacts"]:
        artifact_id = artifact["artifact_id"]
        if artifact_id == "durable_source_evidence_or_source_pack":
            sha = source_pack_sha or artifact["pack_sha256"]
            path = "input/sources/source_pack.json"
        else:
            sha = artifact["sha256"]
            path = artifact["original_path"]
        artifacts.append({"artifact_id": artifact_id, "path": path, "sha256": sha})
    _write_json(case_dir / "case_manifest.json", _case_manifest())
    _write_json(
        case_dir / "frozen_fact_layer.json",
        {
            "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
            "source_run_id": manifest["run_id"],
            "source_archive_path": f"output/runs/{manifest['run_id']}/manifest.json",
            "artifacts": artifacts,
        },
    )
    _write_json(case_dir / "guidance_set.json", _guidance_set())


def _copy_archive_to_workspace(ws: Path, archive_manifest: Path) -> Path:
    run_dir = archive_manifest.parent
    target = ws / "output" / "runs" / run_dir.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run_dir, target)
    return target / "manifest.json"


def _add_scorecard_archive_reports(archive_manifest: Path) -> None:
    archive_root = archive_manifest.parent
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    records = manifest.setdefault("files", [])

    def add_json(archive_path: str, original_path: str, payload: dict) -> None:
        path = archive_root / archive_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, payload)
        records.append({
            "role": "intermediate" if archive_path.startswith("intermediate/") else "control",
            "archive_path": archive_path,
            "original_path": original_path,
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        })

    add_json(
        "intermediate/finalize_report.json",
        "output/intermediate/finalize_report.json",
        {
            "status": "pass",
            "reader_clean": {
                "status": "pass",
                "markdown_blocking_count": 0,
                "docx_blocking_count": 0,
            },
            "delivery_artifacts": ["output/delivery/brief.md"],
        },
    )
    add_json(
        "intermediate/gates/auditor_quality_gate_report.json",
        "output/intermediate/gates/auditor_quality_gate_report.json",
        {"status": "pass", "gate_results": []},
    )
    add_json(
        "intermediate/gates/finalize_quality_gate_report.json",
        "output/intermediate/gates/finalize_quality_gate_report.json",
        {"status": "pass", "gate_results": []},
    )
    add_json(
        "control/artifact_registry.json",
        "output/intermediate/artifact_registry.json",
        {"schema_version": "multi-agent-brief-artifact-registry/v1", "artifacts": {}},
    )
    _write_json(archive_manifest, manifest)


def _write_terminal_runtime(
    ws: Path,
    *,
    run_id: str,
    runtime: str = "codex",
    run_integrity: dict | str | None = None,
    current_stage=None,
    finalize_status: str = "complete",
) -> None:
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    _write_json(
        intermediate / "runtime_manifest.json",
        {
            "schema_version": "multi-agent-brief-runtime-manifest/v1",
            "run_id": run_id,
            "runtime": runtime,
        },
    )
    if run_integrity is None:
        run_integrity = {
            "status": "clean",
            "reference_eligible": True,
            "clean_single_shot": True,
            "reasons": [],
        }
    _write_json(
        intermediate / "workflow_state.json",
        {
            "schema_version": "multi-agent-brief-workflow-state/v1",
            "run_id": run_id,
            "current_stage": current_stage,
            "stage_statuses": {"finalize": {"status": finalize_status}},
            "run_integrity": run_integrity,
        },
    )


def _patch_archive_manifest(path: Path, **updates) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    _write_json(path, payload)


def _register_args(case_dir: Path, ws: Path, output: Path, *, condition: str = "memory") -> list[str]:
    return [
        "experiments",
        "080",
        "register-run",
        "--case",
        str(case_dir),
        "--condition",
        condition,
        "--workspace",
        str(ws),
        "--output",
        str(output),
        "--json",
    ]


def _score_args(case_dir: Path, run_record: Path, output: Path) -> list[str]:
    return [
        "experiments",
        "080",
        "score-run",
        "--case",
        str(case_dir),
        "--run-record",
        str(run_record),
        "--output",
        str(output),
        "--json",
    ]


def test_experiments_080_validate_case_json_ok(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["case_id"] == "weekly_public_001"
    assert sorted(payload["validated_files"]) == [
        "case_manifest.json",
        "frozen_fact_layer.json",
        "guidance_set.json",
    ]


def test_experiments_080_validate_case_missing_frozen_fact_layer_fails(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    (case_dir / "frozen_fact_layer.json").unlink()

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(error["code"] == "missing_case_file" for error in payload["errors"])


def test_experiments_080_validate_case_source_candidates_only_fails(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(
        case_dir / "frozen_fact_layer.json",
        {
            "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
            "source_run_id": "mabw-20260614T000000Z-test",
            "artifacts": [
                {
                    "artifact_id": "source_candidates",
                    "path": "output/intermediate/source_candidates.yaml",
                    "sha256": SHA,
                }
            ],
        },
    )

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(error["code"] == "source_plan_not_evidence" for error in payload["errors"])


def test_experiments_080_validate_case_is_read_only(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    before = {
        path.relative_to(case_dir).as_posix(): path.read_bytes()
        for path in sorted(case_dir.glob("*.json"))
    }

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 0
    json.loads(capsys.readouterr().out)
    after = {
        path.relative_to(case_dir).as_posix(): path.read_bytes()
        for path in sorted(case_dir.glob("*.json"))
    }
    assert after == before


def test_experiments_080_register_run_writes_valid_record(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    output = tmp_path / "runs" / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run_id"] == run_id
    assert payload["output"] == str(output)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert validate_run_record(record) == []
    assert record["workspace_path"] == "<redacted-workspace>"
    assert record["run_archive_path"] == f"output/runs/{run_id}/manifest.json"
    assert record["repo_commit"] == "abc123"
    assert record["repo_commit_source"] == "case_manifest"
    assert record["imported_fact_layer"]["matches_case_frozen_fact_layer"] is True
    assert record["timing"]["schema_version"] == "mabw.control_timing.v1"


def test_experiments_080_register_run_is_idempotent_when_output_matches(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    output = tmp_path / "memory.run_record.json"

    assert main(_register_args(case_dir, ws, output)) == 0
    capsys.readouterr()
    before = output.read_bytes()
    assert main(_register_args(case_dir, ws, output)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["written"] is False
    assert output.read_bytes() == before


def test_experiments_080_register_run_refuses_different_existing_output(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"
    output.write_text("{}\n", encoding="utf-8")

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_OUTPUT_EXISTS"
    assert output.read_text(encoding="utf-8") == "{}\n"


def test_experiments_080_register_run_rejects_condition_not_in_case(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "prompt.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="prompt_only"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_CONDITION_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_non_terminal_workspace(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name, current_stage="analyst")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_NOT_TERMINAL"
    assert not output.exists()


def test_experiments_080_register_run_rejects_missing_archive(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id="mabw-20260614T000000Z-public0001")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_INPUT_MISSING"
    assert not output.exists()


def test_experiments_080_register_run_records_contaminated_run(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    contaminated = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test_contamination", "message": "test"}],
    }
    _patch_archive_manifest(archive_manifest, run_integrity=contaminated)
    _write_terminal_runtime(ws, run_id=run_id, run_integrity=contaminated)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["run_integrity"]["status"] == "contaminated"
    assert record["run_integrity"]["reference_eligible"] is False
    assert validate_run_record(record) == []


def test_experiments_080_register_run_rejects_malformed_run_integrity(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name, run_integrity="bad")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_INTEGRITY_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_run_id_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_id"] = "mabw-20260614T000000Z-other"
    _write_json(workflow_path, workflow)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_ID_MISMATCH"
    assert not output.exists()


def test_experiments_080_register_run_records_fact_layer_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST, source_pack_sha="c" * 64)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    imported = record["imported_fact_layer"]
    assert imported["matches_case_frozen_fact_layer"] is False
    assert imported["mismatches"][0]["artifact_id"] == "durable_source_evidence_or_source_pack"


def test_experiments_080_register_run_rejects_source_plan_archive(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, PLAN_ONLY_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_missing_fact_layer_file(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    claim_ledger = archive_manifest.parent / "fact_layer" / "output" / "intermediate" / "claim_ledger.json"
    claim_ledger.unlink()
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_tampered_fact_layer_file(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    claim_ledger = archive_manifest.parent / "fact_layer" / "output" / "intermediate" / "claim_ledger.json"
    claim_ledger.write_text('{"tampered": true}\n', encoding="utf-8")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_source_pack_hash_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"][0]["pack_sha256"] = "d" * 64
    _write_json(archive_manifest, manifest)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_extra_non_fact_layer_artifact(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"].append({
        "artifact_id": "delivery_brief",
        "fact_role": "not_fact_layer",
        "archive_path": "delivery/brief.md",
        "original_path": "output/delivery/brief.md",
        "sha256": "e" * 64,
        "size_bytes": 1,
    })
    _write_json(archive_manifest, manifest)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_does_not_use_unrelated_cwd_git(tmp_path, capsys, monkeypatch):
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    subprocess.run(["git", "init"], cwd=unrelated, check=True, capture_output=True, text=True)
    (unrelated / "README.md").write_text("unrelated\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=unrelated, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=unrelated,
        check=True,
        capture_output=True,
        text=True,
    )
    unrelated_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=unrelated,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.chdir(unrelated)

    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["repo_commit"] == "abc123"
    assert record["repo_commit"] != unrelated_head
    assert record["repo_commit_source"] == "case_manifest"


def test_experiments_080_score_run_writes_deterministic_scorecard_draft(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "scorecards" / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["validity_class"] == "invalid_incomplete"
    assert payload["assessment_status"] == "needs_assessment"
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert validate_scorecard(scorecard) == []
    assert scorecard["assessment_status"] == "needs_assessment"
    assert scorecard["guidance_scores"] == []
    assert scorecard["guidance_assessment"]["guidance_entry_ids"] == ["AG-0001"]
    assert scorecard["control_integrity"]["archive_present"] is True
    assert scorecard["control_integrity"]["quality_gates_passed"] is True
    assert scorecard["control_integrity"]["finalize_report_pass"] is True
    assert scorecard["control_integrity"]["artifact_registry_valid"] is True
    assert scorecard["reader_clean"]["pass"] is True
    assert scorecard["frozen_fact_layer"]["matches_case"] is True
    assert "Python does not score guidance manifestation" in scorecard["notes"][1]


def test_experiments_080_score_run_is_idempotent_when_output_matches(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0
    capsys.readouterr()
    before = scorecard_path.read_bytes()

    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["written"] is False
    assert scorecard_path.read_bytes() == before


def test_experiments_080_score_run_refuses_different_existing_output(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    scorecard_path.write_text("{}\n", encoding="utf-8")
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_OUTPUT_EXISTS"
    assert scorecard_path.read_text(encoding="utf-8") == "{}\n"


def test_experiments_080_score_run_marks_fact_layer_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST, source_pack_sha="c" * 64)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["validity_class"] == "invalid_fact_layer_mismatch"
    assert scorecard["frozen_fact_layer"]["matches_case"] is False


def test_experiments_080_score_run_marks_contaminated_run(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    contaminated = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test_contamination", "message": "test"}],
    }
    _patch_archive_manifest(archive_manifest, run_integrity=contaminated)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name, run_integrity=contaminated)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["validity_class"] == "invalid_contaminated"
    assert scorecard["control_integrity"]["run_integrity_clean"] is False


def test_experiments_080_score_run_marks_missing_archive_incomplete(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    shutil.rmtree(archive_manifest.parent)

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["validity_class"] == "invalid_incomplete"
    assert scorecard["control_integrity"]["archive_present"] is False
    assert scorecard["reader_clean"]["status"] == "unknown"


def test_experiments_080_score_run_rejects_archive_run_id_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _patch_archive_manifest(archive_manifest, run_id="mabw-20260614T000000Z-other0001")

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_ID_MISMATCH"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_archive_runtime_manifest_run_id_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _patch_archive_manifest(archive_manifest, runtime_manifest_run_id="mabw-20260614T000000Z-other0001")

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_ID_MISMATCH"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_tampered_finalize_report(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _write_json(
        archive_manifest.parent / "intermediate" / "finalize_report.json",
        {"status": "pass", "reader_clean": {"status": "pass", "markdown_blocking_count": 0}},
    )

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_tampered_gate_report(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _write_json(
        archive_manifest.parent / "intermediate" / "gates" / "auditor_quality_gate_report.json",
        {"status": "pass", "gate_results": [{"gate_id": "tampered"}]},
    )

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_tampered_artifact_registry(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _write_json(
        archive_manifest.parent / "control" / "artifact_registry.json",
        {"schema_version": "multi-agent-brief-artifact-registry/v1", "artifacts": {"tampered": {}}},
    )

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_invalid_run_record(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    run_record = tmp_path / "bad.run_record.json"
    _write_json(run_record, {"schema_version": "mabw.experiment_080.run_record.v1"})
    scorecard_path = tmp_path / "bad.scorecard.json"

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_RECORD_INVALID"
    assert not scorecard_path.exists()
