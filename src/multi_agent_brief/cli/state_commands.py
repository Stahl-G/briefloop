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
    """Fail-closed stub for the retired public CLI surface.

    The parser registration is retained so the authority guard can return
    the typed rejection for workspace invocations; any no-workspace bypass
    lands here instead of executing legacy code.
    """

    print("runtime_command_unsupported")
    return 1

# NOTE: the public command surface of this module is retired. The
# SQLite ControlStore is the sole runtime authority; only the parser
# registration (typed rejections) and the stub below remain.
