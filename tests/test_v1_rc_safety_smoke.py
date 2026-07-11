"""Executable v1.0 RC safety scenario matrix tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_v1_rc_safety_smoke.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_v1_rc_safety_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _events(workspace: Path) -> list[dict]:
    path = workspace / "output/intermediate/event_log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_required_rc_safety_matrix_executes_real_lifecycles(tmp_path: Path) -> None:
    runner = _load_runner()

    payload = runner.run_v1_rc_safety_smoke(work_root=tmp_path)

    assert payload["ok"] is True
    assert payload["required_complete"] is True
    assert payload["required_scenario_ids"] == list(runner.REQUIRED_SCENARIO_IDS)
    assert payload["executed_scenario_ids"] == list(runner.REQUIRED_SCENARIO_IDS)
    assert [item["scenario_id"] for item in payload["scenarios"]] == list(
        runner.REQUIRED_SCENARIO_IDS
    )
    assert all(item["ok"] is True for item in payload["scenarios"])
    assert payload["boundary"] == runner.RUNNER_BOUNDARY

    clean = tmp_path / "rc-smoke-01"
    clean_report = _json(clean / "output/intermediate/finalize_report.json")
    assert (clean / "output/intermediate/agent_handoff.json").exists()
    assert clean_report["delivery_promotion"] == "promoted"
    assert (clean / "output/delivery/brief.md").exists()
    assert any(
        event["event_type"] == "decision_recorded"
        and event.get("stage_id") == "finalize"
        and event.get("decision") == "finalize"
        for event in _events(clean)
    )

    reader_failed = tmp_path / "rc-smoke-02"
    failed_report = _json(reader_failed / "output/intermediate/finalize_report.json")
    assert failed_report["status"] == "fail"
    assert failed_report["delivery_promotion"] == "skipped_reader_clean_failed"
    assert (reader_failed / "output/delivery/brief.md").exists()

    contaminated = tmp_path / "rc-smoke-03"
    contaminated_workflow = _json(contaminated / "output/intermediate/workflow_state.json")
    assert contaminated_workflow["run_integrity"]["reference_eligible"] is False
    assert any(
        event["event_type"] == "run_integrity_contaminated"
        for event in _events(contaminated)
    )

    recovered = tmp_path / "rc-smoke-04"
    recovered_workflow = _json(recovered / "output/intermediate/workflow_state.json")
    assert recovered_workflow["run_integrity"]["reference_eligible"] is False
    assert any(
        event["event_type"] == "repair_stage_superseded"
        for event in _events(recovered)
    )

    intake = tmp_path / "rc-smoke-05"
    intake_registry = _json(intake / "output/intermediate/artifact_registry.json")
    for artifact_id in ("candidate_claims", "screened_candidates", "claim_drafts"):
        projection = intake_registry["artifacts"][artifact_id]["intake_projection"]
        assert projection["artifact_id"] == artifact_id
        assert projection["fatal_finding_count"] == 0
        assert projection["normalization_count"] > 0

    for fatal in (tmp_path / "rc-smoke-06-source", tmp_path / "rc-smoke-06-id"):
        assert not (fatal / "output/intermediate/claim_ledger.json").exists()


def test_focused_selection_is_not_a_complete_readiness_matrix(tmp_path: Path) -> None:
    runner = _load_runner()

    payload = runner.run_v1_rc_safety_smoke(
        scenario_ids=["RC-SMOKE-08"],
        work_root=tmp_path,
    )

    assert payload["ok"] is True
    assert payload["required_complete"] is False
    assert payload["executed_scenario_ids"] == ["RC-SMOKE-08"]


def test_scenario_failure_cannot_be_reported_as_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()

    def fail(_parent: Path, _repo_root: Path):
        raise AssertionError("forced real execution failure")

    monkeypatch.setitem(runner.SCENARIOS, "RC-SMOKE-01", fail)
    payload = runner.run_v1_rc_safety_smoke(
        scenario_ids=["RC-SMOKE-01"],
        work_root=tmp_path,
    )

    assert payload["ok"] is False
    assert payload["scenarios"] == [
        {
            "scenario_id": "RC-SMOKE-01",
            "ok": False,
            "error_type": "AssertionError",
            "error": "forced real execution failure",
        }
    ]


@pytest.mark.parametrize(
    "scenario_ids, message",
    [
        (["RC-SMOKE-01", "RC-SMOKE-01"], "duplicate"),
        (["RC-SMOKE-99"], "Unknown"),
    ],
)
def test_runner_rejects_ambiguous_scenario_identity(
    scenario_ids: list[str],
    message: str,
) -> None:
    runner = _load_runner()

    with pytest.raises(ValueError, match=message):
        runner.run_v1_rc_safety_smoke(scenario_ids=scenario_ids)


def test_runner_cli_emits_machine_readable_execution_result(capsys) -> None:
    runner = _load_runner()

    rc = runner.main(["--scenario", "RC-SMOKE-08", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["executed_scenario_ids"] == ["RC-SMOKE-08"]
    assert payload["scenarios"][0]["ok"] is True
    assert payload["required_complete"] is False
