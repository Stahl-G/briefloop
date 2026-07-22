"""Submission semantics for the init web wizard (single bootstrap authority)."""

from __future__ import annotations

import json
import yaml

from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.core_run_v2.errors import CoreRunResult
from multi_agent_brief.core_run_v2.service import CoreRunService
from multi_agent_brief.product.init_web.submit import (
    SUBMISSION_SCHEMA,
    InitWebSubmitter,
    SubmissionError,
    _profile_from_payload,
)
from multi_agent_brief.runtime_assets import RuntimeAssetInstallError


def _body(request_id: str, target: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workspace_target": target,
        "selections": {
            "company": "ExampleCo",
            "industry_or_theme": "manufacturing",
            "task_objective": "Prepare the weekly manufacturing brief.",
            "brief_title": "ExampleCo weekly brief",
            "audience": "management",
            "interface_language": "zh",
            "output_language": "zh",
            "cadence": "weekly",
            "focus_areas": ["operations", "policy"],
            "output_formats": ["markdown", "docx"],
            "forbidden_sources": [],
            "web_search_mode": "disabled",
        },
        "raw_free_text": "weekly manufacturing brief for management",
        "discarded": [],
        "human_confirmation": True,
    }
    payload.update(overrides)
    return {
        "schema_version": SUBMISSION_SCHEMA,
        "request_id": request_id,
        "payload": payload,
    }


def _revision(workspace: Path) -> int:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        head = store.load_workspace_run_head()
        assert head is not None
        return store.load_snapshot(head.current_run_id).store_revision


def _submit_ok(
    submitter: InitWebSubmitter, body: dict[str, object]
) -> dict[str, object]:
    status, response = submitter.submit(body)
    assert status == 200
    assert response["ok"] is True
    return response


def test_committed_submission_creates_runnable_workspace_and_real_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0001", "web-ws")
    response = _submit_ok(submitter, body)

    assert response["status"] == "committed"
    workspace = tmp_path / "web-ws"
    assert (workspace / "config.yaml").is_file()
    assert (workspace / ".codex" / "config.toml").is_file()
    assert (workspace / "briefloop.db").is_file()
    expected_receipt_id = derived_id(
        "REQ-CX-INIT", response["workspace_id"], response["run_id"]
    )
    assert response["transaction_id"] == expected_receipt_id
    assert response["committed_revision"] >= 1
    receipt = response["receipt"]
    assert receipt["transaction_id"] == expected_receipt_id
    assert receipt["run_id"] == response["run_id"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        stored = store.load_transaction_receipt(response["run_id"], expected_receipt_id)
    assert stored is not None
    assert stored.transaction_id == response["transaction_id"]
    revision_before = _revision(workspace)
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["run_id"] == response["run_id"]
    assert _revision(workspace) == revision_before


def test_kit_materialization_failure_never_commits_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_install(**_kwargs: object) -> dict[str, object]:
        raise RuntimeAssetInstallError("injected")

    monkeypatch.setattr(
        "multi_agent_brief.runtime_host_v2.initialization.install_runtime_kit",
        _fail_install,
    )
    submitter = InitWebSubmitter(base_dir=tmp_path)
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(_body("REQ-AAAA0008", "web-ws"))
    assert exc_info.value.error_code == "runtime_adapter_binding_mismatch"
    workspace = tmp_path / "web-ws"
    assert (workspace / "config.yaml").is_file()
    assert not (workspace / "briefloop.db").exists()


def test_replay_verifies_existing_store_kit_without_reinstall(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0009", "web-ws")
    _submit_ok(submitter, body)
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)
    skill = workspace / ".codex" / "skills" / "briefloop" / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\n# drift\n")

    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(body)
    assert exc_info.value.error_code == "runtime_adapter_binding_mismatch"
    assert skill.read_bytes().endswith(b"\n# drift\n")
    assert _revision(workspace) == revision_before


def test_postcommit_unknown_resolves_by_exact_initialization_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_initialize = CoreRunService.initialize
    calls = 0

    def _unknown_once(service: CoreRunService, request) -> CoreRunResult:
        nonlocal calls
        result = original_initialize(service, request)
        calls += 1
        if calls == 1:
            assert result.status == "committed"
            return CoreRunResult(
                status="commit_outcome_unknown",
                error_code="commit_outcome_unknown",
            )
        return result

    monkeypatch.setattr(CoreRunService, "initialize", _unknown_once)
    submitter = InitWebSubmitter(base_dir=tmp_path)
    response = _submit_ok(submitter, _body("REQ-AAAA0010", "web-ws"))

    assert response["status"] == "committed"
    assert calls == 2
    assert _revision(tmp_path / "web-ws") == response["committed_revision"]


def test_web_workspace_matches_cli_init_authority_shape(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0002", "web-ws")
    response = _submit_ok(submitter, body)

    profile = _profile_from_payload(body["payload"])  # type: ignore[arg-type]
    cli_target = tmp_path / "cli-ws"
    create_workspace(cli_target, profile, force=False)

    def _bootstrap(path: Path) -> dict[str, object]:
        config = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
        bootstrap = config["controlstore_v2"]
        bootstrap["workspace_id"] = "<id>"
        bootstrap["run_id"] = "<id>"
        return bootstrap

    assert _bootstrap(tmp_path / "web-ws") == _bootstrap(cli_target)


def test_identical_resubmit_is_replayed_with_zero_writes(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0003", "web-ws")
    first = _submit_ok(submitter, body)
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)

    status, second = submitter.submit(body)
    assert status == 200
    assert second["status"] == "replayed"
    assert second["transaction_id"] == first["transaction_id"]
    assert second["committed_revision"] == first["committed_revision"]
    assert _revision(workspace) == revision_before


def test_same_request_id_with_different_payload_conflicts_with_zero_writes(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0004", "web-ws")
    _submit_ok(submitter, body)
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)

    changed = _body("REQ-AAAA0004", "web-ws", raw_free_text="changed mind")
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(changed)
    assert exc_info.value.error_code == "submission_replay_conflict"
    assert exc_info.value.http_status == 409
    assert _revision(workspace) == revision_before


def test_human_confirmation_is_required(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0005", "web-ws", human_confirmation=False)
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(body)
    assert exc_info.value.error_code == "human_confirmation_required"
    assert exc_info.value.http_status == 422
    assert not (tmp_path / "web-ws").exists()


def test_missing_required_selection_is_rejected(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0006", "web-ws")
    body["payload"]["selections"]["company"] = ""  # type: ignore[index]
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(body)
    assert exc_info.value.error_code == "submission_company_required"
    assert not (tmp_path / "web-ws").exists()


def test_existing_non_empty_target_conflicts(tmp_path: Path) -> None:
    target = tmp_path / "web-ws"
    target.mkdir()
    (target / "occupied.txt").write_text("x", encoding="utf-8")
    submitter = InitWebSubmitter(base_dir=tmp_path)
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(_body("REQ-AAAA0007", "web-ws"))
    assert exc_info.value.error_code == "workspace_target_exists"
    assert exc_info.value.http_status == 409


def test_malformed_body_is_rejected(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit({"schema_version": "wrong"})
    assert exc_info.value.error_code == "submission_payload_invalid"
