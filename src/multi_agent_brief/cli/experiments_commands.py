"""Experiment harness CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.experiments import (
    Experiment080Error,
    import_assessment,
    register_run_record,
    score_run_record,
    summarize_case,
    validate_case_dir,
)


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

    register_run = exp080_sub.add_parser(
        "register-run",
        help="Register a completed workspace run into an MABW-080 case.",
    )
    register_run.add_argument("--case", required=True, dest="case_dir", help="Path to experiments/080/cases/<case_id>.")
    register_run.add_argument(
        "--condition",
        required=True,
        choices=("baseline", "memory", "prompt_only"),
        help="080 condition for this run.",
    )
    register_run.add_argument("--workspace", required=True, help="Completed MABW workspace to register.")
    register_run.add_argument("--output", required=True, help="Path to write run_record.json.")
    register_run.add_argument(
        "--repo-workdir",
        help="Optional explicit MABW source checkout for git commit provenance. Defaults to case_manifest.repo_commit.",
    )
    register_run.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    score_run = exp080_sub.add_parser(
        "score-run",
        help="Build a deterministic MABW-080 scorecard draft from a registered run.",
    )
    score_run.add_argument("--case", required=True, dest="case_dir", help="Path to experiments/080/cases/<case_id>.")
    score_run.add_argument("--run-record", required=True, help="Path to run_record.json.")
    score_run.add_argument("--output", required=True, help="Path to write scorecard.json.")
    score_run.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    import_assessment_parser = exp080_sub.add_parser(
        "import-assessment",
        help="Import external guidance-manifestation assessment into an MABW-080 scorecard.",
    )
    import_assessment_parser.add_argument("--scorecard", required=True, help="Path to scorecard.json.")
    import_assessment_parser.add_argument("--assessment", required=True, help="Path to assessment.json.")
    import_assessment_parser.add_argument("--output", required=True, help="Path to write assessed scorecard.json.")
    import_assessment_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    summarize = exp080_sub.add_parser(
        "summarize",
        help="Summarize deterministic MABW-080 scorecards for a case.",
    )
    summarize.add_argument("--case", required=True, dest="case_dir", help="Path to experiments/080/cases/<case_id>.")
    summarize.add_argument("--output", help="Optional path to write case_summary.json.")
    summarize.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    if args.experiments_action != "080":
        return 1
    if args.experiment_080_action == "validate-case":
        payload = validate_case_dir(args.case_dir)
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_validate_case(payload)
        return 0 if payload.get("ok") else 1
    if args.experiment_080_action == "score-run":
        try:
            payload = score_run_record(
                case_dir=args.case_dir,
                run_record=args.run_record,
                output=args.output,
            )
        except Experiment080Error as exc:
            payload = exc.to_dict()
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"[experiments 080 score-run] ok: False")
                details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
                code = details.get("code")
                suffix = f" ({code})" if code else ""
                print(f"  - {payload.get('error')}{suffix}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_score_run(payload)
        return 0
    if args.experiment_080_action == "import-assessment":
        try:
            payload = import_assessment(
                scorecard=args.scorecard,
                assessment=args.assessment,
                output=args.output,
            )
        except Experiment080Error as exc:
            payload = exc.to_dict()
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"[experiments 080 import-assessment] ok: False")
                details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
                code = details.get("code")
                suffix = f" ({code})" if code else ""
                print(f"  - {payload.get('error')}{suffix}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_import_assessment(payload)
        return 0
    if args.experiment_080_action == "summarize":
        try:
            payload = summarize_case(
                case_dir=args.case_dir,
                output=args.output,
            )
        except Experiment080Error as exc:
            payload = exc.to_dict()
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"[experiments 080 summarize] ok: False")
                details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
                code = details.get("code")
                suffix = f" ({code})" if code else ""
                print(f"  - {payload.get('error')}{suffix}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_summarize(payload)
        return 0
    if args.experiment_080_action != "register-run":
        return 1
    try:
        payload = register_run_record(
            case_dir=args.case_dir,
            condition=args.condition,
            workspace=args.workspace,
            output=args.output,
            repo_workdir=args.repo_workdir,
        )
    except Experiment080Error as exc:
        payload = exc.to_dict()
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"[experiments 080 register-run] ok: False")
            details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
            code = details.get("code")
            suffix = f" ({code})" if code else ""
            print(f"  - {payload.get('error')}{suffix}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_register_run(payload)
    return 0


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


def _print_register_run(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 register-run] ok: {payload.get('ok')}")
    print(f"[experiments 080 register-run] case_id: {payload.get('case_id')}")
    print(f"[experiments 080 register-run] condition: {payload.get('condition')}")
    print(f"[experiments 080 register-run] run_id: {payload.get('run_id')}")
    print(f"[experiments 080 register-run] output: {payload.get('output')}")


def _print_score_run(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 score-run] ok: {payload.get('ok')}")
    print(f"[experiments 080 score-run] case_id: {payload.get('case_id')}")
    print(f"[experiments 080 score-run] condition: {payload.get('condition')}")
    print(f"[experiments 080 score-run] run_id: {payload.get('run_id')}")
    print(f"[experiments 080 score-run] validity_class: {payload.get('validity_class')}")
    print(f"[experiments 080 score-run] assessment_status: {payload.get('assessment_status')}")
    print(f"[experiments 080 score-run] output: {payload.get('output')}")


def _print_import_assessment(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 import-assessment] ok: {payload.get('ok')}")
    print(f"[experiments 080 import-assessment] case_id: {payload.get('case_id')}")
    print(f"[experiments 080 import-assessment] condition: {payload.get('condition')}")
    print(f"[experiments 080 import-assessment] run_id: {payload.get('run_id')}")
    print(f"[experiments 080 import-assessment] validity_class: {payload.get('validity_class')}")
    print(f"[experiments 080 import-assessment] assessment_status: {payload.get('assessment_status')}")
    print(f"[experiments 080 import-assessment] output: {payload.get('output')}")


def _print_summarize(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 summarize] ok: {payload.get('ok')}")
    print(f"[experiments 080 summarize] case_id: {payload.get('case_id')}")
    print(f"[experiments 080 summarize] scorecard_count: {payload.get('scorecard_count')}")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    run_counts = summary.get("run_counts") if isinstance(summary.get("run_counts"), dict) else {}
    validity_counts = (
        run_counts.get("validity_class_counts")
        if isinstance(run_counts.get("validity_class_counts"), dict)
        else {}
    )
    if validity_counts:
        rendered = ", ".join(f"{key}={validity_counts[key]}" for key in sorted(validity_counts))
        print(f"[experiments 080 summarize] validity: {rendered}")
    manifestation = summary.get("manifestation") if isinstance(summary.get("manifestation"), dict) else {}
    if manifestation:
        print(
            "[experiments 080 summarize] manifestation: "
            f"score_2={manifestation.get('score_2_manifested_count', 0)}, "
            f"score_3_overapplication={manifestation.get('score_3_overapplication_count', 0)}"
        )
    output = payload.get("output")
    if output:
        print(f"[experiments 080 summarize] output: {output}")
