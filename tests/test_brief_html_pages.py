"""Page-data contract tests for the read-only three-page brief HTML."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256
from multi_agent_brief.product.brief_html import build_brief_pages_data
from multi_agent_brief.product.brief_html.builder import (
    BRIEF_PAGES_DATA_SCHEMA,
    IMPROVEMENT_CONSUMPTION_NOTE,
    IMPROVEMENT_PLANNED_NOTE,
    LAJ_EXPERIMENTAL_BANNER,
)
from multi_agent_brief.runtime_host_v2.projections import (
    build_store_quality_projection,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LAJ_READER_SCHEMA_ID,
)
from tests.helpers import initialize_workspace, sha256_file


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
    assert quality["reason_code"] == "package_not_ready"
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
    assert isinstance(control["contract_fingerprint"], str)
    assert len(groups["gates"]) >= 1
    assert {row["label"] for row in groups["claims"]} == {
        "claims",
        "claim_freezes",
        "claim_types",
    }
    assert quality["actions"]


def test_semantic_page_is_honest_not_run_without_laj(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    semantic = build_brief_pages_data(workspace)["semantic"]

    assert semantic["status"] == "not_run"
    assert semantic["banner"] == LAJ_EXPERIMENTAL_BANNER
    assert semantic["findings"] == []
    assert len(semantic["dimensions"]) == 9
    assert all(
        row["state"] == "not_assessed_in_view" for row in semantic["dimensions"]
    )
    assert "never trigger Gates" in semantic["handoff_note"]


def test_semantic_page_renders_bound_findings(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    brief = workspace / "output" / "brief.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("# demo brief\n", encoding="utf-8")
    _write_laj_view(workspace, sha256_file(brief))

    semantic = build_brief_pages_data(workspace)["semantic"]
    assert semantic["status"] == "available"
    assert semantic["coverage"]["finding_count"] == 1
    finding = semantic["findings"][0]
    assert finding["finding_id"] == "F-0123456789ab"
    assert finding["severity"] == "major"
    assert finding["dimension_id"] == "uncertainty_calibration"
    assert finding["report_spans"][0]["block_id"] == "B000001"
    states = {row["dimension_id"]: row["state"] for row in semantic["dimensions"]}
    assert states["uncertainty_calibration"] == "finding_reported"
    assert len(states) == 9


def test_semantic_page_marks_stale_when_report_binding_drifts(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    _write_laj_view(workspace, "1" * 64)
    brief = workspace / "output" / "brief.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("# different brief\n", encoding="utf-8")

    semantic = build_brief_pages_data(workspace)["semantic"]
    assert semantic["status"] == "stale"
    assert semantic["findings"] == []


def test_semantic_page_honors_explicit_laj_view_path(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    view_path = _write_laj_view(workspace, "1" * 64)
    semantic = build_brief_pages_data(workspace, laj_view_path=view_path)["semantic"]
    assert semantic["status"] == "available"
    assert semantic["coverage"]["finding_count"] == 1


def test_improvement_page_is_honest_unavailable(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    improvement = build_brief_pages_data(workspace)["improvement"]

    assert improvement["status"] == "unavailable"
    assert improvement["reason_code"] == "pf_review_2_not_shipped"
    assert improvement["recorded"] == []
    assert improvement["consumption_note"] == IMPROVEMENT_CONSUMPTION_NOTE
    assert improvement["planned_note"] == IMPROVEMENT_PLANNED_NOTE
