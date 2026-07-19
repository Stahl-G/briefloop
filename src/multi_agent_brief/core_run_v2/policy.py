"""Frozen product policy for the dormant fresh-v2 core run spine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from types import MappingProxyType
from multi_agent_brief.contracts.v2 import (
    RUNTIME_SOURCE_PROVIDER_IDS,
    RUNTIME_SOURCE_ROUTE_IDS,
    RUNTIME_SOURCE_WEB_PROVIDER_IDS,
)
from multi_agent_brief.control_store.serialization import canonical_fingerprint


CORE_ARTIFACT_IDS = (
    "source_candidates",
    "input_classification",
    "claim_ledger",
    "analyst_draft_snapshot",
    "audited_brief",
    "audit_report",
    "auditor_quality_gate_report",
)
INTERNAL_CONTRACT_ARTIFACT_IDS = (
    "run_contract_stage_specs",
    "run_contract_artifact_contracts",
    "run_contract_policy_pack",
    "run_contract_runtime_adapter",
    "run_contract_runtime_source_plan",
)
REQUIRED_AUDITOR_GATES = (
    "coverage_omission",
    "freshness",
    "material_fact",
    "target_relevance",
)
SOURCE_ROUTE_IDS = frozenset(RUNTIME_SOURCE_ROUTE_IDS)
SOURCE_PROVIDER_IDS = frozenset(RUNTIME_SOURCE_PROVIDER_IDS)
SOURCE_WEB_PROVIDER_IDS = frozenset(RUNTIME_SOURCE_WEB_PROVIDER_IDS)
SOURCE_ROUTE_OWNER_ORDER = MappingProxyType(
    {"deterministic": 0, "specialist": 1, "human": 2}
)
TERMINAL_INTERNAL_ARTIFACT_IDS = (
    "core_v2_run_archive",
    "core_v2_package_manifest",
)
DOCTOR_IMPLEMENTATION = "core-v2-doctor"
DOCTOR_VERSION = "1"
CLAIM_EPISTEMIC = MappingProxyType(
    {
        "fact": "observed",
        "trend": "interpreted",
        "opportunity": "interpreted",
        "risk": "hypothesis",
        "estimate": "hypothesis",
    }
)


@dataclass(frozen=True)
class ArtifactPolicy:
    artifact_id: str
    owner_stage_id: str
    owner_role_id: str
    input_suffix: str
    invocation_required: bool
    producer_tool_id: str | None = None
    invocation_role_id: str | None = None


ARTIFACT_POLICIES = MappingProxyType(
    {
        "source_candidates": ArtifactPolicy(
            artifact_id="source_candidates",
            owner_stage_id="source-discovery",
            owner_role_id="source-planner",
            input_suffix=".yaml",
            invocation_required=True,
            invocation_role_id="source-planner",
        ),
        "input_classification": ArtifactPolicy(
            artifact_id="input_classification",
            owner_stage_id="input-governance",
            owner_role_id="python_tool",
            input_suffix=".json",
            invocation_required=False,
            producer_tool_id="input-governance-v2",
        ),
        "analyst_draft_snapshot": ArtifactPolicy(
            artifact_id="analyst_draft_snapshot",
            owner_stage_id="analyst",
            owner_role_id="analyst",
            input_suffix=".md",
            invocation_required=True,
            producer_tool_id="analyst-snapshot-v2",
            invocation_role_id="analyst",
        ),
        "audited_brief": ArtifactPolicy(
            artifact_id="audited_brief",
            owner_stage_id="editor",
            owner_role_id="editor",
            input_suffix=".md",
            invocation_required=True,
            invocation_role_id="editor",
        ),
    }
)

STAGE_ROLES = MappingProxyType(
    {
        "source-discovery": ("source-planner", "source-provider"),
        "scout": ("scout",),
        "screener": ("screener",),
        "claim-ledger": ("claim-ledger",),
        "analyst": ("analyst",),
        "editor": ("editor",),
        "auditor": ("auditor",),
    }
)


def utc_now(clock: object) -> str:
    value = clock()  # type: ignore[operator]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("core-run clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def derived_id(prefix: str, *parts: str) -> str:
    payload = "\0".join((prefix, *parts)).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:32]}"


def blob_workspace_path(digest: str) -> str:
    return f"briefloop.db.blobs/sha256/{digest[:2]}/{digest}"


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def run_contract_fingerprint(
    *,
    runtime: str,
    stage_specs_schema: str,
    stage_specs_sha256: str,
    artifact_contracts_schema: str,
    artifact_contracts_sha256: str,
    policy_pack_schema: str,
    policy_pack_name: str,
    policy_pack_sha256: str,
    runtime_adapter_sha256: str,
    runtime_adapter_fingerprint: str,
    runtime_source_plan_sha256: str,
    runtime_source_plan_fingerprint: str,
    run_direction: dict[str, object],
    workspace_config_sha256: str,
    sources_config_sha256: str,
    role_topology: str,
    gate_strictness: dict[str, bool],
    input_governance_required: bool,
) -> str:
    return canonical_fingerprint(
        {
            "runtime": runtime,
            "stage_specs_schema": stage_specs_schema,
            "stage_specs_sha256": stage_specs_sha256,
            "artifact_contracts_schema": artifact_contracts_schema,
            "artifact_contracts_sha256": artifact_contracts_sha256,
            "policy_pack_schema": policy_pack_schema,
            "policy_pack_name": policy_pack_name,
            "policy_pack_sha256": policy_pack_sha256,
            "runtime_adapter_sha256": runtime_adapter_sha256,
            "runtime_adapter_fingerprint": runtime_adapter_fingerprint,
            "runtime_source_plan_sha256": runtime_source_plan_sha256,
            "runtime_source_plan_fingerprint": runtime_source_plan_fingerprint,
            "run_direction": run_direction,
            "workspace_config_sha256": workspace_config_sha256,
            "sources_config_sha256": sources_config_sha256,
            "role_topology": role_topology,
            "gate_strictness": gate_strictness,
            "input_governance_required": input_governance_required,
        }
    )


def transaction_type_for(effect_kind: str) -> str:
    values: dict[str, str] = {
        "initialize": "core-v2-initialize",
        "invocation_start": "core-v2-invocation-start",
        "owned_artifact_acceptance": "core-v2-owned-artifact",
        "claim_freeze": "core-v2-claim-freeze",
        "audit_promotion": "core-v2-audit-promotion",
        "gate_evaluation": "core-v2-gate-evaluation",
        "stage_transition": "core-v2-stage-transition",
        "integrity_contamination": "core-v2-integrity-contamination",
        "repair_start": "core-v2-repair-start",
        "artifact_supersession": "core-v2-artifact-supersession",
        "repair_complete": "core-v2-repair-complete",
        "recovery_complete": "core-v2-recovery-complete",
        "run_head_transition": "core-v2-run-reset",
        "finalize_render": "core-v2-finalize-render",
        "finalize_complete": "core-v2-finalize-complete",
        "internal_approval": "core-v2-internal-approval",
        "delivery_authorization": "core-v2-delivery-authorization",
        "delivery_attempt": "core-v2-delivery-attempt",
        "delivery_result": "core-v2-delivery-result",
    }
    return values[effect_kind]


def archive_artifact_usage(artifact_id: str) -> str:
    """Return the sole canonical archive usage vocabulary for an artifact."""

    if artifact_id.startswith("run_contract_"):
        return "control"
    if artifact_id.endswith("quality_gate_report"):
        return "gate"
    if artifact_id == "reader_brief":
        return "reader"
    if artifact_id in {"claim_ledger", "audit_report"}:
        return "evidence"
    return "workflow"


__all__ = [
    "ARTIFACT_POLICIES",
    "CLAIM_EPISTEMIC",
    "CORE_ARTIFACT_IDS",
    "DOCTOR_IMPLEMENTATION",
    "DOCTOR_VERSION",
    "INTERNAL_CONTRACT_ARTIFACT_IDS",
    "REQUIRED_AUDITOR_GATES",
    "STAGE_ROLES",
    "SOURCE_PROVIDER_IDS",
    "SOURCE_ROUTE_IDS",
    "SOURCE_ROUTE_OWNER_ORDER",
    "SOURCE_WEB_PROVIDER_IDS",
    "TERMINAL_INTERNAL_ARTIFACT_IDS",
    "ArtifactPolicy",
    "archive_artifact_usage",
    "blob_workspace_path",
    "derived_id",
    "normalize_text",
    "run_contract_fingerprint",
    "transaction_type_for",
    "utc_now",
]
