"""Runtime state CLI commands for the Orchestrator handoff layer."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES

from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    check_runtime_state,
    complete_finalize_transaction,
    complete_stage_transaction,
    enrich_claim_metadata_transaction,
    freeze_claim_ledger_transaction,
    import_fact_layer_transaction,
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
        required=True,
        choices=list(VALID_RUNTIMES),
        help="Exact runtime identity recorded in runtime_manifest.json.",
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
    stage_complete_parser.add_argument(
        "--runtime",
        help="Runtime that completed the stage, recorded as provenance only.",
    )
    stage_complete_parser.add_argument(
        "--model",
        help="Model used for the stage when known, recorded as provenance only.",
    )
    stage_complete_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    freeze_claim_ledger_parser = actions.add_parser(
        "freeze-claim-ledger",
        help="Freeze claim_drafts.json into deterministic claim_ledger.json.",
    )
    freeze_claim_ledger_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    freeze_claim_ledger_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    freeze_claim_ledger_parser.add_argument(
        "--actor",
        default="cli",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    freeze_claim_ledger_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    enrich_claim_metadata_parser = actions.add_parser(
        "enrich-claim-metadata",
        help="Enrich frozen claim_ledger.json metadata from imported source evidence.",
    )
    enrich_claim_metadata_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    enrich_claim_metadata_parser.add_argument(
        "--from-source-evidence",
        action="store_true",
        required=True,
        help="Derive metadata only from imported frozen source evidence.",
    )
    enrich_claim_metadata_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    enrich_claim_metadata_parser.add_argument(
        "--actor",
        default="cli",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    enrich_claim_metadata_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

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
    finalize_complete_parser.add_argument(
        "--runtime",
        help="Runtime that completed the finalize stage, recorded as provenance only.",
    )
    finalize_complete_parser.add_argument(
        "--model",
        help="Model used for finalize when known, recorded as provenance only.",
    )
    finalize_complete_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    import_fact_layer_parser = actions.add_parser(
        "import-fact-layer",
        help="Import a complete archived frozen fact layer into a new fast-rerun runtime state.",
    )
    import_fact_layer_parser.add_argument("--workspace", required=True, help="Path to target workspace directory.")
    import_fact_layer_parser.add_argument(
        "--archive",
        required=True,
        help="Path to an output/runs/<run_id>/ archive directory or its manifest.json.",
    )
    import_fact_layer_parser.add_argument(
        "--runtime",
        required=True,
        choices=list(VALID_RUNTIMES),
        help="Exact runtime identity recorded in the new runtime_manifest.json.",
    )
    import_fact_layer_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    import_fact_layer_parser.add_argument(
        "--actor",
        default="cli",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    import_fact_layer_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


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
                runtime=getattr(args, "runtime", None),
                model=getattr(args, "model", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state stage-complete", state)
            return 0

        if args.state_action == "freeze-claim-ledger":
            state = freeze_claim_ledger_transaction(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=getattr(args, "actor", "cli"),
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state freeze-claim-ledger", state)
                freeze = state.get("claim_ledger_freeze") or (state.get("manifest") or {}).get("claim_ledger_freeze") or {}
                print(f"[state freeze-claim-ledger] claim_count: {freeze.get('claim_count', 0)}")
                print(f"[state freeze-claim-ledger] claim_ledger_sha256: {freeze.get('claim_ledger_sha256', '')}")
                if freeze.get("warnings"):
                    print(f"[state freeze-claim-ledger] warning_count: {len(freeze.get('warnings') or [])}")
            return 0

        if args.state_action == "enrich-claim-metadata":
            state = enrich_claim_metadata_transaction(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=getattr(args, "actor", "cli"),
                from_source_evidence=getattr(args, "from_source_evidence", False),
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state enrich-claim-metadata", state)
                enrichment = state.get("claim_ledger_metadata_enrichment") or {}
                print(
                    "[state enrich-claim-metadata] enriched_claim_count: "
                    f"{enrichment.get('enriched_claim_count', 0)}"
                )
                print(
                    "[state enrich-claim-metadata] claim_ledger_sha256: "
                    f"{enrichment.get('claim_ledger_sha256', '')}"
                )
            return 0

        if args.state_action == "finalize-complete":
            state = complete_finalize_transaction(
                workspace=args.workspace,
                reason=args.reason,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=args.actor,
                runtime=getattr(args, "runtime", None),
                model=getattr(args, "model", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state finalize-complete", state)
            return 0

        if args.state_action == "import-fact-layer":
            state = import_fact_layer_transaction(
                workspace=args.workspace,
                archive=args.archive,
                runtime=args.runtime,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor=getattr(args, "actor", "cli"),
            )
            if getattr(args, "json", False):
                print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human_summary("state import-fact-layer", state)
                imported = state.get("fact_layer_import") or {}
                print(f"[state import-fact-layer] imported_file_count: {imported.get('imported_file_count', 0)}")
                print(f"[state import-fact-layer] source_run_id: {imported.get('source_run_id', '')}")
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
    fact_layer_import = state.get("fact_layer_import") or {}
    print(f"[{label}] run_id: {manifest.get('run_id', '')}")
    print(f"[{label}] current_stage: {workflow.get('current_stage')}")
    print(f"[{label}] blocked: {workflow.get('blocked')}")
    if workflow.get("blocking_reason"):
        print(f"[{label}] reason: {workflow.get('blocking_reason')}")
    print(f"[{label}] runtime_state_files:")
    for key, rel_path in (state.get("runtime_state_files") or {}).items():
        print(f"  - {key}: {rel_path}")
    _print_fact_layer_import_summary(label, fact_layer_import)


def _print_fact_layer_import_summary(label: str, summary: dict[str, Any]) -> None:
    if not summary:
        return
    if summary.get("status") == "missing":
        return
    print(f"[{label}] fact_layer_import: {summary.get('status')}")
    if summary.get("status") == "valid":
        print(f"[{label}] source_run_id: {summary.get('source_run_id', '')}")
        print(f"[{label}] fact_layer_sha256: {summary.get('fact_layer_sha256', '')}")
        print(f"[{label}] timing_comparability: {summary.get('timing_comparability', '')}")
        print(f"[{label}] imported_satisfied_stages:")
        for stage in summary.get("imported_stages") or []:
            print(f"  - {stage.get('stage_id')}: {stage.get('display_status')}")
        print(f"[{label}] next_runtime_stage: {summary.get('next_stage')}")
    else:
        for reason in summary.get("errors") or []:
            print(f"[{label}] fact_layer_import_error: {reason}")


def _print_error(exc: RuntimeStateError, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[state] {exc}")
        _print_error_details(exc)


def _print_error_details(exc: RuntimeStateError) -> None:
    details = exc.details or {}
    diagnostics = details.get("diagnostics") or []
    if diagnostics:
        print("[state] diagnostics:")
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            field = item.get("field", "<unknown>")
            error = item.get("error", "")
            print(f"  - {field}: {error}")
            allowed_values = item.get("allowed_values")
            if allowed_values:
                print(f"    allowed_values: {', '.join(str(value) for value in allowed_values)}")
            forbidden_fields = item.get("forbidden_fields")
            if forbidden_fields:
                print(f"    forbidden_fields: {', '.join(str(value) for value in forbidden_fields)}")
            required_fields = item.get("required_fields")
            if required_fields:
                print(f"    required_fields: {', '.join(str(value) for value in required_fields)}")
            if item.get("hint"):
                print(f"    hint: {item.get('hint')}")
    required_commands = details.get("required_commands") or []
    if required_commands:
        print("[state] required_commands:")
        for command in required_commands:
            print(f"  - {command}")
    repair_steps = details.get("repair_steps") or []
    if repair_steps:
        print("[state] repair_steps:")
        for step in repair_steps:
            print(f"  - {step}")
    repair_route = details.get("repair_route")
    if not isinstance(repair_route, dict):
        return
    if repair_route.get("ok"):
        print(f"[state] repair_owner: {repair_route.get('repair_owner')}")
        print(f"[state] must_rerun_from: {repair_route.get('must_rerun_from') or 'none'}")
        allowed = repair_route.get("allowed_artifacts") or []
        if allowed:
            print("[state] allowed_artifacts:")
            for artifact in allowed:
                print(f"  - {artifact}")
    else:
        message = repair_route.get("message") or repair_route.get("error")
        if message:
            print(f"[state] repair_route_error: {message}")
