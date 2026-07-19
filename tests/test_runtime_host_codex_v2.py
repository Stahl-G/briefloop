from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.runtime_host_v2.codex import load_codex_adapter_binding
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.service import RuntimeHostService
from multi_agent_brief.workspace.init_profile import InitProfile


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    values = iter(("codex-workspace", "codex-run"))
    create_workspace(
        workspace,
        InitProfile(
            company="ExampleCo",
            industry="manufacturing",
            brief_title="ExampleCo brief",
            task_objective="Prepare the ExampleCo brief.",
            audience="management",
            audience_profile="management",
            focus_areas=["operations"],
            output_formats=["markdown"],
            web_search_mode="disabled",
            web_search_enabled=False,
        ),
        report_date_factory=lambda: date(2026, 7, 19),
        identity_factory=lambda: next(values),
    )
    return workspace


def _external_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "external-workspace"
    values = iter(("external-codex-workspace", "external-codex-run"))
    create_workspace(
        workspace,
        InitProfile(
            company="ExampleCo",
            industry="manufacturing",
            brief_title="ExampleCo brief",
            task_objective="Prepare the ExampleCo brief.",
            audience="management",
            audience_profile="management",
            focus_areas=["operations"],
            output_formats=["markdown"],
            web_search_mode="external_api",
            web_search_enabled=True,
            search_backend="tavily",
        ),
        report_date_factory=lambda: date(2026, 7, 19),
        identity_factory=lambda: next(values),
    )
    return workspace


def _cached_workspace(tmp_path: Path) -> Path:
    workspace = _workspace(tmp_path)
    cache = workspace / "input" / "cached-source.txt"
    cache.write_text(
        "Durable cached source content long enough for deterministic intake.\n",
        encoding="utf-8",
    )
    (workspace / "sources.yaml").write_text(
        """source_strategy:
  profile: conservative
  enabled_providers: [cached_package]
cached_package:
  enabled: true
  paths: [input/cached-source.txt]
  formats: [txt]
""",
        encoding="utf-8",
    )
    return workspace


def _current_action_path(workspace: Path, capsys) -> Path:
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    path = workspace / "runtime_action.json"
    path.write_text(json.dumps(action), encoding="utf-8")
    return path


def _apply_current(workspace: Path, capsys) -> int:
    action = _current_action_path(workspace, capsys)
    return main(
        [
            "runtime",
            "apply",
            "--workspace",
            str(workspace),
            "--action",
            str(action),
        ]
    )


def _start_current(workspace: Path, capsys) -> int:
    return main(
        [
            "runtime",
            "invocation-start",
            "--workspace",
            str(workspace),
        ]
    )


def _start_current_with_action(workspace: Path, capsys) -> int:
    action = _current_action_path(workspace, capsys)
    return main(
        [
            "runtime",
            "invocation-start",
            "--workspace",
            str(workspace),
            "--action",
            str(action),
        ]
    )


def _envelope_path(workspace: Path, envelope: dict[str, object]) -> Path:
    return workspace / str(envelope["scratch_directory"]) / "role_task_envelope.json"


def test_codex_run_initializes_store_and_returns_exact_action(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)

    rc = main(["run", "--workspace", str(workspace), "--runtime", "codex"])

    assert rc == 0
    action = json.loads(capsys.readouterr().out)
    assert action["run_id"] == "RUN-codex-run"
    assert action["stage_id"] == "doctor"
    assert action["effect_kind"] == "doctor_check"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == action["store_revision"]
        assert store.load_workspace_run_head().current_run_id == "RUN-codex-run"
    assert not (workspace / "output" / "intermediate" / "workflow_state.json").exists()


def test_stale_or_forged_action_file_cannot_start_invocation_or_write(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    doctor_action = json.loads(capsys.readouterr().out)
    action_path = workspace / "doctor_action.json"
    action_path.write_text(json.dumps(doctor_action), encoding="utf-8")
    assert (
        main(
            [
                "runtime",
                "apply",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision

    assert (
        main(
            [
                "runtime",
                "invocation-start",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
            ]
        )
        == 1
    )
    assert "runtime_action_stale" in capsys.readouterr().out
    forged = dict(doctor_action)
    forged["reason_code"] = "forged"
    action_path.write_text(json.dumps(forged), encoding="utf-8")
    assert (
        main(
            [
                "runtime",
                "invocation-start",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
            ]
        )
        == 1
    )
    assert "runtime_action_invalid" in capsys.readouterr().out
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision


@pytest.mark.parametrize("supply_action", [False, True])
def test_invocation_start_uses_exact_current_store_action(
    tmp_path: Path,
    capsys,
    supply_action: bool,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    expected_action = json.loads(capsys.readouterr().out)
    action_path = workspace / "expected_action.json"
    arguments = [
        "runtime",
        "invocation-start",
        "--workspace",
        str(workspace),
    ]
    if supply_action:
        action_path.write_text(json.dumps(expected_action), encoding="utf-8")
        arguments.extend(("--action", str(action_path)))

    assert main(arguments) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["action"] == expected_action
    assert envelope["role_id"] == "source-planner"


def test_symlinked_scratch_records_invocation_failure_without_external_write(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "scratch").symlink_to(outside, target_is_directory=True)

    assert (
        main(
            [
                "runtime",
                "invocation-start",
                "--workspace",
                str(workspace),
            ]
        )
        == 1
    )
    assert "runtime_envelope_materialization_failed" in capsys.readouterr().out
    assert list(outside.iterdir()) == []
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot("RUN-codex-run")
    assert len(snapshot.invocations) == 1
    assert snapshot.invocations[0].status == "failed"
    assert snapshot.invocations[0].failure_reason == "envelope_materialization_failed"


def test_existing_codex_run_does_not_reread_mutable_inputs(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    first = json.loads(capsys.readouterr().out)
    (workspace / "config.yaml").write_text("changed: true\n", encoding="utf-8")
    (workspace / "sources.yaml").write_text("changed: true\n", encoding="utf-8")

    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second == first


def test_existing_run_rejects_installed_adapter_drift(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    installed = load_codex_adapter_binding("RUN-codex-run")
    drifted = installed.model_copy(update={"adapter_version": "drifted"})
    host = RuntimeHostService(
        workspace,
        adapter_loader=lambda _run_id: drifted,
    )

    with pytest.raises(RuntimeHostError, match="runtime_adapter_binding_mismatch"):
        host.next_action()


def test_start_and_non_codex_runtime_do_not_mutate_sqlite_workspace(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    database = workspace / "briefloop.db"
    before = database.read_bytes()

    assert main(["start", "--workspace", str(workspace), "--runtime", "codex"]) == 1
    assert "runtime_command_unsupported" in capsys.readouterr().out
    assert main(["run", "--workspace", str(workspace), "--runtime", "operator"]) == 1
    assert "runtime_adapter_unsupported" in capsys.readouterr().out
    assert database.read_bytes() == before


def test_runtime_doctor_then_exact_source_planner_invocation(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()

    assert _apply_current(workspace, capsys) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "committed"
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["action_kind"] == "delegate"
    assert action["role_id"] == "source-planner"

    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["role_id"] == "source-planner"
    assert envelope["action"] == action
    assert envelope["executor_kind"] == "main_session"
    assert envelope["context_mode"] == "shared_session"
    assert envelope["review_mode"] == "stage_separated_self_review"
    assert envelope["dispatch_instruction"] == "execute_in_current_session"
    envelope_path = (
        workspace / envelope["scratch_directory"] / "role_task_envelope.json"
    )
    assert json.loads(envelope_path.read_text(encoding="utf-8")) == envelope

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision
    assert _start_current(workspace, capsys) == 1
    assert "runtime_action_not_invocable" in capsys.readouterr().out
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision


def test_cli_authority_guard_blocks_legacy_and_sqlite_legacy_commands(
    tmp_path: Path,
    capsys,
) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert main(["state", "init", "--runtime", "codex", "--workspace", str(fresh)]) == 1
    assert "runtime_command_unsupported" in capsys.readouterr().out
    assert list(fresh.iterdir()) == []

    legacy = tmp_path / "legacy"
    control = legacy / "output" / "intermediate" / "workflow_state.json"
    control.parent.mkdir(parents=True)
    control.write_text("{}\n", encoding="utf-8")
    before_legacy = control.read_bytes()
    assert main(["status", "--workspace", str(legacy), "--json"]) == 1
    assert "legacy_workspace_unsupported" in capsys.readouterr().out
    assert control.read_bytes() == before_legacy

    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    database = workspace / "briefloop.db"
    before_database = database.read_bytes()
    assert main(["state", "check", "--workspace", str(workspace)]) == 1
    assert "runtime_command_unsupported" in capsys.readouterr().out
    assert database.read_bytes() == before_database


def test_doctor_is_read_only_for_fresh_and_verified_sqlite_workspaces(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    config = workspace / "config.yaml"
    before_paths = sorted(
        path.relative_to(workspace).as_posix() for path in workspace.rglob("*")
    )

    assert main(["doctor", "--config", str(config)]) == 0
    capsys.readouterr()
    assert not (workspace / "briefloop.db").exists()
    assert (
        sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*"))
        == before_paths
    )

    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    database = workspace / "briefloop.db"
    before_database = database.read_bytes()
    with SQLiteControlStore.open(database) as store:
        before_revision = store.current_revision

    assert main(["doctor", "--config", str(config)]) == 0
    capsys.readouterr()
    assert database.read_bytes() == before_database
    with SQLiteControlStore.open(database) as store:
        assert store.current_revision == before_revision


def test_doctor_rejects_legacy_and_invalid_sqlite_without_writes(
    tmp_path: Path,
    capsys,
) -> None:
    legacy = tmp_path / "legacy"
    control = legacy / "output" / "intermediate" / "workflow_state.json"
    control.parent.mkdir(parents=True)
    control.write_text("{}\n", encoding="utf-8")
    before_legacy = control.read_bytes()
    assert main(["doctor", "--config", str(legacy / "config.yaml")]) == 1
    assert "legacy_workspace_unsupported" in capsys.readouterr().out
    assert control.read_bytes() == before_legacy
    assert not (legacy / "briefloop.db").exists()

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "briefloop.db").mkdir()
    before_paths = sorted(path.name for path in invalid.iterdir())
    assert main(["doctor", "--config", str(invalid / "config.yaml")]) == 1
    assert "control_store_integrity_invalid" in capsys.readouterr().out
    assert sorted(path.name for path in invalid.iterdir()) == before_paths


def test_explicit_strict_topology_never_falls_back_to_current_session(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    config_path = workspace / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["controlstore_v2"]["role_topology"] = "strict"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)

    assert envelope["executor_kind"] == "delegated_specialist"
    assert envelope["dispatch_instruction"] == "delegate_exact_role"
    assert envelope["context_mode"] == "independent_stage_context"
    assert envelope["review_mode"] == "independent_stage_context"


def test_source_planner_writes_only_artifact_and_host_derives_accept_request(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["allowed_output_filenames"] == ["source_candidates.yaml"]
    invocation_id = envelope["invocation_id"]
    scratch = workspace / "scratch" / invocation_id
    (scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: manual\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, envelope)),
            ]
        )
        == 0
    )
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["status"] == "committed"
    assert accepted["invocation_id"] == invocation_id
    assert accepted["next_action"]["stage_id"] == "source-discovery"
    host_request = json.loads(
        (scratch / "submit_request.json").read_text(encoding="utf-8")
    )
    assert host_request["invocation_id"] == invocation_id
    assert host_request["artifact_id"] == "source_candidates"
    assert host_request["input_path"] == (
        f"scratch/{invocation_id}/source_candidates.yaml"
    )

    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, envelope)),
            ]
        )
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "replayed"
    assert replay["transaction_id"] == accepted["transaction_id"]
    assert replay["store_revision"] == accepted["store_revision"]


def test_child_failure_is_value_free_recorded_and_exactly_replayed(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    invocation_id = envelope["invocation_id"]

    command = [
        "runtime",
        "invocation-fail",
        "--workspace",
        str(workspace),
        "--envelope",
        str(_envelope_path(workspace, envelope)),
        "--reason",
        "child_timed_out",
    ]
    assert main(command) == 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "rejected_recorded"
    assert failed["next_action"]["role_id"] == "source-planner"

    assert main(command) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "rejected_recorded"
    assert replay["transaction_id"] == failed["transaction_id"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot("RUN-codex-run")
    invocation = next(
        item for item in snapshot.invocations if item.invocation_id == invocation_id
    )
    assert invocation.status == "failed"
    assert invocation.failure_reason == "child_timed_out"


def test_deterministic_source_failure_exhausts_frozen_route_without_retry(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    workspace = _external_workspace(tmp_path)
    calls = 0
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")

    def no_results(_provider, _query, _config):
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(
        "multi_agent_brief.sources.web_search.WebSearchProvider.collect",
        no_results,
    )
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    planner = json.loads(capsys.readouterr().out)
    planner_scratch = workspace / planner["scratch_directory"]
    (planner_scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: web-search\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, planner)),
            ]
        )
        == 0
    )
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["next_action"]["effect_kind"] == "source_acquire"

    (workspace / "sources.yaml").write_text("mutated: true\n", encoding="utf-8")
    assert _apply_current(workspace, capsys) == 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "rejected_recorded"
    assert failed["next_action"]["action_kind"] == "human_decision"
    assert failed["next_action"]["effect_kind"] == "source_input_required"
    assert calls == 1

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision
        snapshot = store.load_snapshot("RUN-external-codex-run")
    assert snapshot.sources == ()
    assert (
        len(
            [item for item in snapshot.invocations if item.role_id == "source-provider"]
        )
        == 1
    )
    assert _apply_current(workspace, capsys) == 1
    assert "runtime_human_request_required" in capsys.readouterr().out
    assert calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision

    content = b"Human-provided durable source content for deterministic intake.\n"
    manual = workspace / "input" / "manual-source.txt"
    manual.write_bytes(content)
    action_path = _current_action_path(workspace, capsys)
    action = json.loads(action_path.read_text(encoding="utf-8"))
    request_path = workspace / "human-source-request.json"
    request_payload = {
        "schema_version": "briefloop.runtime_human_source_material_request.v2",
        "request_id": "REQ-HUMAN-SOURCE-001",
        "run_id": action["run_id"],
        "expected_store_revision": action["store_revision"],
        "input_path": "input/manual-source.txt",
        "expected_input_sha256": hashlib.sha256(content).hexdigest(),
        "title": "Human supplied source",
        "publisher": None,
        "published_at": None,
        "retrieved_at": "2026-07-19T00:00:00+00:00",
        "content_media_type": "text/plain",
    }
    request_path.write_text(
        json.dumps(request_payload, sort_keys=True),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "runtime",
                "apply",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
                "--human-request",
                str(request_path),
            ]
        )
        == 0
    )
    accepted_manual = json.loads(capsys.readouterr().out)
    assert accepted_manual["status"] == "committed"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after_manual = store.current_revision
        snapshot = store.load_snapshot(action["run_id"])
    assert snapshot.sources[-1].claims_eligible is True
    assert snapshot.sources[-1].locator.path == "input/manual-source.txt"

    manual.write_text("mutated after acceptance\n", encoding="utf-8")
    assert (
        main(
            [
                "runtime",
                "apply",
                "--workspace",
                str(workspace),
                "--action",
                str(action_path),
                "--human-request",
                str(request_path),
            ]
        )
        == 0
    )
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["status"] == "replayed"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == after_manual
        replay_snapshot = store.load_snapshot(action["run_id"])

    scratch_before_conflicts = {
        path.relative_to(workspace).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in sorted((workspace / "scratch").rglob("*"))
        if path.is_file()
    }
    authoritative_counts = (
        len(replay_snapshot.invocations),
        len(replay_snapshot.sources),
        len(replay_snapshot.transactions),
    )
    for field, changed_value in (
        ("title", "Changed title under the same request identity"),
        ("input_path", "input/missing-source.txt"),
        ("expected_input_sha256", "0" * 64),
    ):
        changed_request = {**request_payload, field: changed_value}
        request_path.write_text(
            json.dumps(changed_request, sort_keys=True),
            encoding="utf-8",
        )
        assert (
            main(
                [
                    "runtime",
                    "apply",
                    "--workspace",
                    str(workspace),
                    "--action",
                    str(action_path),
                    "--human-request",
                    str(request_path),
                ]
            )
            == 1
        )
        assert "submission_replay_conflict" in capsys.readouterr().out
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            assert store.current_revision == after_manual
            conflict_snapshot = store.load_snapshot(action["run_id"])
        assert (
            len(conflict_snapshot.invocations),
            len(conflict_snapshot.sources),
            len(conflict_snapshot.transactions),
        ) == authoritative_counts
        assert {
            path.relative_to(workspace).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in sorted((workspace / "scratch").rglob("*"))
            if path.is_file()
        } == scratch_before_conflicts


def test_cached_source_acquisition_is_claims_eligible_and_completes_discovery(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _cached_workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    planner = json.loads(capsys.readouterr().out)
    planner_scratch = workspace / planner["scratch_directory"]
    (planner_scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: cached_package\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, planner)),
            ]
        )
        == 0
    )
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["next_action"]["effect_kind"] == "source_acquire"
    assert accepted["next_action"]["source_route_id"] == "cached_package"

    assert _apply_current(workspace, capsys) == 0
    acquired = json.loads(capsys.readouterr().out)
    assert acquired["status"] == "committed"
    assert acquired["next_action"]["effect_kind"] == "stage_complete"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot("RUN-codex-run")
    source = snapshot.sources[0]
    assert source.material_kind == "full_content"
    assert source.claims_eligible is True


def test_cached_source_locator_binds_the_exact_selected_path(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "input" / "ignored.txt").write_text("short\n", encoding="utf-8")
    selected = workspace / "input" / "selected.txt"
    selected.write_text(
        "Selected durable source content long enough for deterministic intake.\n",
        encoding="utf-8",
    )
    (workspace / "sources.yaml").write_text(
        """source_strategy:
  profile: conservative
  enabled_providers: [cached_package]
cached_package:
  enabled: true
  paths: [input/ignored.txt, input/selected.txt]
  formats: [txt]
""",
        encoding="utf-8",
    )
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    planner = json.loads(capsys.readouterr().out)
    (workspace / planner["scratch_directory"] / "source_candidates.yaml").write_text(
        "version: 1\ncandidates:\n  - route: cached_package\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, planner)),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        source = store.load_snapshot("RUN-codex-run").sources[0]
    assert source.locator.path == "input/selected.txt"


def test_single_session_envelope_cannot_be_rewritten_as_delegated_execution(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert _apply_current(workspace, capsys) == 0
    capsys.readouterr()
    assert _start_current(workspace, capsys) == 0
    envelope = json.loads(capsys.readouterr().out)
    invocation_id = envelope["invocation_id"]
    scratch = workspace / envelope["scratch_directory"]
    (scratch / "source_candidates.yaml").write_text(
        "version: 1\ncandidates: []\n",
        encoding="utf-8",
    )
    envelope["executor_kind"] = "delegated_specialist"
    envelope["context_mode"] = "independent_stage_context"
    envelope["review_mode"] = "independent_stage_context"
    envelope["dispatch_instruction"] = "delegate_exact_role"
    (scratch / "role_task_envelope.json").write_text(
        json.dumps(envelope, sort_keys=True),
        encoding="utf-8",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision

    assert (
        main(
            [
                "runtime",
                "invocation-accept",
                "--workspace",
                str(workspace),
                "--envelope",
                str(_envelope_path(workspace, envelope)),
            ]
        )
        == 1
    )
    assert "runtime_envelope_invalid" in capsys.readouterr().out
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before
