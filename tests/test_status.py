from __future__ import annotations

import json
import hashlib
from pathlib import Path

from multi_agent_brief.status import build_workspace_status, format_workspace_status


def _span_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_status_derives_atomic_reader_projection_without_writes(tmp_path: Path) -> None:
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
    (intermediate / "atomic_claim_graph.json").write_text(
        json.dumps(
            {
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
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "TargetCo opened a demo facility. AC-0001-01 [src:CL-0001]\n",
        encoding="utf-8",
    )

    status = build_workspace_status(ws)

    projection = status["atomic_reader_projection"]["audited_brief"]
    assert status["read_only"] is True
    assert projection["status"] == "warning"
    assert projection["summary_counts"]["atom_residue_count"] == 1
    assert projection["claim_citation_coverage"]["cited_graph_claim_ids"] == ["CL-0001"]
    assert not (intermediate / "quality_gate_report.json").exists()


def test_status_derives_claim_support_matrix_projection_without_writes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    intermediate.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    raw_excerpt = "TargetCo opened a demo facility."
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    (source_dir / "source-001.md").write_text(source_text, encoding="utf-8")
    start = source_text.index(raw_excerpt)
    (intermediate / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": raw_excerpt,
                    "source_id": "SRC-001",
                    "evidence_text": raw_excerpt,
                    "claim_type": "fact",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "atomic_claim_graph.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.atomic_claim_graph.v1",
                "claims": [
                    {
                        "claim_id": "CL-0001",
                        "atoms": [
                            {
                                "atom_id": "AC-0001-01",
                                "text": raw_excerpt,
                                "claim_role": "observed_fact",
                                "materiality": "high",
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "evidence_span_registry.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.evidence_span_registry.v1",
                "sources": [
                    {
                        "source_id": "SRC-001",
                        "source_type": "company_release",
                        "source_path": "input/sources/source-001.md",
                        "published_at": "2026-06-01",
                        "source_tier": "company_official",
                        "spans": [
                            {
                                "span_id": "ESP-001-01",
                                "raw_excerpt": raw_excerpt,
                                "hash": _span_hash(raw_excerpt),
                                "span_role": "direct_statement",
                                "char_start": start,
                                "char_end": start + len(raw_excerpt),
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "claim_support_matrix.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.claim_support_matrix.v1",
                "rows": [
                    {
                        "row_id": "CSM-0001",
                        "claim_id": "CL-0001",
                        "atom_id": "AC-0001-01",
                        "evidence_span_id": None,
                        "support_label": "unsupported",
                        "support_strength": "none",
                        "support_reason": "No span supports the high-materiality atom.",
                        "required_action": "block_release",
                        "repair_owner": "editor",
                        "decision_source": "human",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)

    projection = status["claim_support_matrix"]
    assert status["read_only"] is True
    assert projection["status"] == "valid"
    assert projection["summary_counts"]["blocking_atom_count"] == 1
    assert projection["atoms"][0]["verdict"] == "blocking"
    assert "[status] claim_support_matrix: valid blocking_atoms=1" in formatted
    assert not (intermediate / "quality_gate_report.json").exists()
    assert not (intermediate / "event_log.jsonl").exists()
