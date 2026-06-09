"""Deterministic public-safe evaluation case runner."""

from __future__ import annotations

import json
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
from multi_agent_brief.evaluation_cases.fixtures import evaluation_cases_root
from multi_agent_brief.feedback.feedback_contract import feedback_state_paths
from multi_agent_brief.feedback.feedback_state import (
    ingest_feedback,
    plan_feedback,
    validate_feedback_workspace,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    check_runtime_state,
    initialize_runtime_state,
    load_stage_specs,
    record_decision,
    show_runtime_state,
)
from multi_agent_brief.orchestrator_contract import is_source_repo, resolve_repo_workdir
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
            if observed_exit_code != 0:
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
    workspace = temp_root / case_id
    shutil.copytree(source, workspace)
    initialize_runtime_state(workspace=workspace, repo_workdir=repo_workdir, actor="system")
    _advance_to_stage(
        workspace=workspace,
        repo_workdir=repo_workdir,
        initial_stage=str(case.get("initial_stage") or ""),
    )
    return workspace


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
    for stage_id in stage_ids:
        if stage_id == initial_stage:
            break
        record_decision(
            workspace=workspace,
            repo_workdir=repo_workdir,
            stage_id=stage_id,
            decision="continue",
            reason=f"Evaluation fixture advances through {stage_id}.",
            actor="system",
        )


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
    if action == "state.decide":
        return record_decision(
            workspace=_require_workspace(workspace),
            repo_workdir=repo_workdir,
            stage_id=str(args.get("stage") or ""),
            decision=str(args.get("decision") or ""),
            reason=str(args.get("reason") or "Evaluation decision."),
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
    if action == "static.hermes_no_skip_finalize":
        return _check_hermes_no_skip_finalize(repo_workdir=repo_workdir)

    raise EvaluationCaseRunError(
        f"Unsupported evaluation action: {action}",
        details={"action": action},
    )


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

    errors.extend(_assert_contains_text(root=root, repo_workdir=repo_workdir, expected=expected))
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


def _assert_contains_text(*, root: Path, repo_workdir: Path, expected: dict[str, Any]) -> list[str]:
    conditions = expected.get("contains_text") or []
    errors: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append(f"contains_text condition must be an object: {condition!r}.")
            continue
        base = repo_workdir if condition.get("scope") == "repo" else root
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


def _check_hermes_no_skip_finalize(*, repo_workdir: Path) -> dict[str, Any]:
    surfaces, surface_errors, source_repo_mode = _hermes_static_surfaces(repo_workdir=repo_workdir)
    errors: list[str] = []
    errors.extend(surface_errors)
    required_terms = [
        "gates check",
        "state check",
        "state decide",
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
        finalize_idx = text.find("finalize", max(state_idx, 0))
        if gates_idx == -1 or state_idx == -1 or finalize_idx == -1 or not (gates_idx < state_idx < finalize_idx):
            errors.append(f"{label} does not keep gates/state before finalize.")
        if "not a quality-gate executor" not in text and "only renders reader-facing outputs" not in text:
            errors.append(f"{label} does not warn that finalize alone is not a quality-gate executor.")
    if "quality_gate_report.json" not in combined_text:
        errors.append("Hermes runtime guidance does not reference quality_gate_report.json.")
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
