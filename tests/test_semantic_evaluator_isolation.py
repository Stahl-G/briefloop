"""AST isolation guards for the offline shadow research package."""

from __future__ import annotations

import ast
from pathlib import Path

from multi_agent_brief.contracts.base import SchemaRegistry
from multi_agent_brief.contracts.schemas.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION,
    SemanticAssessmentReportContract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "multi_agent_brief"
EVALUATOR_ROOT = SRC_ROOT / "semantic_evaluator"

EXPECTED_PACKAGE_FILES = {
    "__init__.py",
    "adapter.py",
    "adapters/__init__.py",
    "adapters/openai_responses.py",
    "adapters/synthetic_fixture.py",
    "admission.py",
    "archive.py",
    "baseline.py",
    "baselines/structured_checklist_zh_v1.yaml",
    "composition.py",
    "contracts.py",
    "errors.py",
    "fixtures/synthetic_shadow_v1/manifest.json",
    "instrument.py",
    "normalization.py",
    "parser.py",
    "profile.py",
    "profiles/research_design_report_zh_v1.yaml",
    "prompts.py",
    "prompts/dimension_v1.txt",
    "prompts/system_v1.txt",
    "resources.py",
    "runner.py",
    "serialization.py",
    "shadow_contracts.py",
    "snapshot.py",
    "prompt_sizer.py",
    "unit_planner.py",
    "validator.py",
}

FORBIDDEN_EVALUATOR_OWNERS = (
    "multi_agent_brief.control_store",
    "multi_agent_brief.core_run_v2",
    "multi_agent_brief.intake_v2",
    "multi_agent_brief.orchestrator.runtime_state",
    "multi_agent_brief.product.quality_panel",
    "multi_agent_brief.product.bundle_projection",
    "multi_agent_brief.cli.run_commands",
    "multi_agent_brief.cli.state_commands",
    "multi_agent_brief.cli.gates_commands",
    "multi_agent_brief.cli.finalize_commands",
    "multi_agent_brief.cli.deliver_commands",
    "multi_agent_brief.cli.semantic_support_commands",
)
FORBIDDEN_PROVIDER_OR_NETWORK_IMPORTS = (
    "anthropic",
    "httpx",
    "openai",
    "requests",
    "socket",
    "subprocess",
    "urllib.request",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _matches_owner(module: str, owner: str) -> bool:
    return module == owner or module.startswith(f"{owner}.")


def test_pr_se_2_package_inventory_is_exact_and_has_no_unfrozen_modules() -> None:
    actual = {
        path.relative_to(EVALUATOR_ROOT).as_posix()
        for path in EVALUATOR_ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert actual == EXPECTED_PACKAGE_FILES
    assert not (EVALUATOR_ROOT / "presentation.py").exists()


def test_no_normal_workflow_module_imports_semantic_evaluator() -> None:
    offenders = {}
    for path in SRC_ROOT.rglob("*.py"):
        if EVALUATOR_ROOT in path.parents:
            continue
        matched = sorted(
            module
            for module in _imports(path)
            if _matches_owner(module, "multi_agent_brief.semantic_evaluator")
        )
        if matched:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = matched
    assert offenders == {}


def test_evaluator_never_imports_forbidden_authority_owners() -> None:
    offenders = {}
    for path in EVALUATOR_ROOT.rglob("*.py"):
        matched = sorted(
            module
            for module in _imports(path)
            if any(
                _matches_owner(module, owner) for owner in FORBIDDEN_EVALUATOR_OWNERS
            )
        )
        if matched:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = matched
    assert offenders == {}


def test_only_frozen_live_adapter_imports_one_provider_sdk() -> None:
    offenders = {}
    for path in EVALUATOR_ROOT.rglob("*.py"):
        matched = sorted(
            module
            for module in _imports(path)
            if any(
                _matches_owner(module, owner)
                for owner in FORBIDDEN_PROVIDER_OR_NETWORK_IMPORTS
            )
        )
        if matched:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = matched
    assert offenders == {
        "src/multi_agent_brief/semantic_evaluator/adapters/openai_responses.py": [
            "openai"
        ]
    }


def test_only_shadow_archive_owns_persistent_write_calls() -> None:
    write_methods = {
        "mkdir",
        "rename",
        "unlink",
        "write_bytes",
        "write_text",
    }
    offenders = {}
    for path in EVALUATOR_ROOT.rglob("*.py"):
        calls = sorted(
            {
                node.func.attr
                for node in ast.walk(_tree(path))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in write_methods
            }
        )
        if calls:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = calls
    assert offenders == {
        "src/multi_agent_brief/semantic_evaluator/archive.py": ["mkdir"]
    }


def test_prompt_execution_path_cannot_observe_baseline_or_composition() -> None:
    execution_modules = (
        "adapter.py",
        "adapters/openai_responses.py",
        "adapters/synthetic_fixture.py",
        "admission.py",
        "instrument.py",
        "parser.py",
        "prompts.py",
        "unit_planner.py",
        "validator.py",
    )
    forbidden = (
        "multi_agent_brief.semantic_evaluator.baseline",
        "multi_agent_brief.semantic_evaluator.composition",
    )
    offenders = {}
    for name in execution_modules:
        path = EVALUATOR_ROOT / name
        matched = sorted(
            module
            for module in _imports(path)
            if any(_matches_owner(module, owner) for owner in forbidden)
        )
        if matched:
            offenders[name] = matched
    assert offenders == {}


def test_existing_o3_contract_identity_and_registry_path_remain_unchanged() -> None:
    assert SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION == (
        "mabw.semantic_assessment_report.v1"
    )
    assert SemanticAssessmentReportContract.schema_id == "semantic_assessment_report"
    assert SemanticAssessmentReportContract.schema_version == "v1"
    assert (
        SchemaRegistry.get("semantic_assessment_report")
        is SemanticAssessmentReportContract
    )
    assert (
        SRC_ROOT / "contracts" / "schemas" / "semantic_assessment_report.py"
    ).is_file()
