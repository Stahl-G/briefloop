"""Deterministic public-safe evaluation case runner."""

from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

from multi_agent_brief.evaluation_cases.contract import (
    STATIC_CONTRACT_CASE,
    WORKSPACE_CASE,
    EvaluationCaseContractError,
    _path_has_traversal_any_platform,
    _path_is_absolute_any_platform,
    case_definitions,
    validate_case_contract,
)
from multi_agent_brief.controls.contract import ControlSwitchboardError
from multi_agent_brief.controls.switchboard import (
    build_control_switchboard,
    select_control,
    show_control_switchboard,
    validate_control_switchboard,
)
from multi_agent_brief.core.config import build_run_settings, get_output_config, load_config
from multi_agent_brief.orchestrator.handoff import build_handoff, write_handoff_and_state
from multi_agent_brief.evaluation_cases.fixtures import evaluation_cases_root
from multi_agent_brief.feedback.feedback_contract import feedback_state_paths
from multi_agent_brief.feedback.feedback_state import (
    ingest_feedback,
    plan_feedback,
    validate_feedback_workspace,
)
from multi_agent_brief.product.guidance_manifestation import (
    GUIDANCE_MANIFESTATION_BOUNDARY,
    GUIDANCE_MANIFESTATION_REPORT_SCHEMA_VERSION,
    GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS,
    GUIDANCE_MANIFESTATION_RUNTIME_EFFECT,
)
from multi_agent_brief.product.bundle_projection import write_report_bundle_manifest
from multi_agent_brief.product.quality_panel import (
    quality_panel_path,
    write_quality_panel,
    write_quality_panel_html,
    write_quality_summary,
)
from multi_agent_brief.product.release_approval import (
    check_release_readiness,
    release_readiness_report_path,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    build_completion_projection,
    check_runtime_state,
    complete_finalize_transaction,
    complete_stage_transaction,
    initialize_runtime_state,
    load_stage_specs,
    record_decision,
    show_runtime_state,
)
from multi_agent_brief.orchestrator_contract import is_source_repo, resolve_repo_workdir
from multi_agent_brief.outputs.finalize import finalize_reader_outputs
from multi_agent_brief.provenance.builder import (
    build_provenance_workspace,
    show_provenance_workspace,
    validate_provenance_workspace,
)
from multi_agent_brief.quality_gates.contract import quality_gate_paths
from multi_agent_brief.quality_gates.state import (
    check_quality_gates,
    show_quality_gates,
    validate_quality_gates_workspace,
)
from multi_agent_brief.status import build_workspace_status
from multi_agent_brief.workbuddy.diagnose import build_workbuddy_diagnosis


class EvaluationCaseRunError(Exception):
    """Raised when a case cannot be prepared or run."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def list_evaluation_cases(
    *,
    cases_dir: str | Path | None = None,
) -> dict[str, Any]:
    with evaluation_cases_root(cases_dir) as root:
        cases = case_definitions(root)
        return {
            "ok": True,
            "cases_dir": str(root),
            "case_count": len(cases),
            "cases": [
                {
                    "case_id": str(case.get("case_id") or ""),
                    "case_type": str(case.get("case_type") or WORKSPACE_CASE),
                    "description": str(case.get("description") or ""),
                    "initial_stage": case.get("initial_stage"),
                }
                for case in cases
            ],
        }


def validate_evaluation_cases(
    *,
    cases_dir: str | Path | None = None,
) -> dict[str, Any]:
    with evaluation_cases_root(cases_dir) as root:
        result = validate_case_contract(root)
        result["cases_dir"] = str(root)
        return result


def run_evaluation_cases(
    *,
    cases_dir: str | Path | None = None,
    case_id: str | None = None,
    repo_workdir: str | Path | None = None,
    keep_workspaces: bool = False,
) -> dict[str, Any]:
    with evaluation_cases_root(cases_dir) as root:
        validation = validate_case_contract(root)
        if not validation.get("ok"):
            raise EvaluationCaseContractError(
                "Evaluation cases failed contract validation.",
                details=validation,
            )
        repo = resolve_repo_workdir(repo_workdir)
        all_cases = case_definitions(root)
        selected = [case for case in all_cases if case_id is None or case.get("case_id") == case_id]
        if case_id is not None and not selected:
            raise EvaluationCaseRunError(
                f"Unknown evaluation case: {case_id}",
                details={"case_id": case_id},
            )

        results: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="mabw-eval-cases-") as tmp:
            tmp_root = Path(tmp)
            for case in selected:
                results.append(
                    _run_single_case(
                        root=root,
                        temp_root=tmp_root,
                        case=case,
                        repo_workdir=repo,
                        keep_workspaces=keep_workspaces,
                    )
                )

            if keep_workspaces:
                preserved = Path.cwd() / ".mabw-eval-cases"
                preserved.mkdir(exist_ok=True)
                for result in results:
                    workspace = result.get("workspace")
                    if not workspace:
                        continue
                    source = Path(str(workspace))
                    if source.exists():
                        target = preserved / source.name
                        if target.exists():
                            shutil.rmtree(target)
                        shutil.copytree(source, target)
                        result["workspace"] = str(target)

        return {
            "ok": all(result.get("passed") for result in results),
            "cases_dir": str(root),
            "case_count": len(results),
            "passed_count": sum(1 for result in results if result.get("passed")),
            "failed_count": sum(1 for result in results if not result.get("passed")),
            "results": results,
        }


def _run_single_case(
    *,
    root: Path,
    temp_root: Path,
    case: dict[str, Any],
    repo_workdir: Path,
    keep_workspaces: bool,
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    case_type = str(case.get("case_type") or WORKSPACE_CASE)
    errors: list[str] = []
    actions: list[dict[str, Any]] = []
    observed_exit_code = 0
    workspace: Path | None = None

    try:
        if case_type == WORKSPACE_CASE:
            workspace = _prepare_workspace_case(
                root=root,
                temp_root=temp_root,
                case=case,
                repo_workdir=repo_workdir,
            )
        context = {
            "root": root,
            "case": case,
            "workspace": workspace,
            "repo_workdir": repo_workdir,
        }

        for command in case.get("commands") or []:
            action_result = _dispatch_action(command, context)
            actions.append(action_result)
            observed_exit_code = int(action_result.get("exit_code", 1))
            if observed_exit_code != 0 and command.get("continue_on_failure") is not True:
                break

        errors.extend(
            _assert_expected(
                case=case,
                workspace=workspace,
                repo_workdir=repo_workdir,
                root=root,
            )
        )
    except (RuntimeStateError, EvaluationCaseRunError, EvaluationCaseContractError, ControlSwitchboardError) as exc:
        observed_exit_code = 1
        errors.append(str(exc))
        if hasattr(exc, "details") and getattr(exc, "details"):
            errors.append(json.dumps(getattr(exc, "details"), ensure_ascii=False, sort_keys=True))
    except Exception as exc:  # pragma: no cover - keeps CLI diagnostics useful.
        observed_exit_code = 1
        errors.append(f"{type(exc).__name__}: {exc}")

    expected_exit = (case.get("expected") or {}).get("exit_code")
    if expected_exit is not None and observed_exit_code != int(expected_exit):
        errors.append(f"expected exit_code {expected_exit}, got {observed_exit_code}.")
    errors.extend(_assert_expected_actions(case=case, actions=actions))

    result: dict[str, Any] = {
        "case_id": case_id,
        "case_type": case_type,
        "passed": not errors,
        "observed_exit_code": observed_exit_code,
        "actions": actions,
        "errors": errors,
    }
    if keep_workspaces and workspace is not None:
        result["workspace"] = str(workspace)
    return result


def _prepare_workspace_case(
    *,
    root: Path,
    temp_root: Path,
    case: dict[str, Any],
    repo_workdir: Path,
) -> Path:
    case_id = str(case.get("case_id") or "")
    source = root / "cases" / case_id / "workspace"
    if not source.exists():
        raise EvaluationCaseRunError(
            f"Workspace fixture not found for case: {case_id}",
            details={"workspace": str(source)},
        )
    fixture_intermediate = source / "output" / "intermediate"
    fixture_workflow = (
        _load_json(fixture_intermediate / "workflow_state.json")
        if (fixture_intermediate / "workflow_state.json").exists()
        else None
    )
    fixture_registry = (
        _load_json(fixture_intermediate / "artifact_registry.json")
        if (fixture_intermediate / "artifact_registry.json").exists()
        else None
    )
    workspace = temp_root / case_id
    shutil.copytree(source, workspace)
    initialize_runtime_state(workspace=workspace, repo_workdir=repo_workdir, actor="system")
    _advance_to_stage(
        workspace=workspace,
        repo_workdir=repo_workdir,
        initial_stage=str(case.get("initial_stage") or ""),
    )
    if fixture_registry:
        registry_path = workspace / "output" / "intermediate" / "artifact_registry.json"
        registry_path.write_text(
            json.dumps(fixture_registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if fixture_workflow:
        _merge_fixture_workflow_metadata(
            workspace=workspace,
            fixture_workflow=fixture_workflow,
        )
    _ensure_fixture_audit_binding_control_chain(
        workspace=workspace,
        repo_workdir=repo_workdir,
        initial_stage=str(case.get("initial_stage") or ""),
    )
    return workspace


def _merge_fixture_workflow_metadata(
    *,
    workspace: Path,
    fixture_workflow: dict[str, Any],
) -> None:
    workflow_path = workspace / "output" / "intermediate" / "workflow_state.json"
    workflow = _load_json(workflow_path)
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    fixture_statuses = (
        fixture_workflow.get("stage_statuses")
        if isinstance(fixture_workflow.get("stage_statuses"), dict)
        else {}
    )
    for stage_id, fixture_entry in fixture_statuses.items():
        if not isinstance(fixture_entry, dict):
            continue
        metadata = fixture_entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        current_entry = statuses.get(stage_id)
        if not isinstance(current_entry, dict):
            continue
        current_entry["metadata"] = metadata
        statuses[stage_id] = current_entry
    workflow["stage_statuses"] = statuses
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_fixture_audit_binding_control_chain(
    *,
    workspace: Path,
    repo_workdir: Path,
    initial_stage: str,
) -> None:
    stages = load_stage_specs(repo_workdir)
    stage_ids = [str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")]
    if "auditor" not in stage_ids or initial_stage not in stage_ids:
        return
    if stage_ids.index(initial_stage) <= stage_ids.index("auditor"):
        return
    intermediate = workspace / "output" / "intermediate"
    ledger_path = intermediate / "claim_ledger.json"
    audited_brief_path = intermediate / "audited_brief.md"
    audit_report_path = intermediate / "audit_report.json"
    if not ledger_path.exists() or not audited_brief_path.exists() or not audit_report_path.exists():
        return

    ledger_sha = _sha256_file(ledger_path)
    audited_brief_sha = _sha256_file(audited_brief_path)
    audit_sha = _sha256_file(audit_report_path)
    manifest = _load_json(intermediate / "runtime_manifest.json")
    registry_path = intermediate / "artifact_registry.json"
    registry = _load_json(registry_path) if registry_path.exists() else {}
    artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), dict) else {}
    artifacts["claim_ledger"] = {
        "artifact_id": "claim_ledger",
        "path": "output/intermediate/claim_ledger.json",
        "status": "valid",
        "sha256": ledger_sha,
    }
    artifacts["audited_brief"] = {
        "artifact_id": "audited_brief",
        "path": "output/intermediate/audited_brief.md",
        "status": "valid",
        "sha256": audited_brief_sha,
    }
    artifacts["audit_report"] = {
        "artifact_id": "audit_report",
        "path": "output/intermediate/audit_report.json",
        "status": "valid",
        "sha256": audit_sha,
    }
    registry.update({
        "schema_version": "multi-agent-brief-artifact-registry/v1",
        "run_id": str(manifest.get("run_id") or "eval-fixture"),
        "updated_at": str(manifest.get("created_at") or ""),
        "artifacts": artifacts,
    })
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    workflow_path = intermediate / "workflow_state.json"
    workflow = _load_json(workflow_path)
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    auditor = statuses.get("auditor") if isinstance(statuses.get("auditor"), dict) else {}
    if auditor.get("status") != "complete":
        return
    auditor["metadata"] = {
        "upstream_artifact_sha256": {
            "claim_ledger": ledger_sha,
            "audited_brief": audited_brief_sha,
        },
        "produced_artifact_sha256": {
            "audit_report": audit_sha,
        },
    }
    statuses["auditor"] = auditor
    workflow["stage_statuses"] = statuses
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _advance_to_stage(
    *,
    workspace: Path,
    repo_workdir: Path,
    initial_stage: str,
) -> None:
    stages = load_stage_specs(repo_workdir)
    stage_ids = [str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")]
    if initial_stage not in stage_ids:
        raise EvaluationCaseRunError(
            f"Unknown initial_stage: {initial_stage}",
            details={"initial_stage": initial_stage, "known_stages": stage_ids},
        )
    workflow_path = workspace / "output" / "intermediate" / "workflow_state.json"
    workflow = _load_json(workflow_path)
    now = workflow.get("updated_at") or workflow.get("created_at") or ""
    statuses: dict[str, dict[str, str]] = {}
    initial_idx = stage_ids.index(initial_stage)
    for idx, stage_id in enumerate(stage_ids):
        if idx < initial_idx:
            statuses[stage_id] = {
                "status": "complete",
                "reason": f"Evaluation fixture prepares {initial_stage}.",
                "updated_at": str(now),
            }
        elif stage_id == initial_stage:
            statuses[stage_id] = {"status": "ready", "reason": "", "updated_at": str(now)}
        else:
            statuses[stage_id] = {"status": "pending", "reason": "", "updated_at": str(now)}
    workflow["current_stage"] = initial_stage
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["stage_statuses"] = statuses
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage_specs(
        stages=stages,
        stage_id=initial_stage,
    )
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest = _load_json(workspace / "output" / "intermediate" / "runtime_manifest.json")
    run_id = str(manifest.get("run_id") or "")
    for stage_id in stage_ids[:initial_idx]:
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type="decision_recorded",
            actor="system",
            stage_id=stage_id,
            decision="continue",
            reason=f"Evaluation fixture prepares {initial_stage}.",
            metadata={"fixture_prepared": True},
        )


def _allowed_decisions_for_stage_specs(
    *,
    stages: list[dict[str, Any]],
    stage_id: str,
) -> list[str]:
    for stage in stages:
        if str(stage.get("stage_id") or "") == stage_id:
            return [str(item) for item in stage.get("allowed_decisions") or []]
    return []


def _dispatch_action(command: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    action = str(command.get("action") or "")
    args = dict(command.get("args") or {})
    try:
        data = _run_action(action=action, args=args, context=context)
        exit_code = _action_exit_code(action=action, args=args, data=data)
        result: dict[str, Any] = {"action": action, "exit_code": exit_code, "ok": exit_code == 0}
        if "coverage" in data:
            result["coverage"] = data["coverage"]
        if "source_repo_mode" in data:
            result["source_repo_mode"] = data["source_repo_mode"]
        if "trajectory_regulation" in data:
            result["trajectory_regulation"] = data["trajectory_regulation"]
        if "guidance_manifestation" in data:
            result["guidance_manifestation"] = data["guidance_manifestation"]
        if "quality_panel" in data:
            result["quality_panel"] = data["quality_panel"]
        if "quality_summary" in data:
            result["quality_summary"] = data["quality_summary"]
        if "quality_panel_html" in data:
            result["quality_panel_html"] = data["quality_panel_html"]
        if "report_bundle_manifest" in data:
            result["report_bundle_manifest"] = data["report_bundle_manifest"]
        if "release_readiness" in data:
            result["release_readiness"] = data["release_readiness"]
        if "release_readiness_report" in data:
            result["release_readiness_report"] = data["release_readiness_report"]
        if "delivery_bundle_archive" in data:
            result["delivery_bundle_archive"] = data["delivery_bundle_archive"]
        if "audit_bundle_archive" in data:
            result["audit_bundle_archive"] = data["audit_bundle_archive"]
        if "delivery_artifact_count" in data:
            result["delivery_artifact_count"] = data["delivery_artifact_count"]
        if "audit_artifact_count" in data:
            result["audit_artifact_count"] = data["audit_artifact_count"]
        if "completion_projection" in data:
            result["completion_projection"] = data["completion_projection"]
        if "run_card" in data:
            result["run_card"] = data["run_card"]
        if "delivery_truth" in data:
            result["delivery_truth"] = data["delivery_truth"]
        if "finalize_truth" in data:
            result["finalize_truth"] = data["finalize_truth"]
        if "next_allowed_action" in data:
            result["next_allowed_action"] = data["next_allowed_action"]
        if "packaging_hygiene" in data:
            result["packaging_hygiene"] = data["packaging_hygiene"]
        if "suggested_next_command" in data:
            result["suggested_next_command"] = data["suggested_next_command"]
        return result
    except (RuntimeStateError, EvaluationCaseRunError, ControlSwitchboardError) as exc:
        return {
            "action": action,
            "exit_code": 1,
            "ok": False,
            "error": str(exc),
            "details": getattr(exc, "details", {}),
        }


def _run_action(*, action: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    workspace = context.get("workspace")
    repo_workdir = context["repo_workdir"]

    if action == "completion.project":
        projection = build_completion_projection(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
        )
        return {
            "ok": True,
            "completion_projection": projection,
            "delivery_truth": projection.get("delivery_truth") or {},
            "finalize_truth": projection.get("finalize_truth") or {},
            "next_allowed_action": projection.get("next_allowed_action"),
        }
    if action == "workbuddy.diagnose":
        diagnosis = build_workbuddy_diagnosis(workspace=_require_workspace(workspace))
        return {
            "ok": True,
            "run_card": diagnosis.get("run_card") or {},
            "delivery_truth": (
                diagnosis.get("delivery_truth")
                or (diagnosis.get("delivery") or {}).get("truth")
                or {}
            ),
            "finalize_truth": (diagnosis.get("finalize") or {}).get("truth") or {},
            "next_allowed_action": (diagnosis.get("run_card") or {}).get("next_allowed_action"),
        }
    if action == "controls.build_switchboard":
        return build_control_switchboard(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            actor="system",
        )
    if action == "controls.show":
        return show_control_switchboard(workspace=_require_workspace(workspace))
    if action == "controls.select":
        return select_control(
            workspace=_require_workspace(workspace),
            control_id=str(args.get("control") or ""),
            selection=str(args.get("selection") or ""),
            reason=str(args.get("reason") or "Evaluation control selection."),
            approved_by_human=bool(args.get("approved_by_human", False)),
            human_approval_ref=args.get("human_approval_ref"),
            actor="orchestrator",
        )
    if action == "controls.validate":
        return validate_control_switchboard(
            workspace=_require_workspace(workspace),
            strict=bool(args.get("strict", False)),
            actor="system",
        )
    if action == "gates.check":
        return check_quality_gates(
            workspace=_require_workspace(workspace),
            brief=args.get("brief"),
            ledger=args.get("ledger"),
            report_date=str(args.get("report_date") or ""),
            max_source_age_days=args.get("max_source_age_days"),
            stage_id=args.get("stage"),
            strict=bool(args.get("strict", False)),
            repo_workdir=repo_workdir,
            actor="system",
        )
    if action == "gates.show":
        return show_quality_gates(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
        )
    if action == "gates.validate":
        return validate_quality_gates_workspace(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
        )
    if action == "state.check":
        return check_runtime_state(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            actor="system",
        )
    if action == "status.show":
        status = build_workspace_status(_require_workspace(workspace))
        return {
            "ok": bool(status.get("ok")),
            "trajectory_regulation": status.get("trajectory_regulation") or {},
            "guidance_manifestation": status.get("guidance_manifestation") or {},
            "suggested_next_command": status.get("suggested_next_command"),
        }
    if action == "guidance_manifestation.seed_report":
        return _seed_guidance_manifestation_report(
            workspace=_require_workspace(workspace),
            args=args,
        )
    if action == "synthetic.seed_claim_support_case":
        return _seed_claim_support_case(
            workspace=_require_workspace(workspace),
            args=args,
        )
    if action == "release.check":
        ws = _require_workspace(workspace).resolve()
        result = check_release_readiness(
            workspace=ws,
            mode=str(args.get("mode") or ""),
        )
        payload = result.payload
        ok = payload.get("status") == "pass"
        return {
            "ok": ok,
            "release_readiness_report": release_readiness_report_path(ws).relative_to(ws).as_posix(),
            "release_readiness": {
                "mode": payload.get("mode"),
                "status": payload.get("status"),
                "approval_required": payload.get("approval_required"),
                "missing_roles": payload.get("missing_roles", []),
                "blockers": payload.get("blockers", []),
                "branding_context": payload.get("branding_context", {}),
                "authorization": payload.get("authorization"),
                "boundary": payload.get("boundary"),
            },
        }
    if action == "quality.summarize":
        return _write_quality_projection_artifacts(workspace=_require_workspace(workspace))
    if action == "packs.bundle":
        return _write_bundle_projection_artifacts(workspace=_require_workspace(workspace))
    if action == "state.decide":
        return record_decision(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            stage_id=str(args.get("stage") or ""),
            decision=str(args.get("decision") or ""),
            reason=str(args.get("reason") or "Evaluation decision."),
            actor="orchestrator",
        )
    if action == "state.stage_complete":
        return complete_stage_transaction(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            stage_id=str(args.get("stage") or ""),
            reason=str(args.get("reason") or "Evaluation stage completion."),
            actor="orchestrator",
        )
    if action == "state.finalize_complete":
        return complete_finalize_transaction(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            reason=str(args.get("reason") or "Evaluation finalize completion."),
            actor="orchestrator",
        )
    if action == "feedback.ingest":
        ws = _require_workspace(workspace)
        return ingest_feedback(
            workspace=ws,
            feedback_path=_workspace_path(ws, str(args.get("feedback") or "")),
            source=str(args.get("source") or ""),
            stage_id=args.get("stage"),
            artifact_id=args.get("artifact"),
            category=args.get("category"),
            severity=args.get("severity"),
            repo_workdir=repo_workdir,
            actor="system",
        )
    if action == "feedback.plan":
        return plan_feedback(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            actor="system",
        )
    if action == "feedback.validate":
        return validate_feedback_workspace(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
        )
    if action == "finalize":
        ws = _require_workspace(workspace)
        config = load_config(ws / "config.yaml")
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=args.get("output"),
            name=None,
            language=None,
            audience=None,
        )
        output_config = get_output_config(config)
        result = finalize_reader_outputs(
            output_dir=settings["output_dir"],
            project_name=settings["project_name"],
            output_formats=settings.get("output_formats", ["markdown"]),
            output_footer=settings.get("output_footer", ""),
            output_named_outputs=bool(settings.get("output_named_outputs", True)),
            output_filename_template=settings.get("output_filename_template", ""),
            output_filename_tokens=settings.get("output_filename_tokens", {}),
            docx_template=output_config.get("docx_template", "default"),
            source_appendix_config=output_config.get("source_appendix", {}),
        )
        return {"ok": True, "finalize_report": result.to_dict()}
    if action == "provenance.build":
        return build_provenance_workspace(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            strict=bool(args.get("strict", False)),
            actor="system",
        )
    if action == "provenance.show":
        return show_provenance_workspace(workspace=_require_workspace(workspace))
    if action == "provenance.validate":
        return validate_provenance_workspace(
            workspace=_require_workspace(workspace),
            strict=bool(args.get("strict", False)),
            actor="system",
        )
    if action == "runtime.run_handoff":
        ws = _require_workspace(workspace)
        handoff = build_handoff(
            workspace=ws,
            repo_workdir=repo_workdir,
            runtime=str(args.get("runtime") or "hermes"),
            venv=args.get("venv"),
            run_doctor=not bool(args.get("skip_doctor", True)),
        )
        written = write_handoff_and_state(
            handoff=handoff,
            workspace=ws,
            repo_workdir=repo_workdir,
            prefix="[eval-cases]",
        )
        if written is None:
            return {"ok": False}
        md_path, json_path = written
        return {
            "ok": True,
            "handoff_markdown": md_path.relative_to(ws).as_posix(),
            "handoff_json": json_path.relative_to(ws).as_posix(),
        }
    if action == "static.hermes_no_skip_finalize":
        return _check_hermes_no_skip_finalize(repo_workdir=repo_workdir)

    raise EvaluationCaseRunError(
        f"Unsupported evaluation action: {action}",
        details={"action": action},
    )


def _write_quality_projection_artifacts(*, workspace: Path) -> dict[str, Any]:
    ws = workspace.expanduser().resolve()
    panel = write_quality_panel(workspace=ws)
    summary = write_quality_summary(workspace=ws, panel_payload=panel)
    html = write_quality_panel_html(workspace=ws, panel_payload=panel)
    return {
        "ok": True,
        "quality_panel": quality_panel_path(ws).resolve().relative_to(ws).as_posix(),
        "quality_summary": summary["path"],
        "quality_summary_sha256": summary["sha256"],
        "quality_panel_html": html["path"],
        "quality_panel_html_sha256": html["sha256"],
        "overall_status": panel.get("overall_status"),
        "recommended_actions": panel.get("recommended_actions", []),
        "boundary": "quality_projection_only_not_gate_or_release_authority",
    }


def _write_bundle_projection_artifacts(*, workspace: Path) -> dict[str, Any]:
    ws = workspace.expanduser().resolve()
    manifest = write_report_bundle_manifest(workspace=ws, write_archives=True)
    archives = manifest.get("bundle_archives") if isinstance(manifest.get("bundle_archives"), dict) else {}
    delivery = archives.get("delivery") if isinstance(archives.get("delivery"), dict) else {}
    audit = archives.get("audit") if isinstance(archives.get("audit"), dict) else {}
    return {
        "ok": True,
        "report_bundle_manifest": manifest.get("manifest_path"),
        "delivery_bundle_archive": delivery.get("path"),
        "audit_bundle_archive": audit.get("path"),
        "delivery_artifact_count": (manifest.get("delivery_bundle") or {}).get("artifact_count"),
        "audit_artifact_count": (manifest.get("audit_bundle") or {}).get("artifact_count"),
        "packaging_hygiene": (manifest.get("packaging_hygiene") or {}).get("status"),
        "boundary": "bundle_projection_only_not_delivery_or_release_authority",
    }


def _seed_claim_support_case(*, workspace: Path, args: dict[str, Any]) -> dict[str, Any]:
    scenario = str(args.get("scenario") or "").strip()
    case = _claim_support_case_definition(scenario)
    ws = workspace.expanduser().resolve()
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    source_path = ws / "input" / "sources" / "source-001.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)

    raw_excerpt = str(case["raw_excerpt"])
    source_text = f"# Synthetic Source\n\n{raw_excerpt}\n"
    source_path.write_text(source_text, encoding="utf-8")
    char_start = source_text.index(raw_excerpt)
    char_end = char_start + len(raw_excerpt)
    span_id = case.get("evidence_span_id")
    support_label = str(case["support_label"])
    if span_id is None and support_label not in {"unsupported", "insufficient_evidence", "not_applicable"}:
        span_id = "ESP-001-01"

    _write_json(
        intermediate / "claim_ledger.json",
        [
            {
                "claim_id": "CL-0001",
                "statement": str(case["claim_statement"]),
                "source_id": "SRC-001",
                "evidence_text": raw_excerpt,
                "source_url": "https://example.com/synthetic-source",
                "source_type": "web_search",
                "claim_type": str(case.get("claim_type") or "fact"),
                "metadata": {
                    "source_title": str(case["source_title"]),
                    "publisher": str(case["publisher"]),
                    "published_at": "2026-06-30",
                    "importance": "high",
                    "synthetic_failure_pattern": scenario,
                },
            }
        ],
    )
    _write_json(
        intermediate / "atomic_claim_graph.json",
        {
            "schema_version": "mabw.atomic_claim_graph.v1",
            "claims": [
                {
                    "claim_id": "CL-0001",
                    "atoms": [
                        {
                            "atom_id": "AC-0001-01",
                            "text": str(case["atom_text"]),
                            "claim_role": str(case.get("claim_role") or "observed_fact"),
                            "materiality": str(case.get("materiality") or "high"),
                        }
                    ],
                    "edges": [],
                }
            ],
        },
    )
    _write_json(
        intermediate / "evidence_span_registry.json",
        {
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": "SRC-001",
                    "source_type": str(case["source_type"]),
                    "source_path": "input/sources/source-001.md",
                    "published_at": "2026-06-30",
                    "source_tier": str(case["source_tier"]),
                    "spans": [
                        {
                            "span_id": "ESP-001-01",
                            "raw_excerpt": raw_excerpt,
                            "hash": _span_hash(raw_excerpt),
                            "span_role": str(case.get("span_role") or "direct_statement"),
                            "char_start": char_start,
                            "char_end": char_end,
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        intermediate / "claim_support_matrix.json",
        {
            "schema_version": "mabw.claim_support_matrix.v1",
            "metadata": {
                "synthetic": True,
                "issue": "#96",
                "scenario": scenario,
                "semantic_boundary": (
                    "explicit_support_record_fixture_only_not_python_source_truth_judgment"
                ),
            },
            "rows": [
                {
                    "row_id": "CSM-0001",
                    "claim_id": "CL-0001",
                    "atom_id": "AC-0001-01",
                    "evidence_span_id": span_id,
                    "support_label": support_label,
                    "support_strength": str(case["support_strength"]),
                    "support_reason": str(case["support_reason"]),
                    "required_action": str(case["required_action"]),
                    "repair_owner": str(case["repair_owner"]),
                    "decision_source": "human",
                    "metadata": {
                        "synthetic_failure_pattern": scenario,
                        "mode_scope": str(case["mode_scope"]),
                    },
                }
            ],
        },
    )
    (intermediate / "audited_brief.md").write_text(
        f"# Synthetic Evidence Case\n\n## Executive Summary\n{case['claim_statement']} [src:CL-0001]\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "claim_support_matrix": "output/intermediate/claim_support_matrix.json",
        "scenario": scenario,
        "semantic_boundary": "synthetic_explicit_support_records_only_not_support_assessment",
    }


def _claim_support_case_definition(scenario: str) -> dict[str, Any]:
    cases: dict[str, dict[str, Any]] = {
        "mixed_metric_scope": {
            "claim_statement": (
                "Daily sector inflow, weekly concept inflow, and ETF subscription are comparable liquidity signals."
            ),
            "atom_text": "Mixed inflow metrics are comparable liquidity signals.",
            "raw_excerpt": (
                "Synthetic note lists daily sector inflow, weekly concept inflow, and ETF subscription as separate metrics."
            ),
            "source_title": "Synthetic Mixed Metric Snapshot",
            "publisher": "Synthetic Data Service",
            "source_type": "market_data",
            "source_tier": "third_party_dataset",
            "support_label": "insufficient_evidence",
            "support_strength": "none",
            "support_reason": (
                "metric_scope_conflict: same-row metrics use different windows and populations."
            ),
            "required_action": "human_adjudication",
            "repair_owner": "human_review",
            "mode_scope": "research_review_or_above",
            "evidence_span_id": None,
        },
        "media_only_legal_policy": {
            "claim_statement": "A synthetic policy change is legally effective for covered issuers.",
            "atom_text": "The policy change is legally effective.",
            "raw_excerpt": (
                "A synthetic media article says observers expect a policy change to become effective."
            ),
            "source_title": "Synthetic Policy Media Summary",
            "publisher": "Example Media",
            "source_type": "news_media",
            "source_tier": "media_report",
            "support_label": "unsupported",
            "support_strength": "none",
            "support_reason": "official_source_missing: media-only legal/policy claim lacks regulator text.",
            "required_action": "block_release",
            "repair_owner": "human_review",
            "mode_scope": "research_review",
            "evidence_span_id": None,
        },
        "company_event_missing_latest_official_check": {
            "claim_statement": "SyntheticCo completed the asset sale described in media coverage.",
            "atom_text": "SyntheticCo completed the asset sale.",
            "raw_excerpt": (
                "A synthetic media roundup says SyntheticCo was expected to close the asset sale."
            ),
            "source_title": "Synthetic Company Event Roundup",
            "publisher": "Example Media",
            "source_type": "news_media",
            "source_tier": "media_report",
            "support_label": "insufficient_evidence",
            "support_strength": "none",
            "support_reason": "latest_official_check_missing: no filing or company announcement check recorded.",
            "required_action": "block_release",
            "repair_owner": "human_review",
            "mode_scope": "research_review",
            "evidence_span_id": None,
        },
        "third_party_price_snapshot_formal_block": {
            "claim_statement": "The benchmark price was 100 synthetic units at week-end close.",
            "atom_text": "The benchmark price was 100 synthetic units.",
            "raw_excerpt": "A third-party snapshot lists a 100 synthetic unit week-end indication.",
            "source_title": "Synthetic Third-Party Price Snapshot",
            "publisher": "Synthetic Price Page",
            "source_type": "market_data",
            "source_tier": "third_party_dataset",
            "support_label": "weak_support",
            "support_strength": "low",
            "support_reason": (
                "third_party_price_snapshot_formal_block: acceptable as internal caveat, "
                "not a formal release source without exchange or terminal confirmation."
            ),
            "required_action": "block_release",
            "repair_owner": "human_review",
            "mode_scope": "formal_release_candidate",
            "evidence_span_id": "ESP-001-01",
        },
    }
    if scenario not in cases:
        raise EvaluationCaseRunError(
            f"Unknown synthetic claim-support scenario: {scenario}",
            details={"scenario": scenario, "known_scenarios": sorted(cases)},
        )
    return cases[scenario]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _span_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_guidance_manifestation_report(*, workspace: Path, args: dict[str, Any]) -> dict[str, Any]:
    intermediate = workspace / "output" / "intermediate"
    manifest_path = intermediate / "runtime_manifest.json"
    manifest = _load_json(manifest_path)
    run_id = str(manifest.get("run_id") or "")
    if not run_id:
        raise EvaluationCaseRunError("Runtime manifest has no run_id for manifestation seed.")

    entry_id = str(args.get("entry_id") or "AG-0001")
    status = str(args.get("status") or "not_observable")
    snapshot_path = intermediate / "improvement_memory_snapshot.md"
    snapshot_text = f"# Improvement Memory Snapshot\n\n- {entry_id}: synthetic approved guidance.\n"
    snapshot_path.write_text(snapshot_text, encoding="utf-8")
    snapshot_sha = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()

    improvement = manifest.get("improvement") if isinstance(manifest.get("improvement"), dict) else {}
    materialized = improvement.get("materialized_entry_ids") if isinstance(improvement.get("materialized_entry_ids"), list) else []
    materialized_ids = [str(item) for item in materialized if str(item)]
    if entry_id not in materialized_ids:
        materialized_ids.append(entry_id)
    improvement.update(
        {
            "snapshot_path": "output/intermediate/improvement_memory_snapshot.md",
            "snapshot_sha256": snapshot_sha,
            "materialized_entry_ids": materialized_ids,
        }
    )
    manifest["improvement"] = improvement
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = {
        "schema_version": GUIDANCE_MANIFESTATION_REPORT_SCHEMA_VERSION,
        "workspace": ".",
        "run_id": run_id,
        "generated_at": str(args.get("generated_at") or "2026-07-01T00:00:00+00:00"),
        "read_only": True,
        "runtime_effect": GUIDANCE_MANIFESTATION_RUNTIME_EFFECT,
        "boundary": GUIDANCE_MANIFESTATION_BOUNDARY,
        "assessment_method": str(args.get("assessment_method") or "synthetic_eval_human_label"),
        "entries": [
            {
                "entry_id": entry_id,
                "status": status,
                "assessment_source": str(args.get("assessment_source") or "human"),
                "notes": str(args.get("notes") or "Synthetic public-safe manifestation label."),
                "artifact_refs": [
                    {
                        "path": "output/intermediate/improvement_memory_snapshot.md",
                        "label": "synthetic approved guidance snapshot",
                    }
                ],
            }
        ],
        "non_goals": sorted(GUIDANCE_MANIFESTATION_REQUIRED_NON_GOALS),
    }
    report_path = intermediate / "guidance_manifestation_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "guidance_manifestation_report": "output/intermediate/guidance_manifestation_report.json",
    }


def _action_exit_code(*, action: str, args: dict[str, Any], data: dict[str, Any]) -> int:
    if action == "state.check" and bool(args.get("strict", False)):
        workflow = data.get("workflow_state") or {}
        return 1 if workflow.get("blocked") else 0
    if action.endswith(".validate"):
        return 0 if data.get("ok") else 1
    return 0 if data.get("ok", True) else 1


def _assert_expected(
    *,
    case: dict[str, Any],
    workspace: Path | None,
    repo_workdir: Path,
    root: Path,
) -> list[str]:
    expected = case.get("expected") or {}
    errors: list[str] = []
    if not isinstance(expected, dict):
        return ["expected must be an object."]

    for rel_path in expected.get("artifacts_exist") or []:
        if workspace is None:
            errors.append(f"artifacts_exist requires a workspace case: {rel_path}.")
            continue
        if not (workspace / str(rel_path)).exists():
            errors.append(f"Expected artifact does not exist: {rel_path}.")
    for rel_path in expected.get("artifacts_absent") or []:
        if workspace is None:
            errors.append(f"artifacts_absent requires a workspace case: {rel_path}.")
            continue
        if (workspace / str(rel_path)).exists():
            errors.append(f"Expected artifact to be absent, but it exists: {rel_path}.")

    if workspace is not None:
        errors.extend(_assert_findings_any(workspace=workspace, expected=expected))
        errors.extend(_assert_findings_absent(workspace=workspace, expected=expected))
        errors.extend(_assert_issues_any(workspace=workspace, expected=expected))
        errors.extend(_assert_graph_nodes_any(workspace=workspace, expected=expected))
        errors.extend(_assert_graph_edges_any(workspace=workspace, expected=expected))
        errors.extend(_assert_graph_absent_text(workspace=workspace, expected=expected))
        errors.extend(_assert_workflow_state(workspace=workspace, expected=expected))
        errors.extend(_assert_manifest_improvement(workspace=workspace, expected=expected))
        errors.extend(_assert_artifact_statuses(workspace=workspace, expected=expected))

    errors.extend(_assert_contains_text(root=root, repo_workdir=repo_workdir, workspace=workspace, expected=expected))
    errors.extend(_assert_absent_text(root=root, repo_workdir=repo_workdir, workspace=workspace, expected=expected))
    return errors


def _assert_expected_actions(
    *,
    case: dict[str, Any],
    actions: list[dict[str, Any]],
) -> list[str]:
    expected_actions = (case.get("expected") or {}).get("expected_actions") or []
    if not expected_actions:
        return []
    errors: list[str] = []
    if len(actions) != len(expected_actions):
        errors.append(
            f"expected_actions length {len(expected_actions)}, got {len(actions)}."
        )
    for idx, expected in enumerate(expected_actions):
        if not isinstance(expected, dict):
            errors.append(f"expected_actions[{idx}] must be an object.")
            continue
        if idx >= len(actions):
            continue
        action = actions[idx]
        if action.get("action") != expected.get("action"):
            errors.append(
                f"expected_actions[{idx}].action expected {expected.get('action')!r}, "
                f"got {action.get('action')!r}."
            )
        if action.get("exit_code") != expected.get("exit_code"):
            errors.append(
                f"expected_actions[{idx}].exit_code expected {expected.get('exit_code')!r}, "
                f"got {action.get('exit_code')!r}."
            )
        expected_error = expected.get("error_contains")
        if expected_error is not None:
            expected_fragments = (
                [expected_error]
                if isinstance(expected_error, str)
                else [str(fragment) for fragment in expected_error]
            )
            observed = " ".join(
                [
                    str(action.get("error") or ""),
                    json.dumps(action.get("details") or {}, ensure_ascii=False, sort_keys=True),
                ]
            )
            for fragment in expected_fragments:
                if fragment not in observed:
                    errors.append(
                        f"expected_actions[{idx}].error_contains missing {fragment!r}."
                    )
        result_contains = expected.get("result_contains")
        if isinstance(result_contains, dict) and not _matches_nested_partial(action, result_contains):
            errors.append(
                f"expected_actions[{idx}].result_contains expected {result_contains!r}, got {action!r}."
            )
    return errors


def _assert_findings_any(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("findings_any") or []
    if not conditions:
        return []
    report = _load_json(quality_gate_paths(workspace)["quality_gate_report"])
    findings = [finding for finding in report.get("findings") or [] if isinstance(finding, dict)]
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"findings_any condition must be an object: {condition!r}.")
            continue
        if not any(_matches_partial(finding, condition) for finding in findings):
            errors.append(f"No quality gate finding matched {condition}.")
    return errors


def _assert_findings_absent(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("findings_absent") or []
    if not conditions:
        return []
    report = _load_json(quality_gate_paths(workspace)["quality_gate_report"])
    findings = [finding for finding in report.get("findings") or [] if isinstance(finding, dict)]
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"findings_absent condition must be an object: {condition!r}.")
            continue
        if any(_matches_partial(finding, condition) for finding in findings):
            errors.append(f"Unexpected quality gate finding matched {condition}.")
    return errors


def _assert_issues_any(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("issues_any") or []
    if not conditions:
        return []
    payload = _load_json(feedback_state_paths(workspace)["feedback_issues"])
    issues = [issue for issue in payload.get("issues") or [] if isinstance(issue, dict)]
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"issues_any condition must be an object: {condition!r}.")
            continue
        if not any(_matches_partial(issue, condition) for issue in issues):
            errors.append(f"No feedback issue matched {condition}.")
    return errors


def _load_provenance_graph(workspace: Path) -> dict[str, Any]:
    return _load_json(workspace / "output" / "intermediate" / "provenance_graph.json")


def _assert_graph_nodes_any(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("graph_nodes_any") or []
    if not conditions:
        return []
    graph = _load_provenance_graph(workspace)
    nodes = [node for node in graph.get("nodes") or [] if isinstance(node, dict)]
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"graph_nodes_any condition must be an object: {condition!r}.")
            continue
        if not any(_matches_partial(node, condition) for node in nodes):
            errors.append(f"No provenance graph node matched {condition}.")
    return errors


def _assert_graph_edges_any(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("graph_edges_any") or []
    if not conditions:
        return []
    graph = _load_provenance_graph(workspace)
    edges = [edge for edge in graph.get("edges") or [] if isinstance(edge, dict)]
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"graph_edges_any condition must be an object: {condition!r}.")
            continue
        if not any(_matches_partial(edge, condition) for edge in edges):
            errors.append(f"No provenance graph edge matched {condition}.")
    return errors


def _assert_graph_absent_text(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    values = expected.get("graph_absent_text") or []
    if not values:
        return []
    graph_text = json.dumps(_load_provenance_graph(workspace), ensure_ascii=False, sort_keys=True)
    errors: list[str] = []
    for value in values:
        text = str(value)
        if text and text in graph_text:
            errors.append(f"Provenance graph unexpectedly contains text: {text!r}.")
    return errors


def _assert_workflow_state(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    condition = expected.get("workflow_state")
    if not condition:
        return []
    if not isinstance(condition, dict):
        return ["workflow_state expected value must be an object."]
    workflow = show_runtime_state(workspace=workspace)["workflow_state"]
    errors: list[str] = []
    for key, value in condition.items():
        if workflow.get(key) != value:
            errors.append(f"workflow_state.{key} expected {value!r}, got {workflow.get(key)!r}.")
    return errors


def _assert_manifest_improvement(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    condition = expected.get("manifest_improvement")
    if not condition:
        return []
    if not isinstance(condition, dict):
        return ["manifest_improvement expected value must be an object."]
    manifest_path = workspace / "output" / "intermediate" / "runtime_manifest.json"
    if not manifest_path.exists():
        return ["runtime_manifest.json is missing."]
    manifest = _load_json(manifest_path)
    improvement = manifest.get("improvement")
    if not isinstance(improvement, dict):
        return ["runtime_manifest.json does not contain improvement object."]
    errors: list[str] = []
    for key, value in condition.items():
        actual = improvement.get(key)
        if _is_non_null_sentinel(value):
            if actual is None:
                errors.append(f"manifest_improvement.{key} expected non-null value, got None.")
            continue
        if actual != value:
            errors.append(f"manifest_improvement.{key} expected {value!r}, got {actual!r}.")
    return errors


def _assert_artifact_statuses(*, workspace: Path, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("artifact_statuses") or []
    if not conditions:
        return []
    registry_path = workspace / "output" / "intermediate" / "artifact_registry.json"
    if not registry_path.exists():
        return ["artifact_registry.json is missing."]
    registry = _load_json(registry_path)
    artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), dict) else {}
    errors: list[str] = []
    for idx, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            errors.append(f"artifact_statuses[{idx}] condition must be an object.")
            continue
        artifact_id = str(condition.get("artifact_id") or "")
        if not artifact_id:
            errors.append(f"artifact_statuses[{idx}].artifact_id is required.")
            continue
        record = artifacts.get(artifact_id)
        if not isinstance(record, dict):
            errors.append(f"artifact_statuses[{idx}] missing artifact: {artifact_id}.")
            continue
        expected_status = condition.get("status")
        if expected_status is not None and record.get("status") != expected_status:
            errors.append(
                f"artifact_statuses[{idx}].status expected {expected_status!r}, "
                f"got {record.get('status')!r}."
            )
        expected_validation = condition.get("validation_result")
        actual_validation = str(record.get("validation_result") or "")
        if expected_validation is not None and actual_validation != expected_validation:
            errors.append(
                f"artifact_statuses[{idx}].validation_result expected "
                f"{expected_validation!r}, got {actual_validation!r}."
            )
        expected_fragment = condition.get("validation_result_contains")
        if expected_fragment is not None and str(expected_fragment) not in actual_validation:
            errors.append(
                f"artifact_statuses[{idx}].validation_result_contains missing "
                f"{expected_fragment!r}."
            )
    return errors


def _is_non_null_sentinel(value: Any) -> bool:
    return isinstance(value, str) and value in {"non-null", "<non-null>", "__non_null__"}


def _assert_contains_text(*, root: Path, repo_workdir: Path, workspace: Path | None, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("contains_text") or []
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"contains_text condition must be an object: {condition!r}.")
            continue
        try:
            base = _assertion_base(
                root=root,
                repo_workdir=repo_workdir,
                workspace=workspace,
                scope=str(condition.get("scope") or "cases"),
            )
        except EvaluationCaseRunError as exc:
            errors.append(str(exc))
            continue
        try:
            path = _resolve_contained_path(base=base, rel_path=str(condition.get("file") or ""))
        except EvaluationCaseRunError as exc:
            errors.append(str(exc))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"Failed to read {path}: {exc}.")
            continue
        expected_text = str(condition.get("text") or "")
        if expected_text not in text:
            errors.append(f"{path} does not contain expected text: {expected_text!r}.")
    return errors


def _assert_absent_text(*, root: Path, repo_workdir: Path, workspace: Path | None, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("absent_text") or []
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"absent_text condition must be an object: {condition!r}.")
            continue
        try:
            base = _assertion_base(
                root=root,
                repo_workdir=repo_workdir,
                workspace=workspace,
                scope=str(condition.get("scope") or "cases"),
            )
            path = _resolve_contained_path(base=base, rel_path=str(condition.get("file") or ""))
        except EvaluationCaseRunError as exc:
            errors.append(str(exc))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"Failed to read {path}: {exc}.")
            continue
        absent = str(condition.get("text") or "")
        if absent and absent in text:
            errors.append(f"{path} unexpectedly contains text: {absent!r}.")
    return errors


def _assertion_base(*, root: Path, repo_workdir: Path, workspace: Path | None, scope: str) -> Path:
    if scope == "repo":
        return repo_workdir
    if scope == "workspace":
        if workspace is None:
            raise EvaluationCaseRunError("workspace scope requires a workspace case.")
        return workspace
    return root


def _resolve_contained_path(*, base: Path, rel_path: str) -> Path:
    if _path_is_absolute_any_platform(rel_path):
        raise EvaluationCaseRunError(f"contains_text.file must be relative, not absolute: {rel_path}")
    if _path_has_traversal_any_platform(rel_path):
        raise EvaluationCaseRunError(f"contains_text.file must not contain path traversal: {rel_path}")
    resolved_base = base.resolve()
    resolved = (resolved_base / rel_path).resolve()
    try:
        resolved.relative_to(resolved_base)
    except ValueError as exc:
        raise EvaluationCaseRunError(
            f"contains_text.file resolves outside its allowed base: {rel_path}"
        ) from exc
    return resolved


def _matches_partial(item: dict[str, Any], condition: dict[str, Any]) -> bool:
    for key, value in condition.items():
        if item.get(key) != value:
            return False
    return True


def _matches_nested_partial(item: Any, condition: Any) -> bool:
    if isinstance(condition, dict):
        if not isinstance(item, dict):
            return False
        for key, value in condition.items():
            if key not in item or not _matches_nested_partial(item.get(key), value):
                return False
        return True
    if isinstance(condition, list):
        if not isinstance(item, list) or len(item) < len(condition):
            return False
        return all(_matches_nested_partial(item[idx], value) for idx, value in enumerate(condition))
    return item == condition


def _check_hermes_no_skip_finalize(*, repo_workdir: Path) -> dict[str, Any]:
    surfaces, surface_errors, source_repo_mode = _hermes_static_surfaces(repo_workdir=repo_workdir)
    errors: list[str] = []
    errors.extend(surface_errors)
    required_terms = [
        "gates check",
        "state check",
        "state stage-complete",
        "state finalize-complete",
        "finalize",
    ]
    combined_text = ""
    for label, raw_text in surfaces:
        text = _normalized_static_text(raw_text)
        combined_text += "\n" + text
        for term in required_terms:
            if term not in text:
                errors.append(f"{label} is missing invariant text: {term}.")
        gates_idx = text.find("gates check")
        state_idx = text.find("state check", max(gates_idx, 0))
        stage_complete_idx = text.find("state stage-complete", max(state_idx, 0))
        finalize_start = max(stage_complete_idx, 0)
        finalize_candidates = [
            idx
            for idx in (
                text.find("briefloop finalize", finalize_start),
                text.find("multi-agent-brief finalize", finalize_start),
            )
            if idx != -1
        ]
        finalize_idx = min(finalize_candidates) if finalize_candidates else -1
        finalize_complete_idx = text.find("state finalize-complete", max(finalize_idx, 0))
        if (
            gates_idx == -1
            or state_idx == -1
            or stage_complete_idx == -1
            or finalize_idx == -1
            or finalize_complete_idx == -1
            or not (gates_idx < state_idx < stage_complete_idx < finalize_idx < finalize_complete_idx)
        ):
            errors.append(f"{label} does not keep gates/state completion transactions around finalize.")
        if "not a quality-gate executor" not in text and "only renders reader-facing outputs" not in text:
            errors.append(f"{label} does not warn that finalize alone is not a quality-gate executor.")
    if "auditor_quality_gate_report.json" not in combined_text or "finalize_quality_gate_report.json" not in combined_text:
        errors.append("Hermes runtime guidance does not reference stage-scoped quality gate reports.")
    return {
        "ok": not errors,
        "errors": errors,
        "coverage": [label for label, _text in surfaces],
        "source_repo_mode": source_repo_mode,
    }


def _hermes_static_surfaces(*, repo_workdir: Path) -> tuple[list[tuple[str, str]], list[str], bool]:
    surfaces: list[tuple[str, str]] = []
    errors: list[str] = []
    source_repo_mode = is_source_repo(repo_workdir)
    source_files = [
        repo_workdir / "HERMES.md",
        repo_workdir / ".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md",
        repo_workdir / "integrations/hermes-plugin/mabw/skills/mabw-workflow/SKILL.md",
        repo_workdir / "integrations/hermes-plugin/mabw/skills/mabw-workflow/references/delegated-workflow.md",
    ]
    for path in source_files:
        if path.exists():
            surfaces.append((str(path), path.read_text(encoding="utf-8")))
        elif source_repo_mode:
            errors.append(f"Missing Hermes source surface: {path}.")

    from multi_agent_brief.hermes.adapter import render_hermes_prompt, render_hermes_skill

    surfaces.append(("render_hermes_skill()", render_hermes_skill()))
    surfaces.append((
        "render_hermes_prompt()",
        render_hermes_prompt(
            workspace="/tmp/mabw-eval-workspace",
            repo_workdir=str(repo_workdir),
            venv_path="/tmp/mabw-eval-venv",
        ),
    ))
    return surfaces, errors, source_repo_mode


def _normalized_static_text(text: str) -> str:
    return " ".join(text.replace("`", "").lower().split())


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_workspace(workspace: Any) -> Path:
    if workspace is None:
        raise EvaluationCaseRunError("Action requires a workspace case.")
    return Path(workspace)


def _workspace_path(workspace: Path, value: str) -> Path:
    if _path_is_absolute_any_platform(value):
        raise EvaluationCaseRunError(f"workspace fixture path must be relative, not absolute: {value}")
    if _path_has_traversal_any_platform(value):
        raise EvaluationCaseRunError(f"workspace fixture path must not contain path traversal: {value}")

    resolved_workspace = workspace.resolve()
    resolved = (resolved_workspace / value).resolve()
    try:
        resolved.relative_to(resolved_workspace)
    except ValueError as exc:
        raise EvaluationCaseRunError(
            f"workspace fixture path resolves outside workspace: {value}"
        ) from exc
    return resolved
