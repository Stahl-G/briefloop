"""Evaluation-case CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.evaluation_cases.contract import EvaluationCaseContractError
from multi_agent_brief.evaluation_cases.runner import (
    EvaluationCaseRunError,
    list_evaluation_cases,
    run_evaluation_cases,
    validate_evaluation_cases,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "eval-cases",
        help="Run packaged public-safe control-surface evaluation cases.",
    )
    actions = parser.add_subparsers(dest="eval_cases_action", required=True)

    list_parser = actions.add_parser("list", help="List available evaluation cases.")
    list_parser.add_argument("--cases-dir", help="Custom evaluation cases directory.")
    list_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    validate_parser = actions.add_parser("validate", help="Validate evaluation case fixtures.")
    validate_parser.add_argument("--cases-dir", help="Custom evaluation cases directory.")
    validate_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    run_parser = actions.add_parser("run", help="Run evaluation cases.")
    run_parser.add_argument("--cases-dir", help="Custom evaluation cases directory.")
    run_parser.add_argument("--case-id", help="Run one case id.")
    run_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    run_parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help="Copy temporary case workspaces into .mabw-eval-cases for debugging.",
    )
    run_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    try:
        if args.eval_cases_action == "list":
            result = list_evaluation_cases(cases_dir=getattr(args, "cases_dir", None))
            _print_result("eval-cases list", result, as_json=getattr(args, "json", False))
            return 0

        if args.eval_cases_action == "validate":
            result = validate_evaluation_cases(cases_dir=getattr(args, "cases_dir", None))
            _print_result("eval-cases validate", result, as_json=getattr(args, "json", False))
            return 0 if result.get("ok") else 1

        if args.eval_cases_action == "run":
            result = run_evaluation_cases(
                cases_dir=getattr(args, "cases_dir", None),
                case_id=getattr(args, "case_id", None),
                repo_workdir=getattr(args, "repo_workdir", None),
                keep_workspaces=getattr(args, "keep_workspaces", False),
            )
            _print_result("eval-cases run", result, as_json=getattr(args, "json", False))
            return 0 if result.get("ok") else 1
    except (EvaluationCaseContractError, EvaluationCaseRunError, ValueError) as exc:
        payload = exc.to_dict() if hasattr(exc, "to_dict") else {
            "ok": False,
            "error": str(exc),
            "details": getattr(exc, "details", {}),
        }
        _print_result("eval-cases error", payload, as_json=getattr(args, "json", False))
        return 1

    return 1


def _print_result(label: str, result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if "error" in result:
        print(f"[{label}] {result.get('error')}")
        for error in result.get("errors") or (result.get("details") or {}).get("errors") or []:
            print(f"  - {error}")
        return
    print(f"[{label}] ok: {result.get('ok')}")
    if "case_count" in result:
        print(f"[{label}] cases: {result.get('case_count')}")
    if "passed_count" in result:
        print(f"[{label}] passed: {result.get('passed_count')}")
        print(f"[{label}] failed: {result.get('failed_count')}")
    for error in result.get("errors") or []:
        print(f"  - {error}")
    for case in result.get("cases") or []:
        if isinstance(case, dict):
            print(f"  - {case.get('case_id')} ({case.get('case_type')})")
        else:
            print(f"  - {case}")
