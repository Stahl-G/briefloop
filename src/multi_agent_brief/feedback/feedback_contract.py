"""Feedback and repair-plan contracts for the Orchestrator runtime.

This module is intentionally side-effect free.  It validates feedback state and
computes stage-scoped blocking summaries without writing workspace files,
calling agents, or running repair logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FEEDBACK_ISSUES_SCHEMA = "multi-agent-brief-feedback-issues/v1"
REPAIR_PLAN_SCHEMA = "multi-agent-brief-repair-plan/v1"

FEEDBACK_ISSUES_FILE = "output/intermediate/feedback_issues.json"
REPAIR_PLAN_FILE = "output/intermediate/repair_plan.json"
DELTA_AUDIT_REPORT_FILE = "output/intermediate/delta_audit_report.json"

FEEDBACK_STATE_FILES = {
    "feedback_issues": FEEDBACK_ISSUES_FILE,
    "repair_plan": REPAIR_PLAN_FILE,
    "delta_audit_report": DELTA_AUDIT_REPORT_FILE,
}

FEEDBACK_SOURCES = {"human", "audit"}
ISSUE_SEVERITIES = {"low", "medium", "high", "blocking"}
ISSUE_STATUSES = {"triage", "open", "planned", "in_progress", "resolved", "deferred", "blocked"}
UNRESOLVED_BLOCKING_STATUSES = {"open", "planned", "in_progress", "blocked"}
REPAIR_PLAN_STATUSES = {"planned", "in_progress", "completed", "blocked"}

ISSUE_CATEGORIES = {
    "unsupported_claim",
    "missing_source",
    "stale_source",
    "audience_mismatch",
    "formatting",
    "factual_error",
    "citation_error",
    "coverage_gap",
    "clarity",
    "other",
}


class FeedbackContractError(Exception):
    """Raised when feedback or repair-plan files violate the contract."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "details": self.details,
        }


def feedback_state_paths(workspace: str | Path) -> dict[str, Path]:
    ws = Path(workspace).expanduser().resolve()
    return {key: ws / rel_path for key, rel_path in FEEDBACK_STATE_FILES.items()}


def stage_ids(stages: list[dict[str, Any]]) -> set[str]:
    return {str(stage["stage_id"]) for stage in stages if stage.get("stage_id")}


def stage_allowed_decisions(stages: list[dict[str, Any]]) -> dict[str, set[str]]:
    return {
        str(stage["stage_id"]): {str(item) for item in (stage.get("allowed_decisions") or [])}
        for stage in stages
        if stage.get("stage_id")
    }


def artifact_ids(artifacts: list[dict[str, Any]]) -> set[str]:
    return {
        str(artifact["artifact_id"])
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeedbackContractError(
            f"Invalid JSON feedback file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise FeedbackContractError(
            f"Failed to read feedback file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise FeedbackContractError(
            f"Feedback file must contain a JSON object: {path}",
            details={"path": str(path)},
        )
    return data


def load_feedback_issues(workspace: str | Path) -> dict[str, Any] | None:
    return _read_json_object(feedback_state_paths(workspace)["feedback_issues"])


def load_repair_plan(workspace: str | Path) -> dict[str, Any] | None:
    return _read_json_object(feedback_state_paths(workspace)["repair_plan"])


def empty_feedback_issues(*, updated_at: str) -> dict[str, Any]:
    return {
        "schema_version": FEEDBACK_ISSUES_SCHEMA,
        "created_at": updated_at,
        "updated_at": updated_at,
        "issues": [],
    }


def empty_repair_plan(*, updated_at: str) -> dict[str, Any]:
    return {
        "schema_version": REPAIR_PLAN_SCHEMA,
        "created_at": updated_at,
        "updated_at": updated_at,
        "repair_plans": [],
    }


def validate_feedback_issues_payload(
    payload: dict[str, Any],
    *,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != FEEDBACK_ISSUES_SCHEMA:
        errors.append("feedback_issues.json has an unsupported schema_version.")
    issues = payload.get("issues")
    if not isinstance(issues, list):
        errors.append("feedback_issues.json issues must be a list.")
        return errors

    known_stages = stage_ids(stages)
    known_artifacts = artifact_ids(artifacts)
    seen_ids: set[str] = set()
    for idx, issue in enumerate(issues):
        prefix = f"issues[{idx}]"
        if not isinstance(issue, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        issue_id = str(issue.get("issue_id") or "")
        if not issue_id:
            errors.append(f"{prefix}.issue_id is required.")
        elif issue_id in seen_ids:
            errors.append(f"{prefix}.issue_id is duplicated: {issue_id}.")
        seen_ids.add(issue_id)

        source = issue.get("source")
        if source not in FEEDBACK_SOURCES:
            errors.append(f"{prefix}.source must be one of {sorted(FEEDBACK_SOURCES)}.")

        status = issue.get("status")
        if status not in ISSUE_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(ISSUE_STATUSES)}.")

        severity = issue.get("severity")
        if severity not in ISSUE_SEVERITIES:
            errors.append(f"{prefix}.severity must be one of {sorted(ISSUE_SEVERITIES)}.")

        stage_id = issue.get("stage_id")
        if stage_id is not None and str(stage_id) not in known_stages:
            errors.append(f"{prefix}.stage_id is unknown: {stage_id}.")

        artifact_id = issue.get("artifact_id")
        if artifact_id is not None and str(artifact_id) not in known_artifacts:
            errors.append(f"{prefix}.artifact_id is unknown: {artifact_id}.")

        category = issue.get("category")
        if category is not None and str(category) not in ISSUE_CATEGORIES:
            errors.append(f"{prefix}.category is unknown: {category}.")

        if status != "triage":
            if stage_id is None:
                errors.append(f"{prefix}.stage_id is required unless status is triage.")
            if artifact_id is None:
                errors.append(f"{prefix}.artifact_id is required unless status is triage.")
            if category is None:
                errors.append(f"{prefix}.category is required unless status is triage.")
    return errors


def validate_repair_plan_payload(
    payload: dict[str, Any],
    *,
    issues_payload: dict[str, Any],
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != REPAIR_PLAN_SCHEMA:
        errors.append("repair_plan.json has an unsupported schema_version.")
    plans = payload.get("repair_plans")
    if not isinstance(plans, list):
        errors.append("repair_plan.json repair_plans must be a list.")
        return errors

    issues = issues_payload.get("issues") or []
    issue_ids = {
        str(issue.get("issue_id"))
        for issue in issues
        if isinstance(issue, dict) and issue.get("issue_id")
    }
    known_stages = stage_ids(stages)
    known_artifacts = artifact_ids(artifacts)
    allowed_by_stage = stage_allowed_decisions(stages)

    seen_plan_ids: set[str] = set()
    for idx, plan in enumerate(plans):
        prefix = f"repair_plans[{idx}]"
        if not isinstance(plan, dict):
            errors.append(f"{prefix} must be an object.")
            continue

        plan_id = str(plan.get("repair_plan_id") or "")
        if not plan_id:
            errors.append(f"{prefix}.repair_plan_id is required.")
        elif plan_id in seen_plan_ids:
            errors.append(f"{prefix}.repair_plan_id is duplicated: {plan_id}.")
        seen_plan_ids.add(plan_id)

        status = plan.get("status")
        if status not in REPAIR_PLAN_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(REPAIR_PLAN_STATUSES)}.")

        target_stage = plan.get("target_stage")
        if target_stage not in known_stages:
            errors.append(f"{prefix}.target_stage is unknown: {target_stage}.")

        target_artifacts = plan.get("target_artifacts")
        if not isinstance(target_artifacts, list):
            errors.append(f"{prefix}.target_artifacts must be a list.")
        else:
            for artifact_id in target_artifacts:
                if str(artifact_id) not in known_artifacts:
                    errors.append(f"{prefix}.target_artifacts contains unknown artifact: {artifact_id}.")

        refs = plan.get("issue_ids")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{prefix}.issue_ids must be a non-empty list.")
        elif any(str(issue_id) not in issue_ids for issue_id in refs):
            missing = [str(issue_id) for issue_id in refs if str(issue_id) not in issue_ids]
            errors.append(f"{prefix}.issue_ids references missing issues: {missing}.")

        decision = plan.get("allowed_decision")
        if decision is not None:
            allowed = allowed_by_stage.get(str(target_stage), set())
            if str(decision) not in allowed:
                errors.append(
                    f"{prefix}.allowed_decision '{decision}' is not allowed for stage '{target_stage}'."
                )
    return errors


def validate_feedback_state(
    *,
    workspace: str | Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        issues_payload = load_feedback_issues(workspace)
    except FeedbackContractError as exc:
        return {"ok": False, "errors": [str(exc)], "details": exc.details}
    if issues_payload is None:
        issues_payload = empty_feedback_issues(updated_at="")
    errors.extend(
        validate_feedback_issues_payload(
            issues_payload,
            stages=stages,
            artifacts=artifacts,
        )
    )

    try:
        plan_payload = load_repair_plan(workspace)
    except FeedbackContractError as exc:
        return {"ok": False, "errors": [str(exc)], "details": exc.details}
    if plan_payload is not None:
        errors.extend(
            validate_repair_plan_payload(
                plan_payload,
                issues_payload=issues_payload,
                stages=stages,
                artifacts=artifacts,
            )
        )

    return {
        "ok": not errors,
        "errors": errors,
        "issue_count": len(issues_payload.get("issues") or []),
        "triage_count": _triage_count(issues_payload),
        "blocking_triage_count": _blocking_triage_count(issues_payload),
        "repair_plan_count": len((plan_payload or {}).get("repair_plans") or []),
    }


def _triage_count(issues_payload: dict[str, Any]) -> int:
    return sum(
        1
        for issue in issues_payload.get("issues") or []
        if isinstance(issue, dict) and issue.get("status") == "triage"
    )


def _blocking_triage_count(issues_payload: dict[str, Any]) -> int:
    return sum(
        1
        for issue in issues_payload.get("issues") or []
        if isinstance(issue, dict)
        and issue.get("status") == "triage"
        and issue.get("severity") == "blocking"
    )


def unresolved_blocking_issues_for_stage(
    *,
    workspace: str | Path,
    current_stage: str | None,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    if current_stage is None:
        return [], []

    try:
        issues_payload = load_feedback_issues(workspace)
    except FeedbackContractError as exc:
        return [], [f"Feedback state is invalid: {exc}"]
    if issues_payload is None:
        return [], []

    issue_errors = validate_feedback_issues_payload(
        issues_payload,
        stages=stages,
        artifacts=artifacts,
    )
    if issue_errors:
        return [], [f"Feedback issue state is invalid: {' '.join(issue_errors)}"]

    issues = [
        issue
        for issue in issues_payload.get("issues") or []
        if isinstance(issue, dict)
    ]
    unresolved = [
        issue
        for issue in issues
        if issue.get("stage_id") == current_stage
        and (
            issue.get("status") == "blocked"
            or (
                issue.get("severity") == "blocking"
                and issue.get("status") in UNRESOLVED_BLOCKING_STATUSES
            )
        )
    ]
    return unresolved, []


def current_stage_feedback_blocking_reasons(
    *,
    workspace: str | Path,
    current_stage: str | None,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    """Return feedback-related blocking reasons for the current stage only."""
    current_blocking_issues, errors = unresolved_blocking_issues_for_stage(
        workspace=workspace,
        current_stage=current_stage,
        stages=stages,
        artifacts=artifacts,
    )
    if errors:
        return errors
    if not current_blocking_issues:
        return []

    try:
        plan_payload = load_repair_plan(workspace)
    except FeedbackContractError as exc:
        return [f"Repair plan is invalid for current stage '{current_stage}': {exc}"]

    if plan_payload is None:
        ids = ", ".join(str(issue.get("issue_id")) for issue in current_blocking_issues)
        return [f"Current stage '{current_stage}' has unresolved blocking feedback issues without a repair plan: {ids}."]

    try:
        issues_payload = load_feedback_issues(workspace) or empty_feedback_issues(updated_at="")
    except FeedbackContractError as exc:
        return [f"Feedback state is invalid for current stage '{current_stage}': {exc}"]

    plan_errors = validate_repair_plan_payload(
        plan_payload,
        issues_payload=issues_payload,
        stages=stages,
        artifacts=artifacts,
    )
    if plan_errors:
        related_to_current = False
        for plan in plan_payload.get("repair_plans") or []:
            if isinstance(plan, dict) and plan.get("target_stage") == current_stage:
                related_to_current = True
                break
        if related_to_current:
            return [f"Repair plan for current stage '{current_stage}' is invalid: {' '.join(plan_errors)}"]

    actionable_issue_ids: set[str] = set()
    for plan in plan_payload.get("repair_plans") or []:
        if not isinstance(plan, dict) or plan.get("target_stage") != current_stage:
            continue
        if plan.get("status") not in {"planned", "in_progress"}:
            continue
        actionable_issue_ids.update(str(issue_id) for issue_id in (plan.get("issue_ids") or []))

    without_plan = [
        str(issue.get("issue_id"))
        for issue in current_blocking_issues
        if str(issue.get("issue_id")) not in actionable_issue_ids
    ]
    if without_plan:
        return [
            f"Current stage '{current_stage}' has unresolved blocking feedback issues not covered by a repair plan: {', '.join(without_plan)}."
        ]
    ids = ", ".join(str(issue.get("issue_id")) for issue in current_blocking_issues)
    return [
        f"Current stage '{current_stage}' has unresolved blocking feedback issues: {ids}. Use delegate_repair, request_human_review, or block_run until they are resolved."
    ]


def optional_feedback_artifact_activated(
    *,
    workspace: str | Path,
    artifact_id: str,
) -> bool:
    if artifact_id == "repair_plan":
        try:
            issues_payload = load_feedback_issues(workspace)
        except FeedbackContractError:
            return False
        if not issues_payload:
            return False
        return any(
            isinstance(issue, dict)
            and issue.get("severity") == "blocking"
            and issue.get("status") in UNRESOLVED_BLOCKING_STATUSES
            for issue in issues_payload.get("issues") or []
        )

    if artifact_id == "delta_audit_report":
        try:
            plan_payload = load_repair_plan(workspace)
        except FeedbackContractError:
            return False
        if not plan_payload:
            return False
        return any(
            isinstance(plan, dict) and plan.get("status") == "in_progress"
            for plan in plan_payload.get("repair_plans") or []
        )

    return False
