from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore
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
    assert main(
        [
            "runtime",
            "apply",
            "--workspace",
            str(workspace),
            "--action",
            str(action_path),
        ]
    ) == 0
    capsys.readouterr()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision

    assert main(
        [
            "runtime",
            "invocation-start",
            "--workspace",
            str(workspace),
            "--action",
            str(action_path),
        ]
    ) == 1
    assert "runtime_action_stale" in capsys.readouterr().out
    forged = dict(doctor_action)
    forged["reason_code"] = "forged"
    action_path.write_text(json.dumps(forged), encoding="utf-8")
    assert main(
        [
            "runtime",
            "invocation-start",
            "--workspace",
            str(workspace),
            "--action",
            str(action_path),
        ]
    ) == 1
    assert "runtime_action_invalid" in capsys.readouterr().out
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision


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
    assert main(
        [
            "runtime",
            "invocation-accept",
            "--workspace",
            str(workspace),
            "--envelope",
            str(_envelope_path(workspace, planner)),
        ]
    ) == 0
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
    assert len([item for item in snapshot.invocations if item.role_id == "source-provider"]) == 1
    assert _apply_current(workspace, capsys) == 1
    assert "runtime_human_request_required" in capsys.readouterr().out
    assert calls == 1
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision


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
    assert main(
        [
            "runtime",
            "invocation-accept",
            "--workspace",
            str(workspace),
            "--envelope",
            str(_envelope_path(workspace, planner)),
        ]
    ) == 0
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
