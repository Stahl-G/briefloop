#!/usr/bin/env python3
"""Single-citation-parser structural guard.

Only the canonical citation home may define the projectable internal citation
parser. This scanner stops a new module from hand-rolling its own ``[src:...]``
marker regex or enumerating claim-id families (CL-/CLM-/CLAIM_/SYN_CLAIM) in a
direct ``re.*`` pattern call. It covers static direct ``re.*`` patterns and
simple module/function/class string constants; it does not attempt to prove
arbitrary dynamic regex construction or aliased imports.

The guard currently reports SATISFIED: the parsers it was written to chase
(finalize, reader_final_gate, reader_projection, source_appendix, and the #460
reader residue module) have been consolidated into the canonical home, and
those modules all still exist. Advisory by default (prints findings, exit 0);
pass ``--require-satisfied`` to make an unsatisfied guard exit non-zero.

Extracted verbatim from the retired ``check_v1_rc_readiness.py`` in the LD2-3
follow-up (ruling §20.1). The RC readiness gate it used to live in drove the
deleted legacy runtime-state stack; this guard never did, and scans live
``src/`` only.
"""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# Modules allowed to define the internal citation/claim marker parser.
# PR-2A finalizes the single canonical home; shrink this to one entry as the
# duplicates are consolidated. The contract schemas' anchored ``^CL-\d{4}$``
# id-format validator is a single-token format authority and is not a marker
# parser, so it does not need to be listed here.
CANONICAL_CITATION_MODULES: set[str] = {
    "src/multi_agent_brief/core/citations.py",
}


# Distinct claim-id family tokens. Two or more in one regex pattern means the
# pattern is enumerating the claim-id family instead of deferring to ledger
# membership; a single anchored token (the contract format authority) is allowed.
_CLAIM_FAMILY_TOKENS = ("CL-", "CLM-", "SYN_CLAIM", "CLAIM_")


_RE_PATTERN_FUNCTIONS = {
    "compile",
    "findall",
    "finditer",
    "fullmatch",
    "match",
    "search",
    "split",
    "sub",
    "subn",
}


# Negative lookaround assertions can reference a marker only to EXCLUDE it
# (e.g. a number matcher using ``(?<!\\[src:)``); strip those before
# classifying so an exclusion is not mistaken for a parser. Positive
# lookarounds are parser syntax and must remain visible to the classifier.
_NEGATIVE_LOOKAROUND_RE = re.compile(r"\(\?(?:!|<!)[^)]*\)")


_BRACKETED_SRC_MARKER_RE = re.compile(
    r"\\?\[\s*src\s*:",
    re.IGNORECASE,
)


_BRACKETED_SOURCE_MARKER_ALTERNATION_RE = re.compile(
    r"\\?\[\s*\(\?:\s*(?:src\|source|source\|src)\s*\)\s*:",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    path: str
    name: str
    lineno: int
    signal: str


def _is_re_pattern_call(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute) and func.attr in _RE_PATTERN_FUNCTIONS:
        return isinstance(func.value, ast.Name) and func.value.id == "re"
    return isinstance(func, ast.Name) and func.id == "compile"


def _compile_pattern_arg(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg == "pattern":
            return keyword.value
    return None


def _pattern_text(node: ast.expr, constants: dict[str, str] | None = None) -> str:
    constants = constants or {}
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.Name):
        return constants.get(node.id, "")
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(_pattern_text(value.value, constants))
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _pattern_text(node.left, constants) + _pattern_text(node.right, constants)
    return ""


def _scope_string_constants(
    body: list[ast.stmt],
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    constants: dict[str, str] = dict(inherited or {})
    for node in body:
        if isinstance(node, ast.Assign):
            value = _pattern_text(node.value, constants)
            if not value:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = _pattern_text(node.value, constants) if node.value is not None else ""
            if value:
                constants[node.target.id] = value
    return constants


def _target_name(targets: list[ast.expr]) -> str:
    for target in targets:
        if isinstance(target, ast.Name):
            return target.id
    return "<inline>"


def _classify(pattern: str) -> str | None:
    matching = _NEGATIVE_LOOKAROUND_RE.sub("", pattern)
    if _has_source_marker_syntax(matching):
        return "source_marker"
    if sum(token in matching for token in _CLAIM_FAMILY_TOKENS) >= 2:
        return "claim_family"
    return None


def _has_source_marker_syntax(pattern: str) -> bool:
    return bool(
        _BRACKETED_SRC_MARKER_RE.search(pattern)
        or _BRACKETED_SOURCE_MARKER_ALTERNATION_RE.search(pattern)
    )


_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _iter_current_scope_nodes(body: list[ast.stmt]):
    for statement in body:
        yield from _walk_current_scope(statement)


def _walk_current_scope(node: ast.AST):
    if isinstance(node, _SCOPE_NODES):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_NODES):
            continue
        yield from _walk_current_scope(child)


def _iter_child_scopes(body: list[ast.stmt]):
    for statement in body:
        yield from _walk_child_scopes(statement)


def _walk_child_scopes(node: ast.AST):
    if isinstance(node, _SCOPE_NODES):
        yield node
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_child_scopes(child)


def _iter_regex_patterns_in_scope(
    body: list[ast.stmt],
    inherited_constants: dict[str, str] | None = None,
):
    constants = _scope_string_constants(body, inherited_constants)
    nodes = list(_iter_current_scope_nodes(body))
    names: dict[int, str] = {}
    for node in nodes:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            names[id(node.value)] = _target_name(node.targets)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
            names[id(node.value)] = _target_name([node.target])
    for node in nodes:
        if isinstance(node, ast.Call) and _is_re_pattern_call(node.func):
            pattern_arg = _compile_pattern_arg(node)
            if pattern_arg is None:
                continue
            yield names.get(id(node), "<inline>"), node.lineno, _pattern_text(pattern_arg, constants)
    for child_scope in _iter_child_scopes(body):
        yield from _iter_regex_patterns_in_scope(child_scope.body, constants)


def _iter_regex_patterns(tree: ast.Module):
    yield from _iter_regex_patterns_in_scope(tree.body)


def find_citation_parser_violations(
    scan_root: Path, allowlist: set[str], *, rel_to: Path
) -> list[Violation]:
    violations: list[Violation] = []
    for py in sorted(scan_root.rglob("*.py")):
        rel = py.relative_to(rel_to).as_posix()
        if rel in allowlist:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for name, lineno, pattern in _iter_regex_patterns(tree):
            signal = _classify(pattern)
            if signal is not None:
                violations.append(Violation(path=rel, name=name, lineno=lineno, signal=signal))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Single-citation-parser structural guard.")
    parser.add_argument(
        "--require-satisfied",
        action="store_true",
        help="Exit non-zero when the guard is unsatisfied.",
    )
    args = parser.parse_args()

    violations = find_citation_parser_violations(
        REPO_ROOT / "src", CANONICAL_CITATION_MODULES, rel_to=REPO_ROOT
    )
    print("Single Citation Parser Guard")
    print("=" * 40)
    print(f"  canonical home: {sorted(CANONICAL_CITATION_MODULES)}")
    if violations:
        print(f"  [UNSATISFIED] {len(violations)} violation(s):")
        for violation in violations:
            print(
                f"    {violation.path}:{violation.lineno} "
                f"{violation.name} [{violation.signal}]"
            )
    else:
        print("  [SATISFIED] no violations")
    if violations and args.require_satisfied:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
