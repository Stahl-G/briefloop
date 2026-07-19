"""Strict read-only contracts at the runtime host boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from multi_agent_brief.contracts.v2 import (
    ContractId,
    CleanText,
    CoreRunNextAction,
    NonNegativeInt,
    Sha256,
    StrictModel,
    WorkspacePath,
)


class RoleTaskEnvelope(StrictModel):
    schema_id = "briefloop.role_task_envelope.v2"

    schema_version: Literal["briefloop.role_task_envelope.v2"]
    run_id: ContractId
    invocation_id: ContractId
    store_revision: NonNegativeInt
    action: CoreRunNextAction
    action_fingerprint: Sha256
    role_id: ContractId
    stage_id: ContractId
    scratch_directory: WorkspacePath
    allowed_output_filenames: list[ContractId] = Field(min_length=1)
    proposal_schema_id: ContractId
    adapter_binding_fingerprint: Sha256
    source_plan_fingerprint: Sha256
    executor_kind: Literal[
        "main_session", "delegated_specialist", "declared_existing_route"
    ]
    context_mode: Literal[
        "shared_session", "independent_stage_context", "delegated_context",
        "declared_existing_context"
    ]
    review_mode: Literal[
        "stage_separated_self_review", "independent_stage_context",
        "delegated_review", "declared_existing_route"
    ]
    dispatch_instruction: Literal[
        "execute_in_current_session", "delegate_exact_role", "use_declared_route"
    ]
    task_instructions: CleanText

    @model_validator(mode="after")
    def exact_action_binding(self) -> "RoleTaskEnvelope":
        if self.action.run_id != self.run_id:
            raise ValueError("envelope run does not match action")
        if self.action.action_fingerprint != self.action_fingerprint:
            raise ValueError("envelope action fingerprint mismatch")
        if self.action.stage_id != self.stage_id or self.action.role_id not in {
            self.role_id,
            None,
        }:
            raise ValueError("envelope owner does not match action")
        if self.allowed_output_filenames != sorted(set(self.allowed_output_filenames)):
            raise ValueError("allowed output filenames must be sorted and unique")
        return self


class RuntimeDiagnoseReport(StrictModel):
    schema_id = "briefloop.runtime_diagnose_report.v2"

    schema_version: Literal["briefloop.runtime_diagnose_report.v2"]
    run_id: ContractId
    store_revision: NonNegativeInt
    store_valid: bool
    adapter_binding_valid: bool
    projection_drift: bool | None = None
    next_action: CoreRunNextAction


class RuntimeInvocationResult(StrictModel):
    schema_id = "briefloop.runtime_invocation_result.v2"

    schema_version: Literal["briefloop.runtime_invocation_result.v2"]
    run_id: ContractId
    invocation_id: ContractId
    status: Literal["committed", "replayed", "rejected_recorded"]
    transaction_id: ContractId
    store_revision: NonNegativeInt
    next_action: CoreRunNextAction


__all__ = [
    "RoleTaskEnvelope",
    "RuntimeDiagnoseReport",
    "RuntimeInvocationResult",
]
