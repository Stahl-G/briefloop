"""Improvement Ledger CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.improvement.contract import (
    ALLOWED_STATUSES,
    AUDIENCE_GUIDANCE_CATEGORIES,
    AUDIENCE_GUIDANCE_SCOPES,
)
from multi_agent_brief.improvement.memory import rebuild_improvement_memory
from multi_agent_brief.improvement.state import (
    ImprovementLedgerError,
    approve_improvement,
    improvement_stats,
    list_improvements,
    propose_improvement,
    reject_improvement,
    revert_improvement,
    show_improvement,
    validate_improvement_ledger,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    improve_parser = subparsers.add_parser(
        "improve",
        help="Manage the append-only Improvement Ledger.",
    )
    actions = improve_parser.add_subparsers(dest="improve_action", required=True)

    propose_parser = actions.add_parser(
        "propose",
        help="Append a proposed audience-guidance improvement.",
    )
    _add_workspace(propose_parser)
    propose_parser.add_argument("--guidance", required=True, help="Bounded audience guidance text.")
    propose_parser.add_argument("--category", required=True, choices=sorted(AUDIENCE_GUIDANCE_CATEGORIES))
    propose_parser.add_argument("--scope", required=True, choices=sorted(AUDIENCE_GUIDANCE_SCOPES))
    propose_parser.add_argument("--source-summary", help="Required for explicit human proposals.")
    propose_parser.add_argument("--from-issue", help="Feedback issue id to freeze as source evidence.")
    propose_parser.add_argument("--supersedes", help="Approved materializable entry id replaced by this proposal.")
    _add_json(propose_parser)

    list_parser = actions.add_parser(
        "list",
        help="List current Improvement Ledger entries.",
    )
    _add_workspace(list_parser)
    list_parser.add_argument("--status", choices=sorted(ALLOWED_STATUSES), help="Filter by current status.")
    _add_json(list_parser)

    show_parser = actions.add_parser(
        "show",
        help="Show one Improvement Ledger entry and its revisions.",
    )
    _add_workspace(show_parser)
    show_parser.add_argument("--entry-id", required=True, help="Improvement entry id, such as AG-0001.")
    _add_json(show_parser)

    approve_parser = actions.add_parser(
        "approve",
        help="Approve a proposed Improvement Ledger entry without applying it.",
    )
    _add_workspace(approve_parser)
    approve_parser.add_argument("--entry-id", required=True, help="Improvement entry id.")
    approve_parser.add_argument("--by", required=True, help="Operator id approving this entry.")
    _add_json(approve_parser)

    reject_parser = actions.add_parser(
        "reject",
        help="Reject a proposed Improvement Ledger entry.",
    )
    _add_workspace(reject_parser)
    reject_parser.add_argument("--entry-id", required=True, help="Improvement entry id.")
    reject_parser.add_argument("--by", required=True, help="Operator id rejecting this entry.")
    reject_parser.add_argument("--reason", required=True, help="Short rejection reason.")
    _add_json(reject_parser)

    revert_parser = actions.add_parser(
        "revert",
        help="Revert an approved Improvement Ledger entry.",
    )
    _add_workspace(revert_parser)
    revert_parser.add_argument("--entry-id", required=True, help="Improvement entry id.")
    revert_parser.add_argument("--by", required=True, help="Operator id reverting this entry.")
    revert_parser.add_argument("--reason", required=True, help="Short revert reason.")
    _add_json(revert_parser)

    stats_parser = actions.add_parser(
        "stats",
        help="Summarize ledger-only Improvement counts.",
    )
    _add_workspace(stats_parser)
    _add_json(stats_parser)

    validate_parser = actions.add_parser(
        "validate",
        help="Validate the Improvement Ledger without writing files.",
    )
    _add_workspace(validate_parser)
    _add_json(validate_parser)

    rebuild_parser = actions.add_parser(
        "rebuild",
        help="Rebuild deterministic improvement/memory.md without touching runtime state.",
    )
    _add_workspace(rebuild_parser)
    _add_json(rebuild_parser)


def handle(args: argparse.Namespace) -> int:
    try:
        if args.improve_action == "propose":
            state = propose_improvement(
                workspace=args.workspace,
                guidance=args.guidance,
                category=args.category,
                scope=args.scope,
                source_summary=getattr(args, "source_summary", None),
                from_issue=getattr(args, "from_issue", None),
                supersedes=getattr(args, "supersedes", None),
            )
            _print_state("improve propose", state, as_json=getattr(args, "json", False))
            return 0

        if args.improve_action == "list":
            state = list_improvements(
                workspace=args.workspace,
                status=getattr(args, "status", None),
            )
            _print_state("improve list", state, as_json=getattr(args, "json", False))
            return 0

        if args.improve_action == "show":
            state = show_improvement(workspace=args.workspace, entry_id=args.entry_id)
            _print_state("improve show", state, as_json=getattr(args, "json", False))
            return 0

        if args.improve_action == "approve":
            state = approve_improvement(
                workspace=args.workspace,
                entry_id=args.entry_id,
                approved_by=args.by,
            )
            _print_state("improve approve", state, as_json=getattr(args, "json", False))
            return 0

        if args.improve_action == "reject":
            state = reject_improvement(
                workspace=args.workspace,
                entry_id=args.entry_id,
                rejected_by=args.by,
                reason=args.reason,
            )
            _print_state("improve reject", state, as_json=getattr(args, "json", False))
            return 0

        if args.improve_action == "revert":
            state = revert_improvement(
                workspace=args.workspace,
                entry_id=args.entry_id,
                reverted_by=args.by,
                reason=args.reason,
            )
            _print_state("improve revert", state, as_json=getattr(args, "json", False))
            return 0

        if args.improve_action == "stats":
            state = improvement_stats(workspace=args.workspace)
            _print_state("improve stats", state, as_json=getattr(args, "json", False))
            return 0

        if args.improve_action == "validate":
            state = validate_improvement_ledger(workspace=args.workspace)
            _print_state("improve validate", state, as_json=getattr(args, "json", False))
            return 0 if state.get("ok") else 1

        if args.improve_action == "rebuild":
            state = rebuild_improvement_memory(workspace=args.workspace)
            _print_state("improve rebuild", state, as_json=getattr(args, "json", False))
            return 0
    except ImprovementLedgerError as exc:
        _print_error(exc, as_json=getattr(args, "json", False))
        return 1
    return 1


def _add_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, help="Path to workspace directory.")


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def _print_state(label: str, state: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"[{label}] ok: {state.get('ok')}")
    print(f"[{label}] ledger: {state.get('ledger_path')}")
    if "entry_id" in state:
        print(f"[{label}] entry: {state.get('entry_id')}")
    elif isinstance(state.get("entry"), dict):
        print(f"[{label}] entry: {state['entry'].get('entry_id')}")
    print(f"[{label}] entries: {state.get('entry_count', 0)}")
    for diagnostic in state.get("diagnostics") or []:
        print(f"  - {diagnostic.get('severity')}: {diagnostic.get('message')}")
    for warning in state.get("warnings") or []:
        print(f"  - warning: {warning.get('message')}")
    if state.get("event_recorded") is False:
        print(f"[{label}] event: {state.get('event_reason')}")


def _print_error(exc: Exception, *, as_json: bool) -> None:
    payload = exc.to_dict() if hasattr(exc, "to_dict") else {"ok": False, "error": str(exc)}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[improve] {exc}")
