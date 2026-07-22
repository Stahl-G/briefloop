from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.runtime_assets import install_runtime_kit
from multi_agent_brief.runtime_host_v2.codex import (
    load_codex_adapter_binding,
    load_workspace_codex_adapter_binding,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.initialization import WorkspaceBootstrap
from multi_agent_brief.workspace.init_profile import InitProfile


ROLE_IDS = (
    "analyst",
    "auditor",
    "claim-ledger",
    "editor",
    "scout",
    "screener",
    "source-planner",
    "source-provider",
)
ASSET_PATHS = (
    Path("config.toml"),
    Path("skills/briefloop/SKILL.md"),
    Path("skills/briefloop/references/controlstore-v2.md"),
    *(Path(f"agents/briefloop-{role_id}.toml") for role_id in ROLE_IDS),
)


def _workspace(tmp_path: Path, *, install: bool = True) -> Path:
    workspace = tmp_path / "workspace"
    values = iter(("binding-workspace", "binding-run"))
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
        report_date_factory=lambda: date(2026, 7, 22),
        identity_factory=lambda: next(values),
    )
    if install:
        install_runtime_kit(workspace=workspace, runtime="codex")
    return workspace


def _initialize(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> dict[str, object]:
    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    return json.loads(capsys.readouterr().out)


def _assert_revision(
    workspace: Path,
    expected: int,
) -> None:
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == expected


def test_installed_workspace_binding_equals_packaged_binding(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    assert load_workspace_codex_adapter_binding(
        workspace, "RUN-binding-run"
    ) == load_codex_adapter_binding("RUN-binding-run")


def test_run_fails_closed_when_workspace_kit_is_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path, install=False)

    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 1
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
    assert not (workspace / "briefloop.db").exists()


def test_cli_init_prepares_exact_kit_without_committing_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "cli-workspace"
    assert (
        main(
            [
                "init",
                str(workspace),
                "--language",
                "en-US",
                "--company",
                "ExampleCo",
                "--industry",
                "manufacturing",
                "--title",
                "ExampleCo brief",
                "--task-objective",
                "Prepare the ExampleCo brief.",
                "--audience",
                "management",
                "--cadence",
                "weekly",
                "--source-profile",
                "conservative",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (workspace / ".codex" / "config.toml").is_file()
    assert not (workspace / "briefloop.db").exists()

    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["run_id"].startswith("RUN-")
    assert (workspace / "briefloop.db").is_file()


def test_bootstrap_validates_strict_inputs_before_materializing_kit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path, install=False)
    (workspace / "config.yaml").write_text(
        "controlstore_v2: []\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeHostError, match="runtime_initialization_input_invalid"):
        WorkspaceBootstrap(workspace).prepare_codex_runtime()
    assert not (workspace / ".codex").exists()
    assert not (workspace / "briefloop.db").exists()


def test_runtime_install_existing_store_is_verify_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace, capsys)
    asset = workspace / ".codex" / "agents" / "briefloop-scout.toml"
    before_bytes = asset.read_bytes()
    before_mtime = asset.stat().st_mtime_ns
    (workspace / "config.yaml").write_text("not: [valid\n", encoding="utf-8")
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision_before = store.current_revision

    assert (
        main(
            [
                "runtime",
                "install",
                "--workspace",
                str(workspace),
                "--runtime",
                "codex",
                "--force",
            ]
        )
        == 0
    )
    assert "Verified workspace runtime kit for codex" in capsys.readouterr().out
    assert asset.read_bytes() == before_bytes
    assert asset.stat().st_mtime_ns == before_mtime
    _assert_revision(workspace, revision_before)


def test_runtime_install_existing_store_never_repairs_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace, capsys)
    asset = workspace / ".codex" / "agents" / "briefloop-scout.toml"
    asset.write_bytes(asset.read_bytes() + b"\n# drift\n")
    drifted = asset.read_bytes()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision_before = store.current_revision

    assert (
        main(
            [
                "runtime",
                "install",
                "--workspace",
                str(workspace),
                "--runtime",
                "codex",
                "--force",
            ]
        )
        == 1
    )
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
    assert asset.read_bytes() == drifted
    _assert_revision(workspace, revision_before)


@pytest.mark.parametrize("relative", ASSET_PATHS, ids=lambda path: path.as_posix())
@pytest.mark.parametrize("mutation", ["tamper", "delete"])
def test_runtime_next_rejects_every_changed_or_deleted_bound_asset(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: Path,
    mutation: str,
) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace, capsys)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision
    target = workspace / ".codex" / relative
    if mutation == "tamper":
        target.write_bytes(target.read_bytes() + b"\n# drift\n")
    else:
        target.unlink()

    assert main(["runtime", "next", "--workspace", str(workspace)]) == 1
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
    _assert_revision(workspace, before)


@pytest.mark.parametrize(
    "relative",
    [
        Path("unexpected.toml"),
        Path("agents/unexpected.toml"),
        Path("skills/briefloop/references/unexpected.md"),
    ],
)
def test_runtime_next_rejects_added_workspace_kit_assets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relative: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace, capsys)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision
    target = workspace / ".codex" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unexpected\n", encoding="utf-8")

    assert main(["runtime", "next", "--workspace", str(workspace)]) == 1
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
    _assert_revision(workspace, before)


def test_runtime_diagnose_and_apply_reject_workspace_kit_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    action = _initialize(workspace, capsys)
    action_path = workspace / "doctor_action.json"
    action_path.write_text(json.dumps(action), encoding="utf-8")
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.current_revision
    skill = workspace / ".codex/skills/briefloop/SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\n# drift\n")

    assert main(["runtime", "diagnose", "--workspace", str(workspace)]) == 1
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
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
        == 1
    )
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
    _assert_revision(workspace, before)


def test_workspace_binding_rejects_symlinked_asset(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    target = workspace / ".codex/agents/briefloop-scout.toml"
    content = target.read_bytes()
    target.unlink()
    outside = tmp_path / "outside.toml"
    outside.write_bytes(content)
    target.symlink_to(outside)

    with pytest.raises(RuntimeHostError, match="runtime_adapter_binding_mismatch"):
        load_workspace_codex_adapter_binding(workspace, "RUN-binding-run")
