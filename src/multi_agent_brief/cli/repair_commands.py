"""Repair routing CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    complete_repair_transaction,
    start_repair_transaction,
    supersede_stage_artifact_transaction,
)
from multi_agent_brief.repair.router import route_repair


def register(subparsers: argparse._SubParsersAction) -> None:
    repair_parser = subparsers.add_parser(
        "repair",
        help="Route and execute deterministic owner-stage repair transactions.",
    )
    actions = repair_parser.add_subparsers(dest="repair_action", required=True)
    route_parser = actions.add_parser(
        "route",
        help="Show allowed repair owner/artifacts for the current workspace issue.",
    )
    route_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    route_parser.add_argument(
        "--route-index",
        type=int,
        help="0-based route index to inspect explicitly.",
    )
    route_parser.add_argument(
        "--finding-id",
        help="Finding ID to inspect explicitly.",
    )
    route_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    start_parser = actions.add_parser(
        "start",
        help="Start the deterministic owner-stage repair transaction for the current route.",
    )
    start_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    start_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    start_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    start_parser.add_argument(
        "--route-index",
        type=int,
        help="0-based route index from `repair route --json` to start explicitly.",
    )
    start_parser.add_argument(
        "--finding-id",
        help="Finding ID from `repair route --json` to start explicitly.",
    )
    start_parser.add_argument(
        "--gate-stage",
        help="Current quality-gate stage ID for scoped repair start.",
    )
    start_parser.add_argument(
        "--gate-artifact",
        help="Current quality-gate report artifact ID for scoped repair start.",
    )
    start_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    complete_parser = actions.add_parser(
        "complete",
        help="Complete the active owner-stage repair transaction.",
    )
    complete_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    complete_parser.add_argument("--reason", required=True, help="Short repair completion reason summary.")
    complete_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    complete_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    complete_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    supersede_parser = actions.add_parser(
        "supersede-stage",
        help="Record a contaminated owner-stage artifact revision and require downstream rerun.",
    )
    supersede_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    supersede_parser.add_argument("--stage", required=True, help="Owner stage that produced the superseded artifact.")
    supersede_parser.add_argument(
        "--artifact",
        required=True,
        help="Workspace-relative artifact path or artifact id to supersede.",
    )
    supersede_parser.add_argument("--reason", required=True, help="Human/operator reason for the supersede.")
    supersede_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    supersede_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    supersede_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    try:
        if args.repair_action == "route":
            payload = route_repair(
                workspace=args.workspace,
                route_index=getattr(args, "route_index", None),
                finding_id=getattr(args, "finding_id", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_route(payload)
            return 0 if payload.get("ok") else 1
        if args.repair_action == "start":
            payload = start_repair_transaction(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=getattr(args, "actor", "orchestrator"),
                route_index=getattr(args, "route_index", None),
                finding_id=getattr(args, "finding_id", None),
                gate_stage_id=getattr(args, "gate_stage", None),
                gate_artifact_id=getattr(args, "gate_artifact", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_repair_state("repair start", payload)
            return 0
        if args.repair_action == "complete":
            payload = complete_repair_transaction(
                workspace=args.workspace,
                reason=args.reason,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=getattr(args, "actor", "orchestrator"),
            )
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_repair_state("repair complete", payload)
            return 0
        if args.repair_action == "supersede-stage":
            payload = supersede_stage_artifact_transaction(
                workspace=args.workspace,
                stage_id=args.stage,
                artifact=args.artifact,
                reason=args.reason,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=getattr(args, "actor", "orchestrator"),
            )
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_repair_state("repair supersede-stage", payload)
            return 0
    except RuntimeStateError as exc:
        if getattr(args, "json", False):
            print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"[repair] {exc}")
        return 1
    return 1


def _print_route(payload: dict[str, Any]) -> None:
    if not payload.get("ok"):
        print(f"[repair route] Error: {payload.get('message') or payload.get('error')}")
        return
    print(f"[repair route] owner: {payload.get('repair_owner')}")
    print(f"[repair route] must_rerun_from: {payload.get('must_rerun_from') or 'none'}")
    print(f"[repair route] reason: {payload.get('reason')}")
    if payload.get("default_selected") is True:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        finding_id = source.get("finding_id") or source.get("finding_type") or "selected route"
        print(
            f"[repair route] default: {finding_id} -> {payload.get('repair_owner')} "
            f"-> {', '.join(payload.get('allowed_artifacts') or []) or 'none'}"
        )
    print("[repair route] allowed_artifacts:")
    for artifact in payload.get("allowed_artifacts") or []:
        print(f"  - {artifact}")
    blocked = payload.get("blocked_direct_edits") or []
    if blocked:
        print("[repair route] blocked_direct_edits:")
        for artifact in blocked:
            print(f"  - {artifact}")


def _print_repair_state(label: str, payload: dict[str, Any]) -> None:
    workflow = payload.get("workflow_state") or {}
    repair = payload.get("repair") or workflow.get("active_repair") or {}
    print(f"[{label}] current_stage: {workflow.get('current_stage')}")
    print(f"[{label}] repair_owner: {repair.get('repair_owner')}")
    print(f"[{label}] must_rerun_from: {repair.get('must_rerun_from') or repair.get('next_stage') or 'none'}")
    allowed = repair.get("allowed_artifacts") or []
    if allowed:
        print(f"[{label}] allowed_artifacts:")
        for artifact in allowed:
            print(f"  - {artifact}")
    effect = repair.get("run_integrity_effect")
    if isinstance(effect, dict) and effect.get("reference_eligible") is False:
        print(f"[{label}] reference_eligible: false")
        reason = effect.get("reason")
        if reason:
            print(f"[{label}] run_integrity_note: {reason}")
