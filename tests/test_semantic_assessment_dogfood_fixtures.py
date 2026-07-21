from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract
from multi_agent_brief.product.quality_panel import build_quality_panel, validate_quality_panel_payload


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
    }
    for name, payload in artifacts.items():
        (intermediate / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    report_path = intermediate / "semantic_assessment_report.json"
    if "semantic_assessment_report_text" in case:
        report_path.write_text(str(case["semantic_assessment_report_text"]), encoding="utf-8")
    else:
        report_path.write_text(
            json.dumps(case["semantic_assessment_report"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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


def test_semantic_assessment_dogfood_fixture_cases_cover_pr5_scenarios() -> None:
    case_ids = {case["case_id"] for case in _fixture_cases()}

    assert {
        "supported_claim_pass",
        "unsupported_claim_proposal",
        "overstated_claim_proposal",
        "missing_limitation_proposal",
        "external_knowledge_attempt_invalid",
        "free_text_non_json_invalid",
        "none_string_not_pass",
        "llm_only_high_materiality_requires_adjudication",
    }.issubset(case_ids)


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["case_id"])
def test_semantic_assessment_dogfood_reports_match_schema_expectation(case: dict[str, Any]) -> None:
    expected = case["expected"]
    if "semantic_assessment_report_text" in case:
        assert expected["artifact_status"] == "invalid"
        assert expected.get("schema_valid") is False
        return

    report = deepcopy(case["semantic_assessment_report"])
    violations = SemanticAssessmentReportContract.validate(report)

    if expected.get("schema_valid", True):
        assert violations == []
    else:
        assert violations != []






def test_semantic_assessment_dogfood_quality_panel_surfaces_proposal_counts(
    tmp_path: Path,
) -> None:
    case = next(case for case in _fixture_cases() if case["case_id"] == "mixed_valid_proposals")
    ws = _write_fixture_workspace(tmp_path, case)

    panel = build_quality_panel(ws)

    semantic = panel["semantic_support"]
    assert semantic["status"] == "valid"
    assert semantic["proposal_count"] == 4
    assert semantic["requires_human_adjudication_count"] == 2
    assert semantic["recommended_human_review"] is True
    assert {
        "action": "request_human_review",
        "reason": "semantic_support_human_adjudication_required",
    } in panel["recommended_actions"]
    forbidden_actions = {"approve_delivery", "auto_repair", "deliver", "release"}
    assert not forbidden_actions.intersection(
        str(item.get("action") or "") for item in panel["recommended_actions"]
    )
    for key in (
        "accepted_support_truth",
        "delivery_authority",
        "gate_decision",
        "release_authority",
        "repair_execution",
        "state_transition",
        "writes_claim_support_matrix",
    ):
        assert key not in semantic
    assert validate_quality_panel_payload(panel) is None
