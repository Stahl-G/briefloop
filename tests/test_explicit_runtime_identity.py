from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.deliver_commands import _delivery_run_id
from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state import check_runtime_state
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state
from multi_agent_brief.orchestrator.runtime_state import show_runtime_state
from multi_agent_brief.orchestrator.runtime_state.event_log import _load_handoff_runtime_state
from multi_agent_brief.orchestrator.runtime_state.semantic_support_acceptance import _current_run_id
from multi_agent_brief.orchestrator_contract import HISTORICAL_READ_ONLY_RUNTIMES
from multi_agent_brief.orchestrator_contract import RUNTIME_CLI_CHOICE_PLACEHOLDER
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES
from multi_agent_brief.product.release_approval import ReleaseApprovalError
from multi_agent_brief.product.release_approval import _workspace_and_run_id
from multi_agent_brief.provenance.builder import build_provenance_graph
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


@pytest.mark.parametrize("runtime", VALID_RUNTIMES)
def test_initialize_records_each_exact_canonical_runtime(tmp_path: Path, runtime: str) -> None:
    ws = _workspace(tmp_path)

    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        runtime=runtime,
    )

    assert state["manifest"]["runtime"] == runtime
    assert show_runtime_state(workspace=ws)["manifest"]["runtime"] == runtime


@pytest.mark.parametrize(
    "runtime",
    ["auto", "controls", "manual", "Hermes", "OPERATOR", "unknown", "", None],
)
def test_initialize_rejects_noncanonical_runtime_without_writes(
    tmp_path: Path,
    runtime: object,
) -> None:
    ws = _workspace(tmp_path)
    before = _files(ws)

    with pytest.raises(RuntimeStateError):
        initialize_runtime_state(
            workspace=ws,
            repo_workdir=ROOT,
            runtime=runtime,  # type: ignore[arg-type]
        )

    assert _files(ws) == before


@pytest.mark.parametrize("runtime", ["auto", "controls", "manual", "Hermes", "OPERATOR", "unknown"])
def test_run_parser_rejects_noncanonical_runtime_without_writes(
    tmp_path: Path,
    runtime: str,
) -> None:
    ws = _workspace(tmp_path)
    before = _files(ws)

    with pytest.raises(SystemExit):
        main(["run", "--workspace", str(ws), "--runtime", runtime])

    assert _files(ws) == before


def test_existing_runtime_must_match_without_rewriting_identity(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    first = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="codex",
    )
    same = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="codex",
    )
    assert same["manifest"]["run_id"] == first["manifest"]["run_id"]
    assert same["manifest"]["runtime"] == "codex"
    before = _files(ws)

    with pytest.raises(RuntimeStateError):
        initialize_runtime_state(
            workspace=ws,
            repo_workdir=ROOT,
            runtime="operator",
        )
    assert _files(ws) == before


@pytest.mark.parametrize("historical_runtime", sorted(HISTORICAL_READ_ONLY_RUNTIMES))
def test_legacy_manifest_is_read_only_until_reset_and_archived_byte_exact(
    tmp_path: Path,
    historical_runtime: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = _workspace(tmp_path)
    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="operator",
    )
    manifest_path = ws / INTERMEDIATE / "runtime_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"] = historical_runtime
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    historical_bytes = manifest_path.read_bytes()
    before = _files(ws)

    readable = show_runtime_state(workspace=ws)
    assert readable["manifest"]["runtime"] == historical_runtime
    with pytest.raises(RuntimeStateError):
        initialize_runtime_state(
            workspace=ws,
            repo_workdir=ROOT,
            runtime="operator",
        )
    assert _files(ws) == before

    status = build_workspace_status(ws)
    assert status["runtime"]["identity_status"] == "historical_read_only"
    assert "--reset-state" in status["suggested_next_command"]
    assert f"--runtime {historical_runtime}" not in status["suggested_next_command"]

    # LEGACY-DELETE: retired public state/controls/gates/feedback commands on a
    # legacy-JSON workspace authority. The mutating-consumer invariant is kept
    # by the direct-seam assertions in
    # test_mutating_runtime_consumers_reject_historical_identity_without_writes.
    assert main([
        "controls",
        "build-switchboard",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ]) == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    assert _files(ws) == before

    reset = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="codebuddy",
        reset_state=True,
    )
    archive = ws / INTERMEDIATE / f"runtime_manifest.{state['manifest']['run_id']}.json"
    assert archive.read_bytes() == historical_bytes
    assert reset["manifest"]["run_id"] != state["manifest"]["run_id"]
    assert reset["manifest"]["runtime"] == "codebuddy"


def test_active_adapter_entrypoints_bind_runtime_literals() -> None:
    surfaces = {
        "claude": [
            ROOT / ".claude/commands/briefloop.md",
            ROOT / ".claude/commands/generate-brief.md",
        ],
        "opencode": [
            ROOT / ".opencode/commands/briefloop.md",
            ROOT / ".opencode/commands/generate-brief.md",
        ],
        "codebuddy": [
            ROOT / ".codebuddy/skills/briefloop/SKILL.md",
            ROOT / "integrations/workbuddy/briefloop/SKILL.md",
            ROOT / "integrations/workbuddy/assistant/briefloop-assistant-prompt.md",
        ],
        "operator": [ROOT / "scripts/demo-deep-dive.sh"],
    }
    for runtime, paths in surfaces.items():
        for path in paths:
            text = path.read_text(encoding="utf-8")
            assert f"--runtime {runtime}" in text, path
            assert "--runtime auto" not in text, path
            assert "--runtime manual" not in text, path

    hermes_tool = (ROOT / "integrations/hermes-plugin/mabw/tools.py").read_text(
        encoding="utf-8"
    )
    assert '"--runtime", "hermes"' in hermes_tool
    assert 'args.get("runtime")' not in hermes_tool

    runtime_assets = (ROOT / "src/multi_agent_brief/runtime_assets.py").read_text(
        encoding="utf-8"
    )
    # LEGACY-DELETE: the generated `_workspace_skill_text(runtime="codex", ...)`
    # skill left with the retired pre-CX codex kit; the codex workspace kit is
    # now installed verbatim from the packaged ControlStore v2 assets.
    assert "_codex_writes" in runtime_assets
    codex_reference = (
        ROOT
        / "src/multi_agent_brief/runtime_kits/codex/skills/briefloop/references/controlstore-v2.md"
    ).read_text(encoding="utf-8")
    assert "--runtime codex" in codex_reference
    assert "--runtime auto" not in codex_reference
    assert "--runtime manual" not in codex_reference
    assert "--runtime {runtime}" in runtime_assets

    hermes_schema = (ROOT / "integrations/hermes-plugin/mabw/schemas.py").read_text(
        encoding="utf-8"
    )
    assert '"runtime"' not in hermes_schema


def test_active_generic_cli_guidance_requires_explicit_runtime_choice() -> None:
    placeholder = f"--runtime {RUNTIME_CLI_CHOICE_PLACEHOLDER}"
    surfaces = {
        ROOT / "src/multi_agent_brief/cli/init_commands.py": 2,
        ROOT / "src/multi_agent_brief/cli/onboard_commands.py": 3,
        ROOT / "src/multi_agent_brief/cli/product_commands.py": 2,
        ROOT / "src/multi_agent_brief/cli/run_commands.py": 2,
        ROOT / "src/multi_agent_brief/cli/deliver_commands.py": 2,
        ROOT / "src/multi_agent_brief/provenance/builder.py": 2,
    }
    for path, expected_count in surfaces.items():
        text = path.read_text(encoding="utf-8")
        assert text.count("RUNTIME_CLI_CHOICE_PLACEHOLDER") == expected_count, path
        assert "--runtime manual" not in text, path
        assert "--runtime auto" not in text, path

    assert placeholder == (
        "--runtime <hermes|claude|opencode|codex|codebuddy|operator>"
    )

    hermes = (ROOT / "src/multi_agent_brief/hermes/adapter.py").read_text(
        encoding="utf-8"
    )
    assert "briefloop run --workspace <workspace> --runtime hermes" in hermes

    runtime_assets = (ROOT / "src/multi_agent_brief/runtime_assets.py").read_text(
        encoding="utf-8"
    )
    assert "--runtime {runtime}" in runtime_assets

    experiment = (
        ROOT / "src/multi_agent_brief/experiments/experiment_080.py"
    ).read_text(encoding="utf-8")
    assert 'f"--runtime {runtime} --recipe fast-rerun --skip-doctor"' in experiment

    state_init_guidance = [
        ROOT / "src/multi_agent_brief/orchestrator/runtime_state/_transactions.py",
        ROOT / "src/multi_agent_brief/orchestrator/runtime_state/event_log.py",
        ROOT
        / "src/multi_agent_brief/orchestrator/runtime_state/semantic_support_acceptance.py",
    ]
    for path in state_init_guidance:
        text = path.read_text(encoding="utf-8")
        assert "state init --workspace <workspace>`" not in text, path
        assert "RUNTIME_CLI_CHOICE_PLACEHOLDER" in text, path


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
        ROOT / "docs/implementation/v0.6.0-explicit-orchestrator-contract.md",
    ]
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        assert "manual` is a legacy cli alias" not in lowered, path
        assert "manual` remains a cli compatibility alias" not in lowered, path
        assert "manual` runtime value is only a cli compatibility alias" not in lowered, path
        assert "manual` 是其 legacy cli alias" not in lowered, path
        assert "manual` runtime 值只保留为 `operator` 的 cli 兼容别名" not in lowered, path

    repo_instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "briefloop run --workspace <workspace> --runtime operator" in repo_instructions
    assert (
        "briefloop run --workspace /tmp/briefloop-smoke --runtime operator --skip-doctor"
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
        ["state", "decide", "--stage", "doctor", "--decision", "block_run", "--reason", "fixture", "--json"],
        ["controls", "build-switchboard", "--json"],
        ["gates", "check", "--json"],
        ["feedback", "plan", "--json"],
    ],
)
def test_runtime_consumers_do_not_implicitly_initialize(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    ws = _workspace(tmp_path)
    before = _files(ws)
    command = [*argv, "--workspace", str(ws)]

    # LEGACY-DELETE: retired public state/controls/gates/feedback commands. The
    # fail-closed authority guard rejects them with zero writes on a fresh
    # workspace, so runtime state is never implicitly initialized.
    assert main(command) == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert _files(ws) == before


@pytest.mark.parametrize("historical_runtime", sorted(HISTORICAL_READ_ONLY_RUNTIMES))
def test_mutating_runtime_consumers_reject_historical_identity_without_writes(
    tmp_path: Path,
    historical_runtime: str,
) -> None:
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, runtime="operator")
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    manifest_path = ws / INTERMEDIATE / "runtime_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"] = historical_runtime
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    before = _files(ws)

    with pytest.raises(RuntimeStateError):
        _delivery_run_id(ws)
    with pytest.raises(RuntimeStateError):
        _current_run_id(ws)
    with pytest.raises(ReleaseApprovalError):
        _workspace_and_run_id(ws)
    with pytest.raises(ProvenanceError):
        build_provenance_graph(workspace=ws, repo_workdir=ROOT)
    with pytest.raises(RuntimeStateError):
        _load_handoff_runtime_state(ws)

    assert _files(ws) == before


def test_runtime_identity_consumer_inventory_uses_canonical_validator() -> None:
    surfaces = {
        ROOT / "src/multi_agent_brief/cli/deliver_commands.py": "_delivery_run_id",
        ROOT
        / "src/multi_agent_brief/orchestrator/runtime_state/semantic_support_acceptance.py": "_current_run_id",
        ROOT / "src/multi_agent_brief/product/release_approval.py": "_workspace_and_run_id",
        ROOT / "src/multi_agent_brief/provenance/builder.py": "build_provenance_graph",
        ROOT / "src/multi_agent_brief/experiments/experiment_080.py": "register_run_record",
        ROOT / "src/multi_agent_brief/orchestrator/runtime_state/event_log.py": "_load_handoff_runtime_state",
    }
    for path, owner in surfaces.items():
        text = path.read_text(encoding="utf-8")
        function_body = text.split(f"def {owner}(", 1)[1].split("\ndef ", 1)[0]
        assert "require_canonical_runtime" in function_body, (path, owner)
