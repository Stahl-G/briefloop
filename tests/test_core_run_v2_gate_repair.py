"""Pure State x Path coverage for the bounded Gate-repair classifier."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from multi_agent_brief.core_run_v2.gate_repair import (
    classify_gate_repair_legality,
)


def _finding(
    finding_id: str,
    *,
    repair_owner: str = "editor",
    stage_id: str = "editor",
    artifact_id: str = "audited_brief",
    source_id: str | None = None,
    claim_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        evaluation_id="EVAL-1",
        finding_id=finding_id,
        gate_id="final_abstract_quality",
        blocking_level="blocking",
        repair_owner=repair_owner,
        stage_id=stage_id,
        artifact_id=artifact_id,
        source_id=source_id,
        claim_id=claim_id,
        accepted_transaction_id="REQ-GATE-1",
    )


def _snapshot(
    findings: tuple[SimpleNamespace, ...],
    *,
    authorization: bool = True,
    repair_budget: int = 1,
    cycle: SimpleNamespace | None = None,
    outcome: SimpleNamespace | None = None,
    contaminated: bool = False,
    legacy_repair: bool = False,
) -> SimpleNamespace:
    evaluation = SimpleNamespace(
        evaluation_id="EVAL-1",
        gate_batch_id="GATE-BATCH-1",
        stage_id="auditor",
        gate_id="final_abstract_quality",
        report_artifact=SimpleNamespace(
            artifact_id="auditor_quality_gate_report",
            revision=1,
        ),
        finding_ids=[item.finding_id for item in findings],
        blocking=True,
        accepted_transaction_id="REQ-GATE-1",
    )
    return SimpleNamespace(
        run=SimpleNamespace(run_id="RUN-1"),
        artifacts=(
            SimpleNamespace(
                artifact_id="auditor_quality_gate_report",
                current_revision=1,
            ),
            SimpleNamespace(artifact_id="audited_brief", current_revision=1),
        ),
        artifact_revisions=(
            SimpleNamespace(
                artifact_id="auditor_quality_gate_report",
                revision=1,
                sha256="a" * 64,
            ),
            SimpleNamespace(
                artifact_id="audited_brief",
                revision=1,
                sha256="b" * 64,
            ),
        ),
        transactions=(
            SimpleNamespace(
                transaction_id="REQ-GATE-1",
                committed_revision=10,
            ),
        ),
        gate_evaluations=(evaluation,),
        gate_findings=findings,
        gate_artifact_bindings=(
            SimpleNamespace(
                evaluation_id="EVAL-1",
                artifact_id="audited_brief",
                artifact_revision=1,
                artifact_sha256="b" * 64,
            ),
        ),
        run_execution_authorizations=(
            (
                SimpleNamespace(
                    completion_target="finalized_local",
                    repair_budget=repair_budget,
                ),
            )
            if authorization
            else ()
        ),
        gate_repair_cycles=(() if cycle is None else (cycle,)),
        gate_repair_artifact_bindings=(),
        gate_repair_outcomes=(() if outcome is None else (outcome,)),
        invocations=(),
        run_integrity_records=(
            SimpleNamespace(status="contaminated" if contaminated else "clean"),
        ),
        repair_cycles=(
            (SimpleNamespace(repair_id="REPAIR-LEGACY"),)
            if legacy_repair
            else ()
        ),
        artifact_supersessions=(),
        repair_completions=(),
        recovery_completions=(),
        finalizations=(),
        package_ready_records=(),
        approvals=(),
        delivery_authorizations=(),
        delivery_attempts=(),
        delivery_results=(),
    )


@pytest.mark.parametrize(
    ("findings", "expected_state", "expected_reason"),
    (
        (
            (_finding("FINDING-EDITOR"),),
            "eligible",
            None,
        ),
        (
            (
                _finding(
                    "FINDING-SOURCE",
                    repair_owner="source-provider",
                    stage_id="source-discovery",
                    artifact_id="source_candidates",
                    source_id="SRC-1",
                ),
            ),
            "source_or_non_editor_block",
            "gate_repair_source_or_non_editor_block",
        ),
        (
            (
                _finding("FINDING-EDITOR"),
                _finding(
                    "FINDING-SOURCE",
                    repair_owner="source-provider",
                    stage_id="source-discovery",
                    artifact_id="source_candidates",
                    source_id="SRC-1",
                ),
            ),
            "mixed_or_ambiguous_scope",
            "gate_repair_mixed_or_ambiguous_scope",
        ),
        (
            (
                _finding(
                    "FINDING-AMBIGUOUS",
                    repair_owner="auditor",
                    stage_id="auditor",
                ),
            ),
            "mixed_or_ambiguous_scope",
            "gate_repair_mixed_or_ambiguous_scope",
        ),
    ),
)
def test_gate_repair_scope_classifier_is_value_free_and_exact(
    findings: tuple[SimpleNamespace, ...],
    expected_state: str,
    expected_reason: str | None,
) -> None:
    result = classify_gate_repair_legality(_snapshot(findings))
    assert result.state == expected_state
    assert result.reason_code == expected_reason


def test_gate_repair_requires_explicit_authorization_and_exact_budget() -> None:
    finding = (_finding("FINDING-EDITOR"),)
    unauthorized = classify_gate_repair_legality(
        _snapshot(finding, authorization=False)
    )
    exhausted = classify_gate_repair_legality(_snapshot(finding, repair_budget=0))
    contaminated = classify_gate_repair_legality(_snapshot(finding, contaminated=True))

    assert (unauthorized.state, unauthorized.reason_code) == (
        "not_authorized",
        "gate_repair_not_authorized",
    )
    assert (exhausted.state, exhausted.reason_code) == (
        "budget_exhausted",
        "gate_repair_budget_exhausted",
    )
    assert (contaminated.state, contaminated.reason_code) == (
        "invalid",
        "control_store_integrity_invalid",
    )


@pytest.mark.parametrize(
    ("disposition", "expected_state", "expected_reason"),
    (
        (None, "active", None),
        ("passed", "passed", None),
        (
            "blocked",
            "failed_after_attempt",
            "gate_repair_failed_after_attempt",
        ),
    ),
)
def test_gate_repair_cycle_is_single_attempt(
    disposition: str | None,
    expected_state: str,
    expected_reason: str | None,
) -> None:
    cycle = SimpleNamespace(
        gate_repair_id="GATE-REPAIR-1",
        run_id="RUN-1",
        source_gate_batch_id="GATE-BATCH-1",
    )
    outcome = (
        None
        if disposition is None
        else SimpleNamespace(
            gate_repair_id=cycle.gate_repair_id,
            disposition=disposition,
            replacement_gate_batch_id="GATE-BATCH-1",
        )
    )
    result = classify_gate_repair_legality(
        _snapshot(
            (_finding("FINDING-EDITOR"),),
            cycle=cycle,
            outcome=outcome,
        )
    )
    assert result.state == expected_state
    assert result.reason_code == expected_reason


def test_active_gate_repair_contamination_is_human_block_not_legacy_repair() -> None:
    cycle = SimpleNamespace(
        gate_repair_id="GATE-REPAIR-1",
        run_id="RUN-1",
        source_gate_batch_id="GATE-BATCH-1",
    )
    result = classify_gate_repair_legality(
        _snapshot(
            (_finding("FINDING-EDITOR"),),
            cycle=cycle,
            contaminated=True,
        )
    )
    assert (result.state, result.reason_code) == (
        "failed_after_attempt",
        "gate_repair_failed_after_attempt",
    )


def test_gate_repair_and_legacy_repair_graph_is_invalid() -> None:
    cycle = SimpleNamespace(
        gate_repair_id="GATE-REPAIR-1",
        run_id="RUN-1",
        source_gate_batch_id="GATE-BATCH-1",
    )
    result = classify_gate_repair_legality(
        _snapshot(
            (_finding("FINDING-EDITOR"),),
            cycle=cycle,
            legacy_repair=True,
        )
    )
    assert (result.state, result.reason_code) == (
        "invalid",
        "control_store_integrity_invalid",
    )
