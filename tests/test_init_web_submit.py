"""Submission semantics for the init web wizard (single bootstrap authority)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from io import BytesIO
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
from multi_agent_brief.product.init_web.staging import InitWebStaging
from multi_agent_brief.product.workspace_hygiene import (
    NestedWorkspaceTargetError,
    canonical_workspace_target,
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
    source_metadata: list[dict[str, object]] = []
    bindings: list[dict[str, object]] = []
    for index in range(members):
        content = f"public source {index}\n".encode()
        staged = submitter.stage_upload(
            session_id="init-session",
            filename=f"source-{index:03d}.txt",
            stream=BytesIO(content),
            declared_length=len(content),
        )
        source_id = f"SRC-{index + 1:03d}"
        incident = index == 14 and members >= 15
        source_metadata.append(
            {
                "source_id": source_id,
                "expected_content_sha256": staged["sha256"],
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "provider": None,
                "original_url": f"https://example.com/{index}",
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
                "metadata_index": index,
                "upload_handle": str(staged["upload_handle"]),
            }
        )
    preview = submitter.preview_source_manifest(
        session_id="init-session",
        body={
            "source_manifest_mode": "imported",
            "source_metadata": source_metadata,
            "upload_bindings": bindings,
        },
    )
    payload.update(
        {
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "source_manifest_mode": "imported",
            "source_metadata": preview["source_metadata"],
            "source_manifest": preview["source_manifest"],
            "upload_session_id": "init-session",
            "upload_bindings": preview["routing_bindings"],
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
    config = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    assert config["output"]["html_report"]["auto_open"] is False
    assert (workspace / ".codex" / "config.toml").is_file()
    assert (workspace / "briefloop.db").is_file()
    expected_receipt_id = derived_id(
        "REQ-CX-INIT", response["workspace_id"], response["run_id"]
    )
    assert response["transaction_id"] == expected_receipt_id
    assert response["committed_revision"] >= 1
    assert response["execution_authorized"] is False
    assert response["completion_target"] is None
    assert response["repair_budget"] is None
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


def test_public_web_submission_stores_tavily_key_outside_run_contract(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-WEB00001", "web-search-ws")
    payload = body["payload"]
    assert isinstance(payload, dict)
    selections = payload["selections"]
    assert isinstance(selections, dict)
    selections.update(
        {
            "source_profile": "llm_decide",
            "web_search_mode": "external_api",
            "search_backend": "tavily",
        }
    )
    payload["search_secret_session_id"] = "web-session"
    configured = submitter.configure_search_secret(
        session_id="web-session",
        body={"provider": "tavily", "api_key": "tvly-test-secret-123"},
    )
    assert configured == {
        "ok": True,
        "provider": "tavily",
        "api_key_env": "TAVILY_API_KEY",
        "configured": True,
    }

    response = _submit_ok(submitter, body)

    workspace = tmp_path / "web-search-ws"
    secret_path = workspace / ".env"
    assert secret_path.read_text(encoding="utf-8") == (
        "TAVILY_API_KEY=tvly-test-secret-123\n"
    )
    assert secret_path.stat().st_mode & 0o777 == 0o600
    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["source_strategy"]["profile"] == "llm_decide"
    assert sources["web_search"]["mode"] == "external_api"
    assert sources["web_search"]["backend"] == "tavily"
    assert sources["web_search"]["api_key_env"] == "TAVILY_API_KEY"
    assert response["execution_authorized"] is False
    assert response["source_discovery"] == {
        "mode": "automatic",
        "profile": "llm_decide",
        "backend": "tavily",
        "api_key_env": "TAVILY_API_KEY",
    }
    assert "tvly-test-secret-123" not in json.dumps(response)
    config_text = (workspace / "config.yaml").read_text(encoding="utf-8")
    assert "tvly-test-secret-123" not in config_text
    assert b"tvly-test-secret-123" not in (workspace / "briefloop.db").read_bytes()


def test_public_web_submission_requires_secret_before_workspace_write(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _body("REQ-WEB00002", "web-search-ws")
    payload = body["payload"]
    assert isinstance(payload, dict)
    selections = payload["selections"]
    assert isinstance(selections, dict)
    selections.update(
        {
            "source_profile": "llm_decide",
            "web_search_mode": "external_api",
            "search_backend": "tavily",
        }
    )
    payload["search_secret_session_id"] = "web-session"

    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(body)

    assert exc_info.value.error_code == "submission_search_api_key_required"
    assert not (tmp_path / "web-search-ws").exists()


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
    assert response["execution_authorized"] is True
    assert response["completion_target"] == "finalized_local"
    assert response["repair_budget"] == 1
    assert (workspace / "input" / "execution-source-manifest.json").is_file()
    config = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    authorization = config["controlstore_v2"]["execution_authorization"]
    assert authorization["completion_target"] == "finalized_local"
    assert authorization["source_manifest_member_count"] == 1
    assert config["output"]["html_report"]["auto_open"] is True


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
            "source_manifest_mode": payload["source_manifest_mode"],
            "source_metadata": payload["source_metadata"],
            "upload_bindings": payload["upload_bindings"],
        },
    )

    assert preview["ok"] is True
    assert preview["member_count"] == 1
    assert len(str(preview["source_manifest_sha256"])) == 64
    assert preview["routing_bindings"] == payload["upload_bindings"]
    observed = preview["source_preview"][0]
    assert observed["observed_filename"] == "source-000.txt"
    assert observed["observed_sha256"] == payload["source_metadata"][0][
        "expected_content_sha256"
    ]
    assert observed["byte_count"] == len(b"public source 0\n")
    assert not (tmp_path / "authorized-ws").exists()

    bindings = payload["upload_bindings"]
    assert isinstance(bindings, list)
    handle = bindings[0]["upload_handle"]
    staged = submitter._staging._uploads[handle]
    replacement = staged.path.with_name(staged.path.name + "-replacement")
    replacement.write_bytes(staged.path.read_bytes())
    replacement.replace(staged.path)
    with pytest.raises(SubmissionError) as exc_info:
        submitter.preview_source_manifest(
            session_id="init-session",
            body={
                "source_manifest_mode": payload["source_manifest_mode"],
                "source_metadata": payload["source_metadata"],
                "upload_bindings": payload["upload_bindings"],
            },
        )
    assert exc_info.value.error_code in {
        "init_web_source_handle_invalid",
        "init_web_source_hash_mismatch",
    }
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
    assert [item["source_id"] for item in stored["members"]] == [
        f"SRC-{index:03d}" for index in range(1, 26)
    ]
    assert [item["locator"]["url"] for item in stored["members"]] == [
        f"https://example.com/{index}" for index in range(25)
    ]
    assert stored["members"][14]["locator"] == {
        "kind": "web",
        "url": "https://example.com/14",
    }
    assert stored["members"][14]["document_kind"] == "status_incident"
    assert stored["members"][14]["opened_at"] == "2026-07-21T00:00:00Z"


@pytest.mark.parametrize("member_count", [2, 25, 256])
def test_generated_manifest_maps_every_member_once_with_stable_server_ids(
    tmp_path: Path,
    member_count: int,
) -> None:
    projections: list[list[tuple[str, str, str, str]]] = []
    for suffix, reverse in (("a", False), ("b", True)):
        submitter = InitWebSubmitter(base_dir=tmp_path / suffix)
        uploads = []
        inputs = [
            (f"source-{index:03d}.txt", f"content-{index:03d}".encode())
            for index in range(member_count)
        ]
        if reverse:
            inputs.reverse()
        for filename, content in inputs:
            uploads.append(
                submitter.stage_upload(
                    session_id="session",
                    filename=filename,
                    stream=BytesIO(content),
                    declared_length=len(content),
                )
            )
        metadata = [
            {
                "title": upload["filename"],
                "published_at": None,
            }
            for upload in uploads
        ]
        preview = submitter.preview_source_manifest(
            session_id="session",
            body={
                "source_manifest_mode": "generated",
                "source_metadata": metadata,
                "upload_bindings": [
                    {"metadata_index": index, "upload_handle": upload["upload_handle"]}
                    for index, upload in enumerate(uploads)
                ],
            },
        )
        assert len(preview["routing_bindings"]) == member_count
        assert len({item["upload_handle"] for item in preview["routing_bindings"]}) == member_count
        projections.append(
            [
                (
                    item["source_id"],
                    item["input_path"],
                    item["title"],
                    item["content_sha256"],
                )
                for item in preview["source_manifest"]["members"]
            ]
        )
        assert all(
            item["published_at"] is None
            for item in preview["source_manifest"]["members"]
        )
    assert projections[0] == projections[1]
    assert [item[0] for item in projections[0]] == [
        f"SRC-INIT-{index:03d}" for index in range(1, member_count + 1)
    ]


@pytest.mark.parametrize(
    ("forbidden_key", "value"),
    [
        ("source_id", "SRC-CLIENT-001"),
        ("input_path", "input/client.txt"),
        ("locator", {"kind": "file", "path": "input/client.txt"}),
        ("content_sha256", "0" * 64),
        ("expected_content_sha256", "0" * 64),
        ("retrieved_at", "2026-07-23T00:00:00Z"),
        ("origin_type", "uploaded_file"),
    ],
)
def test_generated_manifest_rejects_imported_or_server_derived_fields(
    tmp_path: Path,
    forbidden_key: str,
    value: object,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    staged = submitter.stage_upload(
        session_id="session",
        filename="source.txt",
        stream=BytesIO(b"source"),
        declared_length=6,
    )
    metadata: dict[str, object] = {"title": "Source", forbidden_key: value}

    with pytest.raises(SubmissionError, match="init_web_source_manifest_invalid"):
        submitter.preview_source_manifest(
            session_id="session",
            body={
                "source_manifest_mode": "generated",
                "source_metadata": [metadata],
                "upload_bindings": [
                    {
                        "metadata_index": 0,
                        "upload_handle": staged["upload_handle"],
                    }
                ],
            },
        )


def test_generated_preview_routing_is_reused_for_exact_first_commit(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    uploads = [
        submitter.stage_upload(
            session_id="session",
            filename=filename,
            stream=BytesIO(content),
            declared_length=len(content),
        )
        for filename, content in (("zeta.txt", b"zeta"), ("alpha.txt", b"alpha"))
    ]
    preview = submitter.preview_source_manifest(
        session_id="session",
        body={
            "source_manifest_mode": "generated",
            "source_metadata": [
                {"title": "Same source", "publisher": None},
                {"title": "Same source"},
            ],
            "upload_bindings": [
                {
                    "metadata_index": index,
                    "upload_handle": upload["upload_handle"],
                }
                for index, upload in enumerate(uploads)
            ],
        },
    )
    body = _body(
        "REQ-GENERATED-COMMIT",
        "generated-ws",
        completion_target="finalized_local",
        repair_budget=1,
        source_manifest_mode="generated",
        source_metadata=preview["source_metadata"],
        source_manifest=preview["source_manifest"],
        upload_session_id="session",
        upload_bindings=preview["routing_bindings"],
    )

    response = _submit_ok(submitter, body)

    assert response["execution_authorized"] is True
    stored = json.loads(
        (tmp_path / "generated-ws" / "input/execution-source-manifest.json").read_text()
    )
    assert stored == preview["source_manifest"]
    assert [member["source_id"] for member in stored["members"]] == [
        "SRC-INIT-001",
        "SRC-INIT-002",
    ]


def test_generated_optional_omission_and_null_normalize_identically(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    staged = submitter.stage_upload(
        session_id="session",
        filename="source.txt",
        stream=BytesIO(b"source"),
        declared_length=6,
    )
    binding = [{"metadata_index": 0, "upload_handle": staged["upload_handle"]}]

    omitted = submitter.preview_source_manifest(
        session_id="session",
        body={
            "source_manifest_mode": "generated",
            "source_metadata": [{"title": "Source"}],
            "upload_bindings": binding,
        },
    )
    explicit_null = submitter.preview_source_manifest(
        session_id="session",
        body={
            "source_manifest_mode": "generated",
            "source_metadata": [{"title": "Source", "publisher": None}],
            "upload_bindings": binding,
        },
    )

    assert omitted["source_metadata"] == explicit_null["source_metadata"]
    assert omitted["source_manifest"] == explicit_null["source_manifest"]


def test_generated_canonical_manifest_ignores_upload_and_binding_order(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    uploads = [
        submitter.stage_upload(
            session_id="session",
            filename=f"source-{index}.txt",
            stream=BytesIO(f"content-{index}".encode()),
            declared_length=len(f"content-{index}".encode()),
        )
        for index in range(3)
    ]

    def _preview(ordered: list[dict[str, object]]) -> dict[str, object]:
        return submitter.preview_source_manifest(
            session_id="session",
            body={
                "source_manifest_mode": "generated",
                "source_metadata": [
                    {"title": str(upload["filename"])} for upload in ordered
                ],
                "upload_bindings": [
                    {
                        "metadata_index": index,
                        "upload_handle": upload["upload_handle"],
                    }
                    for index, upload in enumerate(ordered)
                ],
            },
        )

    forward = _preview(uploads)
    reverse = _preview(list(reversed(uploads)))

    assert forward["source_manifest"] == reverse["source_manifest"]
    assert forward["source_metadata"] == reverse["source_metadata"]


def test_generated_normalized_duplicate_is_rejected(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    uploads = [
        submitter.stage_upload(
            session_id="session",
            filename="same.txt",
            stream=BytesIO(b"same"),
            declared_length=4,
        )
        for _index in range(2)
    ]

    with pytest.raises(SubmissionError, match="init_web_source_manifest_invalid"):
        submitter.preview_source_manifest(
            session_id="session",
            body={
                "source_manifest_mode": "generated",
                "source_metadata": [
                    {"title": "Same"},
                    {"title": "Same", "publisher": None},
                ],
                "upload_bindings": [
                    {
                        "metadata_index": index,
                        "upload_handle": upload["upload_handle"],
                    }
                    for index, upload in enumerate(uploads)
                ],
            },
        )


def test_imported_manifest_expected_hash_must_match_staged_observation(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    staged = submitter.stage_upload(
        session_id="session",
        filename="source.txt",
        stream=BytesIO(b"source"),
        declared_length=6,
    )
    metadata = {
        "source_id": "SRC-001",
        "expected_content_sha256": "0" * 64,
        "title": "Source",
        "retrieved_at": "2026-07-23T00:00:00Z",
    }

    with pytest.raises(SubmissionError, match="init_web_source_hash_mismatch"):
        submitter.preview_source_manifest(
            session_id="session",
            body={
                "source_manifest_mode": "imported",
                "source_metadata": [metadata],
                "upload_bindings": [
                    {
                        "metadata_index": 0,
                        "upload_handle": staged["upload_handle"],
                    }
                ],
            },
        )


def test_browser_hash_rewrite_fails_before_store_commit(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _authorized_body(
        submitter, request_id="REQ-BROWSER-HASH", target="authorized-ws"
    )
    payload = body["payload"]
    payload["source_manifest"]["members"][0]["content_sha256"] = "0" * 64

    with pytest.raises(SubmissionError, match="submission_source_manifest_invalid"):
        submitter.submit(body)

    assert not (tmp_path / "authorized-ws" / "briefloop.db").exists()


@pytest.mark.parametrize("phase", ["copy", "post_copy"])
def test_source_mutation_during_or_after_copy_fails_before_store_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    body = _authorized_body(
        submitter, request_id=f"REQ-MUTATE-{phase}", target=f"ws-{phase}"
    )
    payload = body["payload"]
    binding = payload["upload_bindings"][0]
    staged = submitter._staging._uploads[binding["upload_handle"]]
    if phase == "copy":
        original_open = InitWebStaging._open_verified
        open_calls = 0

        def _mutate_after_open(expected_sha256, selected):
            nonlocal open_calls
            open_calls += 1
            descriptor = original_open(expected_sha256, selected)
            if open_calls == 3:
                selected.path.write_bytes(b"Y" * selected.byte_count)
            return descriptor

        monkeypatch.setattr(
            InitWebStaging, "_open_verified", staticmethod(_mutate_after_open)
        )
    else:
        original_verify = InitWebStaging._verify_materialized

        def _mutate_before_post_copy(destination, selected, member):
            destination.write_bytes(b"Z" * selected.byte_count)
            return original_verify(destination, selected, member)

        monkeypatch.setattr(
            InitWebStaging,
            "_verify_materialized",
            staticmethod(_mutate_before_post_copy),
        )

    with pytest.raises(SubmissionError, match="init_web_source_hash_mismatch"):
        submitter.submit(body)

    assert not (tmp_path / f"ws-{phase}" / "briefloop.db").exists()


@pytest.mark.parametrize("changed", [False, True])
def test_same_target_concurrent_submission_is_linearized_before_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    first_body = _authorized_body(
        submitter, request_id="REQ-CONCURRENT", target="authorized-ws"
    )
    second_body = deepcopy(first_body)
    if changed:
        second_payload = second_body["payload"]
        second_payload["source_metadata"][0]["title"] = "Changed source title"
        preview = submitter.preview_source_manifest(
            session_id="init-session",
            body={
                "source_manifest_mode": "imported",
                "source_metadata": second_payload["source_metadata"],
                "upload_bindings": second_payload["upload_bindings"],
            },
        )
        second_payload["source_manifest"] = preview["source_manifest"]

    entered = __import__("threading").Event()
    release = __import__("threading").Event()
    original = submitter._staging.materialize_canonical
    materializations = 0

    def _pause_winner(**kwargs):
        nonlocal materializations
        materializations += 1
        entered.set()
        assert release.wait(timeout=5)
        return original(**kwargs)

    monkeypatch.setattr(submitter._staging, "materialize_canonical", _pause_winner)
    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(submitter.submit, first_body)
        assert entered.wait(timeout=5)
        loser = executor.submit(submitter.submit, second_body)
        release.set()
        first = winner.result(timeout=20)
        if changed:
            with pytest.raises(SubmissionError, match="submission_replay_conflict"):
                loser.result(timeout=20)
        else:
            second = loser.result(timeout=20)
            assert {first[1]["status"], second[1]["status"]} == {
                "committed",
                "replayed",
            }
            assert first[1]["transaction_id"] == second[1]["transaction_id"]
    assert materializations == 1
    assert _revision(tmp_path / "authorized-ws") == 1


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


def test_target_nested_below_existing_workspace_rejects_before_writes(
    tmp_path: Path,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    _submit_ok(submitter, _body("REQ-OUTER-0001", "outer"))
    outer = tmp_path / "outer"
    revision_before = _revision(outer)
    nested = outer / "nested"

    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(_body("REQ-NESTED-001", "outer/nested"))

    assert exc_info.value.error_code == "workspace_target_nested"
    assert exc_info.value.http_status == 409
    assert not nested.exists()
    assert _revision(outer) == revision_before


def test_init_web_rejects_outside_alias_into_workspace_before_source_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    _submit_ok(submitter, _body("REQ-WEB-ALIAS-OUTER", "outer"))
    outer = tmp_path / "outer"
    revision_before = _revision(outer)
    alias = tmp_path / "outside-alias"
    try:
        alias.symlink_to(outer / "input" / "context", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")

    def forbidden_source_access(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("source handles must not be accessed")

    monkeypatch.setattr(
        submitter._staging,
        "canonical_manifest",
        forbidden_source_access,
    )
    body = _body(
        "REQ-WEB-ALIAS-INTO-WORKSPACE",
        "outside-alias/nested",
        source_manifest={"untrusted": True},
        source_manifest_mode="generated",
        upload_session_id="never-read",
        upload_bindings=[{"upload_handle": "never-read"}],
    )

    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(body)

    assert exc_info.value.error_code == "workspace_target_nested"
    assert exc_info.value.http_status == 409
    assert not (outer / "input" / "context" / "nested").exists()
    assert _revision(outer) == revision_before


def test_init_web_rejects_lexically_nested_alias_outward_before_source_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    _submit_ok(submitter, _body("REQ-WEB-LEXICAL-OUTER", "outer"))
    outer = tmp_path / "outer"
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = outer / "input" / "context" / "outward"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")

    def forbidden_source_access(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("source handles must not be accessed")

    monkeypatch.setattr(
        submitter._staging,
        "canonical_manifest",
        forbidden_source_access,
    )
    body = _body(
        "REQ-WEB-LEXICAL-OUTWARD",
        "outer/input/context/outward/nested",
        source_manifest={"untrusted": True},
        source_manifest_mode="generated",
        upload_session_id="never-read",
        upload_bindings=[{"upload_handle": "never-read"}],
    )

    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit(body)

    assert exc_info.value.error_code == "workspace_target_nested"
    assert exc_info.value.http_status == 409
    assert not (outside / "nested").exists()


def test_direct_create_workspace_rejects_alias_into_existing_workspace(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "outer"
    profile = _profile_from_payload(_body("REQ-DIRECT-OUTER", "outer")["payload"])
    create_workspace(outer, profile)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(outer / "input" / "context", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")

    with pytest.raises(NestedWorkspaceTargetError):
        create_workspace(alias / "nested", profile)

    assert not (outer / "input" / "context" / "nested").exists()


def test_canonical_target_remains_bound_after_input_alias_replacement(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(original, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    canonical = canonical_workspace_target(alias / "workspace")
    alias.unlink()
    alias.symlink_to(replacement, target_is_directory=True)
    profile = _profile_from_payload(_body("REQ-ALIAS-SWAP", "workspace")["payload"])

    create_workspace(canonical, profile)

    assert (original / "workspace" / "config.yaml").exists()
    assert not (replacement / "workspace").exists()


def test_malformed_body_is_rejected(tmp_path: Path) -> None:
    submitter = InitWebSubmitter(base_dir=tmp_path)
    with pytest.raises(SubmissionError) as exc_info:
        submitter.submit({"schema_version": "wrong"})
    assert exc_info.value.error_code == "submission_payload_invalid"
