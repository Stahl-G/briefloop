"""Completion-gate orchestration helpers for Orchestrator runtime state."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_IDS,
    evaluate_workspace_agent_artifact_intakes,
)

import yaml

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.delivery.artifact_policy import (
    reader_delivery_artifact_kind,
    reader_delivery_artifact_policy_text,
)
from multi_agent_brief.quality_gates.contract import (
    interpret_quality_gate_binding,
    require_quality_gate_binding_pass,
)
from multi_agent_brief.orchestrator.role_topology import (
    resolve_role_topology,
    stage_satisfaction_rules_for_topology,
)
from multi_agent_brief.orchestrator.runtime_state._io import (
    _load_workspace_yaml,
    _read_json,
    _sha256_file,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    agent_artifact_paths_from_contracts,
    artifact_path_from_contracts,
    artifact_paths_from_contracts,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_INVALID,
    ARTIFACT_VALID,
    _validate_artifact,
)
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.identity import utc_now
from multi_agent_brief.orchestrator.source_evidence import is_evidence_input_path
from multi_agent_brief.outputs.finalize import (
    interpret_finalize_audit_binding,
    require_finalize_audit_binding_pass,
)
from multi_agent_brief.outputs.reader_final_gate import (
    combine_reader_final_gate_results,
    detect_reader_residue,
    detect_reader_residue_in_docx,
)
from multi_agent_brief.product.policy_gate_adapter import (
    policy_forbidden_phrases,
    resolve_workspace_policy_gate_adapter,
)


def _role_topology_from_policy_pack(policy_pack: dict[str, Any] | None) -> str:
    """Return the configured role topology for Layer-D gate logic."""

    return resolve_role_topology(policy_pack)


def _topology_satisfaction_rules(
    *,
    stages: list[dict[str, Any]],
    policy_pack: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Return declared satisfaction hooks without applying stage progress."""

    return stage_satisfaction_rules_for_topology(stages=stages, policy_pack=policy_pack)


def _completion_artifact_gate_reasons(
    *,
    workspace: Path,
    stage: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    return _artifact_gate_reasons_for_ids(
        workspace=workspace,
        artifact_ids=[str(item) for item in (stage.get("expected_artifacts") or [])],
        artifacts_by_id=artifacts_by_id,
        reason_prefix="Required expected artifact",
        optional_prefix="Optional expected artifact",
    )


def _topology_satisfaction_artifact_reasons(
    *,
    workspace: Path,
    stage_id: str,
    rule: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    artifact_ids = [
        str(item)
        for item in (rule.get("required_artifacts") or [])
        if item
    ]
    return _artifact_gate_reasons_for_ids(
        workspace=workspace,
        artifact_ids=artifact_ids,
        artifacts_by_id=artifacts_by_id,
        reason_prefix=f"Required topology artifact for stage '{stage_id}'",
        optional_prefix=f"Optional topology artifact for stage '{stage_id}'",
    )


def _artifact_gate_reasons_for_ids(
    *,
    workspace: Path,
    artifact_ids: list[str],
    artifacts_by_id: dict[str, dict[str, Any]],
    reason_prefix: str,
    optional_prefix: str,
) -> list[str]:
    reasons: list[str] = []
    resolved_artifact_paths = artifact_paths_from_contracts(
        workspace,
        artifacts_by_id,
    )
    intake_bundle = (
        evaluate_workspace_agent_artifact_intakes(
            workspace,
            artifact_paths=agent_artifact_paths_from_contracts(
                workspace,
                artifacts_by_id,
            ),
        )
        if any(artifact_id in AGENT_ARTIFACT_IDS for artifact_id in artifact_ids)
        else None
    )
    for artifact_id in artifact_ids:
        contract = artifacts_by_id.get(str(artifact_id))
        if not contract:
            reasons.append(f"{reason_prefix} '{artifact_id}' is not declared in artifact contracts.")
            continue
        rel_path = str(contract.get("path") or "")
        fmt = str(contract.get("format") or "")
        artifact_path = artifact_path_from_contracts(
            workspace,
            artifacts_by_id,
            artifact_id=str(artifact_id),
        )
        if artifact_path is None:
            reasons.append(
                f"{reason_prefix} '{artifact_id}' has no configured artifact path."
            )
            continue
        status, validation_result = _validate_artifact(
            artifact_path,
            fmt,
            str(artifact_id),
            workspace=workspace,
            intake_result=(
                intake_bundle.get(artifact_id)
                if intake_bundle is not None and artifact_id in AGENT_ARTIFACT_IDS
                else None
            ),
            artifact_paths=resolved_artifact_paths,
        )
        required = bool(contract.get("required", False))
        if required and status != ARTIFACT_VALID:
            reasons.append(
                f"{reason_prefix} '{artifact_id}' at '{rel_path}' is {status} ({validation_result})."
            )
        elif not required and status == ARTIFACT_INVALID:
            reasons.append(
                f"{optional_prefix} '{artifact_id}' at '{rel_path}' is invalid ({validation_result})."
            )
    return reasons


def _count_evidence_files(path: Path, workspace: Path) -> int:
    if not path.exists() or not is_evidence_input_path(path, workspace):
        return 0
    if path.is_file():
        return 1
    if path.is_dir():
        return sum(
            1
            for item in path.rglob("*")
            if item.is_file() and is_evidence_input_path(item, workspace)
        )
    return 0


def _configured_evidence_source_count(sources: dict[str, Any], workspace: Path) -> int:
    count = 0
    manual = sources.get("manual") if isinstance(sources.get("manual"), dict) else {}
    manual_sources = manual.get("sources") if isinstance(manual.get("sources"), list) else []
    for item in manual_sources:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        if item.get("url"):
            count += 1
            continue
        raw_path = item.get("path")
        if raw_path:
            source_path = Path(str(raw_path))
            if not source_path.is_absolute():
                source_path = workspace / source_path
            count += _count_evidence_files(source_path, workspace)

    rss = sources.get("rss") if isinstance(sources.get("rss"), dict) else {}
    feeds = rss.get("feeds") if isinstance(rss.get("feeds"), list) else []
    count += len([
        item
        for item in feeds
        if isinstance(item, dict) and item.get("enabled", True) and item.get("url")
    ])

    cached = sources.get("cached_package") if isinstance(sources.get("cached_package"), dict) else {}
    if cached.get("enabled"):
        paths = cached.get("paths") if isinstance(cached.get("paths"), list) else []
        for raw_path in paths:
            source_path = Path(str(raw_path))
            if not source_path.is_absolute():
                source_path = workspace / source_path
            count += _count_evidence_files(source_path, workspace)

    filing_resolver = (
        sources.get("filing_resolver")
        if isinstance(sources.get("filing_resolver"), dict)
        else {}
    )
    if filing_resolver.get("enabled"):
        tickers = filing_resolver.get("tickers")
        if isinstance(tickers, list):
            count += len([item for item in tickers if item])

    feishu = sources.get("feishu") if isinstance(sources.get("feishu"), dict) else {}
    if feishu.get("enabled"):
        feishu_sources = feishu.get("sources")
        if isinstance(feishu_sources, list):
            count += len([item for item in feishu_sources if item])

    mcp = sources.get("mcp") if isinstance(sources.get("mcp"), dict) else {}
    if mcp.get("enabled"):
        servers = mcp.get("servers")
        if isinstance(servers, list):
            count += len([item for item in servers if item])

    input_dir = workspace / "input"
    if input_dir.exists():
        count += sum(
            1
            for item in input_dir.rglob("*")
            if item.is_file() and is_evidence_input_path(item, workspace)
        )
    return count


def _runtime_search_observation_counts(path: Path) -> tuple[bool, list[int]]:
    if not path.exists():
        return False, []
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError):
        return False, []
    if "Did 0 searches" in text:
        return True, [0]
    if not isinstance(data, dict):
        return False, []

    counts: list[int] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            lowered = {str(key).lower(): val for key, val in value.items()}
            for key in ("result_count", "results_count", "search_count", "observation_count"):
                if key in lowered:
                    try:
                        counts.append(int(lowered[key]))
                    except (TypeError, ValueError):
                        pass
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return bool(counts), counts


def _contains_zero_runtime_search_observation(path: Path) -> bool:
    has_observation, counts = _runtime_search_observation_counts(path)
    return has_observation and counts and all(count == 0 for count in counts)


def _source_candidates_is_plan_only(path: Path) -> bool:
    if not path.exists():
        return False
    data = _load_workspace_yaml(path)
    artifact_type = str(data.get("artifact_type") or "")
    evidence_status = str(data.get("evidence_status") or "")
    return artifact_type == "source_plan_only" or evidence_status == "not_evidence"


def _source_discovery_evidence_reasons(workspace: Path) -> list[str]:
    sources = _load_workspace_yaml(workspace / "sources.yaml")
    web_search = sources.get("web_search") if isinstance(sources.get("web_search"), dict) else {}
    web_search_enabled = web_search.get("enabled") is True
    web_search_mode = str(web_search.get("mode") or "")
    candidates_path = workspace / "source_candidates.yaml"
    evidence_count = _configured_evidence_source_count(sources, workspace)
    has_evidence = evidence_count > 0

    reasons: list[str] = []
    if (
        web_search_enabled
        and web_search_mode == "runtime_tool"
        and _contains_zero_runtime_search_observation(candidates_path)
    ):
        reasons.append(
            "Runtime WebSearch source discovery reported zero searches or zero observations; request human review instead of completing source-discovery."
        )

    if has_evidence:
        return reasons

    if _source_candidates_is_plan_only(candidates_path):
        reasons.append(
            "source_candidates.yaml is a source plan, not evidence; materialize approved sources into input/sources/ or supported source configuration before completing source-discovery."
        )
    if web_search_enabled and web_search_mode == "configure_later":
        reasons.append(
            "Cannot complete source-discovery: web_search.mode is configure_later, and no durable evidence source is available."
        )
    if web_search_enabled and web_search_mode == "runtime_tool":
        reasons.append(
            "Cannot complete source-discovery: runtime_tool web search is enabled, but no evidence source is available. Runtime WebSearch results must be written as durable source files under input/sources/ or into supported source configuration. source_candidates.yaml is a source plan, not evidence."
        )
    return reasons


def _stage_quality_gate_pass_reasons(
    *,
    workspace: Path,
    stage_id: str,
    expected_brief: str,
    expected_ledger: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    verdict = interpret_quality_gate_binding(
        workspace=workspace,
        stage_id=stage_id,
        expected_brief=expected_brief,
        expected_ledger=expected_ledger,
        stages=stages,
        artifacts=artifacts,
    )
    return require_quality_gate_binding_pass(verdict)


def _quality_gate_pass_reasons(
    *,
    workspace: Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    expected_ledger = _contract_claim_ledger_path(workspace, artifacts)
    return _stage_quality_gate_pass_reasons(
        workspace=workspace,
        stage_id="auditor",
        expected_brief="output/intermediate/audited_brief.md",
        expected_ledger=expected_ledger,
        stages=stages,
        artifacts=artifacts,
    )


def _finalize_quality_gate_pass_reasons(
    *,
    workspace: Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    expected_ledger = _contract_claim_ledger_path(workspace, artifacts)
    return _stage_quality_gate_pass_reasons(
        workspace=workspace,
        stage_id="finalize",
        expected_brief="output/brief.md",
        expected_ledger=expected_ledger,
        stages=stages,
        artifacts=artifacts,
    )


def _contract_claim_ledger_path(
    workspace: Path,
    artifacts: list[dict[str, Any]],
) -> str:
    artifacts_by_id = {
        str(artifact.get("artifact_id")): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
    ledger_path = artifact_path_from_contracts(
        workspace,
        artifacts_by_id,
        artifact_id="claim_ledger",
    )
    if ledger_path is None:
        raise RuntimeStateError("Claim Ledger artifact contract path is required.")
    return ledger_path.relative_to(workspace).as_posix()


def _resolve_report_artifact_path(workspace: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _finalize_report_reader_artifact_paths(workspace: Path, report: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    required_brief = workspace / "output" / "brief.md"
    paths.append(required_brief.resolve())
    for key in ("reader_brief", "named_reader_brief", "reader_docx", "named_reader_docx", "source_appendix"):
        path = _resolve_report_artifact_path(workspace, report.get(key))
        if path is not None:
            paths.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        marker = str(path)
        if marker not in seen:
            seen.add(marker)
            unique.append(path)
    return unique


def _finalize_report_auxiliary_artifact_reasons(workspace: Path, report: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("source_appendix_trace",):
        path = _resolve_report_artifact_path(workspace, report.get(key))
        if path is None:
            continue
        if not path.exists():
            reasons.append(f"finalize_report.json references missing auxiliary artifact {key}: {path}.")
    return reasons


def _finalize_report_delivery_artifact_reasons(workspace: Path, report: dict[str, Any]) -> list[str]:
    artifacts = report.get("delivery_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return ["finalize_report.json delivery_artifacts must list the reader delivery bundle."]
    hashes = report.get("delivery_artifact_sha256")
    if not isinstance(hashes, dict) or not hashes:
        return ["finalize_report.json delivery_artifact_sha256 is required before finalize-complete."]
    reasons: list[str] = []
    delivery_root = (workspace / "output" / "delivery").resolve()
    for item in artifacts:
        path = _resolve_report_artifact_path(workspace, item)
        if path is None:
            reasons.append("finalize_report.json contains an invalid delivery_artifacts entry.")
            continue
        if not path.exists():
            reasons.append(f"finalize_report.json references missing delivery artifact: {path}.")
            continue
        if not path.is_file():
            reasons.append(f"finalize_report.json references non-file delivery artifact: {path}.")
            continue
        try:
            path.relative_to(delivery_root)
        except ValueError:
            reasons.append(
                "finalize_report.json delivery_artifacts may only reference files under output/delivery."
            )
            continue
        rel = path.resolve().relative_to(workspace.resolve()).as_posix()
        if not reader_delivery_artifact_kind(path):
            reasons.append(
                f"finalize_report.json references unsupported delivery artifact: {rel}; "
                f"{reader_delivery_artifact_policy_text()}."
            )
            continue
        expected_hash = _hash_for_report_artifact(
            hashes,
            raw_path=item,
            workspace=workspace,
            resolved=path,
        )
        if not expected_hash:
            reasons.append(f"finalize_report.json delivery artifact hash missing: {rel}.")
            continue
        try:
            actual_hash = _sha256_file(path)
        except OSError as exc:
            reasons.append(f"finalize_report.json delivery artifact could not be read: {rel}: {exc}.")
            continue
        if actual_hash != expected_hash:
            reasons.append(f"finalize_report.json delivery artifact hash mismatch: {rel}.")
    return reasons


def _hash_for_report_artifact(
    hashes: dict[str, Any],
    *,
    raw_path: str,
    workspace: Path,
    resolved: Path,
) -> str:
    rel = resolved.resolve().relative_to(workspace.resolve()).as_posix()
    for key in (raw_path, rel, resolved.as_posix(), str(resolved)):
        value = hashes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_control_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt, width in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:width], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _fast_rerun_report_freshness_settings(workspace: Path) -> dict[str, Any]:
    config = _load_workspace_yaml(workspace / "config.yaml")
    report = config.get("report") if isinstance(config.get("report"), dict) else {}
    report_date = str(report.get("date") or "")
    max_age_raw = report.get("max_source_age_days")
    max_source_age_days: int | None = None
    if max_age_raw is not None:
        try:
            max_source_age_days = int(max_age_raw)
        except (TypeError, ValueError):
            max_source_age_days = None
    return {
        "report_date": report_date,
        "max_source_age_days": max_source_age_days,
        "fail_on_stale_source": report.get("fail_on_stale_source"),
    }


def _fast_rerun_import_freshness_snapshot(workspace: Path, *, checked_at: str) -> dict[str, Any]:
    settings = _fast_rerun_report_freshness_settings(workspace)
    ledger_path = workspace / "output" / "intermediate" / "claim_ledger.json"
    report_day = _parse_control_date(settings["report_date"])
    max_age = settings["max_source_age_days"]
    dates: list[dict[str, Any]] = []
    missing_date_claim_ids: list[str] = []
    stale_claims: list[dict[str, Any]] = []
    try:
        ledger = ClaimLedger.import_json(ledger_path)
    except Exception as exc:
        return {
            "schema_version": "mabw.fact_layer_import.freshness.v1",
            "checked_at": checked_at,
            "status": "unknown",
            "reason": "claim_ledger_unreadable",
            "error": str(exc),
            **settings,
        }

    for claim in ledger:
        date_text = str(
            claim.metadata.get("published_at")
            or claim.metadata.get("publication_date")
            or ""
        )
        source_day = _parse_control_date(date_text)
        if source_day is None:
            missing_date_claim_ids.append(claim.claim_id)
            continue
        dates.append({
            "claim_id": claim.claim_id,
            "source_date": source_day.isoformat(),
            "source_date_text": date_text,
        })
        if report_day is not None and max_age is not None:
            age_days = (report_day - source_day).days
            if age_days > max_age:
                stale_claims.append({
                    "claim_id": claim.claim_id,
                    "source_date": source_day.isoformat(),
                    "age_days": age_days,
                })

    if report_day is None or max_age is None:
        status = "unknown"
        reason = "report_date_or_max_source_age_missing"
    elif missing_date_claim_ids:
        status = "unknown"
        reason = "claim_publication_dates_missing"
    elif stale_claims:
        status = "stale"
        reason = "source_age_exceeds_target_window"
    else:
        status = "within_window"
        reason = ""

    sorted_dates = sorted(item["source_date"] for item in dates)
    return {
        "schema_version": "mabw.fact_layer_import.freshness.v1",
        "checked_at": checked_at,
        "status": status,
        "reason": reason,
        "report_date": settings["report_date"],
        "max_source_age_days": max_age,
        "fail_on_stale_source": settings["fail_on_stale_source"],
        "claim_count": len(ledger),
        "dated_claim_count": len(dates),
        "missing_date_claim_count": len(missing_date_claim_ids),
        "missing_date_claim_ids": missing_date_claim_ids[:10],
        "oldest_source_date": sorted_dates[0] if sorted_dates else "",
        "newest_source_date": sorted_dates[-1] if sorted_dates else "",
        "stale_claim_count": len(stale_claims),
        "stale_claims": stale_claims[:10],
    }


def _fast_rerun_finalize_freshness_snapshot(
    workspace: Path,
    manifest: dict[str, Any],
    *,
    checked_at: str,
) -> dict[str, Any]:
    record = manifest.get("fact_layer_import") if isinstance(manifest.get("fact_layer_import"), dict) else None
    if not record:
        return {}
    return _fast_rerun_import_freshness_snapshot(workspace, checked_at=checked_at)


def _fast_rerun_import_freshness_reasons(snapshot: dict[str, Any]) -> list[str]:
    if not snapshot:
        return []
    status = snapshot.get("status")
    if status == "within_window":
        return []
    stale_claims = snapshot.get("stale_claims") if isinstance(snapshot.get("stale_claims"), list) else []
    sample = ", ".join(
        f"{item.get('claim_id')} ({item.get('age_days')}d)"
        for item in stale_claims[:5]
        if isinstance(item, dict)
    )
    if status == "stale":
        return [
            "Fast-rerun imported fact layer is stale at target delivery time "
            f"(report_date={snapshot.get('report_date')}, "
            f"max_source_age_days={snapshot.get('max_source_age_days')}, "
            f"stale_claim_count={snapshot.get('stale_claim_count')}"
            + (f", sample={sample}" if sample else "")
            + ")."
        ]
    return [
        "Fast-rerun imported fact layer freshness cannot be verified at target delivery time "
        f"(status={status or 'unknown'}, reason={snapshot.get('reason') or 'unknown'}, "
        f"report_date={snapshot.get('report_date')}, max_source_age_days={snapshot.get('max_source_age_days')}"
        + ")."
    ]


def _finalize_completion_reasons(
    workspace: Path,
    *,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    runtime_manifest: dict[str, Any] | None = None,
    fast_rerun_freshness_at_finalize: dict[str, Any] | None = None,
) -> list[str]:
    reasons: list[str] = []
    reasons.extend(
        _finalize_quality_gate_pass_reasons(
            workspace=workspace,
            stages=stages,
            artifacts=artifacts,
        )
    )
    report_path = workspace / "output" / "intermediate" / "finalize_report.json"
    if not report_path.exists():
        reasons.append("finalize_report.json is required before finalize-complete.")
        return reasons
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"finalize_report.json is invalid JSON: {exc}"]
    except OSError as exc:
        return [f"finalize_report.json could not be read: {exc}"]
    if not isinstance(report, dict):
        return ["finalize_report.json must contain an object."]
    if report.get("status") != "pass":
        reasons.append("finalize_report.json status must be pass.")
    reader_clean = report.get("reader_clean")
    if not isinstance(reader_clean, dict) or reader_clean.get("status") != "pass":
        reasons.append("finalize_report.json reader_clean.status must be pass.")
    reasons.extend(
        require_finalize_audit_binding_pass(
            interpret_finalize_audit_binding(
                workspace=workspace,
                finalize_report=report,
            )
        )
    )
    reasons.extend(_finalize_report_delivery_artifact_reasons(workspace, report))
    reasons.extend(_finalize_report_auxiliary_artifact_reasons(workspace, report))
    if runtime_manifest is None:
        manifest_path = workspace / "output" / "intermediate" / "runtime_manifest.json"
        try:
            runtime_manifest = _read_json(manifest_path)
        except RuntimeStateError as exc:
            reasons.append(f"runtime_manifest.json could not be read for fast-rerun freshness check: {exc}")
            runtime_manifest = None
    if runtime_manifest is not None:
        if fast_rerun_freshness_at_finalize is None:
            fast_rerun_freshness_at_finalize = _fast_rerun_finalize_freshness_snapshot(
                workspace,
                runtime_manifest,
                checked_at=utc_now(),
            )
        reasons.extend(_fast_rerun_import_freshness_reasons(fast_rerun_freshness_at_finalize))

    artifact_paths = _finalize_report_reader_artifact_paths(workspace, report)
    missing = [path for path in artifact_paths if not path.exists()]
    if missing:
        reasons.append(
            "finalize_report.json references missing reader artifacts: "
            + ", ".join(str(path) for path in missing)
        )
        return reasons

    gate_results = []
    forbidden_phrases = policy_forbidden_phrases(resolve_workspace_policy_gate_adapter(workspace))
    for path in artifact_paths:
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            try:
                gate_results.append(
                    detect_reader_residue(
                        path.read_text(encoding="utf-8"),
                        artifact=str(path),
                        forbidden_phrases=forbidden_phrases,
                    )
                )
            except OSError as exc:
                reasons.append(f"Reader artifact could not be read: {path}: {exc}")
        elif suffix == ".docx":
            gate_results.append(
                detect_reader_residue_in_docx(path, artifact=str(path), forbidden_phrases=forbidden_phrases)
            )
    if gate_results:
        reader_gate = combine_reader_final_gate_results(gate_results)
        if reader_gate.status == "fail":
            reasons.append(
                "Current reader artifacts fail reader final gate: "
                f"{sum(reader_gate.counts.values())} residue findings."
            )
    return reasons


def _raise_completion_reasons(
    *,
    message: str,
    reasons: list[str],
    error_code: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload = dict(details or {})
    payload["blocking_reasons"] = reasons
    raise RuntimeStateError(
        f"{message}: {' '.join(reasons)}",
        details=payload,
        error_code=error_code,
    )
