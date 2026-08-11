"""Tests for workspace-local runtime kit installation."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.runtime_assets import INSTALL_MARKER, JSONC_INSTALL_MARKER

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parent.parent


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "config.yaml").write_text("project:\n  name: Runtime Kit\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# Runtime Kit\n", encoding="utf-8")
    (ws / "audience_profile.md").write_text("Do not overwrite me.\n", encoding="utf-8")
    (ws / "input").mkdir()
    (ws / "input" / "keep.md").write_text("User input.\n", encoding="utf-8")
    return ws


def _all_text_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".md", ".jsonc", ".toml"}
    ]


def _portable_output(text: str) -> str:
    return text.replace("\\", "/")


def _assert_frontmatter_first(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert INSTALL_MARKER in text


def test_runtime_install_codex_workspace_kit_is_local(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Installed workspace runtime kit for codex" in out
    assert "open and trust this workspace in Codex" in out
    # The codex kit is copied verbatim from the packaged ControlStore v2
    # runtime assets and stays entirely under `.codex/`; verbatim assets carry
    # no install marker, so frontmatter is asserted without one.
    config_path = ws / ".codex" / "config.toml"
    skill_path = ws / ".codex" / "skills" / "briefloop" / "SKILL.md"
    reference_path = (
        ws / ".codex" / "skills" / "briefloop" / "references" / "controlstore-v2.md"
    )
    agent_paths = [
        ws / ".codex" / "agents" / f"briefloop-{role}.toml"
        for role in (
            "source-planner",
            "source-provider",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
            "auditor",
        )
    ]
    assert config_path.exists()
    assert skill_path.exists()
    assert reference_path.exists()
    for agent_path in agent_paths:
        assert agent_path.exists()
    assert not (ws / "AGENTS.md").exists()
    assert not (ws / "CLAUDE.md").exists()

    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "agents" in config
    scout = tomllib.loads(
        (ws / ".codex" / "agents" / "briefloop-scout.toml").read_text(encoding="utf-8")
    )
    for key in ("name", "description", "developer_instructions"):
        assert key in scout
    assert scout["name"] == "scout"
    assert "RoleTaskEnvelope" in scout["developer_instructions"]
    assert "briefloop contract show" in scout["developer_instructions"]
    assert "briefloop runtime invocation-validate" in scout["developer_instructions"]
    skill_text = skill_path.read_text(encoding="utf-8")
    assert skill_text.startswith("---\n")
    assert "name: briefloop" in skill_text
    assert "BriefLoop Operator" in skill_text
    assert "references/controlstore-v2.md" in skill_text
    assert "CoreRunNextAction" in skill_text
    reference_text = reference_path.read_text(encoding="utf-8")
    assert "briefloop run --workspace <workspace> --runtime codex" in reference_text
    for action_kind in (
        "delegate",
        "deterministic",
        "human_decision",
        "blocked",
        "complete",
    ):
        assert action_kind in skill_text
        assert f"### `{action_kind}`" in reference_text
    for command in (
        "briefloop runtime next",
        "briefloop runtime invocation-start",
        "briefloop runtime invocation-validate",
        "briefloop runtime invocation-accept",
        "briefloop runtime invocation-fail",
        "briefloop runtime apply",
    ):
        assert command in reference_text
    assert "RoleTaskEnvelope" in reference_text
    assert "briefloop contract show" in reference_text
    assert "briefloop runtime invocation-validate" in reference_text
    assert "allowed_output_filenames" in reference_text
    assert "runtime_action_stale" in reference_text
    assert "effect_kind=package_ready" in reference_text
    assert "effect_kind=delivered" in reference_text
    assert "--human-request" in reference_text
    assert "Never read them back for legality" in reference_text
    assert "--runtime operator" not in skill_text + reference_text
    assert "output/intermediate/workflow_state.json" not in skill_text + reference_text

    assert (ws / "audience_profile.md").read_text(encoding="utf-8") == "Do not overwrite me.\n"
    assert (ws / "config.yaml").read_text(encoding="utf-8") == "project:\n  name: Runtime Kit\n"

    combined = "\n".join(path.read_text(encoding="utf-8") for path in _all_text_files(ws))
    assert ROOT.as_posix() not in combined
    assert "briefloop run --workspace" in combined


def test_runtime_install_codex_dry_run_lists_assets(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
        "--dry-run",
    ])

    assert rc == 0
    out = _portable_output(capsys.readouterr().out)
    assert "would write" in out
    assert "open and trust this workspace in Codex" in out
    assert ".codex/config.toml" in out
    assert ".codex/skills/briefloop/SKILL.md" in out
    assert ".codex/agents/briefloop-scout.toml" in out
    assert not (ws / ".codex").exists()
    assert not (ws / ".claude").exists()


def test_runtime_install_codex_refuses_non_mabw_agent_file(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    target = ws / ".codex" / "agents" / "briefloop-scout.toml"
    target.parent.mkdir(parents=True)
    target.write_text("name = \"user-owned\"\n", encoding="utf-8")

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
    ])

    assert rc == 1
    out = capsys.readouterr().out
    assert "runtime_adapter_binding_mismatch" in out
    assert target.read_text(encoding="utf-8") == "name = \"user-owned\"\n"


def test_runtime_install_codex_force_never_overwrites_user_content(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    target = ws / ".codex" / "agents" / "briefloop-scout.toml"
    target.parent.mkdir(parents=True)
    target.write_text("name = \"user-owned\"\n", encoding="utf-8")

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
        "--force",
    ])

    assert rc == 1
    assert "runtime_adapter_binding_mismatch" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "name = \"user-owned\"\n"


def test_runtime_install_codex_resumes_exact_partial_generated_kit(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    assert main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
    ]) == 0
    missing = ws / ".codex" / "agents" / "briefloop-scout.toml"
    expected = missing.read_bytes()
    missing.unlink()

    assert main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
    ]) == 0
    assert missing.read_bytes() == expected


