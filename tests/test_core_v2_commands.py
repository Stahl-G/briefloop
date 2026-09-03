from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.cli.main import main
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
    assert not (
        workspace / "output" / "intermediate" / "runtime_manifest.json"
    ).exists()
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


def test_core_v2_imports_are_confined_to_bound_importers() -> None:
    package_root = ROOT / "src" / "multi_agent_brief"

    def imports_core_v2(node: ast.AST) -> bool:
        if isinstance(node, ast.ImportFrom):
            modules = (node.module or "",)
        elif isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        else:
            return False
        return any(
            module == "multi_agent_brief.core_run_v2"
            or module.startswith("multi_agent_brief.core_run_v2.")
            for module in modules
        )

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
        # RUN-UX-1A strict RunDirection validation verifies the frozen output
        # contract against the sole Core-owned catalog.
        "contracts/v2.py",
        "product/brief_html/builder.py",
        "product/init_web/submit.py",
        "product/post_final_assessment.py",
        "product/post_final_assessment_projection.py",
        "product/post_final_review.py",
        "runtime_host_v2/initialization.py",
        "runtime_host_v2/projections.py",
        "runtime_host_v2/service.py",
        "runtime_host_v2/source_routes.py",
        # Bootstrap resolves the Human semantic extent once before freezing it
        # into the Store-bound RunDirection.
        "workspace/init_profile.py",
        # evaluation_v2 staging drives the core-run stage machine for the
        # experimental rollout evaluation; the rollout adapter invokes the
        # domain verifier on the staged Store head.
        "evaluation_v2/staging.py",
        "evaluation_v2/codex_rollout.py",
    }
    findings: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if imports_core_v2(node):
                if relative not in allowed and not relative.startswith("core_run_v2/"):
                    findings.append(f"{relative}:{node.lineno}")
    assert findings == []

    synthetic_findings: list[str] = []
    for synthetic_relative, source in (
        (
            "product/unknown_core_from_consumer.py",
            (
                "from multi_agent_brief.core_run_v2.verifier "
                "import CoreRunDomainVerifier\n"
            ),
        ),
        (
            "product/unknown_core_import_consumer.py",
            "import multi_agent_brief.core_run_v2.verifier\n",
        ),
    ):
        synthetic = ast.parse(source)
        synthetic_findings.extend(
            f"{synthetic_relative}:{node.lineno}"
            for node in ast.walk(synthetic)
            if imports_core_v2(node)
            and synthetic_relative not in allowed
            and not synthetic_relative.startswith("core_run_v2/")
        )
    assert synthetic_findings == [
        "product/unknown_core_from_consumer.py:1",
        "product/unknown_core_import_consumer.py:1",
    ]


def test_core_v2_does_not_import_legacy_runtime_writers() -> None:
    # LD2-3 deleted every module named below. The guard is deliberately kept
    # after the deletion: it is the tripwire that stops a legacy writer import
    # from being reintroduced in any form. A name resolving to nothing is the
    # expected state here, not dead code.
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
                or name.startswith("multi_agent_brief.contracts.runtime_contracts")
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
