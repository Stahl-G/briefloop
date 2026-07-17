from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from tests import test_core_run_v2 as core_fixture

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import (
    Approval,
    ApprovalPackageBinding,
    ArtifactRecord,
    ArtifactRevision,
    CoreRunInitializeRequest,
    CoreRunEventBinding,
    DeliveryAttemptRecord,
    DeliveryAuthorizationRecord,
    DeliveryResultRecord,
    EventEnvelope,
    FinalizationRecord,
    FinalizeRenderRecord,
    GateArtifactBinding,
    GateEvaluationRecord,
    PackageArtifactBinding,
    PackageReadyRecord,
    RunArchiveArtifactBinding,
    RunArchiveRecord,
    StageArtifactBinding,
    StageGateBinding,
    StageState,
    StageTransitionRecord,
    TransactionReceipt,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2 import CoreRunService
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.lineage import classify_current_audit_promotion
from multi_agent_brief.core_run_v2.gates import (
    _gate_finding_record,
    _replay_gate_outcomes,
)
from multi_agent_brief.core_run_v2.policy import (
    archive_artifact_usage,
    derived_id,
    transaction_type_for,
)
from multi_agent_brief.core_run_v2.recovery import CoreEffect
from multi_agent_brief.core_run_v2.terminal import (
    TerminalEffectSubject,
    classify_terminal_effect_authorization,
    classify_terminal_legality,
    classify_terminal_state,
)
from multi_agent_brief.core_run_v2.verifier import (
    CoreRunDomainVerifier,
    _verified_core_receipt_binding,
    resolve_core_replay,
)
from multi_agent_brief.quality_gates.contract import GATE_IDS

RUN_ID = "RUN-TERMINAL-PREFIX-001"


def _finalize_ready_workspace(tmp_path: Path) -> tuple[Path, str, object]:
    workspace = core_fixture._workspace(tmp_path)
    core_fixture._advance_to_finalize_ready(workspace)
    return workspace, core_fixture.RUN_ID, core_fixture.CLOCK


def _commit_finalize_render(
    workspace: Path,
    run_id: str,
    clock: object,
) -> tuple[TransactionReceipt, str, FinalizeRenderRecord]:
    transaction_id = "REQ-TERMINAL-RENDER-001"
    render_id = "RENDER-TERMINAL-PERSISTED-001"
    event_id = "EVT-TERMINAL-RENDER-PERSISTED-001"
    reader_bytes = (
        b"# ExampleCo reader brief\n\n## Executive Summary\n\n"
        b"ExampleCo opened a public pilot facility on 2026-07-14.\n"
    )
    reader_digest = sha256_hex(reader_bytes)
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "finalize_render",
            "render_id": render_id,
            "reader_sha256": reader_digest,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        promotion = classify_current_audit_promotion(
            before,
            store.read_artifact_revision_bytes,
        )
        assert promotion is not None
        assert promotion.is_current_lineage
        reader_artifact = _record(
            ArtifactRecord,
            run_id=run_id,
            artifact_id="reader_brief",
            current_revision=1,
            status="valid",
            required=True,
            path="output/brief.md",
            format="markdown",
        )
        reader_revision = _record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id=reader_artifact.artifact_id,
            revision=1,
            path=reader_artifact.path,
            sha256=reader_digest,
            size_bytes=len(reader_bytes),
            frozen=True,
            producer_kind="control_tool",
            producer_id="core-v2-finalize-render",
            created_at=core_fixture.NOW,
        )
        render = _record(
            FinalizeRenderRecord,
            render_id=render_id,
            run_id=run_id,
            audit_proposal_id=promotion.proposal_record.proposal_id,
            audited_brief={
                "artifact_id": promotion.brief_revision.artifact_id,
                "revision": promotion.brief_revision.revision,
            },
            audit_report={
                "artifact_id": promotion.report_revision.artifact_id,
                "revision": promotion.report_revision.revision,
            },
            reader_artifacts=[
                {
                    "artifact_id": reader_revision.artifact_id,
                    "revision": reader_revision.revision,
                }
            ],
            reader_clean_status="pass",
            policy_result_fingerprint="a" * 64,
            run_contract_fingerprint=before.run_contract_bindings[
                0
            ].contract_fingerprint,
            created_at=core_fixture.NOW,
            render_event_id=event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        event = _record(
            EventEnvelope,
            event_id=event_id,
            run_id=run_id,
            event_type="owned_artifact_accepted",
            created_at=core_fixture.NOW,
            actor="system",
            transaction_id=transaction_id,
            stage_id="finalize",
            artifact_id=reader_artifact.artifact_id,
            decision="continue",
            reason="deterministic reader render accepted",
            metadata={},
            intake_binding=None,
            core_run_binding=CoreRunEventBinding.model_validate(
                {
                    "request_id": transaction_id,
                    "request_fingerprint": request_fingerprint,
                    "effect_kind": "finalize_render",
                    "primary_record_id": render_id,
                    "outcome": "committed",
                },
                strict=True,
            ),
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("finalize_render"),
            before.store_revision,
        )
        unit.put_artifact(reader_artifact)
        unit.put_artifact_revision(reader_revision, reader_bytes)
        unit.append_event(event)
        unit.put_finalize_render(render)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store,
                run_id,
            )
        )
        assert receipt.event_ids == [event_id]
        assert [
            (item.artifact_id, item.revision) for item in receipt.artifact_revisions
        ] == [(reader_revision.artifact_id, reader_revision.revision)]
        assert [item.render_id for item in receipt.finalize_renders] == [render_id]
    return receipt, request_fingerprint, render


def test_finalize_render_persists_replays_conflicts_and_survives_restart(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    receipt, fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        assert verified.snapshot.finalize_renders == (render,)
        revision = store.current_revision
        replay = resolve_core_replay(
            store,
            run_id=run_id,
            request_id=receipt.transaction_id,
            request_fingerprint=fingerprint,
        )
        assert replay is not None
        assert replay.status == "replayed"
        assert replay.receipt == receipt
        assert replay.primary_record_id == render.render_id
        with pytest.raises(CoreRunError) as error:
            resolve_core_replay(
                store,
                run_id=run_id,
                request_id=receipt.transaction_id,
                request_fingerprint="0" * 64,
            )
        assert error.value.code == "submission_replay_conflict"
        assert store.current_revision == revision


def _terminal_event(
    *,
    event_id: str,
    run_id: str,
    transaction_id: str,
    event_type: str,
    reason: str,
    fingerprint: str,
    effect_kind: str,
    primary_record_id: str,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    bind: bool = True,
) -> EventEnvelope:
    return _record(
        EventEnvelope,
        event_id=event_id,
        run_id=run_id,
        event_type=event_type,
        created_at=core_fixture.NOW,
        actor="system",
        transaction_id=transaction_id,
        stage_id=stage_id,
        artifact_id=artifact_id,
        decision="record",
        reason=reason,
        metadata={},
        intake_binding=None,
        core_run_binding=(
            CoreRunEventBinding.model_validate(
                {
                    "request_id": transaction_id,
                    "request_fingerprint": fingerprint,
                    "effect_kind": effect_kind,
                    "primary_record_id": primary_record_id,
                    "outcome": "committed",
                },
                strict=True,
            )
            if bind
            else None
        ),
    )


def _archive_usage(artifact_id: str) -> str:
    if artifact_id.startswith("run_contract_"):
        return "control"
    if artifact_id.endswith("quality_gate_report"):
        return "gate"
    if artifact_id == "reader_brief":
        return "reader"
    if artifact_id in {"claim_ledger", "audit_report"}:
        return "evidence"
    return "workflow"


def _commit_finalize_gate(
    workspace: Path,
    run_id: str,
    clock: object,
    render: FinalizeRenderRecord,
) -> tuple[TransactionReceipt, str, tuple[GateEvaluationRecord, ...]]:
    transaction_id = "REQ-TERMINAL-FINALIZE-GATE-001"
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        before = verified.snapshot
        artifacts = {item.artifact_id: item for item in before.artifacts}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in before.artifact_revisions
        }

        def current_revision(artifact_id: str) -> ArtifactRevision:
            record = artifacts[artifact_id]
            return revisions[(artifact_id, record.current_revision)]

        candidate = current_revision("candidate_claims")
        screened = current_revision("screened_candidates")
        reader_revisions = [
            revisions[(item.artifact_id, item.revision)]
            for item in render.reader_artifacts
        ]
        audit_report = revisions[
            (render.audit_report.artifact_id, render.audit_report.revision)
        ]
        ledger = current_revision("claim_ledger")
        assessed = [
            (candidate, "screened_candidates"),
            (screened, "screened_candidates"),
            *((item, "reader_artifact") for item in reader_revisions),
            (audit_report, "audit_report"),
            (ledger, "ledger"),
        ]
        input_hashes = [
            {
                "artifact_id": item.artifact_id,
                "revision": item.revision,
                "sha256": item.sha256,
                "usage": usage,
            }
            for item, usage in assessed
        ]
        request_fingerprint = canonical_fingerprint(
            {
                "effect_kind": "gate_evaluation",
                "stage_id": "finalize",
                "input_artifacts": input_hashes,
                "contract_fingerprint": verified.binding.contract_fingerprint,
                "evaluator": "core-v2-preloaded-quality-gates",
                "evaluator_version": "1",
            }
        )
        batch_id = derived_id("GATE-BATCH", transaction_id, request_fingerprint)
        event_id = derived_id("EVT-GATES", transaction_id, request_fingerprint)
        template_bindings = tuple(
            _record(
                GateArtifactBinding,
                run_id=run_id,
                evaluation_id="GATE-TERMINAL-TEMPLATE",
                position=position,
                artifact_id=revision.artifact_id,
                artifact_revision=revision.revision,
                artifact_sha256=revision.sha256,
                usage=usage,
                accepted_transaction_id=transaction_id,
            )
            for position, (revision, usage) in enumerate(assessed)
        )
        gate_outcomes = _replay_gate_outcomes(
            store,
            before,
            verified.binding,
            stage_id="finalize",
            stages=tuple(dict(item) for item in verified.stages),
            artifacts=tuple(dict(item) for item in verified.artifacts),
            artifact_bindings=template_bindings,
        )
        policy_version = (
            f"{verified.binding.policy_pack_name}:"
            f"{verified.binding.policy_pack_sha256[:16]}"
        )
        evaluations: list[GateEvaluationRecord] = []
        findings = []
        for gate_id in sorted(GATE_IDS):
            forced_status, raw_findings = gate_outcomes[gate_id]
            evaluation_id = derived_id("GATE", batch_id, gate_id)
            selected_findings = [
                _gate_finding_record(
                    run_id=run_id,
                    evaluation_id=evaluation_id,
                    gate_id=gate_id,
                    position=position,
                    raw=raw,
                    accepted_transaction_id=transaction_id,
                )
                for position, raw in enumerate(raw_findings, start=1)
            ]
            findings.extend(selected_findings)
            blocking = any(
                item.blocking_level == "blocking" for item in selected_findings
            )
            status = forced_status or (
                "fail" if blocking else ("warning" if selected_findings else "pass")
            )
            evaluations.append(
                _record(
                    GateEvaluationRecord,
                    evaluation_id=evaluation_id,
                    gate_batch_id=batch_id,
                    run_id=run_id,
                    stage_id="finalize",
                    gate_id=gate_id,
                    policy_version=policy_version,
                    run_contract_fingerprint=verified.binding.contract_fingerprint,
                    status=status,
                    blocking=blocking,
                    finding_ids=[item.finding_id for item in selected_findings],
                    checked_at=core_fixture.NOW,
                    producer_implementation="core-v2-preloaded-quality-gates",
                    producer_version="1",
                    report_artifact={
                        "artifact_id": "finalize_quality_gate_report",
                        "revision": 1,
                    },
                    evaluation_event_id=event_id,
                    accepted_transaction_id=transaction_id,
                    request_fingerprint=request_fingerprint,
                )
            )
        report_bytes = (
            canonical_json_bytes(
                {
                    "schema_version": "briefloop.gate_report.v2",
                    "run_id": run_id,
                    "stage_id": "finalize",
                    "gate_batch_id": batch_id,
                    "policy_version": policy_version,
                    "run_contract_fingerprint": verified.binding.contract_fingerprint,
                    "input_artifacts": input_hashes,
                    "evaluations": [
                        item.model_dump(mode="json", exclude_unset=False)
                        for item in evaluations
                    ],
                    "findings": [
                        item.model_dump(mode="json", exclude_unset=False)
                        for item in findings
                    ],
                }
            )
            + b"\n"
        )
        report_contract = next(
            item
            for item in verified.artifacts
            if item["artifact_id"] == "finalize_quality_gate_report"
        )
        report_record = _record(
            ArtifactRecord,
            run_id=run_id,
            artifact_id="finalize_quality_gate_report",
            current_revision=1,
            status="valid",
            required=bool(report_contract["required"]),
            path=str(report_contract["path"]),
            format=str(report_contract["format"]),
        )
        report_revision = _record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id=report_record.artifact_id,
            revision=1,
            path=report_record.path,
            sha256=sha256_hex(report_bytes),
            size_bytes=len(report_bytes),
            frozen=True,
            producer_kind="control_tool",
            producer_id="core-v2-preloaded-quality-gates",
            created_at=core_fixture.NOW,
        )
        event = _record(
            EventEnvelope,
            event_id=event_id,
            run_id=run_id,
            event_type="quality_gate_checked",
            created_at=core_fixture.NOW,
            actor="system",
            transaction_id=transaction_id,
            stage_id="finalize",
            artifact_id=report_record.artifact_id,
            decision=(
                "block" if any(item.blocking for item in evaluations) else "continue"
            ),
            reason="preloaded deterministic Finalize Gate batch evaluated",
            metadata={},
            intake_binding=None,
            core_run_binding=CoreRunEventBinding.model_validate(
                {
                    "request_id": transaction_id,
                    "request_fingerprint": request_fingerprint,
                    "effect_kind": "gate_evaluation",
                    "primary_record_id": batch_id,
                    "outcome": "committed",
                },
                strict=True,
            ),
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("gate_evaluation"),
            before.store_revision,
        )
        unit.put_artifact(report_record)
        unit.put_artifact_revision(report_revision, report_bytes)
        for evaluation in evaluations:
            unit.put_gate_evaluation(evaluation)
            for template in template_bindings:
                unit.put_gate_artifact_binding(
                    template.model_copy(
                        update={"evaluation_id": evaluation.evaluation_id}
                    )
                )
        for finding in findings:
            unit.put_gate_finding(finding)
        unit.append_event(event)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store,
                run_id,
            )
        )
        assert set(item.evaluation_id for item in receipt.gate_evaluations) == {
            item.evaluation_id for item in evaluations
        }
        assert {
            (item.evaluation_id, item.finding_id) for item in receipt.gate_findings
        } == {(item.evaluation_id, item.finding_id) for item in findings}
        assert {
            (item.evaluation_id, item.position)
            for item in receipt.gate_artifact_bindings
        } == {
            (evaluation.evaluation_id, position)
            for evaluation in evaluations
            for position in range(len(assessed))
        }
        assert receipt.event_ids == [event_id]
        assert [
            (item.artifact_id, item.revision) for item in receipt.artifact_revisions
        ] == [(report_revision.artifact_id, report_revision.revision)]
    return receipt, request_fingerprint, tuple(evaluations)


def _commit_finalize_complete(
    workspace: Path,
    run_id: str,
    clock: object,
    render: FinalizeRenderRecord,
) -> tuple[TransactionReceipt, str, PackageReadyRecord]:
    transaction_id = "REQ-TERMINAL-FINALIZE-COMPLETE-001"
    finalization_id = "FINALIZATION-TERMINAL-PERSISTED-001"
    transition_id = "TRANSITION-TERMINAL-FINALIZE-001"
    final_event_id = "EVT-TERMINAL-FINALIZED-001"
    archive_id = "ARCHIVE-TERMINAL-PERSISTED-001"
    archive_event_id = "EVT-TERMINAL-ARCHIVE-PERSISTED-001"
    package_id = "PACKAGE-TERMINAL-PERSISTED-001"
    package_event_id = "EVT-TERMINAL-PACKAGE-PERSISTED-001"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "finalize_complete",
            "render_id": render.render_id,
            "finalization_id": finalization_id,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        finalize_state = next(
            item for item in before.stage_states if item.stage_id == "finalize"
        )
        selected_gates = tuple(
            sorted(
                (
                    item
                    for item in before.gate_evaluations
                    if item.stage_id == "finalize"
                    and not item.blocking
                    and item.status in {"pass", "warning"}
                ),
                key=lambda item: item.gate_id,
            )
        )
        assert {item.gate_id for item in selected_gates} == set(GATE_IDS)
        assert len({item.gate_batch_id for item in selected_gates}) == 1
        current_revisions = {
            (item.artifact_id, item.current_revision): next(
                revision
                for revision in before.artifact_revisions
                if revision.artifact_id == item.artifact_id
                and revision.revision == item.current_revision
            )
            for item in before.artifacts
            if item.current_revision > 0
        }
        ordered_current = sorted(
            current_revisions.values(),
            key=lambda item: (item.artifact_id, item.revision),
        )
        archive_bytes = (
            canonical_json_bytes(
                {
                    "schema_version": "briefloop.core_v2_run_archive.v1",
                    "run_id": run_id,
                    "finalization_id": finalization_id,
                    "artifacts": [
                        {
                            "artifact_id": item.artifact_id,
                            "revision": item.revision,
                            "sha256": item.sha256,
                        }
                        for item in ordered_current
                    ],
                }
            )
            + b"\n"
        )
        archive_revision = _record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id="core_v2_run_archive",
            revision=1,
            path="output/intermediate/core_v2_run_archive.json",
            sha256=sha256_hex(archive_bytes),
            size_bytes=len(archive_bytes),
            frozen=True,
            producer_kind="control_tool",
            producer_id="core-v2-finalize-complete",
            created_at=core_fixture.NOW,
        )
        reader_revisions = [
            current_revisions[(item.artifact_id, item.revision)]
            for item in render.reader_artifacts
        ]
        package_bytes = (
            canonical_json_bytes(
                {
                    "schema_version": "briefloop.core_v2_package_manifest.v1",
                    "run_id": run_id,
                    "finalization_id": finalization_id,
                    "archive": {
                        "artifact_id": archive_revision.artifact_id,
                        "revision": archive_revision.revision,
                        "sha256": archive_revision.sha256,
                    },
                    "reader_artifacts": [
                        {
                            "artifact_id": item.artifact_id,
                            "revision": item.revision,
                            "sha256": item.sha256,
                        }
                        for item in reader_revisions
                    ],
                }
            )
            + b"\n"
        )
        package_revision = _record(
            ArtifactRevision,
            run_id=run_id,
            artifact_id="core_v2_package_manifest",
            revision=1,
            path="output/intermediate/core_v2_package_manifest.json",
            sha256=sha256_hex(package_bytes),
            size_bytes=len(package_bytes),
            frozen=True,
            producer_kind="control_tool",
            producer_id="core-v2-finalize-complete",
            created_at=core_fixture.NOW,
        )
        transition = _record(
            StageTransitionRecord,
            transition_id=transition_id,
            run_id=run_id,
            stage_id="finalize",
            transition_kind="complete",
            requested_decision="continue",
            prior_status=finalize_state.status,
            prior_revision=finalize_state.revision,
            result_status="complete",
            result_revision=finalize_state.revision + 1,
            reason="Finalize Gate passed and immutable package was created",
            run_contract_fingerprint=before.run_contract_bindings[
                0
            ].contract_fingerprint,
            actor="system",
            producer_invocation_id=None,
            producer_tool_id="core-v2-finalize-complete",
            created_at=core_fixture.NOW,
            transition_event_id=final_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        finalization = _record(
            FinalizationRecord,
            finalization_id=finalization_id,
            run_id=run_id,
            render_id=render.render_id,
            finalize_transition_id=transition_id,
            finalize_gate_batch_id=selected_gates[0].gate_batch_id,
            finalize_gate_evaluation_ids=sorted(
                item.evaluation_id for item in selected_gates
            ),
            recovery_id=None,
            integrity_revision=before.run_integrity_records[-1].integrity_revision,
            finalized_at=core_fixture.NOW,
            finalization_event_id=final_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        archive = _record(
            RunArchiveRecord,
            archive_id=archive_id,
            run_id=run_id,
            finalization_id=finalization_id,
            archive_artifact={
                "artifact_id": archive_revision.artifact_id,
                "revision": archive_revision.revision,
            },
            manifest_sha256=archive_revision.sha256,
            included_count=len(ordered_current),
            created_at=core_fixture.NOW,
            archive_event_id=archive_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        package_members = [*reader_revisions, archive_revision, package_revision]
        package = _record(
            PackageReadyRecord,
            package_id=package_id,
            run_id=run_id,
            finalization_id=finalization_id,
            archive_id=archive_id,
            package_manifest_artifact={
                "artifact_id": package_revision.artifact_id,
                "revision": package_revision.revision,
            },
            package_manifest_sha256=package_revision.sha256,
            artifact_count=len(package_members),
            created_at=core_fixture.NOW,
            package_event_id=package_event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("finalize_complete"),
            before.store_revision,
        )
        unit.put_stage_state(
            _record(
                StageState,
                run_id=run_id,
                stage_id="finalize",
                status="complete",
                revision=finalize_state.revision + 1,
                updated_at=core_fixture.NOW,
            )
        )
        unit.append_stage_transition(transition)
        first_gate_id = selected_gates[0].evaluation_id
        consumed_revisions = {
            (item.artifact_id, item.artifact_revision): current_revisions[
                (item.artifact_id, item.artifact_revision)
            ]
            for item in before.gate_artifact_bindings
            if item.evaluation_id == first_gate_id
        }
        consumed_revisions.update(
            {(item.artifact_id, item.revision): item for item in reader_revisions}
        )
        transition_inputs = sorted(
            [
                *((item, "consumed") for item in consumed_revisions.values()),
                (archive_revision, "produced"),
                (package_revision, "produced"),
            ],
            key=lambda item: (item[0].artifact_id, item[0].revision),
        )
        for position, (revision, usage) in enumerate(transition_inputs):
            unit.put_stage_artifact_binding(
                _record(
                    StageArtifactBinding,
                    run_id=run_id,
                    transition_id=transition_id,
                    position=position,
                    artifact_id=revision.artifact_id,
                    artifact_revision=revision.revision,
                    artifact_sha256=revision.sha256,
                    usage=usage,
                    accepted_transaction_id=transaction_id,
                )
            )
        for evaluation in selected_gates:
            unit.put_stage_gate_binding(
                _record(
                    StageGateBinding,
                    run_id=run_id,
                    transition_id=transition_id,
                    gate_id=evaluation.gate_id,
                    evaluation_id=evaluation.evaluation_id,
                    accepted_transaction_id=transaction_id,
                )
            )
        for record, revision, content in (
            (
                _record(
                    ArtifactRecord,
                    run_id=run_id,
                    artifact_id=archive_revision.artifact_id,
                    current_revision=1,
                    status="valid",
                    required=True,
                    path=archive_revision.path,
                    format="json",
                ),
                archive_revision,
                archive_bytes,
            ),
            (
                _record(
                    ArtifactRecord,
                    run_id=run_id,
                    artifact_id=package_revision.artifact_id,
                    current_revision=1,
                    status="valid",
                    required=True,
                    path=package_revision.path,
                    format="json",
                ),
                package_revision,
                package_bytes,
            ),
        ):
            unit.put_artifact(record)
            unit.put_artifact_revision(revision, content)
        unit.put_finalization(finalization)
        unit.put_run_archive(archive)
        for position, revision in enumerate(ordered_current):
            unit.put_run_archive_artifact_binding(
                _record(
                    RunArchiveArtifactBinding,
                    run_id=run_id,
                    archive_id=archive_id,
                    position=position,
                    artifact_id=revision.artifact_id,
                    artifact_revision=revision.revision,
                    artifact_sha256=revision.sha256,
                    usage=_archive_usage(revision.artifact_id),
                    accepted_transaction_id=transaction_id,
                )
            )
        unit.put_package_ready(package)
        for position, revision in enumerate(package_members):
            unit.put_package_artifact_binding(
                _record(
                    PackageArtifactBinding,
                    run_id=run_id,
                    package_id=package_id,
                    position=position,
                    artifact_id=revision.artifact_id,
                    artifact_revision=revision.revision,
                    artifact_sha256=revision.sha256,
                    usage=(
                        "archive"
                        if revision.artifact_id == archive_revision.artifact_id
                        else "manifest"
                        if revision.artifact_id == package_revision.artifact_id
                        else "reader"
                    ),
                    accepted_transaction_id=transaction_id,
                )
            )
        for event in (
            _terminal_event(
                event_id=final_event_id,
                run_id=run_id,
                transaction_id=transaction_id,
                event_type="stage_status_changed",
                stage_id="finalize",
                reason="finalized",
                fingerprint=request_fingerprint,
                effect_kind="finalize_complete",
                primary_record_id=finalization_id,
            ),
            _terminal_event(
                event_id=archive_event_id,
                run_id=run_id,
                transaction_id=transaction_id,
                event_type="run_archived",
                artifact_id=archive_revision.artifact_id,
                reason="immutable run archive created",
                fingerprint=request_fingerprint,
                effect_kind="finalize_complete",
                primary_record_id=archive_id,
                bind=False,
            ),
            _terminal_event(
                event_id=package_event_id,
                run_id=run_id,
                transaction_id=transaction_id,
                event_type="decision_recorded",
                artifact_id=package_revision.artifact_id,
                reason="package ready",
                fingerprint=request_fingerprint,
                effect_kind="finalize_complete",
                primary_record_id=package_id,
                bind=False,
            ),
        ):
            unit.append_event(event)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store,
                run_id,
            )
        )
        assert [item.finalization_id for item in receipt.finalizations] == [
            finalization_id
        ]
        assert [item.transition_id for item in receipt.stage_transitions] == [
            transition_id
        ]
        assert [
            (item.transition_id, item.position)
            for item in receipt.stage_artifact_bindings
        ] == [(transition_id, position) for position in range(len(transition_inputs))]
        assert {
            (item.transition_id, item.gate_id) for item in receipt.stage_gate_bindings
        } == {(transition_id, item.gate_id) for item in selected_gates}
        assert [item.archive_id for item in receipt.run_archives] == [archive_id]
        assert [
            (item.archive_id, item.position)
            for item in receipt.run_archive_artifact_bindings
        ] == [(archive_id, position) for position in range(len(ordered_current))]
        assert [item.package_id for item in receipt.package_ready_records] == [
            package_id
        ]
        assert [
            (item.package_id, item.position)
            for item in receipt.package_artifact_bindings
        ] == [(package_id, position) for position in range(len(package_members))]
        assert receipt.event_ids == [
            final_event_id,
            archive_event_id,
            package_event_id,
        ]
    return receipt, request_fingerprint, package


def test_finalize_complete_persists_exact_membership_and_replays_after_restart(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    gate_receipt, gate_fingerprint, finalize_gates = _commit_finalize_gate(
        workspace,
        run_id,
        clock,
        render,
    )
    assert {item.gate_id for item in finalize_gates} == set(GATE_IDS)
    assert all(
        item.status in {"pass", "warning"} and not item.blocking
        for item in finalize_gates
    )
    receipt, fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        assert verified.snapshot.package_ready_records == (package,)
        revision = store.current_revision
        gate_replay = resolve_core_replay(
            store,
            run_id=run_id,
            request_id=gate_receipt.transaction_id,
            request_fingerprint=gate_fingerprint,
        )
        assert gate_replay is not None
        assert gate_replay.status == "replayed"
        assert gate_replay.receipt == gate_receipt
        replay = resolve_core_replay(
            store,
            run_id=run_id,
            request_id=receipt.transaction_id,
            request_fingerprint=fingerprint,
        )
        assert replay is not None
        assert replay.status == "replayed"
        assert replay.receipt == receipt
        with pytest.raises(CoreRunError) as error:
            resolve_core_replay(
                store,
                run_id=run_id,
                request_id=receipt.transaction_id,
                request_fingerprint="0" * 64,
            )
        assert error.value.code == "submission_replay_conflict"
        assert store.current_revision == revision


@pytest.mark.parametrize("blocking", (False, True))
def test_finalize_gate_event_decision_must_equal_batch_blocking(
    tmp_path: Path,
    blocking: bool,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    receipt, _fingerprint, _evaluations = _commit_finalize_gate(
        workspace,
        run_id,
        clock,
        render,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
    evaluations = snapshot.gate_evaluations
    if blocking:
        first = next(
            item
            for item in evaluations
            if item.accepted_transaction_id == receipt.transaction_id
        )
        evaluations = tuple(
            item.model_copy(update={"status": "fail", "blocking": True})
            if item.evaluation_id == first.evaluation_id
            else item
            for item in evaluations
        )
        forged_decision = "continue"
    else:
        forged_decision = "block"
    events = tuple(
        item.model_copy(update={"decision": forged_decision})
        if item.event_id in receipt.event_ids
        else item
        for item in snapshot.events
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(
            replace(snapshot, gate_evaluations=evaluations, events=events),
            receipt,
        )


class _InjectedTerminalFailure(RuntimeError):
    pass


def _commit_internal_approval(
    workspace: Path,
    run_id: str,
    clock: object,
    package: PackageReadyRecord,
    *,
    fail_before_commit: bool = False,
    sequence: int = 1,
    decision: str = "approve",
) -> tuple[TransactionReceipt, str, Approval]:
    transaction_id = f"REQ-TERMINAL-APPROVAL-{sequence:03d}"
    approval_id = f"APPROVAL-TERMINAL-PERSISTED-{sequence:03d}"
    event_id = f"EVT-TERMINAL-APPROVAL-PERSISTED-{sequence:03d}"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "internal_approval",
            "approval_id": approval_id,
            "package_id": package.package_id,
            "decision": decision,
        }
    )

    def failure_hook(stage: str) -> None:
        if fail_before_commit and stage == "before_commit":
            raise _InjectedTerminalFailure("injected terminal failure")

    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
        _failure_hook=failure_hook,
    ) as store:
        before = store.load_snapshot(run_id)
        approval = _record(
            Approval,
            approval_id=approval_id,
            run_id=run_id,
            mode="internal_management_review",
            role="content_owner",
            decision=decision,
            reason="Synthetic internal content-owner approval",
            actor_id="HUMAN-TERMINAL-001",
            recorded_at=core_fixture.NOW,
            boundary=(
                "internal_review_approval_records_only_not_public_release_authorization"
            ),
            event_id=event_id,
        )
        binding = _record(
            ApprovalPackageBinding,
            run_id=run_id,
            approval_id=approval_id,
            package_id=package.package_id,
            accepted_transaction_id=transaction_id,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type="human_approval_recorded",
            reason="internal package approval recorded",
            fingerprint=request_fingerprint,
            effect_kind="internal_approval",
            primary_record_id=approval_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("internal_approval"),
            before.store_revision,
        )
        unit.put_approval(approval)
        unit.put_approval_package_binding(binding)
        unit.append_event(event)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store,
                run_id,
            )
        )
        assert [item.approval_id for item in receipt.approvals] == [approval_id]
        assert [
            (item.approval_id, item.package_id)
            for item in receipt.approval_package_bindings
        ] == [(approval_id, package.package_id)]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, approval


def _commit_delivery_authorization(
    workspace: Path,
    run_id: str,
    clock: object,
    package: PackageReadyRecord,
    *,
    sequence: int = 1,
    approval_mode: str = "internal_management_review",
    decision: str = "authorize",
    prior_authorization_id: str | None = None,
    target: str = "local",
    channel: str = "filesystem",
    recipient_fingerprint: str = "d" * 64,
) -> tuple[TransactionReceipt, str, DeliveryAuthorizationRecord]:
    transaction_id = f"REQ-TERMINAL-AUTHORIZATION-{sequence:03d}"
    authorization_id = f"AUTH-TERMINAL-PERSISTED-{sequence:03d}"
    event_id = f"EVT-TERMINAL-AUTHORIZATION-PERSISTED-{sequence:03d}"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "delivery_authorization",
            "authorization_id": authorization_id,
            "package_id": package.package_id,
            "prior_authorization_id": prior_authorization_id,
            "approval_mode": approval_mode,
            "decision": decision,
            "target": target,
            "channel": channel,
            "recipient_fingerprint": recipient_fingerprint,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        authorization = _record(
            DeliveryAuthorizationRecord,
            authorization_id=authorization_id,
            run_id=run_id,
            package_id=package.package_id,
            prior_authorization_id=prior_authorization_id,
            approval_mode=approval_mode,
            retry_of_attempt_id=None,
            purpose="initial_attempt",
            decision=decision,
            target=target,
            channel=channel,
            recipient_fingerprint=recipient_fingerprint,
            actor_id="HUMAN-TERMINAL-001",
            reason="Authorize deterministic local package preparation",
            recorded_at=core_fixture.NOW,
            authorization_event_id=event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type="decision_recorded",
            reason="delivery authorization recorded",
            fingerprint=request_fingerprint,
            effect_kind="delivery_authorization",
            primary_record_id=authorization_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("delivery_authorization"),
            before.store_revision,
        )
        unit.put_delivery_authorization(authorization)
        unit.append_event(event)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store,
                run_id,
            )
        )
        assert [item.authorization_id for item in receipt.delivery_authorizations] == [
            authorization_id
        ]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, authorization


def _commit_delivery_attempt(
    workspace: Path,
    run_id: str,
    clock: object,
    authorization: DeliveryAuthorizationRecord,
) -> tuple[TransactionReceipt, str, DeliveryAttemptRecord]:
    transaction_id = "REQ-TERMINAL-ATTEMPT-001"
    attempt_id = "ATTEMPT-TERMINAL-PERSISTED-001"
    event_id = "EVT-TERMINAL-ATTEMPT-PERSISTED-001"
    operation_id = "LOCAL-PACKAGE-TERMINAL-001"
    connector_fingerprint = "e" * 64
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "delivery_attempt",
            "attempt_id": attempt_id,
            "authorization_id": authorization.authorization_id,
            "connector_operation_id": operation_id,
            "connector_request_fingerprint": connector_fingerprint,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        attempt = _record(
            DeliveryAttemptRecord,
            attempt_id=attempt_id,
            run_id=run_id,
            package_id=authorization.package_id,
            authorization_id=authorization.authorization_id,
            target=authorization.target,
            channel=authorization.channel,
            recipient_fingerprint=authorization.recipient_fingerprint,
            connector_operation_id=operation_id,
            connector_request_fingerprint=connector_fingerprint,
            created_at=core_fixture.NOW,
            attempt_event_id=event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type="delivery_attempted",
            reason="delivery attempt prepared before connector call",
            fingerprint=request_fingerprint,
            effect_kind="delivery_attempt",
            primary_record_id=attempt_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("delivery_attempt"),
            before.store_revision,
        )
        unit.put_delivery_attempt(attempt)
        unit.append_event(event)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store,
                run_id,
            )
        )
        assert [item.attempt_id for item in receipt.delivery_attempts] == [attempt_id]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, attempt


def _commit_delivery_result(
    workspace: Path,
    run_id: str,
    clock: object,
    package: PackageReadyRecord,
    attempt: DeliveryAttemptRecord,
) -> tuple[TransactionReceipt, str, DeliveryResultRecord]:
    transaction_id = "REQ-TERMINAL-RESULT-001"
    result_id = "DELIVERY-RESULT-TERMINAL-PERSISTED-001"
    event_id = "EVT-TERMINAL-RESULT-PERSISTED-001"
    request_fingerprint = canonical_fingerprint(
        {
            "effect_kind": "delivery_result",
            "result_id": result_id,
            "attempt_id": attempt.attempt_id,
            "status": "bundle_prepared",
            "evidence_sha256": package.package_manifest_sha256,
        }
    )
    with SQLiteControlStore.open(
        workspace / "briefloop.db",
        clock=clock,
    ) as store:
        before = store.load_snapshot(run_id)
        result = _record(
            DeliveryResultRecord,
            result_id=result_id,
            run_id=run_id,
            attempt_id=attempt.attempt_id,
            prior_result_id=None,
            reconciliation_authorization_id=None,
            status="bundle_prepared",
            adapter_id="briefloop-local-package",
            adapter_version="V2",
            connector_operation_id=attempt.connector_operation_id,
            evidence_sha256=package.package_manifest_sha256,
            evidence_artifact=package.package_manifest_artifact,
            recorded_at=core_fixture.NOW,
            result_event_id=event_id,
            accepted_transaction_id=transaction_id,
            request_fingerprint=request_fingerprint,
        )
        event = _terminal_event(
            event_id=event_id,
            run_id=run_id,
            transaction_id=transaction_id,
            event_type="delivery_bundle_prepared",
            artifact_id=package.package_manifest_artifact.artifact_id,
            reason="typed local package observation recorded",
            fingerprint=request_fingerprint,
            effect_kind="delivery_result",
            primary_record_id=result_id,
        )
        unit = store.begin(
            run_id,
            transaction_id,
            transaction_type_for("delivery_result"),
            before.store_revision,
        )
        unit.put_delivery_result(result)
        unit.append_event(event)
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: CoreRunDomainVerifier().verify(
                store,
                run_id,
            )
        )
        assert [item.result_id for item in receipt.delivery_results] == [result_id]
        assert receipt.event_ids == [event_id]
    return receipt, request_fingerprint, result


def test_terminal_effect_chain_rolls_back_replays_and_survives_restart(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        before_failure = store.current_revision
    with pytest.raises(_InjectedTerminalFailure):
        _commit_internal_approval(
            workspace,
            run_id,
            clock,
            package,
            fail_before_commit=True,
        )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = store.load_snapshot(run_id)
        assert store.current_revision == before_failure
        assert all(
            item.transaction_id != "REQ-TERMINAL-APPROVAL-001"
            for item in snapshot.transactions
        )
        assert not snapshot.approvals
        assert not snapshot.approval_package_bindings

    approval_receipt, approval_fingerprint, approval = _commit_internal_approval(
        workspace,
        run_id,
        clock,
        package,
    )
    authorization_receipt, authorization_fingerprint, authorization = (
        _commit_delivery_authorization(workspace, run_id, clock, package)
    )
    attempt_receipt, attempt_fingerprint, attempt = _commit_delivery_attempt(
        workspace,
        run_id,
        clock,
        authorization,
    )
    result_receipt, result_fingerprint, result = _commit_delivery_result(
        workspace,
        run_id,
        clock,
        package,
        attempt,
    )

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        verified = CoreRunDomainVerifier().verify(store, run_id)
        snapshot = verified.snapshot
        assert snapshot.approvals == (approval,)
        assert snapshot.delivery_authorizations == (authorization,)
        assert snapshot.delivery_attempts == (attempt,)
        assert snapshot.delivery_results == (result,)
        assert classify_terminal_state(snapshot).state == "package_ready"
        revision = store.current_revision
        for receipt, fingerprint, primary_id in (
            (approval_receipt, approval_fingerprint, approval.approval_id),
            (
                authorization_receipt,
                authorization_fingerprint,
                authorization.authorization_id,
            ),
            (attempt_receipt, attempt_fingerprint, attempt.attempt_id),
            (result_receipt, result_fingerprint, result.result_id),
        ):
            replay = resolve_core_replay(
                store,
                run_id=run_id,
                request_id=receipt.transaction_id,
                request_fingerprint=fingerprint,
            )
            assert replay is not None
            assert replay.status == "replayed"
            assert replay.receipt == receipt
            assert replay.primary_record_id == primary_id
        with pytest.raises(CoreRunError) as error:
            resolve_core_replay(
                store,
                run_id=run_id,
                request_id=authorization_receipt.transaction_id,
                request_fingerprint="0" * 64,
            )
        assert error.value.code == "submission_replay_conflict"
        assert store.current_revision == revision


def test_terminal_authorization_is_recordable_before_approval_but_not_consumable(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path / "required")
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    _authorization_receipt, _authorization_fingerprint, authorization = (
        _commit_delivery_authorization(workspace, run_id, clock, package)
    )
    attempt_subject = TerminalEffectSubject(
        package_id=package.package_id,
        authorization_id=authorization.authorization_id,
        target=authorization.target,
        channel=authorization.channel,
        recipient_fingerprint=authorization.recipient_fingerprint,
        attempt_id="ATTEMPT-PREFLIGHT-001",
        connector_operation_id="CONNECTOR-PREFLIGHT-001",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        pre_approval = CoreRunDomainVerifier().verify(store, run_id).snapshot
        assert classify_terminal_legality(pre_approval).terminal_state == (
            "approval_incomplete"
        )
        assert (
            classify_terminal_effect_authorization(
                pre_approval,
                CoreEffect.DELIVERY_ATTEMPT,
                attempt_subject,
            ).decision
            == "deny"
        )
        revision = store.current_revision
        assert store.current_revision == revision

    _commit_internal_approval(workspace, run_id, clock, package)
    _commit_delivery_attempt(workspace, run_id, clock, authorization)

    draft_workspace, draft_run_id, draft_clock = _finalize_ready_workspace(
        tmp_path / "draft"
    )
    _draft_render_receipt, _draft_render_fingerprint, draft_render = (
        _commit_finalize_render(draft_workspace, draft_run_id, draft_clock)
    )
    _commit_finalize_gate(draft_workspace, draft_run_id, draft_clock, draft_render)
    _draft_complete_receipt, _draft_complete_fingerprint, draft_package = (
        _commit_finalize_complete(
            draft_workspace,
            draft_run_id,
            draft_clock,
            draft_render,
        )
    )
    _draft_auth_receipt, _draft_auth_fingerprint, draft_authorization = (
        _commit_delivery_authorization(
            draft_workspace,
            draft_run_id,
            draft_clock,
            draft_package,
            approval_mode="internal_draft",
        )
    )
    draft_subject = TerminalEffectSubject(
        package_id=draft_package.package_id,
        authorization_id=draft_authorization.authorization_id,
        target=draft_authorization.target,
        channel=draft_authorization.channel,
        recipient_fingerprint=draft_authorization.recipient_fingerprint,
        attempt_id="ATTEMPT-DRAFT-PREFLIGHT-001",
        connector_operation_id="CONNECTOR-DRAFT-PREFLIGHT-001",
    )
    with SQLiteControlStore.open(
        draft_workspace / "briefloop.db",
        clock=draft_clock,
    ) as store:
        draft_snapshot = (
            CoreRunDomainVerifier()
            .verify(
                store,
                draft_run_id,
            )
            .snapshot
        )
        draft_legality = classify_terminal_legality(draft_snapshot)
        assert draft_legality.required_roles == ()
        assert draft_legality.approval_complete is True
        assert (
            classify_terminal_effect_authorization(
                draft_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                draft_subject,
            ).decision
            == "allow"
        )
        assert (
            classify_terminal_effect_authorization(
                draft_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                replace(
                    draft_subject,
                    recipient_fingerprint="0" * 64,
                ),
            ).decision
            == "deny"
        )
    _draft_attempt_receipt, _draft_attempt_fingerprint, draft_attempt = (
        _commit_delivery_attempt(
            draft_workspace,
            draft_run_id,
            draft_clock,
            draft_authorization,
        )
    )
    collision_attempt = draft_attempt.model_copy(
        update={
            "attempt_id": "ATTEMPT-UNRELATED-COLLISION-001",
            "authorization_id": "AUTH-UNRELATED-COLLISION-001",
        }
    )
    assert (
        classify_terminal_effect_authorization(
            replace(draft_snapshot, delivery_attempts=(collision_attempt,)),
            CoreEffect.DELIVERY_ATTEMPT,
            replace(
                draft_subject,
                connector_operation_id=collision_attempt.connector_operation_id,
            ),
        ).decision
        == "deny"
    )
    _deny_receipt, _deny_fingerprint, denied = _commit_delivery_authorization(
        draft_workspace,
        draft_run_id,
        draft_clock,
        draft_package,
        sequence=2,
        approval_mode="internal_draft",
        decision="deny",
        prior_authorization_id=draft_authorization.authorization_id,
    )
    with SQLiteControlStore.open(
        draft_workspace / "briefloop.db",
        clock=draft_clock,
    ) as store:
        denied_snapshot = (
            CoreRunDomainVerifier()
            .verify(
                store,
                draft_run_id,
            )
            .snapshot
        )
        denied_subject = replace(
            draft_subject,
            authorization_id=denied.authorization_id,
            attempt_id="ATTEMPT-DENIED-PREFLIGHT-001",
        )
        assert (
            classify_terminal_effect_authorization(
                denied_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                denied_subject,
            ).decision
            == "deny"
        )
        used_subject = replace(
            draft_subject,
            attempt_id="ATTEMPT-REUSE-PREFLIGHT-001",
            connector_operation_id=draft_attempt.connector_operation_id,
        )
        assert (
            classify_terminal_effect_authorization(
                denied_snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                used_subject,
            ).decision
            == "deny"
        )


def test_result_observation_uses_attempt_receipt_not_later_auth_or_approval(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    _commit_internal_approval(workspace, run_id, clock, package)
    _auth_receipt, _auth_fingerprint, authorization = _commit_delivery_authorization(
        workspace, run_id, clock, package
    )
    _attempt_receipt, _attempt_fingerprint, attempt = _commit_delivery_attempt(
        workspace,
        run_id,
        clock,
        authorization,
    )
    _commit_delivery_authorization(
        workspace,
        run_id,
        clock,
        package,
        sequence=2,
        decision="deny",
        prior_authorization_id=authorization.authorization_id,
    )
    _commit_internal_approval(
        workspace,
        run_id,
        clock,
        package,
        sequence=2,
        decision="reject",
    )
    result_receipt, _result_fingerprint, result = _commit_delivery_result(
        workspace,
        run_id,
        clock,
        package,
        attempt,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = CoreRunDomainVerifier().verify(store, run_id).snapshot
        assert result in snapshot.delivery_results
        assert result_receipt.committed_revision == snapshot.store_revision


def test_result_reconciliation_consumes_current_exact_authorization_once(
    tmp_path: Path,
) -> None:
    workspace, run_id, clock = _finalize_ready_workspace(tmp_path)
    _render_receipt, _render_fingerprint, render = _commit_finalize_render(
        workspace,
        run_id,
        clock,
    )
    _commit_finalize_gate(workspace, run_id, clock, render)
    _complete_receipt, _complete_fingerprint, package = _commit_finalize_complete(
        workspace,
        run_id,
        clock,
        render,
    )
    _auth_receipt, _auth_fingerprint, authorization = _commit_delivery_authorization(
        workspace,
        run_id,
        clock,
        package,
        approval_mode="internal_draft",
        target="gmail",
        channel="email",
        recipient_fingerprint="a" * 64,
    )
    _attempt_receipt, _attempt_fingerprint, attempt = _commit_delivery_attempt(
        workspace,
        run_id,
        clock,
        authorization,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=clock) as store:
        snapshot = CoreRunDomainVerifier().verify(store, run_id).snapshot

    unknown = _record(
        DeliveryResultRecord,
        result_id="RESULT-RECONCILIATION-UNKNOWN-001",
        run_id=run_id,
        attempt_id=attempt.attempt_id,
        prior_result_id=None,
        reconciliation_authorization_id=None,
        status="outcome_unknown",
        adapter_id="gmail-adapter",
        adapter_version="V2",
        connector_operation_id=attempt.connector_operation_id,
        evidence_sha256="b" * 64,
        evidence_artifact=None,
        recorded_at=core_fixture.NOW,
        result_event_id="EVT-RECONCILIATION-UNKNOWN-001",
        accepted_transaction_id="REQ-RECONCILIATION-UNKNOWN-001",
        request_fingerprint="c" * 64,
    )
    reconciliation = authorization.model_copy(
        update={
            "authorization_id": "AUTH-RECONCILIATION-001",
            "prior_authorization_id": authorization.authorization_id,
            "retry_of_attempt_id": attempt.attempt_id,
            "purpose": "result_reconciliation",
            "authorization_event_id": "EVT-AUTH-RECONCILIATION-001",
            "accepted_transaction_id": "REQ-AUTH-RECONCILIATION-001",
            "request_fingerprint": "d" * 64,
        }
    )
    reconciliation_snapshot = replace(
        snapshot,
        delivery_authorizations=(authorization, reconciliation),
        delivery_results=(unknown,),
    )
    retry_subject = TerminalEffectSubject(
        package_id=package.package_id,
        approval_mode="internal_draft",
        authorization_id="AUTH-RETRY-LEGAL-001",
        prior_authorization_id=authorization.authorization_id,
        retry_of_attempt_id=attempt.attempt_id,
        purpose="retry_attempt",
        decision="authorize",
        target=authorization.target,
        channel=authorization.channel,
        recipient_fingerprint=authorization.recipient_fingerprint,
    )
    assert (
        classify_terminal_effect_authorization(
            replace(snapshot, delivery_results=(unknown,)),
            CoreEffect.DELIVERY_AUTHORIZE,
            retry_subject,
        ).decision
        == "allow"
    )
    assert (
        classify_terminal_effect_authorization(
            replace(snapshot, delivery_results=(unknown,)),
            CoreEffect.DELIVERY_AUTHORIZE,
            replace(retry_subject, retry_of_attempt_id="ATTEMPT-WRONG-001"),
        ).decision
        == "deny"
    )
    subject = TerminalEffectSubject(
        package_id=package.package_id,
        attempt_id=attempt.attempt_id,
        connector_operation_id=attempt.connector_operation_id,
        prior_result_id=unknown.result_id,
        reconciliation_authorization_id=reconciliation.authorization_id,
        result_status="succeeded",
    )
    assert (
        classify_terminal_effect_authorization(
            reconciliation_snapshot,
            CoreEffect.DELIVERY_RESULT,
            subject,
        ).decision
        == "allow"
    )
    assert (
        classify_terminal_effect_authorization(
            reconciliation_snapshot,
            CoreEffect.DELIVERY_RESULT,
            replace(subject, attempt_id="ATTEMPT-WRONG-001"),
        ).decision
        == "deny"
    )

    consumed = unknown.model_copy(
        update={
            "result_id": "RESULT-RECONCILIATION-CONSUMED-001",
            "prior_result_id": unknown.result_id,
            "reconciliation_authorization_id": reconciliation.authorization_id,
            "status": "succeeded",
        }
    )
    assert (
        classify_terminal_effect_authorization(
            replace(reconciliation_snapshot, delivery_results=(unknown, consumed)),
            CoreEffect.DELIVERY_RESULT,
            subject,
        ).decision
        == "deny"
    )
    required_approval = reconciliation.model_copy(
        update={"approval_mode": "internal_management_review"}
    )
    assert (
        classify_terminal_effect_authorization(
            replace(
                reconciliation_snapshot,
                delivery_authorizations=(authorization, required_approval),
            ),
            CoreEffect.DELIVERY_RESULT,
            subject,
        ).decision
        == "deny"
    )


def _initialized_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-TERMINAL-PREFIX-INIT-001",
        workspace_id="WS-TERMINAL-PREFIX-001",
        run_id=RUN_ID,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    result = CoreRunService(
        workspace,
        clock=lambda: datetime(2026, 7, 17, tzinfo=timezone.utc),
    ).initialize(CoreRunInitializeRequest.model_validate(request, strict=True))
    assert result.status == "committed"
    return workspace


def _record(model: type, **values: object):
    return model.model_validate(
        {"schema_version": model.schema_id, **values},
        strict=True,
    )


def test_terminal_projection_is_pure_over_one_historical_prefix(
    tmp_path: Path,
) -> None:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        prefix = history.snapshot_at_revision(RUN_ID, 1)

    legality = classify_terminal_legality(prefix)
    assert legality.terminal_state == "core_active"
    assert legality.next_effects == ()
    assert classify_terminal_state(prefix).state == "core_active"


def _terminal_reconstruction_fixture(
    tmp_path: Path,
) -> tuple[ControlStoreHistory, object, TransactionReceipt]:
    workspace = _initialized_workspace(tmp_path)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        original_history = store.load_history()
        original = original_history.snapshot_at_revision(RUN_ID, 1)

    timestamp = "2026-07-17T00:00:00Z"
    initialization = original.transactions[0]
    reader_revision = original.artifact_revisions[0]
    reader_reference = {
        "artifact_id": reader_revision.artifact_id,
        "revision": reader_revision.revision,
    }
    render = _record(
        FinalizeRenderRecord,
        render_id="RENDER-TERMINAL-001",
        run_id=RUN_ID,
        audit_proposal_id="PROP-TERMINAL-AUDIT-001",
        audited_brief=reader_reference,
        audit_report=reader_reference,
        reader_artifacts=[reader_reference],
        reader_clean_status="pass",
        policy_result_fingerprint="a" * 64,
        run_contract_fingerprint="b" * 64,
        created_at=timestamp,
        render_event_id="EVT-TERMINAL-RENDER-001",
        accepted_transaction_id=initialization.transaction_id,
        request_fingerprint="c" * 64,
    )
    initialization_with_render = TransactionReceipt.model_validate(
        {
            **initialization.model_dump(mode="json", exclude_unset=False),
            "finalize_renders": [{"render_id": render.render_id}],
        },
        strict=True,
    )
    historical_full = replace(
        original_history.snapshots[0],
        finalize_renders=(render,),
        transactions=(initialization_with_render,),
    )
    history = replace(original_history, snapshots=(historical_full,))
    pre = history.snapshot_at_revision(RUN_ID, 1)

    transaction_id = "REQ-TERMINAL-COMPLETE-001"
    finalization = _record(
        FinalizationRecord,
        finalization_id="FINALIZATION-TERMINAL-001",
        run_id=RUN_ID,
        render_id=render.render_id,
        finalize_transition_id="TRN-TERMINAL-FINALIZE-001",
        finalize_gate_batch_id="BATCH-TERMINAL-FINALIZE-001",
        finalize_gate_evaluation_ids=["EVAL-TERMINAL-FINALIZE-001"],
        recovery_id=None,
        integrity_revision=1,
        finalized_at=timestamp,
        finalization_event_id="EVT-TERMINAL-FINALIZE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="d" * 64,
    )
    archive_members = [
        next(
            revision
            for revision in pre.artifact_revisions
            if revision.artifact_id == artifact.artifact_id
            and revision.revision == artifact.current_revision
        )
        for artifact in sorted(pre.artifacts, key=lambda item: item.artifact_id)
        if artifact.current_revision > 0
    ]
    archive_payload = {
        "schema_version": "briefloop.core_v2_run_archive.v1",
        "run_id": RUN_ID,
        "finalization_id": finalization.finalization_id,
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "revision": item.revision,
                "sha256": item.sha256,
            }
            for item in archive_members
        ],
    }
    archive_bytes = canonical_json_bytes(archive_payload) + b"\n"
    archive_revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id="core_v2_run_archive",
        revision=1,
        path="output/intermediate/core_v2_run_archive.json",
        sha256=sha256_hex(archive_bytes),
        size_bytes=len(archive_bytes),
        frozen=True,
        producer_kind="control_tool",
        producer_id="core-v2-finalize-complete",
        created_at=timestamp,
    )
    archive = _record(
        RunArchiveRecord,
        archive_id="ARCHIVE-TERMINAL-001",
        run_id=RUN_ID,
        finalization_id=finalization.finalization_id,
        archive_artifact={
            "artifact_id": archive_revision.artifact_id,
            "revision": archive_revision.revision,
        },
        manifest_sha256=archive_revision.sha256,
        included_count=len(archive_members),
        created_at=timestamp,
        archive_event_id="EVT-TERMINAL-ARCHIVE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="e" * 64,
    )
    package_payload = {
        "schema_version": "briefloop.core_v2_package_manifest.v1",
        "run_id": RUN_ID,
        "finalization_id": finalization.finalization_id,
        "archive": {
            "artifact_id": archive_revision.artifact_id,
            "revision": archive_revision.revision,
            "sha256": archive_revision.sha256,
        },
        "reader_artifacts": [
            {
                "artifact_id": reader_revision.artifact_id,
                "revision": reader_revision.revision,
                "sha256": reader_revision.sha256,
            }
        ],
    }
    package_bytes = canonical_json_bytes(package_payload) + b"\n"
    package_revision = _record(
        ArtifactRevision,
        run_id=RUN_ID,
        artifact_id="core_v2_package_manifest",
        revision=1,
        path="output/intermediate/core_v2_package_manifest.json",
        sha256=sha256_hex(package_bytes),
        size_bytes=len(package_bytes),
        frozen=True,
        producer_kind="control_tool",
        producer_id="core-v2-finalize-complete",
        created_at=timestamp,
    )
    package = _record(
        PackageReadyRecord,
        package_id="PACKAGE-TERMINAL-001",
        run_id=RUN_ID,
        finalization_id=finalization.finalization_id,
        archive_id=archive.archive_id,
        package_manifest_artifact={
            "artifact_id": package_revision.artifact_id,
            "revision": package_revision.revision,
        },
        package_manifest_sha256=package_revision.sha256,
        artifact_count=3,
        created_at=timestamp,
        package_event_id="EVT-TERMINAL-PACKAGE-001",
        accepted_transaction_id=transaction_id,
        request_fingerprint="f" * 64,
    )
    archive_bindings = tuple(
        _record(
            RunArchiveArtifactBinding,
            run_id=RUN_ID,
            archive_id=archive.archive_id,
            position=position,
            artifact_id=item.artifact_id,
            artifact_revision=item.revision,
            artifact_sha256=item.sha256,
            usage=archive_artifact_usage(item.artifact_id),
            accepted_transaction_id=transaction_id,
        )
        for position, item in enumerate(archive_members)
    )
    package_members = (
        (reader_revision, "reader"),
        (archive_revision, "archive"),
        (package_revision, "manifest"),
    )
    package_bindings = tuple(
        _record(
            PackageArtifactBinding,
            run_id=RUN_ID,
            package_id=package.package_id,
            position=position,
            artifact_id=item.artifact_id,
            artifact_revision=item.revision,
            artifact_sha256=item.sha256,
            usage=usage,
            accepted_transaction_id=transaction_id,
        )
        for position, (item, usage) in enumerate(package_members)
    )
    receipt = _record(
        TransactionReceipt,
        transaction_id=transaction_id,
        run_id=RUN_ID,
        transaction_type=transaction_type_for("finalize_complete"),
        prior_revision=1,
        committed_revision=2,
        committed_at=timestamp,
        projection_status="current",
        artifact_revisions=[
            {"artifact_id": archive_revision.artifact_id, "revision": 1},
            {"artifact_id": package_revision.artifact_id, "revision": 1},
        ],
        finalizations=[{"finalization_id": finalization.finalization_id}],
        run_archives=[{"archive_id": archive.archive_id}],
        run_archive_artifact_bindings=[
            {"archive_id": archive.archive_id, "position": item.position}
            for item in archive_bindings
        ],
        package_ready_records=[{"package_id": package.package_id}],
        package_artifact_bindings=[
            {"package_id": package.package_id, "position": item.position}
            for item in package_bindings
        ],
    )
    terminal_records = (
        _record(
            ArtifactRecord,
            run_id=RUN_ID,
            artifact_id=archive_revision.artifact_id,
            current_revision=1,
            status="valid",
            required=True,
            path=archive_revision.path,
            format="json",
        ),
        _record(
            ArtifactRecord,
            run_id=RUN_ID,
            artifact_id=package_revision.artifact_id,
            current_revision=1,
            status="valid",
            required=True,
            path=package_revision.path,
            format="json",
        ),
    )
    post = replace(
        pre,
        store_revision=2,
        artifacts=(*pre.artifacts, *terminal_records),
        artifact_revisions=(
            *pre.artifact_revisions,
            archive_revision,
            package_revision,
        ),
        finalizations=(finalization,),
        run_archives=(archive,),
        run_archive_artifact_bindings=archive_bindings,
        package_ready_records=(package,),
        package_artifact_bindings=package_bindings,
        transactions=(initialization_with_render, receipt),
    )
    history = replace(
        history,
        artifact_contents=MappingProxyType(
            {
                **history.artifact_contents,
                (RUN_ID, archive_revision.artifact_id, 1): archive_bytes,
                (RUN_ID, package_revision.artifact_id, 1): package_bytes,
            }
        ),
    )
    return history, post, receipt


def _forge_terminal_membership(post: object, target: str, forgery: str):
    binding_field = (
        "run_archive_artifact_bindings"
        if target == "archive"
        else "package_artifact_bindings"
    )
    bindings = getattr(post, binding_field)
    if forgery == "insertion":
        forged_bindings = (
            *bindings,
            bindings[-1].model_copy(update={"position": len(bindings)}),
        )
    elif forgery == "deletion":
        forged_bindings = bindings[:-1]
    elif forgery == "substitution":
        forged_bindings = (
            bindings[0].model_copy(update={"artifact_id": bindings[1].artifact_id}),
            *bindings[1:],
        )
    elif forgery == "duplicate":
        forged_bindings = (*bindings, bindings[0])
    elif forgery == "reorder":
        forged_bindings = (
            bindings[0].model_copy(update={"position": 1}),
            bindings[1].model_copy(update={"position": 0}),
            *bindings[2:],
        )
    elif forgery == "stale":
        forged_bindings = (
            bindings[0].model_copy(
                update={"artifact_revision": bindings[0].artifact_revision + 1}
            ),
            *bindings[1:],
        )
    elif forgery == "cross_run":
        forged_bindings = (
            bindings[0].model_copy(update={"run_id": "RUN-TERMINAL-OTHER-001"}),
            *bindings[1:],
        )
    elif forgery == "wrong_usage":
        forged_bindings = (
            bindings[0].model_copy(
                update={"usage": "evidence" if target == "archive" else "archive"}
            ),
            *bindings[1:],
        )
    elif forgery == "member_hash":
        forged_bindings = (
            bindings[0].model_copy(update={"artifact_sha256": "0" * 64}),
            *bindings[1:],
        )
    elif forgery == "count":
        if target == "archive":
            record = post.run_archives[0]
            return replace(
                post,
                run_archives=(
                    record.model_copy(
                        update={"included_count": record.included_count + 1}
                    ),
                ),
            )
        record = post.package_ready_records[0]
        return replace(
            post,
            package_ready_records=(
                record.model_copy(update={"artifact_count": record.artifact_count + 1}),
            ),
        )
    elif forgery == "aggregate_hash":
        if target == "archive":
            record = post.run_archives[0]
            return replace(
                post,
                run_archives=(record.model_copy(update={"manifest_sha256": "0" * 64}),),
            )
        record = post.package_ready_records[0]
        return replace(
            post,
            package_ready_records=(
                record.model_copy(update={"package_manifest_sha256": "0" * 64}),
            ),
        )
    else:
        raise AssertionError(f"unknown forgery: {forgery}")
    return replace(post, **{binding_field: forged_bindings})


@pytest.mark.parametrize("target", ("archive", "package"))
@pytest.mark.parametrize(
    "forgery",
    (
        "insertion",
        "deletion",
        "substitution",
        "duplicate",
        "reorder",
        "stale",
        "cross_run",
        "wrong_usage",
        "count",
        "member_hash",
        "aggregate_hash",
    ),
)
def test_archive_and_package_reconstruction_rejects_parameterized_forgeries(
    tmp_path: Path,
    target: str,
    forgery: str,
) -> None:
    history, post, receipt = _terminal_reconstruction_fixture(tmp_path)
    CoreRunDomainVerifier._verify_archive_package_reconstruction(
        history,
        post,
        receipt,
    )

    with pytest.raises(CoreRunError) as error:
        CoreRunDomainVerifier._verify_archive_package_reconstruction(
            history,
            _forge_terminal_membership(post, target, forgery),
            receipt,
        )
    assert error.value.code == f"{target}_membership_invalid"
