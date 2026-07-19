from __future__ import annotations

from datetime import date
import json
from pathlib import Path

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

    assert main(["runtime", "apply", "--workspace", str(workspace)]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "committed"
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["action_kind"] == "delegate"
    assert action["role_id"] == "source-planner"

    assert main(["runtime", "invocation-start", "--workspace", str(workspace)]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["role_id"] == "source-planner"
    assert envelope["action"] == action
    envelope_path = (
        workspace / envelope["scratch_directory"] / "role_task_envelope.json"
    )
    assert json.loads(envelope_path.read_text(encoding="utf-8")) == envelope

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision
    assert main(["runtime", "invocation-start", "--workspace", str(workspace)]) == 1
    assert "runtime_action_not_invocable" in capsys.readouterr().out
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision


def test_source_planner_writes_only_artifact_and_host_derives_accept_request(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = _workspace(tmp_path)
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert main(["runtime", "apply", "--workspace", str(workspace)]) == 0
    capsys.readouterr()
    assert main(["runtime", "invocation-start", "--workspace", str(workspace)]) == 0
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
                "--invocation-id",
                invocation_id,
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
                "--invocation-id",
                invocation_id,
            ]
        )
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "replayed"
    assert replay["transaction_id"] == accepted["transaction_id"]
    assert replay["store_revision"] == accepted["store_revision"]
