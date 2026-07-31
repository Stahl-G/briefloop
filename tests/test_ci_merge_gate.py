from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "tests.yml"
PYPROJECT_PATH = ROOT / "pyproject.toml"
REQUIRED_CODE_JOBS = {
    "test",
    "non-dev-smoke",
    "llm-decide-smoke",
    "onboarding-smoke",
    "docx-smoke",
    "golden-smoke",
}
RESULT_ENV = {
    "changes": "CHANGES_RESULT",
    "docs-only": "DOCS_RESULT",
    "test": "TEST_RESULT",
    "non-dev-smoke": "NON_DEV_RESULT",
    "llm-decide-smoke": "LLM_DECIDE_RESULT",
    "onboarding-smoke": "ONBOARDING_RESULT",
    "docx-smoke": "DOCX_RESULT",
    "golden-smoke": "GOLDEN_RESULT",
}


def _workflow() -> dict[str, Any]:
    payload = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    # PyYAML 1.1 treats the unquoted GitHub key ``on`` as a boolean.
    if True in payload and "on" not in payload:
        payload["on"] = payload.pop(True)
    return payload


def _candidate_matrix_entries(script: str) -> list[dict[str, Any]]:
    """Evaluate the ``test_matrix`` assignment from the classification script.

    The matrix is built with comprehensions rather than written as a literal,
    so executing the single assignment keeps this test bound to the real
    definition instead of a transcribed copy that can drift from it.
    """

    module = ast.parse(script)
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "test_matrix"
        ):
            namespace: dict[str, Any] = {}
            exec(
                compile(
                    ast.Module(body=[node], type_ignores=[]),
                    str(WORKFLOW_PATH),
                    "exec",
                ),
                namespace,
            )
            entries = namespace["test_matrix"]["include"]
            assert isinstance(entries, list)
            return entries
    raise AssertionError("classification script does not define test_matrix")


def _gate_script() -> str:
    steps = _workflow()["jobs"]["merge-gate"]["steps"]
    assert len(steps) == 1
    return str(steps[0]["run"])


def _execute_gate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    event_name: str = "pull_request",
    run_candidate: str = "true",
    docs_only: str = "false",
    results: dict[str, str] | None = None,
) -> None:
    values = {
        "changes": "success",
        "docs-only": "skipped",
        **{job: "success" for job in REQUIRED_CODE_JOBS},
    }
    if results:
        values.update(results)
    monkeypatch.setenv("EVENT_NAME", event_name)
    monkeypatch.setenv("RUN_CANDIDATE", run_candidate)
    monkeypatch.setenv("DOCS_ONLY", docs_only)
    for job, env_name in RESULT_ENV.items():
        monkeypatch.setenv(env_name, values[job])
    exec(compile(_gate_script(), str(WORKFLOW_PATH), "exec"), {"__name__": "__main__"})


def test_candidate_triggers_and_concurrency_are_explicit() -> None:
    workflow = _workflow()
    triggers = workflow["on"]

    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert set(triggers["pull_request"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "converted_to_draft",
    }
    # Dispatch carries exactly one opt-in input: regenerating .test_durations.
    # Shard balance depends on that file, so refreshing it must stay a
    # deliberate manual act rather than a side effect of any other trigger.
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"refresh_test_durations"}
    assert triggers["workflow_dispatch"]["inputs"]["refresh_test_durations"][
        "default"
    ] is False
    concurrency = workflow["concurrency"]
    assert concurrency["group"] == (
        "tests-${{ github.event_name == 'pull_request' && "
        "format('pr-{0}', github.event.pull_request.number) || "
        "format('{0}-{1}-{2}', github.event_name, github.ref, github.run_id) }}"
    )
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' }}"
    )


def test_pr_concurrency_is_stable_while_non_pr_runs_are_unique() -> None:
    group = _workflow()["concurrency"]["group"]

    # Every event for one PR shares a group, so a new head/state transition
    # cancels the stale PR run instead of consuming a second full matrix.
    assert "github.event.pull_request.number" in group
    assert "format('pr-{0}'" in group

    # Main pushes and exceptional manual dispatches must not share one pending
    # slot: GitHub keeps only one pending run per concurrency group.  Including
    # the run identity preserves every push shadow matrix and keeps dispatches
    # from replacing a pending main run.
    assert "github.run_id" in group
    assert "github.event_name" in group
    assert "github.ref" in group
    assert "github.event.pull_request.number || github.ref" not in group


def test_candidate_classification_pins_supported_matrix() -> None:
    """The PR test matrix is three OSes on Python 3.12, sharded by duration.

    #526 shrank a 6-leg matrix to one POSIX leg plus Windows and left Linux
    to the ubuntu smoke jobs. This keeps that shape and swaps which POSIX OS
    carries the full suite: ubuntu has 4 vCPU against macOS's 3, and the
    working-checkout publication surface that dominates runtime is POSIX
    rather than darwin-specific. macOS keeps the darwin durability
    primitives through the macos_publication selection.

    Guard against silently dropping an OS, collapsing the shards back to one
    leg, or restoring an untested Python floor.
    """
    workflow = _workflow()
    changes = workflow["jobs"]["changes"]
    script = changes["steps"][1]["run"]

    assert set(changes["outputs"]) == {"docs_only", "run_candidate", "test_matrix"}
    assert 'event_name != "pull_request" or not pr_is_draft' in script
    assert 'event_name == "pull_request"' in script
    assert 'event_name == "workflow_dispatch"' not in script

    entries = _candidate_matrix_entries(script)
    assert {entry["python-version"] for entry in entries} == {"3.12"}

    shards_by_os: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        shards_by_os.setdefault(str(entry["os"]), []).append(entry)
    assert set(shards_by_os) == {"ubuntu-latest", "windows-latest", "macos-latest"}

    for name, expected_shards, expected_select in (
        ("ubuntu-latest", 4, ""),
        ("windows-latest", 2, ""),
        ("macos-latest", 1, "macos_publication"),
    ):
        legs = shards_by_os[name]
        assert len(legs) == expected_shards
        assert {leg["shards"] for leg in legs} == {expected_shards}
        assert sorted(int(leg["shard"]) for leg in legs) == list(
            range(1, expected_shards + 1)
        )
        assert {leg["select"] for leg in legs} == {expected_select}

    assert workflow["jobs"]["test"]["strategy"]["matrix"] == (
        "${{ fromJSON(needs.changes.outputs.test_matrix) }}"
    )


def test_matrix_pytest_harness_excludes_explicit_e2e_and_is_diagnostic() -> None:
    workflow = _workflow()
    test_job = workflow["jobs"]["test"]
    test_step = next(
        step for step in test_job["steps"] if step.get("name") == "Tests"
    )
    command = " ".join(test_step["run"].split())

    assert test_job["timeout-minutes"] == 60
    assert test_step["timeout-minutes"] == 45
    assert "${{ runner.os == 'Windows' && '-vv' || '-q' }}" in command
    assert "-n auto" in command
    assert "--dist worksteal" in command
    # explicit_e2e stays excluded on every leg; matrix.select only prefixes an
    # additional selection, so it can narrow a leg but never widen it.
    assert 'not explicit_e2e"' in command
    assert (
        "-m \"${{ matrix.select && format('{0} and ', matrix.select) || '' }}"
        'not explicit_e2e"'
    ) in command
    # Duration-weighted sharding. Round-robin would be wrong here: the runtime
    # distribution is extreme and the slowest tests sit adjacent in one file.
    assert "--splits ${{ matrix.shards }}" in command
    assert "--group ${{ matrix.shard }}" in command
    assert "--max-worker-restart=0" in command
    assert "-o faulthandler_timeout=240" in command
    assert "--timeout=" not in command

    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")
    assert '  "pytest-timeout>=2.4,<3",' in pyproject
    assert '  "pytest-split>=0.10,<1",' in pyproject
    # Shards are only balanced while this file tracks the suite.
    assert (ROOT / ".test_durations").is_file()
    assert (
        '"explicit_e2e: heavyweight end-to-end evidence run only when '
        'explicitly authorized; excluded from normal PR CI",'
    ) in pyproject


def test_draft_and_candidate_jobs_use_closed_conditions() -> None:
    jobs = _workflow()["jobs"]
    docs_condition = jobs["docs-only"]["if"]
    assert "run_candidate == 'true'" in docs_condition
    assert "docs_only == 'true'" in docs_condition

    for job in REQUIRED_CODE_JOBS:
        condition = jobs[job]["if"]
        assert "run_candidate == 'true'" in condition, job
        assert "docs_only != 'true'" in condition, job


def test_every_candidate_job_checks_out_the_exact_head() -> None:
    workflow = _workflow()
    assert workflow["env"]["CANDIDATE_SHA"] == (
        "${{ github.event.pull_request.head.sha || github.sha }}"
    )
    for job_name, job in workflow["jobs"].items():
        if job_name == "merge-gate":
            continue
        checkout = next(
            step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"
        )
        assert checkout["with"]["ref"] == "${{ env.CANDIDATE_SHA }}", job_name


def test_merge_gate_has_one_stable_complete_dependency_set() -> None:
    gate = _workflow()["jobs"]["merge-gate"]

    assert gate["name"] == (
        "${{ github.event_name == 'pull_request' && github.event.pull_request.draft && "
        "'Draft gate' || 'Merge gate' }}"
    )
    assert gate["name"].count("Merge gate") == 1
    assert gate["name"].count("Draft gate") == 1
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {
        "changes",
        "docs-only",
        *REQUIRED_CODE_JOBS,
    }
    env = gate["steps"][0]["env"]
    assert set(env) == {
        "EVENT_NAME",
        "RUN_CANDIDATE",
        "DOCS_ONLY",
        *RESULT_ENV.values(),
    }


def test_merge_gate_accepts_only_the_complete_code_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _execute_gate(monkeypatch)


@pytest.mark.parametrize("job", sorted(REQUIRED_CODE_JOBS))
@pytest.mark.parametrize("bad_result", ["failure", "cancelled", "skipped"])
def test_merge_gate_rejects_incomplete_code_route(
    monkeypatch: pytest.MonkeyPatch,
    job: str,
    bad_result: str,
) -> None:
    with pytest.raises(SystemExit, match="dependency mismatch"):
        _execute_gate(monkeypatch, results={job: bad_result})


def test_merge_gate_accepts_only_the_complete_docs_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _execute_gate(
        monkeypatch,
        docs_only="true",
        results={
            "docs-only": "success",
            **{job: "skipped" for job in REQUIRED_CODE_JOBS},
        },
    )


@pytest.mark.parametrize("bad_result", ["failure", "cancelled", "skipped"])
def test_merge_gate_rejects_failed_docs_route(
    monkeypatch: pytest.MonkeyPatch,
    bad_result: str,
) -> None:
    with pytest.raises(SystemExit, match="dependency mismatch"):
        _execute_gate(
            monkeypatch,
            docs_only="true",
            results={
                "docs-only": bad_result,
                **{job: "skipped" for job in REQUIRED_CODE_JOBS},
            },
        )


def test_draft_route_requires_every_heavy_job_to_be_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skipped = {
        "docs-only": "skipped",
        **{job: "skipped" for job in REQUIRED_CODE_JOBS},
    }
    _execute_gate(monkeypatch, run_candidate="false", results=skipped)

    with pytest.raises(SystemExit, match="dependency mismatch"):
        _execute_gate(
            monkeypatch,
            run_candidate="false",
            results={**skipped, "test": "success"},
        )


@pytest.mark.parametrize("bad_result", ["failure", "cancelled", "skipped"])
def test_merge_gate_rejects_failed_classification(
    monkeypatch: pytest.MonkeyPatch,
    bad_result: str,
) -> None:
    with pytest.raises(SystemExit, match="dependency mismatch"):
        _execute_gate(monkeypatch, results={"changes": bad_result})
