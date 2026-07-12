"""Tests for the public product rename guard."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_public_product_rename.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_public_product_rename_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_public_product_rename_guard_runs_clean() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public product rename guard passed" in result.stdout


def test_public_product_rename_guard_reports_line_and_suggestion(tmp_path) -> None:
    target = tmp_path / "README.md"
    target.write_text(
        "Use multi-agent-brief for the first run.\n"
        "The old /mabw command is also shown here.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert f"{target}:1" in result.stdout
    assert f"{target}:2" in result.stdout
    assert "suggestion:" in result.stdout
    assert "prefer `briefloop`" in result.stdout


def test_public_product_rename_guard_rejects_legacy_names_before_sentence_punctuation(tmp_path) -> None:
    target = tmp_path / "getting-started.md"
    target.write_text(
        "Use multi-agent-brief.\n"
        "Formerly MABW.\n"
        "Still not /mabw.\n"
        "Do not flag multi-agent-brief-workflow or MABW-080 compatibility ids here.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert f"{target}:1" in result.stdout
    assert f"{target}:2" in result.stdout
    assert f"{target}:3" in result.stdout
    assert f"{target}:4" not in result.stdout


def test_public_product_rename_guard_rejects_old_setup_banner(tmp_path) -> None:
    target = tmp_path / "scripts" / "setup.sh"
    target.parent.mkdir()
    target.write_text(
        "# package implementation name remains allowed in comments: multi-agent-brief-workflow\n"
        "echo \"=== multi-agent-brief-workflow setup ===\"\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "package_name_setup_output" in result.stdout
    assert f"{target}:2" in result.stdout


def test_public_product_rename_scan_is_limited_to_requested_paths(tmp_path) -> None:
    module = _load_module()
    compatibility_doc = tmp_path / "docs" / "MIGRATION.md"
    compatibility_doc.parent.mkdir()
    compatibility_doc.write_text("MABW and /mabw remain compatibility names.\n", encoding="utf-8")

    assert module.scan(paths=[]) == []
    findings = module.scan(paths=[compatibility_doc])
    assert len(findings) == 2


def test_public_product_rename_default_scan_reports_missing_target(tmp_path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "TARGET_FILES", ["missing-first-user-doc.md"])
    monkeypatch.setattr(module, "NAMING_AUTHORITY_FILES", [])
    monkeypatch.setattr(module, "NAMING_CONSUMER_FILES", [])
    monkeypatch.setattr(module, "CLI_HELP_COMMANDS", [])

    findings = module.scan(root=tmp_path)

    assert len(findings) == 1
    assert findings[0].kind == "missing_target"
    assert findings[0].path == tmp_path / "missing-first-user-doc.md"


def test_public_product_rename_default_scan_reports_missing_cli_help(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "TARGET_FILES", [])
    monkeypatch.setattr(module, "NAMING_AUTHORITY_FILES", [])
    monkeypatch.setattr(module, "NAMING_CONSUMER_FILES", [])
    monkeypatch.setattr(module, "CLI_HELP_COMMANDS", [("removed", "command")])
    monkeypatch.setattr(module, "_briefloop_help_text", lambda args: "")

    findings = module.scan()

    assert len(findings) == 1
    assert findings[0].kind == "missing_cli_help"
    assert str(findings[0].path) == "<briefloop removed command --help>"


def test_naming_authority_surface_is_complete() -> None:
    module = _load_module()

    assert module.NAMING_AUTHORITY_FILES == [
        "docs/briefloop-naming.md",
        "docs/architecture-status.md",
        "docs/support-matrix.md",
        "docs/README.md",
    ]


def test_operator_naming_consumer_surface_is_ratchet_locked() -> None:
    module = _load_module()

    assert module.NAMING_CONSUMER_FILES == [
        ".agents/skills/briefloop/references/naming-and-compatibility.md",
        ".agents/skills/briefloop/references/version-matrix.md",
        "integrations/hermes-plugin/mabw/skills/briefloop/references/naming-and-compatibility.md",
        "integrations/hermes-plugin/mabw/skills/briefloop/references/version-matrix.md",
    ]


def test_operator_naming_consumer_rejects_equivalent_aliases(tmp_path) -> None:
    module = _load_module()
    target = tmp_path / "naming-and-compatibility.md"
    target.write_text(
        "BriefLoop is the only current project and product name.\n"
        "The former project acronym is retired.\n"
        "MABW is BriefLoop's internal implementation name.\n"
        "BriefLoop is powered by the MABW architecture.\n"
        "MABW is the engineering codename behind BriefLoop.\n"
        "Multi-Agent Brief Workflow is the current runtime.\n",
        encoding="utf-8",
    )

    findings = module.scan_naming_consumer_file(target)

    assert [(finding.line, finding.kind) for finding in findings] == [
        (3, "retired_project_name_alias"),
        (4, "retired_project_name_alias"),
        (5, "retired_project_name_alias"),
        (6, "retired_long_project_name_alias"),
    ]

    target.write_text(
        "BriefLoop is the only current project and product name.\n"
        "The former project acronym is retired.\n"
        "Multi Agent Brief Workflow is the current runtime.\n",
        encoding="utf-8",
    )
    assert [
        finding.kind for finding in module.scan_naming_consumer_file(target)
    ] == ["retired_long_project_name_alias"]


@pytest.mark.parametrize(
    "literal",
    [
        "/mabw",
        "MABW-080",
        "mabw.claim_drafts.v1",
        ".mabw-onboarding",
        "MABW_BIN",
        "integrations/hermes-plugin/mabw/skills/mabw-workflow",
        "mabw-workflow",
    ],
    ids=[
        "slash-command",
        "experiment-id",
        "schema-id",
        "dotfile",
        "environment-variable",
        "path",
        "plugin-id",
    ],
)
def test_operator_naming_consumer_allows_classified_literals(
    tmp_path,
    literal: str,
) -> None:
    module = _load_module()
    target = tmp_path / "naming-and-compatibility.md"
    target.write_text(
        "BriefLoop is the only current project and product name.\n"
        "The former project acronym is retired.\n"
        f"Compatibility identifier: `{literal}`.\n",
        encoding="utf-8",
    )

    assert module.scan_naming_consumer_file(target) == []


@pytest.mark.parametrize(
    "alias_line",
    [
        "`mabw-architecture` is the current implementation.",
        "`MABW-architecture` is the current implementation.",
        "`mabw` is the internal architecture.",
        "`MABW` is the internal architecture.",
        "`/mabw-architecture` is the current command.",
        "`/MABW-architecture` is the current command.",
        "`integrations/hermes-plugin/mabw-architecture` is the current path.",
    ],
    ids=[
        "quoted-lower-hyphen-alias",
        "quoted-upper-hyphen-alias",
        "quoted-lower-bare-name",
        "quoted-upper-bare-name",
        "quoted-lower-command-suffix",
        "quoted-upper-command-suffix",
        "path-prefix-is-not-path-literal",
    ],
)
def test_operator_naming_consumer_rejects_quoted_or_suffixed_aliases(
    tmp_path,
    alias_line: str,
) -> None:
    module = _load_module()
    target = tmp_path / "naming-and-compatibility.md"
    target.write_text(
        "BriefLoop is the only current project and product name.\n"
        "The former project acronym is retired.\n"
        f"{alias_line}\n",
        encoding="utf-8",
    )

    findings = module.scan_naming_consumer_file(target)

    assert [(finding.line, finding.kind) for finding in findings] == [
        (3, "retired_project_name_alias")
    ]


def test_operator_naming_consumer_rejects_unclassified_hyphen_alias(tmp_path) -> None:
    module = _load_module()
    target = tmp_path / "naming-and-compatibility.md"
    target.write_text(
        "BriefLoop is the only current project and product name.\n"
        "The former project acronym is retired.\n"
        "MABW-architecture is the current implementation.\n",
        encoding="utf-8",
    )

    findings = module.scan_naming_consumer_file(target)

    assert [finding.kind for finding in findings] == ["retired_project_name_alias"]


def test_operator_naming_consumer_mirrors_are_exact() -> None:
    source_root = ROOT / ".agents" / "skills" / "briefloop" / "references"
    hermes_root = (
        ROOT
        / "integrations"
        / "hermes-plugin"
        / "mabw"
        / "skills"
        / "briefloop"
        / "references"
    )

    for filename in ["naming-and-compatibility.md", "version-matrix.md"]:
        assert (source_root / filename).read_bytes() == (hermes_root / filename).read_bytes()


def test_naming_authority_rejects_implementation_lineage_alias(tmp_path) -> None:
    module = _load_module()
    target = tmp_path / "architecture-status.md"
    target.write_text(
        "BriefLoop is the only current project and product name.\n"
        "The former project acronym is retired.\n"
        "MABW remains the implementation\n"
        "lineage and compatibility surface.\n",
        encoding="utf-8",
    )

    findings = module.scan_naming_authority_file(target)

    assert [finding.kind for finding in findings] == ["implementation_lineage_alias"]


def test_naming_authority_requires_current_and_retired_name_rules(tmp_path) -> None:
    module = _load_module()
    target = tmp_path / "support-matrix.md"
    target.write_text("BriefLoop support matrix.\n", encoding="utf-8")

    findings = module.scan_naming_authority_file(target)

    assert [finding.kind for finding in findings] == [
        "missing_current_project_name_rule",
        "missing_retired_name_rule",
    ]


def test_naming_authority_allows_classified_compatibility_literals(tmp_path) -> None:
    module = _load_module()
    target = tmp_path / "briefloop-naming.md"
    target.write_text(
        "BriefLoop is the only current project and product name.\n"
        "The former project acronym is retired.\n"
        "Literal compatibility identifiers include multi-agent-brief, /mabw, "
        "multi_agent_brief, mabw.*, and MABW-080.\n",
        encoding="utf-8",
    )

    assert module.scan_naming_authority_file(target) == []


def test_default_scan_reports_missing_naming_authority(tmp_path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "TARGET_FILES", [])
    monkeypatch.setattr(module, "NAMING_AUTHORITY_FILES", ["docs/missing-authority.md"])
    monkeypatch.setattr(module, "NAMING_CONSUMER_FILES", [])
    monkeypatch.setattr(module, "CLI_HELP_COMMANDS", [])

    findings = module.scan(root=tmp_path)

    assert len(findings) == 1
    assert findings[0].kind == "missing_naming_authority"
    assert findings[0].path == tmp_path / "docs/missing-authority.md"


def test_public_product_rename_path_help_is_replacement_only() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    compact = " ".join(result.stdout.split())
    assert "Replacement path to scan instead of the default first-user surfaces" in compact
    assert "Additional or replacement" not in result.stdout


def test_installed_briefloop_command_passes_public_rename_guard(tmp_path, capsys) -> None:
    module = _load_module()
    target = tmp_path / "claude"

    rc = main(["claude", "install", "--repo-workdir", str(ROOT), "--target", str(target)])

    assert rc == 0
    capsys.readouterr()
    installed_briefloop = target / "commands" / "briefloop.md"
    installed_mabw = target / "commands" / "mabw.md"
    assert installed_briefloop.exists()
    assert installed_mabw.exists()
    findings = module.scan(paths=[installed_briefloop])
    assert findings == [], "\n".join(finding.format(ROOT) for finding in findings)
    first_screen = installed_briefloop.read_text(encoding="utf-8").split("## Routing", maxsplit=1)[0]
    assert "/mabw" not in first_screen


def test_public_product_rename_guard_scans_briefloop_cli_help() -> None:
    module = _load_module()

    findings = [
        finding
        for finding in module.scan()
        if str(finding.path).startswith("<briefloop")
    ]

    assert findings == [], "\n".join(finding.format(ROOT) for finding in findings)


def test_claude_first_response_uses_briefloop_writer_command() -> None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    first_response = text.split("## Standard Claude Code Path", maxsplit=1)[0]

    assert "/briefloop new" in first_response
    assert "/briefloop run <workspace>" in first_response
    assert "/briefloop status <workspace>" in first_response
    assert "/briefloop feedback <workspace>" in first_response
    assert "/briefloop deliver <workspace>" in first_response
    assert "/mabw" not in first_response
    assert "MABW" not in first_response


def test_compatibility_quarantine_classifies_remaining_legacy_names() -> None:
    naming = (ROOT / "docs" / "briefloop-naming.md").read_text(encoding="utf-8")
    normalized = " ".join(naming.lower().split())
    assert "## Compatibility quarantine" in naming
    assert "not the public product identity" in naming
    assert "do not use them as first-user instructions" in normalized

    expected_rows = [
        "| `/mabw` | Deprecated Claude compatibility alias |",
        "| `multi-agent-brief` | Compatibility CLI and script entrypoint |",
        "| `multi_agent_brief` | Python module compatibility surface |",
        "| `multi-agent-brief-workflow` | Historical distribution/package compatibility reference |",
        "| `MABW-080` | Archived experiment namespace |",
        "| `BriefLoop-090` | Archived experiment/readiness label |",
        "| `mabw.*` schema ids | Old-workspace compatibility ids |",
        "| Old release notes and tech reports | Historical archive |",
    ]
    for row in expected_rows:
        assert row in naming

    forbidden_promotional_claims = [
        "truth proof",
        "delivery approval",
        "autonomous agent runtime",
        "output-quality improvement proof",
    ]
    for phrase in forbidden_promotional_claims:
        assert phrase in normalized


def test_deep_rename_deferral_documents_non_blockers() -> None:
    naming = (ROOT / "docs" / "briefloop-naming.md").read_text(encoding="utf-8")
    normalized = " ".join(naming.lower().split())

    assert "## Deep rename deferral" in naming
    assert "product-facing rename completion, not grep-zero" in normalized
    assert "not v1.0 blockers" in normalized
    assert "`multi_agent_brief`" in naming
    assert "`multi-agent-brief-workflow`" in naming
    assert "`mabw.*` schema ids" in naming
    assert "historical run IDs" in naming
    assert "deleting `/mabw`" in naming
    assert "user friction or packaging evidence" in normalized
    assert "non-editable install smoke coverage" in normalized
    assert "must not rewrite frozen archives or schema ids in place" in normalized


def test_compatibility_surfaces_remain_available_but_not_first_user() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'briefloop = "multi_agent_brief.cli.main:main"' in pyproject
    assert 'multi-agent-brief = "multi_agent_brief.cli.main:main"' in pyproject

    mabw_command = (ROOT / ".claude" / "commands" / "mabw.md").read_text(encoding="utf-8")
    assert "The command name `/mabw` is retained for compatibility" in mabw_command
    assert "BRIEFLOOP_CLI=multi-agent-brief" in mabw_command

    briefloop_command = (ROOT / ".claude" / "commands" / "briefloop.md").read_text(encoding="utf-8")
    first_screen = briefloop_command.split("## Routing", maxsplit=1)[0]
    assert "/briefloop new" in first_screen
    assert "/mabw" not in first_screen
    assert "multi-agent-brief" not in first_screen
