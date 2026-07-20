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
        help="Experimental offline-shadow Semantic Evaluator tools.",
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
    study_preflight = laj_sub.add_parser(
        "study-preflight",
        help="Validate LAJ study eligibility and complete-trial provider budget without provider access.",
    )
    study_preflight.add_argument("--declaration", required=True)
    study_preflight.add_argument("--report", required=True)
    study_preflight.add_argument("--bounded-context", required=True)
    study_preflight.add_argument("--instrument", required=True)
    study_preflight.add_argument("--trial-id", required=True)
    study_preflight.add_argument("--archive-root", required=True)
    study_preflight.add_argument("--budget-policy", required=True)
    study_preflight.add_argument("--control-report")
    study_preflight.add_argument("--sensitivity-manifest")
    study_preflight.add_argument(
        "--output",
        required=True,
        help="New JSON file in an existing standalone laj-study-<label> directory outside every BriefLoop workspace and shadow archive.",
    )
    study_preflight.add_argument("--json", action="store_true")
    budgeted = laj_sub.add_parser(
        "budgeted-shadow-run",
        help="Run an already-authorized shadow trial only when its exact admitted prompts fit the frozen budget.",
    )
    budgeted.add_argument("--authorization", required=True)
    budgeted.add_argument("--budget-policy", required=True)
    budgeted.add_argument("--report", required=True)
    budgeted.add_argument("--bounded-context", required=True)
    budgeted.add_argument("--instrument", required=True)
    budgeted.add_argument("--archive-root", required=True)
    budgeted.add_argument(
        "--evidence-output",
        required=True,
        help="Immutable JSON evidence file in an existing standalone laj-study-<label> directory outside every BriefLoop workspace and shadow archive.",
    )
    budgeted.add_argument("--json", action="store_true")
    compare = laj_sub.add_parser(
        "study-compare",
        help="Offline exact dimension and span-overlap comparison for Human adjudication.",
    )
    compare.add_argument("--case", required=True)
    compare.add_argument("--execution-evidence", required=True)
    compare.add_argument("--archive", required=True)
    compare.add_argument(
        "--output",
        required=True,
        help="New JSON file in an existing standalone laj-study-<label> directory outside every BriefLoop workspace and shadow archive.",
    )
    compare.add_argument("--json", action="store_true")
    present = laj_sub.add_parser(
        "present",
        help="Render one verified shadow archive as advisory JSON, Markdown, and HTML.",
    )
    present.add_argument("--archive", required=True)
    present.add_argument(
        "--output-dir",
        required=True,
        help="New standalone directory named laj-advisory-<label>, outside every BriefLoop workspace and the source archive.",
    )
    present.add_argument("--expected-report-sha256")
    present.add_argument("--json", action="store_true")
    demo = laj_sub.add_parser(
        "demo",
        help="Run the packaged synthetic LAJ trial and render standalone advisory artifacts.",
    )
    demo.add_argument("--archive-root", required=True)
    demo.add_argument(
        "--output-dir",
        required=True,
        help="New standalone directory named laj-advisory-<label>, outside every BriefLoop workspace and archive.",
    )
    demo.add_argument("--json", action="store_true")

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
    # Retired public MABW-080 CLI: the authority guard rejects workspace
    # invocations; this fail-closed stub covers any no-workspace bypass.
    # Case tooling lives in the deterministic multi_agent_brief.experiments
    # service functions, not in a public command.
    print("runtime_command_unsupported")
    return 1


def _handle_laj(args: argparse.Namespace) -> int:
    if args.experiment_laj_action == "study-preflight":
        return _handle_laj_study_preflight(args)
    if args.experiment_laj_action == "budgeted-shadow-run":
        return _handle_laj_budgeted_shadow_run(args)
    if args.experiment_laj_action == "study-compare":
        return _handle_laj_study_compare(args)
    if args.experiment_laj_action == "demo":
        return _handle_laj_demo(args)
    if args.experiment_laj_action == "present":
        return _handle_laj_present(args)
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


class _LajCliError(Exception):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _read_study_model(study: Any, path: str, model: type[Any], reason: str) -> Any:
    from pathlib import Path

    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise _LajCliError(reason) from None
    return study.parse_study_json(raw, model, reason)


def _write_study_output(
    study: Any, path: str, value: Any, *, forbidden_archive: str
) -> None:
    from pathlib import Path

    output = Path(path)
    if output.exists() or not output.parent.exists():
        raise RuntimeError("study output unavailable")
    study.write_canonical_model(output, value, forbidden_archive=forbidden_archive)


def _write_study_payload(
    study: Any,
    path: str,
    payload: dict[str, object],
    *,
    forbidden_archive: str,
) -> None:
    from pathlib import Path

    output = Path(path)
    if output.exists() or not output.parent.exists():
        raise RuntimeError("study output unavailable")
    study.write_canonical_payload(output, payload, forbidden_archive=forbidden_archive)


def _print_study_payload(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print("Experimental / Offline shadow / Advisory only")
    print(f"ok: {payload.get('ok', False)}")
    print(f"reason_codes: {','.join(payload.get('reason_codes', []))}")
    print("runtime_authority: none")


def _handle_laj_study_preflight(args: argparse.Namespace) -> int:
    try:
        study = importlib.import_module("multi_agent_brief.semantic_evaluator.study")
        study.validate_standalone_study_output(
            args.output, forbidden_archive=args.archive_root
        )
        contracts = importlib.import_module(
            "multi_agent_brief.semantic_evaluator.study_contracts"
        )
        declaration = _read_study_model(
            study,
            args.declaration,
            contracts.LajStudyDeclarationV1,
            "study_declaration_invalid",
        )
        eligibility = study.evaluate_study_eligibility(declaration)
        if not eligibility.eligible:
            payload = {
                "ok": False,
                "eligibility": eligibility.to_dict(),
                "resolved_case": None,
                "authorization": None,
                "preflight": None,
                "reason_codes": ["utility_target_ineligible"],
                "provider_calls": 0,
                "runtime_authority": False,
            }
            _write_study_payload(
                study,
                args.output,
                payload,
                forbidden_archive=args.archive_root,
            )
            _print_study_payload(payload, json_output=getattr(args, "json", False))
            return 1
        if not study.verify_study_report_binding(declaration, args.report):
            payload = {
                "ok": False,
                "eligibility": eligibility.to_dict(),
                "resolved_case": None,
                "authorization": None,
                "preflight": None,
                "reason_codes": ["study_report_binding_mismatch"],
                "provider_calls": 0,
                "runtime_authority": False,
            }
            _write_study_payload(
                study,
                args.output,
                payload,
                forbidden_archive=args.archive_root,
            )
            _print_study_payload(payload, json_output=getattr(args, "json", False))
            return 1
        manifest = None
        if args.sensitivity_manifest is not None:
            from pathlib import Path

            try:
                manifest_raw = Path(args.sensitivity_manifest).read_bytes()
            except OSError:
                raise _LajCliError("sensitivity_manifest_invalid") from None
            manifest = study.parse_sensitivity_manifest(manifest_raw)
        policy = _read_study_model(
            study,
            args.budget_policy,
            contracts.LajProviderBudgetPolicyV1,
            "budget_preflight_unavailable",
        )
        result = study.prepare_study(
            declaration=declaration,
            report=args.report,
            bounded_context=args.bounded_context,
            instrument=args.instrument,
            trial_id=args.trial_id,
            archive_root=args.archive_root,
            budget_policy=policy,
            manifest=manifest,
            control_report=args.control_report,
        )
        payload = result.to_dict()
        _write_study_payload(
            study,
            args.output,
            payload,
            forbidden_archive=args.archive_root,
        )
    except Exception as exc:
        reason = getattr(exc, "reason_code", None)
        payload = {
            "ok": False,
            "reason_codes": [reason or "study_declaration_invalid"],
            "provider_calls": 0,
            "runtime_authority": False,
        }
    _print_study_payload(payload, json_output=getattr(args, "json", False))
    return 0 if payload["ok"] else 1


def _handle_laj_budgeted_shadow_run(args: argparse.Namespace) -> int:
    try:
        study = importlib.import_module("multi_agent_brief.semantic_evaluator.study")
        study.validate_standalone_study_output(
            args.evidence_output, forbidden_archive=args.archive_root
        )
        contracts = importlib.import_module(
            "multi_agent_brief.semantic_evaluator.study_contracts"
        )
        authorization = _read_study_model(
            study,
            args.authorization,
            contracts.LajProviderExecutionAuthorizationV1,
            "provider_execution_authorization_invalid",
        )
        policy = _read_study_model(
            study,
            args.budget_policy,
            contracts.LajProviderBudgetPolicyV1,
            "budget_preflight_unavailable",
        )
        result = study.budgeted_shadow_run(
            authorization=authorization,
            budget_policy=policy,
            report=args.report,
            bounded_context=args.bounded_context,
            instrument=args.instrument,
            archive_root=args.archive_root,
            evidence_output=args.evidence_output,
        )
        payload = result.to_dict()
    except Exception as exc:
        reason = getattr(exc, "reason_code", None)
        payload = {
            "ok": False,
            "reason_codes": [reason or "provider_execution_authorization_invalid"],
            "runtime_authority": False,
        }
    _print_study_payload(payload, json_output=getattr(args, "json", False))
    return 0 if payload["ok"] else 1


def _handle_laj_study_compare(args: argparse.Namespace) -> int:
    try:
        study = importlib.import_module("multi_agent_brief.semantic_evaluator.study")
        study.validate_standalone_study_output(
            args.output, forbidden_archive=args.archive
        )
        contracts = importlib.import_module(
            "multi_agent_brief.semantic_evaluator.study_contracts"
        )
        case = _read_study_model(
            study,
            args.case,
            contracts.ResolvedSensitivityCaseV1,
            "sensitivity_case_binding_mismatch",
        )
        evidence = _read_study_model(
            study,
            args.execution_evidence,
            contracts.LajStudyExecutionEvidenceV1,
            "study_execution_evidence_incomplete",
        )
        comparison = study.compare_sensitivity(
            case=case, evidence=evidence, archive_path=args.archive
        )
        _write_study_output(
            study,
            args.output,
            comparison,
            forbidden_archive=args.archive,
        )
        payload = {
            "ok": True,
            "state": comparison.state,
            "comparison_sha256": comparison.comparison_sha256,
            "reason_codes": list(comparison.reason_codes),
            "runtime_authority": False,
        }
    except Exception as exc:
        reason = getattr(exc, "reason_code", None)
        payload = {
            "ok": False,
            "state": "invalid",
            "reason_codes": [reason or "sensitivity_comparison_invalid"],
            "runtime_authority": False,
        }
    _print_study_payload(payload, json_output=getattr(args, "json", False))
    return 0 if payload["ok"] else 1


def _handle_laj_demo(args: argparse.Namespace) -> int:
    try:
        demo = importlib.import_module("multi_agent_brief.semantic_evaluator.demo")
        result = demo.run_public_safe_laj_demo(
            archive_root=args.archive_root,
            output_dir=args.output_dir,
        )
        payload = {
            "advisory_only": True,
            "archive_complete": result.archive_complete,
            "execution_origin": result.execution_origin,
            "finding_count": result.finding_count,
            "ok": result.ok,
            "output_files": list(result.output_files),
            "presentation_available": result.presentation_available,
            "qualification_class": result.qualification_class,
            "qualification_eligible": False,
            "reader_status": result.reader_status,
            "reason_codes": list(result.reason_codes),
            "receipt_id": result.receipt_id,
            "replayed": result.replayed,
            "runtime_authority": False,
            "view_sha256": result.view_sha256,
        }
    except Exception:
        payload = {
            "advisory_only": True,
            "archive_complete": False,
            "execution_origin": None,
            "finding_count": 0,
            "ok": False,
            "output_files": [],
            "presentation_available": False,
            "qualification_class": "synthetic_demo_only",
            "qualification_eligible": False,
            "reader_status": "unavailable",
            "reason_codes": ["shadow_adapter_unavailable"],
            "receipt_id": None,
            "replayed": False,
            "runtime_authority": False,
            "view_sha256": None,
        }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Experimental / Synthetic demo / Advisory only")
        print(f"ok: {payload['ok']}")
        print(f"archive_complete: {payload['archive_complete']}")
        print(f"presentation_available: {payload['presentation_available']}")
        print(f"reader_status: {payload['reader_status']}")
        print("qualification_eligible: false")
        print("runtime_authority: none")
    return 0 if payload["ok"] else 1


def _handle_laj_present(args: argparse.Namespace) -> int:
    try:
        reader = importlib.import_module("multi_agent_brief.semantic_evaluator.reader")
        result = reader.write_laj_reader_artifacts(
            archive_path=args.archive,
            output_dir=args.output_dir,
            expected_report_sha256=args.expected_report_sha256,
        )
        payload = {
            "advisory_only": True,
            "finding_count": result.view.finding_count,
            "ok": True,
            "output_files": list(reader.LAJ_READER_FILENAMES),
            "runtime_authority": False,
            "status": result.view.status,
            "view_sha256": result.view.view_sha256,
        }
    except Exception:
        payload = {
            "advisory_only": True,
            "finding_count": 0,
            "ok": False,
            "output_files": [],
            "runtime_authority": False,
            "status": "unavailable",
            "view_sha256": None,
        }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Experimental / Offline shadow / Advisory only")
        print(f"rendered: {payload['ok']}")
        print(f"status: {payload['status']}")
        print(f"finding_count: {payload['finding_count']}")
        print("runtime_authority: none")
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


