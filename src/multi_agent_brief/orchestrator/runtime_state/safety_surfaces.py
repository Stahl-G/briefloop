"""Test-only registry of runtime safety surfaces guarded by structural tests.

This module is intentionally metadata-only and is not imported by production
runtime paths. It lets structural tests assert that each control-plane safety
surface keeps one interpreter and explicit read/write adapters without
importing those surfaces into runtime_state's public facade.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetySurface:
    """Structural contract for one fail-closed control-plane surface."""

    surface_id: str
    module: str
    verdict_class: str
    interpreter: str
    read_adapter: str
    write_adapter: str
    optimistic_constructors: tuple[str, ...] = ()
    required_runtime_refs: tuple[tuple[str, str], ...] = ()


SAFETY_SURFACES: dict[str, SafetySurface] = {
    "run_integrity": SafetySurface(
        surface_id="run_integrity",
        module="multi_agent_brief.orchestrator.run_integrity",
        verdict_class="RunIntegrityVerdict",
        interpreter="interpret_run_integrity",
        read_adapter="project_for_read",
        write_adapter="require_persistable",
        optimistic_constructors=("_clean_run_integrity",),
        required_runtime_refs=(
            (
                "multi_agent_brief.orchestrator.runtime_state.workflow",
                "require_persistable",
            ),
            (
                "multi_agent_brief.orchestrator.runtime_state.operations",
                "workflow_with_persistable_run_integrity",
            ),
            (
                "multi_agent_brief.orchestrator.runtime_state.event_log",
                "workflow_with_persistable_run_integrity",
            ),
        ),
    ),
    "quality_gate_binding": SafetySurface(
        surface_id="quality_gate_binding",
        module="multi_agent_brief.quality_gates.contract",
        verdict_class="QualityGateBindingVerdict",
        interpreter="interpret_quality_gate_binding",
        read_adapter="project_quality_gate_binding_for_read",
        write_adapter="require_quality_gate_binding_pass",
        required_runtime_refs=(
            (
                "multi_agent_brief.orchestrator.runtime_state.completion_gates",
                "interpret_quality_gate_binding",
            ),
            (
                "multi_agent_brief.orchestrator.runtime_state.completion_gates",
                "require_quality_gate_binding_pass",
            ),
        ),
    ),
    "finalize_audit_binding": SafetySurface(
        surface_id="finalize_audit_binding",
        module="multi_agent_brief.outputs.finalize",
        verdict_class="FinalizeAuditBindingVerdict",
        interpreter="interpret_finalize_audit_binding",
        read_adapter="project_finalize_audit_binding_for_read",
        write_adapter="require_finalize_audit_binding_pass",
        required_runtime_refs=(
            (
                "multi_agent_brief.orchestrator.runtime_state.completion_gates",
                "interpret_finalize_audit_binding",
            ),
            (
                "multi_agent_brief.orchestrator.runtime_state.completion_gates",
                "require_finalize_audit_binding_pass",
            ),
        ),
    ),
    "frozen_artifact_integrity": SafetySurface(
        surface_id="frozen_artifact_integrity",
        module="multi_agent_brief.orchestrator.runtime_state.artifact_registry",
        verdict_class="FrozenArtifactIntegrityVerdict",
        interpreter="interpret_frozen_artifact_integrity",
        read_adapter="project_frozen_artifact_integrity_for_read",
        write_adapter="require_frozen_artifact_integrity_pass",
        required_runtime_refs=(
            (
                "multi_agent_brief.orchestrator.runtime_state.operations",
                "interpret_frozen_artifact_integrity",
            ),
            (
                "multi_agent_brief.orchestrator.runtime_state.operations",
                "require_frozen_artifact_integrity_pass",
            ),
        ),
    ),
    "stage_completion": SafetySurface(
        surface_id="stage_completion",
        module="multi_agent_brief.orchestrator.runtime_state.workflow",
        verdict_class="StageCompletionVerdict",
        interpreter="interpret_stage_completion",
        read_adapter="project_stage_completion_for_read",
        write_adapter="require_stage_completion_persistable",
        required_runtime_refs=(
            (
                "multi_agent_brief.orchestrator.runtime_state.workflow",
                "require_stage_completion_persistable",
            ),
            (
                "multi_agent_brief.orchestrator.runtime_state.artifact_registry",
                "interpret_stage_completion",
            ),
            (
                "multi_agent_brief.orchestrator.runtime_state.artifact_registry",
                "project_stage_completion_for_read",
            ),
        ),
    ),
}


__all__ = ["SAFETY_SURFACES", "SafetySurface"]
