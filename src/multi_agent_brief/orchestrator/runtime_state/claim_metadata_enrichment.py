"""Claim metadata enrichment transaction.

Enriches frozen Claim Ledger entries with provenance metadata from durable
source-evidence files, under single-writer authority rules. Never alters
claim text or claim IDs.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.source_metadata import (
    normalize_retrieval_source_type,
    normalize_source_category,
    normalize_underlying_evidence_type,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state._io import (
    _restore_state_files,
    _sha256_file,
    _snapshot_state_files,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state._transactions import (
    _current_run_start_event_exists,
    _load_manifest_and_workflow,
    _preflight_transaction_files,
    _restore_file_paths,
    _sha256_bytes,
    _snapshot_file_paths,
    _write_bytes_atomic,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_VALID,
    _build_artifact_registry,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    artifact_path_from_contracts,
    workspace_artifact_path,
)
from multi_agent_brief.orchestrator.runtime_state.claim_ledger_freeze import (
    CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS,
    CLAIM_LEDGER_PATH,
    _claim_ledger_bytes,
    _claim_ledger_freeze_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _raise_completion_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _stage_ids,
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ARTIFACT_INVALID,
    E_ILLEGAL_TRANSITION,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    append_event,
    read_event_log_records_strict,
)
from multi_agent_brief.orchestrator.runtime_state.fact_layer import (
    FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID,
    _target_workspace_path,
)
from multi_agent_brief.orchestrator.runtime_state.identity import utc_now
from multi_agent_brief.orchestrator.runtime_state.lifecycle import show_runtime_state
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    STAGE_COMPLETE,
    STAGE_SKIPPED,
    _stage_status,
    _workflow_is_finalized,
)


CLAIM_METADATA_ENRICHMENT_ALLOWED_FIELDS = CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS


CLAIM_METADATA_ENRICHMENT_FORBIDDEN_FIELDS = (
    "claim_id",
    "statement",
    "evidence_text",
    "source_id",
    "source_url",
    "claim_type",
    "support_strength",
    "confidence",
)


CLAIM_LEDGER_METADATA_ENRICHMENT_SCHEMA = "mabw.claim_ledger_metadata_enrichment.v1"


CLAIM_METADATA_REPLACEABLE_DEFAULTS = {
    "retrieval_source_type": {"other"},
    "source_category": {"other"},
    "underlying_evidence_type": {"unknown"},
}


def _imported_claim_ledger_record(
    manifest: dict[str, Any],
    *,
    claim_ledger_path: str = CLAIM_LEDGER_PATH.as_posix(),
) -> dict[str, Any] | None:
    import_record = manifest.get("fact_layer_import")
    if not isinstance(import_record, dict):
        return None
    imported_files = import_record.get("imported_files")
    if not isinstance(imported_files, list):
        return None
    for record in imported_files:
        if not isinstance(record, dict):
            continue
        if (
            record.get("artifact_id") == "claim_ledger"
            and str(record.get("workspace_path") or "") == claim_ledger_path
        ):
            return record
    return None


def _valid_imported_claim_ledger_derivation(
    *,
    manifest: dict[str, Any],
    import_record: dict[str, Any],
    current_sha256: str,
    claim_ledger_path: str = CLAIM_LEDGER_PATH.as_posix(),
) -> bool:
    enrichment = manifest.get("claim_ledger_metadata_enrichment")
    if not isinstance(enrichment, dict):
        return False
    imported_sha256 = import_record.get("sha256")
    derives_from_imported_sha = (
        enrichment.get("source_claim_ledger_sha256") == imported_sha256
        or enrichment.get("previous_claim_ledger_sha256") == imported_sha256
    )
    return (
        enrichment.get("schema_version") == CLAIM_LEDGER_METADATA_ENRICHMENT_SCHEMA
        and enrichment.get("status") == "applied"
        and enrichment.get("claim_ledger_path") == claim_ledger_path
        and derives_from_imported_sha
        and enrichment.get("claim_ledger_sha256") == current_sha256
    )


def _claim_ledger_enrichment_authority(
    *,
    workspace: Path,
    manifest: dict[str, Any],
    ledger_path: Path,
) -> dict[str, str]:
    freeze = manifest.get("claim_ledger_freeze")
    if isinstance(freeze, dict):
        freeze_reasons = _claim_ledger_freeze_reasons(
            workspace=workspace, manifest=manifest
        )
        if freeze_reasons:
            _raise_completion_reasons(
                message="Cannot enrich Claim Ledger metadata before Claim Ledger freeze is valid",
                reasons=freeze_reasons,
                error_code=E_TRANSACTION_INTEGRITY,
                details={"stage_id": "claim-ledger"},
            )
        return {
            "kind": "claim_ledger_freeze",
            "source_claim_ledger_sha256": str(freeze.get("claim_ledger_sha256") or ""),
        }
    if freeze is not None:
        raise RuntimeStateError(
            "Claim Ledger freeze metadata is malformed; refusing metadata enrichment.",
            details={"field": "claim_ledger_freeze"},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    claim_ledger_path = _workspace_relative(workspace, ledger_path)
    import_record = _imported_claim_ledger_record(
        manifest,
        claim_ledger_path=claim_ledger_path,
    )
    if not isinstance(import_record, dict):
        raise RuntimeStateError(
            "Claim metadata enrichment requires a frozen Claim Ledger from local freeze or fact-layer import.",
            details={"required_authority": "claim_ledger_freeze_or_fact_layer_import"},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if not ledger_path.exists() or not ledger_path.is_file():
        raise RuntimeStateError(
            "Imported Claim Ledger is missing; refusing metadata enrichment.",
            details={"workspace_path": CLAIM_LEDGER_PATH.as_posix()},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    expected_sha = str(import_record.get("sha256") or "")
    expected_size = import_record.get("size_bytes")
    current_sha = _sha256_file(ledger_path)
    current_size = ledger_path.stat().st_size
    if current_sha == expected_sha and (
        not isinstance(expected_size, int) or current_size == expected_size
    ):
        return {
            "kind": "fact_layer_import",
            "source_claim_ledger_sha256": expected_sha,
        }
    if _valid_imported_claim_ledger_derivation(
        manifest=manifest,
        import_record=import_record,
        current_sha256=current_sha,
        claim_ledger_path=claim_ledger_path,
    ):
        return {
            "kind": "fact_layer_import_derived",
            "source_claim_ledger_sha256": expected_sha,
        }
    raise RuntimeStateError(
        "Current Claim Ledger does not match imported fact-layer authority.",
        details={
            "workspace_path": CLAIM_LEDGER_PATH.as_posix(),
            "expected_sha256": expected_sha,
            "actual_sha256": current_sha,
            "expected_size_bytes": expected_size,
            "actual_size_bytes": current_size,
        },
        error_code=E_TRANSACTION_INTEGRITY,
    )


def _imported_source_evidence_authority(
    manifest: dict[str, Any], *, workspace: Path
) -> dict[str, dict[str, Any]]:
    import_record = manifest.get("fact_layer_import")
    if not isinstance(import_record, dict):
        raise RuntimeStateError(
            "Claim metadata enrichment requires imported frozen source evidence.",
            details={"required_manifest_field": "fact_layer_import"},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    imported_files = import_record.get("imported_files")
    if not isinstance(imported_files, list):
        raise RuntimeStateError(
            "Fact layer import metadata is missing imported_files.",
            details={"required_manifest_field": "fact_layer_import.imported_files"},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    by_source_id: dict[str, dict[str, Any]] = {}
    for record in imported_files:
        if not isinstance(record, dict):
            continue
        if record.get("artifact_id") != FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID:
            continue
        workspace_path = str(record.get("workspace_path") or "")
        if not workspace_path.startswith("input/sources/"):
            continue
        source_path = _target_workspace_path(workspace, workspace_path)
        if not source_path.exists() or not source_path.is_file():
            raise RuntimeStateError(
                "Imported source evidence file is missing; refusing metadata enrichment.",
                details={"workspace_path": workspace_path},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        expected_sha = str(record.get("sha256") or "")
        actual_sha = _sha256_file(source_path)
        if not expected_sha or actual_sha != expected_sha:
            raise RuntimeStateError(
                "Imported source evidence hash does not match fact_layer_import metadata.",
                details={
                    "workspace_path": workspace_path,
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        if isinstance(
            record.get("size_bytes"), int
        ) and source_path.stat().st_size != record.get("size_bytes"):
            raise RuntimeStateError(
                "Imported source evidence size does not match fact_layer_import metadata.",
                details={
                    "workspace_path": workspace_path,
                    "expected_size_bytes": record.get("size_bytes"),
                    "actual_size_bytes": source_path.stat().st_size,
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        metadata = _source_evidence_metadata_from_file(
            source_path, workspace_path=workspace_path
        )
        if not metadata:
            continue
        source_ids = _source_evidence_ids(source_path, metadata)
        authority = {
            "workspace_path": workspace_path,
            "sha256": actual_sha,
            "metadata": metadata,
        }
        by_source_id.setdefault(workspace_path, authority)
        for source_id in source_ids:
            by_source_id.setdefault(source_id, authority)

    return by_source_id


def _source_evidence_ids(path: Path, metadata: dict[str, str]) -> set[str]:
    ids: set[str] = set()
    for key in ("source_id", "id"):
        value = metadata.get(key)
        if value:
            ids.add(value)
    stem = path.stem.strip()
    if stem:
        ids.add(stem)
        ids.add(stem.upper().replace("-", "_"))
        ids.add(stem.upper().replace("_", "-"))
        normalized_stem = stem.lower().replace("_", "-")
        match = re.fullmatch(r"(?:source|src)-?([0-9]+)", normalized_stem)
        if match:
            numeric_id = match.group(1)
            ids.add(f"SRC-{numeric_id}")
            ids.add(f"SRC-{numeric_id.zfill(3)}")
    return {item for item in ids if item}


def _source_evidence_metadata_from_file(
    path: Path, *, workspace_path: str
) -> dict[str, str]:
    suffix = path.suffix.lower()
    if suffix not in {".json", ".md", ".markdown"}:
        return {"source_path": workspace_path}
    if suffix in {".md", ".markdown"}:
        return _source_evidence_metadata_from_markdown(
            path, workspace_path=workspace_path
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {"source_path": workspace_path}
    if not isinstance(payload, dict):
        return {"source_path": workspace_path}

    metadata: dict[str, str] = {"source_path": workspace_path}
    aliases: dict[str, tuple[str, ...]] = {
        "published_at": ("published_at", "publishedAt", "date", "source_published_at"),
        "retrieved_at": ("retrieved_at", "retrievedAt", "accessed_at", "accessedAt"),
        "source_title": ("source_title", "title"),
        "source_name": ("source_name", "name"),
        "publisher": ("publisher", "source_publisher"),
        "source_url": ("source_url", "url"),
        "source_type": ("source_type", "provider_type", "storage_type"),
        "retrieval_source_type": ("retrieval_source_type",),
        "underlying_evidence_type": ("underlying_evidence_type",),
        "raw_underlying_evidence_type": ("raw_underlying_evidence_type",),
        "source_category": ("source_category", "evidence_category"),
        "topic": ("topic", "category"),
        "source_id": ("source_id", "id"),
    }
    for field, names in aliases.items():
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                metadata[field] = value.strip()
                break
    _normalize_source_evidence_taxonomy(metadata)
    return metadata


def _source_evidence_metadata_from_markdown(
    path: Path, *, workspace_path: str
) -> dict[str, str]:
    metadata: dict[str, str] = {"source_path": workspace_path}
    aliases: dict[str, tuple[str, ...]] = {
        "published_at": ("published", "published_at", "date", "source_published_at"),
        "retrieved_at": ("retrieved", "retrieved_at", "accessed", "accessed_at"),
        "source_title": ("title", "source_title"),
        "source_name": ("source_name", "name"),
        "publisher": ("publisher", "source_publisher"),
        "source_url": ("source_url", "url"),
        "source_type": ("source_type", "provider_type", "storage_type"),
        "retrieval_source_type": ("retrieval_source_type",),
        "underlying_evidence_type": ("underlying_evidence_type",),
        "raw_underlying_evidence_type": ("raw_underlying_evidence_type",),
        "source_category": ("source_category", "evidence_category"),
        "topic": ("topic", "category"),
        "source_id": ("source_id", "source id", "id"),
    }
    key_to_field = {
        alias.lower().replace("-", "_").replace(" ", "_"): field
        for field, field_aliases in aliases.items()
        for alias in field_aliases
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return metadata
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue
        match = re.match(
            r"^\s*(?:[-*]\s*)?([A-Za-z][A-Za-z0-9 _-]{0,48})\s*:\s*(.+?)\s*$", line
        )
        if not match:
            continue
        key = match.group(1).strip().lower().replace("-", "_").replace(" ", "_")
        field = key_to_field.get(key)
        value = match.group(2).strip()
        if field and value and field not in metadata:
            metadata[field] = value
    _normalize_source_evidence_taxonomy(metadata)
    return metadata


def _normalize_source_evidence_taxonomy(metadata: dict[str, str]) -> None:
    raw_underlying = (
        metadata.get("raw_underlying_evidence_type")
        or metadata.get("underlying_evidence_type")
        or metadata.get("source_category")
        or metadata.get("topic")
        or ""
    )
    metadata["retrieval_source_type"] = normalize_retrieval_source_type(
        metadata.get("retrieval_source_type"),
        metadata.get("source_type"),
        raw_underlying,
    )
    if raw_underlying:
        metadata["underlying_evidence_type"] = normalize_underlying_evidence_type(
            raw_underlying
        )
        metadata["source_category"] = normalize_source_category(
            metadata.get("source_category"),
            metadata["underlying_evidence_type"],
            raw_underlying,
        )
        metadata["raw_underlying_evidence_type"] = raw_underlying


def _claims_with_enriched_metadata(
    *,
    claims: list[dict[str, Any]],
    source_authority: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    enriched_claims: list[dict[str, Any]] = []
    enrichment_records: list[dict[str, Any]] = []
    missing_sources: list[str] = []

    for claim in claims:
        next_claim = dict(claim)
        source_id = str(next_claim.get("source_id") or "").strip()
        current_metadata = dict(next_claim.get("metadata") or {})
        metadata_source_path = current_metadata.get("source_path")
        authority = (
            source_authority.get(metadata_source_path)
            if isinstance(metadata_source_path, str) and metadata_source_path.strip()
            else None
        )
        if authority is None:
            authority = source_authority.get(source_id)
        if authority is None:
            missing_sources.append(source_id)
            enriched_claims.append(next_claim)
            continue
        authority_metadata = authority["metadata"]
        original_metadata = dict(current_metadata)
        changed_fields: list[str] = []
        for field in CLAIM_METADATA_ENRICHMENT_ALLOWED_FIELDS:
            new_value = authority_metadata.get(field)
            if not isinstance(new_value, str) or not new_value.strip():
                continue
            existing = current_metadata.get(field)
            if existing is not None and not isinstance(existing, str):
                raise RuntimeStateError(
                    "Claim metadata enrichment found non-string existing metadata.",
                    details={
                        "claim_id": next_claim.get("claim_id"),
                        "source_id": source_id,
                        "field": field,
                        "existing_type": type(existing).__name__,
                    },
                    error_code=E_TRANSACTION_INTEGRITY,
                )
            existing_text = existing.strip() if isinstance(existing, str) else ""
            new_text = new_value.strip()
            if existing_text and existing_text != new_text:
                replaceable_values = CLAIM_METADATA_REPLACEABLE_DEFAULTS.get(
                    field, set()
                )
                if (
                    existing_text in replaceable_values
                    and new_text not in replaceable_values
                ):
                    current_metadata[field] = new_text
                    changed_fields.append(field)
                    continue
                raise RuntimeStateError(
                    "Claim metadata enrichment would overwrite existing metadata with a different value.",
                    details={
                        "claim_id": next_claim.get("claim_id"),
                        "source_id": source_id,
                        "field": field,
                        "existing": existing,
                        "source_value": new_value,
                    },
                    error_code=E_TRANSACTION_INTEGRITY,
                )
            if not existing_text:
                current_metadata[field] = new_value.strip()
                changed_fields.append(field)
        changed_fields.extend(
            _sync_enriched_claim_source_fields(
                next_claim=next_claim,
                original_metadata=original_metadata,
                authority_metadata=authority_metadata,
            )
        )
        if changed_fields:
            next_claim["metadata"] = current_metadata
            enrichment_records.append(
                {
                    "claim_id": str(next_claim.get("claim_id") or ""),
                    "source_id": source_id,
                    "fields": sorted(set(changed_fields)),
                    "source_workspace_path": authority["workspace_path"],
                    "source_sha256": authority["sha256"],
                }
            )
        enriched_claims.append(next_claim)

    if missing_sources:
        raise RuntimeStateError(
            "Claim metadata enrichment could not find imported source evidence for every claim.",
            details={"missing_source_ids": sorted(set(missing_sources))},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if not enrichment_records:
        raise RuntimeStateError(
            "Claim metadata enrichment found no missing metadata to add.",
            details={"allowed_fields": list(CLAIM_METADATA_ENRICHMENT_ALLOWED_FIELDS)},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return enriched_claims, enrichment_records


def _sync_enriched_claim_source_fields(
    *,
    next_claim: dict[str, Any],
    original_metadata: dict[str, Any],
    authority_metadata: dict[str, Any],
) -> list[str]:
    """Mirror enriched source identity into Claim top-level fields used by readers."""

    changed_fields: list[str] = []

    source_url = authority_metadata.get("source_url")
    if isinstance(source_url, str) and source_url.strip():
        existing_url = next_claim.get("source_url")
        if not (isinstance(existing_url, str) and existing_url.strip()):
            next_claim["source_url"] = source_url.strip()
            changed_fields.append("source_url")

    source_type = authority_metadata.get("source_type")
    if isinstance(source_type, str) and source_type.strip():
        existing_type = next_claim.get("source_type")
        existing_metadata_type = original_metadata.get("source_type")
        has_explicit_metadata_type = isinstance(existing_metadata_type, str) and bool(
            existing_metadata_type.strip()
        )
        metadata_type_matches_authority = (
            has_explicit_metadata_type
            and existing_metadata_type.strip() == source_type.strip()
        )
        if not (isinstance(existing_type, str) and existing_type.strip()):
            next_claim["source_type"] = source_type.strip()
            changed_fields.append("source_type")
        elif existing_type.strip() == "local_file" and (
            not has_explicit_metadata_type or metadata_type_matches_authority
        ):
            next_claim["source_type"] = source_type.strip()
            changed_fields.append("source_type")

    return changed_fields


def _workflow_allows_claim_metadata_enrichment(
    workflow: dict[str, Any], stages: list[dict[str, Any]]
) -> None:
    if _workflow_is_finalized(workflow):
        raise RuntimeStateError(
            "Cannot enrich Claim Ledger metadata after finalize; start a new run or explicit revision path.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    stage_ids = _stage_ids(stages)
    if "claim-ledger" not in stage_ids:
        raise RuntimeStateError(
            "Workflow does not contain claim-ledger stage.",
            details={"known_stages": stage_ids},
            error_code=E_ILLEGAL_TRANSITION,
        )
    claim_index = stage_ids.index("claim-ledger")
    completed_downstream = [
        stage_id
        for stage_id in stage_ids[claim_index + 1 :]
        if _stage_status(workflow, stage_id) in {STAGE_COMPLETE, STAGE_SKIPPED}
    ]
    if completed_downstream:
        raise RuntimeStateError(
            "Cannot enrich Claim Ledger metadata after downstream stages completed; start a new run or owner-stage repair.",
            details={"completed_downstream_stages": completed_downstream},
            error_code=E_TRANSACTION_INTEGRITY,
        )


def _workflow_with_enriched_claim_ledger_hash(
    *,
    workflow: dict[str, Any],
    ledger_sha: str,
    transaction_id: str,
    now: str,
) -> dict[str, Any]:
    statuses = dict(workflow.get("stage_statuses") or {})
    claim_status = dict(statuses.get("claim-ledger") or {})
    if claim_status.get("status") == STAGE_COMPLETE:
        metadata = dict(claim_status.get("metadata") or {})
        produced = dict(metadata.get("produced_artifact_sha256") or {})
        produced["claim_ledger"] = ledger_sha
        metadata["produced_artifact_sha256"] = produced
        metadata["claim_ledger_metadata_enrichment"] = {
            "transaction_id": transaction_id,
            "enriched_at": now,
            "claim_ledger_sha256": ledger_sha,
        }
        claim_status["metadata"] = metadata
        statuses["claim-ledger"] = claim_status
    updated = dict(workflow)
    updated["updated_at"] = now
    updated["stage_statuses"] = statuses
    return updated


def enrich_claim_metadata_transaction(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
    from_source_evidence: bool = True,
) -> dict[str, Any]:
    if not from_source_evidence:
        raise RuntimeStateError(
            "Claim metadata enrichment only supports --from-source-evidence.",
            details={"from_source_evidence": from_source_evidence},
            error_code=E_ILLEGAL_TRANSITION,
        )
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Event log is required before enriching Claim Ledger metadata.",
            details={"missing": str(paths["event_log"])},
            error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
        )
    event_records = read_event_log_records_strict(paths["event_log"])
    run_id = str(manifest["run_id"])
    if not _current_run_start_event_exists(event_records, run_id):
        raise RuntimeStateError(
            "Event log does not contain a current-run start event; refusing Claim Ledger metadata enrichment.",
            details={"run_id": run_id, "event_log": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    _workflow_allows_claim_metadata_enrichment(workflow, stages)

    artifacts_by_id = {
        str(artifact.get("artifact_id")): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
    contract_ledger_path = artifact_path_from_contracts(
        ws,
        artifacts_by_id,
        artifact_id="claim_ledger",
    )
    freeze = manifest.get("claim_ledger_freeze")
    if isinstance(freeze, dict):
        ledger_path = workspace_artifact_path(
            ws,
            str(freeze.get("claim_ledger_path") or CLAIM_LEDGER_PATH),
            artifact_id="claim_ledger",
            binding_source="claim_ledger_freeze",
        )
        if ledger_path != contract_ledger_path:
            raise RuntimeStateError(
                "Claim Ledger freeze path does not match the current artifact contract.",
                details={
                    "freeze_path": _workspace_relative(ws, ledger_path),
                    "contract_path": _workspace_relative(ws, contract_ledger_path),
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
    else:
        ledger_path = contract_ledger_path
    ledger_authority = _claim_ledger_enrichment_authority(
        workspace=ws,
        manifest=manifest,
        ledger_path=ledger_path,
    )
    try:
        ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        claims = ClaimLedger._claim_items_from_json(ledger_payload)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeStateError(
            "Claim Ledger is not readable for metadata enrichment.",
            details={"path": _workspace_relative(ws, ledger_path), "reason": str(exc)},
            error_code=E_ARTIFACT_INVALID,
        ) from exc

    source_authority = _imported_source_evidence_authority(manifest, workspace=ws)
    enriched_claims, enrichment_records = _claims_with_enriched_metadata(
        claims=claims,
        source_authority=source_authority,
    )
    ledger_bytes = _claim_ledger_bytes(enriched_claims)
    previous_sha = _sha256_file(ledger_path)
    ledger_sha = _sha256_bytes(ledger_bytes)
    if previous_sha == ledger_sha:
        raise RuntimeStateError(
            "Claim metadata enrichment produced identical Claim Ledger bytes.",
            details={"claim_ledger_sha256": ledger_sha},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    transaction_id = uuid.uuid4().hex
    now = utc_now()
    next_manifest = dict(manifest)
    if isinstance(next_manifest.get("claim_ledger_freeze"), dict):
        freeze = dict(next_manifest["claim_ledger_freeze"])
        freeze["claim_ledger_sha256"] = ledger_sha
        freeze["metadata_enriched_at"] = now
        freeze["metadata_enrichment_transaction_id"] = transaction_id
        next_manifest["claim_ledger_freeze"] = freeze
    enrichment_record = {
        "schema_version": CLAIM_LEDGER_METADATA_ENRICHMENT_SCHEMA,
        "status": "applied",
        "enriched_at": now,
        "transaction_id": transaction_id,
        "source": "fact_layer_imported_source_evidence",
        "claim_ledger_authority": ledger_authority["kind"],
        "source_claim_ledger_sha256": ledger_authority["source_claim_ledger_sha256"],
        "claim_ledger_path": _workspace_relative(ws, ledger_path),
        "previous_claim_ledger_sha256": previous_sha,
        "claim_ledger_sha256": ledger_sha,
        "allowed_fields": list(CLAIM_METADATA_ENRICHMENT_ALLOWED_FIELDS),
        "forbidden_fields": list(CLAIM_METADATA_ENRICHMENT_FORBIDDEN_FIELDS),
        "enriched_claim_count": len(enrichment_records),
        "enriched_claims": enrichment_records,
    }
    next_manifest["claim_ledger_metadata_enrichment"] = enrichment_record
    existing_history = next_manifest.get("claim_ledger_metadata_enrichments")
    history = list(existing_history) if isinstance(existing_history, list) else []
    history.append(enrichment_record)
    next_manifest["claim_ledger_metadata_enrichments"] = history
    next_manifest["updated_at"] = now
    next_workflow = _workflow_with_enriched_claim_ledger_hash(
        workflow=workflow,
        ledger_sha=ledger_sha,
        transaction_id=transaction_id,
        now=now,
    )

    file_snapshots = _snapshot_file_paths([ledger_path])
    state_snapshots = _snapshot_state_files(
        paths, ("runtime_manifest", "artifact_registry", "workflow_state", "event_log")
    )
    try:
        _write_bytes_atomic(ledger_path, ledger_bytes)
        registry = _build_artifact_registry(
            workspace=ws,
            run_id=run_id,
            artifacts=artifacts,
            workflow=next_workflow,
            updated_at=now,
        )
        ledger_record = (registry.get("artifacts") or {}).get("claim_ledger") or {}
        if (
            ledger_record.get("status") != ARTIFACT_VALID
            or ledger_record.get("sha256") != ledger_sha
        ):
            raise RuntimeStateError(
                "Enriched Claim Ledger failed artifact validation.",
                details={
                    "artifact_id": "claim_ledger",
                    "status": ledger_record.get("status"),
                    "validation_result": ledger_record.get("validation_result"),
                    "expected_sha256": ledger_sha,
                    "actual_sha256": ledger_record.get("sha256"),
                },
                error_code=E_ARTIFACT_INVALID,
            )
        _write_json_atomic(paths["runtime_manifest"], next_manifest)
        _write_json_atomic(paths["artifact_registry"], registry)
        _write_json_atomic(paths["workflow_state"], next_workflow)
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="claim_ledger_metadata_enriched",
            actor=actor,
            stage_id="claim-ledger",
            artifact_id="claim_ledger",
            reason="Claim Ledger metadata enriched from imported source evidence.",
            metadata={
                "transaction_id": transaction_id,
                "claim_ledger_path": _workspace_relative(ws, ledger_path),
                "previous_claim_ledger_sha256": previous_sha,
                "claim_ledger_sha256": ledger_sha,
                "enriched_claim_count": len(enrichment_records),
                "allowed_fields": list(CLAIM_METADATA_ENRICHMENT_ALLOWED_FIELDS),
            },
        )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
            _restore_file_paths(
                file_snapshots,
                rollback_message="Claim metadata enrichment rollback failed after partial write.",
            )
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Claim metadata enrichment partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "enrichment_error": str(exc),
                    "enrichment_details": exc.details,
                    "rollback_error": str(rollback_exc),
                    "rollback_details": rollback_exc.details,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Claim metadata enrichment failed; written files were restored.",
            details={
                "transaction_id": transaction_id,
                "enrichment_error": str(exc),
                "enrichment_details": exc.details,
                "restored": True,
            },
            error_code=exc.error_code,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["claim_ledger_metadata_enrichment"] = enrichment_record
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": "claim-ledger",
        "decision": "enrich_claim_metadata",
    }
    return state
