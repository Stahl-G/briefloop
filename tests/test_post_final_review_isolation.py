from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "multi_agent_brief" / "product" / "review_session"
BRIDGE = ROOT / "src" / "multi_agent_brief" / "semantic_evaluator" / "post_final_bridge.py"


FORBIDDEN_PREFIXES = (
    "multi_agent_brief.control_store",
    "multi_agent_brief.core_run_v2",
    "multi_agent_brief.delivery",
    "multi_agent_brief.finalize",
    "multi_agent_brief.gate",
    "multi_agent_brief.improvement",
    "multi_agent_brief.orchestrator.runtime_state",
    "multi_agent_brief.runtime_host_v2",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_review_session_and_laj_bridge_have_zero_runtime_authority_imports() -> None:
    paths = [*PACKAGE.rglob("*.py"), BRIDGE]
    imports = {name for path in paths for name in _imports(path)}
    assert not {
        name
        for name in imports
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    }
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "sqlite3" not in source
    assert "quality_panel.json" not in source
    assert "improvement/ledger.jsonl" not in source
    assert "run_shadow(" not in source
    assert "OpenAI" not in source
