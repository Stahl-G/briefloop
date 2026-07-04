"""Compatibility facade for runtime-state operations.

The implementation moved to transaction-scoped modules in this package:
_transactions, trajectory, fact_layer, lifecycle, claim_ledger_freeze,
claim_metadata_enrichment, repair, stage_completion, and decisions.
This module re-exports the previous public surface for older imports and
for tests that reach constants, errors, and seams via
runtime_state.operations.<name>.
"""

from __future__ import annotations

from multi_agent_brief.orchestrator.run_archive import (  # noqa: F401
    E_RUN_ARCHIVE_CONFLICT,
    RunArchiveError,
)
from multi_agent_brief.orchestrator.runtime_state._io import (  # noqa: F401
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (  # noqa: F401
    _build_artifact_registry,
)
from multi_agent_brief.orchestrator.runtime_state.claim_ledger_freeze import (  # noqa: F401
    CLAIM_DRAFTS_PATH,
    CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS,
    CLAIM_LEDGER_FREEZE_ID_STRATEGY,
    CLAIM_LEDGER_FREEZE_SCHEMA,
    CLAIM_LEDGER_PATH,
    _canonical_claims_from_drafts,
    _claim_draft_sort_key,
    _claim_draft_source_type,
    _claim_draft_warnings,
    _claim_ledger_bytes,
    _claim_ledger_freeze_manifest,
    _claim_ledger_freeze_reasons,
    _normalize_claim_text,
    _read_claim_drafts_for_freeze,
    freeze_claim_ledger_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.claim_metadata_enrichment import (  # noqa: F401
    CLAIM_LEDGER_METADATA_ENRICHMENT_SCHEMA,
    CLAIM_METADATA_ENRICHMENT_ALLOWED_FIELDS,
    CLAIM_METADATA_ENRICHMENT_FORBIDDEN_FIELDS,
    CLAIM_METADATA_REPLACEABLE_DEFAULTS,
    _claim_ledger_enrichment_authority,
    _claims_with_enriched_metadata,
    _imported_claim_ledger_record,
    _imported_source_evidence_authority,
    _normalize_source_evidence_taxonomy,
    _source_evidence_ids,
    _source_evidence_metadata_from_file,
    _source_evidence_metadata_from_markdown,
    _sync_enriched_claim_source_fields,
    _valid_imported_claim_ledger_derivation,
    _workflow_allows_claim_metadata_enrichment,
    _workflow_with_enriched_claim_ledger_hash,
    enrich_claim_metadata_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.decisions import record_decision  # noqa: F401
from multi_agent_brief.orchestrator.runtime_state.errors import (  # noqa: F401
    E_FACT_LAYER_IMPORT_INVALID,
    E_ILLEGAL_TRANSITION,
    E_READER_FINAL_GATE_FAILED,
    E_REPAIR_TRANSACTION_REQUIRED,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_STAGE_MISMATCH,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (  # noqa: F401
    EVENT_LOG_SCHEMA,
    append_event,
    read_event_log_records_strict,
    record_handoff_written,
)
from multi_agent_brief.orchestrator.runtime_state.fact_layer import (  # noqa: F401
    FACT_LAYER_IMPORT_FORBIDDEN_ARTIFACT_IDS,
    FACT_LAYER_IMPORT_REQUIRED_ARTIFACT_IDS,
    FACT_LAYER_IMPORT_SCHEMA,
    FACT_LAYER_IMPORT_SINGLETON_PATHS,
    FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID,
    _archive_fact_layer_path_for,
    _copy_import_files,
    _imported_required_artifact_reasons,
    _path_text_is_unsafe,
    _read_fact_layer_import_plan,
    _reject_duplicate_fact_layer_import_targets,
    _reject_existing_fact_layer_import_leftovers,
    _reject_existing_fact_layer_import_targets,
    _reject_source_plan_fact_layer_record,
    _require_fact_layer_file_record,
    _resolve_fact_layer_archive_manifest,
    _source_archive_path,
    _target_workspace_path,
    _validate_fact_layer_import_record_scope,
    import_fact_layer_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.identity import (  # noqa: F401
    new_run_id,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (  # noqa: F401
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.lifecycle import (  # noqa: F401
    _archive_reset_run_scoped_control_artifact,
    _recompute_stage_state,
    _remove_reset_archive_copy,
    _reset_run_scoped_control_artifact_paths,
    _restore_reset_control_artifacts,
    check_runtime_state,
    initialize_runtime_state,
    show_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA  # noqa: F401
from multi_agent_brief.orchestrator.runtime_state.paths import (  # noqa: F401
    RUNTIME_STATE_FILES,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.repair import (  # noqa: F401
    _active_repair_blocking_error,
    _artifact_allowed,
    _artifact_path_matches,
    _delegate_repair_transaction_required_error,
    _repair_artifact_baseline,
    _repair_changed_artifact_reasons,
    _repair_event_metadata,
    _repair_route_error,
    _source_stage_for_repair_route,
    _stale_artifact_baselines_for_stage,
    _workflow_after_repair_completion,
    _workflow_with_active_repair,
    _workflow_with_repair_run_integrity_effect,
    complete_repair_transaction,
    raise_if_active_repair_open,
    start_repair_transaction,
)
from multi_agent_brief.orchestrator.runtime_state.stage_completion import (  # noqa: F401
    ANALYST_DRAFT_SNAPSHOT_PATH,
    _append_transaction_events,
    _auditable_target_auditor_gate_pass_reasons,
    _auditor_completion_metadata,
    _complete_stage_transaction,
    _manifest_with_fast_rerun_freshness_at_finalize,
    _older_stage_replay_message,
    _repair_transaction_ids_for_artifact,
    _snapshot_analyst_draft,
    _stage_runtime_provenance,
    _stale_artifact_baseline_sha,
    _stale_expected_artifact_refresh_reasons,
    _topology_satisfaction_required_reasons,
    _topology_satisfaction_target_blocking_reasons,
    _topology_satisfaction_targets_for_completion,
    _topology_satisfier_aliases,
    _validate_completion_target,
    _workflow_with_topology_satisfaction,
    complete_finalize_transaction,
    complete_stage_transaction,
    raise_if_auditable_target_complete_blocks_downstream,
)
from multi_agent_brief.orchestrator.runtime_state.trajectory import (  # noqa: F401
    TRAJECTORY_DECISION_NARROWING_STATUS,
    TRAJECTORY_NARROWED_DECISIONS,
    _raise_if_trajectory_narrows_repair_route,
    _raise_if_trajectory_narrows_success_path,
    _trajectory_decision_narrowing,
    _trajectory_narrowing_changed,
    _workflow_with_trajectory_decision_narrowing,
)


__all__ = [
    "E_FACT_LAYER_IMPORT_INVALID",
    "E_RUNTIME_STATE_NOT_INITIALIZED",
    "E_STAGE_MISMATCH",
    "RUNTIME_MANIFEST_SCHEMA",
    "RUNTIME_STATE_FILES",
    "RuntimeStateError",
    "append_event",
    "check_runtime_state",
    "complete_finalize_transaction",
    "complete_stage_transaction",
    "enrich_claim_metadata_transaction",
    "freeze_claim_ledger_transaction",
    "import_fact_layer_transaction",
    "initialize_runtime_state",
    "load_artifact_contracts",
    "load_stage_specs",
    "new_run_id",
    "read_event_log_records_strict",
    "record_decision",
    "record_handoff_written",
    "runtime_state_paths",
    "show_runtime_state",
    "utc_now",
]
