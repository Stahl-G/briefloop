"""Claim Ledger freeze transaction.

Python-owned freeze: reads agent-drafted claim_drafts.json, assigns
deterministic sorted-sequential claim IDs, writes canonical
claim_ledger.json atomically, and records freeze metadata.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.schemas.claim_draft import (
    ClaimDraftContract,
    claim_draft_diagnostics,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json,
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
    CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE,
    _build_artifact_registry,
)
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _raise_completion_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ARTIFACT_INVALID,
    E_CLAIM_DRAFT_CONTRACT_INVALID,
    E_REQUIRED_ARTIFACT_MISSING,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_STAGE_MISMATCH,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    append_event,
    read_event_log_records_strict,
)
from multi_agent_brief.orchestrator.runtime_state.identity import utc_now
from multi_agent_brief.orchestrator.runtime_state.lifecycle import show_runtime_state
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
)


CLAIM_DRAFTS_PATH = Path("output/intermediate/claim_drafts.json")


CLAIM_LEDGER_FREEZE_SCHEMA = "mabw.claim_ledger_freeze.v1"


CLAIM_LEDGER_PATH = Path("output/intermediate/claim_ledger.json")


CLAIM_LEDGER_FREEZE_ID_STRATEGY = "sorted_sequential_v1"


CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS = (
    "published_at",
    "retrieved_at",
    "source_path",
    "source_title",
    "source_name",
    "publisher",
    "source_url",
    "source_type",
    "source_category",
    "retrieval_source_type",
    "underlying_evidence_type",
    "raw_underlying_evidence_type",
    "topic",
)


def _normalize_claim_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _claim_draft_sort_key(
    indexed_draft: tuple[int, dict[str, Any]],
) -> tuple[str, str, str, int]:
    index, draft = indexed_draft
    return (
        _normalize_claim_text(str(draft.get("source_id") or "")),
        _normalize_claim_text(str(draft.get("statement") or "")),
        _normalize_claim_text(str(draft.get("evidence_text") or "")),
        index,
    )


def _claim_draft_warnings(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[int]] = {}
    for idx, draft in enumerate(drafts):
        key = _normalize_claim_text(str(draft.get("statement") or ""))
        if key:
            buckets.setdefault(key, []).append(idx)
    return [
        {
            "warning_type": "lexical_duplicate_statement",
            "draft_indexes": indexes,
            "normalized_statement": statement,
        }
        for statement, indexes in sorted(buckets.items())
        if len(indexes) > 1
    ]


def _read_claim_drafts_for_freeze(
    workspace: Path,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = workspace / CLAIM_DRAFTS_PATH
    if not path.exists():
        raise RuntimeStateError(
            "Claim drafts are required before freezing the Claim Ledger.",
            details={"path": _workspace_relative(workspace, path)},
            error_code=E_REQUIRED_ARTIFACT_MISSING,
        )
    payload = _read_json(path)
    violations = ClaimDraftContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        diagnostics = claim_draft_diagnostics(errors)
        raise RuntimeStateError(
            "Claim drafts failed contract validation.",
            details={
                "path": _workspace_relative(workspace, path),
                "field": first.field,
                "error": first.error,
                "required_fields": ["statement", "source_id", "evidence_text"],
                "forbidden_fields": ["claim_id"],
                "diagnostics": diagnostics,
            },
            error_code=E_CLAIM_DRAFT_CONTRACT_INVALID,
        )
    drafts = payload.get("drafts") or []
    if not drafts:
        raise RuntimeStateError(
            "Claim drafts must contain at least one draft before freezing the Claim Ledger.",
            details={
                "path": _workspace_relative(workspace, path),
                "field": "drafts",
                "error": "must contain at least one draft",
                "required_fields": ["statement", "source_id", "evidence_text"],
                "forbidden_fields": ["claim_id"],
                "diagnostics": [
                    {
                        "field": "drafts",
                        "error": "must contain at least one draft",
                        "severity": "error",
                        "required_fields": ["statement", "source_id", "evidence_text"],
                    }
                ],
            },
            error_code=E_CLAIM_DRAFT_CONTRACT_INVALID,
        )
    return path, payload, [dict(draft) for draft in drafts]


def _canonical_claims_from_drafts(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for seq, (_original_index, draft) in enumerate(
        sorted(enumerate(drafts), key=_claim_draft_sort_key),
        start=1,
    ):
        metadata = dict(draft.get("metadata") or {})
        if draft.get("draft_id"):
            metadata["draft_id"] = str(draft["draft_id"])
        if draft.get("candidate_id"):
            metadata["candidate_id"] = str(draft["candidate_id"])
        source_type = _claim_draft_source_type(draft)
        for field in CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS:
            if draft.get(field) is not None:
                if field == "source_type":
                    raw_source_type = draft.get("source_type")
                    if isinstance(raw_source_type, str) and raw_source_type.strip():
                        metadata.setdefault(field, source_type)
                else:
                    metadata.setdefault(field, str(draft[field]).strip())
        claim = {
            "claim_id": f"CL-{seq:04d}",
            "statement": str(draft["statement"]).strip(),
            "source_id": str(draft["source_id"]).strip(),
            "evidence_text": str(draft["evidence_text"]).strip(),
            "source_url": str(draft.get("source_url") or ""),
            "source_type": source_type,
            "claim_type": str(draft.get("claim_type") or "fact"),
            "confidence": str(draft.get("confidence") or "medium"),
            "requires_audit": bool(draft.get("requires_audit", True)),
            "created_by": str(draft.get("created_by") or "claim-ledger"),
            "used_in_sections": list(draft.get("used_in_sections") or []),
            "metadata": metadata,
            "schema_version": "v2",
            "epistemic_type": str(draft.get("epistemic_type") or "observed"),
            "evidence_relation": str(draft.get("evidence_relation") or "direct"),
            "applicability_reason": str(draft.get("applicability_reason") or ""),
            "limitations": list(draft.get("limitations") or []),
        }
        claims.append(claim)
    return claims


def _claim_draft_source_type(draft: dict[str, Any]) -> str:
    source_type = draft.get("source_type")
    if isinstance(source_type, str):
        return source_type.strip() or "local_file"
    if source_type is None:
        return "local_file"
    return str(source_type).strip() or "local_file"


def _claim_ledger_bytes(claims: list[dict[str, Any]]) -> bytes:
    text = json.dumps(claims, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _claim_ledger_freeze_manifest(
    *,
    workspace: Path,
    frozen_at: str,
    draft_path: Path,
    draft_payload: dict[str, Any],
    drafts: list[dict[str, Any]],
    ledger_path: Path,
    ledger_bytes: bytes,
    warnings: list[dict[str, Any]],
    transaction_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": CLAIM_LEDGER_FREEZE_SCHEMA,
        "status": "frozen",
        "frozen_at": frozen_at,
        "transaction_id": transaction_id,
        "id_strategy": CLAIM_LEDGER_FREEZE_ID_STRATEGY,
        "id_stability_scope": "per_freeze_input",
        "id_strategy_description": (
            "Deterministic for identical claim_drafts.json content under sorted_sequential_v1; "
            "not a cross-incremental stability guarantee when drafts are added, removed, or changed."
        ),
        "source_artifact_id": "claim_drafts",
        "source_path": _workspace_relative(workspace, draft_path),
        "source_schema_version": draft_payload.get("schema_version"),
        "source_sha256": _sha256_file(draft_path),
        "claim_ledger_path": _workspace_relative(workspace, ledger_path),
        "claim_ledger_sha256": _sha256_bytes(ledger_bytes),
        "claim_count": len(drafts),
        "source_ids": sorted(
            {
                str(draft.get("source_id") or "")
                for draft in drafts
                if draft.get("source_id")
            }
        ),
        "warnings": warnings,
    }


def _claim_ledger_freeze_reasons(
    *,
    workspace: Path,
    manifest: dict[str, Any],
) -> list[str]:
    freeze = manifest.get("claim_ledger_freeze")
    if not isinstance(freeze, dict):
        return [
            "Claim Ledger has not been frozen. Run `multi-agent-brief state freeze-claim-ledger --workspace <workspace>`."
        ]
    reasons: list[str] = []
    if freeze.get("schema_version") != CLAIM_LEDGER_FREEZE_SCHEMA:
        reasons.append("Claim Ledger freeze metadata has an unsupported schema.")
    if freeze.get("status") != "frozen":
        reasons.append("Claim Ledger freeze metadata is not frozen.")
    draft_path = workspace / str(freeze.get("source_path") or CLAIM_DRAFTS_PATH)
    ledger_path = workspace / str(freeze.get("claim_ledger_path") or CLAIM_LEDGER_PATH)
    if not draft_path.exists() or not draft_path.is_file():
        reasons.append(
            f"Claim Ledger freeze source is missing: {_workspace_relative(workspace, draft_path)}."
        )
    elif _sha256_file(draft_path) != str(freeze.get("source_sha256") or ""):
        reasons.append(
            "Claim Ledger freeze source hash does not match current claim_drafts.json."
        )
    if not ledger_path.exists() or not ledger_path.is_file():
        reasons.append(
            f"Frozen Claim Ledger is missing: {_workspace_relative(workspace, ledger_path)}."
        )
    elif _sha256_file(ledger_path) != str(freeze.get("claim_ledger_sha256") or ""):
        reasons.append(
            f"Frozen Claim Ledger hash does not match current claim_ledger.json. {CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE}"
        )
    return reasons


def freeze_claim_ledger_transaction(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Event log is required before freezing the Claim Ledger.",
            details={"missing": str(paths["event_log"])},
            error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
        )
    event_records = read_event_log_records_strict(paths["event_log"])
    if workflow.get("current_stage") != "claim-ledger":
        raise RuntimeStateError(
            "Claim Ledger can only be frozen while claim-ledger is the current stage.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_STAGE_MISMATCH,
        )
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    run_id = str(manifest["run_id"])
    if not _current_run_start_event_exists(event_records, run_id):
        raise RuntimeStateError(
            "Event log does not contain a current-run start event; refusing Claim Ledger freeze.",
            details={"run_id": run_id, "event_log": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    transaction_id = uuid.uuid4().hex
    frozen_at = utc_now()
    draft_path, draft_payload, drafts = _read_claim_drafts_for_freeze(ws)
    warnings = _claim_draft_warnings(drafts)
    claims = _canonical_claims_from_drafts(drafts)
    ledger_bytes = _claim_ledger_bytes(claims)
    ledger_path = ws / CLAIM_LEDGER_PATH
    source_sha = _sha256_file(draft_path)
    ledger_sha = _sha256_bytes(ledger_bytes)

    if "claim_ledger_freeze" in manifest:
        existing_freeze = manifest.get("claim_ledger_freeze")
        if not isinstance(existing_freeze, dict):
            raise RuntimeStateError(
                "Claim Ledger freeze metadata is malformed; refusing to freeze again.",
                details={"field": "claim_ledger_freeze"},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        freeze_reasons = _claim_ledger_freeze_reasons(workspace=ws, manifest=manifest)
        frozen_source_sha = str(existing_freeze.get("source_sha256") or "")
        frozen_ledger_sha = str(existing_freeze.get("claim_ledger_sha256") or "")
        if (
            not freeze_reasons
            and frozen_source_sha == source_sha
            and frozen_ledger_sha == ledger_sha
        ):
            state = show_runtime_state(workspace=ws)
            state["claim_ledger_freeze"] = existing_freeze
            state["transaction"] = {
                "transaction_id": existing_freeze.get("transaction_id"),
                "stage_id": "claim-ledger",
                "decision": "freeze_claim_ledger_idempotent",
            }
            return state
        message = (
            "Claim Ledger is already frozen; repeat freeze requires unchanged claim_drafts.json "
            "and claim_ledger.json. Route repair/reset before freezing changed drafts."
        )
        if any(
            CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE in reason for reason in freeze_reasons
        ):
            message = f"{message} {CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE}"
        raise RuntimeStateError(
            message,
            details={
                "freeze_reasons": freeze_reasons,
                "frozen_source_sha256": frozen_source_sha,
                "current_source_sha256": source_sha,
                "frozen_claim_ledger_sha256": frozen_ledger_sha,
                "current_claim_ledger_sha256": ledger_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )

    next_manifest = dict(manifest)
    next_manifest["updated_at"] = frozen_at
    next_manifest["claim_ledger_freeze"] = _claim_ledger_freeze_manifest(
        workspace=ws,
        frozen_at=frozen_at,
        draft_path=draft_path,
        draft_payload=draft_payload,
        drafts=drafts,
        ledger_path=ledger_path,
        ledger_bytes=ledger_bytes,
        warnings=warnings,
        transaction_id=transaction_id,
    )
    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=workflow,
        updated_at=frozen_at,
    )

    file_snapshots = _snapshot_file_paths([ledger_path])
    state_snapshots = _snapshot_state_files(
        paths, ("runtime_manifest", "artifact_registry")
    )
    try:
        _write_bytes_atomic(ledger_path, ledger_bytes)
        registry = _build_artifact_registry(
            workspace=ws,
            run_id=run_id,
            artifacts=artifacts,
            workflow=workflow,
            updated_at=frozen_at,
        )
        ledger_record = (registry.get("artifacts") or {}).get("claim_ledger") or {}
        if ledger_record.get("status") != ARTIFACT_VALID:
            raise RuntimeStateError(
                "Frozen Claim Ledger failed artifact validation.",
                details={
                    "artifact_id": "claim_ledger",
                    "status": ledger_record.get("status"),
                    "validation_result": ledger_record.get("validation_result"),
                },
                error_code=E_ARTIFACT_INVALID,
            )
        _write_json_atomic(paths["runtime_manifest"], next_manifest)
        _write_json_atomic(paths["artifact_registry"], registry)
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="claim_ledger_frozen",
            actor=actor,
            stage_id="claim-ledger",
            artifact_id="claim_ledger",
            reason="Claim Ledger frozen from claim_drafts.json.",
            metadata={
                "transaction_id": transaction_id,
                "source_artifact_id": "claim_drafts",
                "source_path": _workspace_relative(ws, draft_path),
                "source_sha256": source_sha,
                "claim_ledger_path": _workspace_relative(ws, ledger_path),
                "claim_ledger_sha256": ledger_sha,
                "claim_count": len(claims),
                "id_strategy": CLAIM_LEDGER_FREEZE_ID_STRATEGY,
                "warning_count": len(warnings),
            },
        )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
            _restore_file_paths(
                file_snapshots,
                rollback_message="Claim Ledger freeze rollback failed after partial write.",
            )
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Claim Ledger freeze partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "freeze_error": str(exc),
                    "freeze_details": exc.details,
                    "rollback_error": str(rollback_exc),
                    "rollback_details": rollback_exc.details,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Claim Ledger freeze failed; written files were restored.",
            details={
                "transaction_id": transaction_id,
                "freeze_error": str(exc),
                "freeze_details": exc.details,
            },
            error_code=exc.error_code,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["claim_ledger_freeze"] = next_manifest["claim_ledger_freeze"]
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": "claim-ledger",
        "decision": "freeze_claim_ledger",
    }
    return state
