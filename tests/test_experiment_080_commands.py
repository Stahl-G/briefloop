from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.experiments import validate_run_record, validate_scorecard
from multi_agent_brief.orchestrator.runtime_state import (
    E_ASSESSMENT_TARGET_COMPLETE,
    RuntimeStateError,
    raise_if_auditable_target_complete_blocks_downstream,
)


SHA = "b" * 64
ROOT = Path(__file__).resolve().parent.parent
CLEAN_FIXTURE_MANIFEST = (
    ROOT
    / "tests"
    / "fixtures"
    / "fast_rerun_clean_archive"
    / "output"
    / "runs"
    / "mabw-20260614T000000Z-public0001"
    / "manifest.json"
)
PLAN_ONLY_FIXTURE_MANIFEST = (
    ROOT
    / "tests"
    / "fixtures"
    / "fast_rerun_source_candidates_only_archive"
    / "output"
    / "runs"
    / "mabw-20260614T000000Z-planonly0001"
    / "manifest.json"
)


def _case_manifest() -> dict:
    return {
        "schema_version": "mabw.experiment_080.case.v1",
        "experiment_id": "MABW-080",
        "case_id": "weekly_public_001",
        "case_title": "Weekly public brief",
        "public_safe": True,
        "created_at": "2026-06-14T00:00:00Z",
        "repo_commit": "abc123",
        "conditions": ["baseline", "memory"],
        "frozen_fact_layer": {"manifest_path": "frozen_fact_layer.json"},
        "guidance_set": {"path": "guidance_set.json"},
        "allowed_claims": {"a_grade_requires_same_fact_layer": True},
    }


def _frozen_fact_layer() -> dict:
    return {
        "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
        "source_run_id": "mabw-20260614T000000Z-test",
        "source_archive_path": "output/runs/mabw-20260614T000000Z-test/manifest.json",
        "artifacts": [
            {
                "artifact_id": "durable_source_evidence_or_source_pack",
                "path": "input/sources/source_pack.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "input_classification",
                "path": "output/input_classification.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "candidate_claims",
                "path": "output/intermediate/candidate_claims.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "screened_candidates",
                "path": "output/intermediate/screened_candidates.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "claim_ledger",
                "path": "output/intermediate/claim_ledger.json",
                "sha256": SHA,
            },
        ],
    }


def _guidance_set() -> dict:
    return {
        "schema_version": "mabw.experiment_080.guidance_set.v1",
        "entries": [
            {
                "entry_id": "AG-0001",
                "guidance_text": "Lead with business implication before news recap.",
                "source": "improvement_ledger",
                "expected_manifestation": "Business implication appears before news recap.",
                "relevance_rule": "Applies to management-facing market briefs.",
            }
        ],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: dict) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_case(case_dir: Path) -> None:
    case_dir.mkdir(parents=True)
    _write_json(case_dir / "case_manifest.json", _case_manifest())
    _write_json(case_dir / "frozen_fact_layer.json", _frozen_fact_layer())
    _write_json(case_dir / "guidance_set.json", _guidance_set())


def _write_case_from_archive(case_dir: Path, archive_manifest: Path, *, source_pack_sha: str | None = None) -> None:
    case_dir.mkdir(parents=True)
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    fact_layer = manifest["fact_layer"]
    artifacts = []
    for artifact in fact_layer["artifacts"]:
        artifact_id = artifact["artifact_id"]
        if artifact_id == "durable_source_evidence_or_source_pack":
            sha = source_pack_sha or artifact["pack_sha256"]
            path = "input/sources/source_pack.json"
        else:
            sha = artifact["sha256"]
            path = artifact["original_path"]
        artifacts.append({"artifact_id": artifact_id, "path": path, "sha256": sha})
    _write_json(case_dir / "case_manifest.json", _case_manifest())
    _write_json(
        case_dir / "frozen_fact_layer.json",
        {
            "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
            "source_run_id": manifest["run_id"],
            "source_archive_path": f"output/runs/{manifest['run_id']}/manifest.json",
            "artifacts": artifacts,
        },
    )
    _write_json(case_dir / "guidance_set.json", _guidance_set())


def _write_scaffold_workspace(ws: Path) -> None:
    ws.mkdir(parents=True)
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        "project:\n"
        "  name: \"080 Seed Workspace\"\n"
        "language:\n"
        "  interface: \"zh-CN\"\n"
        "  output: \"zh-CN\"\n"
        "  source_handling: \"preserve_original\"\n"
        "input:\n"
        "  path: \"input\"\n"
        "output:\n"
        "  path: \"output\"\n"
        "report:\n"
        "  title: \"Seed Report\"\n"
        "  date: \"2026-07-15\"\n"
        "  max_source_age_days: 30\n"
        "  fail_on_stale_source: false\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# Seed user direction\n\nKeep this reader and task direction.\n", encoding="utf-8")
    (ws / "audience_profile.md").write_text("# Seed audience\n\nBoard-facing Chinese brief.\n", encoding="utf-8")


def _write_auditable_condition_metadata(ws: Path, *, condition: str = "memory") -> None:
    condition_path = ws / "experiment" / "080" / "condition.json"
    condition_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        condition_path,
        {
            "schema_version": "mabw.experiment_080.condition.v1",
            "experiment_id": "MABW-080",
            "case_id": "weekly_public_001",
            "condition": condition,
            "assessment_target": "auditable_brief",
            "assessment_target_manifest": {
                "assessment_target": "auditable_brief",
                "target_status_semantics": "auditor_ready_internal_auditable_draft",
            },
        },
    )


def _write_treatment_condition_metadata(
    ws: Path,
    *,
    condition: str,
    prompt_block: bool = False,
    leak_guidance: bool = False,
    extra_prompt_guidance: bool = False,
) -> None:
    allowed = {
        "baseline": [],
        "memory": ["output/intermediate/improvement_memory_snapshot.md"],
        "prompt_only": ["handoff.prompt_guidance_block"],
    }[condition]
    metadata = {
        "schema_version": "mabw.experiment_080.scaffold_condition.v1",
        "experiment_id": "MABW-080",
        "case_id": "weekly_public_001",
        "condition": condition,
        "assessment_target": "delivery_brief",
        "treatment_visibility": {
            "schema_version": "mabw.experiment_080.treatment_visibility.v1",
            "condition": condition,
            "allowed_visible_treatment_materials": allowed,
            "forbidden_visible_fields": ["expected_manifestation"]
            if condition == "prompt_only"
            else ["guidance_text", "expected_manifestation"],
        },
        "treatment": {
            "condition": condition,
            "guidance_entry_ids": ["AG-0001"],
            "improvement_memory": "disabled" if condition != "memory" else "requires_approved_snapshot",
        },
        "handoff": {},
    }
    if prompt_block:
        guidance = [
            {
                "entry_id": "AG-0001",
                "guidance_text": "Lead with business implication before news recap.",
            }
        ]
        if extra_prompt_guidance:
            guidance.append({
                "entry_id": "AG-9999",
                "guidance_text": "Add an extra treatment angle not present in the assessment set.",
            })
        metadata["handoff"]["prompt_guidance_block"] = {
            "schema_version": "mabw.experiment_080.prompt_guidance_block.v1",
            "source": "case_guidance_set",
            "guidance": guidance,
        }
    if leak_guidance:
        metadata["treatment"]["guidance_entries"] = [
            {
                "entry_id": "AG-0001",
                "guidance_text": "Lead with business implication before news recap.",
                "expected_manifestation": "Business implication appears before news recap.",
            }
        ]
    path = ws / "experiment" / "080" / "condition.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, metadata)


def _write_improvement_memory_snapshot(
    ws: Path,
    *,
    guidance_text: bool = True,
    selected_entry_ids: list[str] | None = None,
) -> None:
    path = ws / "output" / "intermediate" / "improvement_memory_snapshot.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    selected = ", ".join(selected_entry_ids or ["AG-0001"])
    body = [
        "<!-- mabw:improvement-memory-snapshot",
        "schema: multi-agent-brief-improvement-memory/v1",
        "run_id: mabw-20260614T000000Z-public0001",
        "ledger_sha256: " + "a" * 64,
        "memory_sha256: " + "b" * 64,
        f"selected_entry_ids: {selected}",
        "-->",
        "",
        "# Improvement Memory Snapshot",
        "",
    ]
    if guidance_text:
        body.append("- Guidance: Lead with business implication before news recap.")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _copy_archive_to_workspace(ws: Path, archive_manifest: Path) -> Path:
    run_dir = archive_manifest.parent
    target = ws / "output" / "runs" / run_dir.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run_dir, target)
    return target / "manifest.json"


def _copy_archive_to_case_source(case_dir: Path, archive_manifest: Path) -> Path:
    run_dir = archive_manifest.parent
    target = case_dir / "output" / "runs" / run_dir.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run_dir, target)
    return target / "manifest.json"


def _copy_fact_layer_into_workspace(ws: Path, archive_manifest: Path) -> list[dict]:
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    archive_root = archive_manifest.parent
    imported_files = []
    for artifact in manifest["fact_layer"]["artifacts"]:
        records = artifact.get("files") if isinstance(artifact.get("files"), list) else [artifact]
        for record in records:
            target = ws / record["original_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive_root / record["archive_path"], target)
            imported_files.append({
                "artifact_id": artifact["artifact_id"],
                "archive_path": record["archive_path"],
                "workspace_path": record["original_path"],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
            })
    return imported_files


def _write_auditable_target_workspace(
    ws: Path,
    *,
    run_id: str,
    source_archive_manifest: Path,
    active_repair: bool = False,
) -> None:
    intermediate = ws / "output" / "intermediate"
    gates = intermediate / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    source_manifest = json.loads(source_archive_manifest.read_text(encoding="utf-8"))
    imported_files = _copy_fact_layer_into_workspace(ws, source_archive_manifest)
    audited = intermediate / "audited_brief.md"
    audit = intermediate / "audit_report.json"
    gate = gates / "auditor_quality_gate_report.json"
    claim_ledger = intermediate / "claim_ledger.json"
    audited.write_text("# Audited brief\n\nBusiness implication first. [src:CL-0001]\n", encoding="utf-8")
    _write_json(audit, {"audit_status": "pass", "audit_score": 100, "findings": []})
    _write_json(
        gate,
        {
            "schema_version": "multi-agent-brief-quality-gates/v1",
            "status": "pass",
            "metadata": {
                "gate_stage_id": "auditor",
                "stage_id": "auditor",
                "brief": "output/intermediate/audited_brief.md",
                "ledger": "output/intermediate/claim_ledger.json",
            },
            "gate_results": [
                {"gate_id": "material_fact", "status": "pass", "blocking": False, "finding_ids": []},
                {"gate_id": "freshness", "status": "pass", "blocking": False, "finding_ids": []},
                {"gate_id": "target_relevance", "status": "pass", "blocking": False, "finding_ids": []},
            ],
            "findings": [],
        },
    )
    stage_statuses = {
        "doctor": {"status": "complete", "metadata": {"satisfied_by_import": True}},
        "source-discovery": {"status": "complete", "metadata": {"satisfied_by_import": True}},
        "input-governance": {"status": "complete", "metadata": {"satisfied_by_import": True}},
        "scout": {"status": "complete", "metadata": {"satisfied_by_import": True}},
        "screener": {"status": "complete", "metadata": {"satisfied_by_import": True}},
        "claim-ledger": {"status": "complete", "metadata": {"satisfied_by_import": True}},
        "analyst": {"status": "complete"},
        "editor": {"status": "complete"},
        "auditor": {
            "status": "complete",
            "metadata": {
                "audit_binding": {
                    "schema_version": "mabw.auditable_audit_binding.v1",
                    "source": "auditor_stage_complete",
                    "claim_ledger_sha256": _sha256_file(claim_ledger),
                    "audited_brief_sha256": _sha256_file(audited),
                    "audit_report_sha256": _sha256_file(audit),
                    "relevant_repair_transaction_ids": [],
                    "auditor_stage_transaction_id": "txn-auditor",
                    "stage_completion_event": {
                        "event_type": "decision_recorded",
                        "transaction_id": "txn-auditor",
                        "event_id": None,
                        "sequence": None,
                        "availability": "not_available_until_event_append",
                    },
                }
            },
        },
        "finalize": {"status": "ready"},
    }
    _write_terminal_runtime(
        ws,
        run_id=run_id,
        recipe="fast-rerun",
        fact_layer_import={
            "schema_version": "mabw.fact_layer_import.v1",
            "imported_at": "2026-06-14T00:00:00+00:00",
            "source_run_id": source_manifest["run_id"],
            "source_archive_manifest": f"output/runs/{source_manifest['run_id']}/manifest.json",
            "source_archive_manifest_sha256": _sha256_file(source_archive_manifest),
            "fact_layer_status": source_manifest["fact_layer"]["status"],
            "fact_layer_sha256": _sha256_json(source_manifest["fact_layer"]),
            "satisfied_stage_ids": ["doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger"],
            "required_artifact_ids": [
                "durable_source_evidence_or_source_pack",
                "input_classification",
                "candidate_claims",
                "screened_candidates",
                "claim_ledger",
            ],
            "imported_file_count": len(imported_files),
            "imported_files": imported_files,
            "timing_comparability": "downstream_only",
        },
        current_stage="finalize",
        stage_statuses=stage_statuses,
    )
    workflow_path = intermediate / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    if active_repair:
        workflow["active_repair"] = {"repair_owner": "editor", "transaction_id": "repair-test"}
        _write_json(workflow_path, workflow)
    _write_json(
        intermediate / "artifact_registry.json",
        {
            "schema_version": "multi-agent-brief-artifact-registry/v1",
            "artifacts": {
                "claim_ledger": {
                    "path": "output/intermediate/claim_ledger.json",
                    "status": "valid",
                    "validation_result": "valid_claim_ledger_schema",
                    "sha256": _sha256_file(claim_ledger),
                },
                "audited_brief": {
                    "path": "output/intermediate/audited_brief.md",
                    "status": "valid",
                    "validation_result": "reference_only",
                    "sha256": _sha256_file(audited),
                },
                "audit_report": {
                    "path": "output/intermediate/audit_report.json",
                    "status": "valid",
                    "validation_result": "valid_audit_report_schema",
                    "sha256": _sha256_file(audit),
                },
                "auditor_quality_gate_report": {
                    "path": "output/intermediate/gates/auditor_quality_gate_report.json",
                    "status": "valid",
                    "validation_result": "conditional_quality_gate_only",
                    "sha256": _sha256_file(gate),
                },
            },
        },
    )
    events = [
        {
            "schema_version": "multi-agent-brief-event-log/v1",
            "event_id": "evt0",
            "run_id": run_id,
            "created_at": "2026-06-14T00:00:00+00:00",
            "event_type": "run_initialized",
            "actor": "cli",
            "stage_id": None,
            "artifact_id": None,
            "decision": None,
            "reason": "test",
            "metadata": {},
        }
    ]
    for idx, stage_id in enumerate(("analyst", "editor", "auditor"), start=1):
        events.append({
            "schema_version": "multi-agent-brief-event-log/v1",
            "event_id": f"evt{idx}",
            "run_id": run_id,
            "created_at": f"2026-06-14T00:0{idx}:00+00:00",
            "event_type": "decision_recorded",
            "actor": "cli",
            "stage_id": stage_id,
            "artifact_id": None,
            "decision": "continue",
            "reason": "test complete",
            "metadata": {"transaction_id": f"txn-{stage_id}"},
        })
    (intermediate / "event_log.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _mutate_auditable_gate_report(ws: Path, mutator) -> None:
    gate_path = ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    mutator(gate)
    _write_json(gate_path, gate)
    registry_path = ws / "output" / "intermediate" / "artifact_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["artifacts"]["auditor_quality_gate_report"]["sha256"] = _sha256_file(gate_path)
    _write_json(registry_path, registry)


def _add_scorecard_archive_reports(archive_manifest: Path) -> None:
    archive_root = archive_manifest.parent
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    records = manifest.setdefault("files", [])

    def add_json(archive_path: str, original_path: str, payload: dict) -> None:
        path = archive_root / archive_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, payload)
        records.append({
            "role": "intermediate" if archive_path.startswith("intermediate/") else "control",
            "archive_path": archive_path,
            "original_path": original_path,
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        })

    add_json(
        "intermediate/finalize_report.json",
        "output/intermediate/finalize_report.json",
        {
            "status": "pass",
            "reader_clean": {
                "status": "pass",
                "markdown_blocking_count": 0,
                "docx_blocking_count": 0,
            },
            "delivery_artifacts": ["output/delivery/brief.md"],
        },
    )
    add_json(
        "intermediate/gates/auditor_quality_gate_report.json",
        "output/intermediate/gates/auditor_quality_gate_report.json",
        {"status": "pass", "gate_results": []},
    )
    add_json(
        "intermediate/gates/finalize_quality_gate_report.json",
        "output/intermediate/gates/finalize_quality_gate_report.json",
        {"status": "pass", "gate_results": []},
    )
    add_json(
        "control/artifact_registry.json",
        "output/intermediate/artifact_registry.json",
        {"schema_version": "multi-agent-brief-artifact-registry/v1", "artifacts": {}},
    )
    _write_json(archive_manifest, manifest)


def _write_terminal_runtime(
    ws: Path,
    *,
    run_id: str,
    runtime: str = "codex",
    recipe: str | None = None,
    fact_layer_import: dict | None = None,
    run_integrity: dict | str | None = None,
    current_stage=None,
    finalize_status: str = "complete",
    stage_statuses: dict | None = None,
) -> None:
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    runtime_manifest = {
        "schema_version": "multi-agent-brief-runtime-manifest/v1",
        "run_id": run_id,
        "runtime": runtime,
    }
    if recipe is not None:
        runtime_manifest["recipe"] = recipe
    if fact_layer_import is not None:
        runtime_manifest["fact_layer_import"] = fact_layer_import
    _write_json(
        intermediate / "runtime_manifest.json",
        runtime_manifest,
    )
    if run_integrity is None:
        run_integrity = {
            "status": "clean",
            "reference_eligible": True,
            "clean_single_shot": True,
            "reasons": [],
        }
    _write_json(
        intermediate / "workflow_state.json",
        {
            "schema_version": "multi-agent-brief-workflow-state/v1",
            "run_id": run_id,
            "current_stage": current_stage,
            "stage_statuses": stage_statuses or {"finalize": {"status": finalize_status}},
            "run_integrity": run_integrity,
        },
    )


def _patch_archive_manifest(path: Path, **updates) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    _write_json(path, payload)


def _register_args(case_dir: Path, ws: Path, output: Path, *, condition: str = "memory") -> list[str]:
    return [
        "experiments",
        "080",
        "register-run",
        "--case",
        str(case_dir),
        "--condition",
        condition,
        "--workspace",
        str(ws),
        "--output",
        str(output),
        "--json",
    ]


def _score_args(case_dir: Path, run_record: Path, output: Path) -> list[str]:
    return [
        "experiments",
        "080",
        "score-run",
        "--case",
        str(case_dir),
        "--run-record",
        str(run_record),
        "--output",
        str(output),
        "--json",
    ]


def _assessment_args(scorecard: Path, assessment: Path, output: Path) -> list[str]:
    return [
        "experiments",
        "080",
        "import-assessment",
        "--scorecard",
        str(scorecard),
        "--assessment",
        str(assessment),
        "--output",
        str(output),
        "--json",
    ]


def _blind_assessment_args(blind_pack: Path, reveal_mapping: Path, assessment: Path, output: Path) -> list[str]:
    return [
        "experiments",
        "080",
        "import-assessment",
        "--blind-pack",
        str(blind_pack),
        "--reveal-mapping",
        str(reveal_mapping),
        "--assessment",
        str(assessment),
        "--output",
        str(output),
        "--json",
    ]


def _export_blind_pack_args(
    case_dir: Path,
    output: Path,
    scorecards: list[Path],
    *,
    seed: str = "fixed-seed",
) -> list[str]:
    args = [
        "experiments",
        "080",
        "export-blind-pack",
        "--case",
        str(case_dir),
        "--output",
        str(output),
        "--seed",
        seed,
        "--json",
    ]
    for scorecard in scorecards:
        args.extend(["--scorecard", str(scorecard)])
    return args


def _summarize_args(case_dir: Path, output: Path | None = None, *, scorecards: list[Path] | None = None) -> list[str]:
    args = [
        "experiments",
        "080",
        "summarize",
        "--case",
        str(case_dir),
        "--json",
    ]
    for scorecard in scorecards or []:
        args.extend(["--scorecard", str(scorecard)])
    if output is not None:
        args.extend(["--output", str(output)])
    return args


def _scaffold_args(
    case_dir: Path,
    workspace: Path,
    *,
    condition: str = "memory",
    archive: Path | None = CLEAN_FIXTURE_MANIFEST,
) -> list[str]:
    args = [
        "experiments",
        "080",
        "scaffold-condition",
        "--case",
        str(case_dir),
        "--condition",
        condition,
        "--workspace",
        str(workspace),
        "--runtime",
        "codex",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ]
    if archive is not None:
        args.extend(["--archive", str(archive)])
    return args


def _assessment_payload(
    *,
    method: str = "human",
    entry_id: str = "AG-0001",
    run_id: str = "mabw-20260614T000000Z-public0001",
) -> dict:
    return {
        "schema_version": "mabw.experiment_080.assessment.v1",
        "experiment_id": "MABW-080",
        "case_id": "weekly_public_001",
        "condition": "memory",
        "run_id": run_id,
        "assessed_at": "2026-06-16T00:00:00Z",
        "assessed_by": "masked-human-reviewer",
        "guidance_scores": [
            {
                "entry_id": entry_id,
                "relevant": True,
                "manifestation_score": 2,
                "overapplication": False,
                "assessment_method": method,
                "evidence_excerpt": "The brief leads with the business implication.",
            }
        ],
    }


def _blind_assessment_payload(*, blind_item_id: str, artifact_sha256: str, method: str = "human") -> dict:
    payload = _assessment_payload(method=method)
    payload.pop("condition", None)
    payload.pop("run_id", None)
    payload["blind_item_id"] = blind_item_id
    payload["blind_artifact_sha256"] = artifact_sha256
    return payload


def _write_auditable_scorecard_for_condition(
    tmp_path: Path,
    capsys,
    *,
    case_dir: Path,
    condition: str,
    run_id: str,
) -> Path:
    ws = tmp_path / f"{condition}-workspace"
    ws.mkdir()
    _write_auditable_target_workspace(ws, run_id=run_id, source_archive_manifest=CLEAN_FIXTURE_MANIFEST)
    if condition == "memory":
        _write_treatment_condition_metadata(ws, condition=condition)
        _write_improvement_memory_snapshot(ws)
    elif condition == "prompt_only":
        _write_treatment_condition_metadata(ws, condition=condition, prompt_block=True)
    else:
        _write_treatment_condition_metadata(ws, condition=condition)
    run_record = ws / f"{condition}.run_record.json"
    scorecard = ws / f"{condition}.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record, condition=condition)) == 0
    capsys.readouterr()
    assert main(_score_args(case_dir, run_record, scorecard)) == 0
    capsys.readouterr()
    return scorecard


def _write_auditable_three_condition_case(case_dir: Path) -> None:
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    manifest["assessment_target"] = "auditable_brief"
    manifest["conditions"] = ["baseline", "memory", "prompt_only"]
    _write_json(case_dir / "case_manifest.json", manifest)


def _scorecard_payload(
    *,
    condition: str = "memory",
    run_id: str = "mabw-20260614T000000Z-public0001",
    validity_class: str = "A_controlled",
    assessment_status: str = "assessed",
    manifestation_score: int = 2,
    overapplication: bool = False,
    assessment_method: str = "human",
    reader_clean_pass: bool = True,
    coverage_delta: float | None = None,
    timing_status: str = "available",
    blind_assessment_verified: bool = False,
) -> dict:
    control_integrity = {
        "terminal_workflow": True,
        "run_integrity_clean": True,
        "reference_eligible": True,
        "artifact_registry_valid": True,
        "quality_gates_passed": True,
        "archive_present": True,
        "archive_schema_valid": True,
        "finalize_complete": True,
        "finalize_report_pass": True,
        "timing_available": timing_status == "available",
        "treatment_isolation_passed": True,
    }
    frozen_fact_layer = {"matches_case": True, "mismatches": []}
    if validity_class == "invalid_contaminated":
        control_integrity["run_integrity_clean"] = False
        control_integrity["reference_eligible"] = False
    if validity_class == "invalid_fact_layer_mismatch":
        frozen_fact_layer["matches_case"] = False
        frozen_fact_layer["mismatches"] = [{"artifact_id": "claim_ledger"}]
    guidance_scores = []
    if assessment_status != "needs_assessment":
        guidance_scores = [
            {
                "entry_id": "AG-0001",
                "relevant": True,
                "manifestation_score": manifestation_score,
                "overapplication": overapplication,
                "assessment_method": assessment_method,
                "evidence_excerpt": "Observed in the reader brief.",
            }
        ]
    guidance_assessment = {"status": "needs_assessment", "guidance_entry_ids": ["AG-0001"]}
    if assessment_status != "needs_assessment":
        guidance_assessment = {
            "status": "assessed",
            "source": "imported_assessment",
            "assessment_methods": [assessment_method],
            "guidance_entry_ids": ["AG-0001"],
        }
        if blind_assessment_verified:
            guidance_assessment.update({
                "blind_item_id": "BI-A",
                "blind_artifact_sha256": "4" * 64,
                "blind_pack": {
                    "schema_version": "mabw.experiment_080.blind_pack.v1",
                    "blind_item_id": "BI-A",
                    "artifact_sha256": "4" * 64,
                    "scorecard_sha256": "5" * 64,
                    "hash_verified": True,
                },
            })
    return {
        "schema_version": "mabw.experiment_080.scorecard.v1",
        "experiment_id": "MABW-080",
        "case_id": "weekly_public_001",
        "condition": condition,
        "run_id": run_id,
        "validity_class": validity_class,
        "assessment_status": assessment_status,
        "control_integrity": control_integrity,
        "frozen_fact_layer": frozen_fact_layer,
        "reader_clean": {"pass": reader_clean_pass},
        "quality_gates": {"passed": control_integrity["quality_gates_passed"]},
        "finalize": {
            "complete": control_integrity["finalize_complete"],
            "report_pass": control_integrity["finalize_report_pass"],
        },
        "archive": {
            "present": control_integrity["archive_present"],
            "schema_valid": control_integrity["archive_schema_valid"],
        },
        "timing_summary": {
            "status": timing_status,
            "completed_stage_count": 8,
        },
        "coverage_delta": (
            {"status": "computed", "delta": coverage_delta}
            if coverage_delta is not None
            else {"status": "not_computed", "reason": "test"}
        ),
        "guidance_assessment": guidance_assessment,
        "guidance_scores": guidance_scores,
        "treatment_isolation": {
            "schema_version": "mabw.experiment_080.treatment_visibility.v1",
            "status": "pass",
            "condition": condition,
        },
        "regression": {},
        "notes": [],
    }


def _auditable_scorecard_payload(**overrides) -> dict:
    payload = _scorecard_payload(**overrides)
    payload["assessment_target"] = "auditable_brief"
    payload["claim_scope"] = [
        "guidance_manifestation_in_audited_brief",
        "evidence_use_under_frozen_fact_layer",
        "auditor_gate_passage",
    ]
    payload["excluded_claim_scope"] = [
        "reader_clean_delivery",
        "finalize_transform_correctness",
        "management_ready_output",
        "docx_pdf_delivery_quality",
    ]
    payload["reader_clean"] = {
        "pass": None,
        "status": "not_required_for_target",
        "source": "assessment_target.auditable_brief",
    }
    payload["control_integrity"] = {
        "terminal_workflow": False,
        "auditor_complete": True,
        "run_integrity_clean": True,
        "reference_eligible": True,
        "artifact_registry_valid": True,
        "audit_binding_valid": True,
        "audited_brief_frozen_valid": True,
        "audit_report_frozen_valid": True,
        "auditor_gate_report_valid": True,
        "auditor_gates_no_blocking": True,
        "fact_layer_matches": True,
        "treatment_isolation_passed": True,
        "quality_gates_passed": True,
        "archive_present": False,
        "archive_schema_valid": False,
        "finalize_complete": False,
        "finalize_report_pass": False,
        "timing_available": True,
    }
    payload["finalize"] = {
        "complete": False,
        "report_pass": False,
        "report_status": "not_required_for_target",
    }
    payload["archive"] = {"present": False, "schema_valid": False}
    payload["audit_binding"] = {
        "schema_version": "mabw.auditable_audit_binding.v1",
        "status": "valid",
        "source": "workflow_state.stage_statuses.auditor.metadata.audit_binding",
        "claim_ledger_sha256": "1" * 64,
        "audited_brief_sha256": "2" * 64,
        "audit_report_sha256": "3" * 64,
        "relevant_repair_transaction_ids": [],
        "auditor_stage_transaction_id": "txn-auditor",
    }
    payload["treatment_isolation"] = {
        "schema_version": "mabw.experiment_080.treatment_visibility.v1",
        "status": "pass",
        "condition": payload["condition"],
    }
    return payload


def _write_scorecard_draft_from_fixture(tmp_path: Path, capsys) -> tuple[Path, Path]:
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="memory")
    _write_improvement_memory_snapshot(ws)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0
    capsys.readouterr()
    return case_dir, scorecard_path


def test_experiments_080_validate_case_json_ok(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["case_id"] == "weekly_public_001"
    assert sorted(payload["validated_files"]) == [
        "case_manifest.json",
        "frozen_fact_layer.json",
        "guidance_set.json",
    ]


def test_experiments_080_validate_case_missing_frozen_fact_layer_fails(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    (case_dir / "frozen_fact_layer.json").unlink()

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(error["code"] == "missing_case_file" for error in payload["errors"])


def test_experiments_080_validate_case_source_candidates_only_fails(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(
        case_dir / "frozen_fact_layer.json",
        {
            "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
            "source_run_id": "mabw-20260614T000000Z-test",
            "artifacts": [
                {
                    "artifact_id": "source_candidates",
                    "path": "output/intermediate/source_candidates.yaml",
                    "sha256": SHA,
                }
            ],
        },
    )

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(error["code"] == "source_plan_not_evidence" for error in payload["errors"])


def test_experiments_080_validate_case_is_read_only(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    before = {
        path.relative_to(case_dir).as_posix(): path.read_bytes()
        for path in sorted(case_dir.glob("*.json"))
    }

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 0
    json.loads(capsys.readouterr().out)
    after = {
        path.relative_to(case_dir).as_posix(): path.read_bytes()
        for path in sorted(case_dir.glob("*.json"))
    }
    assert after == before


def test_experiments_080_scaffold_condition_imports_fact_layer_workspace(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "baseline workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["condition"] == "baseline"
    assert payload["fact_layer_import"]["source_run_id"] == "mabw-20260614T000000Z-public0001"
    assert (ws / "config.yaml").exists()
    assert (ws / "input" / "sources" / "source-001.md").exists()
    assert (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "delivery" / "brief.md").exists()

    workflow = json.loads((ws / "output" / "intermediate" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_stage"] == "analyst"
    assert workflow["stage_statuses"]["claim-ledger"]["metadata"]["satisfied_by_import"] is True

    metadata = json.loads((ws / "experiment" / "080" / "condition.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "mabw.experiment_080.scaffold_condition.v1"
    assert metadata["workspace_path"] == "<redacted-workspace>"
    assert metadata["treatment_visibility"]["allowed_visible_treatment_materials"] == []
    assert metadata["treatment"]["improvement_memory"] == "disabled"
    assert "guidance_entries" not in metadata["treatment"]
    metadata_text = json.dumps(metadata, ensure_ascii=False)
    assert "Lead with business implication before news recap." not in metadata_text
    assert "Business implication appears before news recap." not in metadata_text
    config = (ws / "config.yaml").read_text(encoding="utf-8")
    assert "interface: \"zh-CN\"" in config
    assert "date: \"2026-07-15\"" in config
    assert "max_source_age_days: 30" in config
    manifest = json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))
    freshness = manifest["fact_layer_import"]["freshness_at_import"]
    assert freshness["report_date"] == "2026-07-15"
    assert freshness["max_source_age_days"] == 30
    instructions = (ws / "experiment" / "080" / "operator_instructions.md").read_text(encoding="utf-8")
    assert "Do not rerun source-discovery, Scout, Screener, or Claim Ledger" in instructions
    assert "multi-agent-brief run --workspace" in instructions
    assert f"--workspace '{ws}'" in instructions
    assert not (ws / "improvement" / "memory.md").exists()


def test_experiments_080_scaffold_accepts_init_sources_readme_placeholder(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "baseline-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir()
    (sources_dir / "README.md").write_text(
        "# External Evidence Sources\n\nPlace source files here.\n",
        encoding="utf-8",
    )

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert not (sources_dir / "README.md").exists()
    assert (sources_dir / "source-001.md").exists()


def test_experiments_080_scaffold_rejects_runtime_visible_guidance_leakage(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "baseline-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    (ws / "user.md").write_text(
        "# Seed user direction\n\nLead with business implication before news recap.\n",
        encoding="utf-8",
    )

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert payload["details"]["condition"] == "baseline"
    assert payload["details"]["treatment_guidance_leaks"] == [
        {"path": "user.md", "entry_id": "AG-0001", "field": "guidance_text"}
    ]
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()


def test_experiments_080_scaffold_rejects_demo_source_leftovers(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "baseline-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir()
    (sources_dir / "README.md").write_text("# External Evidence Sources\n", encoding="utf-8")
    (sources_dir / "news.json").write_text('{"items": []}\n', encoding="utf-8")
    (sources_dir / "market_data.json").write_text('{"items": []}\n', encoding="utf-8")

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_SCAFFOLD_IMPORT_FAILED"
    assert payload["details"]["runtime_error_code"] == "E_FACT_LAYER_IMPORT_INVALID"
    assert payload["details"]["runtime_error_details"]["existing_leftovers"] == [
        "input/sources/market_data.json",
        "input/sources/news.json",
    ]
    assert (sources_dir / "README.md").exists()
    assert (sources_dir / "news.json").exists()
    assert not (ws / "experiment" / "080" / "condition.json").exists()


def test_experiments_080_scaffold_rejects_existing_real_source_leftover(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "baseline-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    sources_dir = ws / "input" / "sources"
    sources_dir.mkdir()
    (sources_dir / "source-note.md").write_text("Real source-like material.\n", encoding="utf-8")

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_SCAFFOLD_IMPORT_FAILED"
    assert payload["details"]["runtime_error_details"]["existing_leftovers"] == [
        "input/sources/source-note.md"
    ]
    assert (sources_dir / "source-note.md").exists()
    assert not (ws / "experiment" / "080" / "condition.json").exists()


def test_experiments_080_scaffold_prompt_only_records_guidance_without_memory(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "prompt-only-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["conditions"] = ["baseline", "memory", "prompt_only"]
    case_manifest["allowed_claims"]["memory_mechanism_adds_over_prompt"] = True
    _write_json(case_dir / "case_manifest.json", case_manifest)

    rc = main(_scaffold_args(case_dir, ws, condition="prompt_only"))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    metadata = json.loads((ws / "experiment" / "080" / "condition.json").read_text(encoding="utf-8"))
    assert metadata["condition"] == "prompt_only"
    assert metadata["treatment_visibility"]["allowed_visible_treatment_materials"] == [
        "handoff.prompt_guidance_block"
    ]
    assert metadata["treatment"]["improvement_memory"] == "disabled"
    assert "prompt_only_guidance" not in metadata["treatment"]
    prompt_block = metadata["handoff"]["prompt_guidance_block"]
    assert prompt_block["schema_version"] == "mabw.experiment_080.prompt_guidance_block.v1"
    assert prompt_block["guidance"] == [
        {
            "entry_id": "AG-0001",
            "guidance_text": "Lead with business implication before news recap.",
        }
    ]
    assert "Business implication appears before news recap." not in json.dumps(metadata, ensure_ascii=False)
    instructions = (ws / "experiment" / "080" / "operator_instructions.md").read_text(encoding="utf-8")
    assert "Do not create or use Improvement Memory" in instructions
    assert "handoff.prompt_guidance_block" in instructions
    assert not (ws / "improvement" / "memory.md").exists()


def test_experiments_080_scaffold_baseline_rejects_existing_improvement_ledger(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "baseline-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    improvement_dir = ws / "improvement"
    improvement_dir.mkdir()
    (improvement_dir / "ledger.jsonl").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-improvement-ledger/v1",
            "entry_id": "AG-0001",
            "revision": 1,
            "level": 2,
            "target_kind": "audience_guidance",
            "status": "approved",
            "guidance_text": "Lead with business implication before news recap.",
        })
        + "\n",
        encoding="utf-8",
    )

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert payload["details"]["condition"] == "baseline"
    assert payload["details"]["existing_improvement_files"] == ["improvement/ledger.jsonl"]
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (ws / "experiment" / "080" / "condition.json").exists()


def test_experiments_080_scaffold_baseline_rejects_existing_improvement_snapshot(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "baseline-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    snapshot = ws / "output" / "intermediate" / "improvement_memory_snapshot.md"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("# Frozen Improvement Memory\n\n- Prior memory treatment.\n", encoding="utf-8")

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert payload["details"]["existing_improvement_files"] == [
        "output/intermediate/improvement_memory_snapshot.md"
    ]
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (ws / "experiment" / "080" / "condition.json").exists()


def test_experiments_080_scaffold_prompt_only_rejects_existing_improvement_memory(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "prompt-only-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["conditions"] = ["baseline", "memory", "prompt_only"]
    case_manifest["allowed_claims"]["memory_mechanism_adds_over_prompt"] = True
    _write_json(case_dir / "case_manifest.json", case_manifest)
    improvement_dir = ws / "improvement"
    improvement_dir.mkdir()
    (improvement_dir / "memory.md").write_text("# Improvement Memory\n\n- Use business framing.\n", encoding="utf-8")

    rc = main(_scaffold_args(case_dir, ws, condition="prompt_only"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert payload["details"]["condition"] == "prompt_only"
    assert payload["details"]["existing_improvement_files"] == ["improvement/memory.md"]
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (ws / "experiment" / "080" / "condition.json").exists()


def test_experiments_080_scaffold_prompt_only_rejects_improvement_runtime_residue(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "prompt-only-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["conditions"] = ["baseline", "memory", "prompt_only"]
    case_manifest["allowed_claims"]["memory_mechanism_adds_over_prompt"] = True
    _write_json(case_dir / "case_manifest.json", case_manifest)
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    _write_json(
        intermediate / "runtime_manifest.json",
        {
            "run_id": "mabw-20260618T000000Z-old0001",
            "improvement": {
                "ledger_sha256": "a" * 64,
                "memory_sha256": "b" * 64,
                "snapshot_path": "output/intermediate/improvement_memory_snapshot.md",
                "snapshot_sha256": "c" * 64,
                "materialized_entry_ids": ["AG-0001"],
            },
        },
    )
    _write_json(
        intermediate / "agent_handoff.json",
        {
            "runtime": "codex",
            "improvement_memory_files": {
                "improvement_memory_snapshot": "output/intermediate/improvement_memory_snapshot.md",
            },
        },
    )

    rc = main(_scaffold_args(case_dir, ws, condition="prompt_only"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert payload["details"]["condition"] == "prompt_only"
    assert payload["details"]["existing_improvement_files"] == [
        "output/intermediate/runtime_manifest.json:improvement",
        "output/intermediate/agent_handoff.json:improvement_memory_files",
    ]
    assert not (ws / "experiment" / "080" / "condition.json").exists()


def test_experiments_080_scaffold_rejects_condition_not_declared(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "prompt-only-workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)

    rc = main(_scaffold_args(case_dir, ws, condition="prompt_only"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_CONDITION_INVALID"
    assert not ws.exists()


def test_experiments_080_scaffold_rejects_archive_fact_layer_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    frozen_fact_layer = json.loads((case_dir / "frozen_fact_layer.json").read_text(encoding="utf-8"))
    frozen_fact_layer["artifacts"][0]["sha256"] = "0" * 64
    _write_json(case_dir / "frozen_fact_layer.json", frozen_fact_layer)

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_FACT_LAYER_MISMATCH"
    assert payload["details"]["mismatches"][0]["artifact_id"] == "durable_source_evidence_or_source_pack"
    assert not ws.exists()


def test_experiments_080_scaffold_requires_initialized_workspace(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)

    rc = main(_scaffold_args(case_dir, ws, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_WORKSPACE_INVALID"
    assert sorted(payload["details"]["missing_files"]) == [
        "audience_profile.md",
        "config.yaml",
        "sources.yaml",
        "user.md",
    ]
    assert not ws.exists()


def test_experiments_080_scaffold_rejects_existing_runtime_state(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    assert main(_scaffold_args(case_dir, ws, condition="baseline")) == 0
    capsys.readouterr()
    shutil.rmtree(ws / "experiment")

    rc = main(_scaffold_args(case_dir, ws, condition="memory"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_SCAFFOLD_IMPORT_FAILED"
    assert payload["details"]["runtime_error_code"] == "E_FACT_LAYER_IMPORT_INVALID"


def test_experiments_080_scaffold_uses_case_archive_path(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_scaffold_workspace(ws)
    run_dir = CLEAN_FIXTURE_MANIFEST.parent
    target_run_dir = case_dir / "output" / "runs" / run_dir.name
    target_run_dir.parent.mkdir(parents=True)
    shutil.copytree(run_dir, target_run_dir)

    rc = main(_scaffold_args(case_dir, ws, condition="memory", archive=None))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["fact_layer_import"]["source_run_id"] == run_dir.name
    metadata = json.loads((ws / "experiment" / "080" / "condition.json").read_text(encoding="utf-8"))
    assert metadata["treatment_visibility"]["allowed_visible_treatment_materials"] == [
        "output/intermediate/improvement_memory_snapshot.md"
    ]
    assert metadata["treatment"]["memory_ready"] == "requires_runtime_snapshot"
    assert "approved Improvement Memory" in metadata["treatment"]["memory_ready_check"]
    metadata_text = json.dumps(metadata, ensure_ascii=False)
    assert "Lead with business implication before news recap." not in metadata_text
    assert "Business implication appears before news recap." not in metadata_text
    instructions = (ws / "experiment" / "080" / "operator_instructions.md").read_text(encoding="utf-8")
    assert "Memory condition readiness check" in instructions


def test_experiments_080_register_run_writes_valid_record(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    output = tmp_path / "runs" / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run_id"] == run_id
    assert payload["output"] == str(output)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert validate_run_record(record) == []
    assert record["workspace_path"] == "<redacted-workspace>"
    assert (output.parent / record["run_archive_path"]).resolve() == archive_manifest.resolve()
    assert record["repo_commit"] == "abc123"
    assert record["repo_commit_source"] == "case_manifest"
    assert record["imported_fact_layer"]["matches_case_frozen_fact_layer"] is True
    assert record["timing"]["schema_version"] == "mabw.control_timing.v1"


def test_experiments_080_register_run_records_memory_snapshot_treatment_isolation(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    _write_treatment_condition_metadata(ws, condition="memory")
    _write_improvement_memory_snapshot(ws)
    output = tmp_path / "runs" / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="memory"))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["treatment_isolation"]["status"] == "pass"
    assert record["treatment_isolation"]["allowed_visible_treatment_materials"] == [
        "output/intermediate/improvement_memory_snapshot.md"
    ]


def test_experiments_080_register_run_rejects_baseline_condition_metadata_guidance_leak(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="baseline", leak_guidance=True)
    output = tmp_path / "runs" / "baseline.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    isolation = payload["details"]["treatment_isolation"]
    assert isolation["status"] == "fail"
    assert isolation["details"][-1]["reason"] == "treatment_guidance_leakage"
    assert not output.exists()


def test_experiments_080_register_run_rejects_memory_prompt_guidance_block(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="memory", prompt_block=True)
    _write_improvement_memory_snapshot(ws)
    output = tmp_path / "runs" / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="memory"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert {"reason": "memory_prompt_guidance_block_present"} in payload["details"]["treatment_isolation"]["details"]
    assert not output.exists()


def test_experiments_080_register_run_rejects_memory_snapshot_extra_guidance_ids(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="memory")
    _write_improvement_memory_snapshot(ws, selected_entry_ids=["AG-0001", "AG-9999"])
    output = tmp_path / "runs" / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="memory"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert {
        "status": "fail",
        "reason": "memory_snapshot_guidance_entry_ids_mismatch",
        "path": "output/intermediate/improvement_memory_snapshot.md",
        "missing_entry_ids": [],
        "unexpected_entry_ids": ["AG-9999"],
    } in payload["details"]["treatment_isolation"]["details"]
    assert not output.exists()


def test_experiments_080_register_run_accepts_memory_live_store_guidance(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="memory")
    _write_improvement_memory_snapshot(ws)
    improvement_dir = ws / "improvement"
    improvement_dir.mkdir()
    (improvement_dir / "memory.md").write_text(
        "# Improvement Memory\n\n- Guidance: Lead with business implication before news recap.\n",
        encoding="utf-8",
    )
    output = tmp_path / "runs" / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="memory"))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["treatment_isolation"]["status"] == "pass"


def test_experiments_080_register_run_rejects_baseline_live_memory_store(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="baseline")
    improvement_dir = ws / "improvement"
    improvement_dir.mkdir()
    (improvement_dir / "memory.md").write_text(
        "# Improvement Memory\n\n- Guidance: Lead with business implication before news recap.\n",
        encoding="utf-8",
    )
    output = tmp_path / "runs" / "baseline.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="baseline"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    detail = payload["details"]["treatment_isolation"]["details"][-1]
    assert detail["reason"] == "treatment_guidance_leakage"
    assert detail["leaks"] == [
        {"path": "improvement/memory.md", "entry_id": "AG-0001", "field": "guidance_text"}
    ]
    assert not output.exists()


def test_experiments_080_register_run_rejects_prompt_only_improvement_memory_snapshot(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["conditions"] = ["baseline", "memory", "prompt_only"]
    case_manifest["allowed_claims"]["memory_mechanism_adds_over_prompt"] = True
    _write_json(case_dir / "case_manifest.json", case_manifest)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="prompt_only", prompt_block=True)
    _write_improvement_memory_snapshot(ws)
    output = tmp_path / "runs" / "prompt.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="prompt_only"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert {
        "reason": "prompt_only_improvement_memory_snapshot_present",
        "path": "output/intermediate/improvement_memory_snapshot.md",
    } in payload["details"]["treatment_isolation"]["details"]
    assert not output.exists()


def test_experiments_080_register_run_rejects_prompt_only_extra_guidance_ids(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["conditions"] = ["baseline", "memory", "prompt_only"]
    case_manifest["allowed_claims"]["memory_mechanism_adds_over_prompt"] = True
    _write_json(case_dir / "case_manifest.json", case_manifest)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(
        ws,
        condition="prompt_only",
        prompt_block=True,
        extra_prompt_guidance=True,
    )
    output = tmp_path / "runs" / "prompt.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="prompt_only"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TREATMENT_CONTAMINATION"
    assert {
        "status": "fail",
        "reason": "prompt_guidance_block_unexpected_guidance",
        "unexpected_entry_ids": ["AG-9999"],
    } in payload["details"]["treatment_isolation"]["details"]
    assert not output.exists()


def test_experiments_080_register_run_accepts_prompt_only_guidance_block_without_memory(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["conditions"] = ["baseline", "memory", "prompt_only"]
    case_manifest["allowed_claims"]["memory_mechanism_adds_over_prompt"] = True
    _write_json(case_dir / "case_manifest.json", case_manifest)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    _write_treatment_condition_metadata(ws, condition="prompt_only", prompt_block=True)
    output = tmp_path / "runs" / "prompt.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="prompt_only"))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["treatment_isolation"]["status"] == "pass"
    assert record["treatment_isolation"]["allowed_visible_treatment_materials"] == [
        "handoff.prompt_guidance_block"
    ]


def test_experiments_080_register_run_writes_archive_path_resolvable_from_case_output(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspaces" / "memory"
    ws.mkdir(parents=True)
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    output = case_dir / "runs" / "memory" / "run_record.json"
    scorecard_path = case_dir / "runs" / "memory" / "scorecard.json"

    assert main(_register_args(case_dir, ws, output)) == 0
    capsys.readouterr()
    record = json.loads(output.read_text(encoding="utf-8"))
    assert not Path(record["run_archive_path"]).is_absolute()
    assert (output.parent / record["run_archive_path"]).resolve() == archive_manifest.resolve()

    assert main(_score_args(case_dir, output, scorecard_path)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["archive"]["present"] is True


def test_experiments_080_register_run_is_idempotent_when_output_matches(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    output = tmp_path / "memory.run_record.json"

    assert main(_register_args(case_dir, ws, output)) == 0
    capsys.readouterr()
    before = output.read_bytes()
    assert main(_register_args(case_dir, ws, output)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["written"] is False
    assert output.read_bytes() == before


def test_experiments_080_register_run_refuses_different_existing_output(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"
    output.write_text("{}\n", encoding="utf-8")

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_OUTPUT_EXISTS"
    assert output.read_text(encoding="utf-8") == "{}\n"


def test_experiments_080_register_run_rejects_condition_not_in_case(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "prompt.run_record.json"

    rc = main(_register_args(case_dir, ws, output, condition="prompt_only"))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_CONDITION_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_non_terminal_workspace(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name, current_stage="analyst")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_NOT_TERMINAL"
    assert not output.exists()


def test_experiments_080_register_run_rejects_missing_archive(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id="mabw-20260614T000000Z-public0001")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_INPUT_MISSING"
    assert not output.exists()


def test_experiments_080_register_run_records_contaminated_run(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    contaminated = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test_contamination", "message": "test"}],
    }
    _patch_archive_manifest(archive_manifest, run_integrity=contaminated)
    _write_terminal_runtime(ws, run_id=run_id, run_integrity=contaminated)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["run_integrity"]["status"] == "contaminated"
    assert record["run_integrity"]["reference_eligible"] is False
    assert validate_run_record(record) == []


def test_experiments_080_register_run_rejects_malformed_run_integrity(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name, run_integrity="bad")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_INTEGRITY_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_run_id_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_id"] = "mabw-20260614T000000Z-other"
    _write_json(workflow_path, workflow)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_ID_MISMATCH"
    assert not output.exists()


def test_experiments_080_register_run_records_fact_layer_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST, source_pack_sha="c" * 64)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    imported = record["imported_fact_layer"]
    assert imported["matches_case_frozen_fact_layer"] is False
    assert imported["mismatches"][0]["artifact_id"] == "durable_source_evidence_or_source_pack"


def test_experiments_080_register_run_rejects_source_plan_archive(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, PLAN_ONLY_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_missing_fact_layer_file(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    claim_ledger = archive_manifest.parent / "fact_layer" / "output" / "intermediate" / "claim_ledger.json"
    claim_ledger.unlink()
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_tampered_fact_layer_file(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    claim_ledger = archive_manifest.parent / "fact_layer" / "output" / "intermediate" / "claim_ledger.json"
    claim_ledger.write_text('{"tampered": true}\n', encoding="utf-8")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_source_pack_hash_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"][0]["pack_sha256"] = "d" * 64
    _write_json(archive_manifest, manifest)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_rejects_extra_non_fact_layer_artifact(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["fact_layer"]["artifacts"].append({
        "artifact_id": "delivery_brief",
        "fact_role": "not_fact_layer",
        "archive_path": "delivery/brief.md",
        "original_path": "output/delivery/brief.md",
        "sha256": "e" * 64,
        "size_bytes": 1,
    })
    _write_json(archive_manifest, manifest)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FACT_LAYER_INVALID"
    assert not output.exists()


def test_experiments_080_register_run_does_not_use_unrelated_cwd_git(tmp_path, capsys, monkeypatch):
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    subprocess.run(["git", "init"], cwd=unrelated, check=True, capture_output=True, text=True)
    (unrelated / "README.md").write_text("unrelated\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=unrelated, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=unrelated,
        check=True,
        capture_output=True,
        text=True,
    )
    unrelated_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=unrelated,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.chdir(unrelated)

    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["repo_commit"] == "abc123"
    assert record["repo_commit"] != unrelated_head
    assert record["repo_commit_source"] == "case_manifest"


def test_experiments_080_score_run_writes_deterministic_scorecard_draft(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "scorecards" / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["validity_class"] == "invalid_incomplete"
    assert payload["assessment_status"] == "needs_assessment"
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert validate_scorecard(scorecard) == []
    assert scorecard["assessment_status"] == "needs_assessment"
    assert scorecard["guidance_scores"] == []
    assert scorecard["guidance_assessment"]["guidance_entry_ids"] == ["AG-0001"]
    assert scorecard["control_integrity"]["archive_present"] is True
    assert scorecard["control_integrity"]["quality_gates_passed"] is True
    assert scorecard["control_integrity"]["finalize_report_pass"] is True
    assert scorecard["control_integrity"]["artifact_registry_valid"] is True
    assert scorecard["reader_clean"]["pass"] is True
    assert scorecard["frozen_fact_layer"]["matches_case"] is True
    assert "Python does not score guidance manifestation" in scorecard["notes"][1]


def test_experiments_080_auditable_brief_target_scores_without_finalize(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    run_id = "mabw-20260614T000000Z-auditable0001"
    _write_auditable_target_workspace(ws, run_id=run_id, source_archive_manifest=CLEAN_FIXTURE_MANIFEST)
    _write_treatment_condition_metadata(ws, condition="memory")
    _write_improvement_memory_snapshot(ws)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "scorecards" / "memory.scorecard.json"

    assert main(_register_args(case_dir, ws, run_record)) == 0
    record = json.loads(run_record.read_text(encoding="utf-8"))
    assert record["assessment_target"] == "auditable_brief"
    assert record["assessment_target_manifest"]["assessment_target"] == "auditable_brief"
    assert record["assessment_target_manifest"]["timing_semantics"] == "diagnostic_only"
    assert record["assessment_target_manifest"]["reader_clean_required"] is False
    assert record["assessment_target_manifest"]["audit_binding_status"] == "required_python_owned"
    assert record["run_archive_path"] == ""
    assert record["audit_binding"]["status"] == "valid"
    assert record["audit_binding"]["source"] == "workflow_state.stage_statuses.auditor.metadata.audit_binding"
    assert record["audit_binding"]["relevant_repair_transaction_ids"] == []
    assert record["treatment_isolation"]["status"] == "pass"
    assert record["target_artifacts"]["audited_brief"]["path"] == "output/intermediate/audited_brief.md"
    assert [stage["stage_id"] for stage in record["timing"]["stages"]] == ["analyst", "editor", "auditor"]
    assert record["timing"]["status"] == "available"
    capsys.readouterr()

    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0

    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert validate_scorecard(scorecard) == []
    assert scorecard["assessment_target"] == "auditable_brief"
    assert scorecard["assessment_target_manifest"]["assessment_target"] == "auditable_brief"
    assert scorecard["assessment_target_manifest"]["timing_semantics"] == "diagnostic_only"
    assert scorecard["assessment_target_manifest"]["reader_clean_required"] is False
    assert scorecard["assessment_target_manifest"]["audit_binding_status"] == "required_python_owned"
    assert scorecard["audit_binding"]["status"] == "valid"
    assert scorecard["target_readiness"]["assessment_target"] == "auditable_brief"
    assert scorecard["target_readiness"]["status"] == "complete"
    assert scorecard["target_readiness"]["ready_for_assessment_import"] is True
    assert scorecard["target_readiness"]["missing_control_keys"] == []
    assert scorecard["claim_scope"] == [
        "guidance_manifestation_in_audited_brief",
        "evidence_use_under_frozen_fact_layer",
        "auditor_gate_passage",
    ]
    assert scorecard["reader_clean"]["pass"] is None
    assert scorecard["reader_clean"]["status"] == "not_required_for_target"
    assert scorecard["finalize"]["complete"] is False
    assert scorecard["control_integrity"]["auditor_complete"] is True
    assert scorecard["control_integrity"]["audit_binding_valid"] is True
    assert scorecard["control_integrity"]["auditor_gates_no_blocking"] is True
    assert scorecard["control_integrity"]["treatment_isolation_passed"] is True
    assert scorecard["control_integrity"]["timing_available"] is True
    assert scorecard["validity_class"] == "invalid_incomplete"

    assessment_path = tmp_path / "assessment.json"
    assessed_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human", run_id=run_id))
    capsys.readouterr()
    assert main(_assessment_args(scorecard_path, assessment_path, assessed_path)) == 0
    assessed = json.loads(assessed_path.read_text(encoding="utf-8"))
    assert assessed["validity_class"] == "A_controlled"
    assert assessed["assessment_target"] == "auditable_brief"

    no_timing_scorecard_path = tmp_path / "scorecards" / "memory.no_timing.scorecard.json"
    no_timing_assessed_path = tmp_path / "assessed.no_timing.scorecard.json"
    scorecard["control_integrity"]["timing_available"] = False
    scorecard["timing_summary"] = {
        "schema_version": "mabw.control_timing.v1",
        "source": "run_record.timing",
        "status": "incomplete",
        "raw_status": "incomplete",
        "timing_comparability": "downstream_only",
    }
    _write_json(no_timing_scorecard_path, scorecard)
    assert validate_scorecard(scorecard) == []

    capsys.readouterr()
    assert main(_assessment_args(no_timing_scorecard_path, assessment_path, no_timing_assessed_path)) == 0
    no_timing_assessed = json.loads(no_timing_assessed_path.read_text(encoding="utf-8"))
    assert no_timing_assessed["control_integrity"]["timing_available"] is False
    assert no_timing_assessed["validity_class"] == "A_controlled"


def test_experiments_080_auditable_brief_missing_treatment_isolation_stays_incomplete(
    tmp_path, capsys
):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    run_id = "mabw-20260614T000000Z-auditable0001"
    _write_auditable_target_workspace(ws, run_id=run_id, source_archive_manifest=CLEAN_FIXTURE_MANIFEST)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "scorecards" / "memory.scorecard.json"

    assert main(_register_args(case_dir, ws, run_record)) == 0
    record = json.loads(run_record.read_text(encoding="utf-8"))
    assert record["treatment_isolation"]["status"] == "not_checked"
    capsys.readouterr()
    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["control_integrity"]["treatment_isolation_passed"] is False
    assert scorecard["target_readiness"]["status"] == "incomplete"
    assert "missing required control: treatment_isolation_passed" in scorecard["target_readiness"]["reasons"]

    assessment_path = tmp_path / "assessment.json"
    assessed_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human", run_id=run_id))
    capsys.readouterr()
    assert main(_assessment_args(scorecard_path, assessment_path, assessed_path)) == 0
    assessed = json.loads(assessed_path.read_text(encoding="utf-8"))
    assert assessed["validity_class"] == "invalid_incomplete"

    tampered_scorecard_path = tmp_path / "scorecards" / "memory.tampered.scorecard.json"
    tampered_assessed_path = tmp_path / "assessed.tampered.scorecard.json"
    scorecard["control_integrity"]["treatment_isolation_passed"] = True
    scorecard["treatment_isolation"] = {
        "schema_version": "mabw.experiment_080.treatment_visibility.v1",
        "status": "fail",
        "condition": "memory",
    }
    _write_json(tampered_scorecard_path, scorecard)
    assert validate_scorecard(scorecard) == []

    capsys.readouterr()
    assert main(_assessment_args(tampered_scorecard_path, assessment_path, tampered_assessed_path)) == 0
    tampered_assessed = json.loads(tampered_assessed_path.read_text(encoding="utf-8"))
    assert tampered_assessed["validity_class"] == "invalid_incomplete"


def test_experiments_080_auditable_brief_target_blocks_finalize(tmp_path, capsys):
    ws = tmp_path / "workspace"
    _write_scaffold_workspace(ws)
    _write_auditable_condition_metadata(ws)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )

    rc = main(["finalize", "--config", str(ws / "config.yaml")])

    captured = capsys.readouterr()
    assert rc == 1
    assert "TARGET COMPLETE: auditable_brief" in captured.err
    assert "outside this target" in captured.err
    assert not (ws / "output" / "delivery" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "finalize_report.json").exists()


def test_experiments_080_auditable_brief_target_block_uses_event_log_repair_binding(
    tmp_path,
):
    ws = tmp_path / "workspace"
    _write_scaffold_workspace(ws)
    _write_auditable_condition_metadata(ws)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    with event_log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": "multi-agent-brief-event-log/v1",
                    "event_id": "repair-event-1",
                    "run_id": "mabw-20260614T000000Z-auditable0001",
                    "created_at": "2026-06-14T00:04:00+00:00",
                    "event_type": "repair_completed",
                    "actor": "cli",
                    "stage_id": "editor",
                    "artifact_id": None,
                    "decision": "repair_complete",
                    "reason": "editor repair completed",
                    "metadata": {
                        "transaction_id": "repair-editor-1",
                        "allowed_artifacts": ["output/intermediate/audited_brief.md"],
                    },
                },
                sort_keys=True,
            )
            + "\n"
        )
    workflow = json.loads((ws / "output" / "intermediate" / "workflow_state.json").read_text(encoding="utf-8"))

    with pytest.raises(RuntimeStateError) as excinfo:
        raise_if_auditable_target_complete_blocks_downstream(
            workspace=ws,
            workflow=workflow,
            command="finalize",
        )

    assert excinfo.value.error_code == E_ASSESSMENT_TARGET_COMPLETE
    assert excinfo.value.details["target_complete"] is False
    assert "audit binding relevant_repair_transaction_ids does not match event_log" in excinfo.value.details["reasons"]


def test_experiments_080_auditable_brief_finalize_blocks_incomplete_target_before_writing(
    tmp_path,
    capsys,
):
    ws = tmp_path / "workspace"
    _write_scaffold_workspace(ws)
    _write_auditable_condition_metadata(ws)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    with event_log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": "multi-agent-brief-event-log/v1",
                    "event_id": "repair-event-1",
                    "run_id": "mabw-20260614T000000Z-auditable0001",
                    "created_at": "2026-06-14T00:04:00+00:00",
                    "event_type": "repair_completed",
                    "actor": "cli",
                    "stage_id": "editor",
                    "artifact_id": None,
                    "decision": "repair_complete",
                    "reason": "editor repair completed",
                    "metadata": {
                        "transaction_id": "repair-editor-1",
                        "allowed_artifacts": ["output/intermediate/audited_brief.md"],
                    },
                },
                sort_keys=True,
            )
            + "\n"
        )

    rc = main(["finalize", "--config", str(ws / "config.yaml")])

    captured = capsys.readouterr()
    assert rc == 1
    assert "TARGET INCOMPLETE: auditable_brief" in captured.err
    assert "TARGET COMPLETE: auditable_brief" not in captured.err
    assert not (ws / "output" / "delivery" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "finalize_report.json").exists()


def test_experiments_080_auditable_brief_target_blocks_finalize_complete(tmp_path, capsys):
    ws = tmp_path / "workspace"
    _write_scaffold_workspace(ws)
    _write_auditable_condition_metadata(ws)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )

    rc = main([
        "state",
        "finalize-complete",
        "--workspace", str(ws),
        "--reason", "auditable target should stop before delivery",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_ASSESSMENT_TARGET_COMPLETE"
    assert payload["details"]["target_complete"] is True
    assert "multi-agent-brief finalize" in payload["details"]["forbidden_downstream_actions"]
    events = (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert not any("finalize_completed" in line for line in events)


def test_experiments_080_auditable_brief_register_rejects_active_repair(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
        active_repair=True,
    )
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ACTIVE_REPAIR_OPEN"
    assert not output.exists()


def test_experiments_080_auditable_brief_register_requires_audit_binding(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["stage_statuses"]["auditor"]["metadata"].pop("audit_binding")
    _write_json(workflow_path, workflow)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_AUDIT_BINDING_INVALID"
    assert not output.exists()


def test_experiments_080_auditable_brief_register_rejects_mismatched_audit_binding(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["stage_statuses"]["auditor"]["metadata"]["audit_binding"]["audited_brief_sha256"] = "0" * 64
    _write_json(workflow_path, workflow)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_AUDIT_BINDING_INVALID"
    assert "audited_brief_sha256" in payload["details"]["mismatches"]
    assert not output.exists()


def test_experiments_080_auditable_brief_register_requires_auditor_completion_event(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    binding = workflow["stage_statuses"]["auditor"]["metadata"]["audit_binding"]
    binding["auditor_stage_transaction_id"] = "fake-nonexistent-tx"
    binding["stage_completion_event"]["transaction_id"] = "fake-nonexistent-tx"
    _write_json(workflow_path, workflow)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_AUDIT_BINDING_INVALID"
    assert "auditor_stage_transaction_id" in payload["details"]["mismatches"]
    assert not output.exists()


def test_experiments_080_auditable_brief_register_rejects_stage_completion_event_tx_mismatch(
    tmp_path,
    capsys,
):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    binding = workflow["stage_statuses"]["auditor"]["metadata"]["audit_binding"]
    binding["stage_completion_event"]["transaction_id"] = "wrong-tx"
    _write_json(workflow_path, workflow)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_AUDIT_BINDING_INVALID"
    assert "stage_completion_event.transaction_id" in payload["details"]["mismatches"]
    assert not output.exists()


def test_experiments_080_auditable_brief_register_rejects_stale_target_artifact(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    registry_path = ws / "output" / "intermediate" / "artifact_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["artifacts"]["audit_report"]["status"] = "stale"
    registry["artifacts"]["audit_report"]["validation_result"] = "stale_after_repair"
    _write_json(registry_path, registry)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_TARGET_ARTIFACT_INVALID"
    assert payload["details"]["artifact_id"] == "audit_report"
    assert payload["details"]["status"] == "stale"
    assert not output.exists()


def test_experiments_080_auditable_brief_register_requires_repair_ids_in_binding(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    repair_event = {
        "schema_version": "multi-agent-brief-event-log/v1",
        "event_id": "repair-event-1",
        "run_id": "mabw-20260614T000000Z-auditable0001",
        "created_at": "2026-06-14T00:04:00+00:00",
        "event_type": "repair_completed",
        "actor": "cli",
        "stage_id": "editor",
        "artifact_id": None,
        "decision": "repair_complete",
        "reason": "editor repair completed",
        "metadata": {
            "transaction_id": "repair-editor-1",
            "allowed_artifacts": ["output/intermediate/audited_brief.md"],
        },
    }
    with event_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(repair_event, sort_keys=True) + "\n")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_AUDIT_BINDING_INVALID"
    assert payload["details"]["mismatches"]["relevant_repair_transaction_ids"]["expected"] == ["repair-editor-1"]
    assert not output.exists()


def test_experiments_080_auditable_brief_register_requires_fact_layer_import(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    manifest_path = ws / "output" / "intermediate" / "runtime_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["fact_layer_import"]
    _write_json(manifest_path, manifest)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID"
    assert not output.exists()


def test_experiments_080_auditable_brief_register_rejects_fact_layer_import_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    manifest_path = ws / "output" / "intermediate" / "runtime_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fact_layer_import"]["fact_layer_sha256"] = "0" * 64
    _write_json(manifest_path, manifest)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID"
    assert not output.exists()


def test_experiments_080_auditable_brief_register_rejects_tampered_imported_fact_layer(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    (ws / "output" / "intermediate" / "claim_ledger.json").write_text('{"tampered": true}\n', encoding="utf-8")
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_FACT_LAYER_IMPORT_INVALID"
    assert not output.exists()


def test_experiments_080_auditable_brief_register_rejects_blocking_gate_binding(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )
    _mutate_auditable_gate_report(ws, lambda gate: gate["gate_results"][0].__setitem__("blocking", True))
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_AUDITOR_GATE_BLOCKED"
    assert "blocking gate_results" in " ".join(payload["details"]["reasons"])
    assert not output.exists()


def test_experiments_080_auditable_brief_register_rejects_blocking_gate_finding(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_case_source(case_dir, CLEAN_FIXTURE_MANIFEST)
    case_manifest = json.loads((case_dir / "case_manifest.json").read_text(encoding="utf-8"))
    case_manifest["assessment_target"] = "auditable_brief"
    _write_json(case_dir / "case_manifest.json", case_manifest)
    _write_auditable_target_workspace(
        ws,
        run_id="mabw-20260614T000000Z-auditable0001",
        source_archive_manifest=CLEAN_FIXTURE_MANIFEST,
    )

    def add_blocking_finding(gate: dict) -> None:
        gate["findings"] = [
            {
                "finding_id": "QG_BLOCKING_001",
                "finding_type": "target_relevance_gap",
                "severity": "high",
                "blocking_level": "blocking",
                "blocking": True,
                "stage_id": "auditor",
                "gate_stage_id": "auditor",
                "artifact_id": "audited_brief",
                "gate_artifact_id": "auditor_quality_gate_report",
                "repair_stage_id": "editor",
                "repair_artifact_id": "audited_brief",
            }
        ]
        gate["gate_results"][0]["finding_ids"] = ["QG_BLOCKING_001"]

    _mutate_auditable_gate_report(ws, add_blocking_finding)
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_AUDITOR_GATE_BLOCKED"
    assert "blocking findings" in " ".join(payload["details"]["reasons"])
    assert not output.exists()


def test_experiments_080_delivery_target_still_requires_terminal_workflow(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = CLEAN_FIXTURE_MANIFEST.parent.name
    _write_terminal_runtime(
        ws,
        run_id=run_id,
        current_stage="finalize",
        finalize_status="ready",
        stage_statuses={"auditor": {"status": "complete"}, "finalize": {"status": "ready"}},
    )
    output = tmp_path / "memory.run_record.json"

    rc = main(_register_args(case_dir, ws, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_NOT_TERMINAL"
    assert not output.exists()


def test_experiments_080_score_run_accepts_fast_rerun_downstream_only_timing(tmp_path, capsys):
    scorecard = _scorecard_from_fast_rerun_timing(
        tmp_path,
        capsys,
        {
            "schema_version": "mabw.control_timing.v1",
            "kind": "control_trace_timing_buckets",
            "source": "event_log",
            "status": "incomplete",
            "total_elapsed_seconds": 123.0,
            "stages": [
                {
                    "stage_id": "scout",
                    "status": "incomplete",
                    "reason": "completion_event_missing",
                },
                {"stage_id": "analyst", "status": "complete"},
                {"stage_id": "editor", "status": "complete"},
                {"stage_id": "auditor", "status": "complete"},
            ],
            "finalize": {"stage_id": "finalize", "status": "complete"},
            "warnings": ["scout: completion event missing"],
        },
    )

    assert scorecard["timing_summary"]["status"] == "downstream_only"
    assert scorecard["timing_summary"]["raw_status"] == "incomplete"
    assert scorecard["control_integrity"]["timing_available"] is True


def test_experiments_080_score_run_rejects_downstream_only_timing_with_missing_analyst(
    tmp_path,
    capsys,
):
    scorecard = _scorecard_from_fast_rerun_timing(
        tmp_path,
        capsys,
        {
            "schema_version": "mabw.control_timing.v1",
            "kind": "control_trace_timing_buckets",
            "source": "event_log",
            "status": "incomplete",
            "total_elapsed_seconds": 123.0,
            "stages": [
                {
                    "stage_id": "scout",
                    "status": "incomplete",
                    "reason": "completion_event_missing",
                },
                {
                    "stage_id": "analyst",
                    "status": "incomplete",
                    "reason": "completion_event_missing",
                },
                {"stage_id": "editor", "status": "complete"},
                {"stage_id": "auditor", "status": "complete"},
            ],
            "finalize": {"stage_id": "finalize", "status": "complete"},
            "warnings": [
                "scout: completion event missing",
                "analyst: completion event missing",
            ],
        },
    )

    assert scorecard["timing_summary"]["status"] == "incomplete"
    assert scorecard["control_integrity"]["timing_available"] is False


def test_experiments_080_score_run_rejects_downstream_only_timing_with_missing_finalize(
    tmp_path,
    capsys,
):
    scorecard = _scorecard_from_fast_rerun_timing(
        tmp_path,
        capsys,
        {
            "schema_version": "mabw.control_timing.v1",
            "kind": "control_trace_timing_buckets",
            "source": "event_log",
            "status": "incomplete",
            "total_elapsed_seconds": 123.0,
            "stages": [
                {
                    "stage_id": "scout",
                    "status": "incomplete",
                    "reason": "completion_event_missing",
                },
                {"stage_id": "analyst", "status": "complete"},
                {"stage_id": "editor", "status": "complete"},
                {"stage_id": "auditor", "status": "complete"},
            ],
            "finalize": {
                "stage_id": "finalize",
                "status": "incomplete",
                "reason": "completion_event_missing",
            },
            "warnings": [
                "scout: completion event missing",
                "finalize: completion event missing",
            ],
        },
    )

    assert scorecard["timing_summary"]["status"] == "incomplete"
    assert scorecard["control_integrity"]["timing_available"] is False


def test_experiments_080_score_run_rejects_downstream_only_timing_with_finalize_status_missing(
    tmp_path,
    capsys,
):
    scorecard = _scorecard_from_fast_rerun_timing(
        tmp_path,
        capsys,
        {
            "schema_version": "mabw.control_timing.v1",
            "kind": "control_trace_timing_buckets",
            "source": "event_log",
            "status": "incomplete",
            "total_elapsed_seconds": 123.0,
            "stages": [
                {
                    "stage_id": "scout",
                    "status": "incomplete",
                    "reason": "completion_event_missing",
                },
                {"stage_id": "analyst", "status": "complete"},
                {"stage_id": "editor", "status": "complete"},
                {"stage_id": "auditor", "status": "complete"},
            ],
            "finalize": {"stage_id": "finalize"},
            "warnings": ["scout: completion event missing"],
        },
    )

    assert scorecard["timing_summary"]["status"] == "incomplete"
    assert scorecard["control_integrity"]["timing_available"] is False


def _scorecard_from_fast_rerun_timing(tmp_path, capsys, timing: dict) -> dict:
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    manifest["timing"] = timing
    _write_json(archive_manifest, manifest)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(
        ws,
        run_id=run_id,
        recipe="fast-rerun",
        fact_layer_import={"timing_comparability": "downstream_only"},
    )
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "scorecards" / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    record = json.loads(run_record.read_text(encoding="utf-8"))
    assert record["timing"]["run_recipe"] == "fast-rerun"
    assert record["timing"]["timing_comparability"] == "downstream_only"

    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0

    return json.loads(scorecard_path.read_text(encoding="utf-8"))


def test_experiments_080_score_run_does_not_promote_downstream_only_without_stage_gaps(
    tmp_path,
    capsys,
):
    scorecard = _scorecard_from_fast_rerun_timing(
        tmp_path,
        capsys,
        {
            "schema_version": "mabw.control_timing.v1",
            "kind": "control_trace_timing_buckets",
            "source": "event_log",
            "status": "incomplete",
            "total_elapsed_seconds": 123.0,
            "stages": [{"stage_id": "analyst", "status": "complete"}],
            "finalize": {"stage_id": "finalize", "status": "complete"},
            "warnings": ["scout: completion event missing"],
        },
    )

    assert scorecard["timing_summary"]["status"] == "incomplete"
    assert scorecard["control_integrity"]["timing_available"] is False


def test_experiments_080_score_run_is_idempotent_when_output_matches(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0
    capsys.readouterr()
    before = scorecard_path.read_bytes()

    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["written"] is False
    assert scorecard_path.read_bytes() == before


def test_experiments_080_score_run_refuses_different_existing_output(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    scorecard_path.write_text("{}\n", encoding="utf-8")
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_OUTPUT_EXISTS"
    assert scorecard_path.read_text(encoding="utf-8") == "{}\n"


def test_experiments_080_score_run_marks_fact_layer_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST, source_pack_sha="c" * 64)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["validity_class"] == "invalid_fact_layer_mismatch"
    assert scorecard["frozen_fact_layer"]["matches_case"] is False


def test_experiments_080_score_run_marks_contaminated_run(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    contaminated = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test_contamination", "message": "test"}],
    }
    _patch_archive_manifest(archive_manifest, run_integrity=contaminated)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name, run_integrity=contaminated)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["validity_class"] == "invalid_contaminated"
    assert scorecard["control_integrity"]["run_integrity_clean"] is False


def test_experiments_080_score_run_marks_missing_archive_incomplete(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    run_id = archive_manifest.parent.name
    _write_terminal_runtime(ws, run_id=run_id)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    shutil.rmtree(archive_manifest.parent)

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["validity_class"] == "invalid_incomplete"
    assert scorecard["control_integrity"]["archive_present"] is False
    assert scorecard["reader_clean"]["status"] == "unknown"


def test_experiments_080_score_run_rejects_archive_run_id_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _patch_archive_manifest(archive_manifest, run_id="mabw-20260614T000000Z-other0001")

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_ID_MISMATCH"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_archive_runtime_manifest_run_id_mismatch(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _patch_archive_manifest(archive_manifest, runtime_manifest_run_id="mabw-20260614T000000Z-other0001")

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_ID_MISMATCH"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_tampered_finalize_report(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _write_json(
        archive_manifest.parent / "intermediate" / "finalize_report.json",
        {"status": "pass", "reader_clean": {"status": "pass", "markdown_blocking_count": 0}},
    )

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_tampered_gate_report(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _write_json(
        archive_manifest.parent / "intermediate" / "gates" / "auditor_quality_gate_report.json",
        {"status": "pass", "gate_results": [{"gate_id": "tampered"}]},
    )

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_tampered_artifact_registry(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    _write_json(
        archive_manifest.parent / "control" / "artifact_registry.json",
        {"schema_version": "multi-agent-brief-artifact-registry/v1", "artifacts": {"tampered": {}}},
    )

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ARCHIVE_FILE_INVALID"
    assert not scorecard_path.exists()


def test_experiments_080_score_run_rejects_invalid_run_record(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    run_record = tmp_path / "bad.run_record.json"
    _write_json(run_record, {"schema_version": "mabw.experiment_080.run_record.v1"})
    scorecard_path = tmp_path / "bad.scorecard.json"

    rc = main(_score_args(case_dir, run_record, scorecard_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_RUN_RECORD_INVALID"
    assert not scorecard_path.exists()


def test_experiments_080_export_blind_pack_strips_conditions_and_hash_binds(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_auditable_three_condition_case(case_dir)
    scorecards = [
        _write_auditable_scorecard_for_condition(
            tmp_path,
            capsys,
            case_dir=case_dir,
            condition=condition,
            run_id=f"mabw-20260614T000000Z-{condition.replace('_', '')}0001",
        )
        for condition in ("baseline", "memory", "prompt_only")
    ]
    blind_dir = tmp_path / "blind-pack"

    rc = main(_export_blind_pack_args(case_dir, blind_dir, scorecards, seed="080-fixed"))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["blind_item_count"] == 3
    pack = json.loads((blind_dir / "blind_pack.json").read_text(encoding="utf-8"))
    reveal = json.loads((blind_dir / "reveal_mapping.json").read_text(encoding="utf-8"))
    assert pack["schema_version"] == "mabw.experiment_080.blind_pack.v1"
    assert pack["condition_blind"] is True
    assert pack["hash_bound"] is True
    assert pack["rubric"]["guidance_entries"][0]["entry_id"] == "AG-0001"
    assert {item["blind_item_id"] for item in pack["items"]} == {"BI-A", "BI-B", "BI-C"}
    assert {item["blind_item_id"] for item in reveal["items"]} == {"BI-A", "BI-B", "BI-C"}
    scorer_visible = json.dumps(pack, ensure_ascii=False)
    assert "baseline" not in scorer_visible
    assert "memory" not in scorer_visible
    assert "prompt_only" not in scorer_visible
    assert "mabw-20260614" not in scorer_visible
    assert "workspace" not in scorer_visible.lower()
    for item in pack["items"]:
        artifact = blind_dir / item["artifact_path"]
        assert artifact.is_file()
        assert _sha256_file(artifact) == item["artifact_sha256"]
        item_json = json.loads((blind_dir / "items" / item["blind_item_id"] / "item.json").read_text(encoding="utf-8"))
        assert item_json["artifact_sha256"] == item["artifact_sha256"]

    repeat_dir = tmp_path / "blind-pack-repeat"
    assert main(_export_blind_pack_args(case_dir, repeat_dir, scorecards, seed="080-fixed")) == 0
    assert (repeat_dir / "blind_pack.json").read_text(encoding="utf-8") == (
        blind_dir / "blind_pack.json"
    ).read_text(encoding="utf-8")


def test_experiments_080_import_blind_assessment_verifies_hash_and_reveals_identity(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_auditable_three_condition_case(case_dir)
    scorecard = _write_auditable_scorecard_for_condition(
        tmp_path,
        capsys,
        case_dir=case_dir,
        condition="memory",
        run_id="mabw-20260614T000000Z-memory0001",
    )
    blind_dir = tmp_path / "blind-pack"
    assert main(_export_blind_pack_args(case_dir, blind_dir, [scorecard], seed="080-fixed")) == 0
    capsys.readouterr()
    pack = json.loads((blind_dir / "blind_pack.json").read_text(encoding="utf-8"))
    item = pack["items"][0]
    assessment_path = tmp_path / "blind-assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(
        assessment_path,
        _blind_assessment_payload(
            blind_item_id=item["blind_item_id"],
            artifact_sha256=item["artifact_sha256"],
        ),
    )

    rc = main(_blind_assessment_args(
        blind_dir / "blind_pack.json",
        blind_dir / "reveal_mapping.json",
        assessment_path,
        output_path,
    ))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["validity_class"] == "A_controlled"
    assessed = json.loads(output_path.read_text(encoding="utf-8"))
    assert assessed["condition"] == "memory"
    assert assessed["run_id"] == "mabw-20260614T000000Z-memory0001"
    assert assessed["guidance_assessment"]["blind_item_id"] == item["blind_item_id"]
    assert assessed["guidance_assessment"]["blind_pack"]["hash_verified"] is True


def test_experiments_080_import_blind_assessment_rejects_modified_item(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_auditable_three_condition_case(case_dir)
    scorecard = _write_auditable_scorecard_for_condition(
        tmp_path,
        capsys,
        case_dir=case_dir,
        condition="memory",
        run_id="mabw-20260614T000000Z-memory0001",
    )
    blind_dir = tmp_path / "blind-pack"
    assert main(_export_blind_pack_args(case_dir, blind_dir, [scorecard], seed="080-fixed")) == 0
    capsys.readouterr()
    pack = json.loads((blind_dir / "blind_pack.json").read_text(encoding="utf-8"))
    item = pack["items"][0]
    (blind_dir / item["artifact_path"]).write_text("tampered\n", encoding="utf-8")
    assessment_path = tmp_path / "blind-assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(
        assessment_path,
        _blind_assessment_payload(
            blind_item_id=item["blind_item_id"],
            artifact_sha256=item["artifact_sha256"],
        ),
    )

    rc = main(_blind_assessment_args(
        blind_dir / "blind_pack.json",
        blind_dir / "reveal_mapping.json",
        assessment_path,
        output_path,
    ))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID"
    assert not output_path.exists()


def test_experiments_080_import_blind_assessment_rejects_rebound_scorecard(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_auditable_three_condition_case(case_dir)
    scorecards = [
        _write_auditable_scorecard_for_condition(
            tmp_path,
            capsys,
            case_dir=case_dir,
            condition=condition,
            run_id=f"mabw-20260614T000000Z-{condition}0001",
        )
        for condition in ("baseline", "memory")
    ]
    blind_dir = tmp_path / "blind-pack"
    assert main(_export_blind_pack_args(case_dir, blind_dir, scorecards, seed="080-fixed")) == 0
    capsys.readouterr()
    reveal_path = blind_dir / "reveal_mapping.json"
    reveal = json.loads(reveal_path.read_text(encoding="utf-8"))
    first, second = reveal["items"]
    original_artifact_sha = first["artifact_sha256"]
    first["condition"] = second["condition"]
    first["run_id"] = second["run_id"]
    first["scorecard"] = second["scorecard"]
    first["scorecard_sha256"] = second["scorecard_sha256"]
    first["guidance_entry_ids"] = second["guidance_entry_ids"]
    first["artifact_sha256"] = original_artifact_sha
    _write_json(reveal_path, reveal)
    assessment_path = tmp_path / "blind-assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(
        assessment_path,
        _blind_assessment_payload(
            blind_item_id=first["blind_item_id"],
            artifact_sha256=original_artifact_sha,
        ),
    )

    rc = main(_blind_assessment_args(
        blind_dir / "blind_pack.json",
        reveal_path,
        assessment_path,
        output_path,
    ))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID"
    assert not output_path.exists()


def test_experiments_080_import_blind_assessment_requires_reveal_mapping(tmp_path, capsys):
    assessment_path = tmp_path / "blind-assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(
        assessment_path,
        _blind_assessment_payload(blind_item_id="BI-A", artifact_sha256="a" * 64),
    )

    rc = main([
        "experiments",
        "080",
        "import-assessment",
        "--blind-pack",
        str(tmp_path / "blind_pack.json"),
        "--assessment",
        str(assessment_path),
        "--output",
        str(output_path),
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_BLIND_ASSESSMENT_INVALID"
    assert not output_path.exists()


def test_experiments_080_import_assessment_promotes_to_a_controlled(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["validity_class"] == "A_controlled"
    assert payload["assessment_status"] == "assessed"
    assessed = json.loads(output_path.read_text(encoding="utf-8"))
    assert validate_scorecard(assessed) == []
    assert assessed["validity_class"] == "A_controlled"
    assert assessed["assessment_status"] == "assessed"
    assert assessed["guidance_scores"][0]["assessment_method"] == "human"
    assert assessed["guidance_assessment"]["source"] == "imported_assessment"
    assert "Python did not judge guidance manifestation" in assessed["notes"][-1]


def test_experiments_080_import_assessment_requires_delivery_treatment_isolation(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    assert scorecard["assessment_target"] == "delivery_brief"
    assert scorecard["control_integrity"]["treatment_isolation_passed"] is False
    capsys.readouterr()
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["validity_class"] == "invalid_incomplete"
    assessed = json.loads(output_path.read_text(encoding="utf-8"))
    assert assessed["validity_class"] == "invalid_incomplete"


def test_experiments_080_import_assessment_llm_only_becomes_b_integration(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="llm_only"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    assessed = json.loads(output_path.read_text(encoding="utf-8"))
    assert assessed["validity_class"] == "B_integration"
    assert assessed["guidance_scores"][0]["assessment_method"] == "llm_only"


def test_experiments_080_import_assessment_keeps_fact_layer_mismatch_invalid(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_case_from_archive(case_dir, CLEAN_FIXTURE_MANIFEST, source_pack_sha="c" * 64)
    archive_manifest = _copy_archive_to_workspace(ws, CLEAN_FIXTURE_MANIFEST)
    _add_scorecard_archive_reports(archive_manifest)
    _write_terminal_runtime(ws, run_id=archive_manifest.parent.name)
    run_record = ws / "memory.run_record.json"
    scorecard_path = tmp_path / "memory.scorecard.json"
    assert main(_register_args(case_dir, ws, run_record)) == 0
    capsys.readouterr()
    assert main(_score_args(case_dir, run_record, scorecard_path)) == 0
    capsys.readouterr()
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 0
    json.loads(capsys.readouterr().out)
    assessed = json.loads(output_path.read_text(encoding="utf-8"))
    assert assessed["validity_class"] == "invalid_fact_layer_mismatch"


def test_experiments_080_import_assessment_rejects_identity_mismatch(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    assessment = _assessment_payload(method="human")
    assessment["run_id"] = "mabw-20260614T000000Z-other0001"
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, assessment)

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ASSESSMENT_MISMATCH"
    assert not output_path.exists()


def test_experiments_080_import_assessment_rejects_unknown_guidance_entry(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human", entry_id="AG-9999"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH"
    assert not output_path.exists()


def test_experiments_080_import_assessment_rejects_missing_guidance_entry(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    scorecard["guidance_assessment"]["guidance_entry_ids"].append("AG-0002")
    _write_json(scorecard_path, scorecard)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH"
    assert payload["details"]["missing_entry_ids"] == ["AG-0002"]
    assert not output_path.exists()


def test_experiments_080_import_assessment_rejects_missing_scorecard_guidance_binding(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    del scorecard["guidance_assessment"]
    _write_json(scorecard_path, scorecard)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human", entry_id="AG-9999"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH"
    assert not output_path.exists()


def test_experiments_080_import_assessment_rejects_empty_scorecard_guidance_binding(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    scorecard["guidance_assessment"]["guidance_entry_ids"] = []
    _write_json(scorecard_path, scorecard)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH"
    assert not output_path.exists()


def test_experiments_080_import_assessment_rejects_duplicate_scorecard_guidance_binding(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    scorecard["guidance_assessment"]["guidance_entry_ids"] = ["AG-0001", "AG-0001"]
    _write_json(scorecard_path, scorecard)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH"
    assert payload["details"]["duplicate_entry_ids"] == ["AG-0001"]
    assert not output_path.exists()


def test_experiments_080_import_assessment_rejects_already_assessed_scorecard(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    scorecard["guidance_assessment"]["status"] = "assessed"
    _write_json(scorecard_path, scorecard)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_ASSESSMENT_GUIDANCE_MISMATCH"
    assert not output_path.exists()


def test_experiments_080_summarize_aggregates_scorecards_without_quality_claim(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(
        case_dir / "baseline.scorecard.json",
        _scorecard_payload(
            condition="baseline",
            run_id="mabw-20260614T000000Z-baseline01",
            validity_class="B_integration",
            manifestation_score=3,
            overapplication=True,
            assessment_method="llm_only",
            reader_clean_pass=False,
            coverage_delta=-1.0,
        ),
    )
    _write_json(
        case_dir / "memory.scorecard.json",
        _scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory01",
            validity_class="A_controlled",
            manifestation_score=2,
            overapplication=False,
            assessment_method="human",
            coverage_delta=2.0,
        ),
    )
    _write_json(
        case_dir / "memory-contaminated.scorecard.json",
        _scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory02",
            validity_class="invalid_contaminated",
            assessment_status="needs_assessment",
            timing_status="contaminated",
        ),
    )
    output = tmp_path / "summary.json"

    rc = main(_summarize_args(case_dir, output))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["scorecard_count"] == 3
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["schema_version"] == "mabw.experiment_080.case_summary.v1"
    assert "no_python_or_llm_quality_judgment" in summary["summary_boundary"]
    assert "invalid_runs_excluded_from_interpretable_metrics" in summary["summary_boundary"]
    assert summary["run_counts"]["validity_class_counts"]["A_controlled"] == 1
    assert summary["run_counts"]["validity_class_counts"]["B_integration"] == 1
    assert summary["run_counts"]["validity_class_counts"]["invalid_contaminated"] == 1
    assert summary["run_counts"]["a_grade_count"] == 0
    assert summary["run_counts"]["interpretable_run_denominator"] == 0
    assert summary["run_counts"]["invalid_excluded_count"] == 1
    assert summary["condition_counts"]["memory"]["total"] == 2
    assert summary["condition_counts"]["baseline"]["total"] == 1
    assert summary["raw_observed_assessments"]["score_2_manifested_count"] == 1
    assert summary["raw_observed_assessments"]["score_3_overapplication_count"] == 1
    assert {
        item["condition"]
        for item in summary["raw_observed_assessments"]["observations"]
    } == {"baseline", "memory"}
    assert summary["manifestation"]["score_2_manifested_count"] == 0
    assert summary["manifestation"]["score_3_overapplication_count"] == 0
    assert summary["reader_clean"]["pass_count"] == 0
    assert summary["reader_clean"]["total_evaluable"] == 0
    assert summary["reader_clean"]["pass_rate"] is None
    assert summary["coverage_delta"]["numeric_count"] == 0
    assert summary["coverage_delta"]["numeric_sum"] == 0.0
    assert summary["coverage_delta"]["numeric_average"] is None
    assert summary["coverage_delta"]["not_computed_count"] == 0
    assert summary["timing"]["available_count"] == 0
    assert summary["timing"]["contaminated_count"] == 0
    assert summary["valid_interpretable_metrics"]["denominator"] == 0
    assert all(item["formal_interpretable"] is False for item in summary["scorecards"])
    assert any(item["warning"] == "blind_assessment_not_hash_verified" for item in summary["hardening_warnings"])
    assert {"reason": "run_integrity_contaminated_or_non_reference", "count": 1} in summary["invalid_reasons"]


def test_experiments_080_summarize_does_not_count_auditable_target_reader_clean(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(case_dir / "memory.scorecard.json", _auditable_scorecard_payload())

    rc = main(_summarize_args(case_dir))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["summary"]
    assert summary["assessment_target_counts"]["auditable_brief"] == 1
    assert summary["reader_clean"]["total_evaluable"] == 0
    assert summary["scorecards"][0]["assessment_target"] == "auditable_brief"


def test_experiments_080_summarize_counts_only_blind_bound_scorecards_as_formal_metrics(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(
        case_dir / "memory.scorecard.json",
        _auditable_scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory01",
            validity_class="A_controlled",
            manifestation_score=2,
            blind_assessment_verified=True,
        ),
    )
    _write_json(
        case_dir / "prompt.scorecard.json",
        _auditable_scorecard_payload(
            condition="prompt_only",
            run_id="mabw-20260614T000000Z-prompt01",
            validity_class="A_controlled",
            manifestation_score=3,
            overapplication=True,
            blind_assessment_verified=True,
        ),
    )
    _write_json(
        case_dir / "baseline-nonblind.scorecard.json",
        _auditable_scorecard_payload(
            condition="baseline",
            run_id="mabw-20260614T000000Z-baseline01",
            validity_class="A_controlled",
            manifestation_score=2,
            blind_assessment_verified=False,
        ),
    )

    rc = main(_summarize_args(case_dir))

    assert rc == 0
    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary["raw_observed_assessments"]["score_2_manifested_count"] == 2
    assert summary["raw_observed_assessments"]["score_3_overapplication_count"] == 1
    assert len(summary["raw_observed_assessments"]["observations"]) == 3
    assert summary["run_counts"]["interpretable_run_denominator"] == 2
    assert summary["manifestation"]["score_2_manifested_count"] == 1
    assert summary["manifestation"]["score_3_overapplication_count"] == 1
    assert summary["manifestation"]["overapplication_count"] == 1
    assert summary["valid_interpretable_metrics"]["denominator"] == 2
    assert {
        item["condition"]
        for item in summary["valid_interpretable_metrics"]["included_scorecards"]
    } == {"memory", "prompt_only"}
    excluded = {item["condition"]: item for item in summary["exclusions"]}
    assert "blind_assessment_not_hash_verified" in excluded["baseline"]["reasons"]
    assert any(item["warning"] == "low_formal_interpretable_n" for item in summary["hardening_warnings"])


def test_experiments_080_summarize_excludes_binding_invalid_scorecards_from_formal_metrics(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    scorecard = _auditable_scorecard_payload(
        condition="memory",
        run_id="mabw-20260614T000000Z-memory01",
        validity_class="B_integration",
        blind_assessment_verified=True,
    )
    scorecard["control_integrity"]["audit_binding_valid"] = False
    scorecard["audit_binding"]["status"] = "invalid"
    _write_json(case_dir / "memory.scorecard.json", scorecard)

    rc = main(_summarize_args(case_dir))

    assert rc == 0
    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary["raw_observed_assessments"]["score_2_manifested_count"] == 1
    assert summary["valid_interpretable_metrics"]["denominator"] == 0
    assert summary["manifestation"]["score_2_manifested_count"] == 0
    assert summary["exclusions"][0]["condition"] == "memory"
    assert "audit_binding_not_valid" in summary["exclusions"][0]["reasons"]
    assert "control_integrity.audit_binding_valid_not_true" in summary["exclusions"][0]["reasons"]


def test_experiments_080_summarize_handles_missing_condition_scorecards(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(
        case_dir / "memory.scorecard.json",
        _scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory01",
            validity_class="A_controlled",
        ),
    )

    rc = main(_summarize_args(case_dir))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["summary"]
    assert summary["scorecard_count"] == 1
    assert summary["condition_counts"]["baseline"]["total"] == 0
    assert summary["timing"]["by_condition"]["baseline"]["completed_stage_count"] == {
        "count": 0,
        "min": None,
        "max": None,
        "average": None,
    }


def test_experiments_080_summarize_includes_explicit_scorecard_outside_case_dir(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    scorecard_path = tmp_path / "assessed_scorecard.json"
    _write_json(
        scorecard_path,
        _scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory01",
            validity_class="A_controlled",
        ),
    )

    rc = main(_summarize_args(case_dir, scorecards=[scorecard_path]))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["summary"]
    assert payload["scorecard_count"] == 1
    assert payload["scorecard_paths"] == ["<external-scorecard>/assessed_scorecard.json"]
    assert summary["scorecard_count"] == 1
    assert summary["condition_counts"]["memory"]["total"] == 1
    assert summary["scorecards"][0]["path"] == "<external-scorecard>/assessed_scorecard.json"


def test_experiments_080_summarize_rejects_external_scorecard_display_path_collision(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    first_dir = tmp_path / "external-a"
    second_dir = tmp_path / "external-b"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "scorecard.json"
    second = second_dir / "scorecard.json"
    _write_json(
        first,
        _scorecard_payload(
            condition="baseline",
            run_id="mabw-20260614T000000Z-baseline01",
            validity_class="A_controlled",
        ),
    )
    _write_json(
        second,
        _scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory01",
            validity_class="A_controlled",
        ),
    )
    output = tmp_path / "summary.json"

    rc = main(_summarize_args(case_dir, output, scorecards=[first, second]))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_SCORECARD_PATH_COLLISION"
    assert payload["details"]["collisions"] == [
        {
            "display_path": "<external-scorecard>/scorecard.json",
            "first_scorecard": str(first.resolve()),
            "second_scorecard": str(second.resolve()),
        }
    ]
    assert not output.exists()


def test_experiments_080_summarize_rejects_missing_explicit_scorecard(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    missing_scorecard = tmp_path / "assessed_scorecard.json"

    rc = main(_summarize_args(case_dir, scorecards=[missing_scorecard]))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_SCORECARD_INVALID"
    assert payload["details"]["path"] == str(missing_scorecard)


def test_experiments_080_summarize_excludes_invalid_scorecards_from_interpretable_metrics(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(
        case_dir / "memory.scorecard.json",
        _scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory01",
            validity_class="A_controlled",
            manifestation_score=2,
            reader_clean_pass=True,
            coverage_delta=1.0,
            blind_assessment_verified=True,
        ),
    )
    _write_json(
        case_dir / "memory-contaminated.scorecard.json",
        _scorecard_payload(
            condition="memory",
            run_id="mabw-20260614T000000Z-memory02",
            validity_class="invalid_contaminated",
            assessment_status="assessed",
            manifestation_score=2,
            reader_clean_pass=True,
            coverage_delta=99.0,
            timing_status="contaminated",
        ),
    )

    rc = main(_summarize_args(case_dir))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["summary"]
    assert summary["run_counts"]["interpretable_run_denominator"] == 1
    assert summary["manifestation"]["score_2_manifested_count"] == 1
    assert summary["reader_clean"]["pass_count"] == 1
    assert summary["reader_clean"]["total_evaluable"] == 1
    assert summary["coverage_delta"]["numeric_count"] == 1
    assert summary["coverage_delta"]["numeric_sum"] == 1.0
    assert summary["timing"]["available_count"] == 1
    assert summary["timing"]["contaminated_count"] == 0
    assert {"reason": "run_integrity_contaminated_or_non_reference", "count": 1} in summary["invalid_reasons"]


def test_experiments_080_summarize_rejects_invalid_scorecard_file(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    invalid_scorecard = _scorecard_payload()
    invalid_scorecard["validity_class"] = "excellent"
    _write_json(case_dir / "bad.scorecard.json", invalid_scorecard)
    output = tmp_path / "summary.json"

    rc = main(_summarize_args(case_dir, output))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_SCORECARD_INVALID"
    assert not output.exists()


def test_experiments_080_import_assessment_rejects_different_existing_output(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    output_path.write_text("{}\n", encoding="utf-8")
    _write_json(assessment_path, _assessment_payload(method="human"))

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["code"] == "E_EXPERIMENT_080_OUTPUT_EXISTS"
    assert output_path.read_text(encoding="utf-8") == "{}\n"


def test_experiments_080_import_assessment_is_idempotent_when_output_matches(tmp_path, capsys):
    _, scorecard_path = _write_scorecard_draft_from_fixture(tmp_path, capsys)
    assessment_path = tmp_path / "assessment.json"
    output_path = tmp_path / "assessed.scorecard.json"
    _write_json(assessment_path, _assessment_payload(method="human"))
    assert main(_assessment_args(scorecard_path, assessment_path, output_path)) == 0
    capsys.readouterr()
    before = output_path.read_bytes()

    rc = main(_assessment_args(scorecard_path, assessment_path, output_path))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["written"] is False
    assert output_path.read_bytes() == before
