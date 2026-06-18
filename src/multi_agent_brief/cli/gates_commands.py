"""Quality-gate CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError
from multi_agent_brief.quality_gates.contract import QualityGateContractError
from multi_agent_brief.quality_gates.state import (
    check_quality_gates,
    show_quality_gates,
    validate_quality_gates_workspace,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    gates_parser = subparsers.add_parser(
        "gates",
        help="Run deterministic quality gates and inspect stage-scoped quality gate reports.",
    )
    actions = gates_parser.add_subparsers(dest="gates_action", required=True)

    check_parser = actions.add_parser(
        "check",
        help="Run material-fact, freshness, target-relevance, and editor-new-fact gates.",
    )
    check_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    check_parser.add_argument(
        "--brief",
        help="Brief path. Defaults to output/intermediate/audited_brief.md, or output/brief.md for --stage finalize.",
    )
    check_parser.add_argument("--ledger", help="Claim Ledger path. Defaults to output/intermediate/claim_ledger.json.")
    check_parser.add_argument("--report-date", default="", help="Report date, e.g. 2026-06-08.")
    check_parser.add_argument("--max-source-age-days", type=int, help="Maximum current-source age in days.")
    check_parser.add_argument(
        "--stage",
        help="Gate stage id. Defaults to auditor for audited_brief.md and finalize for output/brief.md.",
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Escalate high-severity freshness/material/editor-new-fact warnings into blocking findings.",
    )
    check_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    check_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show_parser = actions.add_parser(
        "show",
        help="Show quality gate report state, including stage-scoped reports and legacy projection.",
    )
    show_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    show_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    validate_parser = actions.add_parser(
        "validate",
        help="Validate stage-scoped quality gate reports.",
    )
    validate_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    validate_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    validate_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    try:
        if args.gates_action == "check":
            state = check_quality_gates(
                workspace=args.workspace,
                brief=getattr(args, "brief", None),
                ledger=getattr(args, "ledger", None),
                report_date=getattr(args, "report_date", ""),
                max_source_age_days=getattr(args, "max_source_age_days", None),
                stage_id=getattr(args, "stage", None),
                strict=getattr(args, "strict", False),
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            _print_state("gates check", state, as_json=getattr(args, "json", False))
            return 0 if state.get("ok") else 1

        if args.gates_action == "show":
            state = show_quality_gates(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            _print_state("gates show", state, as_json=getattr(args, "json", False))
            return 0 if state.get("ok") else 1

        if args.gates_action == "validate":
            result = validate_quality_gates_workspace(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_validation(result)
            return 0 if result.get("ok") else 1
    except (RuntimeStateError, QualityGateContractError) as exc:
        _print_error(exc, as_json=getattr(args, "json", False))
        return 1

    return 1


def _print_state(label: str, state: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return
    report = state.get("quality_gate_report") or {}
    validation = state.get("validation") or {}
    print(f"[{label}] status: {report.get('status')}")
    print(f"[{label}] findings: {validation.get('finding_count', 0)}")
    print(f"[{label}] blocking: {validation.get('blocking_count', 0)}")
    print(f"[{label}] valid: {validation.get('ok')}")
    for error in validation.get("errors") or []:
        print(f"  - {error}")
    _print_repair_guidance(label, state)


def _print_repair_guidance(label: str, state: dict[str, Any]) -> None:
    required_commands = state.get("required_commands") or []
    if required_commands:
        print(f"[{label}] required_commands:")
        for command in required_commands:
            print(f"  - {command}")
    repair_warnings = state.get("repair_warnings") or []
    if repair_warnings:
        print(f"[{label}] repair_warnings:")
        for warning in repair_warnings:
            print(f"  - {warning}")
    repair_route = state.get("repair_route")
    if not isinstance(repair_route, dict):
        return
    if repair_route.get("ok"):
        print(f"[{label}] repair_owner: {repair_route.get('repair_owner')}")
        print(f"[{label}] must_rerun_from: {repair_route.get('must_rerun_from') or 'none'}")
        allowed = repair_route.get("allowed_artifacts") or []
        if allowed:
            print(f"[{label}] allowed_artifacts:")
            for artifact in allowed:
                print(f"  - {artifact}")
        blocked = repair_route.get("blocked_direct_edits") or []
        if blocked:
            print(f"[{label}] blocked_direct_edits:")
            for artifact in blocked:
                print(f"  - {artifact}")
    else:
        message = repair_route.get("message") or repair_route.get("error")
        if message:
            print(f"[{label}] repair_route_error: {message}")


def _print_validation(result: dict[str, Any]) -> None:
    print(f"[gates validate] ok: {result.get('ok')}")
    print(f"[gates validate] report_present: {result.get('report_present')}")
    print(f"[gates validate] finding_count: {result.get('finding_count', 0)}")
    print(f"[gates validate] blocking_count: {result.get('blocking_count', 0)}")
    for error in result.get("errors") or []:
        print(f"  - {error}")


def _print_error(exc: Exception, *, as_json: bool) -> None:
    payload = exc.to_dict() if hasattr(exc, "to_dict") else {"ok": False, "error": str(exc)}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[gates] {exc}")
