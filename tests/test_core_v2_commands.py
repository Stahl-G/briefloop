from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.cli.main import build_parser, main
from multi_agent_brief.contracts.v2 import CoreRunInitializeRequest
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2 import CoreRunResult, CoreRunService
from multi_agent_brief.core_run_v2.service import workspace_input_fingerprints


ROOT = Path(__file__).parents[1]


def _bind_runtime_adapter(payload: dict[str, object]) -> None:
    adapter = dict(payload["runtime_adapter_binding"])
    adapter["run_id"] = payload["run_id"]
    adapter.pop("binding_fingerprint", None)
    adapter["binding_fingerprint"] = canonical_fingerprint(adapter)
    payload["runtime_adapter_binding"] = adapter


def test_hidden_core_v2_initialize_emits_one_json_result(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request_path = workspace / "scratch" / "cli" / "submit_request.json"
    request_path.parent.mkdir(parents=True)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id="REQ-CLI-INIT-001",
        run_id="RUN-CLI-CORE-V2-001",
        workspace_id="WS-CLI-CORE-V2-001",
        input_governance_required=False,
        workspace_config_sha256="0" * 64,
        sources_config_sha256="0" * 64,
    )
    _bind_runtime_adapter(payload)
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "core-v2",
            "initialize",
            "--workspace",
            str(workspace),
            "--request",
            request_path.relative_to(workspace).as_posix(),
            "--json",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0, output.out
    assert output.err == ""
    assert output.out.count("\n") == 1
    result = json.loads(output.out)
    assert result["status"] == "committed"
    assert result["primary_record_id"] == "RUN-CLI-CORE-V2-001"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot("RUN-CLI-CORE-V2-001")
    assert snapshot.workspace_run_head is not None
    assert snapshot.workspace_run_head.current_run_id == "RUN-CLI-CORE-V2-001"
    assert len(snapshot.run_contract_bindings) == 1
    config_sha256, sources_sha256 = workspace_input_fingerprints(workspace)
    assert snapshot.run_contract_bindings[0].workspace_config_sha256 == config_sha256
    assert snapshot.run_contract_bindings[0].sources_config_sha256 == sources_sha256
    assert not (workspace / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (workspace / "output" / "intermediate" / "event_log.jsonl").exists()


def test_hidden_core_v2_cli_emits_unknown_and_nonzero_without_values(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request_path = workspace / "scratch" / "cli" / "submit_request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        json.dumps(CoreRunInitializeRequest.minimal_example),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        CoreRunService,
        "initialize",
        lambda _self, _request: CoreRunResult(
            status="commit_outcome_unknown",
            error_code="commit_outcome_unknown",
        ),
    )

    exit_code = main(
        [
            "core-v2",
            "initialize",
            "--workspace",
            str(workspace),
            "--request",
            request_path.relative_to(workspace).as_posix(),
            "--json",
        ]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "commit_outcome_unknown",
        "error_code": "commit_outcome_unknown",
    }


@pytest.mark.parametrize("filename", ["config.yaml", "sources.yaml"])
def test_hidden_core_v2_initialize_replay_uses_verified_binding_hashes(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request_path = workspace / "scratch" / "cli" / "submit_request.json"
    request_path.parent.mkdir(parents=True)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id="REQ-CLI-INIT-REPLAY",
        run_id="RUN-CLI-INIT-REPLAY",
        workspace_id="WS-CLI-INIT-REPLAY",
        input_governance_required=False,
        workspace_config_sha256="caller-value-is-ignored",
        sources_config_sha256="caller-value-is-ignored",
    )
    _bind_runtime_adapter(payload)
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    command = [
        "core-v2",
        "initialize",
        "--workspace",
        str(workspace),
        "--request",
        request_path.relative_to(workspace).as_posix(),
        "--json",
    ]

    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "committed"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision
        snapshot = store.load_snapshot("RUN-CLI-INIT-REPLAY")
    binding = snapshot.run_contract_bindings[0]

    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write("\n# changed after initialization\n")
    payload["workspace_config_sha256"] = "f" * 64
    payload["sources_config_sha256"] = "e" * 64
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    def reject_workspace_reread(*_args, **_kwargs):
        raise AssertionError("existing Store replay reread workspace inputs")

    monkeypatch.setattr(
        "multi_agent_brief.cli.core_v2_commands.workspace_input_fingerprints",
        reject_workspace_reread,
    )
    assert main(command) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "replayed"
    assert replay["receipt"] == first["receipt"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision
        replay_snapshot = store.load_snapshot("RUN-CLI-INIT-REPLAY")
    replay_binding = replay_snapshot.run_contract_bindings[0]
    assert replay_binding.workspace_config_sha256 == binding.workspace_config_sha256
    assert replay_binding.sources_config_sha256 == binding.sources_config_sha256


def test_hidden_core_v2_initialize_semantic_conflict_is_zero_write(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request_path = workspace / "scratch" / "cli" / "submit_request.json"
    request_path.parent.mkdir(parents=True)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id="REQ-CLI-INIT-CONFLICT",
        run_id="RUN-CLI-INIT-CONFLICT",
        workspace_id="WS-CLI-INIT-CONFLICT",
        input_governance_required=False,
    )
    _bind_runtime_adapter(payload)
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    command = [
        "core-v2",
        "initialize",
        "--workspace",
        str(workspace),
        "--request",
        request_path.relative_to(workspace).as_posix(),
        "--json",
    ]

    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "committed"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision = store.current_revision

    payload["run_direction"]["brief_title"] = "A changed semantic request"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(command) == 1
    conflict = json.loads(capsys.readouterr().out)
    assert conflict == {
        "error_code": "submission_replay_conflict",
        "status": "failed_uncommitted",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision


def test_hidden_core_v2_initialize_invalid_store_does_not_fallback(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request_path = workspace / "scratch" / "cli" / "submit_request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        json.dumps(CoreRunInitializeRequest.minimal_example),
        encoding="utf-8",
    )
    database = workspace / "briefloop.db"
    database.write_bytes(b"not a sqlite database")
    original = database.read_bytes()

    def reject_workspace_fallback(*_args, **_kwargs):
        raise AssertionError("invalid Store fell back to workspace inputs")

    monkeypatch.setattr(
        "multi_agent_brief.cli.core_v2_commands.workspace_input_fingerprints",
        reject_workspace_fallback,
    )
    exit_code = main(
        [
            "core-v2",
            "initialize",
            "--workspace",
            str(workspace),
            "--request",
            request_path.relative_to(workspace).as_posix(),
            "--json",
        ]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "error_code": "control_store_integrity_invalid",
        "status": "failed_uncommitted",
    }
    assert database.read_bytes() == original


def test_core_v2_cli_is_internal_and_requires_json() -> None:
    parser = build_parser(prog="briefloop")
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            [
                "core-v2",
                "initialize",
                "--workspace",
                "workspace",
                "--request",
                "scratch/cli/initialize.json",
            ]
        )
    assert exc.value.code == 2
    command_action = next(
        action
        for action in parser._actions
        if getattr(action, "choices", None) and "core-v2" in action.choices
    )
    command = command_action.choices["core-v2"]
    assert "Internal fresh-v2 core run harness" in command.description
    action = next(
        item
        for item in command._actions
        if getattr(item, "choices", None)
    )
    assert set(action.choices) == {
        "initialize",
        "doctor-check",
        "invocation-start",
        "artifact-submit",
        "claim-freeze",
        "audit-promote",
        "gate-check",
        "stage-complete",
        "integrity-check",
    }
    for subcommand in action.choices.values():
        required = {
            option
            for item in subcommand._actions
            if item.required
            for option in item.option_strings
        }
        assert required == {"--workspace", "--request", "--json"}


def test_core_v2_imports_are_confined_to_dormant_cli_package_and_bound_intake() -> None:
    package_root = ROOT / "src" / "multi_agent_brief"
    allowed = {
        "cli/core_v2_commands.py",
        "intake_v2/service.py",
        # Post-CX activation is intentional: runtime_host_v2 is the sole
        # runtime authority and cli/authority_guard.py enforces its
        # fail-closed boundary; both bind core_run_v2 directly. Importers
        # are listed exactly (no prefixes); any new importer fails here.
        "cli/authority_guard.py",
        # brief_html builder is the read-only page-1 projection (C3-sanctioned);
        # init_web submit reuses derived_id for the real bootstrap receipt id.
        "product/brief_html/builder.py",
        "product/init_web/submit.py",
        "runtime_host_v2/initialization.py",
        "runtime_host_v2/projections.py",
        "runtime_host_v2/service.py",
        "runtime_host_v2/source_routes.py",
    }
    findings: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module == "multi_agent_brief.core_run_v2" or module.startswith(
                "multi_agent_brief.core_run_v2."
            ):
                if relative not in allowed and not relative.startswith("core_run_v2/"):
                    findings.append(f"{relative}:{node.lineno}")
    assert findings == []


def test_core_v2_does_not_import_legacy_runtime_writers() -> None:
    package = ROOT / "src" / "multi_agent_brief" / "core_run_v2"
    forbidden = {
        "multi_agent_brief.status",
        "multi_agent_brief.orchestrator.runtime_state.lifecycle",
        "multi_agent_brief.orchestrator.runtime_state.artifact_registry",
        "multi_agent_brief.orchestrator.runtime_state.completion_projection",
        "multi_agent_brief.quality_gates.state.check_quality_gates",
    }
    findings: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            for name in node.names:
                imported = f"{module}.{name.name}"
                if module in forbidden or imported in forbidden:
                    findings.append(f"{path.name}:{node.lineno}:{imported}")
    assert findings == []


def test_core_v2_and_control_store_import_ownership_is_structural() -> None:
    package_root = ROOT / "src" / "multi_agent_brief"
    sqlite_imports: list[str] = []
    store_authority_imports: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            if any(name == "sqlite3" for name in imports):
                sqlite_imports.append(relative)
            if relative.startswith("control_store/") and any(
                name.startswith("multi_agent_brief.core_run_v2")
                or name.startswith(
                    "multi_agent_brief.orchestrator.runtime_state.contracts_loader"
                )
                or name.startswith("multi_agent_brief.quality_gates")
                for name in imports
            ):
                store_authority_imports.append(f"{relative}:{node.lineno}")

    assert sqlite_imports
    assert all(path.startswith("control_store/") for path in sqlite_imports)
    assert store_authority_imports == []


def test_core_v2_has_no_legacy_control_json_writer_surface() -> None:
    package = ROOT / "src" / "multi_agent_brief" / "core_run_v2"
    forbidden_names = {
        "runtime_manifest.json",
        "workflow_state.json",
        "artifact_registry.json",
        "event_log.jsonl",
        "finalize_report.json",
    }
    findings: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            if name not in {"dump", "dumps", "write_text", "write_bytes"}:
                continue
            literals = {
                item.value
                for item in ast.walk(node)
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            if literals & forbidden_names:
                findings.append(f"{path.name}:{node.lineno}")
    assert findings == []


def test_core_v2_static_authority_chokepoints_are_exact() -> None:
    package = ROOT / "src" / "multi_agent_brief"
    core = package / "core_run_v2"
    config_reads: list[tuple[str, str, str]] = []
    for path in sorted(core.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in (
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ):
            for call in (
                node for node in ast.walk(function) if isinstance(node, ast.Call)
            ):
                if not isinstance(call.func, ast.Name):
                    continue
                if call.func.id != "read_workspace_file" or len(call.args) < 2:
                    continue
                relative = call.args[1]
                if isinstance(relative, ast.Constant) and isinstance(
                    relative.value,
                    str,
                ):
                    config_reads.append((path.name, function.name, relative.value))
    assert sorted(config_reads) == [
        ("service.py", "workspace_input_fingerprints", "config.yaml"),
        ("service.py", "workspace_input_fingerprints", "sources.yaml"),
    ]

    fingerprint_calls: list[tuple[str, str]] = []
    for path in (
        core / "service.py",
        package / "cli" / "core_v2_commands.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in (
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ):
            if any(
                isinstance(call.func, ast.Name)
                and call.func.id == "workspace_input_fingerprints"
                for call in ast.walk(function)
                if isinstance(call, ast.Call)
            ):
                fingerprint_calls.append((path.name, function.name))
    assert sorted(fingerprint_calls) == [
        ("core_v2_commands.py", "_initialize_input_fingerprints"),
        ("service.py", "_doctor_check"),
        ("service.py", "_initialize"),
    ]
    service_tree = ast.parse(
        (core / "service.py").read_text(encoding="utf-8"),
        filename="service.py",
    )
    service_class = next(
        node
        for node in service_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CoreRunService"
    )
    initializer = next(
        node
        for node in service_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assert "repo_workdir" not in {item.arg for item in initializer.args.args}
    assert "repo_workdir" not in {
        item.arg for item in initializer.args.kwonlyargs
    }

    gate_tree = ast.parse(
        (core / "gates.py").read_text(encoding="utf-8"),
        filename="gates.py",
    )
    gate_state_imports = {
        item.name
        for node in ast.walk(gate_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "multi_agent_brief.quality_gates.state"
        for item in node.names
    }
    assert gate_state_imports == {"evaluate_quality_gate_findings_preloaded"}

    shared_contract_imports: dict[str, set[str]] = {}
    for filename in ("service.py", "verifier.py"):
        tree = ast.parse(
            (core / filename).read_text(encoding="utf-8"),
            filename=filename,
        )
        shared_contract_imports[filename] = {
            item.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            == "multi_agent_brief.orchestrator.runtime_state.contracts_loader"
            for item in node.names
        }
    assert "load_runtime_contract_payloads" in shared_contract_imports["service.py"]
    assert "validate_runtime_contract_payloads" in shared_contract_imports["verifier.py"]

    forbidden_store_verbs = {
        "complete_stage",
        "doctor_check",
        "evaluate_gate",
        "freeze_claims",
        "initialize_core_run",
        "record_contamination",
        "start_invocation",
    }
    store_function_names = {
        node.name
        for path in sorted((package / "control_store").glob("*.py"))
        for node in ast.walk(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
        if isinstance(node, ast.FunctionDef)
    }
    assert store_function_names.isdisjoint(forbidden_store_verbs)
