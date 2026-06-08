"""Workspace feedback issue ingestion and deterministic repair planning."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.feedback.feedback_contract import (
    FEEDBACK_STATE_FILES,
    FEEDBACK_ISSUES_SCHEMA,
    ISSUE_CATEGORIES,
    ISSUE_SEVERITIES,
    REPAIR_PLAN_SCHEMA,
    artifact_ids,
    empty_feedback_issues,
    empty_repair_plan,
    feedback_state_paths,
    load_feedback_issues,
    load_repair_plan,
    stage_allowed_decisions,
    stage_ids,
    validate_feedback_issues_payload,
    validate_feedback_state,
    validate_repair_plan_payload,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    initialize_runtime_state,
    load_artifact_contracts,
    load_stage_specs,
    show_runtime_state,
    utc_now,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


FEEDBACK_EVENT_ACTOR = "cli"


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise RuntimeStateError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeStateError(
            f"Failed to write feedback state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _read_json_or_text(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read feedback input: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not text.strip():
        raise RuntimeStateError(
            f"Feedback input is empty: {path}",
            details={"path": str(path)},
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _workspace_relative(workspace: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _contracts(
    *,
    workspace: Path,
    repo_workdir: str | Path | None,
) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    repo = resolve_repo_workdir(repo_workdir, workspace=workspace)
    return repo, load_stage_specs(repo), load_artifact_contracts(repo)


def _runtime_run_id(
    *,
    workspace: Path,
    repo_workdir: str | Path | None,
    runtime: str = "hermes",
) -> str:
    try:
        state = show_runtime_state(workspace=workspace)
    except RuntimeStateError:
        state = initialize_runtime_state(
            workspace=workspace,
            runtime=runtime,
            repo_workdir=repo_workdir,
            actor=FEEDBACK_EVENT_ACTOR,
        )
    return str((state.get("manifest") or {}).get("run_id") or "")


def _validate_explicit_refs(
    *,
    stage_id: str | None,
    artifact_id: str | None,
    category: str | None,
    severity: str | None,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    known_stages = stage_ids(stages)
    known_artifacts = artifact_ids(artifacts)
    if stage_id is not None and stage_id not in known_stages:
        raise RuntimeStateError(
            f"Unknown feedback stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": sorted(known_stages)},
        )
    if artifact_id is not None and artifact_id not in known_artifacts:
        raise RuntimeStateError(
            f"Unknown feedback artifact: {artifact_id}",
            details={"artifact_id": artifact_id, "known_artifacts": sorted(known_artifacts)},
        )
    if category is not None and category not in ISSUE_CATEGORIES:
        raise RuntimeStateError(
            f"Unknown feedback category: {category}",
            details={"category": category, "known_categories": sorted(ISSUE_CATEGORIES)},
        )
    if severity is not None and severity not in ISSUE_SEVERITIES:
        raise RuntimeStateError(
            f"Unknown feedback severity: {severity}",
            details={"severity": severity, "known_severities": sorted(ISSUE_SEVERITIES)},
        )


def _summary_from_text(text: str) -> str:
    for line in text.splitlines():
        clean = " ".join(line.strip().split())
        if clean:
            return clean[:240]
    return "Feedback issue"


def _excerpt(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(text.strip().split())[:1000]


def _normalize_severity(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    mapping = {
        "block": "blocking",
        "blocked": "blocking",
        "critical": "blocking",
        "p0": "blocking",
        "p1": "high",
        "warning": "medium",
        "warn": "medium",
        "info": "low",
    }
    text = mapping.get(text, text)
    return text if text in ISSUE_SEVERITIES else None


def _stable_hash(parts: list[str]) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _issue_fingerprint(issue: dict[str, Any]) -> str:
    return _stable_hash([
        str(issue.get("source") or ""),
        str((issue.get("metadata") or {}).get("source_finding_id") or ""),
        str(issue.get("raw_feedback_ref") or ""),
        str(issue.get("stage_id") or ""),
        str(issue.get("artifact_id") or ""),
        " ".join(str(issue.get("summary") or "").lower().split()),
    ])


def _new_issue_id(fingerprint: str) -> str:
    return f"fb_{fingerprint}"


def _issue_status(
    *,
    stage_id: str | None,
    artifact_id: str | None,
    category: str | None,
) -> str:
    if stage_id and artifact_id and category:
        return "open"
    return "triage"


def _human_issue(
    *,
    workspace: Path,
    feedback_path: Path,
    text: str,
    stage_id: str | None,
    artifact_id: str | None,
    category: str | None,
    severity: str | None,
    now: str,
) -> dict[str, Any]:
    issue = {
        "issue_id": "",
        "source": "human",
        "severity": severity or "medium",
        "stage_id": stage_id,
        "artifact_id": artifact_id,
        "category": category,
        "summary": _summary_from_text(text),
        "feedback_excerpt": _excerpt(text),
        "raw_feedback_ref": _workspace_relative(workspace, feedback_path),
        "source_artifact": _workspace_relative(workspace, feedback_path),
        "supporting_context": [],
        "metadata": {},
        "status": _issue_status(stage_id=stage_id, artifact_id=artifact_id, category=category),
        "created_at": now,
        "updated_at": now,
        "fingerprint": "",
    }
    issue["fingerprint"] = _issue_fingerprint(issue)
    issue["issue_id"] = _new_issue_id(str(issue["fingerprint"]))
    return issue


def _extract_audit_findings(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in (
        "findings",
        "issues",
        "audit_findings",
        "blocking_findings",
        "violations",
        "results",
    ):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _first_text(finding: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _valid_or_none(value: Any, allowed: set[str]) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text in allowed else None


def _audit_issue(
    *,
    workspace: Path,
    feedback_path: Path,
    finding: dict[str, Any],
    index: int,
    explicit_stage_id: str | None,
    explicit_artifact_id: str | None,
    explicit_category: str | None,
    explicit_severity: str | None,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    known_stages = stage_ids(stages)
    known_artifacts = artifact_ids(artifacts)
    source_finding_id = (
        finding.get("finding_id")
        or finding.get("id")
        or finding.get("rule_id")
        or finding.get("claim_id")
        or f"finding_{index}"
    )
    blocking_level = finding.get("blocking_level") or finding.get("severity") or finding.get("level")
    repair_owner = finding.get("repair_owner") or finding.get("owner")
    finding_type = finding.get("finding_type") or finding.get("category") or finding.get("type") or finding.get("rule_id")

    stage_id = explicit_stage_id
    if stage_id is None:
        stage_id = _valid_or_none(finding.get("stage_id"), known_stages)
    if stage_id is None:
        stage_id = _valid_or_none(repair_owner, known_stages)

    artifact_id = explicit_artifact_id
    if artifact_id is None:
        artifact_id = _valid_or_none(finding.get("artifact_id"), known_artifacts)

    category = explicit_category
    if category is None:
        category = _valid_or_none(finding_type, ISSUE_CATEGORIES)

    severity = explicit_severity or _normalize_severity(blocking_level) or _normalize_severity(finding.get("severity")) or "medium"
    summary = _first_text(finding, ("summary", "message", "description", "finding", "title")) or _excerpt(finding)
    raw_ref = f"{_workspace_relative(workspace, feedback_path)}#finding:{source_finding_id}"

    issue = {
        "issue_id": "",
        "source": "audit",
        "severity": severity,
        "stage_id": stage_id,
        "artifact_id": artifact_id,
        "category": category,
        "summary": summary[:240],
        "feedback_excerpt": _excerpt(summary),
        "raw_feedback_ref": raw_ref,
        "source_artifact": _workspace_relative(workspace, feedback_path),
        "supporting_context": [],
        "metadata": {
            "source_finding_id": source_finding_id,
            "blocking_level": blocking_level,
            "repair_owner": repair_owner,
            "finding_type": finding_type,
            "raw_finding": finding,
        },
        "status": _issue_status(stage_id=stage_id, artifact_id=artifact_id, category=category),
        "created_at": now,
        "updated_at": now,
        "fingerprint": "",
    }
    issue["fingerprint"] = _issue_fingerprint(issue)
    issue["issue_id"] = _new_issue_id(str(issue["fingerprint"]))
    return issue


def _load_existing_issues(workspace: Path, now: str) -> dict[str, Any]:
    existing = load_feedback_issues(workspace)
    if existing is None:
        return empty_feedback_issues(updated_at=now)
    return existing


def ingest_feedback(
    *,
    workspace: str | Path,
    feedback_path: str | Path,
    source: str,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    repo_workdir: str | Path | None = None,
    actor: str = FEEDBACK_EVENT_ACTOR,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    path = Path(feedback_path).expanduser().resolve()
    if source not in {"human", "audit"}:
        raise RuntimeStateError(
            f"Unknown feedback source: {source}",
            details={"source": source, "known_sources": ["audit", "human"]},
        )
    if not path.exists():
        raise RuntimeStateError(
            f"Feedback input not found: {path}",
            details={"path": str(path)},
        )

    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    _validate_explicit_refs(
        stage_id=stage_id,
        artifact_id=artifact_id,
        category=category,
        severity=severity,
        stages=stages,
        artifacts=artifacts,
    )

    raw = _read_json_or_text(path)
    now = utc_now()
    if source == "human":
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, sort_keys=True)
        candidate_issues = [
            _human_issue(
                workspace=ws,
                feedback_path=path,
                text=text,
                stage_id=stage_id,
                artifact_id=artifact_id,
                category=category,
                severity=severity,
                now=now,
            )
        ]
    else:
        findings = _extract_audit_findings(raw)
        candidate_issues = [
            _audit_issue(
                workspace=ws,
                feedback_path=path,
                finding=finding,
                index=idx,
                explicit_stage_id=stage_id,
                explicit_artifact_id=artifact_id,
                explicit_category=category,
                explicit_severity=severity,
                stages=stages,
                artifacts=artifacts,
                now=now,
            )
            for idx, finding in enumerate(findings)
        ]

    existing = _load_existing_issues(ws, now)
    existing_issues = [issue for issue in existing.get("issues") or [] if isinstance(issue, dict)]
    fingerprints = {str(issue.get("fingerprint")) for issue in existing_issues if issue.get("fingerprint")}
    new_issues = [
        issue for issue in candidate_issues if str(issue.get("fingerprint")) not in fingerprints
    ]
    payload = dict(existing)
    payload["schema_version"] = FEEDBACK_ISSUES_SCHEMA
    payload.setdefault("created_at", now)
    payload["updated_at"] = now
    payload["issues"] = [*existing_issues, *new_issues]

    errors = validate_feedback_issues_payload(payload, stages=stages, artifacts=artifacts)
    if errors:
        raise RuntimeStateError(
            "Feedback issues failed contract validation.",
            details={"errors": errors},
        )

    run_id = _runtime_run_id(workspace=ws, repo_workdir=repo_workdir)
    for issue in new_issues:
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="feedback_issue_created",
            actor=actor,
            stage_id=issue.get("stage_id"),
            artifact_id=issue.get("artifact_id"),
            reason=str(issue.get("summary") or ""),
            metadata={
                "issue_id": issue.get("issue_id"),
                "source": issue.get("source"),
                "severity": issue.get("severity"),
                "status": issue.get("status"),
            },
        )

    _write_json_atomic(feedback_state_paths(ws)["feedback_issues"], payload)
    return show_feedback_state(workspace=ws, repo_workdir=repo_workdir)


def _plan_fingerprint(stage_id: str, issue_ids: list[str], artifacts: list[str]) -> str:
    return _stable_hash([stage_id, *sorted(issue_ids), *sorted(artifacts)])


def _new_plan_id(fingerprint: str) -> str:
    return f"rp_{fingerprint}"


def _candidate_decision(stage_id: str, stages: list[dict[str, Any]]) -> tuple[str | None, bool]:
    allowed = stage_allowed_decisions(stages).get(stage_id, set())
    if "delegate_repair" in allowed:
        return "delegate_repair", False
    if "request_human_review" in allowed:
        return "request_human_review", True
    if "block_run" in allowed:
        return "block_run", True
    return None, True


def plan_feedback(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = FEEDBACK_EVENT_ACTOR,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    now = utc_now()
    issues_payload = _load_existing_issues(ws, now)
    errors = validate_feedback_issues_payload(issues_payload, stages=stages, artifacts=artifacts)
    if errors:
        raise RuntimeStateError(
            "Feedback issues failed contract validation.",
            details={"errors": errors},
        )

    open_issues = [
        issue
        for issue in issues_payload.get("issues") or []
        if isinstance(issue, dict) and issue.get("status") == "open"
    ]
    existing_plan_raw = load_repair_plan(ws)
    existing_plan = existing_plan_raw or empty_repair_plan(updated_at=now)
    existing_errors = (
        validate_repair_plan_payload(
            existing_plan,
            issues_payload=issues_payload,
            stages=stages,
            artifacts=artifacts,
        )
        if existing_plan_raw is not None
        else []
    )
    if existing_errors:
        raise RuntimeStateError(
            "Existing repair plan failed contract validation.",
            details={"errors": existing_errors},
        )

    plans = [plan for plan in existing_plan.get("repair_plans") or [] if isinstance(plan, dict)]
    existing_fingerprints = {str(plan.get("fingerprint")) for plan in plans if plan.get("fingerprint")}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for issue in open_issues:
        stage_id = str(issue.get("stage_id") or "")
        if not stage_id:
            continue
        grouped.setdefault(stage_id, []).append(issue)

    created_plans: list[dict[str, Any]] = []
    planned_issue_ids: set[str] = set()
    for stage_id in sorted(grouped):
        stage_issues = grouped[stage_id]
        issue_ids = [str(issue.get("issue_id")) for issue in stage_issues]
        target_artifacts = sorted({
            str(issue.get("artifact_id"))
            for issue in stage_issues
            if issue.get("artifact_id")
        })
        fingerprint = _plan_fingerprint(stage_id, issue_ids, target_artifacts)
        if fingerprint in existing_fingerprints:
            planned_issue_ids.update(issue_ids)
            continue
        decision, requires_human_review = _candidate_decision(stage_id, stages)
        plan = {
            "repair_plan_id": _new_plan_id(fingerprint),
            "created_at": now,
            "updated_at": now,
            "target_stage": stage_id,
            "target_artifacts": target_artifacts,
            "issue_ids": issue_ids,
            "allowed_decision": decision,
            "repair_scope": "minimal",
            "instructions": [
                f"Review {issue.get('artifact_id')}: {issue.get('summary')}"
                for issue in stage_issues
            ],
            "requires_human_review": requires_human_review,
            "status": "planned",
            "fingerprint": fingerprint,
        }
        plans.append(plan)
        created_plans.append(plan)
        planned_issue_ids.update(issue_ids)

    updated_issues: list[dict[str, Any]] = []
    status_changed: list[dict[str, Any]] = []
    for issue in issues_payload.get("issues") or []:
        if isinstance(issue, dict) and str(issue.get("issue_id")) in planned_issue_ids and issue.get("status") == "open":
            updated = dict(issue)
            updated["status"] = "planned"
            updated["updated_at"] = now
            updated_issues.append(updated)
            status_changed.append(updated)
        else:
            updated_issues.append(issue)

    issues_payload = dict(issues_payload)
    issues_payload["updated_at"] = now
    issues_payload["issues"] = updated_issues
    plan_payload = dict(existing_plan)
    plan_payload["schema_version"] = REPAIR_PLAN_SCHEMA
    plan_payload.setdefault("created_at", now)
    plan_payload["updated_at"] = now
    plan_payload["repair_plans"] = plans

    issue_errors = validate_feedback_issues_payload(issues_payload, stages=stages, artifacts=artifacts)
    plan_errors = validate_repair_plan_payload(
        plan_payload,
        issues_payload=issues_payload,
        stages=stages,
        artifacts=artifacts,
    )
    if issue_errors or plan_errors:
        raise RuntimeStateError(
            "Repair planning failed contract validation.",
            details={"feedback_errors": issue_errors, "repair_plan_errors": plan_errors},
        )

    run_id = _runtime_run_id(workspace=ws, repo_workdir=repo_workdir)
    for issue in status_changed:
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="feedback_issue_planned",
            actor=actor,
            stage_id=issue.get("stage_id"),
            artifact_id=issue.get("artifact_id"),
            reason=str(issue.get("summary") or ""),
            metadata={"issue_id": issue.get("issue_id")},
        )
    for plan in created_plans:
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="repair_plan_created",
            actor=actor,
            stage_id=plan.get("target_stage"),
            reason=f"Repair plan created for {len(plan.get('issue_ids') or [])} issue(s).",
            metadata={
                "repair_plan_id": plan.get("repair_plan_id"),
                "issue_ids": plan.get("issue_ids"),
                "allowed_decision": plan.get("allowed_decision"),
            },
        )

    _write_json_atomic(feedback_state_paths(ws)["feedback_issues"], issues_payload)
    if created_plans or plans:
        _write_json_atomic(feedback_state_paths(ws)["repair_plan"], plan_payload)
    return show_feedback_state(workspace=ws, repo_workdir=repo_workdir)


def resolve_feedback(
    *,
    workspace: str | Path,
    issue_id: str,
    repair_plan_id: str,
    reason: str,
    delta_audit: str | Path | None = None,
    repo_workdir: str | Path | None = None,
    actor: str = FEEDBACK_EVENT_ACTOR,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    now = utc_now()
    issues_payload = load_feedback_issues(ws)
    if issues_payload is None:
        raise RuntimeStateError(
            "feedback_issues.json is not initialized.",
            details={"workspace": str(ws)},
        )
    plan_payload = load_repair_plan(ws)
    if plan_payload is None:
        raise RuntimeStateError(
            "repair_plan.json is not initialized.",
            details={"workspace": str(ws)},
        )

    errors = validate_feedback_issues_payload(issues_payload, stages=stages, artifacts=artifacts)
    errors.extend(
        validate_repair_plan_payload(
            plan_payload,
            issues_payload=issues_payload,
            stages=stages,
            artifacts=artifacts,
        )
    )
    if errors:
        raise RuntimeStateError(
            "Feedback state failed contract validation.",
            details={"errors": errors},
        )

    delta_ref: str | None = None
    if delta_audit is not None:
        delta_path = Path(delta_audit).expanduser().resolve()
        if not delta_path.exists():
            raise RuntimeStateError(
                f"Delta audit report not found: {delta_path}",
                details={"path": str(delta_path)},
            )
        _read_json_or_text(delta_path)
        delta_ref = _workspace_relative(ws, delta_path)

    issues = [issue for issue in issues_payload.get("issues") or [] if isinstance(issue, dict)]
    plans = [plan for plan in plan_payload.get("repair_plans") or [] if isinstance(plan, dict)]
    target_issue = next((issue for issue in issues if issue.get("issue_id") == issue_id), None)
    target_plan = next((plan for plan in plans if plan.get("repair_plan_id") == repair_plan_id), None)
    if target_issue is None:
        raise RuntimeStateError(
            f"Unknown feedback issue: {issue_id}",
            details={"issue_id": issue_id},
        )
    if target_plan is None:
        raise RuntimeStateError(
            f"Unknown repair plan: {repair_plan_id}",
            details={"repair_plan_id": repair_plan_id},
        )
    if issue_id not in [str(item) for item in (target_plan.get("issue_ids") or [])]:
        raise RuntimeStateError(
            f"Repair plan '{repair_plan_id}' does not reference issue '{issue_id}'.",
            details={"issue_id": issue_id, "repair_plan_id": repair_plan_id},
        )

    updated_issues: list[dict[str, Any]] = []
    for issue in issues:
        if issue.get("issue_id") == issue_id:
            updated = dict(issue)
            updated["status"] = "resolved"
            updated["updated_at"] = now
            updated["resolution"] = {
                "reason": reason,
                "repair_plan_id": repair_plan_id,
                "delta_audit_report": delta_ref,
                "resolved_at": now,
            }
            updated_issues.append(updated)
        else:
            updated_issues.append(issue)

    updated_plans: list[dict[str, Any]] = []
    for plan in plans:
        if plan.get("repair_plan_id") == repair_plan_id:
            updated = dict(plan)
            updated["status"] = "completed"
            updated["updated_at"] = now
            updated["completed_at"] = now
            updated["completion_reason"] = reason
            if delta_ref is not None:
                updated["delta_audit_report"] = delta_ref
            updated_plans.append(updated)
        else:
            updated_plans.append(plan)

    issues_payload = dict(issues_payload)
    issues_payload["updated_at"] = now
    issues_payload["issues"] = updated_issues
    plan_payload = dict(plan_payload)
    plan_payload["updated_at"] = now
    plan_payload["repair_plans"] = updated_plans

    issue_errors = validate_feedback_issues_payload(issues_payload, stages=stages, artifacts=artifacts)
    plan_errors = validate_repair_plan_payload(
        plan_payload,
        issues_payload=issues_payload,
        stages=stages,
        artifacts=artifacts,
    )
    if issue_errors or plan_errors:
        raise RuntimeStateError(
            "Resolved feedback state failed contract validation.",
            details={"feedback_errors": issue_errors, "repair_plan_errors": plan_errors},
        )

    run_id = _runtime_run_id(workspace=ws, repo_workdir=repo_workdir)
    target_stage = target_plan.get("target_stage") or target_issue.get("stage_id")
    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="feedback_issue_resolved",
        actor=actor,
        stage_id=target_issue.get("stage_id"),
        artifact_id=target_issue.get("artifact_id"),
        reason=reason,
        metadata={
            "issue_id": issue_id,
            "repair_plan_id": repair_plan_id,
            "delta_audit_report": delta_ref,
        },
    )
    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="repair_plan_completed",
        actor=actor,
        stage_id=str(target_stage) if target_stage else None,
        reason=reason,
        metadata={
            "repair_plan_id": repair_plan_id,
            "issue_id": issue_id,
            "delta_audit_report": delta_ref,
        },
    )

    _write_json_atomic(feedback_state_paths(ws)["feedback_issues"], issues_payload)
    _write_json_atomic(feedback_state_paths(ws)["repair_plan"], plan_payload)
    return show_feedback_state(workspace=ws, repo_workdir=repo_workdir)


def show_feedback_state(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    issues_payload = load_feedback_issues(ws)
    plan_payload = load_repair_plan(ws)
    validation = validate_feedback_state(workspace=ws, stages=stages, artifacts=artifacts)
    return {
        "ok": bool(validation.get("ok")),
        "workspace": str(ws),
        "feedback_state_files": dict(FEEDBACK_STATE_FILES),
        "feedback_issues": issues_payload or empty_feedback_issues(updated_at=""),
        "repair_plan": plan_payload,
        "validation": validation,
    }


def validate_feedback_workspace(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    return validate_feedback_state(workspace=ws, stages=stages, artifacts=artifacts)
