"""AST isolation guards for the offline shadow research package."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "multi_agent_brief"
EVALUATOR_ROOT = SRC_ROOT / "semantic_evaluator"

# `orchestrator.runtime_state` below was deleted in LD2-3. It stays listed on
# purpose: this is a reintroduction tripwire, so an owner that currently
# resolves to nothing is the expected state, not dead config.
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

EXPERIMENT_ENTRYPOINT = SRC_ROOT / "cli" / "experiments_commands.py"
QUALITY_PANEL_READ_ONLY_CONSUMER = SRC_ROOT / "product" / "quality_panel.py"
BRIEF_HTML_READ_ONLY_CONSUMER = SRC_ROOT / "product" / "brief_html" / "builder.py"
POST_FINAL_ASSESSMENT_WRITER = SRC_ROOT / "product" / "post_final_assessment.py"
POST_FINAL_ASSESSMENT_READ_ONLY_PROJECTION = (
    SRC_ROOT / "product" / "post_final_assessment_projection.py"
)
POST_FINAL_ASSESSMENT_READ_MODEL = (
    SRC_ROOT / "product" / "post_final_assessment_read_model.py"
)
POST_FINAL_REVIEW_COORDINATOR = SRC_ROOT / "product" / "post_final_review.py"
READ_ONLY_LAJ_CONSUMERS = {
    EXPERIMENT_ENTRYPOINT,
    QUALITY_PANEL_READ_ONLY_CONSUMER,
    BRIEF_HTML_READ_ONLY_CONSUMER,
    # PF-LAJ-1 is the Store-owned, post-final product coordinator and its
    # read-only projection.  The evaluator keeps no authority imports in the
    # reverse direction; these consumers cannot alter Core/Gate/run truth.
    POST_FINAL_ASSESSMENT_WRITER,
    POST_FINAL_ASSESSMENT_READ_ONLY_PROJECTION,
    # The v0.15 reader-review loop reuses evaluator contracts, normalization,
    # profile loading, and serialization as read-only primitives from the
    # same post-final product layer.
    POST_FINAL_ASSESSMENT_READ_MODEL,
    POST_FINAL_REVIEW_COORDINATOR,
}
NETWORK_IMPORT_ALLOWLIST = {
    "adapters/anthropic_messages.py": {"anthropic"},
    "adapters/openai_responses.py": {"openai"},
    "adapters/local_proxy_responses.py": set(),
}


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


def test_no_normal_workflow_module_imports_semantic_evaluator() -> None:
    offenders = {}
    for path in SRC_ROOT.rglob("*.py"):
        if EVALUATOR_ROOT in path.parents:
            continue
        if path in READ_ONLY_LAJ_CONSUMERS:
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


def test_se2r_15_only_live_adapter_may_import_provider_or_network_code() -> None:
    offenders = {}
    for path in EVALUATOR_ROOT.rglob("*.py"):
        matched = {
            module
            for module in _imports(path)
            if any(
                _matches_owner(module, owner)
                for owner in FORBIDDEN_PROVIDER_OR_NETWORK_IMPORTS
            )
        }
        relative = path.relative_to(EVALUATOR_ROOT).as_posix()
        unexpected = sorted(matched - NETWORK_IMPORT_ALLOWLIST.get(relative, set()))
        if unexpected:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = unexpected
    assert offenders == {}
