from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.product.support_wording import (
    SUPPORT_WORDING_BOUNDARY,
    project_workspace_support_wording,
    validate_support_wording_payload,
)
from tests.helpers import initialized_workspace_writer


_workspace = initialized_workspace_writer(
    project_name="Support Wording Test",
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


def _claim(*, source_category: str = "company_press_release", confidence: str = "medium") -> dict:
    return {
        "claim_id": "CL-0001",
        "statement": "ExampleCo will expand shipments this quarter.",
        "source_id": "SRC-001",
        "evidence_text": "ExampleCo may expand shipments this quarter.",
        "claim_type": "forecast",
        "confidence": confidence,
        "source_type": "manual",
        "metadata": {
            "source_title": "Example Source",
            "source_category": source_category,
            "retrieval_source_type": "local_file",
            "underlying_evidence_type": "company_claim",
        },
        "schema_version": "v2",
        "epistemic_type": "hypothesis",
        "evidence_relation": "inferred",
    }


def _write_claim_stack(
    ws: Path,
    *,
    row: dict | None = None,
    claim: dict | None = None,
    atom_materiality: str = "high",
) -> None:
    claim_payload = claim or _claim()
    _write_json(ws / "output" / "intermediate" / "claim_ledger.json", [claim_payload])
    source_file = ws / "input" / "sources" / "source-001.txt"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("ExampleCo may expand shipments this quarter.", encoding="utf-8")
    _write_json(
        ws / "output" / "intermediate" / "atomic_claim_graph.json",
        {
            "schema_version": "mabw.atomic_claim_graph.v1",
            "claims": [
                {
                    "claim_id": "CL-0001",
                    "atoms": [
                        {
                            "atom_id": "AC-0001-01",
                            "text": "ExampleCo may expand shipments this quarter.",
                            "claim_role": "forward_looking_inference",
                            "materiality": atom_materiality,
                        }
                    ],
                    "edges": [],
                }
            ],
        },
    )
    _write_json(
        ws / "output" / "intermediate" / "evidence_span_registry.json",
        {
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": "SRC-001",
                    "source_type": "manual",
                    "source_tier": "company_official",
                    "source_path": "input/sources/source-001.txt",
                    "retrieved_at": "2026-07-01T00:00:00Z",
                    "spans": [
                        {
                            "span_id": "ESP-001-01",
                            "raw_excerpt": "ExampleCo may expand shipments this quarter.",
                            "hash": "sha256:004025835bb813954b9ec7592145fa8513a5aefe6483aafd610ef0a818b67e16",
                            "span_role": "direct_statement",
                        }
                    ],
                }
            ],
        },
    )
    if row is not None:
        _write_json(
            ws / "output" / "intermediate" / "claim_support_matrix.json",
            {"schema_version": "mabw.claim_support_matrix.v1", "rows": [row]},
        )


def _csm_row(*, support_label: str, support_strength: str, required_action: str) -> dict:
    return {
        "row_id": "CSM-0001",
        "atom_id": "AC-0001-01",
        "claim_id": "CL-0001",
        "evidence_span_id": None if support_label in {"unsupported", "insufficient_evidence"} else "ESP-001-01",
        "support_label": support_label,
        "support_strength": support_strength,
        "support_reason": "Synthetic support record for wording projection tests.",
        "required_action": required_action,
        "repair_owner": "human_review" if required_action == "human_adjudication" else "editor",
        "decision_source": "human",
    }


def test_support_wording_warns_on_weak_support_strong_reader_wording(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_claim_stack(
        ws,
        row=_csm_row(
            support_label="weak_support",
            support_strength="low",
            required_action="downgrade_wording",
        ),
    )
    (ws / "output" / "brief.md").write_text(
        "# Brief\n\nExampleCo will expand shipments this quarter. [S1]\n",
        encoding="utf-8",
    )

    projection = project_workspace_support_wording(ws)

    assert projection["boundary"] == SUPPORT_WORDING_BOUNDARY
    assert projection["status"] == "checked"
    assert projection["support_artifact_status"] == "valid"
    assert projection["summary_counts"]["weak_support_strong_wording_count"] == 1
    assert {finding["finding_type"] for finding in projection["findings"]} >= {
        "weak_support_strong_wording",
    }
    assert validate_support_wording_payload(projection) is None


def test_support_wording_ignores_invalid_csm_as_support_authority(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_claim_stack(ws, row=None, claim={**_claim(source_category="company_press_release"), "confidence": "medium"})
    _write_json(
        ws / "output" / "intermediate" / "claim_support_matrix.json",
        {"schema_version": "mabw.claim_support_matrix.v1", "rows": "not-a-list"},
    )
    (ws / "output" / "brief.md").write_text(
        "# Brief\n\nExampleCo will expand shipments this quarter. [S1]\n",
        encoding="utf-8",
    )

    projection = project_workspace_support_wording(ws)

    assert projection["support_artifact_status"] == "invalid"
    assert projection["summary_counts"]["unsupported_reader_claim_count"] == 0
    assert all(finding["finding_type"] != "unsupported_claim_reaches_reader" for finding in projection["findings"])


def test_support_wording_flags_unsupported_label_even_when_not_policy_blocking(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_claim_stack(
        ws,
        atom_materiality="low",
        row=_csm_row(
            support_label="unsupported",
            support_strength="none",
            required_action="none",
        ),
    )
    (ws / "output" / "brief.md").write_text(
        "# Brief\n\nExampleCo will expand shipments this quarter. [S1]\n",
        encoding="utf-8",
    )

    projection = project_workspace_support_wording(ws)

    assert projection["support_artifact_status"] == "valid"
    assert projection["summary_counts"]["unsupported_reader_claim_count"] == 1
    assert any(
        finding["finding_type"] == "unsupported_claim_reaches_reader"
        and finding["severity"] == "human_review"
        for finding in projection["findings"]
    )
    assert {item["action"] for item in projection["recommended_actions"]} == {"request_human_review"}
    assert validate_support_wording_payload(projection) is None


def test_support_wording_rejects_invalid_claim_ledger_entries(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    invalid_claim = {**_claim(), "statement": "", "source_id": ""}
    _write_json(ws / "output" / "intermediate" / "claim_ledger.json", [invalid_claim])
    (ws / "output" / "brief.md").write_text(
        "# Brief\n\nExampleCo will expand shipments this quarter. [S1]\n",
        encoding="utf-8",
    )

    projection = project_workspace_support_wording(ws)

    assert projection["status"] == "invalid_claim_ledger"
    assert projection["reason"].startswith("claim_ledger_invalid:claims[0].")
    assert projection["findings"] == []
    # LEGACY-DELETE: the retired legacy JSON status/quality-panel surfaces no
    # longer fold support_wording; the direct projection seam is authoritative.
    assert validate_support_wording_payload(projection) is None


def test_support_wording_rejects_non_string_claim_ledger_fields(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    invalid_claim = {**_claim(), "claim_id": 123}
    _write_json(ws / "output" / "intermediate" / "claim_ledger.json", [invalid_claim])
    (ws / "output" / "brief.md").write_text(
        "# Brief\n\nExampleCo will expand shipments this quarter. [S1]\n",
        encoding="utf-8",
    )

    projection = project_workspace_support_wording(ws)

    assert projection["status"] == "invalid_claim_ledger"
    assert projection["reason"] == "claim_ledger_invalid:claims[0].claim_id"
    assert projection["findings"] == []
    # LEGACY-DELETE: the retired legacy JSON status/quality-panel surfaces no
    # longer fold support_wording; the direct projection seam is authoritative.
    assert validate_support_wording_payload(projection) is None


def test_support_wording_unreadable_reader_target_is_not_checked(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_claim_stack(ws, row=None)
    (ws / "output" / "brief.md").parent.mkdir(parents=True, exist_ok=True)
    (ws / "output" / "brief.md").write_bytes(b"\xff\xfe\xfa")

    projection = project_workspace_support_wording(ws)

    assert projection["status"] == "not_available"
    assert projection["reason"] == "reader_targets_unreadable"
    assert projection["summary_counts"]["unreadable_target_count"] == 1
    assert projection["summary_counts"]["present_target_count"] == 0
    assert projection["findings"] == []
    assert validate_support_wording_payload(projection) is None


def test_status_and_quality_panel_survive_unreadable_reader_markdown(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_claim_stack(ws, row=None)
    (ws / "output" / "brief.md").parent.mkdir(parents=True, exist_ok=True)
    (ws / "output" / "brief.md").write_bytes(b"\xff\xfe\xfa")

    before = _workspace_file_bytes(ws)
    projection = project_workspace_support_wording(ws)

    # LEGACY-DELETE: build_workspace_status/build_quality_panel no longer fold
    # support_wording or atomic_reader_projection on ControlStore workspaces;
    # the read-only direct projection seam carries the invariant.
    assert projection["status"] == "not_available"
    assert projection["reason"] == "reader_targets_unreadable"
    assert projection["summary_counts"]["unreadable_target_count"] == 1
    assert projection["summary_counts"]["present_target_count"] == 0
    assert projection["findings"] == []
    assert validate_support_wording_payload(projection) is None
    assert _workspace_file_bytes(ws) == before


def test_support_wording_projects_to_status_and_quality_panel(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_claim_stack(
        ws,
        row=_csm_row(
            support_label="unsupported",
            support_strength="none",
            required_action="human_adjudication",
        ),
    )
    (ws / "output" / "brief.md").write_text(
        "# Brief\n\nExampleCo will expand shipments this quarter. [S1]\n",
        encoding="utf-8",
    )

    projection = project_workspace_support_wording(ws)

    assert projection["status"] == "checked"
    assert projection["summary_counts"]["unsupported_reader_claim_count"] == 1
    # LEGACY-DELETE: the retired legacy JSON status/quality-panel fold-in
    # (including the "[status] support_wording" formatter line) is removed with
    # the retired surface; the direct projection seam carries the invariant.
    assert {item["action"] for item in projection["recommended_actions"]} >= {"request_human_review"}
    assert validate_support_wording_payload(projection) is None


def test_quality_panel_rejects_forged_support_wording_authority() -> None:
    payload = {
        "schema_version": "briefloop.support_wording.v1",
        "read_only": True,
        "runtime_effect": "state_transition",
        "boundary": SUPPORT_WORDING_BOUNDARY,
        "status": "checked",
        "targets": [],
        "findings": [],
        "summary_counts": {
            "target_count": 2,
            "present_target_count": 0,
            "unreadable_target_count": 0,
            "finding_count": 0,
            "unsupported_reader_claim_count": 0,
            "weak_support_strong_wording_count": 0,
            "inference_without_framing_count": 0,
            "source_class_strong_wording_count": 0,
        },
        "recommended_actions": [{"action": "approve_delivery"}],
        "non_goals": sorted(
            {
                "semantic_truth_proof",
                "support_truth_assessment",
                "claim_support_matrix_generation",
                "semantic_assessment_acceptance",
                "gate_decision",
                "delivery_approval",
                "release_authority",
                "quality_score",
            }
        ),
    }

    assert validate_support_wording_payload(payload) == "support_wording_schema_error:runtime_effect"
