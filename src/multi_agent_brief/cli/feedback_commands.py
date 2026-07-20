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
