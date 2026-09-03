"""AST isolation pins for the two new web-surface packages (C3).

brief_html is a read-only projection surface: only builder.py may touch the
Store/LAJ/Human-review read paths.  The shared static asset exposes commands
only when a secured loopback session binding is present; a file/static export
has no command authority.  init_web reaches authority ONLY through the
sanctioned bootstrap seam (cli.init_wizard.create_workspace +
runtime_host_v2.initialization).  Neither package may read improvement-ledger
material, legacy fold-ins, or open raw sockets/sqlite/subprocess.
"""


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
        "multi_agent_brief.product.post_final_assessment_projection",
        "multi_agent_brief.product.market_data_read_model",
        "multi_agent_brief.runtime_host_v2.errors",
        "multi_agent_brief.runtime_host_v2.projections",
        "multi_agent_brief.semantic_evaluator.reader",
    },
    "brief_html/render.py": {
        "multi_agent_brief.product.brief_html.builder",
        # Pure publication-capability boundary shared with ReportBundle.
        "multi_agent_brief.product.projection_platform",
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
        # Public-search setup may use only the deterministic secret writer and
        # known-environment lookup; the key never enters the run contract.
        "multi_agent_brief.cli.secrets_commands",
        "multi_agent_brief.contracts.v2",
        "multi_agent_brief.control_store",
        "multi_agent_brief.control_store.serialization",
        "multi_agent_brief.core.env",
        # RUN-UX-1A uses the sole Core-owned catalog only for bounded,
        # zero-write semantic extent validation and preview resolution.
        "multi_agent_brief.core_run_v2.output_contract",
        "multi_agent_brief.core_run_v2.policy",
        "multi_agent_brief.runtime_host_v2.codex",
        "multi_agent_brief.runtime_host_v2.initialization",
        "multi_agent_brief.workspace.init_profile",
        "multi_agent_brief.product.init_web.staging",
        # M5 routes nested-target checks through the sole shared hygiene
        # classifier; init-web still reaches authority only through bootstrap.
        "multi_agent_brief.product.workspace_hygiene",
    },
    "init_web/staging.py": {
        "multi_agent_brief.contracts.v2",
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








def test_brief_html_static_export_has_no_write_affordance() -> None:
    static = BRIEF_HTML / "static"
    app = (static / "app.js").read_bytes()
    index = (static / "index.html").read_bytes()
    assert b'location.protocol !== "http:"' in app
    assert b'location.hostname !== "127.0.0.1"' in app
    assert b"if (!ACTION_SESSION) return;" in app
    assert b'fetch("/api/v1/command?session_id="' in app
    assert b"XMLHttpRequest" not in app
    assert b"<form" not in index and b"<form" not in app
    assert b"innerHTML" not in app
    assert b"eval(" not in app


def test_brief_html_reader_review_controls_are_session_bound_and_secret_free() -> None:
    app = (BRIEF_HTML / "static" / "app.js").read_bytes()

    assert b"if (!(ACTION_SESSION && sem.selection_required === true)) return;" in app
    assert b'status === "not_assessed"' in app
    assert b'sendReviewCommand("run_reader_review"' in app
    assert b'sendReviewCommand("select_result"' in app
    assert b'sendReviewCommand("refresh"' in app
    assert b"DATA = result.page_data;" in app
    assert b"RUN_REQUEST_ID" in app
    assert b"automatic_retry" in app
    assert b"requirement_assessments" in app
    assert b"template.protocol" in app
    assert b"assessment.requirement_text" in app
    assert b"post_final_assessment_pending" in app
    assert b"post_final_assessment_predecessor_outcome_unknown" in app
    assert b"An external call may have occurred" in app
    assert b"will not retry automatically" in app
    assert b"No findings in this view" not in app
    assert b"api_key" not in app.lower()
    assert b'input.type = "password"' not in app
