from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract
from multi_agent_brief.orchestrator.runtime_state import check_runtime_state, initialize_runtime_state
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    project_semantic_assessment_report_from_workspace,
)


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "semantic_assessment_dogfood" / "cases.json"


def _load_fixture_bundle() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_cases() -> list[dict[str, Any]]:
    bundle = _load_fixture_bundle()
    cases = bundle.get("cases")
    assert isinstance(cases, list)
    return cases


def _write_fixture_workspace(tmp_path: Path, case: dict[str, Any]) -> Path:
    bundle = _load_fixture_bundle()
    base = bundle["base"]
    case_id = case["case_id"]
    ws = tmp_path / case_id
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "input").mkdir(exist_ok=True)
    (ws / "config.yaml").write_text(
        """
project:
  name: "Semantic Assessment Fixture"
output:
  path: "output"
input:
  path: "input"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# User\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    for rel_path, content in base["source_files"].items():
        path = ws / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    artifacts = {
        "claim_ledger.json": base["claim_ledger"],
        "atomic_claim_graph.json": base["atomic_claim_graph"],
        "evidence_span_registry.json": base["evidence_span_registry"],
        "semantic_assessment_report.json": case["semantic_assessment_report"],
    }
    for name, payload in artifacts.items():
        (intermediate / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return ws


def test_semantic_assessment_dogfood_fixture_bundle_is_public_safe_and_bounded() -> None:
    bundle = _load_fixture_bundle()
    rendered = json.dumps(bundle, ensure_ascii=False)

    assert bundle["schema_version"] == "mabw.semantic_assessment_dogfood_fixture.v1"
    assert "private_planning" not in rendered
    assert "release authority" in bundle["metadata"]["boundary"]
    assert "semantic proof" in bundle["metadata"]["boundary"]


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["case_id"])
def test_semantic_assessment_dogfood_reports_are_schema_valid(case: dict[str, Any]) -> None:
    report = deepcopy(case["semantic_assessment_report"])

    assert SemanticAssessmentReportContract.validate(report) == []


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["case_id"])
def test_semantic_assessment_dogfood_fixtures_validate_through_runtime_state(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    ws = _write_fixture_workspace(tmp_path, case)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    registry = state["artifact_registry"]["artifacts"]
    report_record = registry["semantic_assessment_report"]
    expected = case["expected"]

    assert registry["atomic_claim_graph"]["status"] == "valid"
    assert registry["evidence_span_registry"]["status"] == "valid"
    assert report_record["required"] is False
    assert report_record["status"] == expected["artifact_status"]
    assert report_record["validation_result"] == expected["validation_result"]


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["case_id"])
def test_semantic_assessment_dogfood_fixtures_project_expected_status(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    ws = _write_fixture_workspace(tmp_path, case)

    projection = project_semantic_assessment_report_from_workspace(ws)
    expected = case["expected"]

    assert projection["status"] == expected["projection_status"]
    if expected["projection_status"] == "valid":
        assert projection["summary_counts"] == expected["summary_counts"]
        assert projection["proposal_projection"]["semantic_boundary"] == (
            "proposal_projection_only_not_accepted_support_truth"
        )
        assert projection["proposal_projection"]["proposed_csm_delta"]["accepted_csm_rows"] == []
        assert all(
            row["accepted_support_truth"] is False and row["writes_claim_support_matrix"] is False
            for row in projection["proposed_claim_support_rows"]
        )
    else:
        assert projection["reason"] == expected["validation_result"]
        assert projection["proposed_claim_support_rows"] == []
