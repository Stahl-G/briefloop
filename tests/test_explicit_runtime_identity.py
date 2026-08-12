from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator_contract import HISTORICAL_READ_ONLY_RUNTIMES
from multi_agent_brief.orchestrator_contract import RUNTIME_CLI_CHOICE_PLACEHOLDER
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES
from multi_agent_brief.provenance.model import ProvenanceError
from multi_agent_brief.status import build_workspace_status
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent
INTERMEDIATE = Path("output/intermediate")


def _workspace(tmp_path: Path) -> Path:
    return write_workspace_files_under(
        tmp_path,
        config_text="""
project:
  name: "Explicit Runtime Identity"
output:
  path: "output"
input:
  path: "input"
""".strip(),
        user_text="# User\n",
        include_input_dir=True,
    )


def _files(workspace: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(workspace)): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "runtime", ["auto", "controls", "manual", "Hermes", "OPERATOR", "unknown"]
)
def test_run_parser_rejects_noncanonical_runtime_without_writes(
    tmp_path: Path,
    runtime: str,
) -> None:
    ws = _workspace(tmp_path)
    before = _files(ws)

    with pytest.raises(SystemExit):
        main(["run", "--workspace", str(ws), "--runtime", runtime])

    assert _files(ws) == before


def test_active_generic_cli_guidance_requires_explicit_runtime_choice() -> None:
    placeholder = f"--runtime {RUNTIME_CLI_CHOICE_PLACEHOLDER}"
    surfaces = {
        ROOT / "src/multi_agent_brief/cli/init_commands.py": 2,
        ROOT / "src/multi_agent_brief/cli/onboard_commands.py": 3,
        # ReportPack shortcuts now name the only supported runtime directly;
        # they must not reintroduce the historical runtime placeholder.
        ROOT / "src/multi_agent_brief/cli/product_commands.py": 0,
        ROOT / "src/multi_agent_brief/cli/run_commands.py": 0,
    }
    for path, expected_count in surfaces.items():
        text = path.read_text(encoding="utf-8")
        assert text.count("RUNTIME_CLI_CHOICE_PLACEHOLDER") == expected_count, path
        assert "--runtime manual" not in text, path
        assert "--runtime auto" not in text, path

    assert placeholder == (
        "--runtime <hermes|claude|opencode|codex|codebuddy|operator>"
    )

    runtime_assets = (ROOT / "src/multi_agent_brief/runtime_assets.py").read_text(
        encoding="utf-8"
    )
    # the codex workspace kit is installed verbatim from the packaged kit;
    # the codex runtime install does not interpolate a `{runtime}` template.
    assert "--runtime {runtime}" not in runtime_assets
    codex_reference = (
        ROOT
        / "src/multi_agent_brief/runtime_kits/codex/skills/briefloop/references/controlstore-v2.md"
    ).read_text(encoding="utf-8")
    assert "--runtime codex" in codex_reference


def test_active_runtime_docs_do_not_advertise_historical_aliases() -> None:
    surfaces = [
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "docs/architecture-status.md",
        ROOT / "docs/architecture-status.zh-CN.md",
        ROOT / "docs/orchestrator-architecture.md",
        ROOT / "docs/orchestrator-architecture.zh-CN.md",
        ROOT / "docs/support-matrix.md",
    ]
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        assert "manual` is a legacy cli alias" not in lowered, path
        assert "manual` remains a cli compatibility alias" not in lowered, path
        assert (
            "manual` runtime value is only a cli compatibility alias" not in lowered
        ), path
        assert "manual` 是其 legacy cli alias" not in lowered, path
        assert "manual` runtime 值只保留为 `operator` 的 cli 兼容别名" not in lowered, (
            path
        )

    repo_instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "briefloop run --workspace <workspace> --runtime codex" in repo_instructions
    assert (
        "briefloop run --workspace /tmp/briefloop-smoke --runtime codex"
        in repo_instructions
    )

    role_source = (ROOT / "configs/agent_roles.yaml").read_text(encoding="utf-8")
    assert "operator handoff surfaces" in role_source
    assert "manual handoff surfaces" not in role_source


def test_root_and_packaged_runtime_contracts_are_byte_identical() -> None:
    root = ROOT / "configs/orchestrator_contract.yaml"
    packaged = ROOT / "src/multi_agent_brief/configs/orchestrator_contract.yaml"
    assert root.read_bytes() == packaged.read_bytes()


@pytest.mark.parametrize(
    "argv",
    [
        ["state", "check", "--json"],
        [
            "state",
            "decide",
            "--stage",
            "doctor",
            "--decision",
            "block_run",
            "--reason",
            "fixture",
            "--json",
        ],
        ["controls", "build-switchboard", "--json"],
        ["gates", "check", "--json"],
        ["feedback", "plan", "--json"],
    ],
)
def test_deleted_runtime_commands_do_not_implicitly_initialize(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    ws = _workspace(tmp_path)
    before = _files(ws)
    command = [*argv, "--workspace", str(ws)]

    # retired public state/controls/gates/feedback commands no longer exist;
    # argparse rejects the unknown verb with zero writes.
    with pytest.raises(SystemExit):
        main(command)
    assert _files(ws) == before
