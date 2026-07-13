from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "tests.yml"
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
    assert triggers["workflow_dispatch"] is None
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


def test_candidate_classification_keeps_one_full_supported_matrix() -> None:
    workflow = _workflow()
    changes = workflow["jobs"]["changes"]
    script = changes["steps"][1]["run"]

    assert set(changes["outputs"]) == {"docs_only", "run_candidate", "test_matrix"}
    assert '"os": ["ubuntu-latest", "macos-latest", "windows-latest"]' in script
    assert '"python-version": ["3.9", "3.12"]' in script
    assert 'event_name != "pull_request" or not pr_is_draft' in script
    assert 'event_name == "pull_request"' in script
    assert 'event_name == "workflow_dispatch"' not in script
    assert '["ubuntu-latest"], "python-version": ["3.12"]' not in script

    assert workflow["jobs"]["test"]["strategy"]["matrix"] == (
        "${{ fromJSON(needs.changes.outputs.test_matrix) }}"
    )


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


def test_platform_selector_assertion_is_concrete_and_portable() -> None:
    source = (ROOT / "tests" / "test_runtime_state_contracts.py").read_text(
        encoding="utf-8"
    )
    assert 'details["repo_workdir_type"] == type(loop).__name__' in source
    assert 'details["repo_workdir_type"] == "PosixPath"' not in source
