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
from multi_agent_brief.core.citations import SRC_REF_PATTERN
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    initialize_runtime_state,
    load_artifact_contracts,
    load_stage_specs,
    show_runtime_state,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_FROZEN_GATE_REPORT_ALREADY_EXISTS,
    E_TRANSACTION_INTEGRITY,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
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
GATE_RULE_DOC_ANCHOR = "docs/agent-contract.md#quality-gate-rule-summaries"
GATE_RULES: dict[str, dict[str, str]] = {
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
    artifacts = (state.get("artifact_registry") or {}).get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    record = artifacts.get(artifact_id)
    return record if isinstance(record, dict) and record.get("sha256") else None


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
        repair_stage_id = finding.get("repair_stage_id") or finding.get("stage_id")
        repair_artifact_id = finding.get("repair_artifact_id") or finding.get("artifact_id")
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
        "metadata": metadata or {},
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
    return [
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
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stage_id = _stage_or_none(stages, "editor")
    artifact_id = _artifact_or_none(artifacts, "audited_brief")
    terms = _target_terms(config, user_text=user_text)
    if not terms:
        return [
            _finding(
                finding_id="QG_TARGET_RELEVANCE_001",
                gate_id="target_relevance",
                finding_type="target_mapping_ambiguous",
                severity="medium",
                blocking_level="warning",
                repair_owner="human",
                stage_id=stage_id,
                artifact_id=artifact_id,
                description="Target entity or topic could not be derived from workspace config or user context.",
                recommendation="Ask the Orchestrator or human reviewer to clarify the target before enforcing relevance.",
                category="audience_mismatch",
                metadata={},
            )
        ]

    summary = _section_between(markdown, ("executive summary", "摘要", "summary"))
    findings: list[dict[str, Any]] = []
    if summary and not _mentions_any(summary, terms):
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
                description="Executive summary does not mention the configured target entity or topic.",
                recommendation="Revise the summary so the target is visible in the reader-facing decision context.",
                category="audience_mismatch",
                metadata={"target_terms": terms},
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
    parallel: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Evaluate deterministic quality gates from preloaded inputs without writes.

    The returned mapping is intentionally keyed by gate ID so callers can choose
    their own deterministic aggregation policy. Report writing, event emission,
    and legacy projection updates remain owned by ``check_quality_gates``.
    """

    gate_findings: dict[str, list[dict[str, Any]]] = {gate_id: [] for gate_id in sorted(GATE_IDS)}
    gate_tasks: dict[str, Callable[[], list[dict[str, Any]]]] = {}
    if not reader_facing_mode:
        gate_tasks["material_fact"] = lambda: _material_findings(
            markdown=markdown,
            ledger=ledger,
            strict=strict,
            stages=stages,
            artifacts=artifacts,
        )
        gate_tasks["freshness"] = lambda: _freshness_findings(
            markdown=markdown,
            ledger=ledger,
            report_date=report_date,
            max_source_age_days=max_source_age_days,
            strict=strict,
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
        strict=strict,
        reader_facing_mode=reader_mode,
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
            "max_source_age_days": max_source_age_days,
            "stage_id": gate_stage_id,
            "gate_stage_id": gate_stage_id,
            "gate_artifact_id": gate_artifact_id,
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
    state.update(_blocking_repair_guidance(workspace=ws, validation=validation))
    return state


def validate_quality_gates_workspace(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    return validate_quality_gate_workspace(workspace=ws, stages=stages, artifacts=artifacts)


def _blocking_repair_guidance(*, workspace: Path, validation: dict[str, Any]) -> dict[str, Any]:
    if int(validation.get("blocking_count") or 0) <= 0:
        return {}

    route_command = f"multi-agent-brief repair route --workspace {workspace} --json"
    required_commands = [route_command]
    try:
        from multi_agent_brief.repair.router import route_repair

        repair_route = route_repair(workspace=workspace)
    except Exception as exc:  # pragma: no cover - defensive CLI guidance path.
        repair_route = {
            "ok": False,
            "error_code": "E_REPAIR_ROUTE_UNAVAILABLE",
            "message": str(exc),
            "workspace": str(workspace),
        }

    if repair_route.get("ok") and repair_route.get("repair_owner") != "none":
        required_commands.extend([
            f"multi-agent-brief repair start --workspace {workspace} --json",
            f"multi-agent-brief repair complete --workspace {workspace} --reason \"<reason>\" --json",
        ])
    else:
        required_commands.extend([
            f"multi-agent-brief state decide --workspace {workspace} --stage <stage> --decision request_human_review --reason \"<reason>\" --json",
            f"multi-agent-brief state decide --workspace {workspace} --stage <stage> --decision block_run --reason \"<reason>\" --json",
        ])

    return {
        "required_commands": required_commands,
        "repair_route": repair_route,
    }
