"""Tests for v0.6.1 Orchestrator runtime state."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import multi_agent_brief.orchestrator.runtime_state as runtime_state
import multi_agent_brief.orchestrator.runtime_state.event_log as runtime_event_log
from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import interpret_frozen_artifact_integrity
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    validate_semantic_assessment_report_against_artifacts,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RUNTIME_STATE_FILES,
    RuntimeStateError,
    check_runtime_state,
    complete_finalize_transaction,
    complete_repair_transaction,
    complete_stage_transaction,
    enrich_claim_metadata_transaction,
    freeze_claim_ledger_transaction,
    initialize_runtime_state,
    import_fact_layer_transaction,
    record_decision,
    show_runtime_state,
    start_repair_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import _allowed_decisions_for_stage
from multi_agent_brief.orchestrator.run_archive import archive_finalized_run
from multi_agent_brief.orchestrator.timing import derive_control_timing_from_path
from multi_agent_brief.outputs.finalize import finalize_reader_outputs
from multi_agent_brief.outputs.source_appendix import build_source_appendix


ROOT = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# User\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    return ws


def _write_auditable_condition_metadata(ws: Path) -> None:
    path = ws / "experiment" / "080" / "condition.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "case_id": "solar_public_001_auditable",
                "condition": "baseline",
                "assessment_target": "auditable_brief",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _repo_with_role_topology(
    tmp_path: Path,
    topology: str,
    *,
    enable_default_screener_satisfaction: bool = False,
) -> Path:
    repo = tmp_path / f"repo-{topology}"
    shutil.copytree(ROOT / "configs", repo / "configs")
    (repo / "pyproject.toml").write_text("[project]\nname = \"mabw-test\"\n", encoding="utf-8")
    (repo / "src" / "multi_agent_brief").mkdir(parents=True)
    policy_path = repo / "configs" / "policy_packs" / "default.yaml"
    import yaml

    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy.setdefault("policy", {})["role_topology"] = topology
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    if enable_default_screener_satisfaction:
        stage_specs_path = repo / "configs" / "stage_specs.yaml"
        stage_specs = yaml.safe_load(stage_specs_path.read_text(encoding="utf-8"))
        screener = next(
            stage
            for stage in stage_specs["workflow"]["stages"]
            if stage["stage_id"] == "screener"
        )
        screener.setdefault("topology_satisfaction", {})["default"] = {
            "satisfied_by": "scout",
            "required_artifacts": ["candidate_claims", "screened_candidates"],
        }
        stage_specs_path.write_text(yaml.safe_dump(stage_specs, sort_keys=False), encoding="utf-8")
    return repo


def _state_file(ws: Path, key: str) -> Path:
    return ws / RUNTIME_STATE_FILES[key]


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json_artifact(ws: Path, name: str, payload: str = "[]\n") -> None:
    (_intermediate(ws) / name).write_text(payload, encoding="utf-8")


def _write_input_classification(ws: Path, payload: dict) -> None:
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def _write_feedback_issue(ws: Path, *, stage_id: str, artifact_id: str, status: str = "open") -> None:
    out = _intermediate(ws)
    (out / "feedback_issues.json").write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-feedback-issues/v1",
                "created_at": "2026-06-15T00:00:00+00:00",
                "updated_at": "2026-06-15T00:00:00+00:00",
                "issues": [
                    {
                        "issue_id": "FB-TOPOLOGY-001",
                        "source": "human",
                        "status": status,
                        "severity": "blocking",
                        "stage_id": stage_id,
                        "artifact_id": artifact_id,
                        "category": "coverage_gap",
                        "feedback_excerpt": "Downstream stage requires repair before satisfaction.",
                        "raw_feedback_ref": "feedback.txt",
                        "source_artifact": "feedback.txt",
                        "metadata": {},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _valid_claim_ledger_payload(
    claim_id: str = "CL-001",
    statement: str = "ExampleCo opened a demo facility.",
    metadata: dict[str, object] | None = None,
) -> str:
    return json.dumps(
        [
            {
                "claim_id": claim_id,
                "statement": statement,
                "source_id": "SRC-001",
                "evidence_text": "Example evidence.",
                "metadata": metadata or {},
            }
        ]
    ) + "\n"


def _claim_ledger_payload(claims: list[dict[str, object]]) -> str:
    return json.dumps(claims) + "\n"


def _claim_dict(
    claim_id: str,
    *,
    claim_type: str = "fact",
    limitations: list[str] | None = None,
) -> dict[str, object]:
    claim: dict[str, object] = {
        "claim_id": claim_id,
        "statement": f"{claim_id} statement.",
        "source_id": f"SRC-{claim_id[-4:]}",
        "evidence_text": f"{claim_id} evidence.",
        "claim_type": claim_type,
    }
    if limitations is not None:
        claim["limitations"] = limitations
    return claim


def _valid_atomic_claim_graph_payload(claim_id: str = "CL-001", atom_id: str = "AC-0001-01") -> str:
    return json.dumps(
        {
            "schema_version": "mabw.atomic_claim_graph.v1",
            "claims": [
                {
                    "claim_id": claim_id,
                    "statement": "ExampleCo opened a demo facility.",
                    "atoms": [
                        {
                            "atom_id": atom_id,
                            "text": "ExampleCo opened a demo facility.",
                            "claim_role": "observed_fact",
                            "materiality": "high",
                        }
                    ],
                    "edges": [],
                }
            ],
            "metadata": {},
        }
    ) + "\n"


def _span_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_evidence_span_registry_payload() -> str:
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    return json.dumps(
        {
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": "SRC-001",
                    "source_type": "company_release",
                    "url": "https://example.com/release",
                    "published_at": "2026-06-10",
                    "source_tier": "company_official",
                    "spans": [
                        {
                            "span_id": "ESP-001-01",
                            "raw_excerpt": raw_excerpt,
                            "hash": _span_hash(raw_excerpt),
                            "span_role": "numeric_observation",
                        }
                    ],
                }
            ],
        }
    ) + "\n"


def _valid_claim_support_matrix_payload() -> str:
    return json.dumps(
        {
            "schema_version": "mabw.claim_support_matrix.v1",
            "rows": [
                {
                    "row_id": "CSM-0001",
                    "claim_id": "CL-0001",
                    "atom_id": "AC-0001-01",
                    "evidence_span_id": "ESP-001-01",
                    "support_label": "partial_support",
                    "support_strength": "medium",
                    "support_reason": "The span supports activity but not acceleration wording.",
                    "required_action": "downgrade_wording",
                    "repair_owner": "analyst",
                    "decision_source": "human",
                }
            ],
        }
    ) + "\n"


def _valid_semantic_assessment_report_payload() -> str:
    return json.dumps(
        {
            "schema_version": "mabw.semantic_assessment_report.v1",
            "assessors": [
                {
                    "assessor_id": "ASR-001",
                    "assessment_method": "llm_assisted_human",
                    "label": "Reviewer A",
                }
            ],
            "rows": [
                {
                    "row_id": "SAR-0001",
                    "claim_id": "CL-0001",
                    "atom_id": "AC-0001-01",
                    "evidence_span_id": "ESP-001-01",
                    "proposed_support_label": "partial_support",
                    "confidence": 0.72,
                    "uncertainty": "medium",
                    "disagreement": "none",
                    "requires_human_adjudication": True,
                    "assessment_method": "llm_assisted_human",
                    "assessor_id": "ASR-001",
                    "rationale": "The span supports activity but not acceleration wording.",
                }
            ],
        }
    ) + "\n"


def _write_valid_claim_support_matrix_dependencies(ws: Path) -> None:
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-0001", "AC-0001-01"))
    source_text = "Intro.\nExampleCo said module shipments reached 12 MW in Q2.\nOutro.\n"
    _write_source_text(ws, text=source_text)
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(include_offsets=True, source_text=source_text),
    )


def _source_backed_evidence_span_registry_payload(
    *,
    source_path: str = "input/sources/source-001.md",
    source_id: str = "SRC-001",
    raw_excerpt: str = "ExampleCo said module shipments reached 12 MW in Q2.",
    include_offsets: bool = False,
    source_text: str | None = None,
) -> str:
    span = {
        "span_id": "ESP-001-01",
        "raw_excerpt": raw_excerpt,
        "hash": _span_hash(raw_excerpt),
        "span_role": "numeric_observation",
    }
    if include_offsets:
        text = source_text or f"Intro.\n{raw_excerpt}\nOutro.\n"
        start = text.index(raw_excerpt)
        span["char_start"] = start
        span["char_end"] = start + len(raw_excerpt)
    return json.dumps(
        {
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": source_id,
                    "source_type": "company_release",
                    "source_path": source_path,
                    "published_at": "2026-06-10",
                    "source_tier": "company_official",
                    "spans": [span],
                }
            ],
        }
    ) + "\n"


def _write_source_text(
    ws: Path,
    *,
    name: str = "source-001.md",
    text: str = "Intro.\nExampleCo said module shipments reached 12 MW in Q2.\nOutro.\n",
) -> Path:
    source_path = ws / "input" / "sources" / name
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(text, encoding="utf-8")
    return source_path


def _atomic_claim_graph_payload(claim_roles: dict[str, list[str]]) -> str:
    claims = []
    for claim_id, roles in claim_roles.items():
        claim_number = claim_id.rsplit("-", 1)[-1]
        claims.append(
            {
                "claim_id": claim_id,
                "atoms": [
                    {
                        "atom_id": f"AC-{claim_number}-{idx:02d}",
                        "text": f"{claim_id} atom {idx}.",
                        "claim_role": role,
                        "materiality": "high",
                    }
                    for idx, role in enumerate(roles, start=1)
                ],
                "edges": [],
            }
        )
    return json.dumps({"schema_version": "mabw.atomic_claim_graph.v1", "claims": claims}) + "\n"


def _cross_claim_edge_atomic_claim_graph_payload() -> str:
    return json.dumps(
        {
            "schema_version": "mabw.atomic_claim_graph.v1",
            "claims": [
                {
                    "claim_id": "CL-0001",
                    "atoms": [
                        {
                            "atom_id": "AC-0001-01",
                            "text": "ExampleCo opened a demo facility.",
                            "claim_role": "observed_fact",
                            "materiality": "high",
                        }
                    ],
                    "edges": [
                        {
                            "from": "AC-0001-01",
                            "to": "AC-0002-01",
                            "relation": "cross_claim_reference",
                        }
                    ],
                },
                {
                    "claim_id": "CL-0002",
                    "atoms": [
                        {
                            "atom_id": "AC-0002-01",
                            "text": "BetaCo expanded module output.",
                            "claim_role": "observed_fact",
                            "materiality": "medium",
                        }
                    ],
                    "edges": [],
                },
            ],
        }
    ) + "\n"


def _valid_claim_drafts_payload(*, duplicate: bool = False) -> str:
    drafts = [
        {
            "statement": "ExampleCo opened a demo facility.",
            "source_id": "SRC-001",
            "evidence_text": "Example evidence.",
            "source_title": "ExampleCo Demo Facility",
            "source_category": "news_media",
            "claim_type": "fact",
            "confidence": "medium",
        },
        {
            "statement": "BetaCo expanded module output.",
            "source_id": "SRC-002",
            "evidence_text": "Second example evidence.",
            "source_title": "BetaCo source",
            "source_category": "news_media",
            "claim_type": "fact",
            "confidence": "medium",
        },
    ]
    if duplicate:
        drafts.append(dict(drafts[0]))
    return json.dumps({"schema_version": "mabw.claim_drafts.v1", "drafts": drafts}) + "\n"


def _valid_candidate_claims_payload() -> str:
    return json.dumps(
        [
            {
                "candidate_id": "CAND-001",
                "claim": "ExampleCo opened a demo facility.",
                "source_id": "SRC-001",
            }
        ]
    ) + "\n"


def _valid_screened_candidates_payload(*, status: str = "selected", reason: str | None = None) -> str:
    candidate = {
        "candidate_id": "CAND-001",
        "screening_status": status,
    }
    if reason is not None:
        candidate["screening_reason"] = reason
    return json.dumps([candidate]) + "\n"


def _freeze_claim_ledger_fixture(ws: Path, *, duplicate: bool = False) -> None:
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload(duplicate=duplicate))
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)


def _add_imported_source_authority(
    ws: Path,
    *,
    source_id: str = "SRC-001",
    filename: str = "source-001.json",
    metadata: dict[str, object] | None = None,
) -> None:
    source_path = ws / "input" / "sources" / filename
    source_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_id": source_id,
        "content": "Example evidence.",
        **(metadata or {}),
    }
    source_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = _state_file(ws, "runtime_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import_record = manifest.setdefault("fact_layer_import", {
        "schema_version": "mabw.fact_layer_import.v1",
        "imported_at": "2026-06-18T00:00:00+00:00",
        "source_run_id": "mabw-source-run",
        "source_archive_manifest": "output/runs/mabw-source-run/manifest.json",
        "source_archive_manifest_sha256": "0" * 64,
        "fact_layer_status": "complete",
        "fact_layer_sha256": "1" * 64,
        "satisfied_stage_ids": ["doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger"],
        "required_artifact_ids": [
            "durable_source_evidence_or_source_pack",
            "input_classification",
            "candidate_claims",
            "screened_candidates",
            "claim_ledger",
        ],
        "imported_file_count": 0,
        "imported_files": [],
    })
    import_record.setdefault("imported_files", []).append({
        "artifact_id": "durable_source_evidence_or_source_pack",
        "archive_path": f"fact_layer/input/sources/{filename}",
        "workspace_path": f"input/sources/{filename}",
        "sha256": _sha256_file(source_path),
        "size_bytes": source_path.stat().st_size,
    })
    import_record["imported_file_count"] = len(import_record["imported_files"])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _valid_audit_report_payload() -> str:
    return json.dumps(
        {
            "audit_status": "pass",
            "audit_score": 100,
            "passed": True,
            "recommendation": "approve",
            "findings": [],
        }
    ) + "\n"


def _event_records(ws: Path) -> list[dict]:
    path = _state_file(ws, "event_log")
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _fail_appending_event_type(monkeypatch: pytest.MonkeyPatch, event_type: str) -> None:
    original = runtime_event_log._append_jsonl

    def flaky_append(path: Path, payload: dict) -> None:
        if payload.get("event_type") == event_type:
            raise RuntimeStateError("forced event append failure")
        original(path, payload)

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", flaky_append)


def _write_quality_gate_report(
    ws: Path,
    *,
    status: str = "pass",
    blocking: bool = False,
    stage_id: str = "auditor",
    legacy_only: bool = False,
) -> None:
    gate_artifact_id = "finalize_quality_gate_report" if stage_id == "finalize" else "auditor_quality_gate_report"
    brief_ref = "output/brief.md" if stage_id == "finalize" else "output/intermediate/audited_brief.md"
    findings = []
    if blocking:
        findings.append({
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_relevance_failed",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "artifact_id": gate_artifact_id,
            "gate_artifact_id": gate_artifact_id,
            "repair_stage_id": stage_id,
            "repair_artifact_id": "audited_brief",
            "repair_owner": "orchestrator",
            "message": "Synthetic blocking finding.",
            "metadata": {},
        })
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "created_at": "2026-06-11T00:00:00+00:00",
        "updated_at": "2026-06-11T00:00:00+00:00",
        "workspace": ".",
        "report_date": "2026-06-11",
        "policy_pack": "default",
        "status": status,
        "gate_results": [
            {
                "gate_id": "coverage_omission",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "freshness",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "material_fact",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "target_relevance",
                "status": "fail" if blocking else status,
                "blocking": blocking,
                "finding_ids": [item["finding_id"] for item in findings],
            },
        ],
        "findings": findings,
        "metadata": {
            "brief": brief_ref,
            "ledger": "output/intermediate/claim_ledger.json",
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "gate_artifact_id": gate_artifact_id,
        },
    }
    payload_text = json.dumps(payload)
    if not legacy_only:
        report_path = (
            _intermediate(ws)
            / "gates"
            / ("finalize_quality_gate_report.json" if stage_id == "finalize" else "auditor_quality_gate_report.json")
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload_text, encoding="utf-8")
    (_intermediate(ws) / "quality_gate_report.json").write_text(payload_text, encoding="utf-8")


def _write_editor_repair_gate_report(ws: Path) -> None:
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "created_at": "2026-06-11T00:00:00+00:00",
        "updated_at": "2026-06-11T00:00:00+00:00",
        "workspace": ".",
        "report_date": "2026-06-11",
        "policy_pack": "default",
        "status": "fail",
        "gate_results": [
            {
                "gate_id": "material_fact",
                "status": "fail",
                "blocking": True,
                "finding_ids": ["QG_EDITOR_REPAIR_001"],
            }
        ],
        "findings": [
            {
                "finding_id": "QG_EDITOR_REPAIR_001",
                "finding_type": "unsupported_claim",
                "severity": "high",
                "blocking": True,
                "artifact_id": "audited_brief",
                "repair_owner": "editor",
                "repair_stage_id": "editor",
                "repair_artifact_id": "audited_brief",
                "message": "Audited brief needs an owner-stage editor repair.",
            }
        ],
        "metadata": {
            "brief": "output/intermediate/audited_brief.md",
            "ledger": "output/intermediate/claim_ledger.json",
            "stage_id": "auditor",
            "gate_stage_id": "auditor",
            "gate_artifact_id": "auditor_quality_gate_report",
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    (_intermediate(ws) / "quality_gate_report.json").write_text(text, encoding="utf-8")


def _start_active_editor_repair(tmp_path: Path) -> Path:
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    return ws


def _write_finalize_report(
    ws: Path,
    *,
    status: str = "pass",
    reader_clean_status: str = "pass",
) -> None:
    output = ws / "output"
    brief_text = "# Reader Brief\n\nClean reader text.\n"
    (output / "brief.md").write_text(brief_text, encoding="utf-8")
    delivery_dir = output / "delivery"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    delivery_brief = delivery_dir / "brief.md"
    delivery_brief.write_text(brief_text, encoding="utf-8")
    (_intermediate(ws) / "finalize_report.json").write_text(
        json.dumps({
            "status": status,
            "audited_brief": str(_intermediate(ws) / "audited_brief.md"),
            "reader_brief": str(output / "brief.md"),
            "named_reader_brief": "",
            "reader_docx": "",
            "named_reader_docx": "",
            "source_appendix": "",
            "delivery_markdown": str(delivery_brief),
            "delivery_docx": "",
            "delivery_artifacts": [str(delivery_brief)],
            "delivery_artifact_sha256": {
                str(delivery_brief): _sha256_file(delivery_brief),
            },
            "audit_binding": {
                "status": "pass",
                "claim_ledger_sha256": _sha256_file(
                    _intermediate(ws) / "claim_ledger.json"
                ),
                "audited_brief_sha256": _sha256_file(
                    _intermediate(ws) / "audited_brief.md"
                ),
                "audit_report_sha256": _sha256_file(
                    _intermediate(ws) / "audit_report.json"
                ),
                "ledger_claim_count": 1,
                "audited_brief_cited_claim_count": 0,
                "findings": [],
                "warnings": [],
            },
            "reader_clean": {
                "status": reader_clean_status,
                "src_marker_count": 0,
                "bare_claim_id_count": 0,
                "source_id_count": 0,
                "process_wording_count": 0,
                "blank_citation_row_count": 0,
                "local_path_count": 0,
                "debug_residue_count": 0,
                "sample_findings": [],
            },
        }),
        encoding="utf-8",
    )


def _write_fact_layer_inputs(ws: Path) -> None:
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source-001.md").write_text(
        "# Source 001\n\nExample evidence for the archived fact layer.\n",
        encoding="utf-8",
    )
    (source_dir / "README.md").write_text(
        "This README is not source evidence.\n",
        encoding="utf-8",
    )
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps({
            "evidence": [{"path": "input/sources/source-001.md", "name": "source-001.md"}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        }),
        encoding="utf-8",
    )


def _write_archivable_evidence_span_registry(ws: Path) -> str:
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    _write_fact_layer_inputs(ws)
    (ws / "input" / "sources" / "source-001.md").write_text(source_text, encoding="utf-8")
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(
            raw_excerpt=raw_excerpt,
            include_offsets=True,
            source_text=source_text,
        ),
    )
    return raw_excerpt


def _set_current_stage(ws: Path, stage_id: str) -> None:
    stages = runtime_state.load_stage_specs(ROOT)
    stage_ids = [str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")]
    assert stage_id in stage_ids
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    now = runtime_state.utc_now()
    statuses = {}
    for item in stage_ids:
        if stage_ids.index(item) < stage_ids.index(stage_id):
            statuses[item] = {"status": "complete", "reason": f"{item} fixture complete", "updated_at": now}
        elif item == stage_id:
            statuses[item] = {"status": "ready", "reason": "", "updated_at": now}
        else:
            statuses[item] = {"status": "pending", "reason": "", "updated_at": now}
    workflow["updated_at"] = now
    workflow["current_stage"] = stage_id
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["stage_statuses"] = statuses
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, stage_id)
    _state_file(ws, "workflow_state").write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _advance_to_finalize(ws: Path) -> None:
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    (_intermediate(ws) / "analyst_draft_snapshot.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(ws, "finalize")


def _advance_to_auditor(ws: Path) -> None:
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    (_intermediate(ws) / "analyst_draft_snapshot.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(ws, "auditor")


def _complete_finalized_workspace(ws: Path) -> dict:
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    return complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )


def _complete_finalized_workspace_with_claim_metadata(ws: Path, metadata: dict[str, object]) -> dict:
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    (_intermediate(ws) / "claim_ledger.json").write_text(
        _valid_claim_ledger_payload(metadata=metadata),
        encoding="utf-8",
    )
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    return complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )


def test_state_init_creates_runtime_control_files_without_old_run_manifest(tmp_path):
    ws = _write_workspace(tmp_path)

    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["ok"] is True
    assert _state_file(ws, "runtime_manifest").exists()
    assert _state_file(ws, "workflow_state").exists()
    assert _state_file(ws, "event_log").exists()
    assert not (ws / "output" / "intermediate" / "run_manifest.json").exists()

    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "multi-agent-brief-runtime-manifest/v1"
    assert manifest["workspace"] == "."
    assert manifest["runtime_state_files"] == RUNTIME_STATE_FILES
    assert manifest["stage_order"][0] == "doctor"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"] == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }


def test_state_check_fresh_workspace_is_not_globally_blocked(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    registry = state["artifact_registry"]["artifacts"]

    assert workflow["blocked"] is False
    assert workflow["current_stage"] == "doctor"
    assert workflow["stage_statuses"]["doctor"]["status"] == "ready"
    assert workflow["stage_statuses"]["claim-ledger"]["status"] == "pending"
    assert registry["claim_ledger"]["status"] == "expected"
    assert registry["atomic_claim_graph"]["status"] == "expected"
    assert registry["atomic_claim_graph"]["required"] is False
    assert registry["atomic_claim_graph"]["validation_result"] == "not_checked"
    assert registry["evidence_span_registry"]["status"] == "expected"
    assert registry["evidence_span_registry"]["required"] is False
    assert registry["evidence_span_registry"]["validation_result"] == "not_checked"
    assert registry["claim_support_matrix"]["status"] == "expected"
    assert registry["claim_support_matrix"]["required"] is False
    assert registry["claim_support_matrix"]["validation_result"] == "not_checked"
    assert registry["semantic_assessment_report"]["status"] == "expected"
    assert registry["semantic_assessment_report"]["required"] is False
    assert registry["semantic_assessment_report"]["validation_result"] == "not_checked"
    assert registry["audited_brief"]["status"] == "expected"
    assert registry["reader_brief"]["status"] == "expected"
    assert registry["auditor_quality_gate_report"]["status"] == "expected"
    assert registry["finalize_quality_gate_report"]["status"] == "expected"
    assert registry["auditor_quality_gate_report"]["validation_result"] == "not_checked"
    assert registry["finalize_quality_gate_report"]["validation_result"] == "not_checked"


def test_state_check_rejects_malformed_run_integrity_without_rewrite(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = "bad"
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = workflow_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert workflow_path.read_bytes() == before


def test_state_show_rejects_invalid_run_integrity_status_without_rewrite(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "unknown",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = workflow_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        show_runtime_state(workspace=ws)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert workflow_path.read_bytes() == before


def test_stage_complete_rejects_invalid_run_integrity_status_without_rewrite(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "unknown",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = workflow_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor complete",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert workflow_path.read_bytes() == before


def test_state_check_rejects_invalid_stage_status_without_rewrite(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["stage_statuses"]["doctor"]["status"] = "finished"
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = workflow_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "stage_statuses" in str(excinfo.value.details)
    assert workflow_path.read_bytes() == before


def test_state_check_strict_fresh_workspace_returns_zero(tmp_path):
    ws = _write_workspace(tmp_path)

    rc = main([
        "state",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 0


def test_state_decide_continue_requires_completion_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            decision="continue",
            reason="auditor complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert excinfo.value.details["required_command"] == "stage-complete"
    assert after == before


def test_auditor_stage_complete_records_python_owned_audit_binding(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, stage_id="auditor")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor report passed",
    )

    workflow = state["workflow_state"]
    binding = workflow["stage_statuses"]["auditor"]["metadata"]["audit_binding"]
    assert binding["schema_version"] == "mabw.auditable_audit_binding.v1"
    assert binding["source"] == "auditor_stage_complete"
    assert binding["claim_ledger_sha256"] == _sha256_file(_intermediate(ws) / "claim_ledger.json")
    assert binding["audited_brief_sha256"] == _sha256_file(_intermediate(ws) / "audited_brief.md")
    assert binding["audit_report_sha256"] == _sha256_file(_intermediate(ws) / "audit_report.json")
    assert binding["relevant_repair_transaction_ids"] == []
    assert binding["auditor_stage_transaction_id"] == state["transaction"]["transaction_id"]
    assert binding["stage_completion_event"]["event_type"] == "decision_recorded"
    assert binding["stage_completion_event"]["availability"] == "not_available_until_event_append"


def test_state_decide_finalize_requires_completion_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="finalize",
            decision="finalize",
            reason="finalize complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert excinfo.value.details["required_command"] == "finalize-complete"
    assert after == before


def test_invalid_optional_expected_artifact_rejects_continue(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")
    (ws / "source_candidates.yaml").write_text(": [", encoding="utf-8")

    with pytest.raises(RuntimeStateError, match="Optional expected artifact 'source_candidates'"):
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )


def test_source_discovery_runtime_tool_without_sources_or_candidates_rejects_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "runtime_tool web search is enabled" in str(excinfo.value)


def test_source_discovery_runtime_tool_allows_local_source(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "input" / "source.md").write_text("source text\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "    - web_search\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Local Source\n"
        "      path: input/source.md\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_configure_later_with_plan_only_rejects_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: configure_later\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_plan_only\n"
        "evidence_status: not_evidence\n"
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "source plan, not evidence" in str(excinfo.value)
    assert "configure_later" in str(excinfo.value)


def test_source_discovery_configure_later_allows_real_input_source(tmp_path):
    ws = _write_workspace(tmp_path)
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "example.md").write_text(
        "Title: Example Source\nURL: https://example.com/source\nRetrieved: 2026-06-13\nExcerpt: Example evidence.\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: configure_later\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_runtime_tool_rejects_source_candidates_plan_only(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n"
        "    category: industry_media\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "source_candidates.yaml is a source plan, not evidence" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_input_readme_only(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "input" / "README.md").write_text("Put source files here.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source is available" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_input_sources_readme_only(tmp_path):
    ws = _write_workspace(tmp_path)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "README.md").write_text("Put source files here.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source is available" in str(excinfo.value)


def test_source_discovery_runtime_tool_allows_input_sources_file(tmp_path):
    ws = _write_workspace(tmp_path)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "example.md").write_text(
        "Title: Example Source\nURL: https://example.com/source\nRetrieved: 2026-06-13\nExcerpt: Example evidence.\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_source_plan_only_allows_real_source_file(tmp_path):
    ws = _write_workspace(tmp_path)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "example.md").write_text(
        "Title: Example Source\nURL: https://example.com/source\nRetrieved: 2026-06-13\nExcerpt: Example evidence.\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_plan_only\n"
        "evidence_status: not_evidence\n"
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        reason="source discovery complete",
    )

    assert state["workflow_state"]["current_stage"] == "input-governance"


def test_source_discovery_runtime_tool_rejects_context_only_input(tmp_path):
    ws = _write_workspace(tmp_path)
    context_dir = ws / "input" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "style.md").write_text("Use concise prose.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "    - web_search\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Context Only\n"
        "      path: input/context/style.md\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_zero_observation_fixture(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "metadata:\n"
        "  runtime_search_observation:\n"
        "    message: Did 0 searches\n"
        "recommended_sources: []\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "zero searches" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_positive_observation_without_durable_source(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "metadata:\n"
        "  runtime_search_observations:\n"
        "    - query: useful query\n"
        "      result_count: 2\n"
        "recommended_sources: []\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "no evidence source is available" in str(excinfo.value)
    assert "input/sources/" in str(excinfo.value)


def test_source_discovery_runtime_tool_rejects_candidates_urls_even_with_positive_observation(tmp_path):
    ws = _write_workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (ws / "source_candidates.yaml").write_text(
        "metadata:\n"
        "  runtime_search_observations:\n"
        "    - query: empty query\n"
        "      result_count: 0\n"
        "    - query: useful query\n"
        "      result_count: 2\n"
        "recommended_sources:\n"
        "  - name: Example Source\n"
        "    url: https://example.com/source\n"
        "    category: industry_media\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            reason="source discovery complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "source_candidates.yaml is a source plan, not evidence" in str(excinfo.value)


def test_optional_feedback_artifacts_do_not_become_missing_after_auditor_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    registry = state["artifact_registry"]["artifacts"]

    assert registry["feedback_issues"]["status"] == "expected"
    assert registry["repair_plan"]["status"] == "expected"
    assert registry["delta_audit_report"]["status"] == "expected"
    assert registry["auditor_quality_gate_report"]["status"] == "expected"
    assert registry["finalize_quality_gate_report"]["status"] == "expected"
    assert registry["feedback_issues"]["validation_result"] == "not_checked"
    assert registry["repair_plan"]["validation_result"] == "not_checked"
    assert registry["delta_audit_report"]["validation_result"] == "not_checked"
    assert registry["auditor_quality_gate_report"]["validation_result"] == "not_checked"
    assert registry["finalize_quality_gate_report"]["validation_result"] == "not_checked"


def test_delta_audit_report_missing_only_when_repair_active(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    out = _intermediate(ws)
    (out / "feedback_issues.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-feedback-issues/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "issues": [
                {
                    "issue_id": "fb_active",
                    "source": "human",
                    "severity": "blocking",
                    "stage_id": "auditor",
                    "artifact_id": "audit_report",
                    "category": "unsupported_claim",
                    "summary": "Repair requires delta audit.",
                    "feedback_excerpt": "Repair requires delta audit.",
                    "raw_feedback_ref": "feedback.txt",
                    "source_artifact": "feedback.txt",
                    "supporting_context": [],
                    "metadata": {},
                    "status": "in_progress",
                    "created_at": "2026-06-08T00:00:00+00:00",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "fingerprint": "active",
                }
            ],
        }),
        encoding="utf-8",
    )
    (out / "repair_plan.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-repair-plan/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "repair_plans": [
                {
                    "repair_plan_id": "rp_active",
                    "created_at": "2026-06-08T00:00:00+00:00",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "target_stage": "auditor",
                    "target_artifacts": ["audit_report"],
                    "issue_ids": ["fb_active"],
                    "allowed_decision": "delegate_repair",
                    "repair_scope": "minimal",
                    "instructions": ["Run delta audit."],
                    "requires_human_review": False,
                    "status": "in_progress",
                    "fingerprint": "active",
                }
            ],
        }),
        encoding="utf-8",
    )
    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["artifact_registry"]["artifacts"]["delta_audit_report"]["status"] == "missing"


def test_invalid_current_stage_output_blocks_only_that_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    output = ws / "output" / "intermediate"
    output.mkdir(parents=True)
    (output / "candidate_claims.json").write_text("{broken", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    registry = state["artifact_registry"]["artifacts"]

    assert registry["candidate_claims"]["status"] == "invalid"
    assert registry["candidate_claims"]["validation_result"] == "parse_error"
    assert workflow["current_stage"] == "scout"
    assert workflow["stage_statuses"]["scout"]["status"] == "blocked"
    assert workflow["stage_statuses"]["claim-ledger"]["status"] == "pending"


def test_state_check_validates_candidate_screened_and_input_classification_shapes(tmp_path):
    ws = _write_workspace(tmp_path)
    source_path = ws / "input" / "sources" / "source-001.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Source\n\nEvidence.\n", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "candidate_claims.json", _valid_candidate_claims_payload())
    _write_json_artifact(ws, "screened_candidates.json", _valid_screened_candidates_payload())
    _write_input_classification(
        ws,
        {
            "evidence": [{"path": str(source_path), "name": "source-001.md"}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        },
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    registry = state["artifact_registry"]["artifacts"]

    assert registry["candidate_claims"]["status"] == "valid"
    assert registry["candidate_claims"]["validation_result"] == "valid_candidate_claims_schema"
    assert registry["screened_candidates"]["status"] == "valid"
    assert registry["screened_candidates"]["validation_result"] == "valid_screened_candidates_schema"
    assert registry["input_classification"]["status"] == "valid"
    assert registry["input_classification"]["validation_result"] == "valid_input_classification_schema"


def test_state_check_accepts_contract_shaped_candidate_claims(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "evidence_text": "ExampleCo opened a demo facility in June.",
                    "source_url": "https://example.com/source",
                    "published_at": "2026-06-01",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "source_id": "SRC-001",
                    "source_path": "input/sources/source-001.md",
                }
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_candidate_claims_schema"


def test_state_check_accepts_contract_candidate_with_claim_alias(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "claim": "ExampleCo opened a demo facility.",
                    "evidence_text": "ExampleCo opened a demo facility in June.",
                    "source_url": "https://example.com/source",
                    "retrieved_at": "2026-06-02T00:00:00Z",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_candidate_claims_schema"


def test_state_check_accepts_contract_candidate_with_source_path_only(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "evidence_text": "ExampleCo opened a demo facility in June.",
                    "source_path": "input/sources/source-001.md",
                    "published_at": "2026-06-01",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_candidate_claims_schema"


def test_state_check_accepts_local_file_candidate_with_source_category(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "evidence_text": "ExampleCo opened a demo facility in June.",
                    "source_type": "local_file",
                    "source_path": "input/sources/source-001.md",
                    "source_title": "Example clinical paper",
                    "source_category": "peer_reviewed_paper",
                    "published_at": "2026-06-01",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_candidate_claims_schema"


def test_state_check_rejects_contract_candidate_plain_text_source_url(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "evidence_text": "ExampleCo opened a demo facility in June.",
                    "source_url": "GEN Top 10 organoid companies",
                    "published_at": "2026-06-01",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "candidate_claims_schema_error:candidate[0].source_url"


def test_state_check_rejects_contract_candidate_without_source_identity(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "evidence_text": "ExampleCo opened a demo facility in June.",
                    "published_at": "2026-06-01",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": "medium",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "invalid"
    assert (
        record["validation_result"]
        == "candidate_claims_schema_error:candidate[0].source_url_or_source_path"
    )


def test_state_check_rejects_contract_candidate_without_source_date(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "evidence_text": "ExampleCo opened a demo facility in June.",
                    "source_url": "https://example.com/source",
                    "topic": "demo market",
                    "claim_type": "fact",
                    "confidence": "medium",
                }
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "invalid"
    assert (
        record["validation_result"]
        == "candidate_claims_schema_error:candidate[0].published_at_or_retrieved_at"
    )


def test_state_check_marks_candidate_claims_missing_required_field_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps([{"candidate_id": "CAND-001", "source_id": "SRC-001"}]) + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "candidate_claims_schema_error:candidate[0].claim"


def test_state_check_marks_duplicate_candidate_claim_ids_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "A claim.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-001", "claim": "Another claim.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["candidate_claims"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "candidate_claims_schema_error:duplicate_candidate_id:CAND-001"


def test_state_check_accepts_object_shaped_screened_candidates(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "statement": "An older duplicate item.",
                        "reason": "duplicate",
                        "reason_code": "duplicate_source",
                        "explanation": "Dropped because it duplicated a fresher selected candidate.",
                    }
                ],
                "deprioritized": [],
                "screening_policy": {"max_items": 8, "freshness_window_days": 90},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_screened_candidates_schema"


def test_state_check_accepts_legacy_object_screened_candidates_reason_only(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "statement": "An older duplicate item.",
                        "reason": "duplicate",
                    }
                ],
                "screening_policy": {"max_items": 8, "freshness_window_days": 90},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_screened_candidates_schema"


def test_state_check_accepts_object_screened_candidates_with_source_url_identity(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_url": "https://example.com/source",
                        "published_at": "2026-06-01",
                        "topic": "market",
                        "claim_type": "fact",
                        "confidence": "high",
                    }
                ],
                "excluded": [
                    {
                        "statement": "An older duplicate item.",
                        "reason": "duplicate",
                        "reason_code": "duplicate_source",
                        "explanation": "Dropped because it duplicated a fresher selected candidate.",
                    }
                ],
                "screening_policy": {"max_items": 8, "freshness_window_days": 90},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_screened_candidates_schema"


def test_state_check_accepts_object_screened_candidates_local_file_category(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_type": "local_file",
                        "source_path": "input/sources/source-001.md",
                        "source_title": "Example clinical paper",
                        "source_category": "peer_reviewed_paper",
                        "published_at": "2026-06-01",
                        "topic": "market",
                        "claim_type": "fact",
                        "confidence": "high",
                    }
                ],
                "excluded": [
                    {
                        "statement": "An older duplicate item.",
                        "reason": "duplicate",
                        "reason_code": "duplicate_source",
                        "explanation": "Dropped because it duplicated a fresher selected candidate.",
                    }
                ],
                "screening_policy": {"max_items": 8, "freshness_window_days": 90},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_screened_candidates_schema"


def test_state_check_rejects_object_screened_candidates_plain_text_source_url(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_url": "GEN Top 10 organoid companies",
                        "published_at": "2026-06-01",
                        "topic": "market",
                        "claim_type": "fact",
                        "confidence": "high",
                    }
                ],
                "excluded": [
                    {
                        "statement": "An older duplicate item.",
                        "reason": "duplicate",
                        "reason_code": "duplicate_source",
                        "explanation": "Dropped because it duplicated a fresher selected candidate.",
                    }
                ],
                "screening_policy": {"max_items": 8, "freshness_window_days": 90},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:selected[0].source_url"


def test_state_check_rejects_object_screened_candidates_without_selected_evidence(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [{"statement": "ExampleCo opened a demo facility."}],
                "excluded": [],
                "screening_policy": {"max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:selected[0].evidence_text"


def test_state_check_rejects_object_screened_candidates_missing_reason(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [{"statement": "An older duplicate item."}],
                "screening_policy": {"max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:excluded[0].reason"


def test_state_check_rejects_screened_candidates_missing_discard_audit(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:discard_audit_missing"


def test_state_check_accepts_screened_candidates_reason_code_without_legacy_reason(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "reason_code": "duplicate_source",
                        "explanation": "Dropped because it duplicates a stronger selected candidate.",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_screened_candidates_schema"


def test_state_check_rejects_screened_candidates_discard_count_mismatch(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "reason": "stale",
                        "reason_code": "stale_source",
                        "explanation": "Dropped because the source is outside the configured window.",
                    }
                ],
                "screening_policy": {"total_candidates": 3, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:discard_audit_count"


def test_state_check_rejects_screened_candidates_unknown_discard_reason_code(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "reason": "unclear",
                        "reason_code": "vibes",
                        "explanation": "Dropped without a stable machine-readable reason.",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:excluded[0].reason_code"


def test_state_check_rejects_screened_candidates_missing_discard_explanation(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "reason": "stale_source",
                        "reason_code": "stale_source",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:excluded[0].explanation"


def test_state_check_accepts_screened_candidates_complete_discard_audit(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because the section capacity was already filled.",
                    },
                    {
                        "candidate_id": "CAND-003",
                        "reason": "unsafe_evidence_boundary",
                        "reason_code": "unsafe_evidence_boundary",
                        "explanation": "Dropped because the source summary was discovery material, not evidence.",
                    },
                ],
                "deprioritized": [
                    {
                        "candidate_id": "CAND-004",
                        "reason": "weak_relevance",
                        "reason_code": "weak_relevance",
                        "explanation": "Kept out because it was weakly connected to the brief objective.",
                    }
                ],
                "screening_policy": {"total_candidates": 4, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_screened_candidates_schema"


def test_state_check_rejects_screened_candidates_total_below_candidate_universe(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [],
                "screening_policy": {"total_candidates": 1, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:candidate_universe_count_mismatch"


def test_state_check_rejects_screened_candidates_unknown_discard_id(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-999",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because section capacity was already filled.",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert (
        record["validation_result"]
        == "screened_candidates_schema_error:excluded[0].unknown_candidate_id:CAND-999"
    )


def test_state_check_rejects_screened_candidates_duplicate_screened_id(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-001",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because section capacity was already filled.",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:duplicate_screened_candidate_id:CAND-001"


def test_state_check_rejects_screened_candidates_missing_id_against_stable_universe(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-001",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because section capacity was already filled.",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:selected[0].candidate_id"


def test_state_check_rejects_screened_candidates_missing_id_without_declared_total(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-001",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because section capacity was already filled.",
                    }
                ],
                "screening_policy": {"max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:selected[0].candidate_id"


def test_state_check_rejects_screened_candidates_unknown_id_without_declared_total(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-999",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because section capacity was already filled.",
                    }
                ],
                "screening_policy": {"max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert (
        record["validation_result"]
        == "screened_candidates_schema_error:excluded[0].unknown_candidate_id:CAND-999"
    )


def test_state_check_accepts_screened_candidates_total_matching_candidate_universe(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because section capacity was already filled.",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "valid_screened_candidates_schema"


def test_state_check_marks_invalid_screening_status_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps([{"candidate_id": "CAND-001", "screening_status": "maybe"}]) + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:candidate[0].screening_status"


def test_state_check_requires_screening_reason_for_rejected_candidate(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "screened_candidates.json", _valid_screened_candidates_payload(status="rejected"))

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["screened_candidates"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "screened_candidates_schema_error:candidate[0].reason"


def test_state_check_marks_input_classification_path_escape_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_input_classification(
        ws,
        {
            "evidence": [{"path": "../outside.md", "name": "outside.md"}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        },
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["input_classification"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "input_classification_schema_error:evidence[0].path"


def test_state_decide_validates_decision_vocabulary(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "doctor",
        "--decision",
        "invent_decision",
        "--reason",
        "bad",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Unknown Orchestrator decision" in payload["error"]


def test_state_decide_records_event_and_last_decision(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = record_decision(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        decision="block_run",
        reason="doctor failed",
    )

    workflow = state["workflow_state"]
    assert workflow["last_decision"]["decision"] == "block_run"
    assert workflow["stage_statuses"]["doctor"]["status"] == "blocked"
    assert workflow["current_stage"] == "doctor"
    events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["event_type"] == "decision_recorded" for line in events)


def test_state_decide_delegate_repair_requires_repair_transaction_without_mutation(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_editor_repair_gate_report(ws)
    registry_path = _state_file(ws, "artifact_registry")
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_registry = registry_path.read_bytes() if registry_path.exists() else None
    before_events = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            decision="delegate_repair",
            reason="repair editor-owned brief",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_REPAIR_TRANSACTION_REQUIRED
    details = excinfo.value.details
    assert "multi-agent-brief repair route" in "\n".join(details["required_commands"])
    assert "multi-agent-brief repair start" in "\n".join(details["required_commands"])
    assert "multi-agent-brief repair complete" in "\n".join(details["required_commands"])
    assert "Delegate only the reported repair_owner role." in details["repair_steps"]
    assert "Allow edits only to repair_route.allowed_artifacts." in details["repair_steps"]
    assert details["repair_route"]["repair_owner"] == "editor"
    assert details["repair_route"]["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert details["repair_route"]["must_rerun_from"] == "auditor"
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    if before_registry is None:
        assert not registry_path.exists()
    else:
        assert registry_path.read_bytes() == before_registry
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_state_decide_delegate_repair_human_output_points_to_repair_transaction(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_editor_repair_gate_report(ws)

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "auditor",
        "--decision",
        "delegate_repair",
        "--reason",
        "repair editor-owned brief",
    ])

    assert rc == 1
    out = capsys.readouterr().out
    assert "multi-agent-brief repair route" in out
    assert "multi-agent-brief repair start" in out
    assert "multi-agent-brief repair complete" in out
    assert "[state] repair_steps:" in out
    assert "Delegate only the reported repair_owner role." in out
    assert "[state] repair_owner: editor" in out
    assert "[state] must_rerun_from: auditor" in out
    assert "output/intermediate/audited_brief.md" in out


def test_stage_complete_records_transaction_event_and_advances(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )

    workflow = state["workflow_state"]
    transaction_id = workflow["last_completion_transaction"]["transaction_id"]
    assert workflow["current_stage"] == "source-discovery"
    assert workflow["stage_statuses"]["doctor"]["status"] == "complete"
    assert state["transaction"]["transaction_id"] == transaction_id
    decision_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "decision_recorded"
        and (event.get("metadata") or {}).get("transaction_id") == transaction_id
    ]
    assert len(decision_events) == 1
    assert decision_events[0]["decision"] == "continue"


def test_stage_complete_records_runtime_model_provenance_from_cli(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "stage-complete",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "doctor",
        "--reason",
        "doctor passed",
        "--runtime",
        "claude",
        "--model",
        "claude-sonnet-4",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    provenance = payload["transaction"]["runtime_provenance"]
    assert provenance == {
        "schema_version": "mabw.stage_runtime_provenance.v1",
        "source": "stage_completion_args",
        "recorded_by_actor": "orchestrator",
        "provenance_only": True,
        "quality_claim": False,
        "runtime": "claude",
        "model": "claude-sonnet-4",
    }
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["stage_statuses"]["doctor"]["metadata"]["runtime_provenance"] == provenance
    assert workflow["last_completion_transaction"]["runtime_provenance"] == provenance
    transaction_id = workflow["last_completion_transaction"]["transaction_id"]
    decision_event = next(
        event for event in _event_records(ws)
        if event["event_type"] == "decision_recorded"
        and (event.get("metadata") or {}).get("transaction_id") == transaction_id
    )
    assert decision_event["metadata"]["runtime_provenance"] == provenance


def test_stage_complete_duplicate_rejects_without_contamination_event(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed again",
        )

    assert excinfo.value.error_code == "E_STAGE_ALREADY_COMPLETED"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "clean"
    assert workflow["run_integrity"]["reference_eligible"] is True
    assert workflow["run_integrity"].get("reasons", []) == []
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert contamination_events == []
    workflow_bytes_after_first_rejection = _state_file(ws, "workflow_state").read_bytes()
    event_bytes_after_first_rejection = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError):
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed yet again",
        )

    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert workflow["run_integrity"]["status"] == "clean"
    assert workflow["run_integrity"].get("reasons", []) == []
    assert contamination_events == []
    assert len(_event_records(ws)) == len(before_events)
    assert _state_file(ws, "workflow_state").read_bytes() == workflow_bytes_after_first_rejection
    assert _state_file(ws, "event_log").read_bytes() == event_bytes_after_first_rejection


def test_stage_complete_duplicate_validation_runs_before_contamination_event(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    _fail_appending_event_type(monkeypatch, "run_integrity_contaminated")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor replay should fail atomically",
        )

    assert excinfo.value.error_code == "E_STAGE_ALREADY_COMPLETED"
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_stage_complete_missing_required_output_writes_nothing(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="scout",
            reason="scout complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events


def test_stage_complete_cli_json_error_includes_error_code(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "stage-complete",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "auditor",
        "--reason",
        "out of order",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_STAGE_MISMATCH"


def test_stage_complete_event_append_failure_is_detectable_partial_write(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed",
        )

    assert excinfo.value.error_code == "E_TRANSACTION_PARTIAL_WRITE"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["current_stage"] == "source-discovery"
    assert workflow["last_completion_transaction"]["transaction_id"]

    monkeypatch.undo()
    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert checked["workflow_state"]["blocked"] is True
    assert checked["transaction_integrity_warning"]["error_code"] == "E_TRANSACTION_INTEGRITY"


def test_stage_complete_stale_gate_report_does_not_block_early_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_quality_gate_report(ws, blocking=True, stage_id="auditor")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="scout",
        reason="scout complete",
    )

    assert state["workflow_state"]["current_stage"] == "claim-ledger"


def test_default_topology_scout_completion_requires_screened_candidates(tmp_path):
    repo = _repo_with_role_topology(
        tmp_path,
        "default",
    )
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=repo,
            stage_id="scout",
            reason="scout complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert "Required topology artifact for stage 'screener' 'screened_candidates'" in str(excinfo.value)
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events


def test_default_topology_scout_completion_rejects_screened_candidate_universe_mismatch(tmp_path):
    repo = _repo_with_role_topology(
        tmp_path,
        "default",
    )
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "scout")
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [],
                "screening_policy": {"total_candidates": 1, "max_items": 8},
            }
        )
        + "\n",
    )
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=repo,
            stage_id="scout",
            reason="scout complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "candidate_universe_count_mismatch" in str(excinfo.value)
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events


def test_default_topology_scout_completion_satisfies_screener(tmp_path):
    repo = _repo_with_role_topology(
        tmp_path,
        "default",
    )
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=repo,
        stage_id="scout",
        reason="scout complete",
    )

    workflow = state["workflow_state"]
    screener_status = workflow["stage_statuses"]["screener"]
    assert workflow["current_stage"] == "claim-ledger"
    assert screener_status["status"] == "complete"
    assert screener_status["metadata"]["satisfied_by_topology"] is True
    assert screener_status["metadata"]["topology"] == "default"
    assert screener_status["metadata"]["satisfied_by"] == "scout"
    assert screener_status["metadata"]["satisfied_by_stage"] == "scout"
    assert screener_status["metadata"]["required_artifacts"] == [
        "candidate_claims",
        "screened_candidates",
    ]
    events = _event_records(ws)
    topology_events = [
        event
        for event in events
        if event.get("event_type") == "stage_satisfied_by_topology"
    ]
    assert len(topology_events) == 1
    assert topology_events[0]["stage_id"] == "screener"
    assert topology_events[0]["metadata"]["satisfied_by"] == "scout"
    timing_workflow = {
        "run_id": state["manifest"]["run_id"],
        "current_stage": workflow["current_stage"],
        "stage_statuses": {
            "scout": workflow["stage_statuses"]["scout"],
            "screener": workflow["stage_statuses"]["screener"],
        },
        "run_integrity": workflow["run_integrity"],
    }
    timing = derive_control_timing_from_path(
        _state_file(ws, "event_log"),
        workflow_state=timing_workflow,
        stage_order=["scout", "screener"],
        expected_run_id=state["manifest"]["run_id"],
    )
    assert timing["status"] == "available"
    screener_timing = next(stage for stage in timing["stages"] if stage["stage_id"] == "screener")
    assert screener_timing["status"] == "satisfied_by_topology"


def test_default_topology_screener_replay_rejects_without_contamination(tmp_path):
    repo = _repo_with_role_topology(
        tmp_path,
        "default",
    )
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=repo,
        stage_id="scout",
        reason="scout complete",
    )
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=repo,
            stage_id="screener",
            reason="orchestrator replayed topology-satisfied screener",
        )

    assert excinfo.value.error_code == "E_STAGE_ALREADY_COMPLETED"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "clean"
    assert workflow["run_integrity"]["reference_eligible"] is True
    assert workflow["run_integrity"].get("reasons", []) == []
    assert [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ] == []
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_default_topology_satisfied_screener_artifact_is_frozen(tmp_path):
    repo = _repo_with_role_topology(
        tmp_path,
        "default",
    )
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "scout")
    _write_json_artifact(
        ws,
        "candidate_claims.json",
        json.dumps(
            [
                {"candidate_id": "CAND-001", "claim": "ExampleCo opened a demo facility.", "source_id": "SRC-001"},
                {"candidate_id": "CAND-002", "claim": "ExampleCo expanded production.", "source_id": "SRC-002"},
            ]
        )
        + "\n",
    )
    _write_json_artifact(
        ws,
        "screened_candidates.json",
        json.dumps(
            {
                "selected": [
                    {
                        "candidate_id": "CAND-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "evidence_text": "ExampleCo opened a demo facility in June.",
                        "source_id": "SRC-001",
                        "published_at": "2026-06-01",
                    }
                ],
                "excluded": [
                    {
                        "candidate_id": "CAND-002",
                        "reason": "capacity_capped",
                        "reason_code": "capacity_capped",
                        "explanation": "Dropped because section capacity was already filled.",
                    }
                ],
                "screening_policy": {"total_candidates": 2, "max_items": 8},
            }
        )
        + "\n",
    )
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=repo,
        stage_id="scout",
        reason="scout complete",
    )
    registry_before = _state_file(ws, "artifact_registry").read_bytes()

    screened_path = _intermediate(ws) / "screened_candidates.json"
    screened = json.loads(screened_path.read_text(encoding="utf-8"))
    screened["excluded"][0]["explanation"] = "Illegally changed after topology-satisfied Screener completion."
    screened_path.write_text(
        json.dumps(screened, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=repo)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Frozen artifact" in str(excinfo.value)
    assert "screened_candidates.json" in str(excinfo.value)
    assert "owner stage 'screener'" in str(excinfo.value)
    assert _state_file(ws, "artifact_registry").read_bytes() == registry_before
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "contaminated"
    assert workflow["run_integrity"]["reference_eligible"] is False
    assert workflow["run_integrity"]["reasons"][0]["reason_code"] == "frozen_artifact_changed"
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert len(contamination_events) == 1
    assert contamination_events[0]["metadata"]["reason_code"] == "frozen_artifact_changed"


def test_topology_satisfaction_respects_target_stage_feedback_blockers(tmp_path):
    repo = _repo_with_role_topology(
        tmp_path,
        "default",
    )
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_feedback_issue(ws, stage_id="screener", artifact_id="screened_candidates")
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=repo,
            stage_id="scout",
            reason="scout complete",
        )

    assert excinfo.value.error_code == "E_ILLEGAL_TRANSITION"
    assert "Current stage 'screener' has unresolved blocking feedback issues" in str(excinfo.value)
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events


def test_strict_topology_keeps_screener_independent(tmp_path):
    repo = _repo_with_role_topology(tmp_path, "strict")
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=repo,
        stage_id="scout",
        reason="scout complete",
    )

    workflow = state["workflow_state"]
    assert workflow["current_stage"] == "screener"
    assert workflow["stage_statuses"]["screener"]["status"] == "ready"
    assert "metadata" not in workflow["stage_statuses"]["screener"]
    assert not [
        event
        for event in _event_records(ws)
        if event.get("event_type") == "stage_satisfied_by_topology"
    ]


def test_human_assisted_topology_analyst_completion_satisfies_editor(tmp_path):
    repo = _repo_with_role_topology(tmp_path, "human_assisted")
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=repo)
    _set_current_stage(ws, "analyst")
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n\nWriter draft.\n", encoding="utf-8")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=repo,
        stage_id="analyst",
        reason="writer complete",
    )

    workflow = state["workflow_state"]
    editor_status = workflow["stage_statuses"]["editor"]
    assert workflow["current_stage"] == "auditor"
    assert editor_status["status"] == "complete"
    assert editor_status["metadata"]["satisfied_by_topology"] is True
    assert editor_status["metadata"]["topology"] == "human_assisted"
    assert editor_status["metadata"]["satisfied_by"] == "writer"
    assert editor_status["metadata"]["satisfied_by_stage"] == "analyst"


def test_claim_ledger_stage_complete_rejects_non_ledger_json_shape(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", '{"not_claims": []}\n')
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error" in str(excinfo.value)
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events
    assert not _state_file(ws, "artifact_registry").exists()


def test_claim_ledger_stage_complete_rejects_invalid_claim_schema(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        json.dumps(
            [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "claim_type": "unsupported_kind",
                }
            ]
        )
        + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error:claim[0].claim_type" in str(excinfo.value)


def test_freeze_claim_ledger_transaction_writes_canonical_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())

    state = freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    ledger = json.loads((_intermediate(ws) / "claim_ledger.json").read_text(encoding="utf-8"))
    assert [claim["claim_id"] for claim in ledger] == ["CL-0001", "CL-0002"]
    assert [claim["source_id"] for claim in ledger] == ["SRC-001", "SRC-002"]
    freeze = state["manifest"]["claim_ledger_freeze"]
    assert freeze["schema_version"] == "mabw.claim_ledger_freeze.v1"
    assert freeze["status"] == "frozen"
    assert freeze["id_strategy"] == "sorted_sequential_v1"
    assert freeze["id_stability_scope"] == "per_freeze_input"
    assert "not a cross-incremental stability guarantee" in freeze["id_strategy_description"]
    assert freeze["source_path"] == "output/intermediate/claim_drafts.json"
    assert freeze["claim_ledger_path"] == "output/intermediate/claim_ledger.json"
    assert freeze["claim_count"] == 2
    assert freeze["claim_ledger_sha256"] == _sha256_file(_intermediate(ws) / "claim_ledger.json")
    assert freeze["source_sha256"] == _sha256_file(_intermediate(ws) / "claim_drafts.json")
    records = _event_records(ws)
    assert records[-1]["event_type"] == "claim_ledger_frozen"
    assert records[-1]["artifact_id"] == "claim_ledger"


def test_freeze_claim_ledger_preserves_draft_provenance_metadata(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                        "source_url": "https://example.com/news",
                        "source_type": "public_web",
                        "source_path": "input/sources/source-001.md",
                        "published_at": "2026-06-01",
                        "retrieved_at": "2026-06-16T00:00:00Z",
                        "source_title": "ExampleCo Demo Facility",
                        "source_name": "Example Wire",
                        "publisher": "Example Publisher",
                        "source_category": "news_media",
                        "retrieval_source_type": "news_media",
                        "underlying_evidence_type": "media_report",
                        "raw_underlying_evidence_type": "industry_media",
                        "topic": "demo market",
                    }
                ],
            }
        )
        + "\n",
    )

    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    ledger = json.loads((_intermediate(ws) / "claim_ledger.json").read_text(encoding="utf-8"))
    claim = ledger[0]
    assert claim["source_url"] == "https://example.com/news"
    assert claim["source_type"] == "public_web"
    assert claim["metadata"]["source_path"] == "input/sources/source-001.md"
    assert claim["metadata"]["published_at"] == "2026-06-01"
    assert claim["metadata"]["retrieved_at"] == "2026-06-16T00:00:00Z"
    assert claim["metadata"]["source_title"] == "ExampleCo Demo Facility"
    assert claim["metadata"]["source_name"] == "Example Wire"
    assert claim["metadata"]["publisher"] == "Example Publisher"
    assert claim["metadata"]["source_url"] == "https://example.com/news"
    assert claim["metadata"]["source_type"] == "public_web"
    assert claim["metadata"]["source_category"] == "news_media"
    assert claim["metadata"]["retrieval_source_type"] == "news_media"
    assert claim["metadata"]["underlying_evidence_type"] == "media_report"
    assert claim["metadata"]["raw_underlying_evidence_type"] == "industry_media"
    assert claim["metadata"]["topic"] == "demo market"


def test_freeze_claim_ledger_normalizes_blank_draft_source_type_to_default(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                        "source_type": "   ",
                        "source_path": "input/sources/source-001.md",
                        "source_title": "Example clinical study",
                        "source_category": "peer_reviewed_paper",
                    }
                ],
            }
        )
        + "\n",
    )

    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    ledger = json.loads((_intermediate(ws) / "claim_ledger.json").read_text(encoding="utf-8"))
    claim = ledger[0]
    assert claim["source_type"] == "local_file"
    assert "source_type" not in claim["metadata"]


def test_enrich_claim_metadata_fills_source_type_after_blank_draft_default(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _add_imported_source_authority(
        ws,
        metadata={
            "source_url": "https://example.com/news",
            "source_type": "web_search",
        },
    )
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                        "source_type": "   ",
                        "source_path": "input/sources/source-001.json",
                        "source_title": "Example clinical study",
                        "source_category": "peer_reviewed_paper",
                    }
                ],
            }
        )
        + "\n",
    )
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    state = enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    ledger = json.loads((_intermediate(ws) / "claim_ledger.json").read_text(encoding="utf-8"))
    claim = ledger[0]
    assert claim["source_type"] == "web_search"
    assert claim["metadata"]["source_type"] == "web_search"
    record = state["claim_ledger_metadata_enrichment"]["enriched_claims"][0]
    assert "source_type" in record["fields"]


def test_enrich_claim_metadata_uses_imported_source_evidence(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _add_imported_source_authority(
        ws,
        metadata={
            "published_at": "2026-06-01",
            "retrieved_at": "2026-06-16T00:00:00Z",
            "title": "ExampleCo Demo Facility",
            "name": "Example Wire",
            "publisher": "Example Publisher",
            "source_url": "https://example.com/news",
            "source_type": "web_search",
            "source_category": "news_media",
            "retrieval_source_type": "news_media",
            "underlying_evidence_type": "media_report",
            "raw_underlying_evidence_type": "industry_media",
            "topic": "demo market",
        },
    )
    _add_imported_source_authority(ws, source_id="SRC-002", filename="source-002.json")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    state = enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    ledger_path = _intermediate(ws) / "claim_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    claim = ledger[0]
    assert claim["metadata"]["published_at"] == "2026-06-01"
    assert claim["metadata"]["retrieved_at"] == "2026-06-16T00:00:00Z"
    assert claim["metadata"]["source_title"] == "ExampleCo Demo Facility"
    assert claim["metadata"]["source_name"] == "Example Wire"
    assert claim["metadata"]["publisher"] == "Example Publisher"
    assert claim["metadata"]["source_url"] == "https://example.com/news"
    assert claim["metadata"]["source_type"] == "web_search"
    assert claim["metadata"]["source_category"] == "news_media"
    assert claim["metadata"]["retrieval_source_type"] == "news_media"
    assert claim["metadata"]["underlying_evidence_type"] == "media_report"
    assert claim["metadata"]["raw_underlying_evidence_type"] == "industry_media"
    assert claim["metadata"]["topic"] == "demo market"
    assert claim["source_url"] == "https://example.com/news"
    assert claim["source_type"] == "web_search"
    appendix = build_source_appendix(
        audited_markdown="ExampleCo opened a demo facility. [src:CL-0001]",
        ledger_path=ledger_path,
    )
    assert "[S1] ExampleCo Demo Facility" in appendix.markdown
    assert "- Source category: News media" in appendix.markdown
    assert "- Publisher/Institution: Example Publisher" in appendix.markdown
    assert "- URL: <https://example.com/news>" in appendix.markdown
    assert "- Provider type: web_search" in appendix.markdown
    assert appendix.claim_source_map["CL-0001"]["source_type"] == "web_search"
    assert claim["metadata"]["source_path"] == "input/sources/source-001.json"
    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    registry = json.loads(_state_file(ws, "artifact_registry").read_text(encoding="utf-8"))
    assert manifest["claim_ledger_freeze"]["claim_ledger_sha256"] == _sha256_file(ledger_path)
    assert registry["artifacts"]["claim_ledger"]["sha256"] == _sha256_file(ledger_path)
    assert state["claim_ledger_metadata_enrichment"]["enriched_claim_count"] == 2
    assert _event_records(ws)[-1]["event_type"] == "claim_ledger_metadata_enriched"


def test_enrich_claim_metadata_rerun_mirrors_source_type_from_existing_metadata(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _add_imported_source_authority(
        ws,
        metadata={
            "published_at": "2026-06-01",
            "retrieved_at": "2026-06-16T00:00:00Z",
            "title": "ExampleCo Demo Facility",
            "name": "Example Wire",
            "publisher": "Example Publisher",
            "source_url": "https://example.com/news",
            "source_type": "web_search",
            "source_category": "news_media",
            "topic": "demo market",
        },
    )
    _add_imported_source_authority(ws, source_id="SRC-002", filename="source-002.json")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    ledger_path = _intermediate(ws) / "claim_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    claim = ledger[0]
    claim["source_type"] = "local_file"
    claim["source_url"] = "https://example.com/news"
    claim["metadata"].update({
        "published_at": "2026-06-01",
        "retrieved_at": "2026-06-16T00:00:00Z",
        "source_title": "ExampleCo Demo Facility",
        "source_name": "Example Wire",
        "publisher": "Example Publisher",
        "source_url": "https://example.com/news",
        "source_type": "web_search",
        "source_category": "news_media",
        "topic": "demo market",
        "source_path": "input/sources/source-001.json",
    })
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path = _state_file(ws, "runtime_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["claim_ledger_freeze"]["claim_ledger_sha256"] = _sha256_file(ledger_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    state = enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    enriched = json.loads(ledger_path.read_text(encoding="utf-8"))[0]
    assert enriched["metadata"]["source_type"] == "web_search"
    assert enriched["metadata"]["retrieval_source_type"] == "news_media"
    assert enriched["metadata"]["underlying_evidence_type"] == "media_report"
    assert enriched["metadata"]["raw_underlying_evidence_type"] == "news_media"
    assert enriched["source_type"] == "web_search"
    enriched_records = state["claim_ledger_metadata_enrichment"]["enriched_claims"]
    claim_record = next(record for record in enriched_records if record["claim_id"] == "CL-0001")
    assert claim_record["fields"] == [
        "raw_underlying_evidence_type",
        "retrieval_source_type",
        "source_type",
        "underlying_evidence_type",
    ]
    appendix = build_source_appendix(
        audited_markdown="ExampleCo opened a demo facility. [src:CL-0001]",
        ledger_path=ledger_path,
    )
    assert "- Provider type: web_search" in appendix.markdown
    assert appendix.claim_source_map["CL-0001"]["source_type"] == "web_search"


def test_enrich_claim_metadata_updates_completed_claim_ledger_stage_hash(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _add_imported_source_authority(ws, metadata={"published_at": "2026-06-01"})
    _add_imported_source_authority(ws, source_id="SRC-002", filename="source-002.json")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )

    enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    ledger_sha = _sha256_file(_intermediate(ws) / "claim_ledger.json")
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    claim_metadata = workflow["stage_statuses"]["claim-ledger"]["metadata"]
    assert claim_metadata["produced_artifact_sha256"]["claim_ledger"] == ledger_sha
    assert claim_metadata["claim_ledger_metadata_enrichment"]["claim_ledger_sha256"] == ledger_sha


def test_enrich_claim_metadata_accepts_imported_fast_rerun_claim_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    archive = (
        ROOT
        / "tests"
        / "fixtures"
        / "fast_rerun_clean_archive"
        / "output"
        / "runs"
        / "mabw-20260614T000000Z-public0001"
        / "manifest.json"
    )
    imported = import_fact_layer_transaction(
        workspace=ws,
        archive=archive,
        runtime="codex",
        repo_workdir=ROOT,
    )
    assert "claim_ledger_freeze" not in imported["manifest"]
    imported_claim_record = next(
        record
        for record in imported["manifest"]["fact_layer_import"]["imported_files"]
        if record["artifact_id"] == "claim_ledger"
    )

    state = enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    ledger_path = _intermediate(ws) / "claim_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    metadata = ledger[0]["metadata"]
    assert metadata["published_at"] == "2026-06-10"
    assert metadata["retrieved_at"] == "2026-06-14T00:00:00Z"
    assert metadata["source_title"] == "Synthetic public market update"
    assert metadata["source_path"] == "input/sources/source-001.md"
    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    assert "claim_ledger_freeze" not in manifest
    enrichment = manifest["claim_ledger_metadata_enrichment"]
    assert enrichment["claim_ledger_authority"] == "fact_layer_import"
    assert enrichment["previous_claim_ledger_sha256"] == imported_claim_record["sha256"]
    assert enrichment["source_claim_ledger_sha256"] == imported_claim_record["sha256"]
    assert enrichment["claim_ledger_sha256"] == _sha256_file(ledger_path)
    assert state["fact_layer_import"]["status"] == "valid"
    shown = show_runtime_state(workspace=ws)
    assert shown["fact_layer_import"]["status"] == "valid"
    assert shown["fact_layer_import"]["derived_imported_files"] == [
        {
            "artifact_id": "claim_ledger",
            "workspace_path": "output/intermediate/claim_ledger.json",
            "original_sha256": imported_claim_record["sha256"],
            "current_sha256": _sha256_file(ledger_path),
            "derivation": "claim_ledger_metadata_enrichment",
        }
    ]


def test_enrich_claim_metadata_preserves_fast_rerun_import_after_chained_enrichment(tmp_path):
    ws = _write_workspace(tmp_path)
    archive = (
        ROOT
        / "tests"
        / "fixtures"
        / "fast_rerun_clean_archive"
        / "output"
        / "runs"
        / "mabw-20260614T000000Z-public0001"
        / "manifest.json"
    )
    imported = import_fact_layer_transaction(
        workspace=ws,
        archive=archive,
        runtime="codex",
        repo_workdir=ROOT,
    )
    imported_claim_record = next(
        record
        for record in imported["manifest"]["fact_layer_import"]["imported_files"]
        if record["artifact_id"] == "claim_ledger"
    )

    source_path = ws / "input" / "sources" / "source-001.md"
    source_text = source_path.read_text(encoding="utf-8")
    source_path.write_text(
        source_text.replace(
            "Retrieved: 2026-06-14T00:00:00Z\n",
            "Retrieved: 2026-06-14T00:00:00Z\nsource_type: web_search\n",
        ),
        encoding="utf-8",
    )

    manifest_path = _state_file(ws, "runtime_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_record = next(
        record
        for record in manifest["fact_layer_import"]["imported_files"]
        if record["workspace_path"] == "input/sources/source-001.md"
    )
    source_record["sha256"] = _sha256_file(source_path)
    source_record["size_bytes"] = source_path.stat().st_size

    ledger_path = _intermediate(ws) / "claim_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger[0]["source_type"] = "local_file"
    ledger[0]["metadata"]["source_type"] = "web_search"
    ledger_path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    first_derived_sha = _sha256_file(ledger_path)
    enrichment_record = {
        "schema_version": "mabw.claim_ledger_metadata_enrichment.v1",
        "status": "applied",
        "enriched_at": "2026-06-18T00:00:00+00:00",
        "transaction_id": "previous-enrichment",
        "source": "fact_layer_imported_source_evidence",
        "claim_ledger_authority": "fact_layer_import",
        "source_claim_ledger_sha256": imported_claim_record["sha256"],
        "claim_ledger_path": "output/intermediate/claim_ledger.json",
        "previous_claim_ledger_sha256": imported_claim_record["sha256"],
        "claim_ledger_sha256": first_derived_sha,
        "allowed_fields": [],
        "forbidden_fields": [],
        "enriched_claim_count": 1,
        "enriched_claims": [],
    }
    manifest["claim_ledger_metadata_enrichment"] = enrichment_record
    manifest["claim_ledger_metadata_enrichments"] = [enrichment_record]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    state = enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    latest = manifest["claim_ledger_metadata_enrichment"]
    assert latest["claim_ledger_authority"] == "fact_layer_import_derived"
    assert latest["previous_claim_ledger_sha256"] == first_derived_sha
    assert latest["source_claim_ledger_sha256"] == imported_claim_record["sha256"]
    assert latest["claim_ledger_sha256"] == _sha256_file(ledger_path)
    assert state["fact_layer_import"]["status"] == "valid"
    shown = show_runtime_state(workspace=ws)
    assert shown["fact_layer_import"]["status"] == "valid"
    assert shown["fact_layer_import"]["derived_imported_files"] == [
        {
            "artifact_id": "claim_ledger",
            "workspace_path": "output/intermediate/claim_ledger.json",
            "original_sha256": imported_claim_record["sha256"],
            "current_sha256": _sha256_file(ledger_path),
            "derivation": "claim_ledger_metadata_enrichment",
        }
    ]


def test_state_enrich_claim_metadata_cli_json(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _add_imported_source_authority(ws, metadata={"published_at": "2026-06-01"})
    _add_imported_source_authority(ws, source_id="SRC-002", filename="source-002.json")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "enrich-claim-metadata",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--from-source-evidence",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["transaction"]["decision"] == "enrich_claim_metadata"
    assert payload["claim_ledger_metadata_enrichment"]["enriched_claim_count"] == 2


def test_enrich_claim_metadata_rejects_missing_imported_source_authority(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    before_manifest = _state_file(ws, "runtime_manifest").read_bytes()
    before_ledger = (_intermediate(ws) / "claim_ledger.json").read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == "E_TRANSACTION_INTEGRITY"
    assert "imported frozen source evidence" in str(excinfo.value)
    assert _state_file(ws, "runtime_manifest").read_bytes() == before_manifest
    assert (_intermediate(ws) / "claim_ledger.json").read_bytes() == before_ledger


def test_enrich_claim_metadata_rejects_hand_edited_statement(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _add_imported_source_authority(ws, metadata={"published_at": "2026-06-01"})
    _add_imported_source_authority(ws, source_id="SRC-002", filename="source-002.json")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    ledger_path = _intermediate(ws) / "claim_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger[0]["statement"] = "Changed statement."
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Frozen Claim Ledger hash does not match current claim_ledger.json" in str(excinfo.value)


def test_enrich_claim_metadata_rolls_back_when_state_write_fails(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _add_imported_source_authority(ws, metadata={"published_at": "2026-06-01"})
    _add_imported_source_authority(ws, source_id="SRC-002", filename="source-002.json")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    before_manifest = _state_file(ws, "runtime_manifest").read_bytes()
    before_registry = _state_file(ws, "artifact_registry").read_bytes()
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    before_ledger = (_intermediate(ws) / "claim_ledger.json").read_bytes()
    original_write_json_atomic = runtime_state.operations._write_json_atomic

    def fail_artifact_registry_write(path: Path, payload: dict) -> None:
        if path.name == "artifact_registry.json":
            raise RuntimeStateError(
                "artifact registry write failed during enrichment",
                error_code=runtime_state.operations.E_TRANSACTION_INTEGRITY,
            )
        original_write_json_atomic(path, payload)

    monkeypatch.setattr(runtime_state.operations, "_write_json_atomic", fail_artifact_registry_write)

    with pytest.raises(RuntimeStateError) as excinfo:
        enrich_claim_metadata_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert excinfo.value.details["restored"] is True
    assert _state_file(ws, "runtime_manifest").read_bytes() == before_manifest
    assert _state_file(ws, "artifact_registry").read_bytes() == before_registry
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events
    assert (_intermediate(ws) / "claim_ledger.json").read_bytes() == before_ledger


def test_freeze_claim_ledger_is_idempotent_for_same_frozen_inputs(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    manifest_before = _state_file(ws, "runtime_manifest").read_bytes()
    registry_before = _state_file(ws, "artifact_registry").read_bytes()
    event_log_before = _state_file(ws, "event_log").read_bytes()
    ledger_before = (_intermediate(ws) / "claim_ledger.json").read_bytes()

    state = freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert state["transaction"]["decision"] == "freeze_claim_ledger_idempotent"
    assert _state_file(ws, "runtime_manifest").read_bytes() == manifest_before
    assert _state_file(ws, "artifact_registry").read_bytes() == registry_before
    assert _state_file(ws, "event_log").read_bytes() == event_log_before
    assert (_intermediate(ws) / "claim_ledger.json").read_bytes() == ledger_before


def test_freeze_claim_ledger_rejects_hand_edited_frozen_ledger_metadata_with_guidance(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    manifest_before = _state_file(ws, "runtime_manifest").read_bytes()
    registry_before = _state_file(ws, "artifact_registry").read_bytes()
    event_log_before = _state_file(ws, "event_log").read_bytes()
    ledger = json.loads((_intermediate(ws) / "claim_ledger.json").read_text(encoding="utf-8"))
    ledger[0].setdefault("metadata", {})["published_at"] = "2026-06-18"
    (_intermediate(ws) / "claim_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == "E_TRANSACTION_INTEGRITY"
    assert "Do not hand-edit metadata or synchronize hashes manually" in str(excinfo.value)
    assert "deterministic metadata enrichment transaction" in str(excinfo.value)
    assert _state_file(ws, "runtime_manifest").read_bytes() == manifest_before
    assert _state_file(ws, "artifact_registry").read_bytes() == registry_before
    assert _state_file(ws, "event_log").read_bytes() == event_log_before


def test_freeze_claim_ledger_requires_existing_event_log(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    manifest_before = _state_file(ws, "runtime_manifest").read_bytes()
    assert not _state_file(ws, "artifact_registry").exists()
    _state_file(ws, "event_log").unlink()

    with pytest.raises(RuntimeStateError) as excinfo:
        freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == "E_RUNTIME_STATE_NOT_INITIALIZED"
    assert not (_intermediate(ws) / "claim_ledger.json").exists()
    assert not _state_file(ws, "event_log").exists()
    assert _state_file(ws, "runtime_manifest").read_bytes() == manifest_before
    assert not _state_file(ws, "artifact_registry").exists()


def test_freeze_claim_ledger_requires_current_run_initialized_event(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    manifest_before = _state_file(ws, "runtime_manifest").read_bytes()
    assert not _state_file(ws, "artifact_registry").exists()
    _state_file(ws, "event_log").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == "E_TRANSACTION_INTEGRITY"
    assert "current-run start event" in str(excinfo.value)
    assert not (_intermediate(ws) / "claim_ledger.json").exists()
    assert _state_file(ws, "runtime_manifest").read_bytes() == manifest_before
    assert not _state_file(ws, "artifact_registry").exists()


def test_freeze_claim_ledger_accepts_reset_run_start_event(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, reset_state=True)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())

    state = freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert state["claim_ledger_freeze"]["status"] == "frozen"
    assert (_intermediate(ws) / "claim_ledger.json").exists()
    records = _event_records(ws)
    run_id = state["manifest"]["run_id"]
    assert any(record["run_id"] == run_id and record["event_type"] == "run_reset" for record in records)
    assert records[-1]["event_type"] == "claim_ledger_frozen"


def test_freeze_claim_ledger_rejects_changed_drafts_after_existing_freeze(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    manifest_before = _state_file(ws, "runtime_manifest").read_bytes()
    registry_before = _state_file(ws, "artifact_registry").read_bytes()
    event_log_before = _state_file(ws, "event_log").read_bytes()
    ledger_before = (_intermediate(ws) / "claim_ledger.json").read_bytes()
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload(duplicate=True))

    with pytest.raises(RuntimeStateError) as excinfo:
        freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == "E_TRANSACTION_INTEGRITY"
    assert "already frozen" in str(excinfo.value)
    assert "source hash does not match" in str(excinfo.value.details["freeze_reasons"])
    assert _state_file(ws, "runtime_manifest").read_bytes() == manifest_before
    assert _state_file(ws, "artifact_registry").read_bytes() == registry_before
    assert _state_file(ws, "event_log").read_bytes() == event_log_before
    assert (_intermediate(ws) / "claim_ledger.json").read_bytes() == ledger_before


def test_freeze_claim_ledger_rejects_draft_claim_id_without_writing_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "claim_id": "CL-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                    }
                ],
            }
        )
        + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == "E_CLAIM_DRAFT_CONTRACT_INVALID"
    assert "drafts[0].claim_id" in str(excinfo.value.details)
    assert excinfo.value.details["diagnostics"][0]["field"] == "drafts[0].claim_id"
    assert excinfo.value.details["diagnostics"][0]["forbidden_fields"] == ["claim_id"]
    assert not (_intermediate(ws) / "claim_ledger.json").exists()


def test_freeze_claim_ledger_rejects_empty_drafts_without_writing_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps({"schema_version": "mabw.claim_drafts.v1", "drafts": []}) + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == "E_CLAIM_DRAFT_CONTRACT_INVALID"
    assert excinfo.value.details["field"] == "drafts"
    assert excinfo.value.details["diagnostics"][0]["required_fields"] == [
        "statement",
        "source_id",
        "evidence_text",
    ]
    assert "at least one draft" in str(excinfo.value)
    assert not (_intermediate(ws) / "claim_ledger.json").exists()


def test_freeze_claim_ledger_warns_on_lexical_duplicates_without_merging(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload(duplicate=True))

    state = freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    ledger = json.loads((_intermediate(ws) / "claim_ledger.json").read_text(encoding="utf-8"))
    freeze = state["manifest"]["claim_ledger_freeze"]
    assert len(ledger) == 3
    assert [claim["claim_id"] for claim in ledger] == ["CL-0001", "CL-0002", "CL-0003"]
    assert freeze["warnings"][0]["warning_type"] == "lexical_duplicate_statement"
    assert freeze["warnings"][0]["draft_indexes"] == [0, 2]


def test_claim_ledger_stage_complete_requires_freeze(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert "Claim Ledger has not been frozen" in str(excinfo.value)


def test_claim_ledger_stage_complete_accepts_frozen_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )

    assert state["workflow_state"]["current_stage"] == "analyst"
    assert state["artifact_registry"]["artifacts"]["claim_ledger"]["status"] == "valid"


def test_claim_ledger_stage_complete_rejects_changed_drafts_after_freeze(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())
    freeze_claim_ledger_transaction(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload(duplicate=True))

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert "source hash does not match" in str(excinfo.value)


def test_state_freeze_claim_ledger_cli_json(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(ws, "claim_drafts.json", _valid_claim_drafts_payload())

    rc = main([
        "state",
        "freeze-claim-ledger",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["claim_ledger_freeze"]["status"] == "frozen"
    assert payload["transaction"]["decision"] == "freeze_claim_ledger"
    assert (_intermediate(ws) / "claim_ledger.json").exists()


def test_state_freeze_claim_ledger_cli_json_explains_invalid_claim_type(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                        "source_title": "ExampleCo Demo Facility",
                        "source_category": "news_media",
                        "claim_type": "unsupported",
                    }
                ],
            }
        )
        + "\n",
    )

    rc = main([
        "state",
        "freeze-claim-ledger",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_CLAIM_DRAFT_CONTRACT_INVALID"
    diagnostic = payload["details"]["diagnostics"][0]
    assert diagnostic["field"] == "drafts[0].claim_type"
    assert diagnostic["allowed_values"] == [
        "date",
        "fact",
        "forecast",
        "interpretation",
        "number",
        "risk",
    ]
    assert not (_intermediate(ws) / "claim_ledger.json").exists()


def test_state_freeze_claim_ledger_human_output_explains_forbidden_claim_id(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "claim_id": "CL-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                    }
                ],
            }
        )
        + "\n",
    )

    rc = main([
        "state",
        "freeze-claim-ledger",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
    ])

    assert rc == 1
    output = capsys.readouterr().out
    assert "drafts[0].claim_id" in output
    assert "forbidden_fields: claim_id" in output
    assert "Python assigns CL-####" in output
    assert not (_intermediate(ws) / "claim_ledger.json").exists()


def test_claim_ledger_stage_complete_accepts_valid_flat_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _freeze_claim_ledger_fixture(ws)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )

    assert state["workflow_state"]["current_stage"] == "analyst"
    registry = state["artifact_registry"]["artifacts"]
    assert registry["claim_ledger"]["status"] == "valid"
    assert registry["claim_ledger"]["validation_result"] == "valid_claim_ledger_schema"
    assert registry["claim_ledger"]["sha256"]


def test_claim_ledger_stage_complete_records_valid_claim_drafts_after_freeze(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _freeze_claim_ledger_fixture(ws)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )

    assert state["workflow_state"]["current_stage"] == "analyst"
    registry = state["artifact_registry"]["artifacts"]
    assert registry["claim_ledger"]["status"] == "valid"
    assert registry["claim_ledger"]["validation_result"] == "valid_claim_ledger_schema"
    assert registry["claim_drafts"]["status"] == "valid"
    assert registry["claim_drafts"]["validation_result"] == "valid_claim_drafts_schema"


def test_state_check_marks_claim_drafts_with_claim_id_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "claim_id": "CL-001",
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                    }
                ],
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    record = state["artifact_registry"]["artifacts"]["claim_drafts"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "claim_drafts_schema_error:drafts[0].claim_id"


def test_state_check_marks_claim_drafts_omitted_source_type_without_url_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                    }
                ],
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    record = state["artifact_registry"]["artifacts"]["claim_drafts"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "claim_drafts_schema_error:drafts[0].source_category"


def test_state_check_marks_claim_drafts_with_nested_claim_id_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "statement": "ExampleCo opened a demo facility.",
                        "source_id": "SRC-001",
                        "evidence_text": "Example evidence.",
                        "metadata": {"claim_id": "CL-001"},
                    }
                ],
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    record = state["artifact_registry"]["artifacts"]["claim_drafts"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "claim_drafts_schema_error:drafts[0].metadata.claim_id"


def test_state_check_marks_claim_drafts_with_non_string_required_fields_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "claim_drafts.json",
        json.dumps(
            {
                "schema_version": "mabw.claim_drafts.v1",
                "drafts": [
                    {
                        "statement": 123,
                        "source_id": ["SRC-001"],
                        "evidence_text": {"text": "Example evidence."},
                    }
                ],
            }
        )
        + "\n",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    record = state["artifact_registry"]["artifacts"]["claim_drafts"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == "claim_drafts_schema_error:drafts[0].statement"


def test_state_check_validates_present_atomic_claim_graph_against_claim_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-0001"))

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "valid"
    assert record["required"] is False
    assert record["validation_result"] == "experimental_atomic_claim_graph_schema"


def test_state_check_validates_complete_typed_atomic_claim_graph(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        _claim_ledger_payload(
            [
                _claim_dict("CL-0001", claim_type="number"),
                _claim_dict("CL-0002", claim_type="forecast"),
                _claim_dict("CL-0003", claim_type="risk"),
                _claim_dict("CL-0004", limitations=["Evidence does not establish adoption."]),
                _claim_dict("CL-0005", claim_type="date"),
            ]
        ),
    )
    _write_json_artifact(
        ws,
        "atomic_claim_graph.json",
        _atomic_claim_graph_payload(
            {
                "CL-0001": ["numeric_fact"],
                "CL-0002": ["forward_looking_inference"],
                "CL-0003": ["risk_or_limitation"],
                "CL-0004": ["risk_or_limitation"],
                "CL-0005": ["observed_fact"],
            }
        ),
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "experimental_atomic_claim_graph_schema"


def test_state_check_marks_atomic_claim_graph_unknown_claim_id_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-9999", "AC-9999-01"))

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == (
        "atomic_claim_graph_validation_error:unknown_claim_reference:CL-9999"
    )


def test_state_check_marks_atomic_claim_graph_missing_claim_coverage_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        _claim_ledger_payload(
            [
                _claim_dict("CL-0003"),
                _claim_dict("CL-0002"),
                _claim_dict("CL-0001"),
            ]
        ),
    )
    _write_json_artifact(ws, "atomic_claim_graph.json", _atomic_claim_graph_payload({"CL-0001": ["observed_fact"]}))

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "atomic_claim_graph_validation_error:missing_claim_coverage:CL-0002"


def test_state_check_marks_atomic_claim_graph_duplicate_claim_coverage_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _claim_ledger_payload([_claim_dict("CL-0001")]))
    graph = {
        "schema_version": "mabw.atomic_claim_graph.v1",
        "claims": [
            {
                "claim_id": "CL-0001",
                "atoms": [
                    {
                        "atom_id": "AC-0001-01",
                        "text": "First atom.",
                        "claim_role": "observed_fact",
                        "materiality": "high",
                    }
                ],
            },
            {
                "claim_id": "CL-0001",
                "atoms": [
                    {
                        "atom_id": "AC-0001-02",
                        "text": "Second atom.",
                        "claim_role": "observed_fact",
                        "materiality": "medium",
                    }
                ],
            },
        ],
    }
    _write_json_artifact(ws, "atomic_claim_graph.json", json.dumps(graph) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "atomic_claim_graph_validation_error:duplicate_claim_coverage:CL-0001"


def test_state_check_marks_atomic_claim_graph_cross_claim_edge_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                },
                {
                    "claim_id": "CL-0002",
                    "statement": "BetaCo expanded module output.",
                    "source_id": "SRC-002",
                    "evidence_text": "Second example evidence.",
                },
            ]
        )
        + "\n",
    )
    _write_json_artifact(ws, "atomic_claim_graph.json", _cross_claim_edge_atomic_claim_graph_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "atomic_claim_graph_schema_error:claims[0].edges[0].to"


def test_state_check_validates_present_evidence_span_registry(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    source_text = "Intro.\nExampleCo said module shipments reached 12 MW in Q2.\nOutro.\n"
    _write_source_text(ws, text=source_text)
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(include_offsets=True, source_text=source_text),
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "valid"
    assert record["required"] is False
    assert record["validation_result"] == "experimental_evidence_span_registry_schema"


def test_state_check_validates_present_claim_support_matrix_schema(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    _write_json_artifact(ws, "claim_support_matrix.json", _valid_claim_support_matrix_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "valid"
    assert record["required"] is False
    assert record["validation_result"] == "experimental_claim_support_matrix_schema"


def test_state_check_validates_present_semantic_assessment_report_schema(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    _write_json_artifact(ws, "semantic_assessment_report.json", _valid_semantic_assessment_report_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "valid"
    assert record["required"] is False
    assert record["validation_result"] == "experimental_semantic_assessment_report_schema"


def test_state_check_marks_semantic_assessment_report_missing_claim_ledger_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "semantic_assessment_report.json", _valid_semantic_assessment_report_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert record["validation_result"] == "semantic_assessment_report_validation_error:claim_ledger_missing"


def test_state_check_marks_semantic_assessment_report_runtime_invalid_evidence_registry_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-0001", "AC-0001-01"))
    _write_json_artifact(ws, "evidence_span_registry.json", _valid_evidence_span_registry_payload())
    _write_json_artifact(ws, "semantic_assessment_report.json", _valid_semantic_assessment_report_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    evidence_record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]
    report_record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert evidence_record["status"] == "invalid"
    assert evidence_record["validation_result"] == "evidence_span_registry_validation_error:source_path_missing:SRC-001"
    assert report_record["status"] == "invalid"
    assert report_record["required"] is False
    assert (
        report_record["validation_result"]
        == "semantic_assessment_report_validation_error:evidence_span_registry_invalid:source_path_missing:SRC-001"
    )


def test_state_check_marks_semantic_assessment_report_unknown_claim_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_semantic_assessment_report_payload())
    payload["rows"][0]["claim_id"] = "CL-9999"
    payload["rows"][0]["atom_id"] = "AC-9999-01"
    _write_json_artifact(ws, "semantic_assessment_report.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "semantic_assessment_report_validation_error:unknown_claim_reference:CL-9999"


def test_state_check_marks_semantic_assessment_report_unknown_atom_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_semantic_assessment_report_payload())
    payload["rows"][0]["atom_id"] = "AC-0001-99"
    _write_json_artifact(ws, "semantic_assessment_report.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "semantic_assessment_report_validation_error:unknown_atom_reference:AC-0001-99"


def test_state_check_marks_semantic_assessment_report_unknown_span_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_semantic_assessment_report_payload())
    payload["rows"][0]["evidence_span_id"] = "ESP-999-01"
    _write_json_artifact(ws, "semantic_assessment_report.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "semantic_assessment_report_validation_error:unknown_evidence_span_reference:ESP-999-01"


def test_state_check_marks_semantic_assessment_report_unknown_candidate_span_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_semantic_assessment_report_payload())
    payload["rows"][0].pop("evidence_span_id")
    payload["rows"][0]["candidate_evidence_span_ids"] = ["ESP-001-01", "ESP-999-01"]
    _write_json_artifact(ws, "semantic_assessment_report.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "semantic_assessment_report_validation_error:unknown_evidence_span_reference:ESP-999-01"


@pytest.mark.parametrize(
    ("uncertainty", "disagreement"),
    [
        ("high", "none"),
        ("unknown", "none"),
        ("low", "high"),
        ("low", "unknown"),
    ],
)
def test_state_check_marks_llm_only_high_materiality_unresolved_semantic_row_invalid_without_adjudication(
    tmp_path,
    uncertainty,
    disagreement,
):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_semantic_assessment_report_payload())
    payload["assessors"][0]["assessment_method"] = "llm_only"
    payload["rows"][0]["assessment_method"] = "llm_only"
    payload["rows"][0]["uncertainty"] = uncertainty
    payload["rows"][0]["disagreement"] = disagreement
    payload["rows"][0]["requires_human_adjudication"] = False
    _write_json_artifact(ws, "semantic_assessment_report.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert (
        record["validation_result"]
        == "semantic_assessment_report_validation_error:llm_only_high_materiality_requires_human_adjudication:SAR-0001"
    )


def test_state_check_rejects_semantic_assessment_row_method_mismatch(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_semantic_assessment_report_payload())
    payload["assessors"][0]["assessment_method"] = "llm_only"
    payload["rows"][0]["assessment_method"] = "human"
    payload["rows"][0]["uncertainty"] = "high"
    payload["rows"][0]["requires_human_adjudication"] = False
    _write_json_artifact(ws, "semantic_assessment_report.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "semantic_assessment_report_schema_error:rows[0].assessment_method"


def test_semantic_assessment_runtime_validator_derives_method_from_declared_assessor():
    report_payload = json.loads(_valid_semantic_assessment_report_payload())
    report_payload["assessors"][0]["assessment_method"] = "llm_only"
    report_payload["rows"][0]["assessment_method"] = "human"
    report_payload["rows"][0]["uncertainty"] = "high"
    report_payload["rows"][0]["requires_human_adjudication"] = False

    reason = validate_semantic_assessment_report_against_artifacts(
        report_payload=report_payload,
        ledger_claims=json.loads(_valid_claim_ledger_payload("CL-0001")),
        graph_payload=json.loads(_valid_atomic_claim_graph_payload("CL-0001", "AC-0001-01")),
        evidence_span_registry_payload=json.loads(_valid_evidence_span_registry_payload()),
    )

    assert reason == "assessment_method_mismatch:SAR-0001:ASR-001:human:llm_only"


def test_state_check_marks_invalid_semantic_assessment_report_schema_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    payload = json.loads(_valid_semantic_assessment_report_payload())
    payload["rows"][0].pop("evidence_span_id")
    _write_json_artifact(ws, "semantic_assessment_report.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["semantic_assessment_report"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert record["validation_result"] == "semantic_assessment_report_schema_error:rows[0].evidence_span_binding"


def test_state_check_marks_claim_support_matrix_missing_claim_ledger_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-0001", "AC-0001-01"))
    source_text = "Intro.\nExampleCo said module shipments reached 12 MW in Q2.\nOutro.\n"
    _write_source_text(ws, text=source_text)
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(include_offsets=True, source_text=source_text),
    )
    _write_json_artifact(ws, "claim_support_matrix.json", _valid_claim_support_matrix_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert record["validation_result"] == "claim_support_matrix_validation_error:claim_ledger_missing"


def test_state_check_marks_claim_support_matrix_runtime_invalid_claim_ledger_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    duplicate_ledger = [
        {
            "claim_id": "CL-0001",
            "statement": "ExampleCo opened a demo facility.",
            "source_id": "SRC-001",
            "evidence_text": "Example evidence.",
        },
        {
            "claim_id": "CL-0001",
            "statement": "ExampleCo reported duplicate evidence.",
            "source_id": "SRC-001",
            "evidence_text": "Duplicate evidence.",
        },
    ]
    _write_json_artifact(ws, "claim_ledger.json", _claim_ledger_payload(duplicate_ledger))
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-0001", "AC-0001-01"))
    source_text = "Intro.\nExampleCo said module shipments reached 12 MW in Q2.\nOutro.\n"
    _write_source_text(ws, text=source_text)
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(include_offsets=True, source_text=source_text),
    )
    _write_json_artifact(ws, "claim_support_matrix.json", _valid_claim_support_matrix_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    ledger_record = state["artifact_registry"]["artifacts"]["claim_ledger"]
    matrix_record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert ledger_record["status"] == "invalid"
    assert ledger_record["validation_result"] == "claim_ledger_schema_error:duplicate_claim_id:CL-0001"
    assert matrix_record["status"] == "invalid"
    assert matrix_record["required"] is False
    assert (
        matrix_record["validation_result"]
        == "claim_support_matrix_validation_error:claim_ledger_invalid:duplicate_claim_id:CL-0001"
    )


def test_state_check_marks_claim_support_matrix_runtime_invalid_atomic_graph_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-9999", "AC-9999-01"))
    source_text = "Intro.\nExampleCo said module shipments reached 12 MW in Q2.\nOutro.\n"
    _write_source_text(ws, text=source_text)
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(include_offsets=True, source_text=source_text),
    )
    _write_json_artifact(ws, "claim_support_matrix.json", _valid_claim_support_matrix_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    graph_record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]
    matrix_record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert graph_record["status"] == "invalid"
    assert graph_record["validation_result"] == "atomic_claim_graph_validation_error:unknown_claim_reference:CL-9999"
    assert matrix_record["status"] == "invalid"
    assert matrix_record["required"] is False
    assert (
        matrix_record["validation_result"]
        == "claim_support_matrix_validation_error:atomic_claim_graph_invalid:unknown_claim_reference:CL-9999"
    )


def test_state_check_marks_claim_support_matrix_runtime_invalid_evidence_span_registry_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-0001", "AC-0001-01"))
    _write_json_artifact(ws, "evidence_span_registry.json", _valid_evidence_span_registry_payload())
    _write_json_artifact(ws, "claim_support_matrix.json", _valid_claim_support_matrix_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    evidence_record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]
    matrix_record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert evidence_record["status"] == "invalid"
    assert evidence_record["validation_result"] == "evidence_span_registry_validation_error:source_path_missing:SRC-001"
    assert matrix_record["status"] == "invalid"
    assert matrix_record["required"] is False
    assert (
        matrix_record["validation_result"]
        == "claim_support_matrix_validation_error:evidence_span_registry_invalid:source_path_missing:SRC-001"
    )


def test_state_check_marks_claim_support_matrix_unknown_atom_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_claim_support_matrix_payload())
    payload["rows"][0]["atom_id"] = "AC-0001-99"
    _write_json_artifact(ws, "claim_support_matrix.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert record["validation_result"] == "claim_support_matrix_validation_error:unknown_atom_reference:AC-0001-99"


def test_state_check_marks_claim_support_matrix_unknown_span_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_claim_support_matrix_payload())
    payload["rows"][0]["evidence_span_id"] = "ESP-001-99"
    _write_json_artifact(ws, "claim_support_matrix.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert (
        record["validation_result"]
        == "claim_support_matrix_validation_error:unknown_evidence_span_reference:ESP-001-99"
    )


def test_state_check_marks_claim_support_matrix_missing_high_materiality_atom_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    graph = json.loads(_valid_atomic_claim_graph_payload("CL-0001", "AC-0001-01"))
    graph["claims"][0]["atoms"].append(
        {
            "atom_id": "AC-0001-02",
            "text": "The demo facility is strategically important.",
            "claim_role": "trend_interpretation",
            "materiality": "high",
        }
    )
    _write_json_artifact(ws, "atomic_claim_graph.json", json.dumps(graph) + "\n")
    source_text = "Intro.\nExampleCo said module shipments reached 12 MW in Q2.\nOutro.\n"
    _write_source_text(ws, text=source_text)
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(include_offsets=True, source_text=source_text),
    )
    _write_json_artifact(ws, "claim_support_matrix.json", _valid_claim_support_matrix_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert (
        record["validation_result"]
        == "claim_support_matrix_validation_error:high_materiality_atom_missing_row:AC-0001-02"
    )


def test_state_check_marks_claim_support_matrix_support_label_without_span_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_valid_claim_support_matrix_dependencies(ws)
    payload = json.loads(_valid_claim_support_matrix_payload())
    payload["rows"][0]["evidence_span_id"] = None
    payload["rows"][0]["support_label"] = "direct_support"
    _write_json_artifact(ws, "claim_support_matrix.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert record["validation_result"] == "claim_support_matrix_validation_error:support_label_requires_span:CSM-0001"


def test_state_check_marks_malformed_claim_support_matrix_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_support_matrix.json", json.dumps({"rows": []}) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert record["validation_result"] == "claim_support_matrix_schema_error:schema_version"


def test_state_check_marks_duplicate_claim_support_matrix_relation_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    payload = json.loads(_valid_claim_support_matrix_payload())
    duplicate = dict(payload["rows"][0])
    duplicate["row_id"] = "CSM-0002"
    duplicate["support_label"] = "unsupported"
    duplicate["support_strength"] = "none"
    duplicate["required_action"] = "block_release"
    payload["rows"].append(duplicate)
    _write_json_artifact(ws, "claim_support_matrix.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["claim_support_matrix"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert record["validation_result"] == "claim_support_matrix_schema_error:rows[1].evidence_span_id"


def test_state_check_marks_url_only_evidence_span_registry_runtime_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "evidence_span_registry.json", _valid_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["required"] is False
    assert (
        record["validation_result"]
        == "evidence_span_registry_validation_error:source_path_missing:SRC-001"
    )


def test_state_check_marks_evidence_span_registry_hash_mismatch_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    payload = json.loads(_valid_evidence_span_registry_payload())
    payload["sources"][0]["spans"][0]["hash"] = _span_hash("different excerpt")
    _write_json_artifact(ws, "evidence_span_registry.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_schema_error:sources[0].spans[0].hash"


def test_state_check_marks_evidence_span_registry_missing_source_date_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    payload = json.loads(_valid_evidence_span_registry_payload())
    payload["sources"][0].pop("published_at")
    _write_json_artifact(ws, "evidence_span_registry.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_schema_error:sources[0].source_date"


@pytest.mark.parametrize(
    "source_path",
    [
        "/tmp/source-001.md",
        "../input/sources/source-001.md",
        "input/source-001.md",
        "input/sources/../source-001.md",
        r"input\sources\source-001.md",
    ],
)
def test_state_check_marks_evidence_span_registry_unsafe_source_path_invalid(tmp_path, source_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_source_text(ws)
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(source_path=source_path),
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_path_unsafe:SRC-001"


def test_state_check_marks_evidence_span_registry_symlink_escape_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    outside = tmp_path / "outside-source.md"
    outside.write_text("ExampleCo said module shipments reached 12 MW in Q2.\n", encoding="utf-8")
    source_path = ws / "input" / "sources" / "source-001.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation not available: {exc}")
    _write_json_artifact(ws, "evidence_span_registry.json", _source_backed_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_path_unsafe:SRC-001"


def test_state_check_marks_evidence_span_registry_symlinked_source_root_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    outside_sources = tmp_path / "outside-sources"
    outside_sources.mkdir()
    (outside_sources / "source-001.md").write_text(
        "ExampleCo said module shipments reached 12 MW in Q2.\n",
        encoding="utf-8",
    )
    source_root = ws / "input" / "sources"
    try:
        source_root.symlink_to(outside_sources, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation not available: {exc}")
    _write_json_artifact(ws, "evidence_span_registry.json", _source_backed_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_path_unsafe:SRC-001"


def test_state_check_marks_evidence_span_registry_symlinked_input_root_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    outside_input = tmp_path / "outside-input"
    outside_sources = outside_input / "sources"
    outside_sources.mkdir(parents=True)
    (outside_sources / "source-001.md").write_text(
        "ExampleCo said module shipments reached 12 MW in Q2.\n",
        encoding="utf-8",
    )
    shutil.rmtree(ws / "input")
    try:
        (ws / "input").symlink_to(outside_input, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation not available: {exc}")
    _write_json_artifact(ws, "evidence_span_registry.json", _source_backed_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_path_unsafe:SRC-001"


def test_state_check_marks_evidence_span_registry_non_evidence_source_path_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_source_text(ws, name="README.md")
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(source_path="input/sources/README.md"),
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_path_not_evidence:SRC-001"


def test_state_check_marks_evidence_span_registry_missing_source_file_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "evidence_span_registry.json", _source_backed_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_file_missing:SRC-001"


def test_state_check_marks_evidence_span_registry_unreadable_source_file_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    source_path = ws / "input" / "sources" / "source-001.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"\xff\xfe\xfd")
    _write_json_artifact(ws, "evidence_span_registry.json", _source_backed_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_file_unreadable:SRC-001"


def test_state_check_marks_evidence_span_registry_missing_excerpt_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_source_text(ws, text="Different source text.\n")
    _write_json_artifact(ws, "evidence_span_registry.json", _source_backed_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:span_excerpt_not_found:ESP-001-01"


def test_state_check_allows_duplicate_evidence_span_excerpt_without_offsets(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    _write_source_text(ws, text=f"{raw_excerpt}\nRepeated.\n{raw_excerpt}\n")
    _write_json_artifact(ws, "evidence_span_registry.json", _source_backed_evidence_span_registry_payload())

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "experimental_evidence_span_registry_schema"


def test_state_check_marks_evidence_span_registry_incomplete_offset_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_source_text(ws)
    payload = json.loads(_source_backed_evidence_span_registry_payload())
    payload["sources"][0]["spans"][0]["char_start"] = 7
    _write_json_artifact(ws, "evidence_span_registry.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:span_offset_incomplete:ESP-001-01"


def test_state_check_marks_evidence_span_registry_offset_mismatch_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_source_text(ws)
    payload = json.loads(_source_backed_evidence_span_registry_payload())
    payload["sources"][0]["spans"][0]["char_start"] = 0
    payload["sources"][0]["spans"][0]["char_end"] = len(payload["sources"][0]["spans"][0]["raw_excerpt"])
    _write_json_artifact(ws, "evidence_span_registry.json", json.dumps(payload) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:span_offset_mismatch:ESP-001-01"


def test_state_check_validates_evidence_span_registry_against_json_content(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    source_path = ws / "input" / "sources" / "source-001.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"Intro.\n{raw_excerpt}\nOutro.\n"
    source_path.write_text(
        json.dumps({"source_id": "SRC-001", "content": content}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(
            source_path="input/sources/source-001.json",
            include_offsets=True,
            source_text=content,
        ),
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "experimental_evidence_span_registry_schema"


def test_state_check_marks_evidence_span_registry_json_source_id_mismatch_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    source_path = ws / "input" / "sources" / "source-001.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        json.dumps({"source_id": "SRC-999", "content": raw_excerpt}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_json_artifact(
        ws,
        "evidence_span_registry.json",
        _source_backed_evidence_span_registry_payload(source_path="input/sources/source-001.json"),
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "evidence_span_registry_validation_error:source_id_mismatch:SRC-001"


@pytest.mark.parametrize(
    ("claim", "roles", "reason"),
    [
        (
            _claim_dict("CL-0001", claim_type="number"),
            ["observed_fact"],
            "claim_type_number_missing_numeric_fact:CL-0001",
        ),
        (
            _claim_dict("CL-0002", claim_type="forecast"),
            ["observed_fact"],
            "claim_type_forecast_missing_forward_looking_inference:CL-0002",
        ),
        (
            _claim_dict("CL-0003", claim_type="risk"),
            ["observed_fact"],
            "claim_type_risk_missing_risk_atom:CL-0003",
        ),
        (
            _claim_dict("CL-0004", limitations=["Source does not establish demand."]),
            ["observed_fact"],
            "claim_limitations_missing_risk_atom:CL-0004",
        ),
    ],
)
def test_state_check_marks_atomic_claim_graph_type_consistency_invalid(
    tmp_path,
    claim: dict[str, object],
    roles: list[str],
    reason: str,
):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    claim_id = str(claim["claim_id"])
    _write_json_artifact(ws, "claim_ledger.json", _claim_ledger_payload([claim]))
    _write_json_artifact(ws, "atomic_claim_graph.json", _atomic_claim_graph_payload({claim_id: roles}))

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == f"atomic_claim_graph_validation_error:{reason}"


def test_state_check_does_not_require_numeric_atom_for_date_claim(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _claim_ledger_payload([_claim_dict("CL-0001", claim_type="date")]))
    _write_json_artifact(ws, "atomic_claim_graph.json", _atomic_claim_graph_payload({"CL-0001": ["observed_fact"]}))

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "valid"
    assert record["validation_result"] == "experimental_atomic_claim_graph_schema"


def test_state_check_marks_atomic_claim_graph_missing_claim_ledger_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "atomic_claim_graph.json", _valid_atomic_claim_graph_payload("CL-0001"))

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "atomic_claim_graph_validation_error:claim_ledger_missing"


def test_state_check_marks_malformed_atomic_claim_graph_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload("CL-0001"))
    _write_json_artifact(ws, "atomic_claim_graph.json", json.dumps({"claims": []}) + "\n")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["atomic_claim_graph"]

    assert record["status"] == "invalid"
    assert record["validation_result"] == "atomic_claim_graph_schema_error:schema_version"


def test_claim_ledger_stage_complete_rejects_nested_meta_ai_shape(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "CL-001",
                        "claim_text": "ExampleCo opened a demo facility.",
                        "evidence": {
                            "source_id": "SRC-001",
                            "source_url": "https://example.com/source",
                            "text": "Example evidence.",
                        },
                        "metadata": {"confidence": "high"},
                    }
                ]
            }
        )
        + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error:claim[0].statement" in str(excinfo.value)


def test_claim_ledger_stage_complete_rejects_missing_evidence_text(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        json.dumps(
            [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                }
            ]
        )
        + "\n",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="claim-ledger",
            reason="claim ledger complete",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "claim_ledger_schema_error:claim[0].evidence_text" in str(excinfo.value)


def test_state_check_marks_malformed_claim_ledger_invalid(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json", '{"not_claims": []}\n')

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    ledger_record = state["artifact_registry"]["artifacts"]["claim_ledger"]
    assert ledger_record["status"] == "invalid"
    assert ledger_record["validation_result"].startswith("claim_ledger_schema_error:")
    assert state["workflow_state"]["blocked"] is True


def test_state_check_blocks_modified_frozen_claim_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _freeze_claim_ledger_fixture(ws)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )
    registry_before = _state_file(ws, "artifact_registry").read_bytes()
    ledger = json.loads((_intermediate(ws) / "claim_ledger.json").read_text(encoding="utf-8"))
    ledger[0].setdefault("metadata", {})["published_at"] = "2026-06-18"
    (_intermediate(ws) / "claim_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Frozen artifact" in str(excinfo.value)
    assert "owner stage 'claim-ledger'" in str(excinfo.value)
    assert "Do not manually update artifact_registry.json" in str(excinfo.value)
    assert "Do not hand-edit metadata or synchronize hashes manually" in str(excinfo.value)
    assert "deterministic metadata enrichment transaction" in str(excinfo.value)
    assert _state_file(ws, "artifact_registry").read_bytes() == registry_before
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "contaminated"
    assert workflow["run_integrity"]["reference_eligible"] is False
    assert workflow["run_integrity"]["reasons"][0]["reason_code"] == "frozen_artifact_changed"
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert len(contamination_events) == 1
    assert contamination_events[0]["metadata"]["reason_code"] == "frozen_artifact_changed"


def test_state_check_rejects_malformed_registry_before_frozen_integrity_laundering(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _freeze_claim_ledger_fixture(ws)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )
    registry_path = _state_file(ws, "artifact_registry")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["artifacts"] = "not-an-object"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workflow_path = _state_file(ws, "workflow_state")
    event_log_path = _state_file(ws, "event_log")
    before_workflow = workflow_path.read_bytes()
    before_events = event_log_path.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "artifact_registry.json artifacts must be an object" in str(excinfo.value)
    assert workflow_path.read_bytes() == before_workflow
    assert event_log_path.read_bytes() == before_events
    assert json.loads(registry_path.read_text(encoding="utf-8"))["artifacts"] == "not-an-object"


def test_frozen_artifact_interpreter_rejects_malformed_producer_stage_status():
    verdict = interpret_frozen_artifact_integrity(
        old_registry={
            "artifacts": {
                "claim_ledger": {
                    "artifact_id": "claim_ledger",
                    "path": "output/intermediate/claim_ledger.json",
                    "sha256": "a" * 64,
                }
            }
        },
        registry={
            "artifacts": {
                "claim_ledger": {
                    "artifact_id": "claim_ledger",
                    "path": "output/intermediate/claim_ledger.json",
                    "status": "valid",
                    "sha256": "a" * 64,
                }
            }
        },
        workflow={"stage_statuses": {"claim-ledger": {"status": "finished"}}},
        artifacts=[{
            "artifact_id": "claim_ledger",
            "path": "output/intermediate/claim_ledger.json",
            "producer_stage": "claim-ledger",
        }],
        stages=[{"stage_id": "claim-ledger", "produces": ["claim_ledger"]}],
    )

    assert verdict.kind == "degraded"
    assert verdict.contaminates_run is False
    assert "producer stage 'claim-ledger' status is malformed" in verdict.reasons[0]


def test_state_check_contamination_event_failure_rolls_back_workflow(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _freeze_claim_ledger_fixture(ws)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )
    _write_json_artifact(
        ws,
        "claim_ledger.json",
        _valid_claim_ledger_payload(
            claim_id="CL-002",
            statement="ExampleCo changed the already completed ledger.",
        ),
    )
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    _fail_appending_event_type(monkeypatch, "run_integrity_contaminated")

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_PARTIAL_WRITE
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_state_check_accepts_unchanged_frozen_claim_ledger(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _freeze_claim_ledger_fixture(ws)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["artifact_registry"]["artifacts"]["claim_ledger"]["sha256"]


def test_editor_stage_complete_can_rewrite_audited_brief(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    analyst_text = "# Brief\n\nAnalyst draft. [src:CL-001]\n"
    audited.write_text(analyst_text, encoding="utf-8")
    analyst_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    analyst_audited_record = analyst_state["artifact_registry"]["artifacts"]["audited_brief"]
    analyst_sha = analyst_audited_record["sha256"]
    assert analyst_audited_record["producer_stage"] == "editor"
    snapshot = _intermediate(ws) / "analyst_draft_snapshot.md"
    assert snapshot.read_text(encoding="utf-8") == analyst_text
    snapshot_record = analyst_state["artifact_registry"]["artifacts"]["analyst_draft_snapshot"]
    assert snapshot_record["producer_stage"] == "analyst"
    assert snapshot_record["producer_role"] == "python_tool"
    assert snapshot_record["sha256"] == _sha256_file(snapshot)

    audited.write_text("# Brief\n\nEditor-polished draft. [src:CL-001]\n", encoding="utf-8")
    editor_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )

    editor_sha = editor_state["artifact_registry"]["artifacts"]["audited_brief"]["sha256"]
    assert editor_state["workflow_state"]["current_stage"] == "auditor"
    assert editor_sha
    assert editor_sha != analyst_sha
    assert snapshot.read_text(encoding="utf-8") == analyst_text
    assert editor_state["artifact_registry"]["artifacts"]["analyst_draft_snapshot"]["sha256"] == _sha256_file(snapshot)
    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert checked["workflow_state"]["run_integrity"]["status"] == "clean"


def test_audited_brief_mutation_after_editor_complete_contaminates(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor-polished draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )

    audited.write_text("# Brief\n\nChanged after editor completion. [src:CL-001]\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "owner stage 'editor'" in str(excinfo.value)
    assert "Do not manually update artifact_registry.json" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "contaminated"
    assert workflow["run_integrity"]["reasons"][0]["reason_code"] == "frozen_artifact_changed"


def test_analyst_snapshot_mutation_after_analyst_complete_contaminates(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    snapshot = _intermediate(ws) / "analyst_draft_snapshot.md"
    snapshot.write_text("# Brief\n\nIllegally changed analyst snapshot. [src:CL-001]\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "owner stage 'analyst'" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["run_integrity"]["status"] == "contaminated"
    assert workflow["run_integrity"]["reasons"][0]["reason_code"] == "frozen_artifact_changed"


def test_repair_start_records_editor_owner_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    before_registry = _state_file(ws, "artifact_registry").read_bytes()
    _write_editor_repair_gate_report(ws)

    state = start_repair_transaction(workspace=ws, repo_workdir=ROOT)

    workflow = state["workflow_state"]
    repair = workflow["active_repair"]
    assert workflow["current_stage"] == "editor"
    assert workflow["run_integrity"]["status"] == "clean"
    assert workflow["run_integrity"]["reference_eligible"] is True
    assert workflow["run_integrity"].get("reasons", []) == []
    assert repair["repair_owner"] == "editor"
    assert repair["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert repair["source"]["finding_id"] == "QG_EDITOR_REPAIR_001"
    assert repair["must_rerun_from"] == "auditor"
    assert _state_file(ws, "artifact_registry").read_bytes() == before_registry
    events = _event_records(ws)
    assert events[-1]["event_type"] == "repair_started"
    assert events[-1]["metadata"]["repair_owner"] == "editor"
    assert [
        event for event in events
        if event["event_type"] == "run_integrity_contaminated"
    ] == []


def test_stage_complete_fails_while_active_repair_open_without_events(tmp_path):
    ws = _start_active_editor_repair(tmp_path)
    before_events = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="should not advance during active repair",
        )

    assert excinfo.value.error_code == runtime_state.E_ACTIVE_REPAIR_OPEN
    assert "repair complete" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["active_repair"]["repair_owner"] == "editor"
    assert workflow["run_integrity"]["status"] == "clean"
    assert _state_file(ws, "event_log").read_bytes() == before_events
    assert [event for event in _event_records(ws) if event["event_type"] == "run_integrity_contaminated"] == []


def test_finalize_complete_fails_while_active_repair_open_without_events(tmp_path):
    ws = _start_active_editor_repair(tmp_path)
    before_events = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="should not finalize during active repair",
        )

    assert excinfo.value.error_code == runtime_state.E_ACTIVE_REPAIR_OPEN
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["active_repair"]["transaction_id"]
    assert _state_file(ws, "event_log").read_bytes() == before_events
    assert [event for event in _event_records(ws) if event["event_type"] == "decision_recorded"][-1]["stage_id"] == "editor"


def test_repair_start_applies_non_reference_integrity_effect_for_frozen_artifact_change(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor-polished draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    audited.write_text("# Brief\n\nDirect post-freeze edit. [src:CL-001]\n", encoding="utf-8")

    state = start_repair_transaction(workspace=ws, repo_workdir=ROOT)

    workflow = state["workflow_state"]
    integrity = workflow["run_integrity"]
    assert integrity["status"] == "contaminated"
    assert integrity["reference_eligible"] is False
    assert integrity["clean_single_shot"] is False
    assert integrity["reasons"][0]["reason_code"] == "frozen_artifact_changed"
    assert integrity["reasons"][0]["artifact_id"] == "audited_brief"
    assert integrity["reasons"][0]["metadata"]["repair_transaction_id"]
    assert workflow["active_repair"]["run_integrity_effect"]["reference_eligible"] is False
    events = _event_records(ws)
    assert events[-2]["event_type"] == "repair_started"
    assert events[-1]["event_type"] == "run_integrity_contaminated"
    assert events[-1]["metadata"]["reason_code"] == "frozen_artifact_changed"
    assert events[-1]["metadata"]["artifact_id"] == "audited_brief"

    audited.write_text("# Brief\n\nOwner repair edit for local delivery. [src:CL-001]\n", encoding="utf-8")
    completed = complete_repair_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="editor repaired audited brief from deterministic route",
    )

    completed_integrity = completed["workflow_state"]["run_integrity"]
    assert completed_integrity["status"] == "contaminated"
    assert completed_integrity["reference_eligible"] is False
    assert "active_repair" not in completed["workflow_state"]


def test_repair_start_contamination_event_failure_rolls_back_control_files(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor-polished draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    audited.write_text("# Brief\n\nDirect post-freeze edit. [src:CL-001]\n", encoding="utf-8")
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    _fail_appending_event_type(monkeypatch, "run_integrity_contaminated")

    with pytest.raises(RuntimeStateError) as excinfo:
        start_repair_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_PARTIAL_WRITE
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_repair_start_rejects_finalized_workflow_without_mutating_state(tmp_path):
    ws = _write_workspace(tmp_path)
    _complete_finalized_workspace(ws)
    _write_editor_repair_gate_report(ws)
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        start_repair_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_ILLEGAL_TRANSITION
    assert "finalized workflow" in str(excinfo.value)
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_repair_start_rejects_stale_report_from_non_current_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _write_editor_repair_gate_report(ws)
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        start_repair_transaction(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_ILLEGAL_TRANSITION
    assert "source stage does not match" in str(excinfo.value)
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_repair_complete_refreezes_allowed_editor_artifact_and_invalidates_downstream(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    editor_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    old_sha = editor_state["artifact_registry"]["artifacts"]["audited_brief"]["sha256"]
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    audited.write_text("# Brief\n\nEditor repaired draft. [src:CL-001]\n", encoding="utf-8")

    state = complete_repair_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="editor repaired audited brief from deterministic route",
    )

    workflow = state["workflow_state"]
    assert "active_repair" not in workflow
    assert workflow["current_stage"] == "auditor"
    assert workflow["stage_statuses"]["editor"]["status"] == "complete"
    assert workflow["stage_statuses"]["editor"]["metadata"]["repaired"] is True
    assert workflow["stage_statuses"]["auditor"]["status"] == "ready"
    assert workflow["stage_statuses"]["finalize"]["status"] == "pending"
    new_sha = state["artifact_registry"]["artifacts"]["audited_brief"]["sha256"]
    assert new_sha != old_sha
    artifacts = state["artifact_registry"]["artifacts"]
    assert artifacts["audited_brief"]["status"] == "valid"
    assert artifacts["claim_ledger"]["status"] == "valid"
    assert artifacts["analyst_draft_snapshot"]["status"] == "valid"
    assert artifacts["audit_report"]["status"] == "stale"
    assert artifacts["audit_report"]["validation_result"] == "stale_after_repair"
    assert "rerun producer stage 'auditor'" in artifacts["audit_report"]["blocking_reason"]
    assert artifacts["auditor_quality_gate_report"]["status"] == "stale"
    assert artifacts["auditor_quality_gate_report"]["validation_result"] == "stale_after_repair"
    assert workflow["run_integrity"]["status"] == "clean"
    events = _event_records(ws)
    assert events[-1]["event_type"] == "repair_completed"
    assert events[-1]["metadata"]["repair_owner"] == "editor"
    stale_validated_events = [
        event
        for event in events
        if event["event_type"] == "artifact_validated"
        and event["artifact_id"] in {"audit_report", "auditor_quality_gate_report"}
        and event["metadata"].get("status") == "stale"
    ]
    assert {event["artifact_id"] for event in stale_validated_events} == {
        "audit_report",
        "auditor_quality_gate_report",
    }
    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert checked["workflow_state"]["run_integrity"]["status"] == "clean"
    checked_artifacts = checked["artifact_registry"]["artifacts"]
    assert checked_artifacts["audit_report"]["status"] == "stale"
    assert checked_artifacts["auditor_quality_gate_report"]["status"] == "stale"


def test_auditor_rerun_after_editor_repair_clears_stale_downstream_artifacts(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    audited.write_text("# Brief\n\nEditor repaired draft. [src:CL-001]\n", encoding="utf-8")
    repaired = complete_repair_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="editor repaired audited brief from deterministic route",
    )
    repair_transaction_id = repaired["transaction"]["transaction_id"]
    stale_audit_record = repaired["artifact_registry"]["artifacts"]["audit_report"]
    assert stale_audit_record["status"] == "stale"
    stale_audit_sha = stale_audit_record["stale_baseline_sha256"]

    refreshed_report = json.loads(_valid_audit_report_payload())
    refreshed_report["repair_reviewed"] = True
    _write_json_artifact(ws, "audit_report.json", json.dumps(refreshed_report) + "\n")
    refreshed_audit_sha = _sha256_file(_intermediate(ws) / "audit_report.json")
    assert refreshed_audit_sha != stale_audit_sha
    refreshed_state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    refreshed_audit_record = refreshed_state["artifact_registry"]["artifacts"]["audit_report"]
    assert refreshed_audit_record["status"] == "stale"
    assert refreshed_audit_record["sha256"] == refreshed_audit_sha
    assert refreshed_audit_record["stale_baseline_sha256"] == stale_audit_sha
    _write_quality_gate_report(ws, stage_id="auditor")
    audited_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor reran after editor repair",
    )

    workflow = audited_state["workflow_state"]
    assert workflow["current_stage"] == "finalize"
    auditor_metadata = workflow["stage_statuses"]["auditor"]["metadata"]
    assert auditor_metadata["audit_binding"]["relevant_repair_transaction_ids"] == [
        repair_transaction_id
    ]
    artifacts = audited_state["artifact_registry"]["artifacts"]
    assert artifacts["audit_report"]["status"] == "valid"
    assert artifacts["auditor_quality_gate_report"]["status"] == "valid"
    assert artifacts["claim_ledger"]["status"] == "valid"
    assert artifacts["analyst_draft_snapshot"]["status"] == "valid"


def test_auditor_rerun_after_editor_repair_accepts_new_artifact_without_stale_baseline(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    audited.write_text("# Brief\n\nEditor repaired draft. [src:CL-001]\n", encoding="utf-8")
    repaired = complete_repair_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="editor repaired audited brief before auditor wrote audit report",
    )
    repair_transaction_id = repaired["transaction"]["transaction_id"]
    auditor_metadata = repaired["workflow_state"]["stage_statuses"]["auditor"]["metadata"]
    baselines = auditor_metadata["stale_artifact_baselines"]
    assert baselines["audit_report"]["sha256"] is None

    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    refreshed_audit_sha = _sha256_file(_intermediate(ws) / "audit_report.json")
    refreshed_state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    refreshed_audit_record = refreshed_state["artifact_registry"]["artifacts"]["audit_report"]
    assert refreshed_audit_record["status"] == "stale"
    assert refreshed_audit_record["sha256"] == refreshed_audit_sha
    assert "stale_baseline_sha256" not in refreshed_audit_record
    _write_quality_gate_report(ws, stage_id="auditor")

    audited_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor generated audit report after editor repair",
    )

    assert audited_state["workflow_state"]["current_stage"] == "finalize"
    auditor_metadata = audited_state["workflow_state"]["stage_statuses"]["auditor"]["metadata"]
    assert auditor_metadata["audit_binding"]["relevant_repair_transaction_ids"] == [
        repair_transaction_id
    ]
    artifacts = audited_state["artifact_registry"]["artifacts"]
    assert artifacts["audit_report"]["status"] == "valid"
    assert artifacts["auditor_quality_gate_report"]["status"] == "valid"


def test_auditor_rerun_rejects_unrefreshed_stale_audit_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    audited.write_text("# Brief\n\nEditor repaired draft. [src:CL-001]\n", encoding="utf-8")
    repaired = complete_repair_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="editor repaired audited brief from deterministic route",
    )
    assert repaired["artifact_registry"]["artifacts"]["audit_report"]["status"] == "stale"

    _write_quality_gate_report(ws, stage_id="auditor")
    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor reran gate only after editor repair",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "audit_report" in str(excinfo.value)
    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert checked["artifact_registry"]["artifacts"]["audit_report"]["status"] == "stale"
    workflow = checked["workflow_state"]
    assert workflow["current_stage"] == "auditor"
    assert workflow["stage_statuses"]["auditor"]["status"] != "complete"


def test_repair_complete_rejects_blocked_artifact_edit(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    audited.write_text("# Brief\n\nEditor repaired draft. [src:CL-001]\n", encoding="utf-8")
    (_intermediate(ws) / "analyst_draft_snapshot.md").write_text(
        "# Brief\n\nIllegally changed snapshot. [src:CL-001]\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_repair_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="editor repaired audited brief from deterministic route",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Blocked repair artifact changed" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["active_repair"]["repair_owner"] == "editor"


def test_repair_complete_rejects_downstream_artifact_created_during_repair(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    audited.write_text("# Brief\n\nEditor repaired draft. [src:CL-001]\n", encoding="utf-8")
    _write_json_artifact(ws, "audit_report.json", _valid_audit_report_payload())

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_repair_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="editor repaired audited brief from deterministic route",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "output/intermediate/audit_report.json" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["active_repair"]["repair_owner"] == "editor"


def test_repair_complete_rejects_noop_repair(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="analyst",
        reason="analyst complete",
    )
    audited.write_text("# Brief\n\nEditor draft needing repair. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        reason="editor complete",
    )
    _write_editor_repair_gate_report(ws)
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_repair_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="noop repair should fail",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "did not modify any allowed artifact" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["active_repair"]["repair_owner"] == "editor"


def test_analyst_snapshot_rolls_back_when_stage_completion_fails_after_snapshot(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    snapshot = _intermediate(ws) / "analyst_draft_snapshot.md"
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()

    def fail_registry_build(*args, **kwargs):
        raise RuntimeStateError(
            "registry build failed after snapshot",
            error_code=runtime_state.operations.E_TRANSACTION_INTEGRITY,
        )

    monkeypatch.setattr(runtime_state.operations, "_build_artifact_registry", fail_registry_build)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="analyst",
            reason="analyst complete should fail after snapshot",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert not snapshot.exists()
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events


def test_analyst_snapshot_rolls_back_when_state_write_fails_after_snapshot(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    snapshot = _intermediate(ws) / "analyst_draft_snapshot.md"
    before_manifest = _state_file(ws, "runtime_manifest").read_bytes()
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    before_registry = (
        _state_file(ws, "artifact_registry").read_bytes()
        if _state_file(ws, "artifact_registry").exists()
        else None
    )
    original_write_json_atomic = runtime_state.operations._write_json_atomic

    def fail_artifact_registry_write(path: Path, payload: dict) -> None:
        if path.name == "artifact_registry.json":
            raise RuntimeStateError(
                "artifact registry write failed after snapshot",
                error_code=runtime_state.operations.E_TRANSACTION_INTEGRITY,
            )
        original_write_json_atomic(path, payload)

    monkeypatch.setattr(runtime_state.operations, "_write_json_atomic", fail_artifact_registry_write)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="analyst",
            reason="analyst complete should fail during state write",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert excinfo.value.details["restored"] is True
    assert not snapshot.exists()
    assert _state_file(ws, "runtime_manifest").read_bytes() == before_manifest
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events
    if before_registry is None:
        assert not _state_file(ws, "artifact_registry").exists()
    else:
        assert _state_file(ws, "artifact_registry").read_bytes() == before_registry


def test_state_check_allows_append_only_event_log_after_frozen_artifact(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    _freeze_claim_ledger_fixture(ws)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="claim-ledger",
        reason="claim ledger complete",
    )
    runtime_state.append_event(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        event_type="control_switchboard_warning",
        actor="system",
        reason="append-only event log is not a frozen artifact",
        metadata={},
    )

    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert checked["artifact_registry"]["artifacts"]["claim_ledger"]["status"] == "valid"


def test_auditor_stage_complete_requires_passing_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)

    with pytest.raises(RuntimeStateError) as missing:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )
    assert missing.value.error_code == "E_QUALITY_GATE_REQUIRED"

    _write_quality_gate_report(ws, blocking=True, stage_id="auditor")
    with pytest.raises(RuntimeStateError) as blocking:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )
    assert blocking.value.error_code == "E_QUALITY_GATE_REQUIRED"


def test_auditor_stage_complete_rejects_wrong_stage_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, stage_id="doctor")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "gate_stage_id='auditor'" in str(excinfo.value)


def test_auditor_stage_complete_rejects_incomplete_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)
    report_path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["gate_results"] = [
        result for result in report["gate_results"] if result["gate_id"] != "freshness"
    ]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "missing: freshness" in str(excinfo.value)


def test_auditor_stage_complete_rejects_missing_quality_gate_input_metadata(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)
    report_path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["metadata"].pop("brief")
    report["metadata"].pop("ledger")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "brief metadata must be output/intermediate/audited_brief.md" in str(excinfo.value)


def test_auditor_stage_complete_passes_with_clean_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )

    assert state["workflow_state"]["current_stage"] == "finalize"


def test_auditor_stage_complete_allows_warning_gate_for_non_experiment_workflow(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, status="warning", stage_id="auditor")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates produced non-blocking warnings",
    )

    assert state["workflow_state"]["current_stage"] == "finalize"


def test_auditable_target_auditor_stage_complete_requires_gate_status_pass(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_auditable_condition_metadata(ws)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, status="warning", stage_id="auditor")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor warning should not complete auditable target",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "080 auditable_brief target requires auditor quality gate report status pass" in str(excinfo.value)
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["current_stage"] == "auditor"
    events = _event_records(ws)
    assert not any(
        event.get("event_type") == "decision_recorded" and event.get("stage_id") == "auditor"
        for event in events
    )


def test_auditable_target_auditor_stage_complete_allows_final_abstract_advisory_warning(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_auditable_condition_metadata(ws)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, stage_id="auditor")
    report_path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    finding = {
        "finding_id": "QG_FINAL_ABSTRACT_QUALITY_001",
        "gate_id": "final_abstract_quality",
        "finding_type": "final_missing_limitation_section",
        "severity": "medium",
        "blocking_level": "warning",
        "blocking": False,
        "repair_owner": "none",
        "stage_id": "editor",
        "artifact_id": "audited_brief",
        "repair_stage_id": None,
        "repair_artifact_id": None,
        "description": "Advisory final abstract warning.",
        "recommendation": "Review before delivery.",
        "metadata": {"repair_boundary": "advisory_non_routable"},
    }
    report["status"] = "warning"
    report["gate_results"] = [
        {"gate_id": "coverage_omission", "status": "pass", "blocking": False, "finding_ids": []},
        {"gate_id": "freshness", "status": "pass", "blocking": False, "finding_ids": []},
        {"gate_id": "final_abstract_quality", "status": "warning", "blocking": False, "finding_ids": [finding["finding_id"]]},
        {"gate_id": "material_fact", "status": "pass", "blocking": False, "finding_ids": []},
        {"gate_id": "target_relevance", "status": "pass", "blocking": False, "finding_ids": []},
    ]
    report["findings"] = [finding]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor completed with advisory final abstract warning",
    )

    assert state["workflow_state"]["current_stage"] == "finalize"


def test_auditor_stage_complete_ignores_legacy_quality_gate_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, legacy_only=True)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "output/intermediate/gates/auditor_quality_gate_report.json is required" in str(excinfo.value)


def test_finalize_gate_does_not_mutate_frozen_auditor_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    auditor_state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )
    auditor_report = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    auditor_sha = auditor_state["artifact_registry"]["artifacts"]["auditor_quality_gate_report"]["sha256"]
    assert auditor_sha == _sha256_file(auditor_report)

    _write_finalize_report(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    finalize_report = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"

    assert finalize_report.exists()
    assert _sha256_file(auditor_report) == auditor_sha

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader-facing gates and final artifacts passed",
    )

    registry = state["artifact_registry"]["artifacts"]
    assert registry["auditor_quality_gate_report"]["sha256"] == auditor_sha
    assert registry["finalize_quality_gate_report"]["sha256"] == _sha256_file(finalize_report)
    assert state["workflow_state"]["current_stage"] is None


def test_auditor_stage_complete_rejects_audit_report_missing_audit_status(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    report = json.loads((_intermediate(ws) / "audit_report.json").read_text(encoding="utf-8"))
    report.pop("audit_status")
    _write_json_artifact(ws, "audit_report.json", json.dumps(report) + "\n")
    _write_quality_gate_report(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor and gates passed",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "audit_report_schema_error:audit_status" in str(excinfo.value)


def test_auditor_stage_complete_rejects_audit_report_missing_audit_score(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    report = json.loads((_intermediate(ws) / "audit_report.json").read_text(encoding="utf-8"))
    report.pop("audit_score")
    _write_json_artifact(ws, "audit_report.json", json.dumps(report) + "\n")
    _write_quality_gate_report(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor and gates passed",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "audit_report_schema_error:audit_score" in str(excinfo.value)


def test_auditor_stage_complete_rejects_non_integer_audit_score(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    report = json.loads((_intermediate(ws) / "audit_report.json").read_text(encoding="utf-8"))
    report["audit_score"] = 99.5
    _write_json_artifact(ws, "audit_report.json", json.dumps(report) + "\n")
    _write_quality_gate_report(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor and gates passed",
        )

    assert excinfo.value.error_code == "E_ARTIFACT_INVALID"
    assert "audit_report_schema_error:audit_score" in str(excinfo.value)


def test_auditor_stage_complete_records_ledger_and_audit_report_sha(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
        runtime="hermes",
        model="claude-haiku",
    )

    registry = state["artifact_registry"]["artifacts"]
    auditor_status = state["workflow_state"]["stage_statuses"]["auditor"]
    metadata = auditor_status["metadata"]
    assert metadata["upstream_artifact_sha256"]["claim_ledger"] == registry["claim_ledger"]["sha256"]
    assert metadata["upstream_artifact_sha256"]["audited_brief"] == registry["audited_brief"]["sha256"]
    assert metadata["produced_artifact_sha256"]["audit_report"] == registry["audit_report"]["sha256"]
    assert metadata["runtime_provenance"]["runtime"] == "hermes"
    assert metadata["runtime_provenance"]["model"] == "claude-haiku"
    assert metadata["runtime_provenance"]["quality_claim"] is False


def test_state_check_preserves_auditor_binding_metadata_for_finalize(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )

    refreshed = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    metadata = refreshed["workflow_state"]["stage_statuses"]["auditor"]["metadata"]
    assert metadata["upstream_artifact_sha256"]["claim_ledger"]
    assert metadata["upstream_artifact_sha256"]["audited_brief"]
    assert metadata["produced_artifact_sha256"]["audit_report"]

    result = finalize_reader_outputs(
        output_dir=ws / "output",
        project_name="Runtime State Test",
        output_formats=["markdown"],
        output_named_outputs=False,
    )
    assert result.audit_binding["status"] == "pass"


def test_finalize_complete_does_not_consume_delivery_snapshot(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    snapshot_dir = ws / "output" / "delivery-history" / "mabw-runtime-state-test"
    snapshot_dir.mkdir(parents=True)
    snapshot_brief = snapshot_dir / "brief.md"
    snapshot_brief.write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_snapshot_dir"] = str(snapshot_dir)
    report["delivery_snapshot_artifacts"] = [str(snapshot_brief)]
    report["delivery_snapshot_artifact_sha256"] = {str(snapshot_brief): _sha256_file(snapshot_brief)}
    report["delivery_snapshot_semantics"] = "convenience_copy_not_immutable_archive"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert snapshot_dir.exists()
    shutil.rmtree(snapshot_dir)

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )

    assert state["workflow_state"]["stage_statuses"]["finalize"]["status"] == "complete"
    assert not snapshot_dir.exists()


def test_finalize_complete_requires_passing_audit_binding(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("audit_binding")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"
    assert "audit_binding.status must be pass" in str(excinfo.value)


def test_finalize_complete_rejects_stale_audit_binding_hash(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["audit_binding"]["claim_ledger_sha256"] = "0" * 64
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"
    assert "audit_binding.claim_ledger_sha256 does not match current artifact bytes" in str(
        excinfo.value
    )


def test_finalize_complete_rejects_forged_clean_report_with_dirty_artifact(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    (ws / "output" / "brief.md").write_text("# Brief\n\nLeaked [CL-0001].\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="finalize complete",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"


def test_finalize_complete_records_terminal_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
        runtime="manual",
        model="human-reviewed",
    )

    workflow = state["workflow_state"]
    transaction_id = workflow["last_completion_transaction"]["transaction_id"]
    assert workflow["current_stage"] is None
    assert workflow["stage_statuses"]["finalize"]["status"] == "complete"
    assert workflow["last_decision"]["decision"] == "finalize"
    provenance = workflow["stage_statuses"]["finalize"]["metadata"]["runtime_provenance"]
    assert provenance["runtime"] == "manual"
    assert provenance["model"] == "human-reviewed"
    assert provenance["provenance_only"] is True
    assert any(
        event["event_type"] == "decision_recorded"
        and (event.get("metadata") or {}).get("transaction_id") == transaction_id
        and (event.get("metadata") or {}).get("runtime_provenance") == provenance
        for event in _event_records(ws)
    )


def test_finalize_complete_cli_records_runtime_model_provenance(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)

    rc = main([
        "state",
        "finalize-complete",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--reason",
        "reader artifacts finalized and clean",
        "--runtime",
        "codex",
        "--model",
        "gpt-5-codex",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    provenance = payload["workflow_state"]["stage_statuses"]["finalize"]["metadata"]["runtime_provenance"]
    assert provenance["runtime"] == "codex"
    assert provenance["model"] == "gpt-5-codex"
    assert provenance["provenance_only"] is True
    assert provenance["quality_claim"] is False


def test_run_integrity_contamination_event_is_sticky_on_state_check(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    workflow_path = _state_file(ws, "workflow_state")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    runtime_state.append_event(
        workspace=ws,
        run_id=manifest["run_id"],
        event_type="run_integrity_contaminated",
        actor="orchestrator",
        reason="Synthetic contamination event must remain sticky.",
        metadata={
            "reason_code": "synthetic_contamination",
            "message": "Synthetic contamination event must remain sticky.",
            "reference_eligible": False,
            "clean_single_shot": False,
        },
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    integrity = state["workflow_state"]["run_integrity"]
    persisted = json.loads(workflow_path.read_text(encoding="utf-8"))["run_integrity"]

    assert integrity["status"] == "contaminated"
    assert integrity["reference_eligible"] is False
    assert integrity["clean_single_shot"] is False
    assert integrity["reasons"][0]["reason_code"] == "synthetic_contamination"
    assert persisted == integrity


def test_finalize_complete_keeps_contaminated_run_out_of_reference_pack(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    runtime_state.append_event(
        workspace=ws,
        run_id=manifest["run_id"],
        event_type="run_integrity_contaminated",
        actor="orchestrator",
        stage_id="auditor",
        reason="Prior repair contaminated this run.",
        metadata={
            "reason_code": "prior_repair",
            "message": "Prior repair contaminated this run.",
            "reference_eligible": False,
            "clean_single_shot": False,
            "stage_id": "auditor",
        },
    )

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized after repair",
    )

    integrity = state["workflow_state"]["run_integrity"]
    archive_manifest = json.loads(
        (ws / "output" / "runs" / manifest["run_id"] / "manifest.json").read_text(encoding="utf-8")
    )

    assert integrity["status"] == "contaminated_repaired"
    assert integrity["reference_eligible"] is False
    assert integrity["clean_single_shot"] is False
    assert archive_manifest["run_integrity"]["status"] == "contaminated_repaired"
    assert archive_manifest["run_integrity"]["reference_eligible"] is False


def test_finalize_complete_archives_delivery_intermediate_and_control_files(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    run_id = state["manifest"]["run_id"]
    archive = ws / "output" / "runs" / run_id

    assert (archive / "delivery" / "brief.md").exists()
    assert (archive / "intermediate" / "claim_ledger.json").exists()
    assert (archive / "intermediate" / "audit_report.json").exists()
    assert (archive / "intermediate" / "finalize_report.json").exists()
    assert (archive / "control" / "runtime_manifest.json").exists()
    assert (archive / "control" / "workflow_state.json").exists()
    assert (archive / "control" / "artifact_registry.json").exists()
    assert (archive / "control" / "event_log.jsonl").exists()
    assert any(event["event_type"] == "run_archived" for event in _event_records(ws))


def test_finalize_complete_archives_source_appendix_trace_as_auxiliary_artifact(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    trace = ws / "output" / "source_appendix_trace.md"
    trace.write_text(
        "# Evidence Span Trace Audit Copy\n\nTraceability surface only.\n",
        encoding="utf-8",
    )
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["source_appendix_trace"] = str(trace)
    report["source_appendix_trace_generation"] = "generated"
    report["source_appendix_trace_source_count"] = 1
    report["source_appendix_trace_span_count"] = 1
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized",
    )
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    trace_records = [
        record
        for record in manifest["files"]
        if record.get("original_path") == "output/source_appendix_trace.md"
    ]

    assert (archive / "auxiliary" / "source_appendix_trace.md").exists()
    assert len(trace_records) == 1
    assert trace_records[0]["role"] == "auxiliary"
    assert trace_records[0]["archive_path"] == "auxiliary/source_appendix_trace.md"
    assert trace_records[0]["sha256"] == _sha256_file(trace)


def test_finalize_complete_rejects_missing_source_appendix_trace_reference(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["source_appendix_trace"] = "output/source_appendix_trace.md"
    report["source_appendix_trace_generation"] = "generated"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(runtime_state.operations.RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized",
        )

    assert "missing auxiliary artifact source_appendix_trace" in str(excinfo.value)
    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    assert not (ws / "output" / "runs" / manifest["run_id"]).exists()


def test_run_archive_manifest_uses_workspace_relative_paths_only(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    manifest_path = ws / "output" / "runs" / state["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for record in manifest["files"]:
        assert not Path(record["archive_path"]).is_absolute()
        assert not Path(record["original_path"]).is_absolute()
        assert str(ws) not in record["archive_path"]
        assert str(ws) not in record["original_path"]


def test_run_archive_records_sha256_for_every_file(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "mabw.run_archive.v1"
    assert manifest["run_id"] == state["manifest"]["run_id"]
    assert manifest["run_integrity"] == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    assert manifest["timing"]["schema_version"] == "mabw.control_timing.v1"
    assert manifest["timing"]["source"] == "event_log"
    assert manifest["timing"]["precision"] == "control_trace_bucket"
    assert manifest["timing"]["status"] in {"available", "partial", "incomplete", "contaminated", "unknown"}
    assert isinstance(manifest["timing"]["stages"], list)
    assert isinstance(manifest["timing"]["finalize"], dict)
    assert manifest["files"]
    for record in manifest["files"]:
        path = archive / record["archive_path"]
        assert path.exists()
        assert record["sha256"] == _sha256_file(path)
        assert record["size_bytes"] == path.stat().st_size


def test_run_archive_manifest_records_complete_fact_layer(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert fact_layer["schema_version"] == "mabw.run_archive.fact_layer.v1"
    assert fact_layer["status"] == "complete"
    assert fact_layer["missing_artifact_ids"] == []
    assert fact_layer["source_evidence_count"] == 1
    fact_artifact_ids = {record["artifact_id"] for record in fact_layer["artifacts"]}
    assert {
        "durable_source_evidence_or_source_pack",
        "input_classification",
        "candidate_claims",
        "screened_candidates",
        "claim_ledger",
    } <= fact_artifact_ids
    assert len(fact_artifact_ids) == len(fact_layer["artifacts"])
    for record in fact_layer["artifacts"]:
        if record["artifact_id"] == "durable_source_evidence_or_source_pack":
            assert record["fact_role"] == "durable_source_evidence_pack"
            assert record["file_count"] == 1
            assert len(record["files"]) == 1
            for file_record in record["files"]:
                path = archive / file_record["archive_path"]
                assert path.exists()
                assert file_record["sha256"] == _sha256_file(path)
                assert not Path(file_record["archive_path"]).is_absolute()
                assert not Path(file_record["original_path"]).is_absolute()
        else:
            path = archive / record["archive_path"]
            assert path.exists()
            assert record["sha256"] == _sha256_file(path)
            assert not Path(record["archive_path"]).is_absolute()
            assert not Path(record["original_path"]).is_absolute()


def test_run_archive_omits_evidence_span_projection_when_registry_absent(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))

    assert "evidence_span_registry" not in manifest


def test_run_archive_projects_valid_evidence_span_registry_hashes(tmp_path):
    ws = _write_workspace(tmp_path)
    raw_excerpt = _write_archivable_evidence_span_registry(ws)

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    projection = manifest["evidence_span_registry"]
    source_projection = projection["sources"][0]
    span_projection = source_projection["spans"][0]
    archived_source = archive / source_projection["archive_path"]
    archived_registry = archive / projection["registry_archive_path"]
    archived_registry_payload = json.loads(archived_registry.read_text(encoding="utf-8"))

    assert projection["schema_version"] == "mabw.run_archive.evidence_span_registry.v1"
    assert projection["status"] == "valid"
    assert projection["validation_result"] == "experimental_evidence_span_registry_schema"
    assert projection["registry_path"] == "output/intermediate/evidence_span_registry.json"
    assert projection["registry_archive_path"] == "intermediate/evidence_span_registry.json"
    assert projection["registry_sha256"] == _sha256_file(_intermediate(ws) / "evidence_span_registry.json")
    assert projection["registry_sha256"] == _sha256_file(archived_registry)
    assert projection["source_count"] == 1
    assert projection["span_count"] == 1
    assert source_projection["source_id"] == "SRC-001"
    assert source_projection["source_path"] == "input/sources/source-001.md"
    assert source_projection["archive_path"] == "fact_layer/input/sources/source-001.md"
    assert source_projection["source_sha256"] == _sha256_file(archived_source)
    assert source_projection["source_size_bytes"] == archived_source.stat().st_size
    assert span_projection["span_id"] == "ESP-001-01"
    assert span_projection["raw_excerpt_hash"] == _span_hash(raw_excerpt)
    assert span_projection["span_role"] == "numeric_observation"
    assert archived_source.read_text(encoding="utf-8")[
        span_projection["char_start"]:span_projection["char_end"]
    ] == raw_excerpt
    assert archived_registry_payload["sources"][0]["spans"][0]["raw_excerpt"] == raw_excerpt
    assert "raw_excerpt" not in span_projection


def test_run_archive_revalidates_evidence_span_registry_against_current_source_bytes(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_archivable_evidence_span_registry(ws)
    state = _complete_finalized_workspace(ws)
    archive_root = ws / "output" / "runs" / state["manifest"]["run_id"]
    shutil.rmtree(archive_root)
    (ws / "input" / "sources" / "source-001.md").write_text(
        "# Source 001\n\nThe original span text was removed after validation.\n",
        encoding="utf-8",
    )
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))

    with pytest.raises(runtime_state.operations.RunArchiveError) as excinfo:
        archive_finalized_run(
            workspace=ws,
            run_id=state["manifest"]["run_id"],
            manifest=state["manifest"],
            workflow=state["workflow_state"],
            artifact_registry=state["artifact_registry"],
            finalize_report=finalize_report,
        )

    assert "no longer matches current source-pack bytes" in str(excinfo.value)
    assert (
        excinfo.value.details["validation_result"]
        == "evidence_span_registry_validation_error:span_excerpt_not_found:ESP-001-01"
    )
    assert not archive_root.exists()


def test_run_archive_records_invalid_evidence_span_registry_without_span_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    _write_json_artifact(ws, "evidence_span_registry.json", _valid_evidence_span_registry_payload())

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    projection = manifest["evidence_span_registry"]

    assert projection["status"] == "invalid"
    assert projection["registry_path"] == "output/intermediate/evidence_span_registry.json"
    assert projection["registry_archive_path"] == "intermediate/evidence_span_registry.json"
    assert projection["registry_sha256"] == _sha256_file(_intermediate(ws) / "evidence_span_registry.json")
    assert (
        projection["validation_result"]
        == "evidence_span_registry_validation_error:source_path_missing:SRC-001"
    )
    assert "sources" not in projection
    assert (archive / "intermediate" / "evidence_span_registry.json").exists()


def test_run_archive_with_evidence_span_projection_is_idempotent(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_archivable_evidence_span_registry(ws)
    state = _complete_finalized_workspace(ws)
    archive = state["run_archive"]
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))

    result = archive_finalized_run(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        manifest=state["manifest"],
        workflow=state["workflow_state"],
        artifact_registry=state["artifact_registry"],
        finalize_report=finalize_report,
    )

    assert result["archive_manifest_sha256"] == archive["archive_manifest_sha256"]
    assert result["manifest"]["evidence_span_registry"] == archive["manifest"]["evidence_span_registry"]


def test_existing_archive_rejects_corrupted_evidence_span_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_archivable_evidence_span_registry(ws)
    state = _complete_finalized_workspace(ws)
    archive_root = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest_path = archive_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence_span_registry"]["sources"][0]["spans"][0]["raw_excerpt_hash"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))

    with pytest.raises(runtime_state.operations.RunArchiveError) as excinfo:
        archive_finalized_run(
            workspace=ws,
            run_id=state["manifest"]["run_id"],
            manifest=state["manifest"],
            workflow=state["workflow_state"],
            artifact_registry=state["artifact_registry"],
            finalize_report=finalize_report,
        )

    assert excinfo.value.error_code == runtime_state.operations.E_RUN_ARCHIVE_CONFLICT
    assert "evidence_span_registry projection differs" in str(excinfo.value)


def test_run_archive_manifest_groups_multiple_source_files_as_one_source_pack(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    second = ws / "input" / "sources" / "source-002.md"
    second.write_text("# Source 002\n\nSecond evidence file.\n", encoding="utf-8")

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]
    artifacts = {
        record["artifact_id"]: record
        for record in fact_layer["artifacts"]
    }
    source_pack = artifacts["durable_source_evidence_or_source_pack"]

    assert fact_layer["status"] == "complete"
    assert fact_layer["source_evidence_count"] == 2
    assert len(artifacts) == len(fact_layer["artifacts"])
    assert source_pack["file_count"] == 2
    assert len(source_pack["files"]) == 2
    assert {record["original_path"] for record in source_pack["files"]} == {
        "input/sources/source-001.md",
        "input/sources/source-002.md",
    }
    for file_record in source_pack["files"]:
        path = archive / file_record["archive_path"]
        assert path.exists()
        assert file_record["sha256"] == _sha256_file(path)


def test_run_archive_excludes_source_candidates_from_fact_layer(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_plan_only\n"
        "evidence_status: not_evidence\n",
        encoding="utf-8",
    )
    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert {
        "artifact_id": "source_candidates",
        "original_path": "source_candidates.yaml",
        "reason": "source_plan_not_evidence",
        "sha256": _sha256_file(ws / "source_candidates.yaml"),
        "size_bytes": (ws / "source_candidates.yaml").stat().st_size,
    } in fact_layer["excluded"]
    assert all(record["original_path"] != "source_candidates.yaml" for record in manifest["files"])
    assert not (archive / "fact_layer" / "source_candidates.yaml").exists()


def test_run_archive_marks_fact_layer_incomplete_when_source_evidence_missing(tmp_path):
    ws = _write_workspace(tmp_path)
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps({"evidence": [], "feedback": [], "instruction": [], "context": [], "skipped": []}),
        encoding="utf-8",
    )

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert fact_layer["status"] == "incomplete"
    assert "durable_source_evidence_or_source_pack" in fact_layer["missing_artifact_ids"]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("empty.md", ""),
        ("template.md", "# Template\n\nFill this in later.\n"),
        ("placeholder.json", '{"placeholder": true}\n'),
        ("readme.txt", "Source directory notes.\n"),
        (".gitkeep", "keep\n"),
    ],
)
def test_run_archive_uses_source_discovery_evidence_filter_for_fact_layer_sources(
    tmp_path,
    filename: str,
    content: str,
):
    ws = _write_workspace(tmp_path)
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / filename).write_text(content, encoding="utf-8")
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "input_classification.json").write_text(
        json.dumps({
            "evidence": [{"path": f"input/sources/{filename}", "name": filename}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        }),
        encoding="utf-8",
    )

    state = _complete_finalized_workspace(ws)
    archive = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]

    assert fact_layer["status"] == "incomplete"
    assert fact_layer["source_evidence_count"] == 0
    assert "durable_source_evidence_or_source_pack" in fact_layer["missing_artifact_ids"]
    assert all(
        record["artifact_id"] != "durable_source_evidence_or_source_pack"
        for record in fact_layer["artifacts"]
    )


def test_run_archive_manifest_marks_malformed_run_integrity_unknown(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))
    workflow = dict(state["workflow_state"])
    workflow["run_integrity"] = "bad"
    run_id = f"{state['manifest']['run_id']}-malformed"

    archive = archive_finalized_run(
        workspace=ws,
        run_id=run_id,
        manifest=state["manifest"],
        workflow=workflow,
        artifact_registry=state["artifact_registry"],
        finalize_report=finalize_report,
    )
    manifest = archive["manifest"]

    assert manifest["run_integrity"]["status"] == "unknown"
    assert manifest["run_integrity"]["reference_eligible"] is False


def test_finalize_complete_archive_is_idempotent_when_content_matches(tmp_path):
    ws = _write_workspace(tmp_path)
    state = _complete_finalized_workspace(ws)
    archive = state["run_archive"]
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))

    result = archive_finalized_run(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        manifest=state["manifest"],
        workflow=state["workflow_state"],
        artifact_registry=state["artifact_registry"],
        finalize_report=finalize_report,
    )

    assert result["archive_manifest_sha256"] == archive["archive_manifest_sha256"]
    assert result["file_count"] == archive["file_count"]


def test_existing_archive_rejects_corrupted_fact_layer_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    _write_fact_layer_inputs(ws)
    state = _complete_finalized_workspace(ws)
    archive_root = ws / "output" / "runs" / state["manifest"]["run_id"]
    manifest_path = archive_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fact_layer"] = {
        "schema_version": "mabw.run_archive.fact_layer.v1",
        "status": "complete",
        "artifacts": [],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    finalize_report = json.loads((_intermediate(ws) / "finalize_report.json").read_text(encoding="utf-8"))

    with pytest.raises(runtime_state.operations.RunArchiveError) as excinfo:
        archive_finalized_run(
            workspace=ws,
            run_id=state["manifest"]["run_id"],
            manifest=state["manifest"],
            workflow=state["workflow_state"],
            artifact_registry=state["artifact_registry"],
            finalize_report=finalize_report,
        )

    assert excinfo.value.error_code == runtime_state.operations.E_RUN_ARCHIVE_CONFLICT
    assert "fact_layer projection differs" in str(excinfo.value)


def test_import_fact_layer_transaction_copies_archive_and_marks_upstream_stages(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        runtime="codex",
        repo_workdir=ROOT,
    )

    manifest = state["manifest"]
    workflow = state["workflow_state"]
    assert manifest["recipe"] == "fast-rerun"
    assert manifest["runtime"] == "codex"
    import_record = manifest["fact_layer_import"]
    assert import_record["schema_version"] == runtime_state.operations.FACT_LAYER_IMPORT_SCHEMA
    assert import_record["source_run_id"] == finalized["manifest"]["run_id"]
    assert import_record["source_archive_manifest"] == f"output/runs/{finalized['manifest']['run_id']}/manifest.json"
    assert str(source_ws) not in json.dumps(import_record)
    assert import_record["imported_file_count"] >= 4
    assert workflow["current_stage"] == "analyst"
    for stage_id in ("doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger"):
        status = workflow["stage_statuses"][stage_id]
        assert status["status"] == "complete"
        assert status["metadata"]["satisfied_by_import"] is True
    assert (target_ws / "input" / "sources" / "source-001.md").exists()
    assert (target_ws / "output" / "input_classification.json").exists()
    assert (_intermediate(target_ws) / "candidate_claims.json").exists()
    assert (_intermediate(target_ws) / "screened_candidates.json").exists()
    assert (_intermediate(target_ws) / "claim_ledger.json").exists()
    events = _event_records(target_ws)
    assert [event["event_type"] for event in events].count("fact_layer_imported") == 1
    assert not any(event["event_type"] == "decision_recorded" for event in events)

    checked = check_runtime_state(workspace=target_ws, repo_workdir=ROOT)
    assert checked["manifest"]["fact_layer_import"] == import_record
    assert checked["fact_layer_import"]["status"] == "valid"
    assert checked["fact_layer_import"]["source_run_id"] == finalized["manifest"]["run_id"]
    assert all(
        stage["display_status"] == "complete via import"
        for stage in checked["fact_layer_import"]["imported_stages"]
    )
    assert checked["workflow_state"]["stage_statuses"]["claim-ledger"]["metadata"]["satisfied_by_import"] is True

    shown = show_runtime_state(workspace=target_ws)
    assert shown["fact_layer_import"]["status"] == "valid"
    assert shown["fact_layer_import"]["next_stage"] == "analyst"


def test_import_fact_layer_transaction_records_target_freshness_snapshot(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-05-01"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )

    freshness = state["manifest"]["fact_layer_import"]["freshness_at_import"]
    assert freshness["schema_version"] == "mabw.fact_layer_import.freshness.v1"
    assert freshness["status"] == "stale"
    assert freshness["report_date"] == "2026-06-20"
    assert freshness["max_source_age_days"] == 14
    assert freshness["stale_claim_count"] == 1
    assert freshness["stale_claims"][0]["claim_id"] == "CL-001"


def test_fast_rerun_finalize_complete_rejects_stale_imported_fact_layer(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-05-01"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=target_ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "Fast-rerun imported fact layer is stale at target delivery time" in str(excinfo.value)
    assert not (target_ws / "output" / "runs").exists()


@pytest.mark.parametrize(
    "report_yaml",
    [
        """
report:
  date: "not-a-date"
  max_source_age_days: 14
""",
        """
report:
  date: "2026-06-20"
  max_source_age_days: "not-a-number"
""",
        """
report:
  max_source_age_days: 14
""",
    ],
)
def test_fast_rerun_finalize_complete_rejects_unknown_target_freshness_window(tmp_path, report_yaml):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        f"""
project:
  name: "Runtime State Test"
{report_yaml}
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-06-10"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=target_ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "freshness cannot be verified at target delivery time" in str(excinfo.value)
    assert not (target_ws / "output" / "runs").exists()


def test_fast_rerun_finalize_complete_rejects_retrieved_at_as_publication_freshness(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"retrieved_at": "2026-06-19"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    import_freshness = import_state["manifest"]["fact_layer_import"]["freshness_at_import"]
    assert import_freshness["status"] == "unknown"
    assert import_freshness["reason"] == "claim_publication_dates_missing"

    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=target_ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "claim_publication_dates_missing" in str(excinfo.value)
    assert not (target_ws / "output" / "runs").exists()


def test_fast_rerun_archive_records_source_relationship(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-06-10"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_state = import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    state = complete_finalize_transaction(
        workspace=target_ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )

    archive_manifest_path = target_ws / "output" / "runs" / state["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    fast_rerun = manifest["fast_rerun"]
    assert fast_rerun["schema_version"] == "mabw.run_archive.fast_rerun.v1"
    assert fast_rerun["source_run_id"] == finalized["manifest"]["run_id"]
    assert fast_rerun["fact_layer_sha256"] == import_state["manifest"]["fact_layer_import"]["fact_layer_sha256"]
    assert fast_rerun["freshness_at_finalize"]["status"] == "within_window"
    assert fast_rerun["timing_comparability"] == "downstream_only"


def test_fast_rerun_archive_records_finalize_time_freshness(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace_with_claim_metadata(
        source_ws,
        {"published_at": "2026-05-01"},
    )
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        repo_workdir=ROOT,
    )
    target_ws.joinpath("config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
report:
  date: "2026-05-10"
  max_source_age_days: 14
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    (target_ws / "output" / "intermediate" / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(target_ws, "audit_report.json", _valid_audit_report_payload())
    _set_current_stage(target_ws, "finalize")
    _write_quality_gate_report(target_ws, stage_id="finalize")
    _write_finalize_report(target_ws)

    state = complete_finalize_transaction(
        workspace=target_ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )

    archive_manifest_path = target_ws / "output" / "runs" / state["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    fast_rerun = manifest["fast_rerun"]
    runtime_manifest = json.loads((target_ws / "output" / "intermediate" / "runtime_manifest.json").read_text())
    persisted_freshness = runtime_manifest["fact_layer_import"]["freshness_at_finalize"]
    assert fast_rerun["freshness_at_import"]["status"] == "stale"
    assert fast_rerun["freshness_at_import"]["report_date"] == "2026-06-20"
    assert fast_rerun["freshness_at_finalize"]["status"] == "within_window"
    assert fast_rerun["freshness_at_finalize"]["report_date"] == "2026-05-10"
    assert persisted_freshness == fast_rerun["freshness_at_finalize"]


def test_state_show_human_output_reports_imported_stages(tmp_path, capsys):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    import_fact_layer_transaction(
        workspace=target_ws,
        archive=archive_manifest,
        runtime="codex",
        repo_workdir=ROOT,
    )

    rc = main(["state", "show", "--workspace", str(target_ws)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[state show] fact_layer_import: valid" in out
    assert "[state show] imported_satisfied_stages:" in out
    assert "source-discovery: complete via import" in out
    assert "claim-ledger: complete via import" in out
    assert "[state show] next_runtime_stage: analyst" in out


def test_import_fact_layer_transaction_rejects_incomplete_fact_layer(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "incomplete" in str(excinfo.value)


def test_import_fact_layer_transaction_rejects_contaminated_archive(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test"}],
    }
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "clean reference-eligible" in str(excinfo.value)


def test_import_fact_layer_transaction_rejects_archive_hash_mismatch(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    (archive_root / "fact_layer" / "output" / "intermediate" / "claim_ledger.json").write_text(
        "corrupted\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "hash does not match manifest" in str(excinfo.value)
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_existing_source_target(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    existing_source = target_ws / "input" / "sources" / "source-001.md"
    existing_source.parent.mkdir(parents=True, exist_ok=True)
    existing_source.write_text("user source that must not be overwritten\n", encoding="utf-8")
    before = existing_source.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "already exist" in str(excinfo.value)
    assert existing_source.read_bytes() == before
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_existing_intermediate_target(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    existing_ledger = _intermediate(target_ws) / "claim_ledger.json"
    existing_ledger.write_text(_valid_claim_ledger_payload("CL-LOCAL"), encoding="utf-8")
    before = existing_ledger.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "already exist" in str(excinfo.value)
    assert existing_ledger.read_bytes() == before
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_uses_code_required_artifacts_not_manifest_claim(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]
    fact_layer["required_artifact_ids"] = [
        item for item in fact_layer["required_artifact_ids"] if item != "claim_ledger"
    ]
    fact_layer["artifacts"] = [
        item for item in fact_layer["artifacts"] if item.get("artifact_id") != "claim_ledger"
    ]
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert excinfo.value.details["missing_artifact_ids"] == ["claim_ledger"]
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_self_consistent_invalid_claim_ledger(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    bad_ledger = archive_root / "fact_layer" / "output" / "intermediate" / "claim_ledger.json"
    bad_ledger.write_text("{not json}\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "claim_ledger":
            artifact["sha256"] = _sha256_file(bad_ledger)
            artifact["size_bytes"] = bad_ledger.stat().st_size
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "claim_ledger" in str(excinfo.value)
    assert not (_intermediate(target_ws) / "claim_ledger.json").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_source_candidates_fact_layer_artifact(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    source_candidates = archive_root / "fact_layer" / "source_candidates.yaml"
    source_candidates.write_text("artifact_type: source_plan_only\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"].append({
        "artifact_id": "source_candidates",
        "fact_role": "fact_layer_artifact",
        "archive_path": "fact_layer/source_candidates.yaml",
        "original_path": "source_candidates.yaml",
        "sha256": _sha256_file(source_candidates),
        "size_bytes": source_candidates.stat().st_size,
    })
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "source_candidates" in str(excinfo.value)
    assert not (target_ws / "source_candidates.yaml").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_unknown_delivery_artifact(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    delivery_brief = archive_root / "delivery" / "brief.md"
    assert delivery_brief.exists()
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"].append({
        "artifact_id": "delivery_brief",
        "fact_role": "fact_layer_artifact",
        "archive_path": "delivery/brief.md",
        "original_path": "output/delivery/brief.md",
        "sha256": _sha256_file(delivery_brief),
        "size_bytes": delivery_brief.stat().st_size,
    })
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "unsupported artifact_id" in str(excinfo.value)
    assert not (target_ws / "output" / "delivery" / "brief.md").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_source_pack_file_outside_sources(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    delivery_brief = archive_root / "delivery" / "brief.md"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "durable_source_evidence_or_source_pack":
            artifact["files"].append({
                "archive_path": "delivery/brief.md",
                "original_path": "output/delivery/brief.md",
                "sha256": _sha256_file(delivery_brief),
                "size_bytes": delivery_brief.stat().st_size,
            })
            artifact["file_count"] = len(artifact["files"])
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "input/sources" in str(excinfo.value)
    assert not (target_ws / "output" / "delivery" / "brief.md").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_source_candidates_inside_source_pack(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    source_candidates = archive_root / "fact_layer" / "input" / "sources" / "source_candidates.yaml"
    source_candidates.parent.mkdir(parents=True, exist_ok=True)
    source_candidates.write_text("artifact_type: source_plan_only\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "durable_source_evidence_or_source_pack":
            artifact["files"].append({
                "archive_path": "fact_layer/input/sources/source_candidates.yaml",
                "original_path": "input/sources/source_candidates.yaml",
                "sha256": _sha256_file(source_candidates),
                "size_bytes": source_candidates.stat().st_size,
            })
            artifact["file_count"] = len(artifact["files"])
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "source_candidates.yaml" in str(excinfo.value)
    assert not (target_ws / "input" / "sources" / "source_candidates.yaml").exists()
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_duplicate_non_pack_artifact_id(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    claim_record = next(
        item for item in manifest["fact_layer"]["artifacts"] if item.get("artifact_id") == "claim_ledger"
    )
    duplicate = dict(claim_record)
    duplicate["original_path"] = "output/intermediate/claim_ledger_duplicate.json"
    manifest["fact_layer"]["artifacts"].append(duplicate)
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "duplicate non-pack artifact" in str(excinfo.value)
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_duplicate_workspace_target(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_root = source_ws / "output" / "runs" / finalized["manifest"]["run_id"]
    archive_manifest = archive_root / "manifest.json"
    extra_source = archive_root / "fact_layer" / "input" / "sources" / "source-duplicate.md"
    extra_source.parent.mkdir(parents=True, exist_ok=True)
    extra_source.write_text("# duplicate target source\n", encoding="utf-8")
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    for artifact in manifest["fact_layer"]["artifacts"]:
        if artifact.get("artifact_id") == "durable_source_evidence_or_source_pack":
            artifact["files"].append({
                "archive_path": "fact_layer/input/sources/source-duplicate.md",
                "original_path": "input/sources/source-001.md",
                "sha256": _sha256_file(extra_source),
                "size_bytes": extra_source.stat().st_size,
            })
            artifact["file_count"] = len(artifact["files"])
    archive_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "duplicate import targets" in str(excinfo.value)
    assert excinfo.value.details["duplicate_targets"][0]["workspace_path"] == "input/sources/source-001.md"
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_stale_downstream_output(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    stale_delivery = target_ws / "output" / "delivery" / "brief.md"
    stale_delivery.parent.mkdir(parents=True, exist_ok=True)
    stale_delivery.write_text("stale delivery must not survive import\n", encoding="utf-8")
    before = stale_delivery.read_bytes()

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "existing source/output leftovers" in str(excinfo.value)
    assert stale_delivery.read_bytes() == before
    assert not _state_file(target_ws, "runtime_manifest").exists()


def test_import_fact_layer_transaction_rejects_existing_runtime_state(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"
    initialize_runtime_state(workspace=target_ws, repo_workdir=ROOT)

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=target_ws,
            archive=archive_manifest,
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == runtime_state.E_FACT_LAYER_IMPORT_INVALID
    assert "without existing runtime state" in str(excinfo.value)


def test_state_import_fact_layer_cli_outputs_json(tmp_path, capsys):
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source_ws = _write_workspace(source_root)
    target_ws = _write_workspace(target_root)
    _write_fact_layer_inputs(source_ws)
    finalized = _complete_finalized_workspace(source_ws)
    archive_manifest = source_ws / "output" / "runs" / finalized["manifest"]["run_id"] / "manifest.json"

    rc = main([
        "state",
        "import-fact-layer",
        "--workspace",
        str(target_ws),
        "--archive",
        str(archive_manifest),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["recipe"] == "fast-rerun"
    assert payload["fact_layer_import"]["source_run_id"] == finalized["manifest"]["run_id"]


def test_finalize_complete_rejects_archive_conflict_for_same_run_id(tmp_path):
    ws = _write_workspace(tmp_path)
    initialized = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    run_id = initialized["manifest"]["run_id"]
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    archive = ws / "output" / "runs" / run_id
    (archive / "delivery").mkdir(parents=True)
    (archive / "delivery" / "brief.md").write_text("conflicting archive content\n", encoding="utf-8")
    (archive / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "mabw.run_archive.v1",
                "run_id": run_id,
                "archived_at": "2026-06-11T00:00:00Z",
                "source": "finalize-complete",
                "files": [
                    {
                        "role": "delivery",
                        "archive_path": "delivery/brief.md",
                        "original_path": "output/delivery/brief.md",
                        "sha256": "not-the-real-sha",
                        "size_bytes": 0,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_RUN_ARCHIVE_CONFLICT
    after_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after_workflow == before_workflow
    assert after_workflow["current_stage"] == "finalize"
    assert after_workflow["stage_statuses"]["finalize"]["status"] == "ready"


def test_reset_state_archives_finalized_run_before_new_run(tmp_path):
    ws = _write_workspace(tmp_path)
    first = _complete_finalized_workspace(ws)
    old_run_id = first["manifest"]["run_id"]
    shutil.rmtree(ws / "output" / "runs" / old_run_id)

    second = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert second["manifest"]["run_id"] != old_run_id
    assert (ws / "output" / "runs" / old_run_id / "manifest.json").exists()


def test_fresh_reset_state_init_remains_reference_eligible(tmp_path):
    ws = _write_workspace(tmp_path)

    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    integrity = state["workflow_state"]["run_integrity"]
    assert integrity["status"] == "clean"
    assert integrity["reference_eligible"] is True
    assert integrity["clean_single_shot"] is True
    assert integrity["reasons"] == []
    assert [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ] == []


def test_reset_state_does_not_require_archive_for_incomplete_run(tmp_path):
    ws = _write_workspace(tmp_path)
    first = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    second = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert second["manifest"]["run_id"] != first["manifest"]["run_id"]
    assert not (ws / "output" / "runs" / first["manifest"]["run_id"]).exists()
    integrity = second["workflow_state"]["run_integrity"]
    assert integrity["status"] == "contaminated"
    assert integrity["reference_eligible"] is False
    assert integrity["clean_single_shot"] is False
    assert integrity["reasons"][0]["reason_code"] == "run_reset"
    contamination_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "run_integrity_contaminated"
    ]
    assert len(contamination_events) == 1
    assert contamination_events[0]["metadata"]["reason_code"] == "run_reset"


def test_reset_state_event_append_failure_rolls_back_control_files(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    first = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    old_run_id = first["manifest"]["run_id"]
    before_manifest = _state_file(ws, "runtime_manifest").read_bytes()
    before_workflow = _state_file(ws, "workflow_state").read_bytes()
    before_events = _state_file(ws, "event_log").read_bytes()
    _fail_appending_event_type(monkeypatch, "run_reset")

    with pytest.raises(RuntimeStateError) as excinfo:
        initialize_runtime_state(
            workspace=ws,
            repo_workdir=ROOT,
            reset_state=True,
        )

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_PARTIAL_WRITE
    assert _state_file(ws, "runtime_manifest").read_bytes() == before_manifest
    assert _state_file(ws, "workflow_state").read_bytes() == before_workflow
    assert _state_file(ws, "event_log").read_bytes() == before_events
    assert not (ws / "output" / "intermediate" / f"event_log.{old_run_id}.jsonl").exists()


def test_archive_rejects_finalize_report_delivery_artifact_outside_output_delivery(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    report_path = _intermediate(ws) / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifacts"] = [str(ws / "output" / "brief.md")]
    report["delivery_artifact_sha256"] = {
        str(ws / "output" / "brief.md"): _sha256_file(ws / "output" / "brief.md"),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader artifacts finalized and clean",
        )

    assert excinfo.value.error_code == runtime_state.operations.E_READER_FINAL_GATE_FAILED
    assert "output/delivery" in str(excinfo.value)


def test_finalize_complete_accepts_reader_facing_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_finalize_report(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    report_path = _intermediate(ws) / "quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["metadata"]["brief"] = "output/brief.md"
    report["metadata"]["ledger"] = "output/intermediate/claim_ledger.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader-facing gates and final artifacts passed",
    )

    assert state["workflow_state"]["current_stage"] is None
    assert state["workflow_state"]["stage_statuses"]["finalize"]["status"] == "complete"


def test_finalize_complete_ignores_legacy_quality_gate_projection(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_finalize_report(ws)
    _write_quality_gate_report(ws, stage_id="finalize", legacy_only=True)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="reader-facing gates and final artifacts passed",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"
    assert "output/intermediate/gates/finalize_quality_gate_report.json is required" in str(excinfo.value)


def test_completion_transactions_preserve_manifest_extensions(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    manifest_path = _state_file(ws, "runtime_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recipe"] = "fast-rerun"
    manifest["improvement"] = {
        "ledger_sha256": None,
        "memory_sha256": "zero",
        "snapshot_path": None,
        "snapshot_sha256": None,
        "materialized_entry_ids": [],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    after_stage = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_stage["recipe"] == "fast-rerun"
    assert after_stage["improvement"] == manifest["improvement"]

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    after_check = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_check["recipe"] == "fast-rerun"
    assert after_check["improvement"] == manifest["improvement"]

    _advance_to_finalize(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )
    after_finalize = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_finalize["recipe"] == "fast-rerun"
    assert after_finalize["improvement"] == manifest["improvement"]


def test_state_decide_rejects_out_of_order_stage_and_leaves_workflow_unchanged(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "auditor",
        "--decision",
        "continue",
        "--reason",
        "out of order",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "does not match current stage" in payload["error"]
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before


def test_state_decide_event_failure_leaves_workflow_unchanged(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            decision="block_run",
            reason="doctor failed",
        )

    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before


def test_state_check_preserves_explicit_block_decision(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    record_decision(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        decision="block_run",
        reason="doctor failed",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["workflow_state"]["blocked"] is True
    assert state["workflow_state"]["current_stage"] == "doctor"
    assert state["workflow_state"]["stage_statuses"]["doctor"]["status"] == "blocked"
    assert state["workflow_state"]["blocking_reason"] == "doctor failed"


def test_state_check_event_failure_leaves_state_unchanged(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "candidate_claims.json")
    _set_current_stage(ws, "scout")
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_event_log, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError):
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before
    assert not _state_file(ws, "artifact_registry").exists()


def test_state_check_rejects_unknown_event_type(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "cli",
                "event_id": "evt_fake",
                "event_type": "stage_completed",
                "reason": "model-written fake event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Unknown event type" in str(excinfo.value)


def test_state_check_rejects_unknown_event_actor(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "scout",
                "event_id": "evt_fake",
                "event_type": "decision_recorded",
                "reason": "model-written fake event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Unknown event actor" in str(excinfo.value)


def test_state_check_strict_json_reports_event_log_integrity_error(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "scout",
                "event_id": "evt_fake",
                "event_type": "stage_completed",
                "reason": "model-written fake event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main([
        "state",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error_code"] == runtime_state.operations.E_TRANSACTION_INTEGRITY


def test_state_check_rejects_event_log_missing_schema_version(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "cli",
                "event_id": "evt_fake",
                "event_type": "decision_recorded",
                "reason": "model-written incomplete event",
                "run_id": state["manifest"]["run_id"],
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Unsupported event log schema" in str(excinfo.value)


def test_state_check_rejects_non_newline_terminated_event_log(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(
        event_log.read_text(encoding="utf-8")
        + json.dumps(
            {
                "actor": "cli",
                "event_id": "evt_fake",
                "event_type": "decision_recorded",
                "reason": "model-written unterminated event",
                "run_id": state["manifest"]["run_id"],
                "schema_version": runtime_state.operations.EVENT_LOG_SCHEMA,
                "timestamp": "2026-06-12T00:00:00+00:00",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "newline-terminated" in str(excinfo.value)


def test_state_check_rejects_malformed_event_log_line(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    event_log = _state_file(ws, "event_log")
    event_log.write_text(event_log.read_text(encoding="utf-8") + "{bad json}\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert excinfo.value.error_code == runtime_state.operations.E_TRANSACTION_INTEGRITY
    assert "Invalid JSON event log line" in str(excinfo.value)


def test_state_check_only_writes_changed_events_once(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    (_intermediate(ws) / "candidate_claims.json").write_text("{broken", encoding="utf-8")

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    first_events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    second_events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()

    assert len(second_events) == len(first_events)
    event_types = [json.loads(line)["event_type"] for line in first_events]
    assert event_types.count("stage_status_changed") == 1
    assert event_types.count("run_blocked") == 1


def test_state_show_json_handles_corrupted_state_without_traceback(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _state_file(ws, "workflow_state").write_text("{broken", encoding="utf-8")

    rc = main(["state", "show", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Invalid JSON state file" in payload["error"]


def test_reset_state_archives_old_event_log(tmp_path):
    ws = _write_workspace(tmp_path)
    first = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    old_run_id = first["manifest"]["run_id"]

    second = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert second["manifest"]["run_id"] != old_run_id
    archived = ws / "output" / "intermediate" / f"event_log.{old_run_id}.jsonl"
    assert archived.exists()
    assert _state_file(ws, "event_log").exists()
    reset_event = json.loads(_state_file(ws, "event_log").read_text(encoding="utf-8").splitlines()[0])
    assert reset_event["event_type"] == "run_reset"
    assert reset_event["metadata"]["previous_run_id"] == old_run_id
    assert reset_event["metadata"]["archived_event_log"] == f"output/intermediate/event_log.{old_run_id}.jsonl"


def test_reset_state_recovers_from_corrupted_workflow_state(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _state_file(ws, "workflow_state").write_text("{broken", encoding="utf-8")

    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert state["ok"] is True
    assert state["workflow_state"]["current_stage"] == "doctor"


def test_state_paths_are_workspace_relative(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["manifest"]["runtime_state_files"] == RUNTIME_STATE_FILES
    for record in state["artifact_registry"]["artifacts"].values():
        assert not Path(record["path"]).is_absolute()


def test_show_runtime_state_reports_event_count(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = show_runtime_state(workspace=ws)

    assert state["event_count"] >= 1
