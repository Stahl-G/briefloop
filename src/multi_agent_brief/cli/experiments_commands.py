"""Experiment harness CLI commands."""

from __future__ import annotations

import argparse
import importlib
import json
from typing import Any

from multi_agent_brief.experiments import (
    Experiment080Error,
    export_blind_pack,
    import_assessment,
    register_run_record,
    scaffold_condition,
    score_run_record,
    summarize_case,
    validate_case_dir,
)
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "experiments",
        help="Validate experimental harness metadata without running workflow stages.",
    )
    experiments_sub = parser.add_subparsers(dest="experiments_action", required=True)

    laj = experiments_sub.add_parser(
        "laj",
        help="Experimental offline-shadow Semantic Evaluator tools (not shipped).",
    )
    laj_sub = laj.add_subparsers(dest="experiment_laj_action", required=True)
    shadow_run = laj_sub.add_parser(
        "shadow-run",
        help="Run or exactly replay one public/synthetic advisory shadow trial.",
    )
    shadow_run.add_argument("--report", required=True)
    shadow_run.add_argument("--bounded-context", required=True)
    shadow_run.add_argument("--profile", required=True)
    shadow_run.add_argument("--instrument", required=True)
    shadow_run.add_argument("--trial-id", required=True)
    shadow_run.add_argument("--archive-root", required=True)
    shadow_run.add_argument("--json", action="store_true")

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
    validate.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    register_run = exp080_sub.add_parser(
        "register-run",
        help="Register a completed workspace run into an MABW-080 case.",
    )
    register_run.add_argument(
        "--case",
        required=True,
        dest="case_dir",
        help="Path to experiments/080/cases/<case_id>.",
    )
    register_run.add_argument(
        "--condition",
        required=True,
        choices=("baseline", "memory", "prompt_only"),
        help="080 condition for this run.",
    )
    register_run.add_argument(
        "--workspace", required=True, help="Completed MABW workspace to register."
    )
    register_run.add_argument(
        "--output", required=True, help="Path to write run_record.json."
    )
    register_run.add_argument(
        "--repo-workdir",
        help="Optional explicit MABW source checkout for git commit provenance. Defaults to case_manifest.repo_commit.",
    )
    register_run.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    score_run = exp080_sub.add_parser(
        "score-run",
        help="Build a deterministic MABW-080 scorecard draft from a registered run.",
    )
    score_run.add_argument(
        "--case",
        required=True,
        dest="case_dir",
        help="Path to experiments/080/cases/<case_id>.",
    )
    score_run.add_argument(
        "--run-record", required=True, help="Path to run_record.json."
    )
    score_run.add_argument(
        "--output", required=True, help="Path to write scorecard.json."
    )
    score_run.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    import_assessment_parser = exp080_sub.add_parser(
        "import-assessment",
        help="Import external guidance-manifestation assessment into an MABW-080 scorecard.",
    )
    import_assessment_parser.add_argument("--scorecard", help="Path to scorecard.json.")
    import_assessment_parser.add_argument(
        "--assessment", required=True, help="Path to assessment.json."
    )
    import_assessment_parser.add_argument(
        "--output", required=True, help="Path to write assessed scorecard.json."
    )
    import_assessment_parser.add_argument(
        "--blind-pack",
        help="Optional blind_pack.json for hash-bound condition-blind assessment import.",
    )
    import_assessment_parser.add_argument(
        "--reveal-mapping",
        help="Optional reveal_mapping.json that maps blind item IDs back to scorecards after hash verification.",
    )
    import_assessment_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    blind_pack = exp080_sub.add_parser(
        "export-blind-pack",
        help="Export a condition-blind, hash-bound assessment pack from auditable scorecards.",
    )
    blind_pack.add_argument(
        "--case",
        required=True,
        dest="case_dir",
        help="Path to experiments/080/cases/<case_id>.",
    )
    blind_pack.add_argument(
        "--scorecard",
        required=True,
        action="append",
        dest="scorecards",
        help="Scorecard JSON path to include. Pass once per condition.",
    )
    blind_pack.add_argument(
        "--output",
        required=True,
        help="Directory to write blind_pack.json and reveal_mapping.json.",
    )
    blind_pack.add_argument(
        "--seed", help="Optional deterministic shuffle seed for repeatable exports."
    )
    blind_pack.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    summarize = exp080_sub.add_parser(
        "summarize",
        help="Summarize deterministic MABW-080 scorecards for a case.",
    )
    summarize.add_argument(
        "--case",
        required=True,
        dest="case_dir",
        help="Path to experiments/080/cases/<case_id>.",
    )
    summarize.add_argument(
        "--scorecard",
        action="append",
        dest="scorecards",
        default=[],
        help="Additional scorecard JSON path to include. May be passed multiple times.",
    )
    summarize.add_argument("--output", help="Optional path to write case_summary.json.")
    summarize.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    scaffold = exp080_sub.add_parser(
        "scaffold-condition",
        help="Prepare one initialized MABW-080 condition workspace with deterministic fast-rerun import.",
    )
    scaffold.add_argument(
        "--case",
        required=True,
        dest="case_dir",
        help="Path to experiments/080/cases/<case_id>.",
    )
    scaffold.add_argument(
        "--condition",
        required=True,
        choices=("baseline", "memory", "prompt_only"),
        help="080 condition to scaffold.",
    )
    scaffold.add_argument(
        "--workspace",
        required=True,
        help=(
            "Initialized condition workspace to import into. Must already contain "
            "config.yaml, sources.yaml, user.md, and audience_profile.md."
        ),
    )
    scaffold.add_argument(
        "--archive",
        help="Optional run archive manifest or archive directory. Defaults to frozen_fact_layer.source_archive_path.",
    )
    scaffold.add_argument(
        "--runtime",
        required=True,
        choices=list(VALID_RUNTIMES),
        help="Exact runtime identity to record in imported runtime state.",
    )
    scaffold.add_argument(
        "--repo-workdir",
        help="Optional MABW source checkout for packaged config resolution.",
    )
    scaffold.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )


def handle(args: argparse.Namespace) -> int:
    if args.experiments_action == "laj":
        return _handle_laj(args)
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
                details = (
                    payload.get("details")
                    if isinstance(payload.get("details"), dict)
                    else {}
                )
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
                blind_pack=args.blind_pack,
                reveal_mapping=args.reveal_mapping,
            )
        except Experiment080Error as exc:
            payload = exc.to_dict()
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"[experiments 080 import-assessment] ok: False")
                details = (
                    payload.get("details")
                    if isinstance(payload.get("details"), dict)
                    else {}
                )
                code = details.get("code")
                suffix = f" ({code})" if code else ""
                print(f"  - {payload.get('error')}{suffix}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_import_assessment(payload)
        return 0
    if args.experiment_080_action == "export-blind-pack":
        try:
            payload = export_blind_pack(
                case_dir=args.case_dir,
                scorecards=args.scorecards,
                output=args.output,
                seed=args.seed,
            )
        except Experiment080Error as exc:
            payload = exc.to_dict()
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"[experiments 080 export-blind-pack] ok: False")
                details = (
                    payload.get("details")
                    if isinstance(payload.get("details"), dict)
                    else {}
                )
                code = details.get("code")
                suffix = f" ({code})" if code else ""
                print(f"  - {payload.get('error')}{suffix}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_export_blind_pack(payload)
        return 0
    if args.experiment_080_action == "summarize":
        try:
            payload = summarize_case(
                case_dir=args.case_dir,
                output=args.output,
                scorecards=args.scorecards,
            )
        except Experiment080Error as exc:
            payload = exc.to_dict()
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"[experiments 080 summarize] ok: False")
                details = (
                    payload.get("details")
                    if isinstance(payload.get("details"), dict)
                    else {}
                )
                code = details.get("code")
                suffix = f" ({code})" if code else ""
                print(f"  - {payload.get('error')}{suffix}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_summarize(payload)
        return 0
    if args.experiment_080_action == "scaffold-condition":
        try:
            payload = scaffold_condition(
                case_dir=args.case_dir,
                condition=args.condition,
                workspace=args.workspace,
                archive=args.archive,
                runtime=args.runtime,
                repo_workdir=args.repo_workdir,
            )
        except Experiment080Error as exc:
            payload = exc.to_dict()
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"[experiments 080 scaffold-condition] ok: False")
                details = (
                    payload.get("details")
                    if isinstance(payload.get("details"), dict)
                    else {}
                )
                code = details.get("code")
                suffix = f" ({code})" if code else ""
                print(f"  - {payload.get('error')}{suffix}")
            return 1
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_scaffold_condition(payload)
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
            details = (
                payload.get("details")
                if isinstance(payload.get("details"), dict)
                else {}
            )
            code = details.get("code")
            suffix = f" ({code})" if code else ""
            print(f"  - {payload.get('error')}{suffix}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_register_run(payload)
    return 0


def _handle_laj(args: argparse.Namespace) -> int:
    if args.experiment_laj_action != "shadow-run":
        return 1
    try:
        runner = importlib.import_module("multi_agent_brief.semantic_evaluator.runner")
        result = runner.run_shadow(
            report=args.report,
            bounded_context=args.bounded_context,
            profile=args.profile,
            instrument=args.instrument,
            trial_id=args.trial_id,
            archive_root=args.archive_root,
        )
        payload = _shadow_payload(result)
    except Exception:
        payload = _shadow_failure_payload()
    _print_shadow_result(payload, json_output=getattr(args, "json", False))
    return 0 if payload["ok"] else 1


def _shadow_payload(result: Any) -> dict[str, object]:
    """Project the internal result onto the fixed public-safe CLI surface."""

    reason_codes = result.reason_codes
    if not isinstance(reason_codes, tuple) or any(
        type(reason) is not str for reason in reason_codes
    ):
        raise TypeError("invalid shadow result")
    return {
        "archive_complete": result.archive_complete,
        "execution_origin": result.execution_origin,
        "ok": result.ok,
        "qualification_class": result.qualification_class,
        "qualification_eligible": result.qualification_eligible,
        "reason_codes": list(reason_codes),
        "receipt_id": result.receipt_id,
        "replayed": result.replayed,
        "run_status": result.run_status,
        "validation_status": result.validation_status,
    }


def _shadow_failure_payload() -> dict[str, object]:
    return {
        "archive_complete": False,
        "execution_origin": None,
        "ok": False,
        "qualification_class": None,
        "qualification_eligible": False,
        "reason_codes": ["shadow_adapter_unavailable"],
        "receipt_id": None,
        "replayed": False,
        "run_status": None,
        "validation_status": None,
    }


def _print_shadow_result(
    payload: dict[str, object],
    *,
    json_output: bool,
) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print("Experimental / Offline shadow / Advisory only")
    print(f"ok: {payload['ok']}")
    print(f"replayed: {payload['replayed']}")
    print(f"archive_complete: {payload['archive_complete']}")
    if payload["receipt_id"] is not None:
        print(f"receipt_id: {payload['receipt_id']}")
    if payload["run_status"] is not None:
        print(f"run_status: {payload['run_status']}")
    if payload["validation_status"] is not None:
        print(f"validation_status: {payload['validation_status']}")
    reason_codes = payload["reason_codes"]
    if reason_codes:
        print(f"reason_codes: {','.join(reason_codes)}")


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
    print(
        f"[experiments 080 score-run] validity_class: {payload.get('validity_class')}"
    )
    print(
        f"[experiments 080 score-run] assessment_status: {payload.get('assessment_status')}"
    )
    print(f"[experiments 080 score-run] output: {payload.get('output')}")


def _print_import_assessment(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 import-assessment] ok: {payload.get('ok')}")
    print(f"[experiments 080 import-assessment] case_id: {payload.get('case_id')}")
    print(f"[experiments 080 import-assessment] condition: {payload.get('condition')}")
    print(f"[experiments 080 import-assessment] run_id: {payload.get('run_id')}")
    print(
        f"[experiments 080 import-assessment] validity_class: {payload.get('validity_class')}"
    )
    print(
        f"[experiments 080 import-assessment] assessment_status: {payload.get('assessment_status')}"
    )
    print(f"[experiments 080 import-assessment] output: {payload.get('output')}")


def _print_export_blind_pack(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 export-blind-pack] ok: {payload.get('ok')}")
    print(f"[experiments 080 export-blind-pack] case_id: {payload.get('case_id')}")
    print(
        f"[experiments 080 export-blind-pack] assessment_target: {payload.get('assessment_target')}"
    )
    print(
        f"[experiments 080 export-blind-pack] blind_item_count: {payload.get('blind_item_count')}"
    )
    print(
        f"[experiments 080 export-blind-pack] blind_pack: {payload.get('blind_pack')}"
    )
    print(
        f"[experiments 080 export-blind-pack] reveal_mapping: {payload.get('reveal_mapping')}"
    )


def _print_summarize(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 summarize] ok: {payload.get('ok')}")
    print(f"[experiments 080 summarize] case_id: {payload.get('case_id')}")
    print(
        f"[experiments 080 summarize] scorecard_count: {payload.get('scorecard_count')}"
    )
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    run_counts = (
        summary.get("run_counts") if isinstance(summary.get("run_counts"), dict) else {}
    )
    validity_counts = (
        run_counts.get("validity_class_counts")
        if isinstance(run_counts.get("validity_class_counts"), dict)
        else {}
    )
    if validity_counts:
        rendered = ", ".join(
            f"{key}={validity_counts[key]}" for key in sorted(validity_counts)
        )
        print(f"[experiments 080 summarize] validity: {rendered}")
    manifestation = (
        summary.get("manifestation")
        if isinstance(summary.get("manifestation"), dict)
        else {}
    )
    if manifestation:
        print(
            "[experiments 080 summarize] manifestation: "
            f"score_2={manifestation.get('score_2_manifested_count', 0)}, "
            f"score_3_overapplication={manifestation.get('score_3_overapplication_count', 0)}"
        )
    output = payload.get("output")
    if output:
        print(f"[experiments 080 summarize] output: {output}")


def _print_scaffold_condition(payload: dict[str, Any]) -> None:
    print(f"[experiments 080 scaffold-condition] ok: {payload.get('ok')}")
    print(f"[experiments 080 scaffold-condition] case_id: {payload.get('case_id')}")
    print(f"[experiments 080 scaffold-condition] condition: {payload.get('condition')}")
    print(f"[experiments 080 scaffold-condition] workspace: {payload.get('workspace')}")
    print(
        f"[experiments 080 scaffold-condition] metadata: {payload.get('metadata_path')}"
    )
    print(
        f"[experiments 080 scaffold-condition] instructions: {payload.get('operator_instructions_path')}"
    )
    fact_import = (
        payload.get("fact_layer_import")
        if isinstance(payload.get("fact_layer_import"), dict)
        else {}
    )
    if fact_import:
        print(
            f"[experiments 080 scaffold-condition] source_run_id: {fact_import.get('source_run_id')}"
        )
        stages = (
            fact_import.get("satisfied_stage_ids")
            if isinstance(fact_import.get("satisfied_stage_ids"), list)
            else []
        )
        if stages:
            print(
                f"[experiments 080 scaffold-condition] imported_stages: {', '.join(str(stage) for stage in stages)}"
            )
    print(f"[experiments 080 scaffold-condition] next: {payload.get('next_command')}")
