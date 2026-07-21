"""AST isolation pins for the two new web-surface packages (C3).

brief_html is a read-only projection surface: only builder.py may touch the
Store/LAJ read paths.  init_web reaches authority ONLY through the sanctioned
bootstrap seam (cli.init_wizard.create_workspace +
runtime_host_v2.initialization).  Neither package may read improvement-ledger
material, legacy fold-ins, or open raw sockets/sqlite/subprocess.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC_ROOT = ROOT / "src" / "multi_agent_brief"
BRIEF_HTML = SRC_ROOT / "product" / "brief_html"
INIT_WEB = SRC_ROOT / "product" / "init_web"

# Exact per-file import allowances beyond the stdlib/pydantic/pyyaml.
ALLOWED_IMPORTS = {
    "brief_html/builder.py": {
        "multi_agent_brief.control_store",
        "multi_agent_brief.core_run_v2.errors",
        "multi_agent_brief.core_run_v2.next_action",
        "multi_agent_brief.core_run_v2.policy",
        "multi_agent_brief.core_run_v2.terminal",
        "multi_agent_brief.core_run_v2.verifier",
        "multi_agent_brief.product.review_session.contracts",
        "multi_agent_brief.runtime_host_v2.errors",
        "multi_agent_brief.runtime_host_v2.projections",
        "multi_agent_brief.semantic_evaluator.reader",
    },
    "brief_html/render.py": {
        "multi_agent_brief.product.brief_html.builder",
        "yaml",
    },
    "brief_html/__init__.py": {
        "multi_agent_brief.product.brief_html.builder",
        "multi_agent_brief.product.brief_html.render",
    },
    "init_web/server.py": {
        "multi_agent_brief.product.init_web.submit",
        "multi_agent_brief.product.review_session.serialization",
    },
    "init_web/submit.py": {
        "multi_agent_brief.cli.init_wizard",
        "multi_agent_brief.control_store",
        "multi_agent_brief.control_store.serialization",
        "multi_agent_brief.core_run_v2.policy",
        "multi_agent_brief.runtime_host_v2.codex",
        "multi_agent_brief.runtime_host_v2.initialization",
        "multi_agent_brief.workspace.init_profile",
    },
    "init_web/__init__.py": {
        "multi_agent_brief.product.init_web.server",
        "multi_agent_brief.product.init_web.submit",
    },
}

FORBIDDEN_STDLIB = {"sqlite3", "socket", "subprocess"}
FORBIDDEN_SOURCE_MARKERS = (
    b"improvement/ledger.jsonl",
    b"guidance_manifestation",
    b"support_wording",
    b"artifact_registry.json",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _relative(package: Path, path: Path) -> str:
    return f"{package.name}/{path.relative_to(package).as_posix()}"


def _check_package(package: Path) -> None:
    expected = {
        key: value
        for key, value in ALLOWED_IMPORTS.items()
        if key.startswith(f"{package.name}/")
    }
    seen = set()
    for path in sorted(package.rglob("*.py")):
        relative = _relative(package, path)
        seen.add(relative)
        allowed = expected.get(relative, set())
        offenders = {
            name
            for name in _imports(path)
            if name.startswith("multi_agent_brief") or name in FORBIDDEN_STDLIB
        } - allowed
        assert not offenders, f"{relative}: {sorted(offenders)}"
        source = path.read_bytes()
        for marker in FORBIDDEN_SOURCE_MARKERS:
            assert marker not in source, f"{relative} reads {marker!r}"
    assert seen == set(expected), f"{package.name} file inventory drifted"


def test_brief_html_imports_are_pinned_to_read_only_projection() -> None:
    _check_package(BRIEF_HTML)


def test_init_web_imports_are_pinned_to_sanctioned_bootstrap() -> None:
    _check_package(INIT_WEB)


def test_brief_html_static_export_has_no_write_affordance() -> None:
    static = BRIEF_HTML / "static"
    app = (static / "app.js").read_bytes()
    index = (static / "index.html").read_bytes()
    assert b"fetch(" not in app
    assert b"XMLHttpRequest" not in app
    assert b"<form" not in index and b"<form" not in app
    assert b"innerHTML" not in app
    assert b"eval(" not in app
