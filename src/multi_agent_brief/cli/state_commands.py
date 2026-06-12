"""Runtime state CLI commands for the Orchestrator handoff layer."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    check_runtime_state,
    complete_finalize_transaction,
    complete_stage_transaction,
    initialize_runtime_state,
    record_decision,
    show_runtime_state,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    state_parser = subparsers.add_parser(
        "state",
        help="Inspect and update Orchestrator runtime state.",
    )
    actions = state_parser.add_subparsers(dest="state_action", required=True)

    init_parser = actions.add_parser(
        "init",
        help="Initialize runtime state control files for a workspace.",
    )
    init_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    init_parser.add_argument(
        "--runtime",
        default="hermes",
        help="Runtime name recorded in runtime_manifest.json (default: hermes).",
    )
    init_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    init_parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Archive the old event log and create a new runtime run_id.",
    )

    show_parser = actions.add_parser(
        "show",
        help="Show current runtime state.",
    )
    show_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    check_parser = actions.add_parser(
        "check",
        help="Refresh artifact registry and stage readiness without running stages.",
    )
    check_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    check_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero if the current stage is blocked.",
    )
    check_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    decide_parser = actions.add_parser(
        "decide",
        help="Record an Orchestrator decision event.",
    )
    decide_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    decide_parser.add_argument("--stage", required=True, help="Stage id receiving the decision.")
    decide_parser.add_argument("--decision", required=True, help="Orchestrator decision vocabulary value.")
    decide_parser.add_argument("--reason", required=True, help="Short reason summary.")
    decide_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    decide_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    decide_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    stage_complete_parser = actions.add_parser(
        "stage-complete",
        help="Validate and record a successful current-stage completion transaction.",
    )
    stage_complete_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    stage_complete_parser.add_argument("--stage", required=True, help="Current non-finalize stage id to complete.")
    stage_complete_parser.add_argument("--reason", required=True, help="Short completion reason summary.")
    stage_complete_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    stage_complete_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    stage_complete_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    finalize_complete_parser = actions.add_parser(
        "finalize-complete",
        help="Validate reader-final artifacts and record finalize completion.",
    )
    finalize_complete_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    finalize_complete_parser.add_argument("--reason", required=True, help="Short completion reason summary.")
    finalize_complete_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    finalize_complete_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    finalize_complete_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    try:
        if args.state_action == "init":
            state = initialize_runtime_state(
                workspace=args.workspace,
                runtime=args.runtime,
                repo_workdir=getattr(args, "repo_workdir", None),
                reset_state=getattr(args, "reset_state", False),
            )
            _print_human_summary("state init", state)
            return 0

        if args.state_action == "show":
            state = show_runtime_state(workspace=args.workspace)
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state show", state)
            return 0

        if args.state_action == "check":
            state = check_runtime_state(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state check", state)
            return 1 if args.strict and _is_blocked(state) else 0

        if args.state_action == "decide":
            state = record_decision(
                workspace=args.workspace,
                stage_id=args.stage,
                decision=args.decision,
                reason=args.reason,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=args.actor,
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state decide", state)
            return 0

        if args.state_action == "stage-complete":
            state = complete_stage_transaction(
                workspace=args.workspace,
                stage_id=args.stage,
                reason=args.reason,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=args.actor,
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state stage-complete", state)
            return 0

        if args.state_action == "finalize-complete":
            state = complete_finalize_transaction(
                workspace=args.workspace,
                reason=args.reason,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=args.actor,
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state finalize-complete", state)
            return 0
    except RuntimeStateError as exc:
        _print_error(exc, as_json=getattr(args, "json", False))
        return 1

    return 1


def _is_blocked(state: dict[str, Any]) -> bool:
    workflow = state.get("workflow_state") or {}
    return bool(workflow.get("blocked"))


def _print_human_summary(label: str, state: dict[str, Any]) -> None:
    manifest = state.get("manifest") or {}
    workflow = state.get("workflow_state") or {}
    print(f"[{label}] run_id: {manifest.get('run_id', '')}")
    print(f"[{label}] current_stage: {workflow.get('current_stage')}")
    print(f"[{label}] blocked: {workflow.get('blocked')}")
    if workflow.get("blocking_reason"):
        print(f"[{label}] reason: {workflow.get('blocking_reason')}")
    print(f"[{label}] runtime_state_files:")
    for key, rel_path in (state.get("runtime_state_files") or {}).items():
        print(f"  - {key}: {rel_path}")


def _print_error(exc: RuntimeStateError, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[state] {exc}")
