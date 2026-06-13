"""Experiment harness CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.experiments import validate_case_dir


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "experiments",
        help="Validate experimental harness metadata without running workflow stages.",
    )
    experiments_sub = parser.add_subparsers(dest="experiments_action", required=True)

    exp080 = experiments_sub.add_parser(
        "080",
        help="MABW-080 approved-guidance manifestation experiment tools.",
    )
    exp080_sub = exp080.add_subparsers(dest="experiment_080_action", required=True)

    validate = exp080_sub.add_parser(
        "validate-case",
        help="Read-only validation for an MABW-080 case directory.",
    )
    validate.add_argument("case_dir", help="Path to experiments/080/cases/<case_id>.")
    validate.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    if args.experiments_action != "080":
        return 1
    if args.experiment_080_action != "validate-case":
        return 1
    payload = validate_case_dir(args.case_dir)
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_validate_case(payload)
    return 0 if payload.get("ok") else 1


def _print_validate_case(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 validate-case] ok: {payload.get('ok')}")
    if payload.get("case_id"):
        print(f"[experiments 080 validate-case] case_id: {payload.get('case_id')}")
    conditions = payload.get("conditions") or []
    if conditions:
        print(f"[experiments 080 validate-case] conditions: {', '.join(conditions)}")
    for error in payload.get("errors") or []:
        location = f" ({error.get('path')})" if error.get("path") else ""
        print(f"  - {error.get('code')}: {error.get('message')}{location}")
    for warning in payload.get("warnings") or []:
        location = f" ({warning.get('path')})" if warning.get("path") else ""
        print(f"  - warning {warning.get('code')}: {warning.get('message')}{location}")
