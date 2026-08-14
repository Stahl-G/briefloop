"""Tests for the experimental DeepSeek Harness (DSH) runtime kit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.runtime_host_v2.dsh import (
    load_dsh_adapter_binding,
    load_workspace_dsh_adapter_binding,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError


ROOT = Path(__file__).resolve().parent.parent

DSH_ROLES = (
    "source-planner",
    "source-provider",
    "scout",
    "screener",
    "claim-ledger",
    "analyst",
    "editor",
    "auditor",
)

VERIFY_RUN_ID = "RUN-DSH-KIT-VERIFY"


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "config.yaml").write_text("project:\n  name: DSH Kit\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# DSH Kit\n", encoding="utf-8")
    return ws


def _installed_files(ws: Path) -> list[Path]:
    return sorted(path for path in (ws / ".dsh").rglob("*") if path.is_file())


def _assert_kit_complete(ws: Path) -> None:
    files = _installed_files(ws)
    assert len(files) == 21
    assert (ws / ".dsh" / "README.md").exists()
    assert (ws / ".dsh" / "plugin" / "briefloop-dsh.host.js").exists()
    assert (ws / ".dsh" / "plugin" / "briefloop-dsh.client.js").exists()
    assert (ws / ".dsh" / "skills" / "briefloop" / "SKILL.md").exists()
    assert (
        ws / ".dsh" / "skills" / "briefloop" / "references" / "controlstore-v2.md"
    ).exists()
    for role in DSH_ROLES:
        assert (ws / ".dsh" / "presets" / f"briefloop-{role}" / "agent.cordis.yml").exists()
        assert (ws / ".dsh" / "presets" / f"briefloop-{role}" / "preset.yml").exists()


def test_runtime_install_dsh_workspace_kit_is_local(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main(
        ["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Installed workspace runtime kit for dsh" in out
    assert "DSH note" in out
    assert ".agent-presets/" in out
    _assert_kit_complete(ws)
    assert not (ws / "briefloop.db").exists()

    skill_text = (ws / ".dsh" / "skills" / "briefloop" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert skill_text.startswith("---\n")
    assert "name: briefloop" in skill_text
    assert "DeepSeek Harness" in skill_text
    assert "CoreRunNextAction" in skill_text

    scout = yaml.safe_load(
        (ws / ".dsh" / "presets" / "briefloop-scout" / "agent.cordis.yml").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(scout, list)
    assert scout[0]["id"] == "persona"
    assert scout[0]["name"] == "@deepseek-ai/dsh-persona"
    assert "BriefLoop scout specialist" in scout[0]["config"]["text"]
    assert "RoleTaskEnvelope" in scout[0]["config"]["text"]


def test_runtime_install_dsh_dry_run_lists_assets(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main(
        [
            "runtime",
            "install",
            "--workspace",
            str(ws),
            "--runtime",
            "dsh",
            "--dry-run",
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Planned workspace runtime kit for dsh" in out
    assert not (ws / ".dsh").exists()


def test_runtime_install_dsh_refuses_non_kit_file(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    assert (
        main(["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"]) == 0
    )
    capsys.readouterr()
    extra = ws / ".dsh" / "user-note.txt"
    extra.write_text("mine\n", encoding="utf-8")

    rc = main(["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"])

    assert rc == 1
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
    assert extra.read_text(encoding="utf-8") == "mine\n"


def test_runtime_install_dsh_resumes_exact_partial_generated_kit(
    tmp_path: Path, capsys
) -> None:
    ws = _workspace(tmp_path)
    assert (
        main(["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"]) == 0
    )
    capsys.readouterr()
    removed = ws / ".dsh" / "presets" / "briefloop-auditor" / "preset.yml"
    removed.unlink()

    rc = main(["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"])

    assert rc == 0
    assert removed.exists()
    _assert_kit_complete(ws)


def test_dsh_binding_matches_packaged_and_detects_tamper(
    tmp_path: Path, capsys
) -> None:
    ws = _workspace(tmp_path)
    assert (
        main(["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"]) == 0
    )
    capsys.readouterr()

    packaged = load_dsh_adapter_binding(VERIFY_RUN_ID)
    installed = load_workspace_dsh_adapter_binding(ws, VERIFY_RUN_ID)
    assert installed == packaged
    assert installed.runtime == "dsh"
    assert installed.adapter_id == "briefloop-dsh-controlstore"
    assert installed.role_ids == sorted(DSH_ROLES)
    assert "dsh.README.md" in installed.adapter_asset_sha256
    assert len(installed.adapter_asset_sha256) == 21

    preset_path = ws / ".dsh" / "presets" / "briefloop-scout" / "agent.cordis.yml"
    # A byte-level tamper that preserves structure changes the hash-bound
    # binding identity without breaking parsing; exact comparison catches it.
    preset_path.write_text(preset_path.read_text(encoding="utf-8") + "\n# tamper\n")
    tampered = load_workspace_dsh_adapter_binding(ws, VERIFY_RUN_ID)
    assert tampered != packaged
    assert tampered.runtime == "dsh"

    # A structural tamper fails closed at binding build time.
    broken = (
        ws / ".dsh" / "presets" / "briefloop-scout" / "agent.cordis.yml"
    )
    rows = yaml.safe_load(broken.read_text(encoding="utf-8"))
    broken.write_text(
        yaml.safe_dump([row for row in rows if row.get("id") != "persona"])
    )
    with pytest.raises(RuntimeHostError):
        load_workspace_dsh_adapter_binding(ws, VERIFY_RUN_ID)


def test_run_dsh_handoff_missing_kit_then_installed(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main(["run", "--workspace", str(ws), "--runtime", "dsh"])

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema_version"] == "briefloop.dsh_handoff.v1"
    assert payload["runtime"] == "dsh"
    assert payload["kit"] == "missing"
    assert any("runtime install" in step for step in payload["steps"])

    assert (
        main(["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"]) == 0
    )
    capsys.readouterr()
    rc = main(["run", "--workspace", str(ws), "--runtime", "dsh"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kit"] == "installed"
    assert payload["store_runtime"] == "codex"
    assert payload["control_protocol"] == "controlstore_v2"


def test_run_dsh_handoff_rejects_tampered_kit(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    assert (
        main(["runtime", "install", "--workspace", str(ws), "--runtime", "dsh"]) == 0
    )
    capsys.readouterr()
    preset_path = ws / ".dsh" / "presets" / "briefloop-editor" / "agent.cordis.yml"
    preset_path.write_text(preset_path.read_text(encoding="utf-8") + "# tamper\n")

    rc = main(["run", "--workspace", str(ws), "--runtime", "dsh"])

    assert rc == 1
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out


def test_dsh_presets_declare_no_service_publishing_rows() -> None:
    """Every preset row must either register into a host registry or consume
    one; no row may publish a process-global service without an isolate
    realm (the DSH mount audit rejects such a preset)."""

    service_publishing_packages = {
        "@deepseek-ai/dsh-tool-bash",
        "@deepseek-ai/dsh-tool-fs",
        "@deepseek-ai/dsh-tool-fs-search",
        "@deepseek-ai/dsh-tool-todo",
        "@deepseek-ai/dsh-tool-skill",
        "@deepseek-ai/dsh-skill-filesystem",
    }
    allowed_row_names = service_publishing_packages | {"@deepseek-ai/dsh-persona"}
    for role in DSH_ROLES:
        path = (
            ROOT
            / "src/multi_agent_brief/runtime_kits/dsh/presets"
            / f"briefloop-{role}"
            / "agent.cordis.yml"
        )
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, dict), path
            assert row["name"] in allowed_row_names, (path, row)
            assert row.get("group") is not True, (path, row)


def test_dsh_presets_declare_required_tool_config() -> None:
    """tool-fs-search and tool-todo have required config fields; omitting them
    makes the preset fail the DSH mount audit (standingKeyFor)."""

    for role in DSH_ROLES:
        path = (
            ROOT
            / "src/multi_agent_brief/runtime_kits/dsh/presets"
            / f"briefloop-{role}"
            / "agent.cordis.yml"
        )
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))
        by_name = {row.get("id"): row for row in rows}
        fs_search = by_name["tool-fs-search"]
        assert fs_search["config"]["sampleOverCapGlobResults"] is False, path
        todo = by_name["tool-todo"]
        assert todo["config"]["allowParallelInProgress"] is True, path


def test_dsh_plugin_source_declares_operator_and_cli_only() -> None:
    plugin = (
        ROOT / "src/multi_agent_brief/runtime_kits/dsh/plugin/briefloop-dsh.host.js"
    ).read_text(encoding="utf-8")
    assert "name: 'briefloop-dsh-operator'" in plugin
    # Every tool is CLI-only; the plugin never opens SQLite or the Store.
    assert "sqlite3" not in plugin
    assert "SQLiteControlStore" not in plugin
    assert "BRIEFLOOP_BIN" in plugin
    assert "briefloop_start" in plugin
    for tool in (
        "briefloop_version",
        "briefloop_status",
        "briefloop_runtime_next",
        "briefloop_init",
        "briefloop_runtime_install",
        "briefloop_runtime_continue",
        "briefloop_runtime_apply",
        "briefloop_runtime_invocation_start",
        "briefloop_runtime_invocation_validate",
        "briefloop_runtime_invocation_accept",
        "briefloop_runtime_invocation_fail",
        "briefloop_role_dispatch",
    ):
        assert tool in plugin

    client = (
        ROOT / "src/multi_agent_brief/runtime_kits/dsh/plugin/briefloop-dsh.client.js"
    ).read_text(encoding="utf-8")
    assert "sidebar.footer.action" in client
    assert "briefloop_start" in client
    assert "host.call" in client
    assert "sqlite3" not in client


def test_init_runtime_dsh_writes_dsh_bound_bootstrap(tmp_path: Path, capsys) -> None:
    ws = tmp_path / "dsh-ws"
    rc = main(["init", str(ws), "--demo", "--runtime", "dsh", "--force"])

    assert rc == 0
    capsys.readouterr()
    config = yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))
    assert config["controlstore_v2"]["runtime"] == "dsh"


def test_run_runtime_dsh_initializes_dsh_bound_store(tmp_path: Path, capsys) -> None:
    ws = tmp_path / "dsh-ws"
    assert main(["init", str(ws), "--demo", "--runtime", "dsh", "--force"]) == 0
    capsys.readouterr()

    rc = main(["run", "--workspace", str(ws), "--runtime", "dsh"])

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema_version"] == "briefloop.core_run_next_action.v2"
    # The Store is now bound to the dsh runtime.
    assert (ws / "briefloop.db").exists()
    from multi_agent_brief.runtime_host_v2.initialization import store_runtime

    assert store_runtime(ws) == "dsh"


def test_adapter_loader_for_workspace_dispatches_on_store_runtime(
    tmp_path: Path, capsys
) -> None:
    from multi_agent_brief.runtime_host_v2.initialization import (
        adapter_loader_for_workspace,
        store_runtime,
    )

    ws = tmp_path / "dsh-ws"
    assert main(["init", str(ws), "--demo", "--runtime", "dsh", "--force"]) == 0
    assert main(["run", "--workspace", str(ws), "--runtime", "dsh"]) == 0
    capsys.readouterr()

    assert store_runtime(ws) == "dsh"
    loader = adapter_loader_for_workspace(ws)
    binding = loader("RUN-LOADER-DISPATCH")
    assert binding.runtime == "dsh"
    assert binding.adapter_id == "briefloop-dsh-controlstore"


def test_read_host_contract_accepts_symlinked_workspace_prefix(tmp_path: Path) -> None:
    """A symlinked workspace prefix (macOS /tmp -> /private/tmp) must not break
    workspace containment: the caller resolves the workspace while the host
    input path may still carry the symlinked prefix."""

    from pydantic import BaseModel

    from multi_agent_brief.runtime_host_v2.scratch import read_host_contract

    class _Contract(BaseModel):
        x: int

    real = tmp_path / "real-ws"
    real.mkdir()
    (real / "action.json").write_text('{"x": 7}', encoding="utf-8")
    link = tmp_path / "link-ws"
    link.symlink_to(real, target_is_directory=True)

    workspace = Path(str(link)).resolve()
    result = read_host_contract(
        workspace,
        str(link / "action.json"),
        _Contract,
        error_code="runtime_action_invalid",
    )
    assert result.x == 7


def test_read_host_contract_still_rejects_inner_symlink(tmp_path: Path) -> None:
    """The symlinked-prefix fix must not weaken inner-symlink rejection."""

    from pydantic import BaseModel

    from multi_agent_brief.runtime_host_v2.scratch import read_host_contract

    class _Contract(BaseModel):
        x: int

    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "action.json").write_text('{"x": 9}', encoding="utf-8")
    (workspace / "action.json").symlink_to(outside / "action.json")

    with pytest.raises(RuntimeHostError):
        read_host_contract(
            workspace.resolve(),
            str(workspace / "action.json"),
            _Contract,
            error_code="runtime_action_invalid",
        )
