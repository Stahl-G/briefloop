from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.outputs.atomic_reader_projection import (
    project_atomic_reader_text,
    project_atomic_reader_text_from_workspace,
)


def _graph() -> dict:
    return {
        "schema_version": "mabw.atomic_claim_graph.v1",
        "claims": [
            {
                "claim_id": "CL-0001",
                "atoms": [
                    {
                        "atom_id": "AC-0001-01",
                        "text": "TargetCo opened a demo facility.",
                        "claim_role": "observed_fact",
                        "materiality": "high",
                    }
                ],
            },
            {
                "claim_id": "CL-0002",
                "atoms": [
                    {
                        "atom_id": "AC-0002-01",
                        "text": "TargetCo may expand the program.",
                        "claim_role": "forward_looking_inference",
                        "materiality": "medium",
                    }
                ],
            },
        ],
    }


def test_atomic_reader_projection_absent_graph_is_non_blocking() -> None:
    projection = project_atomic_reader_text(
        graph_payload=None,
        target_text="TargetCo opened a demo facility [src:CL-0001].",
        target_artifact="output/intermediate/audited_brief.md",
    )

    assert projection["status"] == "not_available"
    assert projection["graph_present"] is False
    assert projection["atom_residue_findings"] == []


def test_atomic_reader_projection_reports_citation_coverage_without_residue() -> None:
    projection = project_atomic_reader_text(
        graph_payload=_graph(),
        target_text="TargetCo opened a demo facility [src:CL-0001].",
        target_artifact="output/intermediate/audited_brief.md",
    )

    assert projection["status"] == "pass"
    coverage = projection["claim_citation_coverage"]
    assert coverage["cited_graph_claim_ids"] == ["CL-0001"]
    assert coverage["uncited_graph_claim_ids"] == ["CL-0002"]
    assert coverage["uncited_high_materiality_claim_ids"] == []
    assert projection["summary_counts"]["graph_atom_count"] == 2


def test_atomic_reader_projection_reports_known_and_unknown_atom_id_residue() -> None:
    projection = project_atomic_reader_text(
        graph_payload=_graph(),
        target_text="Do not expose AC-0001-01 or AC-9999-01 to readers.",
        target_artifact="output/intermediate/audited_brief.md",
    )

    assert projection["status"] == "warning"
    assert [finding["finding_type"] for finding in projection["atom_residue_findings"]] == [
        "atom_id_residue",
        "unknown_atom_id_residue",
    ]
    assert projection["summary_counts"]["atom_residue_count"] == 1
    assert projection["summary_counts"]["unknown_atom_residue_count"] == 1


def test_atomic_reader_projection_reports_process_wording_residue() -> None:
    projection = project_atomic_reader_text(
        graph_payload=_graph(),
        target_text="The Atomic Claim Graph shows the decomposition.",
        target_artifact="output/intermediate/audited_brief.md",
    )

    assert projection["status"] == "warning"
    assert projection["atom_residue_findings"][0]["finding_type"] == "atomic_graph_process_residue"
    assert projection["summary_counts"]["process_residue_count"] == 1


def test_atomic_reader_projection_from_workspace_rejects_invalid_graph_without_blocking(tmp_path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": "TargetCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Evidence.",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "atomic_claim_graph.json").write_text(json.dumps({"claims": []}) + "\n", encoding="utf-8")

    projection = project_atomic_reader_text_from_workspace(
        workspace=ws,
        target_text="TargetCo opened a demo facility [src:CL-0001].",
        target_artifact="output/intermediate/audited_brief.md",
    )

    assert projection["status"] == "invalid_graph"
    assert projection["reason"] == "atomic_claim_graph_schema_error:schema_version"


def test_atomic_reader_projection_uses_explicit_contract_graph_path(tmp_path) -> None:
    ws = tmp_path / "ws"
    default_graph = ws / "output" / "intermediate" / "atomic_claim_graph.json"
    default_graph.parent.mkdir(parents=True)
    default_graph.write_text("{wrong-default-bytes\n", encoding="utf-8")
    default_ledger = ws / "output" / "intermediate" / "claim_ledger.json"
    default_ledger.write_text("{wrong-default-bytes\n", encoding="utf-8")
    custom_graph = ws / "custom" / "graph" / "atoms.json"
    custom_graph.parent.mkdir(parents=True)
    custom_graph.write_text(json.dumps(_graph()) + "\n", encoding="utf-8")
    custom_ledger = ws / "custom" / "ledger" / "claims.json"
    custom_ledger.parent.mkdir(parents=True)
    custom_ledger.write_text(
        json.dumps([{"claim_id": "CL-0001"}, {"claim_id": "CL-0002"}]) + "\n",
        encoding="utf-8",
    )

    projection = project_atomic_reader_text_from_workspace(
        workspace=ws,
        target_text="TargetCo opened a demo facility [src:CL-0001].",
        target_artifact="output/intermediate/audited_brief.md",
        artifact_paths={
            "atomic_claim_graph": Path(custom_graph),
            "claim_ledger": Path(custom_ledger),
        },
    )

    assert projection["status"] == "pass"
    assert projection["graph_present"] is True
    assert projection["summary_counts"]["graph_atom_count"] == 2


def test_atomic_reader_projection_rejects_missing_ledger_path_binding(tmp_path) -> None:
    ws = tmp_path / "ws"
    custom_graph = ws / "custom" / "graph" / "atoms.json"
    custom_graph.parent.mkdir(parents=True)
    custom_graph.write_text(json.dumps(_graph()) + "\n", encoding="utf-8")
    default_ledger = ws / "output" / "intermediate" / "claim_ledger.json"
    default_ledger.parent.mkdir(parents=True)
    default_ledger.write_text(
        json.dumps([{"claim_id": "CL-0001"}, {"claim_id": "CL-0002"}]) + "\n",
        encoding="utf-8",
    )

    projection = project_atomic_reader_text_from_workspace(
        workspace=ws,
        target_text="TargetCo opened a demo facility [src:CL-0001].",
        target_artifact="output/intermediate/audited_brief.md",
        artifact_paths={"atomic_claim_graph": Path(custom_graph)},
    )

    assert projection["status"] == "invalid_graph"
    assert projection["reason"] == (
        "atomic_claim_graph_validation_error:claim_ledger_path_binding_missing"
    )
