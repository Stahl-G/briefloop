from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml

from multi_agent_brief.outputs.finalize import finalize_reader_outputs
from multi_agent_brief.product.policy_gate_adapter import (
    policy_forbidden_phrases,
    policy_gate_is_strict,
    resolve_workspace_policy_gate_adapter,
)
from multi_agent_brief.product.policy_projection import project_workspace_policy_profile
from multi_agent_brief.quality_gates import state as quality_gate_state
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "policy_profile_dogfood" / "cases.json"
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _load_fixture_bundle() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_cases() -> list[dict[str, Any]]:
    cases = _load_fixture_bundle().get("cases")
    assert isinstance(cases, list)
    return cases


def _write_fixture_workspace(tmp_path: Path, case: dict[str, Any]) -> Path:
    bundle = _load_fixture_bundle()
    base = bundle["base"]
    ws = tmp_path / case["case_id"]
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "TargetCo"
output:
  path: "output"
input:
  path: "input"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# User\nTarget: TargetCo\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")

    report_spec = deepcopy(base["report_spec"])
    if case.get("policy_profile") is None:
        report_spec.pop("policy_profile", None)
    else:
        report_spec["policy_profile"] = case["policy_profile"]
    (ws / "report_spec.yaml").write_text(yaml.safe_dump(report_spec, sort_keys=False), encoding="utf-8")

    (intermediate / "claim_ledger.json").write_text(
        json.dumps(base["claim_ledger"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(case["audited_brief"], encoding="utf-8")
    initialize_runtime_state(
        workspace=ws,
        runtime="operator",
        repo_workdir=ROOT,
        actor="system",
    )
    return ws


def test_policy_profile_dogfood_fixture_bundle_is_public_safe_and_bounded() -> None:
    bundle = _load_fixture_bundle()
    rendered = json.dumps(bundle, ensure_ascii=False)

    assert bundle["schema_version"] == "briefloop.policy_profile_dogfood_fixture.v1"
    assert bundle["metadata"]["synthetic"] is True
    assert bundle["metadata"]["public_safe"] is True
    assert "private_planning" not in rendered
    assert "/Users/" not in rendered
    assert "file://" not in rendered.lower()
    assert "truth proof" in bundle["metadata"]["boundary"]
    assert "release readiness" in bundle["metadata"]["boundary"]
    urls = _urls_from_fixture(bundle)
    assert urls
    for url in urls:
        assert urlparse(url).hostname == "example.com"


def test_policy_profile_dogfood_url_guard_extracts_json_string_values() -> None:
    payload = {"source_url": "https://example.com/targetco-demo", "note": "synthetic URL"}

    assert _urls_from_fixture(payload) == ["https://example.com/targetco-demo"]


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["case_id"])
def test_policy_profile_dogfood_resolves_expected_profile(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    ws = _write_fixture_workspace(tmp_path, case)
    expected = case["expected"]

    projection = project_workspace_policy_profile(ws)
    adapter = resolve_workspace_policy_gate_adapter(ws)

    assert projection["status"] == "resolved"
    assert projection["resolved_policy_profile"] == expected["resolved_policy_profile"]
    assert projection["source"] == expected["source"]
    assert adapter["status"] == expected["adapter_status"]
    assert adapter["policy_profile_id"] == expected["resolved_policy_profile"]
    assert policy_gate_is_strict(adapter, "target_relevance") is expected.get("target_relevance_strict", False)
    if "forbidden_phrases_any" in expected:
        assert bool(policy_forbidden_phrases(adapter)) is expected["forbidden_phrases_any"]
    assert "compliance" not in adapter.get("runtime_effect", "")


@pytest.mark.parametrize(
    "case",
    [case for case in _fixture_cases() if "quality_gate_finding_type" in case["expected"]],
    ids=lambda case: case["case_id"],
)
def test_policy_profile_dogfood_quality_gate_blocks_expected_cases(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    ws = _write_fixture_workspace(tmp_path, case)
    expected = case["expected"]

    report = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)["quality_gate_report"]

    assert report["metadata"]["policy_gate_adapter"]["status"] == "applied"
    assert report["metadata"]["gate_strictness"]["target_relevance"] is expected["target_relevance_strict"]
    finding = next(item for item in report["findings"] if item["finding_type"] == expected["quality_gate_finding_type"])
    assert (finding["blocking_level"] == "blocking") is expected["quality_gate_blocking"]


@pytest.mark.parametrize(
    "case",
    [case for case in _fixture_cases() if "quality_gate_target_relevance_findings" in case["expected"]],
    ids=lambda case: case["case_id"],
)
def test_policy_profile_dogfood_standard_target_relevance_remains_non_blocking(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    ws = _write_fixture_workspace(tmp_path, case)
    expected = case["expected"]

    report = quality_gate_state.check_quality_gates(workspace=ws, repo_workdir=ROOT)["quality_gate_report"]
    target_findings = [item for item in report["findings"] if item["gate_id"] == "target_relevance"]

    assert report["metadata"]["policy_gate_adapter"]["status"] == "applied"
    assert report["metadata"]["gate_strictness"]["target_relevance"] is False
    assert len(target_findings) == expected["quality_gate_target_relevance_findings"]


@pytest.mark.parametrize(
    "case",
    [case for case in _fixture_cases() if "reader_clean_finding_kind" in case["expected"]],
    ids=lambda case: case["case_id"],
)
def test_policy_profile_dogfood_finalize_reader_forbidden_phrase(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    ws = _write_fixture_workspace(tmp_path, case)
    expected = case["expected"]

    with pytest.raises(RuntimeError, match="Reader final output gate failed"):
        finalize_reader_outputs(
            output_dir=ws / "output",
            project_name="PolicyProfile Dogfood",
            output_formats=["markdown"],
            output_named_outputs=False,
        )

    report = json.loads((ws / "output" / "intermediate" / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["policy_gate_adapter"]["status"] == "applied"
    assert report["policy_gate_adapter"]["policy_profile_id"] == expected["resolved_policy_profile"]
    assert report["reader_clean"]["status"] == expected["reader_clean_status"]
    assert report["reader_clean"]["sample_findings"][0]["kind"] == expected["reader_clean_finding_kind"]


def _urls_from_fixture(value: Any) -> list[str]:
    if isinstance(value, str):
        return [match.rstrip(".,);]") for match in _URL_RE.findall(value)]
    if isinstance(value, dict):
        urls: list[str] = []
        for item in value.values():
            urls.extend(_urls_from_fixture(item))
        return urls
    if isinstance(value, list):
        urls = []
        for item in value:
            urls.extend(_urls_from_fixture(item))
        return urls
    return []
