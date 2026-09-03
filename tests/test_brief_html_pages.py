"""Page-data contract tests for the read-only three-page brief HTML."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256
from multi_agent_brief.product.brief_html import build_brief_pages_data
from multi_agent_brief.product.brief_html.builder import (
    BRIEF_PAGES_DATA_SCHEMA,
    LAJ_EXPERIMENTAL_BANNER,
)
from multi_agent_brief.runtime_host_v2.projections import (
    build_store_quality_projection,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LAJ_READER_SCHEMA_ID,
)
from tests.helpers import initialize_workspace


def _finding(report_sha256: str) -> dict[str, object]:
    return {
        "assessment_unit_id": "AU-0123456789ab",
        "scope_class": "O1",
        "dimension_id": "uncertainty_calibration",
        "severity": "major",
        "impact_scope": "decision",
        "report_spans": [
            {
                "report_sha256": report_sha256,
                "block_id": "B000001",
                "start_char": 0,
                "end_char": 12,
                "excerpt_sha256": "a" * 64,
            }
        ],
        "context_requirement_ids": [],
        "observation": "Observed uncertainty wording.",
        "rationale": "The wording overstates certainty.",
        "severity_basis": "Major because it changes the decision frame.",
        "confidence_basis": "direct_single_span",
        "external_premise_disclosure": "none",
        "recommended_human_action": "recalibrate_uncertainty",
        "suggested_rewrite": None,
        "finding_id": "F-0123456789ab",
        "status": "proposal",
    }


def _laj_view_payload(report_sha256: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": LAJ_READER_SCHEMA_ID,
        "status": "available",
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": True,
        "binding": {
            "artifact_id": "artifact-laj-1",
            "report_sha256": report_sha256,
            "trial_id": "trial-1",
            "shadow_receipt_id": "receipt-shadow-1",
            "instrument_sha256": "b" * 64,
            "execution_sha256": "c" * 64,
            "execution_origin": "synthetic",
            "model_id": "model-1",
            "model_version": "model-version-1",
            "archive_manifest_sha256": "d" * 64,
            "presentation_sha256": "e" * 64,
        },
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": ["assessment_completed"],
        "assessed_unit_count": 3,
        "finding_count": 1,
        "withheld_finding_count": 0,
        "abstention_count": 0,
        "findings": [_finding(report_sha256)],
        "requirement_assessments": [],
        "disclaimer": "Experimental advisory assessment.",
    }
    payload["view_sha256"] = canonical_sha256(payload)
    return payload


def _write_laj_view(workspace: Path, report_sha256: str) -> Path:
    import json

    target_dir = workspace / "laj-advisory-demo"
    target_dir.mkdir(parents=True)
    target = target_dir / "laj.json"
    target.write_text(
        json.dumps(_laj_view_payload(report_sha256), ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def test_quality_page_matches_store_projection_verbatim(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    data = build_brief_pages_data(workspace)

    assert data["schema_version"] == BRIEF_PAGES_DATA_SCHEMA
    assert data["workspace"]["authority"] == "sqlite_control_store"
    quality = data["quality"]
    assert quality["status"] == "unavailable"
    assert quality["reason_code"] == "final_reader_not_available"
    assert quality["projection"] == build_store_quality_projection(workspace)

    groups = quality["groups"]
    assert set(groups) == {
        "control",
        "source",
        "gates",
        "claims",
        "reader_clean",
        "closeout",
    }
    control = {row["label"]: row["value"] for row in groups["control"]}
    assert control["run_id"] == data["workspace"]["run_id"]
    assert control["store_revision"] == data["workspace"]["store_revision"]
    assert control["view_state"] == "setup"
    assert len(groups["gates"]) >= 1
    assert {row["label"] for row in groups["claims"]} == {"claims"}
    assert quality["actions"]


def test_semantic_page_is_honest_not_run_without_laj(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    _write_laj_view(workspace, "1" * 64)
    semantic = build_brief_pages_data(workspace)["semantic"]

    assert semantic["status"] == "not_run"
    assert semantic["banner"] == LAJ_EXPERIMENTAL_BANNER
    assert semantic["findings"] == []
    assert len(semantic["dimensions"]) == 9
    assert all(row["state"] == "not_assessed_in_view" for row in semantic["dimensions"])
    assert "never trigger Gates" in semantic["handoff_note"]
