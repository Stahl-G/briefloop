from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.product.materiality_selection import (
    MATERIALITY_SELECTION_RUNTIME_EFFECT,
    project_workspace_materiality_selection,
    validate_materiality_selection_payload,
)
from multi_agent_brief.product.quality_panel import (
    build_quality_panel,
    render_quality_panel_html,
    render_quality_summary,
    validate_quality_panel_payload,
)
from multi_agent_brief.status import build_workspace_status, format_workspace_status


def _workspace(tmp_path: Path, *, with_policy: bool = True) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "project": {"name": "Materiality Selection Test"},
                "focus": {"areas": ["tariff"]} if with_policy else {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if with_policy:
        (ws / "report_spec.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "briefloop.report_spec.v1",
                    "report_pack": "market_weekly",
                    "policy_profile": "manufacturing_default",
                    "report_type": "market_weekly",
                    "title": "Market Weekly Brief",
                    "cadence": "weekly",
                    "audience": {"label": "business reader", "language": "en-US"},
                    "source_policy": {"mode": "local_first", "hidden_autonomous_crawling": False},
                    "control_spine": {
                        "claim_ledger": True,
                        "artifact_registry": True,
                        "quality_gates": True,
                        "event_log": True,
                        "archive": True,
                        "source_appendix": True,
                        "support_records": True,
                        "human_delivery_approval": True,
                        "frozen_artifact_integrity": True,
                    },
                    "outputs": ["markdown"],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    assert main(["state", "init", "--runtime", "operator", "--workspace", str(ws)]) == 0
    return ws


def _write_screened_candidates(ws: Path) -> None:
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "candidate_claims.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": candidate_id,
                    "claim": f"Example candidate {candidate_id}.",
                    "source_id": source_id,
                }
                for candidate_id, source_id in (
                    ("CAND-001", "SRC-001"),
                    ("CAND-002", "SRC-002"),
                    ("CAND-003", "SRC-003"),
                )
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "screened_candidates.json").write_text(
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo reported routine supplier updates.",
                        "evidence_text": "ExampleCo reported routine supplier updates.",
                        "source_id": "SRC-001",
                        "retrieved_at": "2026-07-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "statement": "ExampleCo capacity expansion is delayed by tariff uncertainty.",
                        "source_id": "SRC-002",
                        "reason_code": "capacity_capped",
                        "explanation": "Capacity cap applied after selection.",
                    }
                ],
                "deprioritized": [
                    {
                        "candidate_id": "CAND-003",
                        "statement": "Inventory movements were off focus for this brief.",
                        "source_id": "SRC-003",
                        "reason_code": "off_focus",
                        "explanation": "Outside the selected brief focus.",
                    }
                ],
                "screening_policy": {
                    "method": "deterministic_test",
                    "total_candidates": 3,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_materiality_selection_flags_capacity_capped_policy_or_focus_terms(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_screened_candidates(ws)

    projection = project_workspace_materiality_selection(ws)

    assert projection["status"] == "checked"
    assert projection["runtime_effect"] == MATERIALITY_SELECTION_RUNTIME_EFFECT
    assert projection["read_only"] is True
    assert projection["summary_counts"]["finding_count"] == 2
    assert projection["summary_counts"]["human_review_recommended_count"] == 1
    assert projection["findings"][0]["candidate_id"] == "CAND-002"
    assert projection["findings"][0]["reason_code"] == "capacity_capped"
    assert projection["findings"][0]["severity"] == "human_review"
    assert "capacity" in projection["findings"][0]["matched_materiality_terms"]
    assert "tariff" in projection["findings"][0]["matched_must_watch_terms"]
    assert projection["recommended_actions"] == [
        {
            "action": "request_human_review",
            "reason": "materiality_or_focus_candidate_excluded_by_capacity_or_scope",
        }
    ]
    assert validate_materiality_selection_payload(projection) is None


def test_materiality_selection_consumes_normalized_intake_view(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    candidate_path = ws / "output" / "intermediate" / "candidate_claims.json"
    candidate_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "CAND-002",
                    "claim": "ExampleCo capacity expansion is delayed by tariff uncertainty.",
                    "source_id": "SRC-002",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path = ws / "output" / "intermediate" / "screened_candidates.json"
    path.write_text(
        json.dumps(
            {
                "selected_candidates": [],
                "excluded_candidates": [
                    {
                        "candidate_id": "CAND-002",
                        "claim_statement": (
                            "ExampleCo capacity expansion is delayed by tariff uncertainty."
                        ),
                        "source_id": "SRC-002",
                        "reason_code": "capacity_capped",
                        "explanation": "Capacity cap applied after selection.",
                    }
                ],
                "screening_policy": {"total_candidates": 1},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    projection = project_workspace_materiality_selection(ws)

    assert projection["status"] == "checked"
    assert projection["discarded_count"] == 1
    assert projection["findings"][0]["candidate_id"] == "CAND-002"
    assert projection["findings"][0]["statement"].startswith("ExampleCo capacity")


def test_status_and_quality_panel_surface_materiality_selection_without_authority(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_screened_candidates(ws)

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    assert status["materiality_selection"]["status"] == "checked"
    assert "[status] materiality_selection: checked findings=2 human_review=1" in formatted

    panel = build_quality_panel(ws)
    assert validate_quality_panel_payload(panel) is None
    assert panel["materiality_selection"]["summary_counts"]["finding_count"] == 2
    assert {
        "action": "request_human_review",
        "reason": "materiality_or_focus_candidate_excluded_by_capacity_or_scope",
    } in panel["recommended_actions"]
    assert panel["runtime_effect"] == "projection_only"
    assert panel["materiality_selection"]["runtime_effect"] == "none"
    assert panel["boundary"]

    panel_sha = hashlib.sha256(
        json.dumps(panel, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    summary = render_quality_summary(panel, quality_panel_sha256=panel_sha)
    html = render_quality_panel_html(panel, quality_panel_sha256=panel_sha)

    assert "Materiality selection status: `checked`" in summary
    assert "Materiality/focus exclusions: `2`" in summary
    assert "Materiality/focus exclusions" in html
    assert "ready to publish" not in summary
    assert "approved for release" not in html


def test_materiality_selection_does_not_guess_without_policy_or_focus_terms(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, with_policy=False)
    _write_screened_candidates(ws)

    projection = project_workspace_materiality_selection(ws)

    assert projection["status"] == "no_materiality_policy"
    assert projection["summary_counts"]["finding_count"] == 0
    assert projection["recommended_actions"] == []
    assert validate_materiality_selection_payload(projection) is None


def test_materiality_selection_rejects_contract_invalid_screened_candidates(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    path = ws / "output" / "intermediate" / "screened_candidates.json"
    path.write_text(
        json.dumps({"selected": [], "excluded": "not-a-list"}, indent=2) + "\n",
        encoding="utf-8",
    )

    projection = project_workspace_materiality_selection(ws)
    status = build_workspace_status(ws)
    panel = build_quality_panel(ws)

    assert projection["status"] == "invalid_screened_candidates"
    assert projection["reason"] == "screened_candidates_schema_error:screening_policy"
    assert projection["summary_counts"]["finding_count"] == 0
    assert projection["recommended_actions"] == []
    assert status["materiality_selection"]["status"] == "invalid_screened_candidates"
    assert panel["materiality_selection"]["status"] == "invalid_screened_candidates"
    assert validate_quality_panel_payload(panel) is None


def test_materiality_does_not_interpret_universe_invalid_screening(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (intermediate / "candidate_claims.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "CAND-001",
                    "claim": "ExampleCo reported a supplier update.",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "screened_candidates.json").write_text(
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-999",
                        "statement": "ExampleCo reported a supplier update.",
                        "evidence_text": "ExampleCo reported a supplier update.",
                        "source_id": "SRC-001",
                        "retrieved_at": "2026-07-01",
                    }
                ],
                "excluded": [],
                "screening_policy": {"total_candidates": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    projection = project_workspace_materiality_selection(ws)

    assert projection["status"] == "invalid_screened_candidates"
    assert "unknown_candidate_id:CAND-999" in projection["reason"]
    assert projection["summary_counts"]["finding_count"] == 0
    assert projection["recommended_actions"] == []


def test_materiality_selection_rejects_malformed_discard_bucket(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    path = ws / "output" / "intermediate" / "screened_candidates.json"
    path.write_text(
        json.dumps(
            {
                "selected": [],
                "excluded": "not-a-list",
                "screening_policy": {"method": "deterministic_test"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    projection = project_workspace_materiality_selection(ws)

    assert projection["status"] == "invalid_screened_candidates"
    assert projection["reason"] == "screened_candidates_schema_error:excluded"
    assert projection["summary_counts"]["finding_count"] == 0
    assert projection["recommended_actions"] == []
    assert validate_materiality_selection_payload(projection) is None


def test_materiality_selection_rejects_bad_screening_policy(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    path = ws / "output" / "intermediate" / "screened_candidates.json"
    path.write_text(
        json.dumps(
            {
                "selected": [],
                "excluded": [],
                "screening_policy": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    projection = project_workspace_materiality_selection(ws)

    assert projection["status"] == "invalid_screened_candidates"
    assert projection["reason"] == "screened_candidates_schema_error:screening_policy"
    assert projection["summary_counts"]["finding_count"] == 0
    assert projection["recommended_actions"] == []
    assert validate_materiality_selection_payload(projection) is None


def test_materiality_selection_validator_rejects_authority_shape(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_screened_candidates(ws)
    projection = project_workspace_materiality_selection(ws)

    projection["findings"][0]["semantic_importance_score"] = 1

    assert validate_materiality_selection_payload(projection) == (
        "materiality_selection_schema_error:authority_field"
    )
