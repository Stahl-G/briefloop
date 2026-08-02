"""Normal same-workspace successor creation with frozen approved guidance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable

from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    CoreRunEventBinding,
    EventEnvelope,
    GuidanceReuseScopeV1,
    RunGuidanceSelectionDecisionRecord,
    RunGuidanceSnapshotItemRecord,
    RunGuidanceSnapshotRecord,
    RunHeadTransitionRecord,
    RunIdentity,
    RunIntegrityRecord,
    RunSuccessorStartRequest,
    StageState,
    StageTransitionRecord,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)

from .errors import CoreRunError, CoreRunResult, core_run_failure_result
from .recovery import classify_recovery_legality
from .terminal import classify_terminal_legality


_Clock = Callable[[], datetime]
_MAX_GUIDANCE_ITEMS = 16
_MAX_GUIDANCE_UTF8_BYTES = 65_536


@dataclass(frozen=True)
class SuccessorStartLegality:
    """Pure product legality for a normal reference successor."""

    allowed: bool
    reason_code: str

    def require_allowed(self) -> "SuccessorStartLegality":
        if not self.allowed:
            raise CoreRunError(self.reason_code)
        return self


def classify_successor_start_legality(snapshot) -> SuccessorStartLegality:
    recovery = classify_recovery_legality(snapshot)
    terminal = classify_terminal_legality(snapshot)
    if recovery.state != "not_required":
        return SuccessorStartLegality(False, "successor_run_not_ready")
    if terminal.terminal_state != "finalized_local":
        return SuccessorStartLegality(False, "successor_run_not_ready")
    if any(item.status == "active" for item in snapshot.invocations):
        return SuccessorStartLegality(False, "successor_run_not_ready")
    return SuccessorStartLegality(True, "successor_start_allowed")


def build_guidance_reuse_scope(run_direction) -> GuidanceReuseScopeV1:
    payload = {
        "schema_version": GuidanceReuseScopeV1.schema_id,
        "audience": run_direction.audience,
        "audience_profile": run_direction.audience_profile,
        "output_language": run_direction.output_language,
        "output_style": run_direction.output_style,
        "output_formats": list(run_direction.output_formats),
        "cadence": run_direction.cadence,
    }
    payload["scope_fingerprint"] = canonical_fingerprint(payload)
    return GuidanceReuseScopeV1.model_validate(payload, strict=True)


def _latest_by_revision(records, *, key):
    selected = {}
    for record in records:
        identity = key(record)
        current = selected.get(identity)
        if current is None or record.draft_revision > current.draft_revision:
            selected[identity] = record
    return selected


def build_run_guidance_snapshot(
    *,
    history,
    successor_contract,
    request: RunSuccessorStartRequest,
    snapshot_id: str,
    snapshot_event_id: str,
    derived_id,
):
    """Freeze the complete deterministic compatible guidance set."""

    successor_scope = build_guidance_reuse_scope(successor_contract.run_direction)
    receipts = {
        (receipt.run_id, receipt.transaction_id): receipt
        for source_snapshot in history.snapshots
        for receipt in source_snapshot.transactions
    }
    run_order = {
        source_snapshot.run.run_id: (
            source_snapshot.run.created_at,
            source_snapshot.run.run_id,
        )
        for source_snapshot in history.snapshots
    }
    source_snapshots = {
        source_snapshot.run.run_id: source_snapshot
        for source_snapshot in history.snapshots
    }
    all_drafts = [
        draft
        for source_snapshot in history.snapshots
        for draft in source_snapshot.post_final_guidance_drafts
    ]
    latest_drafts = _latest_by_revision(
        all_drafts,
        key=lambda item: (item.run_id, item.guidance_id),
    )
    candidates = sorted(
        latest_drafts.values(),
        key=lambda item: (
            run_order.get(item.run_id, ("", item.run_id)),
            item.guidance_id,
            item.draft_revision,
        ),
    )

    candidate_payloads: list[dict[str, object]] = []
    staged: list[tuple[object, object | None, GuidanceReuseScopeV1, str]] = []
    for draft in candidates:
        source = source_snapshots.get(draft.run_id)
        if source is None or len(source.run_contract_bindings) != 1:
            raise CoreRunError("guidance_binding_invalid")
        source_scope = build_guidance_reuse_scope(
            source.run_contract_bindings[0].run_direction
        )
        dispositions = [
            item
            for item in source.post_final_finding_dispositions
            if item.disposition_id == draft.disposition_id
        ]
        results = [
            item
            for item in source.post_final_assessment_results
            if item.assessment_result_id == draft.assessment_result_id
        ]
        statuses = [
            item
            for item in source.post_final_guidance_statuses
            if item.guidance_id == draft.guidance_id
        ]
        if len(dispositions) != 1 or len(results) != 1:
            raise CoreRunError("guidance_binding_invalid")
        disposition = dispositions[0]
        result = results[0]
        if any(
            (item.run_id, item.accepted_transaction_id) not in receipts
            for item in (draft, disposition, result)
        ):
            raise CoreRunError("guidance_binding_invalid")
        if (
            disposition.decision != "accept"
            or disposition.run_id != draft.run_id
            or disposition.assessment_result_id != draft.assessment_result_id
            or disposition.assessment_result_fingerprint
            != draft.assessment_result_fingerprint
            or disposition.finding_id != draft.finding_id
            or disposition.finding_fingerprint != draft.finding_fingerprint
            or disposition.disposition_fingerprint != draft.disposition_fingerprint
            or result.run_id != draft.run_id
            or result.result_fingerprint != draft.assessment_result_fingerprint
            or result.finalized_lineage_fingerprint
            != draft.finalized_lineage_fingerprint
        ):
            raise CoreRunError("guidance_binding_invalid")
        current_status = None
        if statuses:
            try:
                current_status = max(
                    statuses,
                    key=lambda item: (
                        receipts[
                            (item.run_id, item.accepted_transaction_id)
                        ].committed_revision
                    ),
                )
            except KeyError as exc:
                raise CoreRunError("guidance_binding_invalid") from exc
            if (
                current_status.run_id != draft.run_id
                or current_status.guidance_sha256 != draft.guidance_sha256
            ):
                raise CoreRunError("guidance_binding_invalid")

        if not request.include_approved_guidance:
            reason = "reuse_not_requested"
        elif (
            current_status is None
            or current_status.draft_revision != draft.draft_revision
        ):
            reason = "guidance_unapproved"
        elif current_status.status in {"deactivated", "reverted"}:
            reason = "guidance_inactive"
        elif current_status.status == "superseded":
            reason = "guidance_superseded"
        elif current_status.status != "approved":
            reason = "guidance_unapproved"
        elif source_scope.scope_fingerprint != successor_scope.scope_fingerprint:
            reason = "guidance_scope_mismatch"
        else:
            reason = "approved_scope_match"
        candidate_payloads.append(
            {
                "source_run_id": draft.run_id,
                "guidance_id": draft.guidance_id,
                "draft_revision": draft.draft_revision,
                "draft_fingerprint": draft.draft_fingerprint,
                "status_revision_id": (
                    None
                    if current_status is None
                    else current_status.status_revision_id
                ),
                "status_fingerprint": (
                    None
                    if current_status is None
                    else current_status.status_fingerprint
                ),
                "source_scope_fingerprint": source_scope.scope_fingerprint,
                "reason_code": reason,
            }
        )
        staged.append((draft, current_status, source_scope, reason))

    candidate_set_fingerprint = canonical_fingerprint(
        {"candidates": candidate_payloads}
    )
    decisions: list[RunGuidanceSelectionDecisionRecord] = []
    items: list[RunGuidanceSnapshotItemRecord] = []
    for draft, current_status, source_scope, reason in staged:
        disposition = next(
            item
            for item in source_snapshots[draft.run_id].post_final_finding_dispositions
            if item.disposition_id == draft.disposition_id
        )
        result = next(
            item
            for item in source_snapshots[draft.run_id].post_final_assessment_results
            if item.assessment_result_id == draft.assessment_result_id
        )
        decision_id = derived_id(
            "GUIDANCE-DECISION",
            request.request_id,
            draft.run_id,
            draft.guidance_id,
            str(draft.draft_revision),
        )
        decision_payload = {
            "schema_version": RunGuidanceSelectionDecisionRecord.schema_id,
            "decision_id": decision_id,
            "run_id": request.successor_run_id,
            "snapshot_id": snapshot_id,
            "source_run_id": draft.run_id,
            "guidance_id": draft.guidance_id,
            "draft_revision": draft.draft_revision,
            "status_revision_id": (
                None if current_status is None else current_status.status_revision_id
            ),
            "assessment_result_id": result.assessment_result_id,
            "finding_id": draft.finding_id,
            "disposition_id": disposition.disposition_id,
            "result_fingerprint": result.result_fingerprint,
            "finding_fingerprint": draft.finding_fingerprint,
            "disposition_fingerprint": disposition.disposition_fingerprint,
            "draft_fingerprint": draft.draft_fingerprint,
            "status_fingerprint": (
                None if current_status is None else current_status.status_fingerprint
            ),
            "source_scope_fingerprint": source_scope.scope_fingerprint,
            "successor_scope_fingerprint": successor_scope.scope_fingerprint,
            "selected": reason == "approved_scope_match",
            "reason_code": reason,
        }
        decision_payload["decision_fingerprint"] = canonical_fingerprint(
            decision_payload
        )
        decision = RunGuidanceSelectionDecisionRecord.model_validate(
            decision_payload,
            strict=True,
        )
        decisions.append(decision)
        if not decision.selected:
            continue
        if current_status is None:
            raise CoreRunError("guidance_binding_invalid")
        item_payload = {
            "schema_version": RunGuidanceSnapshotItemRecord.schema_id,
            "item_id": derived_id(
                "GUIDANCE-ITEM",
                request.request_id,
                draft.run_id,
                draft.guidance_id,
                str(draft.draft_revision),
            ),
            "run_id": request.successor_run_id,
            "snapshot_id": snapshot_id,
            "position": len(items),
            "source_run_id": draft.run_id,
            "finalized_lineage_fingerprint": draft.finalized_lineage_fingerprint,
            "assessment_result_id": result.assessment_result_id,
            "assessment_result_fingerprint": result.result_fingerprint,
            "finding_id": draft.finding_id,
            "finding_fingerprint": draft.finding_fingerprint,
            "disposition_id": disposition.disposition_id,
            "disposition_fingerprint": disposition.disposition_fingerprint,
            "guidance_id": draft.guidance_id,
            "draft_revision": draft.draft_revision,
            "draft_fingerprint": draft.draft_fingerprint,
            "status_revision_id": current_status.status_revision_id,
            "status_fingerprint": current_status.status_fingerprint,
            "guidance_text": draft.guidance_text,
            "guidance_sha256": draft.guidance_sha256,
            "reuse_scope": source_scope.model_dump(mode="json"),
        }
        item_payload["item_fingerprint"] = canonical_fingerprint(item_payload)
        items.append(
            RunGuidanceSnapshotItemRecord.model_validate(item_payload, strict=True)
        )

    if (
        len(items) > _MAX_GUIDANCE_ITEMS
        or sum(len(item.guidance_text.encode("utf-8")) for item in items)
        > _MAX_GUIDANCE_UTF8_BYTES
    ):
        raise CoreRunError("approved_guidance_context_limit_exceeded")

    snapshot_payload = {
        "schema_version": RunGuidanceSnapshotRecord.schema_id,
        "snapshot_id": snapshot_id,
        "workspace_id": request.workspace_id,
        "run_id": request.successor_run_id,
        "predecessor_run_id": request.predecessor_run_id,
        "reuse_requested": request.include_approved_guidance,
        "successor_direction_fingerprint": canonical_fingerprint(
            successor_contract.run_direction.model_dump(mode="json")
        ),
        "successor_run_contract_fingerprint": successor_contract.contract_fingerprint,
        "candidate_set_fingerprint": candidate_set_fingerprint,
        "selected_item_ids": [item.item_id for item in items],
        "decision_ids": [item.decision_id for item in decisions],
        "selected_count": len(items),
        "omitted_count": len(decisions) - len(items),
        "snapshot_event_id": snapshot_event_id,
        "accepted_transaction_id": request.request_id,
        "request_fingerprint": request.request_fingerprint,
    }
    snapshot_payload["snapshot_fingerprint"] = canonical_fingerprint(snapshot_payload)
    snapshot = RunGuidanceSnapshotRecord.model_validate(
        snapshot_payload,
        strict=True,
    )
    return snapshot, tuple(decisions), tuple(items)


class CoreRunSuccessorService:
    """Sole writer for one normal same-workspace reference successor."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        clock: _Clock | None = None,
    ) -> None:
        try:
            self.workspace = Path(workspace).expanduser().resolve(strict=True)
            if not self.workspace.is_dir():
                raise ValueError
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CoreRunError("core_run_request_invalid") from exc
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def start_successor(self, request: RunSuccessorStartRequest) -> CoreRunResult:
        try:
            return self._start_successor(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def _start_successor(self, request: RunSuccessorStartRequest) -> CoreRunResult:
        from .checkout import (
            prepare_cross_run_checkout_effect,
            publish_checkout_effect,
            stage_checkout_effect,
        )
        from .policy import (
            CORE_ARTIFACT_IDS,
            INTERNAL_CONTRACT_ARTIFACT_IDS,
            blob_workspace_path,
            derived_id,
            run_contract_fingerprint,
            transaction_type_for,
        )
        from .service import (
            _artifact_pair,
            _derive_runtime_source_plan,
            workspace_input_fingerprints,
        )
        from .verifier import CoreRunDomainVerifier, resolve_core_replay

        fingerprint = request.request_fingerprint
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db",
            clock=self._clock,
        ) as store:
            replay = resolve_core_replay(
                store,
                run_id=request.successor_run_id,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            (
                workspace_config_sha256,
                sources_config_sha256,
                sources_content,
            ) = workspace_input_fingerprints(
                self.workspace,
                include_sources_content=True,
            )
            if (
                workspace_config_sha256 != request.workspace_config_sha256
                or sources_config_sha256 != request.sources_config_sha256
            ):
                raise CoreRunError("core_run_contract_mismatch")
            verifier = CoreRunDomainVerifier()
            verified = verifier.verify(store, request.predecessor_run_id)
            snapshot = verified.snapshot
            head = snapshot.workspace_run_head
            if (
                request.workspace_id != snapshot.workspace_id
                or request.runtime != snapshot.run.runtime
                or request.predecessor_run_id == request.successor_run_id
                or head is None
                or head.current_run_id != request.expected_head_run_id
                or head.current_run_id != request.predecessor_run_id
                or snapshot.store_revision != request.expected_store_revision
                or request.expected_workspace_revision != snapshot.store_revision
                or request.role_topology != verified.binding.role_topology
                or request.gate_strictness != verified.binding.gate_strictness
                or request.input_governance_required
                != verified.binding.input_governance_required
                or request.role_topology
                not in verified.runtime_adapter.supported_role_topologies
            ):
                raise CoreRunError("successor_history_invalid")
            classify_successor_start_legality(snapshot).require_allowed()
            history = store.load_history()
            verifier.verify_history(history)
            now = self._now()

            adapter_payload = verified.runtime_adapter.model_dump(
                mode="json",
                exclude_unset=False,
            )
            adapter_payload.update(run_id=request.successor_run_id)
            adapter_payload.pop("binding_fingerprint", None)
            adapter_payload["binding_fingerprint"] = canonical_fingerprint(
                adapter_payload
            )
            adapter_bytes = canonical_json_bytes(adapter_payload)
            source_plan = _derive_runtime_source_plan(
                sources_content,
                run_id=request.successor_run_id,
                sources_config_sha256=sources_config_sha256,
                run_direction=request.run_direction,
                workspace_root=self.workspace,
            )
            source_bytes = canonical_json_bytes(
                source_plan.model_dump(mode="json", exclude_unset=False)
            )
            frozen_payloads = (
                store.read_artifact_revision_bytes(
                    request.predecessor_run_id,
                    verified.binding.stage_specs_artifact.artifact_id,
                    verified.binding.stage_specs_artifact.revision,
                ),
                store.read_artifact_revision_bytes(
                    request.predecessor_run_id,
                    verified.binding.artifact_contracts_artifact.artifact_id,
                    verified.binding.artifact_contracts_artifact.revision,
                ),
                store.read_artifact_revision_bytes(
                    request.predecessor_run_id,
                    verified.binding.policy_pack_artifact.artifact_id,
                    verified.binding.policy_pack_artifact.revision,
                ),
                adapter_bytes,
                source_bytes,
            )
            contract_artifacts = [
                _artifact_pair(
                    run_id=request.successor_run_id,
                    artifact_id=artifact_id,
                    revision=1,
                    path=blob_workspace_path(sha256_hex(content)),
                    artifact_format="json",
                    content=content,
                    producer_kind="control_tool",
                    producer_id="core-v2-initializer",
                    created_at=now,
                    required=True,
                )
                + (content,)
                for artifact_id, content in zip(
                    INTERNAL_CONTRACT_ARTIFACT_IDS,
                    frozen_payloads,
                )
            ]
            contract_values = verified.binding.model_dump(
                mode="json",
                exclude_unset=False,
            )
            contract_values.update(
                run_id=request.successor_run_id,
                run_direction=request.run_direction.model_dump(mode="json"),
                workspace_config_sha256=workspace_config_sha256,
                sources_config_sha256=sources_config_sha256,
                role_topology=request.role_topology,
                gate_strictness=request.gate_strictness,
                input_governance_required=request.input_governance_required,
                runtime_adapter_sha256=sha256_hex(adapter_bytes),
                runtime_adapter_fingerprint=adapter_payload["binding_fingerprint"],
                runtime_source_plan_sha256=sha256_hex(source_bytes),
                runtime_source_plan_fingerprint=source_plan.source_plan_fingerprint,
                created_at=now,
                accepted_transaction_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            initialized_event_id = derived_id(
                "EVT-SUCCESSOR-INIT",
                request.request_id,
                fingerprint,
            )
            contract_values["initialization_event_id"] = initialized_event_id
            contract_values["contract_fingerprint"] = run_contract_fingerprint(
                runtime=request.runtime,
                stage_specs_schema=verified.binding.stage_specs_schema,
                stage_specs_sha256=verified.binding.stage_specs_sha256,
                artifact_contracts_schema=verified.binding.artifact_contracts_schema,
                artifact_contracts_sha256=verified.binding.artifact_contracts_sha256,
                policy_pack_schema=verified.binding.policy_pack_schema,
                policy_pack_name=verified.binding.policy_pack_name,
                policy_pack_sha256=verified.binding.policy_pack_sha256,
                runtime_adapter_sha256=contract_values["runtime_adapter_sha256"],
                runtime_adapter_fingerprint=contract_values[
                    "runtime_adapter_fingerprint"
                ],
                runtime_source_plan_sha256=contract_values[
                    "runtime_source_plan_sha256"
                ],
                runtime_source_plan_fingerprint=contract_values[
                    "runtime_source_plan_fingerprint"
                ],
                run_direction=request.run_direction.model_dump(mode="json"),
                workspace_config_sha256=workspace_config_sha256,
                sources_config_sha256=sources_config_sha256,
                role_topology=request.role_topology,
                gate_strictness=request.gate_strictness,
                input_governance_required=request.input_governance_required,
            )
            contract = type(verified.binding).model_validate(
                contract_values,
                strict=True,
            )
            transition_id = derived_id(
                "HEAD-SUCCESSOR",
                request.request_id,
                fingerprint,
            )
            successor_event_id = derived_id(
                "EVT-SUCCESSOR",
                request.request_id,
                fingerprint,
            )
            snapshot_id = derived_id(
                "GUIDANCE-SNAPSHOT",
                request.request_id,
                fingerprint,
            )
            snapshot_event_id = derived_id(
                "EVT-GUIDANCE-SNAPSHOT",
                request.request_id,
                fingerprint,
            )
            transition = RunHeadTransitionRecord.model_validate(
                {
                    "schema_version": RunHeadTransitionRecord.schema_id,
                    "head_transition_id": transition_id,
                    "workspace_id": request.workspace_id,
                    "predecessor_run_id": request.predecessor_run_id,
                    "successor_run_id": request.successor_run_id,
                    "prior_workspace_revision": request.expected_workspace_revision,
                    "successor_workspace_revision": request.expected_workspace_revision
                    + 1,
                    "reason_code": "human_started_successor",
                    "successor_disposition": "reference",
                    "created_at": now,
                    "transition_event_id": successor_event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            guidance_snapshot, decisions, items = build_run_guidance_snapshot(
                history=history,
                successor_contract=contract,
                request=request,
                snapshot_id=snapshot_id,
                snapshot_event_id=snapshot_event_id,
                derived_id=derived_id,
            )
            checkout = prepare_cross_run_checkout_effect(
                workspace=self.workspace,
                snapshot=snapshot,
                successor_run_id=request.successor_run_id,
                transaction_id=request.request_id,
                created_at=self._clock(),
            )
            unit = store.begin(
                request.successor_run_id,
                request.request_id,
                transaction_type_for("run_successor_start"),
                request.expected_store_revision,
            )
            unit.put_run(
                RunIdentity.model_validate(
                    {
                        "schema_version": RunIdentity.schema_id,
                        "run_id": request.successor_run_id,
                        "workspace_id": request.workspace_id,
                        "runtime": request.runtime,
                        "created_at": now,
                    },
                    strict=True,
                )
            )
            unit.put_workspace_run_head(
                WorkspaceRunHead.model_validate(
                    {
                        "schema_version": WorkspaceRunHead.schema_id,
                        "workspace_id": request.workspace_id,
                        "current_run_id": request.successor_run_id,
                        "updated_at": now,
                    },
                    strict=True,
                )
            )
            unit.put_run_contract_binding(contract)
            for artifact, revision, content in contract_artifacts:
                unit.put_artifact(artifact)
                unit.put_artifact_revision(revision, content)
            artifact_contracts = {
                str(item["artifact_id"]): item for item in verified.artifacts
            }
            for artifact_id in CORE_ARTIFACT_IDS:
                row = artifact_contracts[artifact_id]
                unit.put_artifact(
                    ArtifactRecord.model_validate(
                        {
                            "schema_version": ArtifactRecord.schema_id,
                            "run_id": request.successor_run_id,
                            "artifact_id": artifact_id,
                            "current_revision": 0,
                            "status": "expected",
                            "required": bool(row["required"]),
                            "path": row["path"],
                            "format": row["format"],
                        },
                        strict=True,
                    )
                )
            for position, stage_contract in enumerate(verified.stages):
                stage_id = str(stage_contract["stage_id"])
                status = "ready" if position == 0 else "pending"
                event_id = derived_id(
                    "EVT-SUCCESSOR-STAGE",
                    request.request_id,
                    stage_id,
                )
                transition_record_id = derived_id(
                    "TRANSITION-SUCCESSOR",
                    request.request_id,
                    stage_id,
                )
                unit.put_stage_state(
                    StageState.model_validate(
                        {
                            "schema_version": StageState.schema_id,
                            "run_id": request.successor_run_id,
                            "stage_id": stage_id,
                            "status": status,
                            "revision": 0,
                            "updated_at": now,
                        },
                        strict=True,
                    )
                )
                unit.append_stage_transition(
                    StageTransitionRecord.model_validate(
                        {
                            "schema_version": StageTransitionRecord.schema_id,
                            "transition_id": transition_record_id,
                            "run_id": request.successor_run_id,
                            "stage_id": stage_id,
                            "transition_kind": "initialize",
                            "requested_decision": None,
                            "prior_status": None,
                            "prior_revision": None,
                            "result_status": status,
                            "result_revision": 0,
                            "reason": "normal successor stage initialized",
                            "run_contract_fingerprint": contract.contract_fingerprint,
                            "actor": "system",
                            "producer_invocation_id": None,
                            "producer_tool_id": None,
                            "producer_result_status": None,
                            "producer_result_fingerprint": None,
                            "producer_implementation": None,
                            "producer_version": None,
                            "topology": None,
                            "satisfaction_source_kind": None,
                            "satisfied_by_id": None,
                            "created_at": now,
                            "transition_event_id": event_id,
                            "accepted_transaction_id": request.request_id,
                            "request_fingerprint": fingerprint,
                        },
                        strict=True,
                    )
                )
                unit.append_event(
                    self._event(
                        event_id,
                        request,
                        "stage_status_changed",
                        stage_id=stage_id,
                    )
                )
            unit.append_run_integrity_record(
                RunIntegrityRecord.model_validate(
                    {
                        "schema_version": RunIntegrityRecord.schema_id,
                        "run_id": request.successor_run_id,
                        "integrity_revision": 1,
                        "status": "clean",
                        "prior_integrity_revision": None,
                        "affected_artifact_id": None,
                        "affected_artifact_revision": None,
                        "expected_workspace_path": None,
                        "expected_sha256": None,
                        "observed_entry_kind": None,
                        "observed_sha256": None,
                        "reason_code": None,
                        "first_detected_at": None,
                        "first_detected_event_id": None,
                        "accepted_transaction_id": request.request_id,
                        "request_fingerprint": fingerprint,
                    },
                    strict=True,
                )
            )
            unit.put_run_head_transition(transition)
            unit.put_run_guidance_snapshot(guidance_snapshot)
            for decision in decisions:
                unit.put_run_guidance_selection_decision(decision)
            for item in items:
                unit.put_run_guidance_snapshot_item(item)
            unit.append_event(
                self._event(
                    initialized_event_id,
                    request,
                    "run_initialized",
                    stage_id="doctor",
                )
            )
            unit.append_event(
                self._event(
                    successor_event_id,
                    request,
                    "run_successor_started",
                    primary_record_id=transition_id,
                    bind=True,
                )
            )
            unit.append_event(
                self._event(
                    snapshot_event_id,
                    request,
                    "run_guidance_snapshot_frozen",
                )
            )
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(
                    store,
                    request.successor_run_id,
                )
            )
            published, _warnings = publish_checkout_effect(
                workspace=self.workspace,
                store=store,
                prepared=checkout,
            )
            if not published:
                return CoreRunResult(
                    status="commit_outcome_unknown",
                    error_code="commit_outcome_unknown",
                )
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=transition_id,
            )

    def _event(
        self,
        event_id: str,
        request: RunSuccessorStartRequest,
        event_type: str,
        *,
        stage_id: str | None = None,
        primary_record_id: str | None = None,
        bind: bool = False,
    ) -> EventEnvelope:
        return EventEnvelope.model_validate(
            {
                "schema_version": EventEnvelope.schema_id,
                "event_id": event_id,
                "run_id": request.successor_run_id,
                "event_type": event_type,
                "created_at": self._now(),
                "actor": "system",
                "transaction_id": request.request_id,
                "stage_id": stage_id,
                "decision": "continue",
                "reason": event_type.replace("_", " "),
                "metadata": {},
                "core_run_binding": (
                    CoreRunEventBinding(
                        request_id=request.request_id,
                        request_fingerprint=request.request_fingerprint,
                        effect_kind="run_successor_start",
                        primary_record_id=primary_record_id,
                        outcome="committed",
                    )
                    if bind and primary_record_id is not None
                    else None
                ),
            },
            strict=True,
        )

    def _now(self) -> str:
        return (
            self._clock()
            .astimezone(timezone.utc)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )


__all__ = [
    "CoreRunSuccessorService",
    "SuccessorStartLegality",
    "build_guidance_reuse_scope",
    "build_run_guidance_snapshot",
    "classify_successor_start_legality",
]
