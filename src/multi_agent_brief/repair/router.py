"""Deterministic repair routing for blocked or failed workspaces.

The router is intentionally read-only. It does not create repair plans, mutate
artifacts, call agents, or execute repair. It only maps known finding shapes to
the stage/artifact owner that may repair them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.runtime_state import (
    load_artifact_contracts,
    load_stage_specs,
    runtime_state_paths,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import _build_artifact_registry
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.quality_gates.contract import quality_gate_paths


INTERMEDIATE_DIR = Path("output/intermediate")
STAGE_REPAIR_ARTIFACTS = {
    "source-discovery": ["input/sources/*"],
    "input-governance": ["input/*"],
    "scout": ["output/intermediate/candidate_claims.json"],
    "screener": ["output/intermediate/screened_candidates.json"],
    "claim-ledger": ["output/intermediate/claim_ledger.json"],
    "analyst": ["output/intermediate/audited_brief.md"],
    "editor": ["output/intermediate/audited_brief.md"],
    "auditor": ["output/intermediate/audit_report.json"],
    "finalize": ["output/delivery/brief.md"],
}
ARTIFACT_PATHS = {
    "candidate_claims": "output/intermediate/candidate_claims.json",
    "screened_candidates": "output/intermediate/screened_candidates.json",
    "claim_ledger": "output/intermediate/claim_ledger.json",
    "audited_brief": "output/intermediate/audited_brief.md",
    "audit_report": "output/intermediate/audit_report.json",
    "auditor_quality_gate_report": "output/intermediate/gates/auditor_quality_gate_report.json",
    "finalize_quality_gate_report": "output/intermediate/gates/finalize_quality_gate_report.json",
}
OWNER_ALIASES = {
    "source": "source-discovery",
    "source_discovery": "source-discovery",
    "source-discovery": "source-discovery",
    "claim_ledger": "claim-ledger",
    "claim-ledger": "claim-ledger",
    "analyst": "analyst",
    "editor": "editor",
    "auditor": "auditor",
    "human": "human",
    "orchestrator": "orchestrator",
}
RERUN_FROM_BY_OWNER = {
    "source-discovery": "input-governance",
    "input-governance": "scout",
    "scout": "screener",
    "screener": "claim-ledger",
    "claim-ledger": "analyst",
    "analyst": "editor",
    "editor": "auditor",
    "auditor": "gates",
    "finalize": "finalize",
}
DOWNSTREAM_BLOCKED_EDITS = {
    "source-discovery": [
        "output/intermediate/candidate_claims.json",
        "output/intermediate/screened_candidates.json",
        "output/intermediate/claim_ledger.json",
        "output/intermediate/audited_brief.md",
        "output/intermediate/audit_report.json",
    ],
    "claim-ledger": [
        "output/intermediate/audited_brief.md",
        "output/intermediate/audit_report.json",
    ],
    "analyst": [
        "output/intermediate/analyst_draft_snapshot.md",
        "output/intermediate/claim_ledger.json",
        "output/intermediate/audit_report.json",
    ],
    "editor": [
        "output/intermediate/analyst_draft_snapshot.md",
        "output/intermediate/claim_ledger.json",
        "output/intermediate/audit_report.json",
    ],
}
IMPORTED_FACT_LAYER_FORBIDDEN_ARTIFACTS = {
    "output/input_classification.json",
    "output/intermediate/candidate_claims.json",
    "output/intermediate/screened_candidates.json",
    "output/intermediate/claim_ledger.json",
}
IMPORTED_FACT_LAYER_FORBIDDEN_PREFIXES = (
    "input/sources/",
)


def route_repair(
    *,
    workspace: str | Path,
    route_index: int | None = None,
    finding_id: str | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    if route_index is not None and finding_id is not None:
        return {
            "ok": False,
            "error_code": "E_REPAIR_ROUTE_SELECTION_INVALID",
            "message": "Use either --route-index or --finding-id, not both.",
            "workspace": str(ws),
        }
    if not (ws / "config.yaml").exists():
        return {
            "ok": False,
            "error_code": "E_REPAIR_WORKSPACE_MISSING",
            "message": f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            "workspace": str(ws),
        }

    findings, input_errors, payloads = _collect_findings(ws)
    if input_errors:
        return {
            "ok": False,
            "error_code": "E_REPAIR_INPUT_INVALID",
            "message": "Repair route inputs are invalid or unreadable.",
            "workspace": str(ws),
            "input_errors": input_errors,
        }
    routes = [_route_for_finding(finding) for finding in findings]
    routes = [route for route in routes if route is not None]
    routes = sorted(routes, key=_route_priority)
    routes = _annotated_routes(routes, imported_fact_layer=_workspace_has_fact_layer_import(payloads))
    selected = _select_route(routes, route_index=route_index, finding_id=finding_id)
    if not selected.get("ok", True):
        return {
            "ok": False,
            "workspace": str(ws),
            "routes": routes,
            "finding_count": len(findings),
            **selected,
        }
    selected = _with_run_integrity_context(selected, payloads.get("workflow_state"))
    return {
        "ok": True,
        "workspace": str(ws),
        **selected,
        "routes": routes,
        "finding_count": len(findings),
    }


def _collect_findings(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    input_errors: list[dict[str, str]] = []
    payloads: dict[str, dict[str, Any]] = {}
    input_paths = _input_paths(workspace)
    for label, path in input_paths.items():
        payload, error = _read_json_object(path)
        if error:
            input_errors.append({"source": label, "path": _workspace_relative(workspace, path), "error": error})
            continue
        if payload is None:
            continue
        payloads[label] = payload
    current_stage = _current_workflow_stage(payloads.get("workflow_state"))
    for label, payload in payloads.items():
        path = input_paths[label]
        rel_path = _workspace_relative(workspace, path)
        findings.extend(_findings_from_payload(payload, source=label, path=rel_path))
        if label == "finalize_report" and current_stage == "finalize":
            findings.extend(_findings_from_finalize_report(payload, path=rel_path))
    registry = payloads.get("artifact_registry")
    findings.extend(_findings_from_artifact_registry(registry))
    findings.extend(_findings_from_frozen_artifact_integrity(workspace, payloads))
    return _dedupe_findings(findings), input_errors, payloads


def _current_workflow_stage(workflow_state: dict[str, Any] | None) -> str:
    stage = workflow_state.get("current_stage") if isinstance(workflow_state, dict) else None
    return stage if isinstance(stage, str) else ""


def _input_paths(workspace: Path) -> dict[str, Path]:
    gate_paths = quality_gate_paths(workspace)
    runtime_paths = runtime_state_paths(workspace)
    paths = {
        "auditor_quality_gate_report": gate_paths["auditor_quality_gate_report"],
        "finalize_quality_gate_report": gate_paths["finalize_quality_gate_report"],
        "audit_report": workspace / INTERMEDIATE_DIR / "audit_report.json",
        "finalize_report": workspace / INTERMEDIATE_DIR / "finalize_report.json",
        "runtime_manifest": runtime_paths["runtime_manifest"],
        "workflow_state": runtime_paths["workflow_state"],
        "artifact_registry": runtime_paths["artifact_registry"],
    }
    if not gate_paths["auditor_quality_gate_report"].exists() and not gate_paths["finalize_quality_gate_report"].exists():
        paths["quality_gate_report"] = gate_paths["quality_gate_report"]
    return paths


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, str(exc)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "JSON payload must be an object"
    return payload, None


def _findings_from_payload(payload: dict[str, Any], *, source: str, path: str) -> list[dict[str, Any]]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return []
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source_stage_id = metadata.get("gate_stage_id") or metadata.get("stage_id")
    report_status = payload.get("status") or payload.get("audit_status")
    normalized: list[dict[str, Any]] = []
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        item = dict(finding)
        item.setdefault("_source", source)
        item.setdefault("_source_path", path)
        item.setdefault("_source_index", idx)
        if report_status:
            item.setdefault("_report_status", report_status)
        if source_stage_id:
            item.setdefault("_source_stage_id", source_stage_id)
        normalized.append(item)
    return normalized


def _findings_from_finalize_report(payload: dict[str, Any], *, path: str) -> list[dict[str, Any]]:
    reader_clean = payload.get("reader_clean")
    if not isinstance(reader_clean, dict) or reader_clean.get("status") == "pass":
        return []
    sample_findings = reader_clean.get("sample_findings")
    if not isinstance(sample_findings, list):
        return []
    normalized: list[dict[str, Any]] = []
    for idx, finding in enumerate(sample_findings):
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "reader_clean_residue")
        message = str(finding.get("message") or f"Reader-clean failure: {kind}.")
        residue = str(finding.get("text") or "")
        if residue:
            message = f"{message} Residue: {residue!r}."
        normalized.append({
            "_source": "finalize_report",
            "_source_path": path,
            "_source_index": idx,
            "_source_stage_id": "finalize",
            "finding_id": f"READER_CLEAN_{idx + 1:03d}",
            "finding_type": f"reader_clean_{kind}",
            "category": "reader_clean",
            "severity": "medium",
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": message,
            "recommended_action": "repair_editor_audited_brief_and_rerun_auditor_finalize",
        })
    return normalized


def _findings_from_artifact_registry(registry: dict[str, Any] | None) -> list[dict[str, Any]]:
    artifacts = registry.get("artifacts") if isinstance(registry, dict) else None
    if not isinstance(artifacts, dict):
        return []
    findings: list[dict[str, Any]] = []
    for artifact_id, record in artifacts.items():
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "")
        validation_result = str(record.get("validation_result") or "")
        blocking_reason = str(record.get("blocking_reason") or "")
        if status == "missing" and not blocking_reason and validation_result in {"", "not_checked"}:
            continue
        if status not in {"invalid", "missing"}:
            continue
        findings.append({
            "_source": "artifact_registry",
            "_source_path": "output/intermediate/artifact_registry.json",
            "finding_type": validation_result or "artifact_invalid",
            "artifact_id": artifact_id,
            "message": blocking_reason or f"Artifact {artifact_id} is {status}.",
        })
    return findings


def _findings_from_frozen_artifact_integrity(
    workspace: Path,
    payloads: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    manifest = payloads.get("runtime_manifest")
    workflow = payloads.get("workflow_state")
    old_registry = payloads.get("artifact_registry")
    if not isinstance(manifest, dict) or not isinstance(workflow, dict) or not isinstance(old_registry, dict):
        return []
    run_id = str(manifest.get("run_id") or "")
    if not run_id:
        return []
    try:
        repo = resolve_repo_workdir(None, workspace=workspace)
        stages = load_stage_specs(repo)
        artifacts = load_artifact_contracts(repo)
        current_registry = _build_artifact_registry(
            workspace=workspace,
            run_id=run_id,
            artifacts=artifacts,
            workflow=workflow,
            updated_at=utc_now(),
        )
    except Exception:
        return []

    old_records = old_registry.get("artifacts")
    current_records = current_registry.get("artifacts")
    if not isinstance(old_records, dict) or not isinstance(current_records, dict):
        return []

    findings: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_id = str(artifact.get("artifact_id") or "")
        owner = str(artifact.get("producer_stage") or "")
        if not artifact_id or not owner or not _stage_is_complete(workflow, owner):
            continue
        old_record = old_records.get(artifact_id)
        current_record = current_records.get(artifact_id)
        if not isinstance(old_record, dict) or not isinstance(current_record, dict):
            continue
        old_sha = str(old_record.get("sha256") or "")
        current_sha = str(current_record.get("sha256") or "")
        if not old_sha or (current_sha and current_sha == old_sha):
            continue
        path = str(current_record.get("path") or old_record.get("path") or artifact.get("path") or artifact_id)
        if not current_sha or current_record.get("status") == "missing":
            message = (
                f"Frozen artifact '{path}' from owner stage '{owner}' is missing after stage-complete; "
                "route repair back to the owner stage."
            )
        else:
            message = (
                f"Frozen artifact '{path}' from owner stage '{owner}' changed after stage-complete; "
                "route repair back to the owner stage instead of downstream in-place conversion."
            )
        findings.append(
            {
                "_source": "transaction_integrity",
                "_source_path": "output/intermediate/artifact_registry.json",
                "finding_id": f"TX_FROZEN_{artifact_id}",
                "finding_type": "frozen_artifact_changed",
                "category": "transaction_integrity",
                "severity": "high",
                "artifact_id": artifact_id,
                "repair_owner": owner,
                "repair_stage_id": owner,
                "repair_artifact_id": artifact_id,
                "message": message,
                "recommended_action": (
                    "Use repair start/complete for local inspection, or start a fresh workspace for "
                    "reference-grade evidence."
                ),
                "run_integrity_effect": {
                    "reference_eligible": False,
                    "reason": "Frozen artifact changed after stage completion.",
                },
            }
        )
    return findings


def _stage_is_complete(workflow: dict[str, Any], stage_id: str) -> bool:
    statuses = workflow.get("stage_statuses")
    if not isinstance(statuses, dict):
        return False
    stage = statuses.get(stage_id)
    return isinstance(stage, dict) and stage.get("status") == "complete"


def _route_for_finding(finding: dict[str, Any]) -> dict[str, Any] | None:
    if _is_input_limitation_finding(finding):
        return _input_limitation_route(finding)

    explicit = _explicit_repair_route(finding)
    if explicit is not None:
        return explicit

    text = _finding_text(finding)
    artifact_id = str(finding.get("artifact_id") or finding.get("repair_artifact_id") or "")
    finding_type = str(finding.get("finding_type") or finding.get("category") or "").lower()

    if _is_source_pack_missing_excerpt(text, finding_type):
        return _route(
            repair_owner="source-discovery",
            allowed_artifacts=["input/sources/*"],
            must_rerun_from="input-governance",
            blocked_direct_edits=[
                "output/intermediate/candidate_claims.json",
                "output/intermediate/screened_candidates.json",
                "output/intermediate/claim_ledger.json",
                "output/intermediate/audited_brief.md",
                "output/intermediate/audit_report.json",
            ],
            reason=_reason(finding, "source pack missing raw excerpt/snippet"),
            source=finding,
        )

    if _is_claim_ledger_issue(text, finding_type, artifact_id):
        return _route(
            repair_owner="claim-ledger",
            allowed_artifacts=["output/intermediate/claim_ledger.json"],
            must_rerun_from="analyst",
            blocked_direct_edits=[
                "output/intermediate/audited_brief.md",
                "output/intermediate/audit_report.json",
            ],
            reason=_reason(finding, "claim ledger schema/support issue"),
            source=finding,
        )

    if _is_audited_brief_binding_issue(finding_type, artifact_id):
        return _route(
            repair_owner="editor",
            allowed_artifacts=["output/intermediate/audited_brief.md"],
            must_rerun_from="auditor",
            blocked_direct_edits=[
                "output/intermediate/claim_ledger.json",
                "output/intermediate/audit_report.json",
            ],
            reason=_reason(finding, "audited brief claim binding issue"),
            source=finding,
        )
    return None


def _explicit_repair_route(finding: dict[str, Any]) -> dict[str, Any] | None:
    owner = _normalize_owner(finding.get("repair_stage_id") or finding.get("repair_owner"))
    if owner is None:
        return None
    repair_artifact = _repair_artifact_path(finding.get("repair_artifact_id"))
    if owner == "analyst" and repair_artifact == "output/intermediate/audited_brief.md":
        owner = "editor"
    if owner == "source-discovery":
        allowed_artifacts = STAGE_REPAIR_ARTIFACTS[owner]
    else:
        allowed_artifacts = [repair_artifact] if repair_artifact else STAGE_REPAIR_ARTIFACTS.get(owner, [])
    if not allowed_artifacts:
        return None
    return _route(
        repair_owner=owner,
        allowed_artifacts=allowed_artifacts,
        must_rerun_from=RERUN_FROM_BY_OWNER.get(owner, ""),
        blocked_direct_edits=DOWNSTREAM_BLOCKED_EDITS.get(owner, []),
        reason=_reason(finding, f"{owner} repair required"),
        source=finding,
    )


def _normalize_owner(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace(" ", "-")
    if not key:
        return None
    return OWNER_ALIASES.get(key)


def _repair_artifact_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    artifact_id = value.strip()
    if not artifact_id:
        return None
    if "/" in artifact_id or artifact_id.startswith("input/"):
        return artifact_id
    return ARTIFACT_PATHS.get(artifact_id)


def _is_source_pack_missing_excerpt(text: str, finding_type: str) -> bool:
    if "source" not in text and "raw_excerpt" not in text and "snippet" not in text:
        return False
    return (
        "raw_excerpt" in text
        or "raw excerpt" in text
        or "snippet" in text
        or "missing_raw_excerpt" in finding_type
        or "missing_snippet" in finding_type
    )


def _is_claim_ledger_issue(text: str, finding_type: str, artifact_id: str) -> bool:
    if artifact_id == "claim_ledger":
        return True
    if "claim_ledger" not in text and "claim ledger" not in text:
        return False
    return any(token in text or token in finding_type for token in ("schema", "support", "invalid", "missing support"))


def _is_input_limitation_finding(finding: dict[str, Any]) -> bool:
    finding_type = str(finding.get("finding_type") or finding.get("category") or "").lower()
    return finding_type in {"insufficient_claims", "no_reportable_claims"}


def _input_limitation_route(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "repair_owner": "none",
        "allowed_artifacts": [],
        "must_rerun_from": "",
        "blocked_direct_edits": [
            "output/intermediate/claim_ledger.json",
            "output/intermediate/audited_brief.md",
            "output/intermediate/audit_report.json",
        ],
        "reason": _reason(finding, "Input limitation requires human review or a fresh evidence setup."),
        "recommended_action": "request_human_review_or_start_fresh_workspace",
        "run_integrity_effect": None,
        "source": {
            "file": finding.get("_source_path"),
            "kind": finding.get("_source"),
            "stage_id": finding.get("_source_stage_id") or finding.get("gate_stage_id") or finding.get("stage_id"),
            "finding_id": finding.get("finding_id") or finding.get("id"),
            "finding_type": finding.get("finding_type") or finding.get("category"),
            "artifact_id": finding.get("artifact_id"),
            "route_classification": "input_limitation",
        },
    }


def _is_audited_brief_binding_issue(finding_type: str, artifact_id: str) -> bool:
    return (
        finding_type in {"unsupported_claim", "claim_binding_imprecise"}
        and artifact_id in {"", "audited_brief"}
    )


def _route(
    *,
    repair_owner: str,
    allowed_artifacts: list[str],
    must_rerun_from: str,
    blocked_direct_edits: list[str],
    reason: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "repair_owner": repair_owner,
        "allowed_artifacts": allowed_artifacts,
        "must_rerun_from": must_rerun_from,
        "blocked_direct_edits": blocked_direct_edits,
        "reason": reason,
        "recommended_action": source.get("recommended_action"),
        "run_integrity_effect": source.get("run_integrity_effect"),
        "source": {
            "file": source.get("_source_path"),
            "kind": source.get("_source"),
            "stage_id": source.get("_source_stage_id") or source.get("gate_stage_id") or source.get("stage_id"),
            "finding_id": source.get("finding_id") or source.get("id"),
            "finding_type": source.get("finding_type") or source.get("category"),
            "artifact_id": source.get("artifact_id"),
            "severity": source.get("severity"),
            "status": source.get("_report_status") or source.get("status") or source.get("audit_status"),
            "blocking": source.get("blocking"),
        },
    }


def _no_route() -> dict[str, Any]:
    return {
        "repair_owner": "none",
        "allowed_artifacts": [],
        "must_rerun_from": "",
        "blocked_direct_edits": [],
        "reason": "No deterministic repair route found.",
        "recommended_action": "start_fresh_workspace_or_request_human_review",
        "run_integrity_effect": None,
        "source": {},
    }


def _no_legal_route() -> dict[str, Any]:
    route = _no_route()
    route["ok"] = False
    route["error_code"] = "E_REPAIR_NO_LEGAL_ROUTE"
    route["message"] = (
        "No legal deterministic repair route found. Available routes target imported frozen fact-layer "
        "artifacts or require human review."
    )
    route["reason"] = (
        "No legal deterministic repair route found. Available routes target imported frozen fact-layer "
        "artifacts or require human review."
    )
    route["recommended_action"] = "start_fresh_workspace_or_request_human_review"
    route["no_legal_route"] = True
    return route


def _workspace_has_fact_layer_import(payloads: dict[str, dict[str, Any]]) -> bool:
    manifest = payloads.get("runtime_manifest")
    return isinstance(manifest, dict) and isinstance(manifest.get("fact_layer_import"), dict)


def _annotated_routes(routes: list[dict[str, Any]], *, imported_fact_layer: bool) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for rank, route in enumerate(routes):
        item = dict(route)
        forbidden = imported_fact_layer and _route_targets_imported_fact_layer(item)
        item["route_rank"] = rank
        item["is_blocking"] = _route_is_blocking(item)
        item["is_imported_fact_layer_forbidden"] = forbidden
        item["default_selected"] = False
        annotated.append(item)
    default = _default_selected_route(annotated)
    if default is not None:
        default["default_selected"] = True
    return annotated


def _select_route(
    routes: list[dict[str, Any]],
    *,
    route_index: int | None,
    finding_id: str | None,
) -> dict[str, Any]:
    if finding_id is not None:
        for route in routes:
            source = route.get("source") if isinstance(route.get("source"), dict) else {}
            if source.get("finding_id") == finding_id:
                return _selected_route_or_forbidden_error(route)
        return {
            "ok": False,
            "error_code": "E_REPAIR_ROUTE_NOT_FOUND",
            "message": f"No deterministic repair route found for finding_id '{finding_id}'.",
        }
    if route_index is not None:
        if route_index < 0 or route_index >= len(routes):
            return {
                "ok": False,
                "error_code": "E_REPAIR_ROUTE_NOT_FOUND",
                "message": f"No deterministic repair route found for route_index {route_index}.",
            }
        return _selected_route_or_forbidden_error(routes[route_index])
    if not routes:
        return _no_route()
    selected = _default_selected_route(routes)
    return selected if selected is not None else _no_legal_route()


def _selected_route_or_forbidden_error(route: dict[str, Any]) -> dict[str, Any]:
    if route.get("is_imported_fact_layer_forbidden") is not True:
        return route
    source = route.get("source") if isinstance(route.get("source"), dict) else {}
    finding_id = source.get("finding_id") or source.get("finding_type") or "<unknown>"
    return {
        "ok": False,
        "error_code": "E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN",
        "message": (
            f"Repair route '{finding_id}' targets imported frozen fact-layer artifacts. "
            "Start a fresh condition workspace or use human review; do not repair imported fact layer artifacts in place."
        ),
        "selected_route": route,
        "allowed_artifacts": list(route.get("allowed_artifacts") or []),
    }


def _default_selected_route(routes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not routes:
        return None
    first = routes[0]
    source = first.get("source") if isinstance(first.get("source"), dict) else {}
    if source.get("route_classification") == "input_limitation":
        return first
    for route in routes:
        if route.get("repair_owner") in {None, "", "none"}:
            continue
        if route.get("is_imported_fact_layer_forbidden") is True:
            continue
        return route
    return None


def _route_priority(route: dict[str, Any]) -> tuple[int, int, int, str, str]:
    source = route.get("source") if isinstance(route.get("source"), dict) else {}
    kind = str(source.get("kind") or "")
    finding_type = str(source.get("finding_type") or "")
    repair_owner = str(route.get("repair_owner") or "")
    if source.get("route_classification") == "input_limitation":
        return (0, 0, 0, kind, finding_type)
    blocking_priority = 0 if _route_is_blocking(route) else 1
    kind_priority = {
        "transaction_integrity": 0,
        "auditor_quality_gate_report": 1,
        "quality_gate_report": 1,
        "finalize_quality_gate_report": 2,
        "audit_report": 3,
        "finalize_report": 4,
        "artifact_registry": 5,
    }.get(kind, 20)
    if finding_type == "frozen_artifact_changed":
        kind_priority = 0
    if repair_owner == "none":
        kind_priority = 90
    finding_id = str(source.get("finding_id") or "")
    return (1, blocking_priority, kind_priority, finding_id, finding_type)


def _route_is_blocking(route: dict[str, Any]) -> bool:
    source = route.get("source") if isinstance(route.get("source"), dict) else {}
    kind = str(source.get("kind") or "")
    finding_type = str(source.get("finding_type") or "")
    status = str(source.get("status") or "").lower()
    severity = str(source.get("severity") or "").lower()
    blocking = source.get("blocking")
    if source.get("route_classification") == "input_limitation":
        return True
    if kind == "transaction_integrity" or finding_type == "frozen_artifact_changed":
        return True
    if isinstance(blocking, bool):
        return blocking
    if status in {"fail", "failed", "block", "blocked", "blocking"}:
        return True
    if kind in {"auditor_quality_gate_report", "finalize_quality_gate_report", "quality_gate_report"}:
        return severity in {"high", "critical", "blocker", "blocking"}
    if kind == "audit_report":
        return severity in {"high", "critical", "blocker", "blocking"} or status in {"fail", "block", "blocking"}
    return False


def _route_targets_imported_fact_layer(route: dict[str, Any]) -> bool:
    for artifact in route.get("allowed_artifacts") or []:
        if _path_targets_imported_fact_layer(str(artifact)):
            return True
    return False


def _path_targets_imported_fact_layer(path_or_pattern: str) -> bool:
    normalized = path_or_pattern.strip().replace("\\", "/")
    if not normalized:
        return False
    if normalized in {"input/sources", "input/sources/*"}:
        return True
    if normalized in IMPORTED_FACT_LAYER_FORBIDDEN_ARTIFACTS:
        return True
    stripped = normalized.rstrip("*")
    return any(stripped.startswith(prefix) for prefix in IMPORTED_FACT_LAYER_FORBIDDEN_PREFIXES)


def _with_run_integrity_context(route: dict[str, Any], workflow: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(workflow, dict):
        return route
    integrity = workflow.get("run_integrity")
    if not isinstance(integrity, dict):
        return route
    status = str(integrity.get("status") or "")
    if status in {"", "clean"} and integrity.get("reference_eligible", True) is True:
        return route
    updated = dict(route)
    existing_effect = updated.get("run_integrity_effect")
    if not isinstance(existing_effect, dict):
        existing_effect = {}
    updated["run_integrity_effect"] = {
        **existing_effect,
        "status": status or "unknown",
        "reference_eligible": False,
        "reason": (
            "This run is already non-reference-eligible. Repair can support local inspection "
            "but cannot restore clean reference eligibility."
        ),
    }
    recommended = updated.get("recommended_action")
    if not isinstance(recommended, str) or not recommended.strip():
        recommended = "repair_for_local_inspection_or_start_fresh_workspace"
    elif "local inspection" not in recommended:
        recommended = f"{recommended}; repair is local inspection only for this non-reference run"
    updated["recommended_action"] = recommended
    return updated


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for finding in findings:
        key = (
            str(finding.get("finding_id") or finding.get("id") or ""),
            str(finding.get("finding_type") or finding.get("category") or ""),
            str(finding.get("artifact_id") or finding.get("repair_artifact_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _finding_text(finding: dict[str, Any]) -> str:
    fields = [
        finding.get("finding_type"),
        finding.get("category"),
        finding.get("message"),
        finding.get("summary"),
        finding.get("description"),
        finding.get("artifact_id"),
        finding.get("repair_artifact_id"),
    ]
    return " ".join(str(item).lower() for item in fields if item)


def _reason(finding: dict[str, Any], fallback: str) -> str:
    for key in ("message", "summary", "description", "reason"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:400]
    return fallback


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
