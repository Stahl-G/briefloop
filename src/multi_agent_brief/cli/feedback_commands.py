"""Feedback issue and repair-plan CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.feedback.feedback_contract import FeedbackContractError
from multi_agent_brief.feedback.feedback_state import (
    ingest_feedback,
    plan_feedback,
    resolve_feedback,
    show_feedback_state,
    validate_feedback_workspace,
)
from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError


def register(subparsers: argparse._SubParsersAction) -> None:
    feedback_parser = subparsers.add_parser(
        "feedback",
        help="Ingest feedback issues and create deterministic repair plans.",
    )
    actions = feedback_parser.add_subparsers(dest="feedback_action", required=True)

    ingest_parser = actions.add_parser(
        "ingest",
        help="Convert human feedback or audit findings into feedback_issues.json.",
    )
    ingest_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    ingest_parser.add_argument("--feedback", required=True, help="Feedback or audit report file path.")
    ingest_parser.add_argument(
        "--source",
        required=True,
        help="Feedback source: human or audit.",
    )
    ingest_parser.add_argument("--stage", help="Mapped stage id. Missing mappings create triage issues.")
    ingest_parser.add_argument("--artifact", help="Mapped artifact id. Missing mappings create triage issues.")
    ingest_parser.add_argument("--category", help="Feedback category. Missing mappings create triage issues.")
    ingest_parser.add_argument("--severity", help="Severity: low, medium, high, or blocking.")
    ingest_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    ingest_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    plan_parser = actions.add_parser(
        "plan",
        help="Create deterministic repair_plan.json from open feedback issues.",
    )
    plan_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    plan_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    plan_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    resolve_parser = actions.add_parser(
        "resolve",
        help="Mark a feedback issue resolved and its repair plan completed.",
    )
    resolve_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    resolve_parser.add_argument("--issue-id", required=True, help="Feedback issue id to resolve.")
    resolve_parser.add_argument("--repair-plan-id", required=True, help="Repair plan id covering the issue.")
    resolve_parser.add_argument("--reason", required=True, help="Resolution reason summary.")
    resolve_parser.add_argument(
        "--delta-audit",
        help="Optional delta audit report path to reference after repair.",
    )
    resolve_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    resolve_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show_parser = actions.add_parser(
        "show",
        help="Show feedback issue and repair-plan state.",
    )
    show_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    show_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    validate_parser = actions.add_parser(
        "validate",
        help="Validate feedback_issues.json and repair_plan.json.",
    )
    validate_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    validate_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    validate_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    try:
        if args.feedback_action == "ingest":
            state = ingest_feedback(
                workspace=args.workspace,
                feedback_path=args.feedback,
                source=args.source,
                stage_id=getattr(args, "stage", None),
                artifact_id=getattr(args, "artifact", None),
                category=getattr(args, "category", None),
                severity=getattr(args, "severity", None),
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            _print_state("feedback ingest", state, as_json=getattr(args, "json", False))
            return 0

        if args.feedback_action == "plan":
            state = plan_feedback(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            _print_state("feedback plan", state, as_json=getattr(args, "json", False))
            return 0

        if args.feedback_action == "resolve":
            state = resolve_feedback(
                workspace=args.workspace,
                issue_id=args.issue_id,
                repair_plan_id=args.repair_plan_id,
                reason=args.reason,
                delta_audit=getattr(args, "delta_audit", None),
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            _print_state("feedback resolve", state, as_json=getattr(args, "json", False))
            return 0

        if args.feedback_action == "show":
            state = show_feedback_state(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            _print_state("feedback show", state, as_json=getattr(args, "json", False))
            return 0 if state.get("ok") else 1

        if args.feedback_action == "validate":
            result = validate_feedback_workspace(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_validation(result)
            return 0 if result.get("ok") else 1
    except (RuntimeStateError, FeedbackContractError) as exc:
        _print_error(exc, as_json=getattr(args, "json", False))
        return 1

    return 1


def _print_state(label: str, state: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return
    issues = (state.get("feedback_issues") or {}).get("issues") or []
    plans = (state.get("repair_plan") or {}).get("repair_plans") or []
    validation = state.get("validation") or {}
    print(f"[{label}] issues: {len(issues)}")
    print(f"[{label}] repair_plans: {len(plans)}")
    print(f"[{label}] valid: {validation.get('ok')}")
    print(f"[{label}] triage: {validation.get('triage_count', 0)}")
    if validation.get("errors"):
        for error in validation.get("errors") or []:
            print(f"  - {error}")


def _print_validation(result: dict[str, Any]) -> None:
    print(f"[feedback validate] ok: {result.get('ok')}")
    print(f"[feedback validate] issue_count: {result.get('issue_count', 0)}")
    print(f"[feedback validate] triage_count: {result.get('triage_count', 0)}")
    print(f"[feedback validate] blocking_triage_count: {result.get('blocking_triage_count', 0)}")
    print(f"[feedback validate] repair_plan_count: {result.get('repair_plan_count', 0)}")
    for error in result.get("errors") or []:
        print(f"  - {error}")


def _print_error(exc: Exception, *, as_json: bool) -> None:
    payload = exc.to_dict() if hasattr(exc, "to_dict") else {"ok": False, "error": str(exc)}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[feedback] {exc}")
