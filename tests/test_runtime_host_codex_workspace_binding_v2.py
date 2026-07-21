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


def _initialize(workspace: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
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
