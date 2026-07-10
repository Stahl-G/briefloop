"""Quality-gate report generation and workspace state helpers."""

from __future__ import annotations

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

import yaml

from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.audit.harness import QualityHarnessAuditAgent
from multi_agent_brief.contracts.agent_artifact_intake import (
    evaluate_agent_artifact_intake,
)
from multi_agent_brief.core.citations import SRC_REF_PATTERN
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    initialize_runtime_state,
    load_artifact_contracts,
    load_stage_specs,
    raise_if_active_repair_open,
    runtime_state_paths,
    show_runtime_state,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    project_claim_support_matrix_from_workspace,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ACTIVE_REPAIR_OPEN,
    E_FROZEN_GATE_REPORT_ALREADY_EXISTS,
    E_TRANSACTION_INTEGRITY,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.outputs.atomic_reader_projection import (
    project_atomic_reader_text_from_workspace,
)
from multi_agent_brief.product.policy_gate_adapter import (
    policy_gate_is_strict,
    resolve_workspace_policy_gate_adapter,
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
ANALYST_DRAFT_SNAPSHOT_FILE = "output/intermediate/analyst_draft_snapshot.md"
FACT_NUMBER_RE = re.compile(
    r"(?<![\w])(?:[$€¥£]\s*)?\d+(?:[,.]\d+)*(?:\.\d+)?%?(?:\s*(?:million|billion|trillion|thousand|mn|bn|mw|gw|gwh|mwh|%))?",
    re.IGNORECASE,
)
ENTITY_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&.-]*|[A-Z]{2,})(?:[ \t]+(?:[A-Z][A-Za-z0-9&.-]*|[A-Z]{2,})){1,5}\b"
)
ENTITY_STOP_PHRASES = {
    "Executive Summary",
    "Key Takeaways",
    "Important Notes",
}
STRATEGIC_IMPLICATION_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("early_mover_demand", ("early-mover demand", "early mover demand")),
    ("procurement_pathways", ("procurement pathway", "procurement pathways")),
    ("municipal_buyer_demand", ("municipal buyer demand", "municipal buyers demand")),
    ("policy_driven_demand", ("policy-driven demand", "policy driven demand")),
    ("partnership_recommendations", ("partnership recommendation", "partnership recommendations")),
)
FINAL_ABSTRACT_COMPARISON_RE = re.compile(
    r"\b(?:better than|worse than|outperform(?:s|ed|ing)?|underperform(?:s|ed|ing)?|"
    r"compared with|compared to|versus|vs\.?|higher than|lower than)\b|"
    r"(?:相比|相较|优于|劣于|高于|低于|超过)",
    re.IGNORECASE,
)
FINAL_ABSTRACT_SUPERLATIVE_RE = re.compile(
    r"\b(?:best|largest|smallest|leading|market-leading|dominant|unmatched)\b|"
    r"(?:最佳|最大|最小|领先|主导)",
    re.IGNORECASE,
)
FINAL_ABSTRACT_RECOMMENDATION_RE = re.compile(
    r"\b(?:recommend(?:s|ed|ing|ation)?|should|must|outlook|forecast|projection|scenario)\b|"
    r"(?:建议|应该|必须|展望|预测|情景|推演)",
    re.IGNORECASE,
)
FINAL_ABSTRACT_BASIS_HEADING_RE = re.compile(
    r"\b(?:basis|methodology|method|benchmark|comparison basis|assumptions?|scope)\b|"
    r"(?:比较基准|方法|基准|假设|范围)",
    re.IGNORECASE,
)
FINAL_ABSTRACT_LIMITATION_HEADING_RE = re.compile(
    r"\b(?:limitations?|assumptions?|scope|caveats?|constraints?)\b|"
    r"(?:限制|局限|假设|范围|注意事项)",
    re.IGNORECASE,
)
FINAL_ABSTRACT_SOURCE_HEADING_RE = re.compile(
    r"\b(?:source appendix|sources|references|bibliography)\b|(?:来源|参考)",
    re.IGNORECASE,
)
FINAL_ABSTRACT_CASE_HEADING_RE = re.compile(
    r"\b(?:key cases|case studies|cases)\b|(?:关键案例|案例)",
    re.IGNORECASE,
)
FINAL_ABSTRACT_CASE_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "date_or_period": re.compile(r"\b20\d{2}(?:-\d{2}(?:-\d{2})?)?\b|(?:日期|时间|期间|窗口)"),
    "source_reference": re.compile(r"\[src:[^\]]+\]|\[S\d+\]|(?:source|来源)[:：]", re.IGNORECASE),
    "impact_or_relevance": re.compile(r"\b(?:impact|relevance|implication|why it matters)\b|(?:影响|意义|启示|相关性)"),
}
GATE_RULE_DOC_ANCHOR = "docs/agent-contract.md#quality-gate-rule-summaries"
GATE_RULES: dict[str, dict[str, str]] = {
    "coverage_omission": {
        "rule_summary": (
            "High-priority selected screened candidates should carry into Claim Ledger claims and cited brief "
            "references, unless an explicit limitation or omission reason is recorded."
        ),
        "docs_anchor": "docs/agent-contract.md#coverage_omission",
    },
    "material_fact": {
        "rule_summary": (
            "Reader-facing factual claims must be traceable to supported Claim Ledger entries; numbers "
            "and material assertions cannot rely on uncited prose alone."
        ),
        "docs_anchor": "docs/agent-contract.md#material_fact",
    },
    "freshness": {
        "rule_summary": (
            "Time-sensitive claims must respect the workspace freshness window and source-date requirements."
        ),
        "docs_anchor": "docs/agent-contract.md#freshness",
    },
    "target_relevance": {
        "rule_summary": (
            "The reader-facing summary must keep the configured target entity or topic visible."
        ),
        "docs_anchor": "docs/agent-contract.md#target_relevance",
    },
    "editor_new_fact": {
        "rule_summary": (
            "The Delivery Editor may polish wording but must not introduce factual tokens absent from the "
            "Analyst draft."
        ),
        "docs_anchor": "docs/agent-contract.md#editor_new_fact",
    },
    "final_abstract_quality": {
        "rule_summary": (
            "Final abstract quality checks surface deterministic reader-risk patterns as warnings only; "
            "they do not score prose, prove quality, or approve delivery."
        ),
        "docs_anchor": "docs/agent-contract.md#final_abstract_quality",
    },
}
FINDING_RULES: dict[str, dict[str, str]] = {
    "target_relevance_gap": {
        "rule_summary": "Executive summary target visibility is required for reader context.",
        "docs_anchor": "docs/agent-contract.md#target_relevance_gap",
    },
    "target_priority_claim_missing_from_summary": {
        "rule_summary": "High-priority target-specific Claim Ledger entries should be represented in the summary.",
        "docs_anchor": "docs/agent-contract.md#target_relevance_gap",
    },
    "number_without_source": {
        "rule_summary": "Numbers in the brief must be tied to source-backed Claim Ledger support.",
        "docs_anchor": "docs/agent-contract.md#number_without_source",
    },
    "editor_introduced_new_fact": {
        "rule_summary": "Editor-added factual tokens must be removed or routed back through the owner stages.",
        "docs_anchor": "docs/agent-contract.md#editor_introduced_new_fact",
    },
    "unsupported_strategic_implication": {
        "rule_summary": (
            "Strategic implications and recommendations need Claim Ledger support; lexical overreach is flagged "
            "as a non-blocking warning for auditor review."
        ),
        "docs_anchor": "docs/agent-contract.md#unsupported_strategic_implication",
    },
    "atomic_atom_id_residue": {
        "rule_summary": (
            "Atomic Claim Graph atom IDs are internal decomposition aids and must not appear in reader-facing prose."
        ),
        "docs_anchor": "docs/agent-contract.md#atomic-claim-graph",
    },
    "atomic_graph_process_residue": {
        "rule_summary": (
            "Atomic Claim Graph process wording is internal control-plane residue and must not appear in reader-facing prose."
        ),
        "docs_anchor": "docs/agent-contract.md#atomic-claim-graph",
    },
    "claim_support_matrix_blocking_support": {
        "rule_summary": (
            "A present valid Claim-Support Matrix explicitly records a high-materiality atom as unsupported, "
            "contradicted, or insufficiently evidenced."
        ),
        "docs_anchor": "docs/agent-contract.md#claim-support-matrix",
    },
    "claim_support_matrix_weak_support": {
        "rule_summary": (
            "A present valid Claim-Support Matrix explicitly records weak support requiring downgrade or adjudication."
        ),
        "docs_anchor": "docs/agent-contract.md#claim-support-matrix",
    },
    "claim_support_matrix_inference_framing": {
        "rule_summary": (
            "A present valid Claim-Support Matrix explicitly records inferential support requiring reader-facing framing."
        ),
        "docs_anchor": "docs/agent-contract.md#claim-support-matrix",
    },
    "selected_candidate_missing_from_ledger": {
        "rule_summary": (
            "Selected high-priority screened candidates should not disappear before Claim Ledger freeze "
            "without an explicit limitation or omission reason."
        ),
        "docs_anchor": "docs/agent-contract.md#coverage_omission",
    },
    "selected_candidate_missing_from_brief": {
        "rule_summary": (
            "Selected high-priority screened candidates that reach the Claim Ledger should be cited in the "
            "audited or reader-facing brief unless intentionally scoped out."
        ),
        "docs_anchor": "docs/agent-contract.md#coverage_omission",
    },
    "final_scope_title_mismatch": {
        "rule_summary": "Report title cadence should not contradict the configured report cadence.",
        "docs_anchor": "docs/agent-contract.md#final_abstract_quality",
    },
    "final_missing_comparison_basis": {
        "rule_summary": "Comparison framing should include an explicit basis, method, benchmark, or scope.",
        "docs_anchor": "docs/agent-contract.md#final_abstract_quality",
    },
    "final_missing_limitation_section": {
        "rule_summary": "Recommendation, forecast, comparison, or superlative framing should declare limitations, scope, or assumptions.",
        "docs_anchor": "docs/agent-contract.md#final_abstract_quality",
    },
    "final_incomplete_key_case_fields": {
        "rule_summary": "Key-case bullets should carry date/period, source reference, and relevance/impact fields.",
        "docs_anchor": "docs/agent-contract.md#final_abstract_quality",
    },
    "final_unsupported_superlative": {
        "rule_summary": "Superlative wording should carry explicit local support or be downgraded.",
        "docs_anchor": "docs/agent-contract.md#final_abstract_quality",
    },
}


def _gate_rule(gate_id: str) -> dict[str, str]:
    return GATE_RULES.get(
        gate_id,
        {
            "rule_summary": "Quality gate rule details are available in the runtime agent contract.",
            "docs_anchor": GATE_RULE_DOC_ANCHOR,
        },
    )


def _finding_rule(*, finding_type: str, gate_id: str) -> dict[str, str]:
    return FINDING_RULES.get(finding_type, _gate_rule(gate_id))


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
        state = show_runtime_state(workspace=workspace)
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


def _runtime_run_id(
    *,
    workspace: Path,
    repo_workdir: str | Path | None,
    runtime: str = "hermes",
) -> str:
    try:
        state = show_runtime_state(workspace=workspace)
    except RuntimeStateError:
        state = initialize_runtime_state(
            workspace=workspace,
            runtime=runtime,
            repo_workdir=repo_workdir,
            actor=GATE_EVENT_ACTOR,
        )
    return str((state.get("manifest") or {}).get("run_id") or "")


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


def _stage_exists(stages: list[dict[str, Any]], stage_id: str) -> bool:
    return any(stage.get("stage_id") == stage_id for stage in stages)


def _artifact_exists(artifacts: list[dict[str, Any]], artifact_id: str) -> bool:
    return any(artifact.get("artifact_id") == artifact_id for artifact in artifacts)


def _stage_or_none(stages: list[dict[str, Any]], preferred: str) -> str | None:
    return preferred if _stage_exists(stages, preferred) else None


def _artifact_or_none(artifacts: list[dict[str, Any]], preferred: str) -> str | None:
    return preferred if _artifact_exists(artifacts, preferred) else None


def _blocking_level(*, default_blocking: bool, strict: bool) -> str:
    return "blocking" if default_blocking or strict else "warning"


def _apply_gate_context(
    findings: list[dict[str, Any]],
    *,
    gate_stage_id: str,
    gate_artifact_id: str,
) -> list[dict[str, Any]]:
    for finding in findings:
        metadata = finding.get("metadata") if isinstance(finding.get("metadata"), dict) else {}
        advisory_non_routable = (
            finding.get("gate_id") == "final_abstract_quality"
            and metadata.get("repair_boundary") == "advisory_non_routable"
        )
        repair_stage_id = None if advisory_non_routable else finding.get("repair_stage_id") or finding.get("stage_id")
        repair_artifact_id = (
            None if advisory_non_routable else finding.get("repair_artifact_id") or finding.get("artifact_id")
        )
        finding["gate_stage_id"] = gate_stage_id
        finding["gate_artifact_id"] = gate_artifact_id
        finding["repair_stage_id"] = repair_stage_id
        finding["repair_artifact_id"] = repair_artifact_id
    return findings


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


def _finding(
    *,
    finding_id: str,
    gate_id: str,
    finding_type: str,
    severity: str,
    blocking_level: str,
    repair_owner: str,
    stage_id: str | None,
    artifact_id: str | None,
    description: str,
    recommendation: str,
    category: str,
    claim_id: str | None = None,
    source_id: str | None = None,
    line_number: int | None = None,
    evidence_ref: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rule = _finding_rule(finding_type=finding_type, gate_id=gate_id)
    resolved_metadata = dict(metadata or {})
    if (
        blocking_level == "blocking"
        and repair_owner == "editor"
        and artifact_id == "audited_brief"
    ):
        resolved_metadata.update({
            "requires_content_edit": True,
            "owner_stage": "editor",
            "post_freeze_action": "open_editor_repair",
            "delivery_effect": "blocks_until_repaired",
        })
    return {
        "finding_id": finding_id,
        "gate_id": gate_id,
        "finding_type": finding_type,
        "category": category,
        "severity": severity,
        "blocking_level": blocking_level,
        "blocking": blocking_level == "blocking",
        "repair_owner": repair_owner,
        "stage_id": stage_id,
        "artifact_id": artifact_id,
        "gate_stage_id": None,
        "gate_artifact_id": None,
        "repair_stage_id": stage_id,
        "repair_artifact_id": artifact_id,
        "claim_id": claim_id,
        "source_id": source_id,
        "line_number": line_number,
        "description": description,
        "recommendation": recommendation,
        "rule_summary": rule["rule_summary"],
        "docs_anchor": rule["docs_anchor"],
        "summary": description,
        "evidence_ref": evidence_ref,
        "metadata": resolved_metadata,
    }


def _map_audit_finding(
    *,
    finding: AuditFinding,
    idx: int,
    gate_id: str,
    strict: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    finding_type = finding.finding_type
    related_claim_id = finding.related_claim_id or None
    source_stage = _stage_or_none(stages, "source-discovery")
    claim_stage = _stage_or_none(stages, "claim-ledger")
    editor_stage = _stage_or_none(stages, "editor")
    audited_artifact = _artifact_or_none(artifacts, "audited_brief")
    ledger_artifact = _artifact_or_none(artifacts, "claim_ledger")

    stage_id = editor_stage
    artifact_id = audited_artifact
    repair_owner = "editor"
    category = "unsupported_claim"
    default_blocking = False

    if finding_type in {"missing_source", "missing_source_date"}:
        stage_id = claim_stage
        artifact_id = ledger_artifact
        repair_owner = "claim-ledger"
        category = "missing_source" if finding_type == "missing_source" else "stale_source"
        default_blocking = finding_type == "missing_source"
    elif finding_type == "stale_source":
        stage_id = claim_stage or source_stage
        artifact_id = ledger_artifact
        repair_owner = "claim-ledger" if claim_stage else "source-discovery"
        category = "stale_source"
        default_blocking = False
    elif finding_type in {"missing_claim", "number_without_source"}:
        default_blocking = True
    elif finding_type in {"needs_recrawl_claim_used", "low_confidence_source_used"}:
        stage_id = source_stage or claim_stage
        artifact_id = ledger_artifact
        repair_owner = "source-discovery" if source_stage else "claim-ledger"
        default_blocking = True
    elif finding_type in {"unsupported_certainty", "low_source_density"}:
        default_blocking = True

    blocking_level = _blocking_level(default_blocking=default_blocking, strict=strict)
    severity = "high" if blocking_level == "blocking" else finding.severity
    return _finding(
        finding_id=f"QG_{gate_id.upper()}_{idx:03d}",
        gate_id=gate_id,
        finding_type=finding_type,
        severity=severity if severity in {"low", "medium", "high"} else "medium",
        blocking_level=blocking_level,
        repair_owner=repair_owner,
        stage_id=stage_id,
        artifact_id=artifact_id,
        claim_id=related_claim_id,
        source_id=None,
        line_number=finding.line_number,
        description=finding.description,
        recommendation=finding.recommendation,
        evidence_ref=finding.evidence,
        category=category,
        metadata={"source_finding_type": finding_type},
    )


def _material_findings(
    *,
    markdown: str,
    ledger: ClaimLedger,
    strict: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    report = run_deterministic_audit(markdown, ledger)
    harness = QualityHarnessAuditAgent().run_audit(markdown, ledger)
    raw = [
        finding
        for finding in [*report.findings, *harness.findings]
        if finding.finding_type
        in {
            "missing_claim",
            "number_without_source",
            "missing_source",
            "needs_recrawl_claim_used",
            "low_confidence_source_used",
            "unsupported_certainty",
            "low_source_density",
        }
    ]
    findings = [
        _map_audit_finding(
            finding=finding,
            idx=idx,
            gate_id="material_fact",
            strict=strict,
            stages=stages,
            artifacts=artifacts,
        )
        for idx, finding in enumerate(raw, start=1)
    ]
    findings.extend(
        _unsupported_strategic_implication_findings(
            markdown=markdown,
            ledger=ledger,
            start_idx=len(findings) + 1,
            stages=stages,
            artifacts=artifacts,
        )
    )
    return findings


def _unsupported_strategic_implication_findings(
    *,
    markdown: str,
    ledger: ClaimLedger,
    start_idx: int,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    support_text = _claim_ledger_support_text(ledger)
    normalized_markdown = markdown.lower()
    findings: list[dict[str, Any]] = []
    editor_stage = _stage_or_none(stages, "editor")
    audited_artifact = _artifact_or_none(artifacts, "audited_brief")
    for _phrase_id, variants in STRATEGIC_IMPLICATION_PHRASES:
        matched_variant = next((variant for variant in variants if variant in normalized_markdown), "")
        if not matched_variant:
            continue
        if any(variant in support_text for variant in variants):
            continue
        findings.append(
            _finding(
                finding_id=f"QG_MATERIAL_FACT_{start_idx + len(findings):03d}",
                gate_id="material_fact",
                finding_type="unsupported_strategic_implication",
                severity="medium",
                blocking_level="warning",
                repair_owner="editor",
                stage_id=editor_stage,
                artifact_id=audited_artifact,
                description=(
                    "The brief introduces a strategic implication or recommendation phrase "
                    f"('{matched_variant}') without matching Claim Ledger support."
                ),
                recommendation=(
                    "Downgrade or remove the implication, or add explicit Claim Ledger support through the "
                    "proper owner stages before presenting it as an implication."
                ),
                category="strategic_overreach",
                line_number=_line_number_for_token(markdown, matched_variant),
                evidence_ref=matched_variant,
                metadata={
                    "matched_phrase": matched_variant,
                    "support_check": "lexical_phrase_absent_from_claim_ledger",
                    "semantic_boundary": (
                        "warning_only; Python flags lexical overreach risk but does not judge full strategic support"
                    ),
                },
            )
        )
    return findings


def _atomic_reader_projection_findings(
    *,
    projection: dict[str, Any],
    start_idx: int,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    reader_facing_mode: bool,
) -> list[dict[str, Any]]:
    residue_findings = projection.get("atom_residue_findings")
    if not isinstance(residue_findings, list):
        return []
    stage_id = _stage_or_none(stages, "editor")
    artifact_id = _artifact_or_none(artifacts, "reader_brief" if reader_facing_mode else "audited_brief")
    findings: list[dict[str, Any]] = []
    for item in residue_findings:
        if not isinstance(item, dict):
            continue
        raw_type = str(item.get("finding_type") or "")
        if raw_type == "atomic_graph_process_residue":
            finding_type = "atomic_graph_process_residue"
            description = "Reader-facing text contains Atomic Claim Graph process wording."
        else:
            finding_type = "atomic_atom_id_residue"
            description = "Reader-facing text contains an Atomic Claim Graph atom ID."
        evidence_ref = str(item.get("atom_id") or item.get("text") or "")
        findings.append(
            _finding(
                finding_id=f"QG_MATERIAL_FACT_{start_idx + len(findings):03d}",
                gate_id="material_fact",
                finding_type=finding_type,
                severity="medium",
                blocking_level="warning",
                repair_owner="editor",
                stage_id=stage_id,
                artifact_id=artifact_id,
                claim_id=item.get("claim_id") if isinstance(item.get("claim_id"), str) else None,
                description=description,
                recommendation=(
                    "Remove Atomic Claim Graph residue from reader-facing prose and preserve only "
                    "`[src:<claim_id>]` Claim Ledger citations."
                ),
                category="atomic_reader_residue",
                line_number=item.get("line") if isinstance(item.get("line"), int) else None,
                evidence_ref=evidence_ref,
                metadata={
                    "target_artifact": projection.get("target_artifact"),
                    "projection_status": projection.get("status"),
                    "semantic_boundary": projection.get("semantic_boundary"),
                    "raw_projection_finding": item,
                },
            )
        )
    return findings


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


def _row_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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


def _claim_ledger_support_text(ledger: ClaimLedger) -> str:
    parts: list[str] = []
    for claim in ledger:
        parts.extend([
            claim.statement,
            claim.evidence_text,
            claim.applicability_reason,
        ])
        for value in claim.metadata.values():
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                parts.extend(str(item) for item in value if isinstance(item, str))
    return "\n".join(part for part in parts if isinstance(part, str)).lower()


HIGH_PRIORITY_SCREENING_VALUES = {"high", "critical", "blocking", "direct", "must_include", "must-include"}
COVERAGE_LIMITATION_FIELDS = (
    "omission_reason",
    "coverage_limitation",
    "limitation",
    "limitations",
    "limitation_reason",
    "not_included_reason",
    "scope_reason",
    "defer_reason",
    "deferred_reason",
)
COVERAGE_LIMITATION_WORDS = (
    "limitation",
    "limitations",
    "scope",
    "scoped out",
    "not covered",
    "not included",
    "omitted",
    "deferred",
    "排除",
    "范围",
    "限制",
    "未覆盖",
    "未纳入",
    "省略",
)


def _coverage_omission_projection(
    *,
    workspace: Path | None,
    markdown: str,
    ledger: ClaimLedger,
    reader_facing_mode: bool = False,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "not_available",
        "semantic_boundary": (
            "deterministic_selected_candidate_continuity_only; not full-world recall, semantic proof, "
            "or source-discovery completeness"
        ),
        "reader_facing_mode": reader_facing_mode,
        "selected_count": 0,
        "high_priority_selected_count": 0,
        "missing_from_ledger_count": 0,
        "missing_from_brief_count": 0,
        "missing_from_brief_check": (
            "skipped_reader_facing_no_internal_claim_refs"
            if reader_facing_mode
            else "internal_claim_ref_citation_continuity"
        ),
        "not_interpreted_reason": "",
    }
    if workspace is None:
        base["not_interpreted_reason"] = "workspace_not_provided"
        return base

    path = workspace / "output" / "intermediate" / "screened_candidates.json"
    if not path.exists():
        base["status"] = "missing"
        base["not_interpreted_reason"] = "screened_candidates_missing"
        return base
    intake = evaluate_agent_artifact_intake(path, artifact_id="screened_candidates")
    base["screened_candidates_validation_result"] = intake.validation_result
    if intake.status != "valid":
        base["status"] = "invalid"
        base["not_interpreted_reason"] = intake.validation_result
        return base
    payload = intake.normalized_payload
    if not isinstance(payload, dict):
        base["status"] = "legacy_not_interpreted"
        base["not_interpreted_reason"] = "legacy_list_shape_has_no_selected_bucket"
        return base

    selected = [item for item in payload.get("selected") or [] if isinstance(item, dict)]
    high_priority = [candidate for candidate in selected if _screened_candidate_is_high_priority(candidate)]
    base["status"] = "checked"
    base["selected_count"] = len(selected)
    base["high_priority_selected_count"] = len(high_priority)
    base["trace_basis"] = "candidate_id_or_exact_statement"

    cited_claim_ids = set(SRC_REF_PATTERN.findall(markdown))
    missing_from_ledger: list[dict[str, Any]] = []
    missing_from_brief: list[dict[str, Any]] = []
    scoped_out: list[dict[str, Any]] = []
    untraceable: list[dict[str, Any]] = []

    for candidate in high_priority:
        trace = _screened_candidate_trace(candidate)
        if not trace["candidate_id"] and not trace["statement"]:
            untraceable.append(trace)
            continue
        limitation = _coverage_limitation_reason(candidate=candidate, markdown=markdown)
        matches = _matching_claims_for_screened_candidate(candidate, ledger)
        if not matches:
            if limitation:
                scoped_out.append({**trace, "limitation": limitation})
                continue
            missing_from_ledger.append(trace)
            continue
        cited_matches = [claim for claim in matches if claim.claim_id in cited_claim_ids]
        if reader_facing_mode:
            if limitation:
                scoped_out.append({**trace, "limitation": limitation})
            continue
        if not cited_matches and not limitation:
            missing_from_brief.append({
                **trace,
                "claim_ids": [claim.claim_id for claim in matches],
                "source_ids": [claim.source_id for claim in matches],
            })
        elif limitation:
            scoped_out.append({**trace, "limitation": limitation})

    base.update(
        {
            "missing_from_ledger_count": len(missing_from_ledger),
            "missing_from_brief_count": len(missing_from_brief),
            "scoped_out_count": len(scoped_out),
            "untraceable_high_priority_count": len(untraceable),
            "missing_from_ledger": missing_from_ledger,
            "missing_from_brief": missing_from_brief,
            "scoped_out": scoped_out,
            "untraceable_high_priority": untraceable,
        }
    )
    return base


def _coverage_omission_findings(
    *,
    projection: dict[str, Any],
    strict: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    reader_facing_mode: bool,
) -> list[dict[str, Any]]:
    if projection.get("status") != "checked":
        return []
    findings: list[dict[str, Any]] = []
    blocking_level = _blocking_level(default_blocking=False, strict=strict)
    severity = "high" if blocking_level == "blocking" else "medium"
    ledger_stage = _stage_or_none(stages, "claim-ledger")
    ledger_artifact = _artifact_or_none(artifacts, "claim_ledger")
    editor_stage = _stage_or_none(stages, "editor")
    brief_artifact = _artifact_or_none(artifacts, "reader_brief" if reader_facing_mode else "audited_brief")

    for item in _row_list(projection.get("missing_from_ledger")):
        findings.append(
            _finding(
                finding_id=f"QG_COVERAGE_OMISSION_{len(findings)+1:03d}",
                gate_id="coverage_omission",
                finding_type="selected_candidate_missing_from_ledger",
                severity=severity,
                blocking_level=blocking_level,
                repair_owner="claim-ledger",
                stage_id=ledger_stage,
                artifact_id=ledger_artifact,
                source_id=_text_or_none(item.get("source_id")),
                description=(
                    "A high-priority selected screened candidate is not carried into the Claim Ledger "
                    f"and has no explicit omission or limitation reason: {item.get('display') or 'unknown'}."
                ),
                recommendation=(
                    "Carry the selected candidate into claim_drafts.json before freezing, or record an explicit "
                    "scope/limitation reason instead of silently dropping it."
                ),
                category="coverage_gap",
                evidence_ref=_text_or_none(item.get("candidate_id")) or _text_or_none(item.get("statement")) or "",
                metadata={
                    "screened_candidate": item,
                    "semantic_boundary": projection.get("semantic_boundary"),
                },
            )
        )
    for item in _row_list(projection.get("missing_from_brief")):
        findings.append(
            _finding(
                finding_id=f"QG_COVERAGE_OMISSION_{len(findings)+1:03d}",
                gate_id="coverage_omission",
                finding_type="selected_candidate_missing_from_brief",
                severity=severity,
                blocking_level=blocking_level,
                repair_owner="editor",
                stage_id=editor_stage,
                artifact_id=brief_artifact,
                claim_id=_first_text(item.get("claim_ids")),
                source_id=_first_text(item.get("source_ids")) or _text_or_none(item.get("source_id")),
                description=(
                    "A high-priority selected screened candidate reached the Claim Ledger but is not cited in "
                    f"the brief and has no explicit omission or limitation reason: {item.get('display') or 'unknown'}."
                ),
                recommendation=(
                    "Cite the corresponding Claim Ledger entry in the brief, or add an explicit scope/limitation "
                    "reason for omitting the selected item."
                ),
                category="coverage_gap",
                evidence_ref=", ".join(str(claim_id) for claim_id in item.get("claim_ids") or []),
                metadata={
                    "screened_candidate": item,
                    "semantic_boundary": projection.get("semantic_boundary"),
                },
            )
        )
    return findings


def _text_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _first_text(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            text = _text_or_none(item)
            if text:
                return text
    return _text_or_none(value)


def _screened_candidate_trace(candidate: dict[str, Any]) -> dict[str, Any]:
    candidate_id = _text_or_none(candidate.get("candidate_id"))
    statement = _text_or_none(candidate.get("statement")) or _text_or_none(candidate.get("claim"))
    source_id = _text_or_none(candidate.get("source_id"))
    source_title = _text_or_none(candidate.get("source_title")) or _text_or_none(candidate.get("title"))
    display = candidate_id or statement or source_title or source_id or "unknown"
    return {
        "candidate_id": candidate_id,
        "statement": statement,
        "source_id": source_id,
        "source_title": source_title,
        "display": display,
        "priority": _screened_candidate_priority_value(candidate),
    }


def _screened_candidate_priority_value(candidate: dict[str, Any]) -> str:
    for container in (candidate, candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}):
        for key in ("priority", "importance", "materiality", "severity", "selected_priority"):
            value = container.get(key) if isinstance(container, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    if candidate.get("high_priority") is True:
        return "high"
    return ""


def _screened_candidate_is_high_priority(candidate: dict[str, Any]) -> bool:
    return _screened_candidate_priority_value(candidate) in HIGH_PRIORITY_SCREENING_VALUES


def _matching_claims_for_screened_candidate(candidate: dict[str, Any], ledger: ClaimLedger) -> list[Any]:
    candidate_id = _text_or_none(candidate.get("candidate_id"))
    explicit_claim_id = _text_or_none(candidate.get("claim_id"))
    normalized_statement = _normalize_candidate_statement(
        _text_or_none(candidate.get("statement")) or _text_or_none(candidate.get("claim")) or ""
    )
    matches: list[Any] = []
    for claim in ledger:
        metadata = claim.metadata if isinstance(claim.metadata, dict) else {}
        metadata_candidate_ids = _metadata_candidate_ids(metadata)
        if explicit_claim_id and claim.claim_id == explicit_claim_id:
            matches.append(claim)
            continue
        if candidate_id and candidate_id in metadata_candidate_ids:
            matches.append(claim)
            continue
        if not candidate_id and normalized_statement and _normalize_candidate_statement(claim.statement) == normalized_statement:
            matches.append(claim)
    return matches


def _metadata_candidate_ids(metadata: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("candidate_id", "screened_candidate_id", "candidate_ids", "screened_candidate_ids"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            values.add(value.strip())
        elif isinstance(value, list):
            values.update(str(item).strip() for item in value if str(item).strip())
    return values


def _normalize_candidate_statement(value: str) -> str:
    return " ".join(value.lower().split()).strip(".,;:()[]{}")


def _coverage_limitation_reason(*, candidate: dict[str, Any], markdown: str) -> str:
    for field in COVERAGE_LIMITATION_FIELDS:
        value = candidate.get(field)
        if isinstance(value, str) and value.strip():
            return f"{field}:{value.strip()}"
        if isinstance(value, list):
            joined = "; ".join(str(item).strip() for item in value if str(item).strip())
            if joined:
                return f"{field}:{joined}"
    tokens = [
        _text_or_none(candidate.get("candidate_id")),
        _text_or_none(candidate.get("source_id")),
        _text_or_none(candidate.get("source_title")),
        _text_or_none(candidate.get("title")),
    ]
    tokens = [token.lower() for token in tokens if token]
    if not tokens:
        return ""
    for line in markdown.splitlines():
        lower = line.lower()
        if any(word in lower for word in COVERAGE_LIMITATION_WORDS) and any(token in lower for token in tokens):
            return f"brief_limitation:{line.strip()}"
    return ""


def _freshness_findings(
    *,
    markdown: str,
    ledger: ClaimLedger,
    report_date: str,
    max_source_age_days: int | None,
    strict: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    report = run_deterministic_audit(
        markdown,
        ledger,
        report_date=report_date,
        max_source_age_days=max_source_age_days,
        fail_on_stale_source=strict,
    )
    raw = [
        finding
        for finding in report.findings
        if finding.finding_type in {"stale_source", "missing_source_date"}
    ]
    findings = [
        _map_audit_finding(
            finding=finding,
            idx=idx,
            gate_id="freshness",
            strict=strict,
            stages=stages,
            artifacts=artifacts,
        )
        for idx, finding in enumerate(raw, start=1)
    ]
    findings.extend(
        _market_quote_metadata_findings(
            ledger=ledger,
            strict=strict,
            stages=stages,
            artifacts=artifacts,
            start_idx=len(findings) + 1,
        )
    )
    return findings


def _normalize_fact_token(value: str) -> str:
    return " ".join(value.strip().split()).strip(".,;:()[]{}").lower()


def _token_map(pattern: re.Pattern[str], text: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for match in pattern.finditer(text):
        raw = match.group(0).strip()
        normalized = _normalize_fact_token(raw.replace(",", ""))
        if normalized:
            tokens.setdefault(normalized, raw)
    return tokens


def _claim_ref_map(text: str) -> dict[str, str]:
    return {claim_id.lower(): claim_id for claim_id in SRC_REF_PATTERN.findall(text)}


def _entity_map(text: str) -> dict[str, str]:
    entities: dict[str, str] = {}
    in_code_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if not stripped or stripped.startswith("#"):
            continue
        line_body = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", stripped)
        entities.update(_token_map(ENTITY_RE, line_body))
    stop = {_normalize_fact_token(item): item for item in ENTITY_STOP_PHRASES}
    return {key: value for key, value in entities.items() if key not in stop}


def _line_number_for_token(text: str, token: str) -> int | None:
    normalized_token = token.lower()
    for idx, line in enumerate(text.splitlines(), start=1):
        if normalized_token in line.lower():
            return idx
    return None


def _editor_introduced_new_fact_findings(
    *,
    markdown: str,
    analyst_markdown: str | None,
    config: dict[str, Any],
    user_text: str,
    strict: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if analyst_markdown is None:
        return []

    introduced_numbers = sorted(
        set(_token_map(FACT_NUMBER_RE, markdown)) - set(_token_map(FACT_NUMBER_RE, analyst_markdown))
    )
    introduced_claim_ids = sorted(set(_claim_ref_map(markdown)) - set(_claim_ref_map(analyst_markdown)))
    allowed_metadata_entities = _declared_metadata_entity_tokens(config=config, user_text=user_text)
    introduced_entities = sorted(
        set(_entity_map(markdown)) - set(_entity_map(analyst_markdown)) - allowed_metadata_entities
    )
    if not introduced_numbers and not introduced_claim_ids and not introduced_entities:
        return []

    number_values = _token_map(FACT_NUMBER_RE, markdown)
    claim_values = _claim_ref_map(markdown)
    entity_values = _entity_map(markdown)
    samples = {
        "numbers": [number_values[item] for item in introduced_numbers[:5]],
        "claim_ids": [claim_values[item] for item in introduced_claim_ids[:5]],
        "entities": [entity_values[item] for item in introduced_entities[:5]],
    }
    sample_text = ", ".join(value for values in samples.values() for value in values)
    first_sample = next((value for values in samples.values() for value in values), "")
    blocking_level = _blocking_level(default_blocking=False, strict=strict)
    return [
        _finding(
            finding_id="QG_EDITOR_NEW_FACT_001",
            gate_id="editor_new_fact",
            finding_type="editor_introduced_new_fact",
            severity="high" if blocking_level == "blocking" else "medium",
            blocking_level=blocking_level,
            repair_owner="editor",
            stage_id=_stage_or_none(stages, "editor"),
            artifact_id=_artifact_or_none(artifacts, "audited_brief"),
            line_number=_line_number_for_token(markdown, first_sample) if first_sample else None,
            description=(
                "Delivery Editor introduced factual tokens that were absent from the Analyst draft"
                + (f": {sample_text}." if sample_text else ".")
            ),
            recommendation=(
                "Remove the editor-introduced fact, or route the intended factual addition back through "
                "Analyst and Claim Ledger before editing."
            ),
            category="editorial_governance",
            metadata={
                "analyst_draft_snapshot": ANALYST_DRAFT_SNAPSHOT_FILE,
                "introduced_numbers": samples["numbers"],
                "introduced_claim_ids": samples["claim_ids"],
                "introduced_entities": samples["entities"],
                "ignored_declared_metadata_entities": sorted(allowed_metadata_entities),
                "strict": strict,
            },
        )
    ]


def _final_abstract_quality_findings(
    *,
    markdown: str,
    config: dict[str, Any],
    reader_facing_mode: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Surface deterministic final-quality risks as warning-only findings."""

    stage_id = _stage_or_none(stages, "editor")
    artifact_id = _artifact_or_none(artifacts, "reader_brief" if reader_facing_mode else "audited_brief")
    findings: list[dict[str, Any]] = []

    def append_finding(
        *,
        finding_type: str,
        description: str,
        recommendation: str,
        category: str,
        line_number: int | None = None,
        evidence_ref: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        finding = _finding(
            finding_id=f"QG_FINAL_ABSTRACT_QUALITY_{len(findings)+1:03d}",
            gate_id="final_abstract_quality",
            finding_type=finding_type,
            severity="medium",
            blocking_level="warning",
            repair_owner="none",
            stage_id=stage_id,
            artifact_id=artifact_id,
            description=description,
            recommendation=recommendation,
            category=category,
            line_number=line_number,
            evidence_ref=evidence_ref,
            metadata={
                "semantic_boundary": (
                    "warning_only_deterministic_pattern_surface; not a prose-quality score, "
                    "truth proof, release authority, delivery approval, or repair authority"
                ),
                "repair_boundary": "advisory_non_routable",
                **(metadata or {}),
            },
        )
        finding["repair_stage_id"] = None
        finding["repair_artifact_id"] = None
        findings.append(finding)

    title = _first_markdown_h1(markdown)
    expected_cadence = _configured_report_cadence(config)
    title_cadence = _title_cadence(title)
    if expected_cadence and title_cadence and expected_cadence != title_cadence:
        append_finding(
            finding_type="final_scope_title_mismatch",
            description=(
                f"Report title cadence appears to be {title_cadence}, but workspace config expects "
                f"{expected_cadence}."
            ),
            recommendation="Align the title with the configured report cadence or update config/report_spec before delivery.",
            category="final_abstract_quality",
            line_number=_line_number_for_token(markdown, title) if title else None,
            evidence_ref=title,
            metadata={"expected_cadence": expected_cadence, "title_cadence": title_cadence},
        )

    comparison_line = _first_body_line_matching(markdown, FINAL_ABSTRACT_COMPARISON_RE)
    has_comparison = comparison_line is not None
    if has_comparison and not _has_markdown_heading(markdown, FINAL_ABSTRACT_BASIS_HEADING_RE):
        append_finding(
            finding_type="final_missing_comparison_basis",
            description="The brief uses comparison framing without an explicit basis, method, benchmark, or scope section.",
            recommendation="Add a comparison basis/methodology/scope section or downgrade the comparison wording.",
            category="final_abstract_quality",
            line_number=comparison_line[0],
            evidence_ref=comparison_line[1],
            metadata={"matched_pattern": "comparison_without_basis_section"},
        )

    risk_line = _first_body_line_matching(
        markdown,
        re.compile(
            "|".join(
                [
                    FINAL_ABSTRACT_RECOMMENDATION_RE.pattern,
                    FINAL_ABSTRACT_COMPARISON_RE.pattern,
                    FINAL_ABSTRACT_SUPERLATIVE_RE.pattern,
                ]
            ),
            re.IGNORECASE,
        ),
    )
    if risk_line and not _has_markdown_heading(markdown, FINAL_ABSTRACT_LIMITATION_HEADING_RE):
        append_finding(
            finding_type="final_missing_limitation_section",
            description=(
                "The brief uses recommendation, forecast, comparison, or superlative framing without a "
                "limitations, assumptions, or scope section."
            ),
            recommendation="Add a limitations/scope/assumptions section or soften the abstract framing.",
            category="final_abstract_quality",
            line_number=risk_line[0],
            evidence_ref=risk_line[1],
            metadata={"matched_pattern": "risk_framing_without_limitation_section"},
        )

    for line_number, line in _key_case_lines(markdown):
        missing = [
            field
            for field, pattern in FINAL_ABSTRACT_CASE_FIELD_PATTERNS.items()
            if not pattern.search(line)
        ]
        if missing:
            append_finding(
                finding_type="final_incomplete_key_case_fields",
                description=f"Key-case entry is missing required deterministic fields: {', '.join(missing)}.",
                recommendation="Add date/period, source reference, and impact/relevance fields to key-case entries.",
                category="final_abstract_quality",
                line_number=line_number,
                evidence_ref=line,
                metadata={"missing_fields": missing},
            )

    for line_number, line in _unsupported_superlative_lines(markdown):
        append_finding(
            finding_type="final_unsupported_superlative",
            description="Superlative wording appears without a local citation or source reference on the same line.",
            recommendation="Attach explicit local support for the superlative or downgrade the wording.",
            category="final_abstract_quality",
            line_number=line_number,
            evidence_ref=line,
            metadata={"matched_pattern": "superlative_without_local_reference"},
        )

    return findings


def _configured_report_cadence(config: dict[str, Any]) -> str:
    candidates: list[Any] = []
    report = config.get("report")
    if isinstance(report, dict):
        candidates.append(report.get("cadence"))
    candidates.append(config.get("cadence"))
    for value in candidates:
        normalized = _normalize_cadence(value)
        if normalized:
            return normalized
    return ""


def _normalize_cadence(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"weekly", "week", "周报", "weekly-report"}:
        return "weekly"
    if normalized in {"monthly", "month", "月报", "monthly-report"}:
        return "monthly"
    return ""


def _title_cadence(title: str) -> str:
    lower = title.lower()
    if "weekly" in lower or "周报" in title:
        return "weekly"
    if "monthly" in lower or "月报" in title:
        return "monthly"
    return ""


def _first_markdown_h1(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip(" #")
    return ""


def _has_markdown_heading(markdown: str, pattern: re.Pattern[str]) -> bool:
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        if pattern.search(heading):
            return True
    return False


def _first_body_line_matching(markdown: str, pattern: re.Pattern[str]) -> tuple[int, str] | None:
    for line_number, line in _body_lines(markdown):
        if pattern.search(line):
            return line_number, line
    return None


def _body_lines(markdown: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    in_code_block = False
    in_source_section = False
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            in_source_section = bool(FINAL_ABSTRACT_SOURCE_HEADING_RE.search(heading))
            continue
        if in_source_section or not stripped:
            continue
        lines.append((line_number, stripped))
    return lines


def _key_case_lines(markdown: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    in_case_section = False
    case_level: int | None = None
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        heading_level = _markdown_heading_level(stripped)
        if heading_level is not None:
            heading = stripped.lstrip("#").strip()
            if FINAL_ABSTRACT_CASE_HEADING_RE.search(heading):
                in_case_section = True
                case_level = heading_level
            elif in_case_section and case_level is not None and heading_level <= case_level:
                in_case_section = False
            continue
        if not in_case_section:
            continue
        if re.match(r"^(?:[-*+]|\d+[.)])\s+", stripped):
            results.append((line_number, stripped))
    return results


def _markdown_heading_level(stripped_line: str) -> int | None:
    if not stripped_line.startswith("#"):
        return None
    marker = stripped_line.split(maxsplit=1)[0]
    if set(marker) == {"#"}:
        return len(marker)
    return None


def _unsupported_superlative_lines(markdown: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line in _body_lines(markdown):
        if not FINAL_ABSTRACT_SUPERLATIVE_RE.search(line):
            continue
        if _line_has_local_source_reference(line):
            continue
        findings.append((line_number, line))
        if len(findings) >= 3:
            break
    return findings


def _line_has_local_source_reference(line: str) -> bool:
    return bool(
        SRC_REF_PATTERN.search(line)
        or re.search(r"\[S\d+\]", line)
        or re.search(r"https?://", line, re.IGNORECASE)
    )


def _market_quote_metadata_findings(
    *,
    ledger: ClaimLedger,
    strict: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    start_idx: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    stage_id = _stage_or_none(stages, "claim-ledger")
    artifact_id = _artifact_or_none(artifacts, "claim_ledger")
    for claim in ledger:
        metadata = claim.metadata or {}
        has_quote = any(key in metadata for key in ("ticker", "price", "as_of", "quote_source"))
        if not has_quote:
            continue
        missing = [
            key
            for key in ("ticker", "price", "as_of")
            if metadata.get(key) in {None, ""}
        ]
        if not (metadata.get("source") or metadata.get("quote_source")):
            missing.append("source")
        if not missing:
            continue
        blocking_level = _blocking_level(default_blocking=False, strict=strict)
        findings.append(
            _finding(
                finding_id=f"QG_FRESHNESS_{start_idx + len(findings):03d}",
                gate_id="freshness",
                finding_type="market_quote_metadata_incomplete",
                severity="high",
                blocking_level=blocking_level,
                repair_owner="claim-ledger",
                stage_id=stage_id,
                artifact_id=artifact_id,
                claim_id=claim.claim_id,
                source_id=claim.source_id,
                description=f"Market quote metadata is incomplete for claim {claim.claim_id}: missing {', '.join(missing)}.",
                recommendation="Populate ticker, price, as_of, and source metadata before treating the quote as current.",
                category="stale_source",
                metadata={"missing_fields": missing},
            )
        )
    return findings


def _section_between(content: str, start_patterns: tuple[str, ...]) -> str:
    lines = content.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        lower = line.strip().lower()
        if any(pattern in lower for pattern in start_patterns):
            start_idx = idx + 1
            break
    if start_idx is None:
        return ""
    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        if lines[idx].startswith("## "):
            end_idx = idx
            break
    return "\n".join(lines[start_idx:end_idx])


def _target_terms(config: dict[str, Any], *, user_text: str = "") -> list[str]:
    terms: list[str] = []
    project = config.get("project") or {}
    if isinstance(project, dict):
        for key in ("name", "target", "company", "organization"):
            value = project.get(key)
            if isinstance(value, str) and value.strip():
                terms.append(value.strip())
    for key in ("target", "company", "organization"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    if user_text:
        for marker in ("Target:", "Company:", "Organization:", "目标：", "公司："):
            for line in user_text.splitlines():
                if marker in line:
                    value = line.split(marker, 1)[1].strip(" #:-")
                    if value:
                        terms.append(value)
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        normalized = " ".join(term.split()).lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(term)
    return result


def _declared_metadata_entity_tokens(*, config: dict[str, Any], user_text: str) -> set[str]:
    tokens: set[str] = set()
    for term in _target_terms(config, user_text=user_text):
        tokens.update(_entity_map(term))
    return tokens


def _mentions_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms if term)


def _target_relevance_findings(
    *,
    markdown: str,
    ledger: ClaimLedger,
    config: dict[str, Any],
    user_text: str,
    reader_facing_mode: bool,
    strict: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stage_id = _stage_or_none(stages, "editor")
    artifact_id = _artifact_or_none(artifacts, "audited_brief")
    terms = _target_terms(config, user_text=user_text)
    if not terms:
        blocking_level = "blocking" if strict else "warning"
        return [
            _finding(
                finding_id="QG_TARGET_RELEVANCE_001",
                gate_id="target_relevance",
                finding_type="target_mapping_ambiguous",
                severity="high" if strict else "medium",
                blocking_level=blocking_level,
                repair_owner="human",
                stage_id=stage_id,
                artifact_id=artifact_id,
                description="Target entity or topic could not be derived from workspace config or user context.",
                recommendation="Ask the Orchestrator or human reviewer to clarify the target before enforcing relevance.",
                category="audience_mismatch",
                metadata={"strict": strict},
            )
        ]

    summary = _section_between(markdown, ("executive summary", "摘要", "summary"))
    findings: list[dict[str, Any]] = []
    if strict and not summary:
        findings.append(
            _finding(
                finding_id="QG_TARGET_RELEVANCE_001",
                gate_id="target_relevance",
                finding_type="target_relevance_gap",
                severity="high",
                blocking_level="blocking",
                repair_owner="editor",
                stage_id=stage_id,
                artifact_id=artifact_id,
                description="Executive summary section is missing, so configured target visibility cannot be verified.",
                recommendation="Add an executive summary that makes the configured target visible in reader-facing context.",
                category="audience_mismatch",
                metadata={"target_terms": terms, "strict": strict},
            )
        )
    if summary and not _mentions_any(summary, terms):
        findings.append(
            _finding(
                finding_id=f"QG_TARGET_RELEVANCE_{len(findings)+1:03d}",
                gate_id="target_relevance",
                finding_type="target_relevance_gap",
                severity="high",
                blocking_level="blocking",
                repair_owner="editor",
                stage_id=stage_id,
                artifact_id=artifact_id,
                description="Executive summary does not mention the configured target entity or topic.",
                recommendation="Revise the summary so the target is visible in the reader-facing decision context.",
                category="audience_mismatch",
                metadata={"target_terms": terms, "strict": strict},
            )
        )

    target_claims = [
        claim
        for claim in ledger
        if _mentions_any(f"{claim.statement}\n{claim.evidence_text}", terms)
        and str((claim.metadata or {}).get("importance", "")).lower() in {"high", "critical", "blocking", "direct"}
    ]
    if summary and target_claims and not reader_facing_mode:
        refs = set(SRC_REF_PATTERN.findall(summary))
        if not any(claim.claim_id in refs for claim in target_claims):
            findings.append(
                _finding(
                    finding_id=f"QG_TARGET_RELEVANCE_{len(findings)+1:03d}",
                    gate_id="target_relevance",
                    finding_type="target_priority_claim_missing_from_summary",
                    severity="high",
                    blocking_level="blocking",
                    repair_owner="editor",
                    stage_id=stage_id,
                    artifact_id=artifact_id,
                    claim_id=target_claims[0].claim_id,
                    source_id=target_claims[0].source_id,
                    description="A high-priority target-specific claim is not represented in the executive summary.",
                    recommendation="Include at least one high-priority target-specific claim in the summary or document why it is excluded.",
                    category="coverage_gap",
                    metadata={"target_terms": terms, "target_claim_ids": [claim.claim_id for claim in target_claims]},
                )
            )
    return findings


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


def evaluate_quality_gate_findings(
    *,
    markdown: str,
    ledger: ClaimLedger,
    config: dict[str, Any],
    user_text: str,
    analyst_markdown: str | None,
    report_date: str,
    max_source_age_days: int | None,
    strict: bool,
    reader_facing_mode: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    policy_gate_adapter: dict[str, Any] | None = None,
    coverage_omission_projection: dict[str, Any] | None = None,
    parallel: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Evaluate deterministic quality gates from preloaded inputs without writes.

    The returned mapping is intentionally keyed by gate ID so callers can choose
    their own deterministic aggregation policy. Report writing, event emission,
    and legacy projection updates remain owned by ``check_quality_gates``.
    """

    gate_findings: dict[str, list[dict[str, Any]]] = {gate_id: [] for gate_id in sorted(GATE_IDS)}
    gate_tasks: dict[str, Callable[[], list[dict[str, Any]]]] = {}
    material_fact_strict = policy_gate_is_strict(policy_gate_adapter, "material_fact", cli_strict=strict)
    freshness_strict = policy_gate_is_strict(policy_gate_adapter, "freshness", cli_strict=strict)
    target_relevance_strict = policy_gate_is_strict(policy_gate_adapter, "target_relevance", cli_strict=strict)
    coverage_omission_strict = policy_gate_is_strict(policy_gate_adapter, "coverage_omission", cli_strict=strict)
    coverage_projection = coverage_omission_projection or _coverage_omission_projection(
        workspace=None,
        markdown=markdown,
        ledger=ledger,
        reader_facing_mode=reader_facing_mode,
    )
    gate_tasks["coverage_omission"] = lambda: _coverage_omission_findings(
        projection=coverage_projection,
        strict=coverage_omission_strict,
        stages=stages,
        artifacts=artifacts,
        reader_facing_mode=reader_facing_mode,
    )
    gate_tasks["final_abstract_quality"] = lambda: _final_abstract_quality_findings(
        markdown=markdown,
        config=config,
        reader_facing_mode=reader_facing_mode,
        stages=stages,
        artifacts=artifacts,
    )
    if not reader_facing_mode:
        gate_tasks["material_fact"] = lambda: _material_findings(
            markdown=markdown,
            ledger=ledger,
            strict=material_fact_strict,
            stages=stages,
            artifacts=artifacts,
        )
        gate_tasks["freshness"] = lambda: _freshness_findings(
            markdown=markdown,
            ledger=ledger,
            report_date=report_date,
            max_source_age_days=max_source_age_days,
            strict=freshness_strict,
            stages=stages,
            artifacts=artifacts,
        )
        gate_tasks["editor_new_fact"] = lambda: _editor_introduced_new_fact_findings(
            markdown=markdown,
            analyst_markdown=analyst_markdown,
            config=config,
            user_text=user_text,
            strict=strict,
            stages=stages,
            artifacts=artifacts,
        )
    gate_tasks["target_relevance"] = lambda: _target_relevance_findings(
        markdown=markdown,
        ledger=ledger,
        config=config,
        user_text=user_text,
        reader_facing_mode=reader_facing_mode,
        strict=target_relevance_strict,
        stages=stages,
        artifacts=artifacts,
    )

    if not parallel or len(gate_tasks) <= 1:
        for gate_id in sorted(gate_tasks):
            gate_findings[gate_id] = gate_tasks[gate_id]()
        return gate_findings

    gate_errors: dict[str, str] = {}
    max_workers = min(len(gate_tasks), 4)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mabw-quality-gate") as executor:
        futures = {executor.submit(gate_tasks[gate_id]): gate_id for gate_id in sorted(gate_tasks)}
        for future in as_completed(futures):
            gate_id = futures[future]
            try:
                gate_findings[gate_id] = future.result()
            except Exception as exc:  # pragma: no cover - exercised through monkeypatch tests.
                gate_errors[gate_id] = str(exc)
    if gate_errors:
        raise RuntimeStateError(
            "Quality gate evaluation failed.",
            details={"gate_errors": {gate_id: gate_errors[gate_id] for gate_id in sorted(gate_errors)}},
        )
    return gate_findings


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

    requested_stage_id = stage_id or "auditor"
    default_brief = "output/brief.md" if requested_stage_id == "finalize" else "output/intermediate/audited_brief.md"
    brief_path = _resolve_path(ws, brief, default_brief)
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
    ledger_path = _resolve_path(ws, ledger, "output/intermediate/claim_ledger.json")
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
    claim_support_projection = project_claim_support_matrix_from_workspace(ws)
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
        run_id = _runtime_run_id(workspace=ws, repo_workdir=repo_workdir)
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
                f"multi-agent-brief repair start --workspace {workspace} "
                f"--gate-stage {gate_stage_id} --gate-artifact {gate_artifact_id} --json"
            ),
            f"multi-agent-brief repair complete --workspace {workspace} --reason \"<reason>\" --json",
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
            f"multi-agent-brief state decide --workspace {workspace} --stage <stage> --decision request_human_review --reason \"<reason>\" --json",
            f"multi-agent-brief state decide --workspace {workspace} --stage <stage> --decision block_run --reason \"<reason>\" --json",
        ])
        repair_steps = [
            "This blocking gate requires human review before deterministic repair can proceed.",
            "Use request_human_review or block_run instead of starting owner-stage repair.",
        ]
    else:
        required_commands.extend([
            f"multi-agent-brief state decide --workspace {workspace} --stage <stage> --decision request_human_review --reason \"<reason>\" --json",
            f"multi-agent-brief state decide --workspace {workspace} --stage <stage> --decision block_run --reason \"<reason>\" --json",
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
            f"multi-agent-brief gates check --workspace {workspace} --stage {gate_stage_id} --json",
            f"multi-agent-brief gates show --workspace {workspace} --json",
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
