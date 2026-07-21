from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "semantic_assessment_dogfood" / "cases.json"


def _load_fixture_bundle() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_cases() -> list[dict[str, Any]]:
    bundle = _load_fixture_bundle()
    cases = bundle.get("cases")
    assert isinstance(cases, list)
    return cases




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






