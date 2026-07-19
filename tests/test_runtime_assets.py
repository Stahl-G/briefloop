"""Tests for workspace-local runtime kit installation."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.start_commands import CONTRACT_REFERENCES
from multi_agent_brief.runtime_assets import INSTALL_MARKER, JSONC_INSTALL_MARKER, TOML_INSTALL_MARKER

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


def test_runtime_install_opencode_workspace_kit_is_local(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "opencode",
        "--repo-workdir",
        str(ROOT),
    ])

    assert rc == 0
    assert "Installed workspace runtime kit for opencode" in capsys.readouterr().out
    assert (ws / "AGENTS.md").exists()
    assert (ws / ".opencode" / "commands" / "generate-brief.md").exists()
    assert (ws / ".opencode" / "commands" / "capability.md").exists()
    assert (ws / ".opencode" / "agents" / "brief-orchestrator.md").exists()
    assert (ws / ".opencode" / "skills" / "multi-agent-brief-workflow" / "SKILL.md").exists()
    assert (ws / ".opencode" / "skills" / "multi-agent-brief-workflow" / "references" / "runtime-workflow.md").exists()
    assert (ws / "opencode.jsonc").exists()
    assert (ws / "opencode.jsonc").read_text(encoding="utf-8").startswith(
        JSONC_INSTALL_MARKER
    )
    _assert_frontmatter_first(ws / ".opencode" / "commands" / "generate-brief.md")
    _assert_frontmatter_first(ws / ".opencode" / "commands" / "capability.md")
    _assert_frontmatter_first(ws / ".opencode" / "agents" / "brief-orchestrator.md")
    _assert_frontmatter_first(
        ws / ".opencode" / "skills" / "multi-agent-brief-workflow" / "SKILL.md"
    )
    assert (ws / "audience_profile.md").read_text(encoding="utf-8") == "Do not overwrite me.\n"
    assert (ws / "config.yaml").read_text(encoding="utf-8") == "project:\n  name: Runtime Kit\n"

    combined = "\n".join(path.read_text(encoding="utf-8") for path in _all_text_files(ws))
    assert ROOT.as_posix() not in combined
    assert "briefloop run --workspace" in combined
    assert "Do not assume this workspace" in combined


def test_runtime_install_claude_workspace_kit_is_local(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "claude",
        "--repo-workdir",
        str(ROOT),
    ])

    assert rc == 0
    assert "Installed workspace runtime kit for claude" in capsys.readouterr().out
    assert (ws / "CLAUDE.md").exists()
    assert (ws / ".claude" / "commands" / "mabw.md").exists()
    assert (ws / ".claude" / "commands" / "generate-brief.md").exists()
    assert (ws / ".claude" / "commands" / "capability.md").exists()
    assert (ws / ".claude" / "commands" / "init-brief.md").exists()
    assert (ws / ".claude" / "commands" / "propose-competitors.md").exists()
    assert (ws / ".claude" / "agents" / "orchestrator.md").exists()
    assert (ws / ".claude" / "skills" / "multi-agent-brief-workflow" / "SKILL.md").exists()
    assert (ws / ".claude" / "skills" / "multi-agent-brief-workflow" / "references" / "artifact-boundary.md").exists()
    _assert_frontmatter_first(ws / ".claude" / "commands" / "mabw.md")
    _assert_frontmatter_first(ws / ".claude" / "commands" / "generate-brief.md")
    _assert_frontmatter_first(ws / ".claude" / "commands" / "capability.md")
    _assert_frontmatter_first(ws / ".claude" / "commands" / "propose-competitors.md")
    _assert_frontmatter_first(ws / ".claude" / "agents" / "orchestrator.md")
    _assert_frontmatter_first(
        ws / ".claude" / "skills" / "multi-agent-brief-workflow" / "SKILL.md"
    )

    combined = "\n".join(path.read_text(encoding="utf-8") for path in _all_text_files(ws))
    assert ROOT.as_posix() not in combined
    assert "briefloop run --workspace" in combined


def test_runtime_install_codex_workspace_kit_is_local(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
        "--repo-workdir",
        str(ROOT),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Installed workspace runtime kit for codex" in out
    assert "open and trust this workspace in Codex" in out
    assert (ws / ".codex" / "config.toml").exists()
    assert (ws / ".codex" / "agents" / "briefloop-scout.toml").exists()
    assert not (ws / ".codex" / "agents" / "orchestrator.toml").exists()
    assert (ws / ".codex" / "skills" / "briefloop" / "SKILL.md").exists()
    assert (
        ws
        / ".codex"
        / "skills"
        / "briefloop"
        / "references"
        / "controlstore-v2.md"
    ).exists()
    scout = tomllib.loads(
        (ws / ".codex" / "agents" / "briefloop-scout.toml").read_text(
            encoding="utf-8"
        )
    )
    for key in ("name", "description", "developer_instructions"):
        assert key in scout
    assert scout["name"] == "scout"
    assert "invocation scratch" in scout["developer_instructions"]
    skill_path = ws / ".codex" / "skills" / "briefloop" / "SKILL.md"
    assert skill_path.read_text(encoding="utf-8").startswith("---\n")
    skill_text = skill_path.read_text(encoding="utf-8")
    assert "SQLite-only" in skill_text
    assert "CoreRunNextAction" in skill_text
    reference_text = (
        ws
        / ".codex"
        / "skills"
        / "briefloop"
        / "references"
        / "controlstore-v2.md"
    ).read_text(encoding="utf-8")
    assert "execute_in_current_session" in reference_text
    assert "stage-separated self-review" in reference_text

    combined = "\n".join(path.read_text(encoding="utf-8") for path in _all_text_files(ws))
    assert ROOT.as_posix() not in combined
    assert "briefloop run --workspace" in combined
    assert "JSON-only workspace is unsupported" in combined


def test_runtime_install_dry_run_does_not_write_files(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "all",
        "--repo-workdir",
        str(ROOT),
        "--dry-run",
    ])

    assert rc == 0
    out = _portable_output(capsys.readouterr().out)
    assert "would write" in out
    assert out.count("/AGENTS.md") == 1
    assert not (ws / "AGENTS.md").exists()
    assert not (ws / "CLAUDE.md").exists()
    assert not (ws / ".opencode").exists()
    assert not (ws / ".codex").exists()


def test_runtime_install_codex_dry_run_lists_assets(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "codex",
        "--repo-workdir",
        str(ROOT),
        "--dry-run",
    ])

    assert rc == 0
    out = _portable_output(capsys.readouterr().out)
    assert "would write" in out
    assert "open and trust this workspace in Codex" in out
    assert ".codex/config.toml" in out
    assert ".codex/agents/briefloop-scout.toml" in out
    assert not (ws / ".codex").exists()
    assert not (ws / ".claude").exists()


def test_runtime_install_refuses_non_mabw_existing_file(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    (ws / "AGENTS.md").write_text("User-owned agent notes.\n", encoding="utf-8")

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "opencode",
        "--repo-workdir",
        str(ROOT),
    ])

    assert rc == 1
    out = capsys.readouterr().out
    assert "Refusing to overwrite existing non-generated file without --force" in out
    assert (ws / "AGENTS.md").read_text(encoding="utf-8") == "User-owned agent notes.\n"


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
        "--repo-workdir",
        str(ROOT),
    ])

    assert rc == 1
    out = capsys.readouterr().out
    assert "Refusing to overwrite existing non-generated file without --force" in out
    assert target.read_text(encoding="utf-8") == "name = \"user-owned\"\n"


def test_runtime_install_refreshes_generated_files(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / "AGENTS.md").write_text(f"{INSTALL_MARKER}\nold\n", encoding="utf-8")

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "opencode",
        "--repo-workdir",
        str(ROOT),
    ])

    assert rc == 0
    assert "old" not in (ws / "AGENTS.md").read_text(encoding="utf-8")


def test_runtime_install_package_contract_base_fails_source_clone_only(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    package_base = tmp_path / "multi_agent_brief"
    for rel_path in CONTRACT_REFERENCES.values():
        target = package_base / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder: true\n", encoding="utf-8")
    (package_base / "__init__.py").write_text("", encoding="utf-8")

    rc = main([
        "runtime",
        "install",
        "--workspace",
        str(ws),
        "--runtime",
        "opencode",
        "--repo-workdir",
        str(package_base),
    ])

    assert rc == 1
    out = capsys.readouterr().out
    assert "source-clone-only" in out
    assert "--repo-workdir" in out
