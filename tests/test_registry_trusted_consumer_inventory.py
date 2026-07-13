from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
COMPLETION_MODULE_PACKAGE = "multi_agent_brief.orchestrator.runtime_state"
REGISTRY_READER_MODULE = f"{COMPLETION_MODULE_PACKAGE}.artifact_registry_read"
APPROVED_COMPLETION_REGISTRY_SYMBOLS = {
    "CanonicalRegistryView",
    "interpret_artifact_registry",
}
TARGET_CALLS = {
    "_build_artifact_registry",
    "check_runtime_state",
    "interpret_artifact_registry",
    "show_runtime_state",
}


def _resolved_import_targets(
    node: ast.Import | ast.ImportFrom,
) -> tuple[tuple[str, str | None], ...]:
    """Resolve ordinary import syntax without importing the referenced module.

    The returned pairs are ``(module, imported_name)``.  ``import module`` has
    no imported name; ``from module import name`` keeps both components so the
    caller can distinguish a symbol import from a submodule import.
    """

    if isinstance(node, ast.Import):
        return tuple((item.name, None) for item in node.names)

    if node.level:
        relative_name = f"{'.' * node.level}{node.module or ''}"
        try:
            module = importlib.util.resolve_name(
                relative_name,
                COMPLETION_MODULE_PACKAGE,
            )
        except (ImportError, ValueError) as exc:
            raise ValueError("unresolvable relative import") from exc
    else:
        module = node.module or ""
    if not module:
        raise ValueError("import-from statement has no resolvable module")
    return tuple((module, item.name) for item in node.names)


def _python_registry_accesses() -> set[tuple[str, str, str]]:
    paths = sorted((ROOT / "src" / "multi_agent_brief").rglob("*.py"))
    paths.append(ROOT / "integrations" / "hermes-plugin" / "mabw" / "tools.py")
    accesses: set[tuple[str, str, str]] = set()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        accesses.update(
            _python_registry_accesses_from_source(
                relative,
                path.read_text(encoding="utf-8"),
            )
        )
    return accesses


def _python_registry_accesses_from_source(
    relative: str,
    source: str,
) -> set[tuple[str, str, str]]:
    tree = ast.parse(source, filename=relative)
    accesses: set[tuple[str, str, str]] = set()
    functions: list[str] = []
    imported_names: dict[str, str] = {}

    class Visitor(ast.NodeVisitor):
        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for imported in node.names:
                if imported.name in TARGET_CALLS:
                    imported_names[imported.asname or imported.name] = imported.name
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            functions.append(node.name)
            self.generic_visit(node)
            functions.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            function = node.func
            local_name = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else ""
            )
            name = imported_names.get(local_name, local_name)
            if name in TARGET_CALLS:
                accesses.add(
                    (
                        relative,
                        functions[-1] if functions else "<module>",
                        f"call:{name}",
                    )
                )
            self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            if (
                isinstance(node.slice, ast.Constant)
                and node.slice.value == "artifact_registry"
            ):
                accesses.add(
                    (
                        relative,
                        functions[-1] if functions else "<module>",
                        "mapping:artifact_registry",
                    )
                )
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if node.id == "ARTIFACT_REGISTRY_SCHEMA" and isinstance(
                node.ctx, ast.Load
            ):
                accesses.add(
                    (
                        relative,
                        functions[-1] if functions else "<module>",
                        "schema:ARTIFACT_REGISTRY_SCHEMA",
                    )
                )

        def visit_Constant(self, node: ast.Constant) -> None:
            if (
                isinstance(node.value, str)
                and "artifact_registry.json" in node.value
            ):
                accesses.add(
                    (
                        relative,
                        functions[-1] if functions else "<module>",
                        "literal:artifact_registry.json",
                    )
                )

    Visitor().visit(tree)
    return accesses


def _completion_registry_boundary_findings(source: str) -> tuple[set[str], int]:
    """Enforce the closed access boundary for the migrated completion consumer.

    The repository-wide inventory below is an exact list of known syntactic
    access points, not a proof about arbitrary Python data flow.  This stricter
    check applies to the migrated consumer itself: its only runtime-state path
    lookup is a direct ``event_log`` selection, and its only Registry input is
    one call to the typed interpreter.
    """

    tree = ast.parse(source, filename="completion_projection.py")
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    imported_names: dict[str, str] = {}
    forbidden = {
        "ARTIFACT_REGISTRY_SCHEMA",
        "_build_artifact_registry",
        "check_runtime_state",
        "show_runtime_state",
    }
    findings: set[str] = set()
    interpreter_call_count = 0

    def enclosing_function_name(node: ast.AST) -> str:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current.name
            current = parents.get(current)
        return "<module>"

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        try:
            resolved_targets = _resolved_import_targets(node)
        except ValueError:
            findings.add("unresolvable_ordinary_import")
            continue

        function_name = enclosing_function_name(node)
        for module, imported_name in resolved_targets:
            if imported_name == "*":
                findings.add("star_import_forbidden")
                continue

            imports_reader_module = (
                module == REGISTRY_READER_MODULE
                or (
                    imported_name is not None
                    and f"{module}.{imported_name}" == REGISTRY_READER_MODULE
                )
            )
            if not imports_reader_module:
                continue
            if function_name != "build_completion_projection":
                findings.add("typed_registry_import_must_be_build_local")
                continue
            if not (
                module == REGISTRY_READER_MODULE
                and imported_name in APPROVED_COMPLETION_REGISTRY_SYMBOLS
            ):
                findings.add("typed_registry_import_requires_exact_symbols")

        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                local_name = imported.asname or imported.name
                imported_names[local_name] = imported.name
                if imported.name in forbidden:
                    findings.add(f"forbidden_import:{imported.name}")

    def call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return imported_names.get(node.func.id, node.func.id)
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    def is_runtime_state_paths_call(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and call_name(node) == "runtime_state_paths"

    def literal_subscript_key(node: ast.Subscript) -> object:
        return node.slice.value if isinstance(node.slice, ast.Constant) else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node)
            if name == "interpret_artifact_registry":
                interpreter_call_count += 1
            if name in forbidden:
                findings.add(f"forbidden_call:{name}")
            if not is_runtime_state_paths_call(node):
                continue
            parent = parents.get(node)
            if not (
                isinstance(parent, ast.Subscript)
                and parent.value is node
                and literal_subscript_key(parent) == "event_log"
            ):
                findings.add("runtime_state_paths_must_select_event_log_directly")
        elif isinstance(node, ast.Name) and node.id == "ARTIFACT_REGISTRY_SCHEMA":
            findings.add("forbidden_schema_reference")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "artifact_registry.json" in node.value
        ):
            findings.add("forbidden_raw_registry_literal")

    if interpreter_call_count != 1:
        findings.add(f"typed_interpreter_call_count:{interpreter_call_count}")
    return findings, interpreter_call_count


CANONICAL_OWNER_OR_CONSUMER = {
    (
        "src/multi_agent_brief/orchestrator/runtime_state/artifact_registry_read.py",
        "<module>",
        "literal:artifact_registry.json",
    ),
    (
        "src/multi_agent_brief/orchestrator/runtime_state/artifact_registry_read.py",
        "_interpret_materialized_registry",
        "call:_build_artifact_registry",
    ),
    (
        "src/multi_agent_brief/orchestrator/runtime_state/completion_projection.py",
        "build_completion_projection",
        "call:interpret_artifact_registry",
    ),
    (
        "src/multi_agent_brief/status.py",
        "build_workspace_status",
        "call:interpret_artifact_registry",
    ),
}


WRITER_TRANSACTION_OR_DOMAIN_OWNER = {
    ("src/multi_agent_brief/cli/deliver_commands.py", "_refresh_runtime_state_before_delivery", "call:check_runtime_state"),
    ("src/multi_agent_brief/cli/finalize_commands.py", "_preflight_runtime_state_before_finalize", "call:check_runtime_state"),
    ("src/multi_agent_brief/cli/state_commands.py", "handle", "call:check_runtime_state"),
    ("src/multi_agent_brief/feedback/feedback_state.py", "_runtime_run_id", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/handoff.py", "write_handoff_and_state", "call:check_runtime_state"),
    ("src/multi_agent_brief/orchestrator/recovery_state.py", "interpret_recovery_state", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/recovery_state.py", "<module>", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/recovery_state.py", "load_recovery_context_verdict", "schema:ARTIFACT_REGISTRY_SCHEMA"),
    ("src/multi_agent_brief/orchestrator/recovery_state.py", "load_recovery_context_verdict", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/run_archive.py", "<module>", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/artifact_registry.py", "<module>", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/artifact_registry.py", "_artifact_registry_sha", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/artifact_registry.py", "_build_artifact_registry", "schema:ARTIFACT_REGISTRY_SCHEMA"),
    ("src/multi_agent_brief/orchestrator/runtime_state/artifact_registry.py", "interpret_frozen_artifact_integrity", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "_append_current_claim_ledger_registry_binding_reasons", "schema:ARTIFACT_REGISTRY_SCHEMA"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "_append_current_claim_ledger_registry_binding_reasons", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "_append_current_intake_binding_reasons", "schema:ARTIFACT_REGISTRY_SCHEMA"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "_append_current_intake_binding_reasons", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "_registry_bound_to_current_intake", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "_registry_bound_to_current_intake", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "_registry_bound_to_current_intake", "schema:ARTIFACT_REGISTRY_SCHEMA"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "freeze_claim_ledger_transaction", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "freeze_claim_ledger_transaction", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_ledger_freeze.py", "freeze_claim_ledger_transaction", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_metadata_enrichment.py", "enrich_claim_metadata_transaction", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_metadata_enrichment.py", "enrich_claim_metadata_transaction", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/claim_metadata_enrichment.py", "enrich_claim_metadata_transaction", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/decisions.py", "record_decision", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/fact_layer.py", "import_fact_layer_transaction", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/fact_layer.py", "import_fact_layer_transaction", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/fact_layer.py", "import_fact_layer_transaction", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/lifecycle.py", "check_runtime_state", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/lifecycle.py", "check_runtime_state", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/lifecycle.py", "check_runtime_state", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/lifecycle.py", "initialize_runtime_state", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/lifecycle.py", "initialize_runtime_state", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/paths.py", "<module>", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "_repair_changed_artifact_reasons", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "complete_repair_transaction", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "complete_repair_transaction", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "complete_repair_transaction", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "start_repair_transaction", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "start_repair_transaction", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "supersede_stage_artifact_transaction", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "supersede_stage_artifact_transaction", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "supersede_stage_artifact_transaction", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/repair.py", "supersede_stage_artifact_transaction", "mapping:artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/stage_completion.py", "_complete_stage_transaction", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/orchestrator/runtime_state/stage_completion.py", "_complete_stage_transaction", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/stage_completion.py", "_complete_stage_transaction", "mapping:artifact_registry"),
}


PENDING_PILOT_CONSUMERS = {
    ("src/multi_agent_brief/cli/state_commands.py", "handle", "call:show_runtime_state"),
    ("src/multi_agent_brief/orchestrator/runtime_state/lifecycle.py", "show_runtime_state", "mapping:artifact_registry"),
    ("src/multi_agent_brief/hermes/adapter.py", "<module>", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/hermes/adapter.py", "build_hermes_cron_plan", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/hermes/adapter.py", "render_hermes_prompt", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/handoff.py", "<module>", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/handoff.py", "_codex_handoff", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/handoff.py", "_hermes_handoff", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/outputs/finalize.py", "_append_python_audit_binding_findings", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/quality_gates/state.py", "_blocking_repair_guidance", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/quality_gates/state.py", "_ensure_frozen_report_is_unchanged", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/quality_gates/state.py", "_frozen_report_record", "call:show_runtime_state"),
    ("src/multi_agent_brief/quality_gates/state.py", "_legacy_quality_gate_materialization_guidance", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/quality_gates/state.py", "_runtime_intake_context", "call:check_runtime_state"),
    ("src/multi_agent_brief/quality_gates/state.py", "_runtime_intake_context", "call:show_runtime_state"),
    ("src/multi_agent_brief/quality_gates/state.py", "_stale_quality_gate_blocker_guidance", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/repair/router.py", "<module>", "schema:ARTIFACT_REGISTRY_SCHEMA"),
    ("src/multi_agent_brief/repair/router.py", "_findings_from_artifact_registry", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/repair/router.py", "_findings_from_frozen_artifact_integrity", "call:_build_artifact_registry"),
    ("src/multi_agent_brief/repair/router.py", "_findings_from_frozen_artifact_integrity", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/repair/router.py", "_input_paths", "mapping:artifact_registry"),
    ("src/multi_agent_brief/runtime_assets.py", "_workspace_command_text", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/runtime_assets.py", "_workspace_skill_text", "literal:artifact_registry.json"),
    ("integrations/hermes-plugin/mabw/tools.py", "run_handoff", "literal:artifact_registry.json"),
}


EXPLICIT_DEFERRED_OR_NONSEMANTIC = {
    ("src/multi_agent_brief/controls/switchboard.py", "_control_provenance", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/controls/switchboard.py", "_switchboard_inputs", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/evaluation_cases/runner.py", "_assert_artifact_statuses", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/evaluation_cases/runner.py", "_assert_workflow_state", "call:show_runtime_state"),
    ("src/multi_agent_brief/evaluation_cases/runner.py", "_ensure_fixture_audit_binding_control_chain", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/evaluation_cases/runner.py", "_prepare_workspace_case", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/evaluation_cases/runner.py", "_run_action", "call:check_runtime_state"),
    ("src/multi_agent_brief/experiments/experiment_080.py", "_auditable_audit_binding_projection", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/experiments/experiment_080.py", "_auditable_target_artifacts", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/experiments/experiment_080.py", "_scorecard_archive_projection", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/product/bundle_projection.py", "_audit_records", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/product/quality_panel.py", "build_quality_panel", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/orchestrator/runtime_state/stage_completion.py", "raise_if_auditable_target_complete_blocks_downstream", "mapping:artifact_registry"),
    ("src/multi_agent_brief/provenance/builder.py", "build_provenance_graph", "mapping:artifact_registry"),
    ("src/multi_agent_brief/provenance/builder.py", "_load_required_state", "literal:artifact_registry.json"),
    ("src/multi_agent_brief/provenance/builder.py", "_load_required_state", "mapping:artifact_registry"),
}


def test_known_registry_reader_inventory_has_no_unclassified_access() -> None:
    expected = (
        CANONICAL_OWNER_OR_CONSUMER
        | WRITER_TRANSACTION_OR_DOMAIN_OWNER
        | PENDING_PILOT_CONSUMERS
        | EXPLICIT_DEFERRED_OR_NONSEMANTIC
    )
    assert _python_registry_accesses() == expected


def test_completion_projection_has_only_the_typed_registry_read() -> None:
    completion_accesses = {
        item
        for item in _python_registry_accesses()
        if item[0].endswith("/completion_projection.py")
    }
    assert completion_accesses == {
        (
            "src/multi_agent_brief/orchestrator/runtime_state/completion_projection.py",
            "build_completion_projection",
            "call:interpret_artifact_registry",
        )
    }
    source = (
        ROOT
        / "src"
        / "multi_agent_brief"
        / "orchestrator"
        / "runtime_state"
        / "completion_projection.py"
    ).read_text(encoding="utf-8")
    findings, interpreter_call_count = _completion_registry_boundary_findings(source)
    assert findings == set()
    assert interpreter_call_count == 1


def test_registry_inventory_detects_path_mapping_and_import_aliases() -> None:
    path_mapping = _python_registry_accesses_from_source(
        "src/example/raw_reader.py",
        """
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths

def read_registry(workspace):
    return runtime_state_paths(workspace)[\"artifact_registry\"].read_text()
""",
    )
    assert path_mapping == {
        (
            "src/example/raw_reader.py",
            "read_registry",
            "mapping:artifact_registry",
        )
    }

    aliased_interpreter = _python_registry_accesses_from_source(
        "src/example/aliased_reader.py",
        """
from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import (
    interpret_artifact_registry as read_registry,
)

def project(workspace):
    return read_registry(workspace=workspace)
""",
    )
    assert aliased_interpreter == {
        (
            "src/example/aliased_reader.py",
            "project",
            "call:interpret_artifact_registry",
        )
    }


def test_completion_boundary_rejects_path_mapping_escape_forms() -> None:
    cases = {
        "mapping_get": """
from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import interpret_artifact_registry
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths

def project(workspace):
    interpret_artifact_registry(workspace=workspace)
    return runtime_state_paths(workspace).get("artifact_registry")
""",
        "module_alias": """
from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import interpret_artifact_registry
import multi_agent_brief.orchestrator.runtime_state.paths as state_paths

def project(workspace):
    interpret_artifact_registry(workspace=workspace)
    return state_paths.runtime_state_paths(workspace)["artifact_registry"]
""",
        "assigned_then_accessed": """
from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import interpret_artifact_registry
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths

def project(workspace):
    interpret_artifact_registry(workspace=workspace)
    paths = runtime_state_paths(workspace)
    return paths["artifact_registry"]
""",
        "dynamic_selector": """
from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import interpret_artifact_registry
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths

def project(workspace, selector):
    interpret_artifact_registry(workspace=workspace)
    return runtime_state_paths(workspace)[selector]
""",
    }
    for case_id, source in cases.items():
        findings, interpreter_call_count = _completion_registry_boundary_findings(source)
        assert "runtime_state_paths_must_select_event_log_directly" in findings, case_id
        assert interpreter_call_count == 1, case_id


def test_completion_boundary_allows_only_direct_event_log_selection() -> None:
    source = """
from multi_agent_brief.orchestrator.runtime_state.paths import (
    runtime_state_paths as state_paths,
)

def build_completion_projection(workspace):
    from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import (
        interpret_artifact_registry as read_registry,
    )
    read_registry(workspace=workspace)
    return state_paths(workspace)["event_log"]
"""
    findings, interpreter_call_count = _completion_registry_boundary_findings(source)
    assert findings == set()
    assert interpreter_call_count == 1


@pytest.mark.parametrize(
    "import_statement",
    [
        (
            "import multi_agent_brief.orchestrator.runtime_state."
            "artifact_registry_read as registry_reader"
        ),
        (
            "from multi_agent_brief.orchestrator.runtime_state import "
            "artifact_registry_read as registry_reader"
        ),
        (
            "from multi_agent_brief.orchestrator.runtime_state import "
            "paths, artifact_registry_read as registry_reader"
        ),
        (
            "from multi_agent_brief.orchestrator.runtime_state."
            "artifact_registry_read import CanonicalRegistryView, "
            "interpret_artifact_registry"
        ),
        "from . import artifact_registry_read as registry_reader",
        (
            "from .artifact_registry_read import CanonicalRegistryView, "
            "interpret_artifact_registry"
        ),
    ],
)
def test_completion_boundary_rejects_module_scope_registry_import_forms(
    import_statement: str,
) -> None:
    source = f"""
{import_statement}

def build_completion_projection(workspace):
    return interpret_artifact_registry(workspace=workspace)
"""
    findings, interpreter_call_count = _completion_registry_boundary_findings(source)
    assert "typed_registry_import_must_be_build_local" in findings
    assert interpreter_call_count == 1


@pytest.mark.parametrize(
    "import_statement",
    [
        (
            "from multi_agent_brief.orchestrator.runtime_state."
            "artifact_registry_read import *"
        ),
        "from .artifact_registry_read import *",
    ],
)
def test_completion_boundary_rejects_registry_star_import(
    import_statement: str,
) -> None:
    source = f"""
{import_statement}

def build_completion_projection(workspace):
    return interpret_artifact_registry(workspace=workspace)
"""
    findings, interpreter_call_count = _completion_registry_boundary_findings(source)
    assert "star_import_forbidden" in findings
    assert interpreter_call_count == 1


def test_completion_boundary_rejects_unresolvable_relative_import() -> None:
    source = """
from .....runtime_state import artifact_registry_read

def build_completion_projection(workspace):
    return artifact_registry_read.interpret_artifact_registry(workspace=workspace)
"""
    findings, interpreter_call_count = _completion_registry_boundary_findings(source)
    assert "unresolvable_ordinary_import" in findings
    assert interpreter_call_count == 1


def test_completion_boundary_allows_build_local_relative_exact_symbols() -> None:
    source = """
def build_completion_projection(workspace):
    from .artifact_registry_read import (
        CanonicalRegistryView,
        interpret_artifact_registry,
    )
    verdict = interpret_artifact_registry(workspace=workspace)
    return isinstance(verdict, CanonicalRegistryView)
"""
    findings, interpreter_call_count = _completion_registry_boundary_findings(source)
    assert findings == set()
    assert interpreter_call_count == 1


@pytest.mark.parametrize(
    "import_statement",
    [
        (
            "from multi_agent_brief.orchestrator.runtime_state import "
            "artifact_registry_read as registry_reader"
        ),
        (
            "import multi_agent_brief.orchestrator.runtime_state."
            "artifact_registry_read as registry_reader"
        ),
    ],
)
def test_completion_boundary_rejects_build_local_registry_module_import(
    import_statement: str,
) -> None:
    source = f"""
def build_completion_projection(workspace):
    {import_statement}
    return registry_reader.interpret_artifact_registry(workspace=workspace)
"""
    findings, interpreter_call_count = _completion_registry_boundary_findings(source)
    assert "typed_registry_import_requires_exact_symbols" in findings
    assert interpreter_call_count == 1


def test_runtime_instruction_registry_inventory_is_explicit() -> None:
    roots = (
        ROOT / ".agents" / "hermes-skills",
        ROOT / "integrations" / "hermes-plugin" / "mabw",
    )
    observed = {
        path.relative_to(ROOT).as_posix()
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and "artifact_registry.json" in path.read_text(encoding="utf-8")
    }
    assert observed == {
        ".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md",
        "integrations/hermes-plugin/mabw/skills/briefloop/SKILL.md",
        "integrations/hermes-plugin/mabw/skills/briefloop/references/control-record-map.md",
        "integrations/hermes-plugin/mabw/skills/briefloop/references/repair-protocol.md",
        "integrations/hermes-plugin/mabw/skills/mabw-workflow/references/artifact-contract.md",
        "integrations/hermes-plugin/mabw/tools.py",
    }
