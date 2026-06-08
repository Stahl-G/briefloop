"""Quality-gate report generation and workspace state helpers."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

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
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.quality_gates.contract import (
    GATE_IDS,
    QUALITY_GATE_REPORT_FILE,
    QUALITY_GATE_SCHEMA,
    empty_quality_gate_report,
    load_quality_gate_report,
    quality_gate_paths,
    validate_quality_gate_report_payload,
    validate_quality_gate_workspace,
)


GATE_EVENT_ACTOR = "cli"
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
    analyst_stage = _stage_or_none(stages, "analyst")
    audited_artifact = _artifact_or_none(artifacts, "audited_brief")
    ledger_artifact = _artifact_or_none(artifacts, "claim_ledger")

    stage_id = analyst_stage
    artifact_id = audited_artifact
    repair_owner = "analyst"
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


def _target_terms(workspace: Path, config: dict[str, Any]) -> list[str]:
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
    user_path = workspace / "user.md"
    if user_path.exists():
        try:
            text = user_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for marker in ("Target:", "Company:", "Organization:", "目标：", "公司："):
            for line in text.splitlines():
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


def _mentions_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms if term)


def _target_relevance_findings(
    *,
    workspace: Path,
    markdown: str,
    ledger: ClaimLedger,
    config: dict[str, Any],
    reader_facing_mode: bool,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stage_id = _stage_or_none(stages, "analyst")
    artifact_id = _artifact_or_none(artifacts, "audited_brief")
    terms = _target_terms(workspace, config)
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
                repair_owner="analyst",
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
                    repair_owner="analyst",
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
    return {
        "gate_id": gate_id,
        "status": _gate_status(findings),
        "blocking": any(finding.get("blocking_level") == "blocking" for finding in findings),
        "finding_ids": [str(finding.get("finding_id")) for finding in findings],
    }


def _reader_facing_mode(workspace: Path, brief_path: Path) -> bool:
    return _workspace_relative(workspace, brief_path) == "output/brief.md"


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

    brief_path = _resolve_path(ws, brief, "output/intermediate/audited_brief.md")
    reader_mode = _reader_facing_mode(ws, brief_path)
    gate_stage_id = "finalize" if reader_mode else (stage_id or "auditor")
    gate_artifact_id = "reader_brief" if reader_mode else "quality_gate_report"
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
    report_date, max_source_age_days = _config_report_defaults(
        config,
        report_date=report_date,
        max_source_age_days=max_source_age_days,
    )

    gate_findings: dict[str, list[dict[str, Any]]] = {gate_id: [] for gate_id in GATE_IDS}
    if not reader_mode:
        gate_findings["material_fact"] = _material_findings(
            markdown=markdown,
            ledger=claim_ledger,
            strict=strict,
            stages=stages,
            artifacts=artifacts,
        )
        gate_findings["freshness"] = _freshness_findings(
            markdown=markdown,
            ledger=claim_ledger,
            report_date=report_date,
            max_source_age_days=max_source_age_days,
            strict=strict,
            stages=stages,
            artifacts=artifacts,
        )
    gate_findings["target_relevance"] = _target_relevance_findings(
        workspace=ws,
        markdown=markdown,
        ledger=claim_ledger,
        config=config,
        reader_facing_mode=reader_mode,
        stages=stages,
        artifacts=artifacts,
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

    report_path = quality_gate_paths(ws)["quality_gate_report"]
    old_report = report_path.read_bytes() if report_path.exists() else None
    wrote_report = False
    try:
        _write_json_atomic(report_path, payload)
        wrote_report = True
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
    validation = validate_quality_gates_workspace(
        workspace=ws,
        repo_workdir=repo_workdir,
    )
    return {
        "ok": bool(validation.get("ok")),
        "workspace": str(ws),
        "quality_gate_state_files": {"quality_gate_report": QUALITY_GATE_REPORT_FILE},
        "quality_gate_report": report or empty_quality_gate_report(),
        "validation": validation,
    }


def validate_quality_gates_workspace(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _repo, stages, artifacts = _contracts(workspace=ws, repo_workdir=repo_workdir)
    return validate_quality_gate_workspace(workspace=ws, stages=stages, artifacts=artifacts)
