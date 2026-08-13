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
    assert len(files) == 19
    assert (ws / ".dsh" / "README.md").exists()
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
    assert len(installed.adapter_asset_sha256) == 19

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
