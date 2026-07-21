"""Quality-gate report generation and workspace state helpers."""

from __future__ import annotations

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.audit.harness import QualityHarnessAuditAgent
from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_IDS,
    AgentArtifactId,
    evaluate_workspace_agent_artifact_intakes,
    validate_workspace_intake_consumption_context,
)
from multi_agent_brief.core.citations import extract_src_ref_ids
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    check_runtime_state,
    initialize_runtime_state,
    load_artifact_contracts,
    load_stage_specs,
    raise_if_active_repair_open,
    runtime_state_paths,
    show_runtime_state,
    utc_now,
)
from multi_agent_brief.contracts.artifact_paths import (
    artifact_paths_from_contracts,
)
from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    project_claim_support_matrix_from_workspace,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ACTIVE_REPAIR_OPEN,
    E_ARTIFACT_INVALID,
    E_FROZEN_GATE_REPORT_ALREADY_EXISTS,
    E_TRANSACTION_INTEGRITY,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.outputs.atomic_reader_projection import (
    project_atomic_reader_text,
    project_atomic_reader_text_from_workspace,
)
from multi_agent_brief.product.policy_gate_adapter import (
    policy_gate_is_strict,
    resolve_workspace_policy_gate_adapter,
)
from multi_agent_brief.quality_gates.evaluation import (  # noqa: F401
    ANALYST_DRAFT_SNAPSHOT_FILE,
    COVERAGE_LIMITATION_FIELDS,
    COVERAGE_LIMITATION_WORDS,
    ENTITY_RE,
    ENTITY_STOP_PHRASES,
    FACT_NUMBER_RE,
    FINAL_ABSTRACT_BASIS_HEADING_RE,
    FINAL_ABSTRACT_CASE_FIELD_PATTERNS,
    FINAL_ABSTRACT_CASE_HEADING_RE,
    FINAL_ABSTRACT_COMPARISON_RE,
    FINAL_ABSTRACT_LIMITATION_HEADING_RE,
    FINAL_ABSTRACT_RECOMMENDATION_RE,
    FINAL_ABSTRACT_SOURCE_HEADING_RE,
    FINAL_ABSTRACT_SUPERLATIVE_RE,
    FINDING_RULES,
    GATE_RULES,
    GATE_RULE_DOC_ANCHOR,
    HIGH_PRIORITY_SCREENING_VALUES,
    STRATEGIC_IMPLICATION_PHRASES,
    _apply_gate_context,
    _artifact_exists,
    _artifact_or_none,
    _atomic_reader_projection_findings,
    _blocking_level,
    _body_lines,
    _claim_ledger_support_text,
    _claim_ref_map,
    _configured_report_cadence,
    _coverage_limitation_reason,
    _coverage_omission_findings,
    _coverage_omission_projection,
    _declared_metadata_entity_tokens,
    _editor_introduced_new_fact_findings,
    _entity_map,
    _final_abstract_case_field_present,
    _final_abstract_quality_findings,
    _finding,
    _finding_rule,
    _first_body_line_matching,
    _first_markdown_h1,
    _first_text,
    _freshness_findings,
    _gate_rule,
    _has_markdown_heading,
    _key_case_lines,
    _line_has_local_source_reference,
    _line_number_for_token,
    _map_audit_finding,
    _markdown_heading_level,
    _market_quote_metadata_findings,
    _matching_claims_for_screened_candidate,
    _material_findings,
    _mentions_any,
    _metadata_candidate_ids,
    _normalize_cadence,
    _normalize_candidate_statement,
    _normalize_fact_token,
    _row_list,
    _screened_candidate_is_high_priority,
    _screened_candidate_priority_value,
    _screened_candidate_trace,
    _section_between,
    _stage_exists,
    _stage_or_none,
    _target_relevance_findings,
    _target_terms,
    _text_or_none,
    _title_cadence,
    _token_map,
    _unsupported_strategic_implication_findings,
    _unsupported_superlative_lines,
    evaluate_quality_gate_findings,
    evaluate_quality_gate_findings_preloaded,
)
from multi_agent_brief.quality_gates.contract import (
    GATE_IDS,
    QUALITY_GATE_SCHEMA,
    QUALITY_GATE_STATE_FILES,
    empty_quality_gate_report,
    load_quality_gate_report,
    load_quality_gate_report_for_stage,
    quality_gate_report_key_for_stage,
    quality_gate_report_path_for_stage,
    quality_gate_paths,
    validate_quality_gate_report_payload,
    validate_quality_gate_workspace,
)


GATE_EVENT_ACTOR = "cli"
GATE_SCOPED_STAGES = {"auditor", "finalize"}
CURRENT_WORDS = re.compile(r"\b(this week|current|latest|newly|本周|本期|当前|最新|新增)\b", re.IGNORECASE)






def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise RuntimeStateError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeStateError(
            f"Failed to write quality gate report: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _stable_report_projection(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            str(key): _stable_report_projection(value)
            for key, value in sorted(payload.items(), key=lambda item: str(item[0]))
            if key not in {"created_at", "updated_at"}
        }
    if isinstance(payload, list):
        return [_stable_report_projection(item) for item in payload]
    return payload


def _quality_gate_reports_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _stable_report_projection(left) == _stable_report_projection(right)


def _frozen_report_record(workspace: Path, artifact_id: str) -> dict[str, Any] | None:
    try:
        state = show_runtime_state(
            workspace=workspace,
            allow_noncanonical_runtime=False,
        )
    except RuntimeStateError:
        return None
    stage_id = _gate_report_producer_stage(artifact_id)
    if stage_id is None or not _stage_is_frozen(state, stage_id):
        return None
    artifacts = (state.get("artifact_registry") or {}).get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    record = artifacts.get(artifact_id)
    return record if isinstance(record, dict) and record.get("sha256") else None


def _gate_report_producer_stage(artifact_id: str) -> str | None:
    if artifact_id == "auditor_quality_gate_report":
        return "auditor"
    if artifact_id == "finalize_quality_gate_report":
        return "finalize"
    return None


def _stage_is_frozen(state: dict[str, Any], stage_id: str) -> bool:
    workflow = state.get("workflow_state")
    statuses = workflow.get("stage_statuses") if isinstance(workflow, dict) else None
    stage = statuses.get(stage_id) if isinstance(statuses, dict) else None
    return isinstance(stage, dict) and stage.get("status") in {"complete", "skipped"}


def _ensure_frozen_report_is_unchanged(
    *,
    workspace: Path,
    report_path: Path,
    artifact_id: str,
) -> dict[str, Any] | None:
    record = _frozen_report_record(workspace, artifact_id)
    if record is None:
        return None
    expected_sha = str(record.get("sha256") or "")
    if not report_path.exists():
        raise RuntimeStateError(
            f"Frozen quality gate report is missing: {_workspace_relative(workspace, report_path)}",
            details={
                "artifact_id": artifact_id,
                "path": _workspace_relative(workspace, report_path),
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    actual_sha = _sha256_file(report_path)
    if actual_sha != expected_sha:
        raise RuntimeStateError(
            "Frozen quality gate report no longer matches artifact_registry.json.",
            details={
                "artifact_id": artifact_id,
                "path": _workspace_relative(workspace, report_path),
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return record


def _workspace_relative(workspace: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _contracts(
    *,
    workspace: Path,
    repo_workdir: str | Path | None,
) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    repo = resolve_repo_workdir(repo_workdir, workspace=workspace)
    return repo, load_stage_specs(repo), load_artifact_contracts(repo)


def _runtime_intake_context(
    *,
    workspace: Path,
    repo_workdir: str | Path | None,
    artifact_paths: Mapping[AgentArtifactId, Path],
) -> tuple[str, dict[str, Any]]:
    """Load the current-run registry used to decide intake consumption."""

    state = show_runtime_state(
        workspace=workspace,
        allow_noncanonical_runtime=False,
    )
    manifest = state.get("manifest")
    registry = state.get("artifact_registry")
    artifacts = registry.get("artifacts") if isinstance(registry, dict) else None
    candidate_path = artifact_paths["candidate_claims"]
    registry_needs_refresh = not isinstance(registry, dict) or (
        candidate_path.is_file()
        and (
            not isinstance(artifacts, dict)
            or not isinstance(artifacts.get("candidate_claims"), dict)
        )
    )
    if registry_needs_refresh:
        # Use the authoritative registry recomputer only when a materialized
        # dependency has no record. Existing current-run records, including
        # stale overlays, remain read-only inputs to the gate decision.
        state = check_runtime_state(
            workspace=workspace,
            repo_workdir=repo_workdir,
            actor=GATE_EVENT_ACTOR,
        )
        manifest = state.get("manifest")
        registry = state.get("artifact_registry")
    run_id = str(manifest.get("run_id") or "") if isinstance(manifest, dict) else ""
    registry_run_id = (
        str(registry.get("run_id") or "") if isinstance(registry, dict) else ""
    )
    if not run_id or registry_run_id != run_id:
        raise RuntimeStateError(
            "Quality gates require one current-run artifact registry authority.",
            details={
                "manifest_run_id": run_id or None,
                "artifact_registry_run_id": registry_run_id or None,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return run_id, registry


def _load_config(workspace: Path) -> dict[str, Any]:
    path = workspace / "config.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_path(workspace: Path, value: str | Path | None, default: str) -> Path:
    if value is None:
        return workspace / default
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    workspace_candidate = (workspace / path).resolve()
    if workspace_candidate.exists():
        return workspace_candidate

    cwd_candidate = path.resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    return workspace_candidate


def _read_text(path: Path, *, label: str) -> str:
    if not path.exists():
        raise RuntimeStateError(
            f"{label} not found: {path}",
            details={"path": str(path)},
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read {label}: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not text.strip():
        raise RuntimeStateError(
            f"{label} is empty: {path}",
            details={"path": str(path)},
        )
    return text


def _read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_analyst_draft_snapshot(workspace: Path) -> str | None:
    snapshot_path = workspace / ANALYST_DRAFT_SNAPSHOT_FILE
    if not snapshot_path.exists():
        return None
    try:
        return snapshot_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read Analyst draft snapshot: {snapshot_path}",
            details={"path": str(snapshot_path), "reason": str(exc)},
        ) from exc


def _load_ledger(path: Path, *, required: bool) -> ClaimLedger:
    if not path.exists():
        if required:
            raise RuntimeStateError(
                f"Claim ledger not found: {path}",
                details={"path": str(path)},
            )
        return ClaimLedger()
    try:
        return ClaimLedger.import_json(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeStateError(
            f"Failed to read Claim Ledger: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _gate_status(findings: list[dict[str, Any]]) -> str:
    if any(finding.get("blocking_level") == "blocking" for finding in findings):
        return "fail"
    if findings:
        return "warning"
    return "pass"


def _report_status(gate_results: list[dict[str, Any]]) -> str:
    if any(result.get("status") == "fail" for result in gate_results):
        return "fail"
    if any(result.get("status") == "warning" for result in gate_results):
        return "warning"
    return "pass"














def _config_report_defaults(
    config: dict[str, Any],
    *,
    report_date: str,
    max_source_age_days: int | None,
) -> tuple[str, int | None]:
    report = config.get("report") or {}
    if not isinstance(report, dict):
        return report_date, max_source_age_days

    resolved_report_date = report_date
    if not resolved_report_date and report.get("date") is not None:
        resolved_report_date = str(report.get("date") or "")

    resolved_max_source_age_days = max_source_age_days
    if resolved_max_source_age_days is None and "max_source_age_days" in report:
        try:
            resolved_max_source_age_days = int(report["max_source_age_days"])
        except (TypeError, ValueError) as exc:
            raise RuntimeStateError(
                "Invalid report.max_source_age_days in config.yaml.",
                details={"value": report.get("max_source_age_days")},
            ) from exc
    return resolved_report_date, resolved_max_source_age_days












def _claim_support_matrix_findings(
    *,
    projection: dict[str, Any],
    start_idx: int,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    reader_facing_mode: bool,
) -> list[dict[str, Any]]:
    if projection.get("status") != "valid":
        return []
    policy_projection = projection.get("policy_projection")
    atoms = policy_projection.get("atoms") if isinstance(policy_projection, dict) else None
    if not isinstance(atoms, list):
        return []

    findings: list[dict[str, Any]] = []
    emitted_row_ids: set[str] = set()
    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        for row in _row_list(atom.get("blocking_rows")):
            _append_claim_support_matrix_finding(
                findings,
                row=row,
                atom=atom,
                start_idx=start_idx,
                finding_type="claim_support_matrix_blocking_support",
                severity="high",
                blocking_level="blocking",
                description="Claim-Support Matrix records a high-materiality atom with blocking support state.",
                recommendation=(
                    "Do not release this wording as supported. Follow the matrix required_action, "
                    "or route repair/human review through the declared owner."
                ),
                stages=stages,
                artifacts=artifacts,
                reader_facing_mode=reader_facing_mode,
                emitted_row_ids=emitted_row_ids,
                projection=projection,
            )
        for row in [
            *_row_list(atom.get("weak_rows")),
            *_row_list(atom.get("downgrade_required_rows")),
            *_row_list(atom.get("adjudication_required_rows")),
        ]:
            _append_claim_support_matrix_finding(
                findings,
                row=row,
                atom=atom,
                start_idx=start_idx,
                finding_type="claim_support_matrix_weak_support",
                severity="medium",
                blocking_level="warning",
                description="Claim-Support Matrix records weak support, downgrade, or adjudication need.",
                recommendation=(
                    "Downgrade the wording or complete the declared adjudication/repair path before "
                    "treating the atom as cleanly supported."
                ),
                stages=stages,
                artifacts=artifacts,
                reader_facing_mode=reader_facing_mode,
                emitted_row_ids=emitted_row_ids,
                projection=projection,
            )
        for row in _row_list(atom.get("inference_framing_required_rows")):
            _append_claim_support_matrix_finding(
                findings,
                row=row,
                atom=atom,
                start_idx=start_idx,
                finding_type="claim_support_matrix_inference_framing",
                severity="medium",
                blocking_level="warning",
                description="Claim-Support Matrix records inferential support that needs explicit framing.",
                recommendation=(
                    "Frame this statement as an inference or clarify the inference boundary in reader-facing prose."
                ),
                stages=stages,
                artifacts=artifacts,
                reader_facing_mode=reader_facing_mode,
                emitted_row_ids=emitted_row_ids,
                projection=projection,
            )
    return findings




def _append_claim_support_matrix_finding(
    findings: list[dict[str, Any]],
    *,
    row: dict[str, Any],
    atom: dict[str, Any],
    start_idx: int,
    finding_type: str,
    severity: str,
    blocking_level: str,
    description: str,
    recommendation: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    reader_facing_mode: bool,
    emitted_row_ids: set[str],
    projection: dict[str, Any],
) -> None:
    row_id = str(row.get("row_id") or "")
    if row_id and row_id in emitted_row_ids:
        return
    if row_id:
        emitted_row_ids.add(row_id)

    repair_owner = str(row.get("repair_owner") or "human_review")
    stage_id = _claim_support_repair_stage(repair_owner, stages)
    artifact_id = _claim_support_repair_artifact(
        repair_owner=repair_owner,
        artifacts=artifacts,
        reader_facing_mode=reader_facing_mode,
    )
    row_action = str(row.get("required_action") or "unknown")
    row_label = str(row.get("support_label") or "unknown")
    atom_id = str(row.get("atom_id") or atom.get("atom_id") or "")
    findings.append(
        _finding(
            finding_id=f"QG_MATERIAL_FACT_{start_idx + len(findings):03d}",
            gate_id="material_fact",
            finding_type=finding_type,
            severity=severity,
            blocking_level=blocking_level,
            repair_owner=repair_owner if repair_owner else "human_review",
            stage_id=stage_id,
            artifact_id=artifact_id,
            claim_id=str(row.get("claim_id") or "") or None,
            source_id=None,
            description=f"{description} row={row_id or 'unknown'} atom={atom_id} label={row_label}.",
            recommendation=f"{recommendation} required_action={row_action}.",
            category="claim_support_matrix",
            evidence_ref=row_id,
            metadata={
                "row": row,
                "atom_id": atom_id,
                "atom_materiality": atom.get("materiality"),
                "atom_verdict": atom.get("verdict"),
                "matrix_status": projection.get("status"),
                "semantic_boundary": projection.get("semantic_boundary"),
            },
        )
    )


def _claim_support_repair_stage(repair_owner: str, stages: list[dict[str, Any]]) -> str | None:
    if repair_owner in {"analyst", "editor", "auditor", "claim-ledger"}:
        return _stage_or_none(stages, repair_owner)
    return None


def _claim_support_repair_artifact(
    *,
    repair_owner: str,
    artifacts: list[dict[str, Any]],
    reader_facing_mode: bool,
) -> str | None:
    if repair_owner == "claim-ledger":
        return _artifact_or_none(artifacts, "claim_ledger")
    if repair_owner == "editor":
        if reader_facing_mode:
            return _artifact_or_none(artifacts, "reader_brief")
        return _artifact_or_none(artifacts, "audited_brief")
    if repair_owner == "auditor":
        return _artifact_or_none(artifacts, "audit_report")
    return None
















































































def _gate_result(gate_id: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    rule = _gate_rule(gate_id)
    return {
        "gate_id": gate_id,
        "status": _gate_status(findings),
        "blocking": any(finding.get("blocking_level") == "blocking" for finding in findings),
        "finding_ids": [str(finding.get("finding_id")) for finding in findings],
        "rule_summary": rule["rule_summary"],
        "docs_anchor": rule["docs_anchor"],
    }


def _reader_facing_mode(workspace: Path, brief_path: Path) -> bool:
    rel_path = _workspace_relative(workspace, brief_path)
    if rel_path == "output/brief.md":
        return True
    return rel_path.startswith("output/delivery/") and rel_path.endswith(".md")






def check_quality_gates(
    *,
    workspace: str | Path,
    brief: str | Path | None = None,
    ledger: str | Path | None = None,
    report_date: str = "",
    max_source_age_days: int | None = None,
    stage_id: str | None = None,
    strict: bool = False,
    repo_workdir: str | Path | None = None,
    actor: str = GATE_EVENT_ACTOR,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _raise_if_active_repair_open_for_gate_check(ws)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    artifacts_by_id = {
        str(artifact.get("artifact_id")): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
    resolved_artifact_paths = artifact_paths_from_contracts(ws, artifacts_by_id)
    missing_intake_bindings = sorted(
        AGENT_ARTIFACT_IDS.difference(resolved_artifact_paths)
    )
    if missing_intake_bindings:
        raise RuntimeStateError(
            "Quality gates require complete agent artifact path bindings.",
            details={"missing_artifact_ids": missing_intake_bindings},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    intake_artifact_paths: dict[AgentArtifactId, Path] = {
        artifact_id: resolved_artifact_paths[artifact_id]
        for artifact_id in AGENT_ARTIFACT_IDS
    }
    run_id, artifact_registry = _runtime_intake_context(
        workspace=ws,
        repo_workdir=repo_workdir,
        artifact_paths=intake_artifact_paths,
    )

    requested_stage_id = stage_id or "auditor"
    default_brief = "output/brief.md" if requested_stage_id == "finalize" else "output/intermediate/audited_brief.md"
    if brief is not None:
        brief_path = _resolve_path(ws, brief, default_brief)
    elif requested_stage_id == "auditor":
        brief_path = resolved_artifact_paths["audited_brief"]
    else:
        brief_path = _resolve_path(ws, None, default_brief)
    reader_mode = _reader_facing_mode(ws, brief_path)
    gate_stage_id = stage_id or ("finalize" if reader_mode else "auditor")
    gate_artifact_id = quality_gate_report_key_for_stage(gate_stage_id)
    if not _stage_exists(stages, gate_stage_id):
        raise RuntimeStateError(
            f"Unknown gate stage: {gate_stage_id}",
            details={"stage_id": gate_stage_id},
        )
    if not _artifact_exists(artifacts, gate_artifact_id):
        raise RuntimeStateError(
            f"Unknown gate artifact: {gate_artifact_id}",
            details={"artifact_id": gate_artifact_id},
        )
    ledger_path = (
        _resolve_path(ws, ledger, "")
        if ledger is not None
        else resolved_artifact_paths["claim_ledger"]
    )
    markdown = _read_text(brief_path, label="Brief")
    claim_ledger = _load_ledger(ledger_path, required=not reader_mode)
    config = _load_config(ws)
    user_text = _read_optional_text(ws / "user.md")
    analyst_markdown = None if reader_mode else _read_analyst_draft_snapshot(ws)
    report_date, max_source_age_days = _config_report_defaults(
        config,
        report_date=report_date,
        max_source_age_days=max_source_age_days,
    )
    policy_gate_adapter = resolve_workspace_policy_gate_adapter(ws)
    gate_strictness = {
        "coverage_omission": policy_gate_is_strict(policy_gate_adapter, "coverage_omission", cli_strict=strict),
        "final_abstract_quality": False,
        "material_fact": policy_gate_is_strict(policy_gate_adapter, "material_fact", cli_strict=strict),
        "freshness": policy_gate_is_strict(policy_gate_adapter, "freshness", cli_strict=strict),
        "target_relevance": policy_gate_is_strict(policy_gate_adapter, "target_relevance", cli_strict=strict),
        "editor_new_fact": strict,
    }
    coverage_omission_projection = _coverage_omission_projection(
        workspace=ws,
        markdown=markdown,
        ledger=claim_ledger,
        reader_facing_mode=reader_mode,
        artifact_paths=intake_artifact_paths,
        artifact_registry=artifact_registry,
        expected_run_id=run_id,
    )
    if coverage_omission_projection.get("status") == "invalid":
        raise RuntimeStateError(
            "Quality gates cannot consume invalid screened candidates.",
            details={
                "artifact_id": "screened_candidates",
                "validation_result": coverage_omission_projection.get(
                    "screened_candidates_validation_result"
                ),
                "reason": coverage_omission_projection.get("not_interpreted_reason"),
            },
            error_code=E_ARTIFACT_INVALID,
        )

    gate_findings = evaluate_quality_gate_findings(
        markdown=markdown,
        ledger=claim_ledger,
        config=config,
        user_text=user_text,
        analyst_markdown=analyst_markdown,
        report_date=report_date,
        max_source_age_days=max_source_age_days,
        stages=stages,
        artifacts=artifacts,
        policy_gate_adapter=policy_gate_adapter,
        coverage_omission_projection=coverage_omission_projection,
        strict=strict,
        reader_facing_mode=reader_mode,
    )
    atomic_projection = project_atomic_reader_text_from_workspace(
        workspace=ws,
        target_text=markdown,
        target_artifact=_workspace_relative(ws, brief_path),
        ledger_claims=claim_ledger.to_list(),
        artifact_paths=resolved_artifact_paths,
    )
    gate_findings["material_fact"].extend(
        _atomic_reader_projection_findings(
            projection=atomic_projection,
            start_idx=len(gate_findings["material_fact"]) + 1,
            stages=stages,
            artifacts=artifacts,
            reader_facing_mode=reader_mode,
        )
    )
    claim_support_projection = project_claim_support_matrix_from_workspace(
        ws,
        artifact_paths=resolved_artifact_paths,
    )
    gate_findings["material_fact"].extend(
        _claim_support_matrix_findings(
            projection=claim_support_projection,
            start_idx=len(gate_findings["material_fact"]) + 1,
            stages=stages,
            artifacts=artifacts,
            reader_facing_mode=reader_mode,
        )
    )
    for gate_id in sorted(GATE_IDS):
        gate_findings[gate_id] = _apply_gate_context(
            gate_findings[gate_id],
            gate_stage_id=gate_stage_id,
            gate_artifact_id=gate_artifact_id,
        )

    gate_results = [_gate_result(gate_id, gate_findings[gate_id]) for gate_id in sorted(GATE_IDS)]
    findings = [finding for gate_id in sorted(GATE_IDS) for finding in gate_findings[gate_id]]
    now = utc_now()
    payload = {
        "schema_version": QUALITY_GATE_SCHEMA,
        "created_at": now,
        "updated_at": now,
        "workspace": ".",
        "report_date": report_date,
        "policy_pack": "default",
        "status": _report_status(gate_results),
        "gate_results": gate_results,
        "findings": findings,
        "metadata": {
            "brief": _workspace_relative(ws, brief_path),
            "ledger": _workspace_relative(ws, ledger_path),
            "reader_facing_mode": reader_mode,
            "strict": strict,
            "gate_strictness": gate_strictness,
            "max_source_age_days": max_source_age_days,
            "stage_id": gate_stage_id,
            "gate_stage_id": gate_stage_id,
            "gate_artifact_id": gate_artifact_id,
            "policy_gate_adapter": policy_gate_adapter,
            "atomic_reader_projection": atomic_projection,
            "claim_support_matrix_projection": claim_support_projection,
            "coverage_omission_projection": coverage_omission_projection,
        },
    }

    errors = validate_quality_gate_report_payload(payload, stages=stages, artifacts=artifacts)
    if errors:
        raise RuntimeStateError(
            "Generated quality gate report failed contract validation.",
            details={"errors": errors},
        )

    report_path = quality_gate_report_path_for_stage(ws, gate_stage_id)
    legacy_report_path = quality_gate_paths(ws)["quality_gate_report"]
    frozen_record = _ensure_frozen_report_is_unchanged(
        workspace=ws,
        report_path=report_path,
        artifact_id=gate_artifact_id,
    )
    if frozen_record is not None:
        raise RuntimeStateError(
            "Stage-scoped gate report is already frozen. Read the existing report, or use repair/new run if the report must change.",
            details={
                "artifact_id": gate_artifact_id,
                "path": _workspace_relative(ws, report_path),
                "producer_stage": gate_stage_id,
                "required_action": "read_existing_report_or_repair_or_new_run",
            },
            error_code=E_FROZEN_GATE_REPORT_ALREADY_EXISTS,
        )
    existing_report = _read_json_object(report_path)
    if existing_report is not None and _quality_gate_reports_equivalent(existing_report, payload):
        legacy_report = _read_json_object(legacy_report_path)
        if legacy_report is None or not _quality_gate_reports_equivalent(legacy_report, existing_report):
            _write_json_atomic(legacy_report_path, existing_report)
        return show_quality_gates(workspace=ws, repo_workdir=repo_workdir)
    old_report = report_path.read_bytes() if report_path.exists() else None
    old_legacy_report = legacy_report_path.read_bytes() if legacy_report_path.exists() else None
    wrote_report = False
    wrote_legacy_report = False
    try:
        _write_json_atomic(report_path, payload)
        wrote_report = True
        _write_json_atomic(legacy_report_path, payload)
        wrote_legacy_report = True
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="quality_gate_checked",
            actor=actor,
            stage_id=gate_stage_id,
            artifact_id=gate_artifact_id,
            reason=f"Quality gates checked with status {payload['status']}.",
            metadata={
                "status": payload["status"],
                "report_path": _workspace_relative(ws, report_path),
                "legacy_projection_path": _workspace_relative(ws, legacy_report_path),
                "finding_count": len(findings),
                "blocking_count": sum(1 for finding in findings if finding.get("blocking_level") == "blocking"),
            },
        )
        if any(finding.get("blocking_level") == "blocking" for finding in findings):
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="quality_gate_blocked",
                actor=actor,
                stage_id=gate_stage_id,
                artifact_id=gate_artifact_id,
                reason="Quality gates produced blocking findings.",
                metadata={"finding_ids": [finding.get("finding_id") for finding in findings if finding.get("blocking_level") == "blocking"]},
            )
        else:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="quality_gate_passed",
                actor=actor,
                stage_id=gate_stage_id,
                artifact_id=gate_artifact_id,
                reason="Quality gates produced no blocking findings.",
                metadata={},
            )
    except Exception:
        if wrote_legacy_report:
            if old_legacy_report is None:
                legacy_report_path.unlink(missing_ok=True)
            else:
                legacy_report_path.write_bytes(old_legacy_report)
        if wrote_report:
            if old_report is None:
                report_path.unlink(missing_ok=True)
            else:
                report_path.write_bytes(old_report)
        raise

    return show_quality_gates(workspace=ws, repo_workdir=repo_workdir)


def _raise_if_active_repair_open_for_gate_check(workspace: Path) -> None:
    workflow_path = runtime_state_paths(workspace)["workflow_state"]
    if not workflow_path.exists():
        return
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeStateError(
            f"workflow_state.json is unreadable before quality gate check: {exc}",
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    if not isinstance(workflow, dict):
        raise RuntimeStateError(
            "workflow_state.json must contain an object before quality gate check.",
            error_code=E_TRANSACTION_INTEGRITY,
        )
    raise_if_active_repair_open(workspace=workspace, workflow=workflow)


def show_quality_gates(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    try:
        report = load_quality_gate_report(ws)
    except Exception:
        report = None
    stage_reports: dict[str, Any] = {}
    for stage in ("auditor", "finalize"):
        try:
            stage_report = load_quality_gate_report_for_stage(ws, stage, allow_legacy=False)
        except Exception:
            stage_report = None
        if stage_report is not None:
            stage_reports[quality_gate_report_key_for_stage(stage)] = stage_report
    validation = validate_quality_gates_workspace(
        workspace=ws,
        repo_workdir=repo_workdir,
    )
    state = {
        "ok": bool(validation.get("ok")),
        "workspace": str(ws),
        "quality_gate_state_files": QUALITY_GATE_STATE_FILES,
        "quality_gate_report": report or empty_quality_gate_report(),
        "stage_quality_gate_reports": stage_reports,
        "validation": validation,
    }
    state.update(_blocking_repair_guidance(workspace=ws, validation=validation, repo_workdir=repo_workdir))
    return state


def validate_quality_gates_workspace(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    return validate_quality_gate_workspace(workspace=ws, stages=stages, artifacts=artifacts)


def _blocking_repair_guidance(
    *,
    workspace: Path,
    validation: dict[str, Any],
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    if int(validation.get("blocking_count") or 0) <= 0:
        return {}

    required_commands: list[str] = []
    try:
        from multi_agent_brief.repair.router import route_repair_for_gate

        workflow_path = runtime_state_paths(workspace)["workflow_state"]
        workflow = (
            json.loads(workflow_path.read_text(encoding="utf-8"))
            if workflow_path.exists()
            else {}
        )
        current_stage_id = str(workflow.get("current_stage") or "")
        gate_scope = _current_gate_scope_for_stage(current_stage_id)
        if gate_scope is None:
            return _stale_quality_gate_blocker_guidance(
                workspace=workspace,
                gate_stage_id=current_stage_id,
                gate_artifact_id="",
            )
        gate_stage_id, gate_artifact_id = gate_scope
        if not _current_stage_gate_report_has_blocker(workspace, gate_stage_id, gate_artifact_id):
            if (
                _validation_uses_only_legacy_gate_projection(validation)
                and _legacy_quality_gate_report_has_blocker(workspace)
            ):
                return _legacy_quality_gate_materialization_guidance(
                    workspace=workspace,
                    gate_stage_id=gate_stage_id,
                    gate_artifact_id=gate_artifact_id,
                )
            return _stale_quality_gate_blocker_guidance(
                workspace=workspace,
                gate_stage_id=gate_stage_id,
                gate_artifact_id=gate_artifact_id,
            )
        repair_route = route_repair_for_gate(
            workspace=workspace,
            gate_stage_id=gate_stage_id,
            gate_artifact_id=gate_artifact_id,
            repo_workdir=repo_workdir,
        )
    except Exception as exc:  # pragma: no cover - defensive CLI guidance path.
        gate_stage_id = ""
        gate_artifact_id = ""
        repair_route = {
            "ok": False,
            "error_code": "E_REPAIR_ROUTE_UNAVAILABLE",
            "message": str(exc),
            "workspace": str(workspace),
        }

    route_kind = repair_route.get("route_kind")
    repair_owner = repair_route.get("repair_owner")
    is_owner_stage_repair = (
        repair_route.get("ok")
        and route_kind == "owner_stage_repair"
        and repair_owner not in {None, "", "none", "human"}
        and bool(repair_route.get("allowed_artifacts"))
        and bool(repair_route.get("must_rerun_from"))
    )
    if is_owner_stage_repair:
        required_commands.extend([
            (
                f"briefloop repair start --workspace {workspace} "
                f"--gate-stage {gate_stage_id} --gate-artifact {gate_artifact_id} --json"
            ),
            f"briefloop repair complete --workspace {workspace} --reason \"<reason>\" --json",
        ])
        repair_steps = [
            "Current gate has an owner-stage repair route. Scoped repair start is handled by the repair transaction.",
            "Delegate only the reported repair_owner role.",
            "Allow edits only to repair_route.allowed_artifacts.",
            "Run repair complete after the owner edits.",
            "Rerun downstream stages from repair_route.must_rerun_from.",
        ]
    elif repair_route.get("ok") and route_kind == "human_review":
        required_commands.extend([
            f"briefloop state decide --workspace {workspace} --stage <stage> --decision request_human_review --reason \"<reason>\" --json",
            f"briefloop state decide --workspace {workspace} --stage <stage> --decision block_run --reason \"<reason>\" --json",
        ])
        repair_steps = [
            "This blocking gate requires human review before deterministic repair can proceed.",
            "Use request_human_review or block_run instead of starting owner-stage repair.",
        ]
    else:
        required_commands.extend([
            f"briefloop state decide --workspace {workspace} --stage <stage> --decision request_human_review --reason \"<reason>\" --json",
            f"briefloop state decide --workspace {workspace} --stage <stage> --decision block_run --reason \"<reason>\" --json",
        ])
        repair_steps = [
            "No deterministic owner-stage repair route is available.",
            "Use request_human_review or block_run instead of editing artifacts directly.",
        ]

    return {
        "required_commands": required_commands,
        "repair_steps": repair_steps,
        "repair_route": repair_route,
        "repair_warnings": [
            "Do not edit frozen artifacts directly.",
            "Direct edits will mark the run contaminated and non-reference-eligible.",
            "Never manually update artifact_registry.json, runtime_manifest.json, workflow_state.json, event_log.jsonl, or SHA fields.",
        ],
    }


def _current_gate_scope_for_stage(stage_id: str | None) -> tuple[str, str] | None:
    stage = str(stage_id or "")
    if stage not in GATE_SCOPED_STAGES:
        return None
    return stage, quality_gate_report_key_for_stage(stage)


def _current_stage_gate_report_has_blocker(
    workspace: Path,
    gate_stage_id: str,
    gate_artifact_id: str,
) -> bool:
    try:
        report = load_quality_gate_report_for_stage(workspace, gate_stage_id, allow_legacy=False)
    except Exception:
        return False
    if not isinstance(report, dict):
        return False
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    actual_stage = str(metadata.get("gate_stage_id") or metadata.get("stage_id") or "")
    actual_artifact = str(metadata.get("gate_artifact_id") or "")
    if actual_stage != gate_stage_id or actual_artifact != gate_artifact_id:
        return False
    return _quality_gate_report_payload_has_blocker(report)


def _legacy_quality_gate_report_has_blocker(workspace: Path) -> bool:
    try:
        report = load_quality_gate_report(workspace)
    except Exception:
        return False
    return _quality_gate_report_payload_has_blocker(report)


def _validation_uses_only_legacy_gate_projection(validation: dict[str, Any]) -> bool:
    statuses = validation.get("statuses")
    if not isinstance(statuses, dict):
        return False
    keys = {str(key) for key in statuses}
    return keys == {"quality_gate_report"}


def _quality_gate_report_payload_has_blocker(report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    if report.get("status") == "fail":
        return True
    gate_results = report.get("gate_results")
    if isinstance(gate_results, list):
        for result in gate_results:
            if not isinstance(result, dict):
                continue
            if result.get("status") == "fail" or result.get("blocking") is True:
                return True
    findings = report.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            if finding.get("blocking") is True or finding.get("blocking_level") == "blocking":
                return True
    return False


def _legacy_quality_gate_materialization_guidance(
    *,
    workspace: Path,
    gate_stage_id: str,
    gate_artifact_id: str,
) -> dict[str, Any]:
    return {
        "required_commands": [
            f"briefloop gates check --workspace {workspace} --stage {gate_stage_id} --json",
            f"briefloop gates show --workspace {workspace} --json",
        ],
        "repair_steps": [
            "Legacy quality_gate_report.json has blocking findings, but no current-stage scoped gate report is available.",
            "Rerun gates check for workflow.current_stage to materialize a stage-scoped report.",
            "Then rerun gates show and follow required_commands.",
        ],
        "repair_route": {
            "ok": True,
            "route_kind": "none",
            "repair_owner": "none",
            "review_owner": "",
            "allowed_artifacts": [],
            "must_rerun_from": "",
            "recommended_action": "",
            "reason": "Legacy gate projection must be materialized as a current-stage scoped gate report.",
            "source": {
                "stage_id": gate_stage_id,
                "kind": gate_artifact_id,
                "legacy_projection": "quality_gate_report",
            },
        },
        "repair_warnings": [
            "Do not edit frozen artifacts directly.",
            "Direct edits will mark the run contaminated and non-reference-eligible.",
            "Never manually update artifact_registry.json, runtime_manifest.json, workflow_state.json, event_log.jsonl, or SHA fields.",
        ],
    }


def _stale_quality_gate_blocker_guidance(
    *,
    workspace: Path,
    gate_stage_id: str,
    gate_artifact_id: str,
) -> dict[str, Any]:
    return {
        "required_commands": [],
        "repair_steps": [
            "Blocking quality-gate reports exist outside the current workflow stage.",
            "Do not start repair from stale downstream reports.",
            "Rerun the current or downstream gates, or inspect stage_quality_gate_reports to locate the blocking report.",
        ],
        "repair_route": {
            "ok": True,
            "route_kind": "none",
            "repair_owner": "none",
            "review_owner": "",
            "allowed_artifacts": [],
            "must_rerun_from": "",
            "recommended_action": "",
            "reason": "No blocking current gate requires repair routing.",
            "source": {
                "stage_id": gate_stage_id,
                "kind": gate_artifact_id,
            },
        },
        "repair_warnings": [
            "Do not edit frozen artifacts directly.",
            "Direct edits will mark the run contaminated and non-reference-eligible.",
            "Never manually update artifact_registry.json, runtime_manifest.json, workflow_state.json, event_log.jsonl, or SHA fields.",
        ],
    }
