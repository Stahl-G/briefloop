"""Stable runtime-state facade."""

from __future__ import annotations

from . import operations
from .contracts_loader import load_artifact_contracts, load_stage_specs
from .errors import (
    E_ACTIVE_REPAIR_OPEN,
    E_ASSESSMENT_TARGET_COMPLETE,
    E_FACT_LAYER_IMPORT_INVALID,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_STAGE_MISMATCH,
    RuntimeStateError,
)
from .event_log import append_event, read_event_log_records_strict, record_handoff_written
from .identity import new_run_id, utc_now
from .manifest import RUNTIME_MANIFEST_SCHEMA
from .claim_ledger_freeze import freeze_claim_ledger_transaction
from .claim_metadata_enrichment import enrich_claim_metadata_transaction
from .completion_projection import build_completion_projection
from .decisions import record_decision
from .fact_layer import import_fact_layer_transaction
from .lifecycle import (
    check_runtime_state,
    initialize_runtime_state,
    show_runtime_state,
)
from .paths import RUNTIME_STATE_FILES, runtime_state_paths
from .repair import (
    complete_repair_transaction,
    raise_if_active_repair_open,
    start_repair_transaction,
)
from .stage_completion import (
    complete_finalize_transaction,
    complete_stage_transaction,
    raise_if_auditable_target_complete_blocks_downstream,
)


__all__ = sorted([
    "E_ACTIVE_REPAIR_OPEN",
    "E_ASSESSMENT_TARGET_COMPLETE",
    "E_FACT_LAYER_IMPORT_INVALID",
    "E_RUNTIME_STATE_NOT_INITIALIZED",
    "E_STAGE_MISMATCH",
    "RUNTIME_MANIFEST_SCHEMA",
    "RUNTIME_STATE_FILES",
    "RuntimeStateError",
    "append_event",
    "build_completion_projection",
    "check_runtime_state",
    "complete_finalize_transaction",
    "complete_repair_transaction",
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
    "raise_if_auditable_target_complete_blocks_downstream",
    "record_handoff_written",
    "raise_if_active_repair_open",
    "runtime_state_paths",
    "show_runtime_state",
    "start_repair_transaction",
    "utc_now",
])
