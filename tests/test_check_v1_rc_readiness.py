"""Tests for the v1.0 RC readiness structural guard.

The guard enforces one canonical projectable citation parser: no module outside
the allowlisted canonical home may define its own `[src:...]` marker regex or
enumerate claim-id families (CL-/CLM-/CLAIM_/SYN_CLAIM) in a direct `re.*`
pattern call. The scanner covers static direct `re.*` patterns and simple
module/function/class string constants; dynamic construction and aliased imports
are out of scope.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_v1_rc_readiness.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_v1_rc_readiness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass annotation resolution can find the module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("import re\n\n" + body, encoding="utf-8")


def _violation_paths(root: Path, allowlist: set[str]) -> set[str]:
    guard = _load_guard()
    return {v.path for v in guard.find_citation_parser_violations(root, allowlist, rel_to=root)}


def test_flags_source_marker_parser_outside_canonical_home(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/finalize.py", '_SRC = re.compile(r"\\[src:[^\\]]*\\]")\n')
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_bracketed_alternation_marker_parser(tmp_path: Path) -> None:
    # The (?:src|source): form has no literal "src:" substring; still a marker parser.
    _write(tmp_path, "pkg/gate.py", '_SRC = re.compile(r"\\[(?:src|source):[^\\]]+\\]")\n')
    assert "pkg/gate.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_claim_id_family_enumeration(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/audit.py",
        '_ID = re.compile(r"(?:CL-\\d{3,}|CLM-\\d{3,}|SYN_CLAIM_[A-Z0-9]+|CLAIM_[A-Z0-9]+)")\n',
    )
    assert "pkg/audit.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_claim_id_family_enumeration_via_fstring_constant(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/audit.py",
        'INTERNAL_CLAIM_ID_PATTERN = r"(?:CL-\\d+|CLM-\\d+|SYN_CLAIM_[A-Z0-9]+|CLAIM_[A-Z0-9]+)"\n'
        'CLAIM_ID_RE = re.compile(rf"^{INTERNAL_CLAIM_ID_PATTERN}$")\n',
    )
    assert "pkg/audit.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_via_fstring_constant(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'SOURCE_MARKER_PATTERN = r"\\[(?:src|source):[^\\]]+\\]"\n'
        'SOURCE_MARKER_RE = re.compile(rf"^{SOURCE_MARKER_PATTERN}$")\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_with_keyword_pattern(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        '_SRC = re.compile(pattern=r"\\[src:[^\\]]+\\]")\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_bracketed_alternation_marker_parser_with_keyword_pattern(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        '_SRC = re.compile(pattern=r"\\[(?:src|source):[^\\]]+\\]")\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_in_re_findall(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def parse(text):\n'
        '    return re.findall(r"\\[src:([^\\]]+)\\]", text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_in_re_finditer(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def parse(text):\n'
        '    return re.finditer(r"\\[(?:src|source):([^\\]]+)\\]", text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_in_re_sub(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def strip(text):\n'
        '    return re.sub(r"\\[src:[^\\]]+\\]", "", text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_in_re_search_keyword_pattern(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def has_marker(text):\n'
        '    return re.search(pattern=r"\\[src:[^\\]]+\\]", string=text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_via_function_local_constant(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def parse(text):\n'
        '    PAT = r"\\[src:([^\\]]+)\\]"\n'
        '    return re.finditer(PAT, text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_source_marker_parser_via_annotated_local_constant(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def parse(text):\n'
        '    PREFIX: str = r"\\[src:"\n'
        '    PAT: str = PREFIX + r"([^\\]]+)\\]"\n'
        '    return re.finditer(PAT, text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_positive_lookbehind_source_marker_parser(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def parse(text):\n'
        '    return re.findall(r"(?<=\\[src:)[^\\]]+", text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_flags_positive_lookahead_source_marker_splitter(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/finalize.py",
        'def split_markers(text):\n'
        '    return re.split(r"(?=\\[src:)", text)\n',
    )
    assert "pkg/finalize.py" in _violation_paths(tmp_path, allowlist=set())


def test_allows_parser_inside_canonical_home(tmp_path: Path) -> None:
    _write(tmp_path, "core/citations.py", '_SRC = re.compile(r"\\[src:([^\\]]+)\\]")\n')
    assert _violation_paths(tmp_path, allowlist={"core/citations.py"}) == set()


def test_ignores_single_token_claim_id_format_authority(tmp_path: Path) -> None:
    # The contract's ledger id-format validator uses one anchored family token.
    _write(tmp_path, "contracts/schema.py", 'CLAIM_ID_RE = re.compile(r"^CL-(\\d{4})$")\n')
    assert _violation_paths(tmp_path, allowlist=set()) == set()


def test_ignores_plain_string_usage_without_re_compile(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/user.py", 'LABEL = "[src:CL-0001] example"\n')
    assert _violation_paths(tmp_path, allowlist=set()) == set()


def test_ignores_unrelated_regex(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/dates.py", 'DATE = re.compile(r"\\d{4}-\\d{2}-\\d{2}")\n')
    assert _violation_paths(tmp_path, allowlist=set()) == set()


def test_ignores_internal_id_scanner_with_src_source_alternation(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/residue.py",
        'INTERNAL_ID_RE = re.compile(r"(?:CLAIM|SRC|SOURCE|CLM)_[A-Z0-9]+")\n',
    )
    assert _violation_paths(tmp_path, allowlist=set()) == set()


def test_ignores_plain_source_label_regex(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/prose.py", 'SOURCE_LABEL_RE = re.compile(r"Source:\\s+(.+)")\n')
    assert _violation_paths(tmp_path, allowlist=set()) == set()


def test_ignores_noncanonical_source_marker_residue_detector(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/residue.py", 'SOURCE_RE = re.compile(r"\\[source:[^\\]]+\\]")\n')
    assert _violation_paths(tmp_path, allowlist=set()) == set()


def test_ignores_negative_lookbehind_source_marker_exclusion(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/claim_ids.py",
        'CLAIM_ID_RE = re.compile(r"(?<!\\[src:)CL-\\d+")\n',
    )
    assert _violation_paths(tmp_path, allowlist=set()) == set()


def test_reports_name_and_signal_for_a_violation(tmp_path: Path) -> None:
    guard = _load_guard()
    _write(tmp_path, "pkg/finalize.py", '_SRC_MARKER_RE = re.compile(r"\\[src:[^\\]]*\\]")\n')
    violations = guard.find_citation_parser_violations(tmp_path, set(), rel_to=tmp_path)
    assert len(violations) == 1
    v = violations[0]
    assert v.name == "_SRC_MARKER_RE"
    assert v.signal == "source_marker"


def test_real_tree_currently_has_scattered_parsers(tmp_path: Path) -> None:
    # Red-now baseline: the real repo has duplicate parsers pending PR-2A consolidation.
    # This test documents the current violation set and MUST be updated (shrinking to
    # empty) as consolidation lands. It is the acceptance ratchet, not a permanent fixture.
    guard = _load_guard()
    item = guard.check_single_citation_parser(ROOT)
    offenders = {v.path for v in item.violations}
    assert "src/multi_agent_brief/outputs/finalize.py" in offenders
    assert "src/multi_agent_brief/outputs/reader_final_gate.py" in offenders
    assert item.satisfied is False
    # The canonical home must never be reported as a violation.
    assert "src/multi_agent_brief/core/citations.py" not in offenders


def _runner_payload(guard, *, ok: bool = True) -> dict:
    scenarios = [
        {"scenario_id": scenario_id, "ok": ok, "evidence": {}}
        for scenario_id in guard.REQUIRED_SCENARIO_IDS
    ]
    return {
        "ok": ok,
        "required_complete": True,
        "required_scenario_ids": list(guard.REQUIRED_SCENARIO_IDS),
        "executed_scenario_ids": list(guard.REQUIRED_SCENARIO_IDS),
        "boundary": guard.RUNNER_BOUNDARY,
        "scenarios": scenarios,
    }


def test_executable_readiness_requires_exact_successful_scenario_matrix(
    monkeypatch,
) -> None:
    guard = _load_guard()
    monkeypatch.setattr(
        guard,
        "run_v1_rc_safety_smoke",
        lambda **_kwargs: _runner_payload(guard),
    )

    item = guard.check_executable_rc_safety(ROOT)

    assert item.satisfied is True
    assert item.name == "executable_rc_safety"
    assert item.evidence == [
        f"{scenario_id}=pass" for scenario_id in guard.REQUIRED_SCENARIO_IDS
    ]


def test_executable_readiness_rejects_declared_success_with_missing_scenario(
    monkeypatch,
) -> None:
    guard = _load_guard()
    payload = _runner_payload(guard)
    payload["executed_scenario_ids"] = payload["executed_scenario_ids"][:-1]
    payload["scenarios"] = payload["scenarios"][:-1]
    monkeypatch.setattr(
        guard,
        "run_v1_rc_safety_smoke",
        lambda **_kwargs: payload,
    )

    item = guard.check_executable_rc_safety(ROOT)

    assert item.satisfied is False
    assert any("required_ids=" in evidence for evidence in item.evidence)


def test_executable_readiness_rejects_mutated_declared_required_scenario_ids(
    monkeypatch,
) -> None:
    guard = _load_guard()
    payload = _runner_payload(guard)
    payload["required_scenario_ids"] = payload["required_scenario_ids"][:-1]
    monkeypatch.setattr(
        guard,
        "run_v1_rc_safety_smoke",
        lambda **_kwargs: payload,
    )

    item = guard.check_executable_rc_safety(ROOT)

    assert item.satisfied is False
    assert any("declared_required_ids=" in evidence for evidence in item.evidence)


def test_executable_readiness_reruns_runner_instead_of_reading_pass_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    guard = _load_guard()
    (tmp_path / "v1_rc_safety_pass.json").write_text(
        json.dumps({"ok": True}),
        encoding="utf-8",
    )
    calls = []

    def fail_runner(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("runner execution failed")

    monkeypatch.setattr(guard, "run_v1_rc_safety_smoke", fail_runner)

    item = guard.check_executable_rc_safety(tmp_path)

    assert item.satisfied is False
    assert calls == [{"repo_root": tmp_path}]
    assert item.evidence == ["runner_error=RuntimeError: runner execution failed"]


def test_executable_readiness_rejects_malformed_runner_identity_without_crashing(
    monkeypatch,
) -> None:
    guard = _load_guard()
    payload = _runner_payload(guard)
    payload["executed_scenario_ids"] = [{"scenario_id": "RC-SMOKE-01"}]
    monkeypatch.setattr(
        guard,
        "run_v1_rc_safety_smoke",
        lambda **_kwargs: payload,
    )

    item = guard.check_executable_rc_safety(ROOT)

    assert item.satisfied is False
    assert any("executed_ids=" in evidence for evidence in item.evidence)


def test_real_executable_rc_safety_check_runs_all_required_scenarios() -> None:
    guard = _load_guard()

    item = guard.check_executable_rc_safety(ROOT)

    assert item.satisfied is True
    assert item.evidence == [
        f"{scenario_id}=pass" for scenario_id in guard.REQUIRED_SCENARIO_IDS
    ]
