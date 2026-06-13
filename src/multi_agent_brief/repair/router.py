"""Deterministic repair routing for blocked or failed workspaces.

The router is intentionally read-only. It does not create repair plans, mutate
artifacts, call agents, or execute repair. It only maps known finding shapes to
the stage/artifact owner that may repair them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.runtime_state import runtime_state_paths
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
    "analyst": "auditor",
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
        "output/intermediate/claim_ledger.json",
        "output/intermediate/audit_report.json",
    ],
    "editor": [
        "output/intermediate/claim_ledger.json",
        "output/intermediate/audit_report.json",
    ],
}


def route_repair(*, workspace: str | Path) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        return {
            "ok": False,
            "error_code": "E_REPAIR_WORKSPACE_MISSING",
            "message": f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            "workspace": str(ws),
        }

    findings, input_errors = _collect_findings(ws)
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
    selected = routes[0] if routes else _no_route()
    return {
        "ok": True,
        "workspace": str(ws),
        **selected,
        "routes": routes,
        "finding_count": len(findings),
    }


def _collect_findings(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    findings: list[dict[str, Any]] = []
    input_errors: list[dict[str, str]] = []
    payloads: dict[str, dict[str, Any]] = {}
    for label, path in _input_paths(workspace).items():
        payload, error = _read_json_object(path)
        if error:
            input_errors.append({"source": label, "path": _workspace_relative(workspace, path), "error": error})
            continue
        if payload is None:
            continue
        payloads[label] = payload
        findings.extend(_findings_from_payload(payload, source=label, path=_workspace_relative(workspace, path)))
    registry = payloads.get("artifact_registry")
    findings.extend(_findings_from_artifact_registry(registry))
    return _dedupe_findings(findings), input_errors


def _input_paths(workspace: Path) -> dict[str, Path]:
    gate_paths = quality_gate_paths(workspace)
    runtime_paths = runtime_state_paths(workspace)
    paths = {
        "auditor_quality_gate_report": gate_paths["auditor_quality_gate_report"],
        "finalize_quality_gate_report": gate_paths["finalize_quality_gate_report"],
        "audit_report": workspace / INTERMEDIATE_DIR / "audit_report.json",
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
    normalized: list[dict[str, Any]] = []
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        item = dict(finding)
        item.setdefault("_source", source)
        item.setdefault("_source_path", path)
        item.setdefault("_source_index", idx)
        normalized.append(item)
    return normalized


def _findings_from_artifact_registry(registry: dict[str, Any] | None) -> list[dict[str, Any]]:
    artifacts = registry.get("artifacts") if isinstance(registry, dict) else None
    if not isinstance(artifacts, dict):
        return []
    findings: list[dict[str, Any]] = []
    for artifact_id, record in artifacts.items():
        if not isinstance(record, dict) or record.get("status") != "invalid":
            continue
        findings.append({
            "_source": "artifact_registry",
            "_source_path": "output/intermediate/artifact_registry.json",
            "finding_type": str(record.get("validation_result") or "artifact_invalid"),
            "artifact_id": artifact_id,
            "message": str(record.get("blocking_reason") or f"Artifact {artifact_id} is invalid."),
        })
    return findings


def _route_for_finding(finding: dict[str, Any]) -> dict[str, Any] | None:
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
            repair_owner="analyst",
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
        "source": {
            "file": source.get("_source_path"),
            "kind": source.get("_source"),
            "finding_id": source.get("finding_id") or source.get("id"),
            "finding_type": source.get("finding_type") or source.get("category"),
            "artifact_id": source.get("artifact_id"),
        },
    }


def _no_route() -> dict[str, Any]:
    return {
        "repair_owner": "none",
        "allowed_artifacts": [],
        "must_rerun_from": "",
        "blocked_direct_edits": [],
        "reason": "No deterministic repair route found.",
        "source": {},
    }


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
