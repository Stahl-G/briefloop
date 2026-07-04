"""Runtime state facade public-surface guardrails."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_STATE_MODULE = "multi_agent_brief.orchestrator.runtime_state"
PINNED_EXTRA_EXPORTS = {"new_run_id"}


def test_runtime_state_all_matches_in_repo_from_imports():
    runtime_state = importlib.import_module(RUNTIME_STATE_MODULE)
    expected = _runtime_state_from_imports() | PINNED_EXTRA_EXPORTS

    assert set(runtime_state.__all__) == expected
    assert runtime_state.__all__ == sorted(runtime_state.__all__)
    assert "__getattr__" not in vars(runtime_state)


def test_runtime_state_facade_does_not_proxy_impl_internals():
    runtime_state = importlib.import_module(RUNTIME_STATE_MODULE)

    assert not hasattr(runtime_state, "_impl")
    assert hasattr(runtime_state, "operations")
    assert "operations" not in runtime_state.__all__
    assert not hasattr(runtime_state, "_append_jsonl")
    assert not hasattr(runtime_state, "_sha256_file")
    assert not hasattr(runtime_state, "_allowed_decisions_for_stage")
    assert hasattr(runtime_state, "new_run_id")
    assert not hasattr(runtime_state, "EVENT_TYPES")
    assert not hasattr(runtime_state, "E_TRANSACTION_INTEGRITY")


def test_operations_compatibility_surface_is_preserved() -> None:
    """operations.py stays a compatibility surface during/after the split.

    This locks the names that tests and legacy callers reach through
    runtime_state.operations.<name>. It intentionally does not lock the full
    historical implementation surface, so unused internals can still move or
    disappear later.
    """

    runtime_state = importlib.import_module(RUNTIME_STATE_MODULE)

    compat_names = [
        "check_runtime_state",
        "complete_finalize_transaction",
        "complete_repair_transaction",
        "complete_stage_transaction",
        "enrich_claim_metadata_transaction",
        "freeze_claim_ledger_transaction",
        "import_fact_layer_transaction",
        "initialize_runtime_state",
        "record_decision",
        "raise_if_active_repair_open",
        "raise_if_auditable_target_complete_blocks_downstream",
        "show_runtime_state",
        "start_repair_transaction",
        "EVENT_LOG_SCHEMA",
        "E_ACTIVE_REPAIR_OPEN",
        "E_ARTIFACT_INVALID",
        "E_ASSESSMENT_TARGET_COMPLETE",
        "E_CLAIM_DRAFT_CONTRACT_INVALID",
        "E_COMPLETION_TRANSACTION_REQUIRED",
        "E_ILLEGAL_TRANSITION",
        "E_FACT_LAYER_IMPORT_INVALID",
        "E_QUALITY_GATE_REQUIRED",
        "E_READER_FINAL_GATE_FAILED",
        "E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN",
        "E_REPAIR_NO_LEGAL_ROUTE",
        "E_REPAIR_TRANSACTION_REQUIRED",
        "E_REQUIRED_ARTIFACT_MISSING",
        "E_RUN_ARCHIVE_CONFLICT",
        "E_STAGE_ALREADY_COMPLETED",
        "E_STAGE_MISMATCH",
        "E_TRANSACTION_INTEGRITY",
        "E_TRANSACTION_PARTIAL_WRITE",
        "FACT_LAYER_IMPORT_SCHEMA",
        "RunArchiveError",
        "RuntimeStateError",
        "_write_json_atomic",
        "_build_artifact_registry",
        "_source_evidence_metadata_from_file",
    ]
    missing = [name for name in compat_names if not hasattr(runtime_state.operations, name)]
    assert not missing, f"operations compatibility surface lost: {missing}"


def test_operations_all_preserves_legacy_error_code_exports() -> None:
    runtime_state = importlib.import_module(RUNTIME_STATE_MODULE)

    legacy_error_exports = {
        "E_ACTIVE_REPAIR_OPEN",
        "E_ARTIFACT_INVALID",
        "E_ASSESSMENT_TARGET_COMPLETE",
        "E_CLAIM_DRAFT_CONTRACT_INVALID",
        "E_COMPLETION_TRANSACTION_REQUIRED",
        "E_FACT_LAYER_IMPORT_INVALID",
        "E_ILLEGAL_TRANSITION",
        "E_QUALITY_GATE_REQUIRED",
        "E_READER_FINAL_GATE_FAILED",
        "E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN",
        "E_REPAIR_NO_LEGAL_ROUTE",
        "E_REPAIR_TRANSACTION_REQUIRED",
        "E_REQUIRED_ARTIFACT_MISSING",
        "E_RUNTIME_STATE_NOT_INITIALIZED",
        "E_STAGE_ALREADY_COMPLETED",
        "E_STAGE_MISMATCH",
        "E_TRANSACTION_INTEGRITY",
        "E_TRANSACTION_PARTIAL_WRITE",
    }

    assert legacy_error_exports <= set(runtime_state.operations.__all__)


def test_operations_all_exports_are_defined() -> None:
    runtime_state = importlib.import_module(RUNTIME_STATE_MODULE)

    missing = [name for name in runtime_state.operations.__all__ if not hasattr(runtime_state.operations, name)]

    assert missing == []


def test_operations_is_compatibility_facade_only() -> None:
    operations_path = REPO_ROOT / "src" / "multi_agent_brief" / "orchestrator" / "runtime_state" / "operations.py"
    tree = ast.parse(operations_path.read_text(encoding="utf-8"), filename=str(operations_path))

    definitions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]

    assert definitions == []


def test_runtime_state_lifecycle_does_not_import_fact_layer() -> None:
    lifecycle_path = REPO_ROOT / "src" / "multi_agent_brief" / "orchestrator" / "runtime_state" / "lifecycle.py"
    tree = ast.parse(lifecycle_path.read_text(encoding="utf-8"), filename=str(lifecycle_path))

    forbidden_modules = {
        "multi_agent_brief.orchestrator.runtime_state.fact_layer",
        "multi_agent_brief.orchestrator.runtime_state.operations",
    }
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
            imports.append(str(node.module))
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if alias.name in forbidden_modules)

    assert imports == []


def test_claim_ledger_split_has_no_reverse_runtime_state_imports() -> None:
    runtime_state_root = REPO_ROOT / "src" / "multi_agent_brief" / "orchestrator" / "runtime_state"

    freeze_imports = _module_imports(runtime_state_root / "claim_ledger_freeze.py")
    assert "multi_agent_brief.orchestrator.runtime_state.claim_metadata_enrichment" not in freeze_imports
    assert "multi_agent_brief.orchestrator.runtime_state.operations" not in freeze_imports

    enrichment_imports = _module_imports(runtime_state_root / "claim_metadata_enrichment.py")
    assert "multi_agent_brief.orchestrator.runtime_state.operations" not in enrichment_imports


def test_core_manifest_has_no_live_consumers():
    consumers: list[str] = []
    for path in _python_files():
        if path.name == "test_runtime_state_public_surface.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "multi_agent_brief.core.manifest" in text:
            consumers.append(str(path.relative_to(REPO_ROOT)))

    assert consumers == []


def test_production_code_does_not_import_operations_facade():
    consumers: list[str] = []
    for path in REPO_ROOT.joinpath("src").rglob("*.py"):
        if path.name == "operations.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == f"{RUNTIME_STATE_MODULE}.operations":
                consumers.append(str(path.relative_to(REPO_ROOT)))
            elif isinstance(node, ast.Import):
                consumers.extend(
                    str(path.relative_to(REPO_ROOT))
                    for alias in node.names
                    if alias.name == f"{RUNTIME_STATE_MODULE}.operations"
                )

    assert consumers == []


def _runtime_state_from_imports() -> set[str]:
    names: set[str] = set()
    for path in _python_files():
        if path.name == "test_runtime_state_public_surface.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == RUNTIME_STATE_MODULE:
                names.update(alias.name for alias in node.names)
    return names


def _python_files() -> list[Path]:
    return sorted([*REPO_ROOT.joinpath("src").rglob("*.py"), *REPO_ROOT.joinpath("tests").rglob("*.py")])


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    return imports
