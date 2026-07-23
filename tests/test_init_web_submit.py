"""Submission semantics for the init web wizard (single bootstrap authority)."""

from __future__ import annotations

import json
from io import BytesIO
import hashlib
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
from multi_agent_brief.runtime_host_v2.initialization import WorkspaceBootstrap


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
            "output_extent": "balanced",
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


def _authorized_body(
    submitter: InitWebSubmitter,
    *,
    request_id: str,
    target: str,
    members: int = 1,
) -> dict[str, object]:
    body = _body(request_id, target)
    payload = body["payload"]
    assert isinstance(payload, dict)
    manifest_members: list[dict[str, object]] = []
    bindings: list[dict[str, str]] = []
    for index in range(members):
        content = f"public source {index}\n".encode()
        staged = submitter.stage_upload(
            session_id="init-session",
            filename=f"source-{index:03d}.txt",
            stream=BytesIO(content),
            declared_length=len(content),
        )
        source_id = f"SRC-INIT-{index + 1:03d}"
        input_path = f"input/sources/{index + 1:03d}-source.txt"
        incident = index == members - 1 and members > 1
        manifest_members.append(
            {
                "source_id": source_id,
                "input_path": input_path,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_media_type": "text/plain",
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "provider": None,
                "locator": {
                    "kind": "web" if incident else "file",
                    **(
                        {"url": f"https://example.com/{index}"}
                        if incident
                        else {"path": input_path}
                    ),
                },
                "title": f"Public source {index}",
                "publisher": "Example publisher",
                "published_at": None if incident else "2026-07-22",
                "retrieved_at": "2026-07-23T00:00:00Z",
                "source_category": "other",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "unknown",
                "raw_underlying_evidence_type": None,
                "document_kind": "status_incident" if incident else None,
                "opened_at": "2026-07-21T00:00:00Z" if incident else None,
                "resolved_at": None,
            }
        )
        bindings.append(
            {
                "input_path": input_path,
                "upload_handle": str(staged["upload_handle"]),
            }
        )
    payload.update(
        {
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "source_manifest": {
                "schema_version": "briefloop.execution_source_manifest.v2",
                "members": manifest_members,
            },
            "upload_session_id": "init-session",
            "upload_bindings": bindings,
        }
    )
    return body


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


def test_authorized_submission_freezes_manifest_and_returns_first_action(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _authorized_body(
        submitter,
        request_id="REQ-AUTH0001",
        target="authorized-ws",
    )

    response = _submit_ok(submitter, body)

    workspace = tmp_path / "authorized-ws"
    assert response["next_action"]["effect_kind"] == "doctor_check"
    assert (workspace / "input" / "execution-source-manifest.json").is_file()
    config = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    authorization = config["controlstore_v2"]["execution_authorization"]
    assert authorization["completion_target"] == "finalized_local"
    assert authorization["source_manifest_member_count"] == 1


def test_source_manifest_preview_is_server_canonical_and_zero_workspace_write(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _authorized_body(
        submitter,
        request_id="REQ-PREVIEW01",
        target="authorized-ws",
    )
    payload = body["payload"]
    assert isinstance(payload, dict)

    preview = submitter.preview_source_manifest(
        session_id="init-session",
        body={
            "source_manifest": payload["source_manifest"],
            "upload_bindings": payload["upload_bindings"],
        },
    )

    assert preview["ok"] is True
    assert preview["member_count"] == 1
    assert len(str(preview["source_manifest_sha256"])) == 64
    assert not (tmp_path / "authorized-ws").exists()

    manifest = payload["source_manifest"]
    assert isinstance(manifest, dict)
    members = manifest["members"]
    assert isinstance(members, list)
    members[0]["content_sha256"] = "0" * 64
    with pytest.raises(SubmissionError) as exc_info:
        submitter.preview_source_manifest(
            session_id="init-session",
            body={
                "source_manifest": manifest,
                "upload_bindings": payload["upload_bindings"],
            },
        )
    assert exc_info.value.error_code == "init_web_source_hash_mismatch"
    assert not (tmp_path / "authorized-ws").exists()


def test_authorized_replay_precedes_deleted_staging_and_handle_lookup(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _authorized_body(
        submitter,
        request_id="REQ-AUTH0002",
        target="authorized-ws",
    )
    first = _submit_ok(submitter, body)
    submitter.close()

    restarted = InitWebSubmitter(base_dir=tmp_path)
    payload = body["payload"]
    assert isinstance(payload, dict)
    bindings = payload["upload_bindings"]
    assert isinstance(bindings, list)
    bindings[0]["upload_handle"] = "upload-routing-handle-can-change"
    second = _submit_ok(restarted, body)

    assert second["status"] == "replayed"
    assert second["receipt"] == first["receipt"]


def test_authorized_changed_semantic_manifest_conflicts_before_source_reads(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _authorized_body(
        submitter,
        request_id="REQ-AUTH0003",
        target="authorized-ws",
    )
    _submit_ok(submitter, body)
    submitter.close()
    payload = body["payload"]
    assert isinstance(payload, dict)
    manifest = payload["source_manifest"]
    assert isinstance(manifest, dict)
    members = manifest["members"]
    assert isinstance(members, list)
    members[0]["title"] = "Changed confirmed title"

    with pytest.raises(SubmissionError) as exc_info:
        InitWebSubmitter(base_dir=tmp_path).submit(body)

    assert exc_info.value.error_code == "submission_replay_conflict"


def test_authorized_25_member_manifest_preserves_url_and_incident_semantics(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _authorized_body(
        submitter,
        request_id="REQ-AUTH0025",
        target="authorized-ws",
        members=25,
    )

    _submit_ok(submitter, body)

    stored = json.loads(
        (tmp_path / "authorized-ws" / "input" / "execution-source-manifest.json").read_text()
    )
    assert len(stored["members"]) == 25
    assert stored["members"][-1]["locator"] == {
        "kind": "web",
        "url": "https://example.com/24",
    }
    assert stored["members"][-1]["document_kind"] == "status_incident"
    assert stored["members"][-1]["opened_at"] == "2026-07-21T00:00:00Z"


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

    restarted = InitWebSubmitter(base_dir=tmp_path)
    with pytest.raises(SubmissionError) as exc_info:
        restarted.submit(body)
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

    restarted = InitWebSubmitter(base_dir=tmp_path)
    status, second = restarted.submit(body)
    assert status == 200
    assert second["status"] == "replayed"
    assert second["workspace_id"] == first["workspace_id"]
    assert second["run_id"] == first["run_id"]
    assert second["transaction_id"] == first["transaction_id"]
    assert second["committed_revision"] == first["committed_revision"]
    assert second["receipt"] == first["receipt"]
    assert _revision(workspace) == revision_before


def test_fresh_and_existing_submit_targets_use_bootstrap_facade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    original = WorkspaceBootstrap.classify_target

    def _recording_classification(self: WorkspaceBootstrap) -> str:
        authority_kind = original(self)
        observed.append(authority_kind)
        return authority_kind

    monkeypatch.setattr(
        WorkspaceBootstrap, "classify_target", _recording_classification
    )
    body = _body("REQ-AAAA0012", "web-ws")
    first = _submit_ok(InitWebSubmitter(base_dir=tmp_path), body)
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)

    second = _submit_ok(InitWebSubmitter(base_dir=tmp_path), body)

    assert first["status"] == "committed"
    assert second["status"] == "replayed"
    assert second["receipt"] == first["receipt"]
    assert _revision(workspace) == revision_before
    assert "fresh" in observed
    assert "sqlite" in observed


def test_same_request_id_with_different_payload_conflicts_with_zero_writes(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-AAAA0004", "web-ws")
    _submit_ok(submitter, body)
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)

    changed = _body("REQ-AAAA0004", "web-ws")
    changed_payload = changed["payload"]
    assert isinstance(changed_payload, dict)
    changed_selections = changed_payload["selections"]
    assert isinstance(changed_selections, dict)
    changed_selections["task_objective"] = "Prepare a different confirmed brief."
    restarted = InitWebSubmitter(base_dir=tmp_path)
    with pytest.raises(SubmissionError) as exc_info:
        restarted.submit(changed)
    assert exc_info.value.error_code == "submission_replay_conflict"
    assert exc_info.value.http_status == 409
    assert _revision(workspace) == revision_before


def test_unrelated_request_to_initialized_target_is_not_replay(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    _submit_ok(submitter, _body("REQ-AAAA0011", "web-ws"))
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)

    restarted = InitWebSubmitter(base_dir=tmp_path)
    with pytest.raises(SubmissionError) as exc_info:
        restarted.submit(_body("REQ-BBBB0011", "web-ws"))
    assert exc_info.value.error_code == "workspace_target_exists"
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


@pytest.mark.parametrize("extent", ["unknown", None])
def test_unknown_output_extent_is_rejected_before_workspace_writes(
    tmp_path: Path,
    extent: object,
) -> None:
    body = _body("REQ-OUTPUT-EXTENT", "web-ws")
    body["payload"]["selections"]["output_extent"] = extent  # type: ignore[index]

    with pytest.raises(SubmissionError, match="submission_output_extent_invalid"):
        InitWebSubmitter(base_dir=tmp_path).submit(body)

    assert not (tmp_path / "web-ws").exists()


def test_output_extent_is_store_frozen_and_part_of_replay_identity(
    tmp_path: Path,
) -> None:
    body = _body("REQ-OUTPUT-EXTENT-REPLAY", "web-ws")
    body["payload"]["selections"]["output_language"] = "en"  # type: ignore[index]
    first = _submit_ok(InitWebSubmitter(base_dir=tmp_path), body)
    workspace = tmp_path / "web-ws"
    revision_before = _revision(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        binding = store.load_snapshot(first["run_id"]).run_contract_bindings[0]
    assert binding.run_direction.output_contract is not None
    assert binding.run_direction.output_contract.output_extent == "balanced"
    assert binding.run_direction.output_contract.resolved_minimum == 600
    assert binding.run_direction.output_contract.resolved_maximum == 800

    changed = _body("REQ-OUTPUT-EXTENT-REPLAY", "web-ws")
    changed["payload"]["selections"]["output_language"] = "en"  # type: ignore[index]
    changed["payload"]["selections"]["output_extent"] = "detailed"  # type: ignore[index]
    with pytest.raises(SubmissionError, match="submission_replay_conflict"):
        InitWebSubmitter(base_dir=tmp_path).submit(changed)
    assert _revision(workspace) == revision_before


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
