"""Build deterministic provenance projections from workspace control files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    load_stage_specs,
    runtime_state_paths,
    utc_now,
)
from multi_agent_brief.orchestrator_contract import (
    RUNTIME_CLI_CHOICE_PLACEHOLDER,
    require_canonical_runtime,
    resolve_repo_workdir,
)
from multi_agent_brief.provenance.io import (
    ensure_safe_relative_path,
    read_json_object,
    read_jsonl,
    sha256_file,
    sha256_text,
    workspace_relative,
    write_json_atomic,
)
from multi_agent_brief.provenance.model import (
    PROVENANCE_GRAPH_FILE,
    PROVENANCE_GRAPH_SCHEMA,
    GraphAccumulator,
    ProvenanceError,
    node_id,
)
from multi_agent_brief.provenance.references import extract_references
from multi_agent_brief.provenance.validator import validate_graph_payload


OPTIONAL_INPUTS = {
    "claim_ledger": "output/intermediate/claim_ledger.json",
    "audited_brief": "output/intermediate/audited_brief.md",
    "audit_report": "output/intermediate/audit_report.json",
    "feedback_issues": "output/intermediate/feedback_issues.json",
    "repair_plan": "output/intermediate/repair_plan.json",
    "delta_audit_report": "output/intermediate/delta_audit_report.json",
    "auditor_quality_gate_report": "output/intermediate/gates/auditor_quality_gate_report.json",
    "finalize_quality_gate_report": "output/intermediate/gates/finalize_quality_gate_report.json",
    "quality_gate_report": "output/intermediate/quality_gate_report.json",
    "reader_brief": "output/brief.md",
}


def provenance_graph_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / PROVENANCE_GRAPH_FILE


def build_provenance_graph(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Build a provenance projection in memory without writing files."""
    ws = _require_workspace(workspace)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    source_files: list[dict[str, Any]] = []
    warnings: list[str] = []

    required = _load_required_state(ws, source_files=source_files)
    manifest = required["runtime_manifest"]
    workflow = required["workflow_state"]
    registry = required["artifact_registry"]
    events = required["event_log"]
    try:
        runtime = require_canonical_runtime(manifest.get("runtime"))
    except ValueError as exc:
        raise ProvenanceError(
            "runtime_manifest.json contains a historical or unsupported runtime identity; "
            "reset the workspace with an explicit canonical --runtime before building provenance."
        ) from exc
    run_id = str(manifest.get("run_id") or workflow.get("run_id") or registry.get("run_id") or "")
    if not run_id:
        raise ProvenanceError("Runtime state is missing a run_id.")

    try:
        stages = load_stage_specs(repo)
    except RuntimeStateError as exc:
        raise ProvenanceError(str(exc), details=exc.details) from exc

    graph = GraphAccumulator()
    graph.add_node({
        "id": node_id("run", run_id),
        "type": "run",
        "run_id": run_id,
        "mabw_version": manifest.get("mabw_version"),
        "runtime": runtime,
        "ref": ".",
    })

    stage_order = _stage_order(manifest, stages)
    _add_stage_nodes(graph, run_id=run_id, stage_order=stage_order, stages=stages)
    artifact_records = _artifact_records(registry)
    _add_artifact_nodes_and_edges(
        graph,
        workspace=ws,
        artifact_records=artifact_records,
        stage_order=stage_order,
    )
    _add_process_lineage_edges(graph, stages=stages, artifact_records=artifact_records)
    _add_event_nodes_and_edges(graph, events=events, stage_order=stage_order, artifact_records=artifact_records)

    claim_ids: set[str] = set()
    source_ids: set[str] = set()
    claim_ledger_path = ws / OPTIONAL_INPUTS["claim_ledger"]
    if claim_ledger_path.exists():
        _add_source_file(ws, claim_ledger_path, source_files)
        claim_ids, source_ids = _add_claim_projection(
            graph,
            claim_ledger_path=claim_ledger_path,
            warnings=warnings,
        )
    else:
        warnings.append("Optional claim_ledger.json is missing; claim/source projection skipped.")

    _add_markdown_reference_projection(
        graph,
        workspace=ws,
        artifact_id="audited_brief",
        rel_path=OPTIONAL_INPUTS["audited_brief"],
        known_claim_ids=claim_ids,
        known_source_ids=source_ids,
        source_files=source_files,
        warnings=warnings,
    )
    _add_markdown_reference_projection(
        graph,
        workspace=ws,
        artifact_id="reader_brief",
        rel_path=OPTIONAL_INPUTS["reader_brief"],
        known_claim_ids=claim_ids,
        known_source_ids=source_ids,
        source_files=source_files,
        warnings=warnings,
        warn_if_missing=False,
    )
    known_feedback_issue_ids = _add_feedback_projection(
        graph,
        workspace=ws,
        source_files=source_files,
        warnings=warnings,
        stage_order=stage_order,
        artifact_records=artifact_records,
    )
    _add_repair_projection(
        graph,
        workspace=ws,
        source_files=source_files,
        warnings=warnings,
        known_feedback_issue_ids=known_feedback_issue_ids,
    )
    _add_quality_gate_projection(
        graph,
        workspace=ws,
        source_files=source_files,
        warnings=warnings,
        stage_order=stage_order,
        artifact_records=artifact_records,
    )

    nodes = graph.nodes()
    edges = graph.edges()
    payload = {
        "schema_version": PROVENANCE_GRAPH_SCHEMA,
        "run_id": run_id,
        "generated_at": utc_now(),
        "workspace": ".",
        "source_files": sorted(source_files, key=lambda item: str(item.get("path") or "")),
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "warning_count": len(warnings),
            "error_count": 0,
        },
        "warnings": warnings,
    }
    validation = validate_graph_payload(payload, strict=strict)
    payload["summary"]["error_count"] = int(validation.get("error_count", 0))
    if validation.get("errors"):
        raise ProvenanceError(
            "Provenance graph validation failed.",
            details=validation,
        )
    return payload


def build_provenance_workspace(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    strict: bool = False,
    actor: str = "cli",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    graph = build_provenance_graph(workspace=ws, repo_workdir=repo_workdir, strict=strict)
    path = provenance_graph_path(ws)
    write_json_atomic(path, graph)
    graph_hash = sha256_file(path)
    validation = validate_graph_payload(graph, strict=strict)
    _append_provenance_event(
        workspace=ws,
        graph=graph,
        graph_sha256=graph_hash,
        event_type="provenance_graph_built",
        actor=actor,
        validation=validation,
        reason="Provenance projection built.",
    )
    return {
        "ok": validation.get("ok", False),
        "workspace": str(ws),
        "provenance_graph_path": PROVENANCE_GRAPH_FILE,
        "graph_sha256": graph_hash,
        "provenance_graph": graph,
        "validation": validation,
    }


def show_provenance_workspace(*, workspace: str | Path) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    path = provenance_graph_path(ws)
    if not path.exists():
        raise ProvenanceError(
            "provenance_graph.json not found. Run `briefloop provenance build --workspace <ws>` first.",
            details={"path": str(path)},
        )
    graph = read_json_object(path, label="provenance graph")
    validation = validate_graph_payload(graph, strict=False)
    return {
        "ok": validation.get("ok", False),
        "workspace": str(ws),
        "provenance_graph_path": PROVENANCE_GRAPH_FILE,
        "graph_sha256": sha256_file(path),
        "summary": graph.get("summary") or {},
        "validation": validation,
        "provenance_graph": graph,
    }


def validate_provenance_workspace(
    *,
    workspace: str | Path,
    strict: bool = False,
    actor: str = "cli",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    path = provenance_graph_path(ws)
    if not path.exists():
        raise ProvenanceError(
            "provenance_graph.json not found. Run `briefloop provenance build --workspace <ws>` first.",
            details={"path": str(path)},
        )
    graph = read_json_object(path, label="provenance graph")
    validation = validate_graph_payload(graph, strict=strict)
    graph_hash = sha256_file(path)
    _append_provenance_event(
        workspace=ws,
        graph=graph,
        graph_sha256=graph_hash,
        event_type="provenance_graph_validated" if validation.get("ok") else "provenance_graph_invalid",
        actor=actor,
        validation=validation,
        reason="Provenance projection validated." if validation.get("ok") else "Provenance projection invalid.",
    )
    return {
        "ok": validation.get("ok", False),
        "workspace": str(ws),
        "provenance_graph_path": PROVENANCE_GRAPH_FILE,
        "graph_sha256": graph_hash,
        "validation": validation,
    }


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise ProvenanceError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _load_required_state(workspace: Path, *, source_files: list[dict[str, Any]]) -> dict[str, Any]:
    paths = runtime_state_paths(workspace)
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise ProvenanceError(
            "Runtime state is missing. Run `briefloop run --workspace <ws> "
            f"--runtime {RUNTIME_CLI_CHOICE_PLACEHOLDER}` first.",
            details={"missing_state_files": missing},
        )
    runtime_manifest = read_json_object(paths["runtime_manifest"], label="runtime_manifest.json")
    workflow_state = read_json_object(paths["workflow_state"], label="workflow_state.json")
    artifact_registry = read_json_object(paths["artifact_registry"], label="artifact_registry.json")
    event_log = read_jsonl(paths["event_log"], label="event_log.jsonl")
    for path in paths.values():
        _add_source_file(workspace, path, source_files)
    return {
        "runtime_manifest": runtime_manifest,
        "workflow_state": workflow_state,
        "artifact_registry": artifact_registry,
        "event_log": event_log,
    }


def _add_source_file(workspace: Path, path: Path, source_files: list[dict[str, Any]]) -> None:
    if not path.exists():
        return
    rel = workspace_relative(workspace, path)
    if any(item.get("path") == rel for item in source_files):
        return
    source_files.append({"path": rel, "sha256": sha256_file(path)})


def _stage_order(manifest: dict[str, Any], stages: list[dict[str, Any]]) -> list[str]:
    raw = manifest.get("stage_order")
    if isinstance(raw, list) and raw:
        return [str(stage_id) for stage_id in raw if str(stage_id)]
    return [str(stage.get("stage_id")) for stage in stages if stage.get("stage_id")]


def _stage_by_id(stages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(stage.get("stage_id")): stage for stage in stages if stage.get("stage_id")}


def _artifact_records(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = registry.get("artifacts") or {}
    return {
        str(artifact_id): record
        for artifact_id, record in records.items()
        if isinstance(record, dict)
    }


def _add_stage_nodes(
    graph: GraphAccumulator,
    *,
    run_id: str,
    stage_order: list[str],
    stages: list[dict[str, Any]],
) -> None:
    stages_by_id = _stage_by_id(stages)
    for idx, stage_id in enumerate(stage_order):
        stage = stages_by_id.get(stage_id) or {}
        graph.add_node({
            "id": node_id("stage", stage_id),
            "type": "stage",
            "stage_id": stage_id,
            "order": idx,
            "owner": stage.get("owner"),
            "category": stage.get("category"),
        })
        graph.add_edge({
            "from": node_id("run", run_id),
            "to": node_id("stage", stage_id),
            "type": "run_has_stage",
            "method": "runtime_manifest_stage_order",
        })


def _add_artifact_nodes_and_edges(
    graph: GraphAccumulator,
    *,
    workspace: Path,
    artifact_records: dict[str, dict[str, Any]],
    stage_order: list[str],
) -> None:
    known_stages = set(stage_order)
    for artifact_id, record in sorted(artifact_records.items()):
        rel_path = str(record.get("path") or "")
        artifact_path = (
            _safe_workspace_relative_path(
                workspace,
                rel_path,
                label=f"artifact_registry[{artifact_id}].path",
            )
            if rel_path
            else None
        )
        node = {
            "id": node_id("artifact", artifact_id),
            "type": "artifact",
            "artifact_id": artifact_id,
            "ref": rel_path,
            "status": record.get("status"),
            "validation_result": record.get("validation_result"),
            "producer_stage": record.get("producer_stage"),
            "producer_role": record.get("producer_role"),
            "required": bool(record.get("required", False)),
        }
        if artifact_path is not None and artifact_path.exists():
            node["sha256"] = sha256_file(artifact_path)
        graph.add_node(node)

        producer_stage = str(record.get("producer_stage") or "")
        if producer_stage in known_stages:
            graph.add_edge({
                "from": node_id("stage", producer_stage),
                "to": node_id("artifact", artifact_id),
                "type": "stage_produces_artifact",
                "method": "artifact_contract",
            })
        for consumer_stage in record.get("consumer_stages") or []:
            consumer = str(consumer_stage)
            if consumer in known_stages:
                graph.add_edge({
                    "from": node_id("artifact", artifact_id),
                    "to": node_id("stage", consumer),
                    "type": "artifact_consumed_by_stage",
                    "method": "artifact_contract",
                })


def _add_process_lineage_edges(
    graph: GraphAccumulator,
    *,
    stages: list[dict[str, Any]],
    artifact_records: dict[str, dict[str, Any]],
) -> None:
    artifact_ids = set(artifact_records)
    for stage in stages:
        stage_id = str(stage.get("stage_id") or "")
        outputs = [str(item) for item in (stage.get("produces") or []) if str(item) in artifact_ids]
        inputs = [str(item) for item in (stage.get("consumes") or []) if str(item) in artifact_ids]
        for output_id in outputs:
            for input_id in inputs:
                if output_id == input_id:
                    continue
                graph.add_edge({
                    "from": node_id("artifact", output_id),
                    "to": node_id("artifact", input_id),
                    "type": "artifact_derived_from",
                    "method": "stage_consumed_then_produced",
                    "stage_id": stage_id,
                    "semantic_verified": False,
                })


def _add_event_nodes_and_edges(
    graph: GraphAccumulator,
    *,
    events: list[dict[str, Any]],
    stage_order: list[str],
    artifact_records: dict[str, dict[str, Any]],
) -> None:
    known_stages = set(stage_order)
    known_artifacts = set(artifact_records)
    for idx, event in enumerate(events):
        event_id = str(event.get("event_id") or f"missing_event_id_{idx}")
        event_type = str(event.get("event_type") or "")
        if event_type.startswith("provenance_graph_") or event_type.startswith("audience_profile_snapshot_"):
            continue
        graph.add_node({
            "id": node_id("event", event_id),
            "type": "event",
            "event_id": event_id,
            "event_type": event_type,
            "created_at": event.get("created_at"),
            "actor": event.get("actor"),
        })
        stage_id = str(event.get("stage_id") or "")
        artifact_id = str(event.get("artifact_id") or "")
        if event_type == "decision_recorded":
            graph.add_node({
                "id": node_id("decision", event_id),
                "type": "decision",
                "decision_id": event_id,
                "decision": event.get("decision"),
                "created_at": event.get("created_at"),
                "actor": event.get("actor"),
            })
            if stage_id in known_stages:
                graph.add_edge({
                    "from": node_id("decision", event_id),
                    "to": node_id("stage", stage_id),
                    "type": "decision_applies_to_stage",
                    "method": "event_log_stage_id",
                })
        if event_type == "artifact_observed" and artifact_id in known_artifacts:
            graph.add_edge({
                "from": node_id("event", event_id),
                "to": node_id("artifact", artifact_id),
                "type": "event_observed_artifact",
                "method": "event_log_artifact_id",
            })
        if event_type == "artifact_validated" and artifact_id in known_artifacts:
            graph.add_edge({
                "from": node_id("event", event_id),
                "to": node_id("artifact", artifact_id),
                "type": "event_validated_artifact",
                "method": "event_log_artifact_id",
            })


def _add_claim_projection(
    graph: GraphAccumulator,
    *,
    claim_ledger_path: Path,
    warnings: list[str],
) -> tuple[set[str], set[str]]:
    try:
        ledger = ClaimLedger.import_json(claim_ledger_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        warnings.append(f"Optional claim_ledger.json is malformed; claim projection skipped: {exc}")
        return set(), set()

    claim_ids: set[str] = set()
    source_ids: set[str] = set()
    graph.add_node({
        "id": node_id("artifact", "claim_ledger"),
        "type": "artifact",
        "artifact_id": "claim_ledger",
        "ref": "output/intermediate/claim_ledger.json",
    })
    for claim in ledger:
        claim_id = str(claim.claim_id or "")
        if not claim_id:
            continue
        source_id = str(claim.source_id or "")
        claim_ids.add(claim_id)
        if source_id:
            source_ids.add(source_id)
        graph.add_node({
            "id": node_id("claim", claim_id),
            "type": "claim",
            "claim_id": claim_id,
            "statement_sha256": sha256_text(claim.statement or ""),
            "claim_type": claim.claim_type,
            "confidence": claim.confidence,
            "schema_version": claim.schema_version,
        })
        graph.add_edge({
            "from": node_id("claim", claim_id),
            "to": node_id("artifact", "claim_ledger"),
            "type": "claim_recorded_in_artifact",
            "method": "claim_ledger_record",
        })
        if source_id:
            graph.add_node({
                "id": node_id("source", source_id),
                "type": "source",
                "source_id": source_id,
                "ref": source_id,
            })
            graph.add_edge({
                "from": node_id("claim", claim_id),
                "to": node_id("source", source_id),
                "type": "claim_cites_source",
                "method": "declared_reference",
                "semantic_verified": False,
            })
    return claim_ids, source_ids


def _add_markdown_reference_projection(
    graph: GraphAccumulator,
    *,
    workspace: Path,
    artifact_id: str,
    rel_path: str,
    known_claim_ids: set[str],
    known_source_ids: set[str],
    source_files: list[dict[str, Any]],
    warnings: list[str],
    warn_if_missing: bool = True,
) -> None:
    path = workspace / rel_path
    if not path.exists():
        if warn_if_missing:
            warnings.append(f"Optional {rel_path} is missing; reference projection skipped.")
        return
    _add_source_file(workspace, path, source_files)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"Optional {rel_path} could not be read; reference projection skipped: {exc}")
        return
    graph.add_node({
        "id": node_id("artifact", artifact_id),
        "type": "artifact",
        "artifact_id": artifact_id,
        "ref": rel_path,
        "sha256": sha256_file(path),
    })
    for ref in extract_references(
        text,
        known_claim_ids=known_claim_ids,
        known_source_ids=known_source_ids,
    ):
        if ref.normalized_ref_type == "claim":
            if ref.normalized_ref_id not in known_claim_ids:
                warnings.append(f"{rel_path} references unknown claim id: {ref.normalized_ref_id}")
                continue
            graph.add_edge({
                "from": node_id("artifact", artifact_id),
                "to": node_id("claim", ref.normalized_ref_id),
                "type": "artifact_references_claim",
                "method": "citation_marker",
                "raw_ref": ref.raw_ref,
                "normalized_ref_type": "claim",
                "normalized_ref_id": ref.normalized_ref_id,
                "semantic_verified": False,
            })
        elif ref.normalized_ref_type == "source":
            if ref.normalized_ref_id not in known_source_ids:
                warnings.append(f"{rel_path} references unknown source id: {ref.normalized_ref_id}")
                continue
            graph.add_edge({
                "from": node_id("artifact", artifact_id),
                "to": node_id("source", ref.normalized_ref_id),
                "type": "artifact_references_source",
                "method": "citation_marker",
                "raw_ref": ref.raw_ref,
                "normalized_ref_type": "source",
                "normalized_ref_id": ref.normalized_ref_id,
                "semantic_verified": False,
            })


def _read_optional_json(workspace: Path, rel_path: str, *, warnings: list[str], label: str) -> dict[str, Any] | None:
    path = workspace / rel_path
    if not path.exists():
        warnings.append(f"Optional {rel_path} is missing; {label} projection skipped.")
        return None
    try:
        return read_json_object(path, label=label)
    except ProvenanceError as exc:
        warnings.append(f"Optional {rel_path} is malformed; {label} projection skipped: {exc}")
        return None


def _add_feedback_projection(
    graph: GraphAccumulator,
    *,
    workspace: Path,
    source_files: list[dict[str, Any]],
    warnings: list[str],
    stage_order: list[str],
    artifact_records: dict[str, dict[str, Any]],
) -> set[str]:
    known_issue_ids: set[str] = set()
    rel_path = "output/intermediate/feedback_issues.json"
    payload = _read_optional_json(workspace, rel_path, warnings=warnings, label="feedback_issues.json")
    if payload is None:
        return known_issue_ids
    _add_source_file(workspace, workspace / rel_path, source_files)
    known_stages = set(stage_order)
    known_artifacts = set(artifact_records)
    for issue in payload.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        issue_id = str(issue.get("issue_id") or "")
        if not issue_id:
            continue
        known_issue_ids.add(issue_id)
        graph.add_node({
            "id": node_id("feedback_issue", issue_id),
            "type": "feedback_issue",
            "issue_id": issue_id,
            "source": issue.get("source"),
            "severity": issue.get("severity"),
            "status": issue.get("status"),
            "category": issue.get("category"),
        })
        stage_id = str(issue.get("stage_id") or "")
        artifact_id = str(issue.get("artifact_id") or "")
        if stage_id in known_stages:
            graph.add_edge({
                "from": node_id("feedback_issue", issue_id),
                "to": node_id("stage", stage_id),
                "type": "feedback_targets_stage",
                "method": "feedback_issue_stage_id",
            })
        if artifact_id in known_artifacts:
            graph.add_edge({
                "from": node_id("feedback_issue", issue_id),
                "to": node_id("artifact", artifact_id),
                "type": "feedback_targets_artifact",
                "method": "feedback_issue_artifact_id",
            })
    return known_issue_ids


def _add_repair_projection(
    graph: GraphAccumulator,
    *,
    workspace: Path,
    source_files: list[dict[str, Any]],
    warnings: list[str],
    known_feedback_issue_ids: set[str],
) -> None:
    rel_path = "output/intermediate/repair_plan.json"
    payload = _read_optional_json(workspace, rel_path, warnings=warnings, label="repair_plan.json")
    if payload is None:
        return
    _add_source_file(workspace, workspace / rel_path, source_files)
    for plan in payload.get("repair_plans") or []:
        if not isinstance(plan, dict):
            continue
        plan_id = str(plan.get("repair_plan_id") or "")
        if not plan_id:
            continue
        graph.add_node({
            "id": node_id("repair_plan", plan_id),
            "type": "repair_plan",
            "repair_plan_id": plan_id,
            "status": plan.get("status"),
            "target_stage": plan.get("target_stage"),
        })
        for issue_id in plan.get("issue_ids") or []:
            issue_id = str(issue_id)
            if issue_id not in known_feedback_issue_ids:
                warnings.append(
                    f"repair_plan.json references unknown feedback issue id: {issue_id}; repair edge skipped."
                )
                continue
            graph.add_edge({
                "from": node_id("repair_plan", plan_id),
                "to": node_id("feedback_issue", issue_id),
                "type": "repair_plan_addresses_issue",
                "method": "repair_plan_issue_ids",
            })


def _safe_workspace_relative_path(workspace: Path, rel_path: str, *, label: str) -> Path:
    ensure_safe_relative_path(rel_path, label=label)
    resolved_workspace = workspace.expanduser().resolve()
    resolved = (resolved_workspace / rel_path).resolve()
    try:
        resolved.relative_to(resolved_workspace)
    except ValueError as exc:
        raise ProvenanceError(
            f"{label} resolves outside workspace.",
            details={"workspace": str(resolved_workspace), "path": str(resolved), label: rel_path},
        ) from exc
    return resolved


def _add_quality_gate_projection(
    graph: GraphAccumulator,
    *,
    workspace: Path,
    source_files: list[dict[str, Any]],
    warnings: list[str],
    stage_order: list[str],
    artifact_records: dict[str, dict[str, Any]],
) -> None:
    scoped_report_paths = [
        (
            "output/intermediate/gates/auditor_quality_gate_report.json",
            "auditor_quality_gate_report.json",
        ),
        (
            "output/intermediate/gates/finalize_quality_gate_report.json",
            "finalize_quality_gate_report.json",
        ),
    ]
    payloads: list[tuple[str, dict[str, Any]]] = []
    for rel_path, label in scoped_report_paths:
        payload = _read_optional_json(workspace, rel_path, warnings=warnings, label=label)
        if payload is not None:
            payloads.append((rel_path, payload))
    if not payloads:
        legacy_rel_path = "output/intermediate/quality_gate_report.json"
        legacy_payload = _read_optional_json(
            workspace,
            legacy_rel_path,
            warnings=warnings,
            label="quality_gate_report.json",
        )
        if legacy_payload is not None:
            payloads.append((legacy_rel_path, legacy_payload))
    if not payloads:
        return
    known_stages = set(stage_order)
    known_artifacts = set(artifact_records)
    for rel_path, payload in payloads:
        _add_source_file(workspace, workspace / rel_path, source_files)
        for finding in payload.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            finding_id = str(finding.get("finding_id") or "")
            if not finding_id:
                continue
            graph.add_node({
                "id": node_id("gate_finding", finding_id),
                "type": "gate_finding",
                "finding_id": finding_id,
                "finding_type": finding.get("finding_type"),
                "severity": finding.get("severity"),
                "blocking_level": finding.get("blocking_level"),
                "blocking": finding.get("blocking"),
            })
            stage_id = str(finding.get("gate_stage_id") or finding.get("stage_id") or "")
            artifact_id = str(finding.get("gate_artifact_id") or finding.get("artifact_id") or "")
            if artifact_id == "quality_gate_report" and "auditor_quality_gate_report" in known_artifacts:
                artifact_id = "auditor_quality_gate_report"
            if stage_id in known_stages:
                graph.add_edge({
                    "from": node_id("gate_finding", finding_id),
                    "to": node_id("stage", stage_id),
                    "type": "gate_finding_targets_stage",
                    "method": "quality_gate_finding_stage_id",
                })
            if artifact_id in known_artifacts:
                graph.add_edge({
                    "from": node_id("gate_finding", finding_id),
                    "to": node_id("artifact", artifact_id),
                    "type": "gate_finding_targets_artifact",
                    "method": "quality_gate_finding_artifact_id",
                })


def _append_provenance_event(
    *,
    workspace: Path,
    graph: dict[str, Any],
    graph_sha256: str,
    event_type: str,
    actor: str,
    validation: dict[str, Any],
    reason: str,
) -> None:
    append_event(
        workspace=workspace,
        run_id=str(graph.get("run_id") or ""),
        event_type=event_type,
        actor=actor,
        artifact_id="provenance_graph",
        reason=reason,
        metadata={
            "graph_sha256": graph_sha256,
            "warning_count": int(validation.get("warning_count", 0)),
            "error_count": int(validation.get("error_count", 0)),
            "node_count": int(validation.get("node_count", 0)),
            "edge_count": int(validation.get("edge_count", 0)),
        },
    )
