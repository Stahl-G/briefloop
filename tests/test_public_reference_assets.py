"""Current-contract checks for checked-in public reference assets."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Callable

import pytest

from multi_agent_brief.contracts.runtime_contracts import load_runtime_contract_payloads
from multi_agent_brief.contracts.target_contract import (
    auditable_gate_has_only_final_abstract_advisory_warnings,
)
from multi_agent_brief.quality_gates.contract import (
    FINDING_SEVERITIES,
    GATE_IDS,
    QUALITY_GATE_SCHEMA,
    validate_quality_gate_report_payload,
)


ROOT = Path(__file__).resolve().parents[1]
INIT_WEB_STATIC = ROOT / "src" / "multi_agent_brief" / "product" / "init_web" / "static"
NOTICE_PATH = INIT_WEB_STATIC / "THIRD_PARTY_NOTICES.txt"
PROVENANCE_PATH = INIT_WEB_STATIC / "provenance.json"
QUALITY_GATE_FIXTURE = (
    ROOT
    / "examples"
    / "reference-workspaces"
    / "industry-weekly-demo"
    / "artifacts"
    / "quality_gate_report.json"
)
UPSTREAM_REPOSITORY = "https://github.com/hugohe3/ppt-master"
UPSTREAM_COMMIT = "619a954695d866dde970552db9fb1a6640c643c8"
UPSTREAM_SURFACE = "skills/ppt-master/scripts/confirm_ui/"
UPSTREAM_LICENSE = "MIT"
UPSTREAM_COPYRIGHT = "Copyright (c) 2025-2026 Hugo He"
UPSTREAM_ASSET_HASHES = {
    "index.html_sha256": "cc623bf37c88a7ac73526962398bfc232d47bd25bb74395b5b8d978e39440b02",
    "app.js_sha256": "c40c9e23a58cff79e12f11a3721794bc57e811adbb14e6cf65a695b346b18318",
    "style.css_sha256": "4a3fe851410121cde5463487ce4c6f6c9df98b0dd443bf3aeb4e0f5256c08f18",
}
INIT_WEB_STATIC_FILENAMES = (
    "index.html",
    "app.js",
    "style.css",
    "THIRD_PARTY_NOTICES.txt",
    "provenance.json",
)


def _fixture_payload() -> dict[str, object]:
    return json.loads(QUALITY_GATE_FIXTURE.read_text(encoding="utf-8"))


def _contract_payloads() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    contracts = load_runtime_contract_payloads(ROOT)
    return list(contracts.stages), list(contracts.artifacts)


def _validation_errors(payload: dict[str, object]) -> list[str]:
    stages, artifacts = _contract_payloads()
    return validate_quality_gate_report_payload(
        payload, stages=stages, artifacts=artifacts
    )


def test_init_web_public_assets_are_pinned_and_source_fresh() -> None:
    static_bytes = {
        name: (INIT_WEB_STATIC / name).read_bytes()
        for name in INIT_WEB_STATIC_FILENAMES
    }
    notice = static_bytes[NOTICE_PATH.name]
    notice_text = notice.decode("utf-8")
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))

    assert "BriefLoop initialization panel" in notice_text
    assert UPSTREAM_REPOSITORY in notice_text
    assert UPSTREAM_COMMIT in notice_text
    assert UPSTREAM_SURFACE in notice_text
    assert "MIT License" in notice_text
    assert UPSTREAM_COPYRIGHT in notice_text

    assert provenance["schema_version"] == "briefloop.init_web.asset_provenance.v1"
    assert provenance["upstream_repository"] == UPSTREAM_REPOSITORY
    assert provenance["upstream_source_path"] == UPSTREAM_SURFACE
    assert provenance["upstream_ppt_master_commit"] == UPSTREAM_COMMIT
    assert provenance["upstream_license"] == UPSTREAM_LICENSE
    assert provenance["upstream_copyright"] == UPSTREAM_COPYRIGHT
    assert provenance["upstream_assets"] == UPSTREAM_ASSET_HASHES
    for retired_field in (
        "prototype_repository",
        "prototype_source_path",
        "prototype_assets",
    ):
        assert retired_field not in provenance

    for name, content in static_bytes.items():
        text = content.decode("utf-8")
        lowered = text.lower()
        for retired_reference in (
            "briefloop-prototypes",
            "interactive-init prototype",
            "intermediate prototype lineage",
            "quality panel",
            "post-final",
        ):
            assert retired_reference not in lowered, (name, retired_reference)

    for name in ("index.html", "app.js", "style.css"):
        text = static_bytes[name].decode("utf-8")
        for direct_upstream_value in (
            UPSTREAM_REPOSITORY,
            UPSTREAM_COMMIT,
            UPSTREAM_SURFACE,
            UPSTREAM_LICENSE,
            UPSTREAM_COPYRIGHT,
        ):
            assert direct_upstream_value in text, name

    expected_production_hashes = {
        f"{name}_sha256": hashlib.sha256(content).hexdigest()
        for name, content in static_bytes.items()
        if name != "provenance.json"
    }
    assert provenance["production_assets"] == expected_production_hashes

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert UPSTREAM_REPOSITORY in readme
    assert UPSTREAM_COMMIT in readme
    assert (
        "[third-party notice](src/multi_agent_brief/product/init_web/static/THIRD_PARTY_NOTICES.txt)"
        in readme
    )
    assert "[README.md](README.md)" in (ROOT / "README_en.md").read_text(
        encoding="utf-8"
    )


def test_public_quality_gate_fixture_uses_current_contract() -> None:
    payload = _fixture_payload()
    stages, artifacts = _contract_payloads()

    assert {
        "schema_version",
        "created_at",
        "updated_at",
        "workspace",
        "report_date",
        "policy_pack",
        "status",
        "gate_results",
        "findings",
        "metadata",
    } <= payload.keys()
    assert payload["schema_version"] == QUALITY_GATE_SCHEMA
    assert payload["created_at"] == "2026-06-14T09:08:00Z"
    assert payload["updated_at"] == "2026-06-14T09:08:00Z"
    assert payload["workspace"] == "industry-weekly-demo"
    assert payload["report_date"] == "2026-06-14"
    assert payload["policy_pack"] == "default"
    assert payload["status"] == "warning"
    assert "stage" not in payload
    assert "blocking_count" not in payload
    assert "warning_count" not in payload
    assert "boundary" not in payload
    assert _validation_errors(payload) == []

    gate_results = {item["gate_id"]: item for item in payload["gate_results"]}
    assert set(gate_results) == {
        "coverage_omission",
        "freshness",
        "material_fact",
        "target_relevance",
        "final_abstract_quality",
    }
    for gate_id in (
        "coverage_omission",
        "freshness",
        "material_fact",
        "target_relevance",
    ):
        assert gate_results[gate_id] == {
            "gate_id": gate_id,
            "status": "pass",
            "blocking": False,
            "finding_ids": [],
        }
    assert gate_results["final_abstract_quality"] == {
        "gate_id": "final_abstract_quality",
        "status": "warning",
        "blocking": False,
        "finding_ids": ["QG_FINAL_ABSTRACT_QUALITY_001"],
    }

    findings = payload["findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["gate_id"] in GATE_IDS
    assert finding["severity"] in FINDING_SEVERITIES
    assert finding["gate_id"] == "final_abstract_quality"
    assert finding["finding_type"] == "final_missing_comparison_basis"
    assert finding["severity"] == "medium"
    assert finding["blocking_level"] == "warning"
    assert finding["blocking"] is False
    assert finding["repair_owner"] == "none"
    assert finding["stage_id"] == "editor"
    assert finding["artifact_id"] == "audited_brief"
    assert finding["gate_stage_id"] == "auditor"
    assert finding["gate_artifact_id"] == "auditor_quality_gate_report"
    assert finding["repair_stage_id"] is None
    assert finding["repair_artifact_id"] is None
    assert "artifact" not in finding
    assert isinstance(finding["description"], str) and finding["description"]
    assert isinstance(finding["recommendation"], str) and finding["recommendation"]
    assert finding["metadata"]["repair_boundary"] == "advisory_non_routable"
    assert finding["metadata"]["authority_boundary"] == (
        "deterministic_warning_only_no_repair_or_delivery_authority"
    )

    known_stages = {stage["stage_id"] for stage in stages}
    known_artifacts = {artifact["artifact_id"] for artifact in artifacts}
    metadata = payload["metadata"]
    assert metadata["stage_id"] == "auditor"
    assert metadata["gate_stage_id"] == "auditor"
    assert metadata["gate_artifact_id"] == "auditor_quality_gate_report"
    assert metadata["brief"] == "output/intermediate/audited_brief.md"
    assert metadata["ledger"] == "output/intermediate/claim_ledger.json"
    assert metadata["authority_boundary"] == (
        "deterministic_warning_only_no_repair_or_delivery_authority"
    )
    for key in ("stage_id", "gate_stage_id", "repair_stage_id"):
        if finding[key] is not None:
            assert finding[key] in known_stages
    for key in ("artifact_id", "gate_artifact_id", "repair_artifact_id"):
        if finding[key] is not None:
            assert finding[key] in known_artifacts
    for key in ("stage_id", "gate_stage_id"):
        assert metadata[key] in known_stages
    assert metadata["gate_artifact_id"] in known_artifacts
    assert auditable_gate_has_only_final_abstract_advisory_warnings(payload)


def _old_schema(payload: dict[str, object]) -> None:
    payload["schema_version"] = "briefloop.quality_gate_report.v1"


def _retired_warning_severity(payload: dict[str, object]) -> None:
    payload["findings"][0]["severity"] = "warning"


def _unknown_stage(payload: dict[str, object]) -> None:
    payload["findings"][0]["stage_id"] = "unknown-stage"


def _unknown_artifact(payload: dict[str, object]) -> None:
    payload["findings"][0]["artifact_id"] = "unknown-artifact"


@pytest.mark.parametrize(
    "tamper",
    [_old_schema, _retired_warning_severity, _unknown_stage, _unknown_artifact],
)
def test_public_quality_gate_fixture_tampering_is_rejected(
    tamper: Callable[[dict[str, object]], None],
) -> None:
    payload = copy.deepcopy(_fixture_payload())
    tamper(payload)
    assert _validation_errors(payload)


def test_concrete_public_fixture_rejects_blocking_or_unrelated_warning_surface() -> (
    None
):
    blocking = copy.deepcopy(_fixture_payload())
    blocking["gate_results"][-1]["blocking"] = True
    assert not auditable_gate_has_only_final_abstract_advisory_warnings(blocking)

    unrelated_warning = copy.deepcopy(_fixture_payload())
    unrelated_warning["gate_results"][0]["status"] = "warning"
    assert not auditable_gate_has_only_final_abstract_advisory_warnings(
        unrelated_warning
    )
