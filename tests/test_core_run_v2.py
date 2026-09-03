
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.contracts.v2 import Approval, ArtifactSubmitRequest, AuditPromotionRequest, ClaimFreezeRequest, CoreRunInitializeRequest, Delivery, ExecutionSourceManifest, EventEnvelope, GateCheckRequest, IntegrityCheckRequest, InternalApprovalRequest, InvocationStartRequest, OwnedArtifactSubmitRequest, ReceiptCheckoutBinding, RunOutputContract, StageCompleteRequest
from multi_agent_brief.control_store import ControlStoreIntegrityError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2 import ArtifactAcceptanceService, ClaimFreezeService, CoreRunService, GateEvaluationService
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.artifacts import _input_classification_bytes
from multi_agent_brief.core_run_v2.checkout import build_checkout_revision
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.lineage import verify_no_post_seal_records
from multi_agent_brief.core_run_v2.policy import REQUIRED_AUDITOR_GATES, required_auditor_gates, run_contract_fingerprint
from multi_agent_brief.core_run_v2.errors import CoreRunError, CoreRunResult
from multi_agent_brief.core_run_v2.terminal import CoreRunTerminalService
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier, _verified_core_receipt_binding, resolve_core_replay
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.quality_gates.contract import GATE_IDS
from multi_agent_brief.evaluation_v2.staging import (
    CLOCK,
    NOW,
    RUN_ID,
    WORKSPACE_ID,
    _advance_before_auditor,
    _advance_to_analyst_ready,
    _advance_to_auditor_ready,
    _advance_to_claim_ledger_ready,
    _advance_to_finalize_ready,
    _advance_to_input_governance_ready,
    _advance_to_scout_ready,
    _bind_init_payload,
    _candidate_payload,
    _complete_stage,
    _gate_request,
    _initialize,
    _record,
    _require_supported_working_projection,
    _screened_payload,
    _start_invocation,
    _stage,
    _store_revision,
    _submit_proposal,
    _submit_source,
    _write_json,
)




def _commit_core_fixture(store: SQLiteControlStore, unit, *, observer=None):
    """Commit an intentionally forged core receipt with a valid checkout edge."""

    snapshot = store.load_snapshot(unit.run_id)
    current = {
        (item.artifact_id, item.revision): item for item in snapshot.artifact_revisions
    }
    selected = {
        artifact.artifact_id: current[(artifact.artifact_id, artifact.current_revision)]
        for artifact in snapshot.artifacts
        if artifact.current_revision > 0
        and not current[
            (artifact.artifact_id, artifact.current_revision)
        ].path.startswith("briefloop.db.blobs/")
    }
    selected.update(
        {
            item.record.artifact_id: item.record
            for item in unit._artifact_revisions
            if not item.record.path.startswith("briefloop.db.blobs/")
        }
    )
    committed = {
        receipt.transaction_id: receipt.committed_revision
        for receipt in snapshot.transactions
    }
    current_checkout_binding = max(
        snapshot.receipt_checkout_bindings,
        key=lambda item: committed[item.transaction_id],
        default=None,
    )
    pre_checkout_revision_id = (
        None
        if current_checkout_binding is None
        else current_checkout_binding.post_checkout_revision_id
    )
    checkout = build_checkout_revision(
        workspace_id=snapshot.workspace_id,
        run_id=unit.run_id,
        transaction_id=unit.transaction_id,
        created_at=CLOCK(),
        artifact_revisions=selected.values(),
        parent_checkout_revision_id=pre_checkout_revision_id,
    )
    unit.put_checkout_revision(checkout.record)
    for member in checkout.members:
        unit.put_checkout_revision_member(member)
    unit.put_receipt_checkout_binding(
        ReceiptCheckoutBinding.model_validate(
            {
                "schema_version": ReceiptCheckoutBinding.schema_id,
                "workspace_id": snapshot.workspace_id,
                "run_id": unit.run_id,
                "transaction_id": unit.transaction_id,
                "pre_run_id": unit.run_id,
                "pre_checkout_revision_id": pre_checkout_revision_id,
                "post_run_id": unit.run_id,
                "post_checkout_revision_id": checkout.record.checkout_revision_id,
            },
            strict=True,
        )
    )
    return unit.commit(_postcommit_observer=observer)


ROOT = Path(__file__).parents[1]





def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    return workspace




def _store_opener_with_failure(workspace: Path, failure_stage: str):
    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise ControlStoreIntegrityError("injected_core_run_failure")

    def open_store() -> SQLiteControlStore:
        return SQLiteControlStore.open(
            workspace / "briefloop.db",
            clock=CLOCK,
            _failure_hook=fail,
        )

    return open_store



def _execution_authorization(workspace: Path) -> dict[str, object]:
    source_path = workspace / "input" / "authorized-source.txt"
    source_path.write_text("frozen authorized evidence\n", encoding="utf-8")
    content = source_path.read_bytes()
    member = {
        "source_id": "SRC-AUTHORIZED-001",
        "input_path": "input/authorized-source.txt",
        "content_sha256": sha256_hex(content),
        "content_media_type": "text/plain",
        "origin_type": "manual_evidence",
        "acquisition_method": "manual_evidence",
        "material_kind": "full_content",
        "provider": None,
        "locator": {"kind": "file", "path": "input/authorized-source.txt"},
        "title": "Authorized source",
        "publisher": None,
        "published_at": "2026-07-14",
        "retrieved_at": NOW,
        "source_category": "other",
        "retrieval_source_type": "local_file",
        "underlying_evidence_type": "unknown",
        "raw_underlying_evidence_type": None,
        "document_kind": None,
        "opened_at": None,
        "resolved_at": None,
    }
    manifest = ExecutionSourceManifest.model_validate(
        {
            "schema_version": ExecutionSourceManifest.schema_id,
            "members": [member],
        },
        strict=True,
    )
    canonical = canonical_json_bytes(
        manifest.model_dump(mode="json", exclude_unset=False)
    )
    return {
        "schema_version": "briefloop.run_execution_authorization_input.v2",
        "completion_target": "finalized_local",
        "source_manifest": manifest.model_dump(mode="json", exclude_unset=False),
        "source_manifest_sha256": sha256_hex(canonical),
        "source_manifest_member_count": 1,
        "repair_budget": 1,
    }


def _replace_with_external_hardlink(path: Path, *, outside: Path) -> bytes:
    """Replace one workspace leaf with a distinct external hardlink alias."""

    original = path.read_bytes()
    outside.write_bytes(original)
    path.unlink()
    try:
        os.link(outside, path)
    except OSError as exc:
        pytest.skip(f"test filesystem does not support hardlinks: {exc}")
    outside_info = outside.stat()
    path_info = path.stat()
    assert (path_info.st_dev, path_info.st_ino) == (
        outside_info.st_dev,
        outside_info.st_ino,
    )
    assert path_info.st_nlink > 1
    return original


def _link_workspace_file_after_pre_stat_before_open(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: Path,
    outside: Path,
) -> dict[str, bool]:
    """Race the real workspace-file open with a new external hardlink."""

    target_info = target.stat()
    target_identity = (target_info.st_dev, target_info.st_ino)
    original_open = os.open
    original_read = os.read
    state = {"hardlink_created": False, "target_body_read": False}

    def intercept_open(path: object, flags: int, mode: int = 0o777) -> int:
        candidate = os.fspath(path)
        if (
            not state["hardlink_created"]
            and isinstance(candidate, str)
            and Path(candidate) == target
        ):
            try:
                os.link(target, outside)
            except OSError as exc:
                raise AssertionError(f"hardlink creation failed: {exc}") from exc
            if target.stat().st_nlink <= 1:
                raise AssertionError("target hardlink was not created")
            state["hardlink_created"] = True
        return original_open(path, flags, mode)

    def intercept_read(descriptor: int, size: int) -> bytes:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) == target_identity:
            state["target_body_read"] = True
            raise AssertionError("target body read after hardlink race")
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "open", intercept_open)
    monkeypatch.setattr(os, "read", intercept_read)
    return state


def test_initialize_freezes_receipt_owned_execution_authorization(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    authorization = _execution_authorization(workspace)
    service = _initialize(workspace, execution_authorization=authorization)

    doctor = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id="REQ-AUTHORIZED-DOCTOR-001",
            run_id=RUN_ID,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert doctor.status == "committed", doctor.to_dict()

    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
    assert len(verified.snapshot.run_execution_authorizations) == 1
    record = verified.snapshot.run_execution_authorizations[0]
    assert record.completion_target == "finalized_local"
    assert record.source_manifest_member_count == 1
    receipt = next(
        item
        for item in verified.snapshot.transactions
        if item.transaction_id == record.accepted_transaction_id
    )
    assert [item.authorization_id for item in receipt.run_execution_authorizations] == [
        record.authorization_id
    ]
    action = classify_core_run_next_action(verified)
    assert (
        action.action_kind,
        action.effect_kind,
        action.reason_code,
        action.stage_id,
        action.role_id,
        action.request_schema_id,
    ) == (
        "deterministic",
        "authorized_source_pack_commit",
        "authorized_source_pack_commit_required",
        "source-discovery",
        None,
        None,
    )




def test_core_applies_authorized_source_pack_without_a_host_dto(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(
        workspace, execution_authorization=_execution_authorization(workspace)
    )
    assert (
        service.doctor_check(
            _record(
                IntegrityCheckRequest,
                request_id="REQ-AUTHORIZED-PACK-DOCTOR-001",
                run_id=RUN_ID,
                expected_store_revision=_store_revision(workspace),
            )
        ).status
        == "committed"
    )
    result = service.apply_authorized_source_pack()
    assert result.status == "committed", result.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
    assert len(verified.snapshot.sources) == 1
    assert len(verified.snapshot.owned_artifact_submissions) == 1
    assert (
        verified.snapshot.owned_artifact_submissions[0].artifact_id
        == "input_classification"
    )
    assert result.receipt is not None
    assert result.receipt.checkout_publication_intents == []
    projection = workspace / "output" / "input_classification.json"
    assert not projection.exists()
    projection.parent.mkdir(parents=True, exist_ok=True)
    projection.write_text('{"forged":true}', encoding="utf-8")
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
    assert classify_core_run_next_action(verified).effect_kind == "stage_complete"
    revision_after_commit = _store_revision(workspace)
    (workspace / "input" / "authorized-source.txt").unlink()
    replayed = service.apply_authorized_source_pack()
    assert replayed.status == "replayed"
    assert result.receipt is not None
    assert replayed.receipt is not None
    assert replayed.receipt.transaction_id == result.receipt.transaction_id
    assert _store_revision(workspace) == revision_after_commit
    assert projection.read_text(encoding="utf-8") == '{"forged":true}'


def test_read_workspace_file_rejects_hardlinked_leaf_without_hash_or_content(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    source_path = workspace / "input" / "hardlinked-source.txt"
    source_path.write_bytes(b"external hardlink sentinel\n")
    original = _replace_with_external_hardlink(
        source_path,
        outside=tmp_path / "outside-hardlinked-source.txt",
    )

    observed = read_workspace_file(workspace, "input/hardlinked-source.txt")

    assert observed.entry_kind == "unsafe"
    assert observed.sha256 is None
    assert observed.content is None
    assert source_path.read_bytes() == original




def test_authorized_source_pack_rejects_hardlinked_member_before_invocation_start(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(
        workspace, execution_authorization=_execution_authorization(workspace)
    )
    assert (
        service.doctor_check(
            _record(
                IntegrityCheckRequest,
                request_id="REQ-AUTHORIZED-HARDLINK-DOCTOR-001",
                run_id=RUN_ID,
                expected_store_revision=_store_revision(workspace),
            )
        ).status
        == "committed"
    )
    source_path = workspace / "input" / "authorized-source.txt"
    original = _replace_with_external_hardlink(
        source_path,
        outside=tmp_path / "outside-authorized-source.txt",
    )
    database = workspace / "briefloop.db"
    before_bytes = database.read_bytes()

    result = service.apply_authorized_source_pack()

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "source_pack_authorization_invalid",
    }
    assert database.read_bytes() == before_bytes
    assert source_path.read_bytes() == original
    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        snapshot = store.load_snapshot(RUN_ID)
    assert not any(
        item.role_id == "source-provider" and item.status == "active"
        for item in snapshot.invocations
    )
    assert not any(
        item.core_run_binding is not None
        and item.core_run_binding.effect_kind == "invocation_start"
        for item in snapshot.events
    )
































def test_input_governance_accepts_only_recomputed_canonical_tool_bytes(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_input_governance_ready(workspace)
    scratch = workspace / "scratch" / "input-governance-v2"
    scratch.mkdir(parents=True, exist_ok=True)
    candidate = scratch / "input_classification.json"
    candidate.write_bytes(b"this is not json\n")
    before = _store_revision(workspace)
    request_values = {
        "run_id": RUN_ID,
        "artifact_id": "input_classification",
        "invocation_id": None,
        "producer_tool_id": "input-governance-v2",
        "input_path": candidate.relative_to(workspace).as_posix(),
        "expected_store_revision": before,
        "expected_artifact_revision": 0,
        "expected_parent_artifact": None,
    }
    rejected = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-INPUT-GOV-FORGED",
            **request_values,
        )
    )
    assert rejected.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "artifact_input_unsafe",
    }
    assert _store_revision(workspace) == before
    assert _stage(workspace, "input-governance").status == "ready"
    assert not (
        workspace / "output" / "intermediate" / "input_classification.json"
    ).exists()

    canonical = _input_classification_bytes(workspace)
    candidate.write_bytes(canonical)
    accepted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-INPUT-GOV-CANONICAL",
            **request_values,
        )
    )
    assert accepted.status == "committed", accepted.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert (
            store.read_artifact_revision_bytes(
                RUN_ID,
                "input_classification",
                1,
            )
            == canonical
        )
    _complete_stage(
        service,
        workspace,
        stage_id="input-governance",
        artifacts=[("input_classification", 1)],
    )
    assert _stage(workspace, "input-governance").status == "complete"




def test_input_classification_identity_is_workspace_relative_and_selector_stable(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    source = workspace / "input" / "source.md"
    source.write_text("public evidence\n", encoding="utf-8")
    canonical = _input_classification_bytes(workspace)
    payload = json.loads(canonical)
    reported_paths = [
        item[field]
        for lane in payload.values()
        for item in lane
        for field in ("path", "extracted_markdown")
        if item.get(field)
    ]
    assert reported_paths
    assert all(not Path(item).is_absolute() for item in reported_paths)
    assert "input/source.md" in reported_paths

    if os.name != "nt":
        alias = tmp_path / "workspace-alias"
        alias.symlink_to(workspace, target_is_directory=True)
        assert _input_classification_bytes(alias) == canonical

    if sys.platform == "darwin" and str(workspace).startswith("/private/var/"):
        var_alias = Path(str(workspace).removeprefix("/private"))
        assert var_alias.is_dir()
        assert _input_classification_bytes(var_alias) == canonical


@pytest.mark.skipif(sys.platform != "win32", reason="Windows publication contract")
def test_windows_artifact_publication_is_rejected_before_store_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    doctor = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id="REQ-DOCTOR-WINDOWS-PUBLICATION",
            run_id=RUN_ID,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert doctor.status == "committed", doctor.to_dict()
    invocation_id = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-WINDOWS-PUBLICATION",
        stage_id="source-discovery",
        role_id="source-planner",
    )
    scratch = workspace / "scratch" / invocation_id / "source_candidates.yaml"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("sources:\n  - SRC-001\n", encoding="utf-8")
    before = _store_revision(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_snapshot = store.load_snapshot(RUN_ID)

    result = ArtifactAcceptanceService(workspace, clock=CLOCK).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-WINDOWS-PUBLICATION",
            run_id=RUN_ID,
            artifact_id="source_candidates",
            invocation_id=invocation_id,
            producer_tool_id=None,
            input_path=scratch.relative_to(workspace).as_posix(),
            expected_store_revision=before,
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "checkout_publication_unsupported",
    }
    assert _store_revision(workspace) == before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    assert snapshot.artifacts == before_snapshot.artifacts
    assert snapshot.artifact_revisions == before_snapshot.artifact_revisions
    assert snapshot.checkout_revisions == before_snapshot.checkout_revisions
    assert scratch.read_text(encoding="utf-8") == "sources:\n  - SRC-001\n"















def _balanced_output_contract() -> dict[str, object]:
    return RunOutputContract.model_validate(
        {
            "schema_version": RunOutputContract.schema_id,
            "output_extent": "balanced",
            "extent_catalog_id": "briefloop.output_extent_catalog.v1",
            "body_length_basis": "reader_body_excluding_source_reference_sections",
            "body_length_unit": "word_equivalent_tokens",
            "resolved_minimum": 600,
            "resolved_maximum": 800,
        },
        strict=True,
    ).model_dump(mode="json", exclude_unset=False)


def _compact_output_contract() -> dict[str, object]:
    return RunOutputContract.model_validate(
        {
            "schema_version": RunOutputContract.schema_id,
            "output_extent": "compact",
            "extent_catalog_id": "briefloop.output_extent_catalog.v1",
            "body_length_basis": "reader_body_excluding_source_reference_sections",
            "body_length_unit": "word_equivalent_tokens",
            "resolved_minimum": 350,
            "resolved_maximum": 550,
        },
        strict=True,
    ).model_dump(mode="json", exclude_unset=False)


def test_legacy_run_direction_binding_without_output_contract_remains_verifiable(
    tmp_path: Path,
) -> None:
    legacy_workspace = _workspace(tmp_path / "legacy")
    _initialize(legacy_workspace)
    database = legacy_workspace / "briefloop.db"

    with sqlite3.connect(database) as connection:
        # This isolated fixture emulates a pre-output-contract frozen row.
        connection.execute("DROP TRIGGER run_contract_bindings_no_update")
        row = connection.execute(
            "SELECT payload_json FROM run_contract_bindings WHERE run_id = ?",
            (RUN_ID,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        payload["run_direction"].pop("output_contract", None)
        connection.execute(
            "UPDATE run_contract_bindings SET payload_json = ? WHERE run_id = ?",
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                RUN_ID,
            ),
        )
        connection.execute(
            "CREATE TRIGGER run_contract_bindings_no_update "
            "BEFORE UPDATE ON run_contract_bindings "
            "BEGIN SELECT RAISE(ABORT, 'append_only'); END"
        )

    with SQLiteControlStore.open(database, clock=CLOCK) as store:
        revision = store.current_revision
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
        assert verified.binding.run_direction.output_contract is None
        assert store.current_revision == revision

    bound_workspace = _workspace(tmp_path / "bound")
    _initialize(bound_workspace, output_contract=_balanced_output_contract())
    bound_database = bound_workspace / "briefloop.db"
    with SQLiteControlStore.open(bound_database, clock=CLOCK) as store:
        binding = store.load_snapshot(RUN_ID).run_contract_bindings[0]
        absent_fingerprint = run_contract_fingerprint(
            runtime=binding.runtime,
            stage_specs_schema=binding.stage_specs_schema,
            stage_specs_sha256=binding.stage_specs_sha256,
            artifact_contracts_schema=binding.artifact_contracts_schema,
            artifact_contracts_sha256=binding.artifact_contracts_sha256,
            policy_pack_schema=binding.policy_pack_schema,
            policy_pack_name=binding.policy_pack_name,
            policy_pack_sha256=binding.policy_pack_sha256,
            runtime_adapter_sha256=binding.runtime_adapter_sha256,
            runtime_adapter_fingerprint=binding.runtime_adapter_fingerprint,
            runtime_source_plan_sha256=binding.runtime_source_plan_sha256,
            runtime_source_plan_fingerprint=binding.runtime_source_plan_fingerprint,
            run_direction={
                key: value
                for key, value in binding.run_direction.model_dump(
                    mode="json", exclude_unset=False
                ).items()
                if key != "output_contract"
            },
            workspace_config_sha256=binding.workspace_config_sha256,
            sources_config_sha256=binding.sources_config_sha256,
            role_topology=binding.role_topology,
            gate_strictness=binding.gate_strictness,
            input_governance_required=binding.input_governance_required,
        )
        assert binding.contract_fingerprint != absent_fingerprint

    with sqlite3.connect(bound_database) as connection:
        # Forge only the frozen fixture payload; the production Store stays append-only.
        connection.execute("DROP TRIGGER run_contract_bindings_no_update")
        row = connection.execute(
            "SELECT payload_json FROM run_contract_bindings WHERE run_id = ?",
            (RUN_ID,),
        ).fetchone()
        assert row is not None
        forged = json.loads(row[0])
        forged["run_direction"]["output_contract"] = _compact_output_contract()
        connection.execute(
            "UPDATE run_contract_bindings SET payload_json = ? WHERE run_id = ?",
            (
                json.dumps(forged, sort_keys=True, separators=(",", ":")),
                RUN_ID,
            ),
        )
        connection.execute(
            "CREATE TRIGGER run_contract_bindings_no_update "
            "BEFORE UPDATE ON run_contract_bindings "
            "BEGIN SELECT RAISE(ABORT, 'append_only'); END"
        )

    with pytest.raises(ControlStoreIntegrityError, match="core_run_relation_invalid"):
        SQLiteControlStore.open(bound_database, clock=CLOCK)


def test_store_frozen_output_contract_blocks_auditor_gate_and_stage_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_auditor_ready(
        workspace,
        output_contract=_balanced_output_contract(),
    )

    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.gates.evaluate_quality_gate_findings_preloaded",
        lambda **_kwargs: {gate_id: [] for gate_id in GATE_IDS},
    )
    result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace, request_id="REQ-GATE-OUTPUT-CONTRACT")
    )
    assert result.status == "committed", result.to_dict()

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    required_gate_ids = required_auditor_gates(
        snapshot.run_contract_bindings[0].run_direction
    )
    assert required_gate_ids == (*REQUIRED_AUDITOR_GATES, "final_abstract_quality")
    final_quality = next(
        item
        for item in snapshot.gate_evaluations
        if item.gate_id == "final_abstract_quality"
    )
    assert final_quality.blocking is True
    finding = next(
        item
        for item in snapshot.gate_findings
        if item.evaluation_id == final_quality.evaluation_id
    )
    assert finding.finding_type == "reader_body_length_out_of_bounds"
    assert finding.repair_owner == "editor"
    assert finding.metadata == {
        "output_extent": "balanced",
        "extent_catalog_id": "briefloop.output_extent_catalog.v1",
        "basis": "reader_body_excluding_source_reference_sections",
        "unit": "word_equivalent_tokens",
        "resolved_minimum": 600,
        "resolved_maximum": 800,
        "actual": finding.metadata["actual"],
    }
    assert finding.metadata["actual"] < 600

    auditor = _stage(workspace, "auditor")
    completion = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-AUDITOR-OUTPUT-CONTRACT",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="out-of-bounds reader contract must block completion",
            expected_stage_revision=auditor.revision,
            expected_store_revision=snapshot.store_revision,
            expected_artifact_revisions=[
                {"artifact_id": "claim_ledger", "revision": 1},
                {"artifact_id": "audited_brief", "revision": 1},
                {"artifact_id": "audit_report", "revision": 1},
                {"artifact_id": "auditor_quality_gate_report", "revision": 1},
                {"artifact_id": "analyst_draft_snapshot", "revision": 1},
            ],
            expected_gate_evaluation_ids=[
                item.evaluation_id
                for item in snapshot.gate_evaluations
                if item.gate_id in required_gate_ids
            ],
        )
    )
    assert completion.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "stage_gate_binding_invalid",
    }


def test_gate_batches_append_and_old_request_exactly_replays(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    core = _advance_to_auditor_ready(workspace)
    service = GateEvaluationService(workspace, clock=CLOCK)
    request = _gate_request(workspace, request_id="REQ-GATE-REPLAY")
    first = service.evaluate(request)
    assert first.status == "committed", first.to_dict()
    committed_revision = _store_revision(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    report_path = workspace / next(
        item.path
        for item in snapshot.artifacts
        if item.artifact_id == "auditor_quality_gate_report"
    )
    report_bytes = report_path.read_bytes()

    replay = service.evaluate(request)
    assert replay.status == "replayed"
    assert replay.receipt == first.receipt
    assert replay.primary_record_id == first.primary_record_id
    assert _store_revision(workspace) == committed_revision
    assert report_path.read_bytes() == report_bytes

    second_values = request.model_dump(mode="python", exclude_unset=False)
    second_values.update(
        request_id="REQ-GATE-SECOND-BATCH",
        expected_store_revision=committed_revision,
        expected_report_artifact_revision=1,
    )
    second_request = GateCheckRequest.model_validate(second_values, strict=True)
    second = service.evaluate(second_request)
    assert second.status == "committed", second.to_dict()
    assert _store_revision(workspace) == committed_revision + 1
    second_report_bytes = report_path.read_bytes()
    assert second_report_bytes != report_bytes
    old_replay = service.evaluate(request)
    assert old_replay.status == "replayed"
    assert old_replay.receipt == first.receipt
    assert report_path.read_bytes() == second_report_bytes
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)

    _complete_stage(
        core,
        workspace,
        stage_id="auditor",
        artifacts=[
            ("claim_ledger", 1),
            ("audited_brief", 1),
            ("audit_report", 1),
            ("auditor_quality_gate_report", 2),
            ("analyst_draft_snapshot", 1),
        ],
        gate_evaluation_ids=[
            item.evaluation_id
            for item in snapshot.gate_evaluations
            if item.report_artifact.revision == 2
            if item.gate_id in REQUIRED_AUDITOR_GATES
        ],
    )
    after_completion = _store_revision(workspace)
    assert _stage(workspace, "finalize").status == "ready"
    lifecycle_replay = service.evaluate(request)
    assert lifecycle_replay.status == "replayed"
    assert lifecycle_replay.receipt == first.receipt
    assert _store_revision(workspace) == after_completion
    assert report_path.read_bytes() == second_report_bytes








def test_audit_intake_rejects_a_non_brief_target_before_acceptance(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_before_auditor(workspace)
    auditor = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-AUDITOR-WRONG-TARGET",
        stage_id="auditor",
        role_id="auditor",
    )
    scratch = workspace / "scratch" / auditor
    proposal_path = scratch / "audit_proposal.json"
    _write_json(
        proposal_path,
        {
            "schema_version": "briefloop.audit_proposal.v2",
            "proposal_id": "PROP-AUDIT-WRONG-TARGET",
            "run_id": RUN_ID,
            "artifact_id": "claim_ledger",
            "artifact_revision": 1,
            "decision": "pass",
            "created_at": NOW,
            "findings": [],
        },
    )
    request_path = scratch / "submit_request.json"
    _write_json(
        request_path,
        _record(
            ArtifactSubmitRequest,
            request_id="REQ-AUDIT-WRONG-TARGET",
            run_id=RUN_ID,
            artifact_id="audit_proposal",
            invocation_id=auditor,
            input_path=proposal_path.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=0,
        ).model_dump(mode="json", exclude_unset=False),
    )
    result = IntakeService(workspace, clock=CLOCK).submit_proposal(
        "audit",
        request_path.relative_to(workspace).as_posix(),
    )
    assert result.status == "rejected_recorded"
    assert result.error_code == "audit_target_invalid"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(RUN_ID)
    assert not any(
        item.proposal_id == "PROP-AUDIT-WRONG-TARGET"
        for item in after.accepted_proposals
    )
    audit_artifact = next(
        (item for item in after.artifacts if item.artifact_id == "audit_proposal"),
        None,
    )
    assert audit_artifact is None or audit_artifact.current_revision == 0
    assert _stage(workspace, "auditor").status == "ready"
    assert _stage(workspace, "finalize").status == "pending"






def test_auditor_completion_requires_report_from_current_audit_proposal(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_before_auditor(workspace)
    auditor = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-AUDITOR-CURRENT",
        stage_id="auditor",
        role_id="auditor",
    )
    _submit_proposal(
        workspace,
        lane="audit",
        invocation_id=auditor,
        request_id="REQ-AUDIT-CURRENT",
        artifact_id="audit_proposal",
        payload={
            "schema_version": "briefloop.audit_proposal.v2",
            "proposal_id": "PROP-AUDIT-002",
            "run_id": RUN_ID,
            "artifact_id": "audited_brief",
            "artifact_revision": 1,
            "decision": "pass",
            "created_at": NOW,
            "findings": [],
        },
    )
    # Single-shot auditor stage: once the proposal exists the next action is
    # the promotion, so a competing audit delegate is rejected fail-closed and
    # no second (stale-making) proposal can be produced.
    competing = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-AUDITOR-COMPETING",
            run_id=RUN_ID,
            stage_id="auditor",
            role_id="auditor",
            runtime="operator",
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert competing.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "invocation_owner_mismatch",
    }

    promoted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).promote_audit_proposal(
        _record(
            AuditPromotionRequest,
            request_id="REQ-AUDIT-PROMOTE-CURRENT",
            run_id=RUN_ID,
            audit_proposal_id="PROP-AUDIT-002",
            expected_target_artifact={
                "artifact_id": "audited_brief",
                "revision": 1,
            },
            expected_audit_report_revision=0,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert promoted.status == "committed", promoted.to_dict()

    gate_service = GateEvaluationService(workspace, clock=CLOCK)
    gate_request = _gate_request(workspace, request_id="REQ-GATE-CURRENT-AUDIT")
    gate = gate_service.evaluate(gate_request)
    assert gate.status == "committed", gate.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before = store.load_snapshot(RUN_ID)
    gate_ids = [
        item.evaluation_id
        for item in before.gate_evaluations
        if item.gate_id in REQUIRED_AUDITOR_GATES
    ]
    stage = next(item for item in before.stage_states if item.stage_id == "auditor")

    # A completion claiming a report revision other than the current one is
    # rejected by the artifact binding check.
    rejected = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-AUDITOR-STALE-AUDIT",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="stale audit report cannot complete",
            expected_stage_revision=stage.revision,
            expected_store_revision=before.store_revision,
            expected_artifact_revisions=[
                {"artifact_id": artifact_id, "revision": revision}
                for artifact_id, revision in [
                    ("claim_ledger", 1),
                    ("audited_brief", 1),
                    ("audit_report", 2),
                    ("auditor_quality_gate_report", 1),
                    ("analyst_draft_snapshot", 1),
                ]
            ],
            expected_gate_evaluation_ids=gate_ids,
        )
    )
    assert rejected.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "stage_artifact_binding_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID) == before

    # A completion bound to the wrong gate evaluations is rejected by the gate
    # binding check.
    wrong_gates = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-AUDITOR-WRONG-GATES",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="gate set must match the current evaluation",
            expected_stage_revision=stage.revision,
            expected_store_revision=before.store_revision,
            expected_artifact_revisions=[
                {"artifact_id": artifact_id, "revision": revision}
                for artifact_id, revision in [
                    ("claim_ledger", 1),
                    ("audited_brief", 1),
                    ("audit_report", 1),
                    ("auditor_quality_gate_report", 1),
                    ("analyst_draft_snapshot", 1),
                ]
            ],
            expected_gate_evaluation_ids=[],
        )
    )
    assert wrong_gates.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "stage_gate_binding_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID) == before

    # The old gate request exactly replays without writes.
    replay = gate_service.evaluate(gate_request)
    assert replay.status == "replayed"
    assert replay.receipt == gate.receipt
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID) == before

    # The completion bound to the current report and gate commits.
    completed = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-AUDITOR-CURRENT",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="current audit report completes the stage",
            expected_stage_revision=stage.revision,
            expected_store_revision=before.store_revision,
            expected_artifact_revisions=[
                {"artifact_id": artifact_id, "revision": revision}
                for artifact_id, revision in [
                    ("claim_ledger", 1),
                    ("audited_brief", 1),
                    ("audit_report", 1),
                    ("auditor_quality_gate_report", 1),
                    ("analyst_draft_snapshot", 1),
                ]
            ],
            expected_gate_evaluation_ids=gate_ids,
        )
    )
    assert completed.status == "committed", completed.to_dict()














def _submit_human_assisted_draft(
    workspace: Path,
    *,
    invocation_id: str,
    request_id: str,
    artifact_id: str,
    revision: int,
    parent: dict[str, object] | None = None,
) -> bytes:
    _require_supported_working_projection()
    content = (
        f"# {artifact_id} revision {revision}\n\n"
        "ExampleCo opened a public pilot facility. [src:CL-0001]\n"
    ).encode()
    scratch = workspace / "scratch" / invocation_id / f"{artifact_id}.md"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(content)
    result = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id=request_id,
            run_id=RUN_ID,
            artifact_id=artifact_id,
            invocation_id=invocation_id,
            producer_tool_id=(
                "analyst-snapshot-v2"
                if artifact_id == "analyst_draft_snapshot"
                else None
            ),
            input_path=scratch.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=revision - 1,
            expected_parent_artifact=parent,
        )
    )
    assert result.status == "committed", result.to_dict()
    return content


_WRITER_ROUTE_ROLE_IDS = [
    "auditor",
    "claim-ledger",
    "editor",
    "scout",
    "screener",
    "source-planner",
    "source-provider",
    "writer",
]






def test_human_assisted_writer_satisfies_analyst_and_editor(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_analyst_ready(
        workspace,
        topology="human_assisted",
        role_ids=_WRITER_ROUTE_ROLE_IDS,
    )
    writer = _start_invocation(
        service,
        workspace,
        request_id="REQ-INVOKE-WRITER",
        stage_id="analyst",
        role_id="writer",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_reserved_conflict = store.load_snapshot(RUN_ID)
    rejected_analyst = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-ANALYST-WHILE-WRITER-RESERVED",
            run_id=RUN_ID,
            stage_id="analyst",
            role_id="analyst",
            runtime="operator",
            expected_store_revision=before_reserved_conflict.store_revision,
        )
    )
    assert rejected_analyst.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "invocation_owner_mismatch",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID) == before_reserved_conflict
    brief_path = workspace / "scratch" / writer / "audited_brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(
        "# ExampleCo weekly brief\n\n"
        "ExampleCo opened a public pilot facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_parent_rejection = store.load_snapshot(RUN_ID)
    rejected_parent = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-WRITER-WITH-PARENT",
            run_id=RUN_ID,
            artifact_id="audited_brief",
            invocation_id=writer,
            producer_tool_id=None,
            input_path=brief_path.relative_to(workspace).as_posix(),
            expected_store_revision=before_parent_rejection.store_revision,
            expected_artifact_revision=0,
            expected_parent_artifact={
                "artifact_id": "claim_ledger",
                "revision": 1,
            },
        )
    )
    assert rejected_parent.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "artifact_revision_conflict",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after_parent_rejection = store.load_snapshot(RUN_ID)
    assert after_parent_rejection == before_parent_rejection
    accepted = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-WRITER",
            run_id=RUN_ID,
            artifact_id="audited_brief",
            invocation_id=writer,
            producer_tool_id=None,
            input_path=brief_path.relative_to(workspace).as_posix(),
            expected_store_revision=_store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert accepted.status == "committed", accepted.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_writer_route_conflict = store.load_snapshot(RUN_ID)
    rejected_after_brief = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-ANALYST-AFTER-WRITER-BRIEF",
            run_id=RUN_ID,
            stage_id="analyst",
            role_id="analyst",
            runtime="operator",
            expected_store_revision=before_writer_route_conflict.store_revision,
        )
    )
    assert rejected_after_brief.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "invocation_owner_mismatch",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID) == before_writer_route_conflict
    _complete_stage(
        service,
        workspace,
        stage_id="analyst",
        artifacts=[("audited_brief", 1)],
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
        snapshot = verified.snapshot
        writer_submission = next(
            item
            for item in snapshot.owned_artifact_submissions
            if item.artifact_id == "audited_brief"
        )
        forged_payload = writer_submission.model_dump(
            mode="json",
            exclude_unset=False,
        )
        forged_payload["parent_artifact"] = {
            "artifact_id": "claim_ledger",
            "revision": 1,
        }
        forged_writer_submission = type(writer_submission).model_validate(
            forged_payload,
            strict=True,
        )
        with pytest.raises(
            CoreRunError,
            match="control_store_integrity_invalid",
        ):
            CoreRunDomainVerifier._verify_stage_chain(
                store,
                replace(
                    snapshot,
                    owned_artifact_submissions=tuple(
                        forged_writer_submission
                        if item.submission_id == writer_submission.submission_id
                        else item
                        for item in snapshot.owned_artifact_submissions
                    ),
                ),
                verified.contracts,
                verified.binding,
            )
    transitions = {
        (item.stage_id, item.transition_kind): item
        for item in snapshot.stage_transitions
    }
    assert transitions[("analyst", "complete")].producer_invocation_id == writer
    editor = transitions[("editor", "satisfied_by_topology")]
    assert editor.producer_invocation_id == writer
    assert editor.satisfaction_source_kind == "role"
    assert editor.satisfied_by_id == "writer"
    assert _stage(workspace, "auditor").status == "ready"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        CoreRunDomainVerifier().verify(store, RUN_ID)

    receipt = next(
        item
        for item in snapshot.transactions
        if item.transaction_id == "REQ-COMPLETE-ANALYST"
    )
    event_by_id = {item.event_id: item for item in snapshot.events}
    analyst_event = event_by_id[
        transitions[("analyst", "complete")].transition_event_id
    ]
    editor_event = event_by_id[editor.transition_event_id]
    forged_events = tuple(
        item.model_copy(
            update={
                "event_type": (
                    editor_event.event_type
                    if item.event_id == analyst_event.event_id
                    else analyst_event.event_type
                )
            }
        )
        if item.event_id in {analyst_event.event_id, editor_event.event_id}
        else item
        for item in snapshot.events
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        _verified_core_receipt_binding(
            replace(snapshot, events=forged_events),
            receipt,
        )




@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("missing", "unavailable"),
        ("malformed", "invalid"),
        ("invalid_finding", "invalid"),
    ],
)
def test_known_negative_gate_outcome_is_durable_and_blocks_auditor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_status: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_auditor_ready(workspace)
    direct_report = (
        workspace / "output" / "intermediate" / "auditor_quality_gate_report.json"
    )
    direct_report.parent.mkdir(parents=True, exist_ok=True)
    direct_report.write_text('{"status":"pass"}', encoding="utf-8")
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_direct = store.load_snapshot(RUN_ID)
    assert not before_direct.gate_evaluations
    assert (
        next(
            item
            for item in before_direct.artifacts
            if item.artifact_id == "auditor_quality_gate_report"
        ).current_revision
        == 0
    )

    def known_negative(**_kwargs):
        result: dict[str, object] = {gate_id: [] for gate_id in GATE_IDS}
        if mode == "missing":
            result.pop("freshness")
        elif mode == "malformed":
            result["freshness"] = "not-a-finding-list"
        else:
            result["freshness"] = [
                {
                    "finding_type": "bad-finding",
                    "severity": "not-a-severity",
                    "blocking_level": "blocking",
                }
            ]
        return result

    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.gates.evaluate_quality_gate_findings_preloaded",
        known_negative,
    )
    gate_result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace)
    )
    assert gate_result.status == "committed", gate_result.to_dict()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    freshness = next(
        item for item in snapshot.gate_evaluations if item.gate_id == "freshness"
    )
    assert freshness.status == expected_status
    assert freshness.blocking is True
    assert freshness.finding_ids
    before = snapshot.store_revision
    auditor = _stage(workspace, "auditor")
    completion = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id=f"REQ-COMPLETE-AUDITOR-{mode.upper()}",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="auditor output complete",
            expected_stage_revision=auditor.revision,
            expected_store_revision=before,
            expected_artifact_revisions=[
                {"artifact_id": "claim_ledger", "revision": 1},
                {"artifact_id": "audited_brief", "revision": 1},
                {"artifact_id": "audit_report", "revision": 1},
                {
                    "artifact_id": "auditor_quality_gate_report",
                    "revision": 1,
                },
                {"artifact_id": "analyst_draft_snapshot", "revision": 1},
            ],
            expected_gate_evaluation_ids=[
                item.evaluation_id
                for item in snapshot.gate_evaluations
                if item.gate_id in REQUIRED_AUDITOR_GATES
            ],
        )
    )
    assert completion.status == "failed_uncommitted"
    assert completion.error_code == "stage_gate_binding_invalid"
    assert _store_revision(workspace) == before
    assert _stage(workspace, "auditor").status == "ready"


@pytest.mark.parametrize("audit_mode", ["fail", "error_finding"])
def test_negative_audit_truth_blocks_auditor_without_rewriting_report(
    tmp_path: Path,
    audit_mode: str,
) -> None:
    workspace = _workspace(tmp_path)
    findings = (
        [
            {
                "finding_code": "UNSUPPORTED-CLAIM",
                "severity": "error",
                "artifact_id": "audited_brief",
                "summary": "One claim is not supported by frozen evidence.",
            }
        ]
        if audit_mode == "error_finding"
        else []
    )
    service = _advance_to_auditor_ready(
        workspace,
        audit_decision="fail" if audit_mode == "fail" else "pass",
        audit_findings=findings,
    )
    gate_result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace, request_id=f"REQ-GATE-{audit_mode.upper()}")
    )
    assert gate_result.status == "committed", gate_result.to_dict()

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
        report = next(
            item
            for item in snapshot.artifact_revisions
            if item.artifact_id == "audit_report" and item.revision == 1
        )
        report_bytes = store.read_artifact_revision_bytes(
            RUN_ID,
            report.artifact_id,
            report.revision,
        )
    before = snapshot.store_revision
    auditor = _stage(workspace, "auditor")
    result = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id=f"REQ-COMPLETE-AUDITOR-{audit_mode.upper()}",
            run_id=RUN_ID,
            stage_id="auditor",
            reason="negative audit truth cannot complete",
            expected_stage_revision=auditor.revision,
            expected_store_revision=before,
            expected_artifact_revisions=[
                {"artifact_id": "claim_ledger", "revision": 1},
                {"artifact_id": "audited_brief", "revision": 1},
                {"artifact_id": "audit_report", "revision": 1},
                {
                    "artifact_id": "auditor_quality_gate_report",
                    "revision": 1,
                },
                {"artifact_id": "analyst_draft_snapshot", "revision": 1},
            ],
            expected_gate_evaluation_ids=[
                item.evaluation_id
                for item in snapshot.gate_evaluations
                if item.gate_id in REQUIRED_AUDITOR_GATES
            ],
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "stage_artifact_binding_invalid",
    }
    assert _store_revision(workspace) == before
    assert _stage(workspace, "auditor") == auditor
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert (
            store.read_artifact_revision_bytes(
                RUN_ID,
                report.artifact_id,
                report.revision,
            )
            == report_bytes
        )


def test_unexpected_gate_evaluator_failure_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_auditor_ready(workspace)
    before = _store_revision(workspace)

    def explode(**_kwargs):
        raise RuntimeError("injected evaluator failure")

    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.gates.evaluate_quality_gate_findings_preloaded",
        explode,
    )
    result = GateEvaluationService(workspace, clock=CLOCK).evaluate(
        _gate_request(workspace)
    )
    assert result.status == "failed_uncommitted"
    assert result.error_code == "gate_input_binding_invalid"
    assert _store_revision(workspace) == before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    assert not snapshot.gate_evaluations
    report = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "auditor_quality_gate_report"
    )
    assert report.current_revision == 0




def test_direct_legacy_control_files_have_zero_run_truth_effect(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace)
    before = _store_revision(workspace)
    doctor_before = _stage(workspace, "doctor")
    controls = workspace / "output" / "intermediate"
    controls.mkdir(parents=True, exist_ok=True)
    (controls / "workflow_state.json").write_text(
        '{"current_stage":"finalize","stage_statuses":{"auditor":"complete"}}',
        encoding="utf-8",
    )
    (controls / "artifact_registry.json").write_text(
        '{"artifact_count":999,"artifacts":{"audited_brief":{"status":"valid"}}}',
        encoding="utf-8",
    )
    for relative_path in (
        "output/intermediate/claim_ledger.json",
        "output/intermediate/audit_report.json",
        "output/intermediate/auditor_quality_gate_report.json",
        "output/intermediate/finalize_report.json",
    ):
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"status":"pass"}', encoding="utf-8")
    assert _store_revision(workspace) == before
    assert _stage(workspace, "doctor") == doctor_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    assert not snapshot.claims
    assert not snapshot.claim_freezes
    assert not snapshot.gate_evaluations
    assert {
        item.artifact_id: item.current_revision
        for item in snapshot.artifacts
        if item.artifact_id
        in {"claim_ledger", "audit_report", "auditor_quality_gate_report"}
    } == {
        "audit_report": 0,
        "auditor_quality_gate_report": 0,
        "claim_ledger": 0,
    }


@pytest.mark.parametrize("filename", ["config.yaml"])
def test_initialize_replay_is_exact_and_conflict_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = CoreRunService(workspace, clock=CLOCK)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id="REQ-INIT-REPLAY",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    request = CoreRunInitializeRequest.model_validate(
        _bind_init_payload(payload), strict=True
    )
    first = service.initialize(request)
    assert first.status == "committed"
    revision = _store_revision(workspace)

    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write("\n# mutable input changed after initialization\n")

    def reject_workspace_reread(*_args, **_kwargs):
        raise AssertionError("initialize replay reread mutable workspace inputs")

    monkeypatch.setattr(
        "multi_agent_brief.core_run_v2.service.workspace_input_fingerprints",
        reject_workspace_reread,
    )
    replay = service.initialize(request)
    assert replay.status == "replayed"
    assert replay.receipt == first.receipt
    assert _store_revision(workspace) == revision

    changed = deepcopy(payload)
    changed["run_direction"]["brief_title"] = "Conflicting title"
    conflict = service.initialize(
        CoreRunInitializeRequest.model_validate(changed, strict=True)
    )
    assert conflict.status == "failed_uncommitted"
    assert conflict.error_code == "submission_replay_conflict"
    assert _store_revision(workspace) == revision




@pytest.mark.parametrize("filename", ["config.yaml"])
def test_secret_bearing_workspace_input_is_rejected_before_store_creation(
    tmp_path: Path,
    filename: str,
) -> None:
    workspace = _workspace(tmp_path)
    secret = "DO-NOT-PERSIST-THIS-SECRET"
    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write(f"\nprivate_provider:\n  api_key: {secret}\n")
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id=f"REQ-INIT-SECRET-{filename.split('.')[0].upper()}",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    result = CoreRunService(workspace, clock=CLOCK).initialize(
        CoreRunInitializeRequest.model_validate(
            _bind_init_payload(payload), strict=True
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "core_run_contract_mismatch",
    }
    assert secret not in str(result.to_dict())
    assert not (workspace / "briefloop.db").exists()


@pytest.mark.parametrize("filename", ["config.yaml"])
def test_workspace_input_byte_change_blocks_doctor_without_stage_effect(
    tmp_path: Path,
    filename: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    before = _store_revision(workspace)
    with (workspace / filename).open("a", encoding="utf-8") as stream:
        stream.write("\n# exact input fingerprint changed\n")
    result = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id=f"REQ-DOCTOR-CHANGED-{filename.split('.')[0].upper()}",
            run_id=RUN_ID,
            expected_store_revision=before,
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "doctor_check_failed",
    }
    assert _store_revision(workspace) == before
    assert _stage(workspace, "doctor").status == "ready"
    assert _stage(workspace, "source-discovery").status == "pending"


@pytest.mark.parametrize(
    ("failure_stage", "committed"),
    [("after_records", False), ("after_commit", True)],
)
def test_initialize_failure_cleans_revision_zero_or_exactly_replays_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    committed: bool,
) -> None:
    workspace = _workspace(tmp_path)
    service = CoreRunService(workspace, clock=CLOCK)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id=f"REQ-INIT-INJECT-{failure_stage.upper()}",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    request = CoreRunInitializeRequest.model_validate(
        _bind_init_payload(payload), strict=True
    )
    original_create = SQLiteControlStore.create

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise ControlStoreIntegrityError("injected_core_run_failure")

    def create_with_failure(path, **kwargs):
        return original_create(path, **kwargs, _failure_hook=fail)

    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteControlStore,
            "create",
            staticmethod(create_with_failure),
        )
        result = service.initialize(request)

    expected_result = (
        {
            "status": "commit_outcome_unknown",
            "error_code": "commit_outcome_unknown",
        }
        if committed
        else {
            "status": "failed_uncommitted",
            "error_code": "control_store_integrity_invalid",
        }
    )
    assert result.to_dict() == expected_result
    database = workspace / "briefloop.db"
    if not committed:
        assert not database.exists()
        assert not database.with_name("briefloop.db.blobs").exists()
        return

    assert _store_revision(workspace) == 1
    replay = service.initialize(request)
    assert replay.status == "replayed"
    assert replay.receipt is not None
    assert _store_revision(workspace) == 1


def test_initialize_unknown_never_deletes_store_when_cleanup_reopen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    service = CoreRunService(workspace, clock=CLOCK)
    payload = deepcopy(CoreRunInitializeRequest.minimal_example)
    payload.update(
        request_id="REQ-INIT-UNKNOWN-PRESERVE",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(
            workspace,
            "config.yaml",
        ).sha256,
        sources_config_sha256=read_workspace_file(
            workspace,
            "sources.yaml",
        ).sha256,
    )
    request = CoreRunInitializeRequest.model_validate(
        _bind_init_payload(payload), strict=True
    )
    original_create = SQLiteControlStore.create

    def fail_after_commit(stage: str) -> None:
        if stage == "after_commit":
            raise ControlStoreIntegrityError("injected_after_commit_failure")

    def create_with_failure(path, **kwargs):
        return original_create(path, **kwargs, _failure_hook=fail_after_commit)

    def fail_cleanup_reopen(*_args, **_kwargs):
        raise ControlStoreIntegrityError("injected_cleanup_reopen_failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteControlStore,
            "create",
            staticmethod(create_with_failure),
        )
        patch.setattr(
            SQLiteControlStore,
            "open",
            staticmethod(fail_cleanup_reopen),
        )
        unknown = service.initialize(request)

    assert unknown.to_dict() == {
        "status": "commit_outcome_unknown",
        "error_code": "commit_outcome_unknown",
    }
    database = workspace / "briefloop.db"
    blob_root = workspace / "briefloop.db.blobs"
    assert database.is_file()
    assert blob_root.is_dir()
    with SQLiteControlStore.open(database) as store:
        assert store.current_revision == 1
        receipt = store.load_transaction_receipt(RUN_ID, request.request_id)
        assert receipt is not None

    replay = service.initialize(request)
    assert replay.status == "replayed"
    assert replay.receipt == receipt
    assert _store_revision(workspace) == 1






@pytest.mark.parametrize("only", ["source_candidates"])
def test_source_discovery_requires_candidates_and_eligible_source(
    tmp_path: Path,
    only: str,
) -> None:
    _require_supported_working_projection()
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    checked = service.doctor_check(
        _record(
            IntegrityCheckRequest,
            request_id="REQ-DOCTOR-SOURCE-BINDING",
            run_id=RUN_ID,
            expected_store_revision=_store_revision(workspace),
        )
    )
    assert checked.status == "committed", checked.to_dict()

    expected_artifacts: list[dict[str, object]] = []
    if only == "source_candidates":
        planner = _start_invocation(
            service,
            workspace,
            request_id="REQ-INVOKE-PLANNER-ONLY",
            stage_id="source-discovery",
            role_id="source-planner",
        )
        candidates = workspace / "scratch" / planner / "source_candidates.yaml"
        candidates.parent.mkdir(parents=True, exist_ok=True)
        candidates.write_text("sources:\n  - SRC-001\n", encoding="utf-8")
        accepted = ArtifactAcceptanceService(
            workspace,
            clock=CLOCK,
        ).submit_owned_artifact(
            _record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-ARTIFACT-SOURCES-ONLY",
                run_id=RUN_ID,
                artifact_id="source_candidates",
                invocation_id=planner,
                producer_tool_id=None,
                input_path=candidates.relative_to(workspace).as_posix(),
                expected_store_revision=_store_revision(workspace),
                expected_artifact_revision=0,
                expected_parent_artifact=None,
            )
        )
        assert accepted.status == "committed", accepted.to_dict()
        expected_artifacts.append({"artifact_id": "source_candidates", "revision": 1})
    else:
        planner = _start_invocation(
            service,
            workspace,
            request_id="REQ-INVOKE-PLANNER-FOR-SOURCE",
            stage_id="source-discovery",
            role_id="source-planner",
        )
        candidates = workspace / "scratch" / planner / "source_candidates.yaml"
        candidates.parent.mkdir(parents=True, exist_ok=True)
        candidates.write_text("sources:\n  - SRC-001\n", encoding="utf-8")
        accepted = ArtifactAcceptanceService(
            workspace,
            clock=CLOCK,
        ).submit_owned_artifact(
            _record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-ARTIFACT-SOURCES-FOR-SOURCE",
                run_id=RUN_ID,
                artifact_id="source_candidates",
                invocation_id=planner,
                producer_tool_id=None,
                input_path=candidates.relative_to(workspace).as_posix(),
                expected_store_revision=_store_revision(workspace),
                expected_artifact_revision=0,
                expected_parent_artifact=None,
            )
        )
        assert accepted.status == "committed", accepted.to_dict()
        provider = _start_invocation(
            service,
            workspace,
            request_id="REQ-INVOKE-PROVIDER-ONLY",
            stage_id="source-discovery",
            role_id="source-provider",
        )
        _submit_source(workspace, provider)
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            source = store.load_snapshot(RUN_ID).sources[0]
        expected_artifacts.append(
            {
                "artifact_id": source.content_artifact_id,
                "revision": source.content_artifact_revision,
            }
        )

    before = _store_revision(workspace)
    stage = _stage(workspace, "source-discovery")
    result = service.complete_stage(
        _record(
            StageCompleteRequest,
            request_id=f"REQ-COMPLETE-SOURCE-{only.upper()}",
            run_id=RUN_ID,
            stage_id="source-discovery",
            reason="one-sided source binding cannot complete",
            expected_stage_revision=stage.revision,
            expected_store_revision=before,
            expected_artifact_revisions=expected_artifacts,
            expected_gate_evaluation_ids=[],
        )
    )

    assert result.status == "failed_uncommitted"
    assert result.error_code == "stage_artifact_binding_invalid"
    assert _store_revision(workspace) == before
    assert _stage(workspace, "source-discovery") == stage


@pytest.mark.parametrize(
    ("failure_stage", "committed"),
    [("after_records", False), ("after_commit", True)],
)
def test_doctor_commit_failure_is_typed_and_postcommit_exactly_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    committed: bool,
) -> None:
    workspace = _workspace(tmp_path)
    service = _initialize(workspace)
    before = _store_revision(workspace)
    request = _record(
        IntegrityCheckRequest,
        request_id=f"REQ-DOCTOR-INJECT-{failure_stage.upper()}",
        run_id=RUN_ID,
        expected_store_revision=before,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            service,
            "_open_store",
            _store_opener_with_failure(workspace, failure_stage),
        )
        result = service.doctor_check(request)

    expected_result = (
        {
            "status": "commit_outcome_unknown",
            "error_code": "commit_outcome_unknown",
        }
        if committed
        else {
            "status": "failed_uncommitted",
            "error_code": "control_store_integrity_invalid",
        }
    )
    assert result.to_dict() == expected_result
    if not committed:
        assert _store_revision(workspace) == before
        assert _stage(workspace, "doctor").status == "ready"
        assert _stage(workspace, "source-discovery").status == "pending"
        return

    assert _store_revision(workspace) == before + 1
    assert _stage(workspace, "doctor").status == "complete"
    assert _stage(workspace, "source-discovery").status == "ready"
    replay = service.doctor_check(request)
    assert replay.status == "replayed"
    assert replay.receipt is not None
    assert _store_revision(workspace) == before + 1










def test_commit_outcome_unknown_core_result_is_strictly_value_free() -> None:
    result = CoreRunResult(
        status="commit_outcome_unknown",
        error_code="commit_outcome_unknown",
    )
    assert result.exit_code == 1
    assert result.to_dict() == {
        "status": "commit_outcome_unknown",
        "error_code": "commit_outcome_unknown",
    }
    with pytest.raises(ValueError, match="invalid core-run result shape"):
        CoreRunResult(
            status="commit_outcome_unknown",
            error_code="commit_outcome_unknown",
            primary_record_id="must-not-leak",
        )




def test_artifact_commit_failure_leaves_only_unbound_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    core = _advance_to_analyst_ready(workspace)
    invocation_id = _start_invocation(
        core,
        workspace,
        request_id="REQ-INVOKE-ANALYST-ROLLBACK",
        stage_id="analyst",
        role_id="analyst",
    )
    scratch = workspace / "scratch" / invocation_id / "analyst_draft_snapshot.md"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    content = b"# Unbound draft\n\nThis checkout must not become run truth.\n"
    scratch.write_bytes(content)
    before = _store_revision(workspace)
    service = ArtifactAcceptanceService(workspace, clock=CLOCK)
    monkeypatch.setattr(
        service,
        "_open_store",
        _store_opener_with_failure(workspace, "after_records"),
    )
    result = service.submit_owned_artifact(
        _record(
            OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-ROLLBACK",
            run_id=RUN_ID,
            artifact_id="analyst_draft_snapshot",
            invocation_id=invocation_id,
            producer_tool_id="analyst-snapshot-v2",
            input_path=scratch.relative_to(workspace).as_posix(),
            expected_store_revision=before,
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )

    assert result.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "control_store_integrity_invalid",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    artifact = next(
        item
        for item in snapshot.artifacts
        if item.artifact_id == "analyst_draft_snapshot"
    )
    assert snapshot.store_revision == before
    assert artifact.current_revision == 0
    assert not any(
        item.accepted_transaction_id == "REQ-ARTIFACT-ROLLBACK"
        for item in snapshot.owned_artifact_submissions
    )
    assert not (workspace / artifact.path).exists()
    assert scratch.read_bytes() == content
    analyst = _stage(workspace, "analyst")
    blocked = core.complete_stage(
        _record(
            StageCompleteRequest,
            request_id="REQ-COMPLETE-UNBOUND-ARTIFACT",
            run_id=RUN_ID,
            stage_id="analyst",
            reason="unbound bytes cannot satisfy the stage",
            expected_stage_revision=analyst.revision,
            expected_store_revision=before,
            expected_artifact_revisions=[
                {"artifact_id": "analyst_draft_snapshot", "revision": 1}
            ],
            expected_gate_evaluation_ids=[],
        )
    )
    assert blocked.status == "failed_uncommitted"
    assert blocked.error_code == "stage_artifact_binding_invalid"
    assert _store_revision(workspace) == before




def test_claim_freeze_requires_current_drafts_revision_and_exactly_replays(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    core = _advance_to_claim_ledger_ready(workspace)

    def submit_claim_drafts(*, revision: int) -> None:
        invocation_id = _start_invocation(
            core,
            workspace,
            request_id=f"REQ-INVOKE-CLAIMS-REV{revision}",
            stage_id="claim-ledger",
            role_id="claim-ledger",
        )
        _submit_proposal(
            workspace,
            lane="claim-drafts",
            invocation_id=invocation_id,
            request_id=f"REQ-CLAIM-DRAFTS-REV{revision}",
            artifact_id="claim_drafts",
            expected_artifact_revision=revision - 1,
            payload={
                "schema_version": "briefloop.claim_drafts_proposal.v2",
                "proposal_id": f"PROP-CLAIM-DRAFTS-REV{revision}",
                "run_id": RUN_ID,
                "screened_candidates_proposal_id": "PROP-SCREENED-001",
                "created_at": NOW,
                "drafts": [
                    {
                        "draft_id": f"DRAFT-REV{revision}",
                        "statement": ("ExampleCo opened a public pilot facility."),
                        "evidence_text": (
                            "ExampleCo opened a public pilot facility on 2026-07-14."
                        ),
                        "source_ids": ["SRC-001"],
                        "claim_type": "fact",
                    }
                ],
            },
        )

    submit_claim_drafts(revision=1)
    # Single-shot claim-ledger stage: after the drafts proposal the next
    # action is the claim freeze, so a second drafts delegate (which would
    # produce revision 2) is rejected fail-closed with zero writes.
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_second = store.load_snapshot(RUN_ID)
    second = core.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-CLAIMS-REV2",
            run_id=RUN_ID,
            stage_id="claim-ledger",
            role_id="claim-ledger",
            runtime="operator",
            expected_store_revision=before_second.store_revision,
        )
    )
    assert second.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "invocation_owner_mismatch",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID) == before_second

    def tracked_file_state() -> dict[str, tuple[bytes, int]]:
        state: dict[str, tuple[bytes, int]] = {}
        for root_name in ("briefloop.db.blobs", "output"):
            root = workspace / root_name
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    state[path.relative_to(workspace).as_posix()] = (
                        path.read_bytes(),
                        path.stat().st_mtime_ns,
                    )
        return state

    service = ClaimFreezeService(workspace, clock=CLOCK)
    before_files = tracked_file_state()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_stale = store.load_snapshot(RUN_ID)
    stale = service.freeze(
        _record(
            ClaimFreezeRequest,
            request_id="REQ-FREEZE-STALE-DRAFTS",
            run_id=RUN_ID,
            claim_drafts_proposal_id="PROP-CLAIM-DRAFTS-REV1",
            expected_claim_drafts_artifact={
                "artifact_id": "claim_drafts",
                "revision": 2,
            },
            expected_store_revision=before_stale.store_revision,
            expected_ledger_revision=0,
        )
    )
    assert stale.to_dict() == {
        "status": "failed_uncommitted",
        "error_code": "artifact_revision_conflict",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID) == before_stale
    assert tracked_file_state() == before_files

    current_request = _record(
        ClaimFreezeRequest,
        request_id="REQ-FREEZE-CURRENT-DRAFTS",
        run_id=RUN_ID,
        claim_drafts_proposal_id="PROP-CLAIM-DRAFTS-REV1",
        expected_claim_drafts_artifact={
            "artifact_id": "claim_drafts",
            "revision": 1,
        },
        expected_store_revision=before_stale.store_revision,
        expected_ledger_revision=0,
    )
    frozen = service.freeze(current_request)
    assert frozen.status == "committed", frozen.to_dict()
    _complete_stage(
        core,
        workspace,
        stage_id="claim-ledger",
        artifacts=[("claim_drafts", 1), ("claim_ledger", 1)],
    )
    assert _stage(workspace, "analyst").status == "ready"

    replay = service.freeze(current_request)
    assert replay.status == "replayed"
    assert replay.receipt == frozen.receipt
    assert replay.primary_record_id == frozen.primary_record_id

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
        drafts_artifact = next(
            item
            for item in verified.snapshot.artifacts
            if item.artifact_id == "claim_drafts"
        )
        stale_snapshot = replace(
            verified.snapshot,
            artifacts=tuple(
                item.model_copy(update={"current_revision": 0})
                if item.artifact_id == "claim_drafts"
                else item
                for item in verified.snapshot.artifacts
            ),
        )
        assert drafts_artifact.current_revision == 1
        with pytest.raises(
            CoreRunError,
            match="control_store_integrity_invalid",
        ):
            CoreRunDomainVerifier._verify_claim_chain(
                store,
                stale_snapshot,
                verified.binding,
            )














@pytest.mark.parametrize("mutation", ["edit"])
def test_protected_checkout_mutation_records_contamination_and_blocks_effect(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace = _workspace(tmp_path)
    service = _advance_to_scout_ready(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    candidate_record = next(
        item for item in snapshot.artifacts if item.artifact_id == "source_candidates"
    )
    candidate_path = workspace / candidate_record.path
    if mutation == "edit":
        candidate_path.write_text("sources:\n  - MUTATED\n", encoding="utf-8")
    else:
        candidate_path.unlink()
    before = snapshot.store_revision
    request = _record(
        InvocationStartRequest,
        request_id="REQ-INVOKE-CONTAMINATED",
        run_id=RUN_ID,
        stage_id="scout",
        role_id="scout",
        runtime="operator",
        expected_store_revision=before,
    )
    result = service.start_invocation(request)
    assert result.status == "blocked"
    assert result.error_code == "frozen_artifact_contaminated"
    assert result.receipt is not None
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        after = store.load_snapshot(RUN_ID)
    assert after.store_revision == before + 1
    assert after.run_integrity_records[-1].status == "contaminated"
    assert after.run_integrity_records[-1].affected_artifact_id == "source_candidates"
    assert not any(
        item.invocation_id == result.primary_record_id for item in after.invocations
    )
    assert _stage(workspace, "scout").status == "ready"
    contamination_event = next(
        item for item in after.events if item.event_type == "run_integrity_contaminated"
    )
    assert contamination_event.core_run_binding is not None
    base_request_fingerprint = canonical_fingerprint(
        request.model_dump(mode="json", exclude_unset=False)
    )
    contamination_record = after.run_integrity_records[-1]
    assert contamination_record.request_fingerprint == base_request_fingerprint
    observation_fingerprint = canonical_fingerprint(
        {
            "run_id": contamination_record.run_id,
            "artifact_id": contamination_record.affected_artifact_id,
            "artifact_revision": contamination_record.affected_artifact_revision,
            "expected_workspace_path": (contamination_record.expected_workspace_path),
            "expected_sha256": contamination_record.expected_sha256,
            "observed_entry_kind": contamination_record.observed_entry_kind,
            "observed_sha256": contamination_record.observed_sha256,
        }
    )
    assert contamination_event.core_run_binding.request_fingerprint == (
        canonical_fingerprint(
            {
                "effect_kind": "integrity_contamination",
                "base_request_fingerprint": base_request_fingerprint,
                "observation_fingerprint": observation_fingerprint,
            }
        )
    )

    exact_replay = service.start_invocation(request)
    assert exact_replay.status == "blocked"
    assert exact_replay.receipt == result.receipt
    assert exact_replay.primary_record_id == result.primary_record_id
    assert _store_revision(workspace) == after.store_revision

    repeated = service.start_invocation(
        _record(
            InvocationStartRequest,
            request_id="REQ-INVOKE-CONTAMINATED-AGAIN",
            run_id=RUN_ID,
            stage_id="scout",
            role_id="scout",
            runtime="operator",
            expected_store_revision=after.store_revision,
        )
    )
    # Fail-closed precedence: with the next action bound to contamination
    # repair, the reservation guard rejects a new delegate invocation first.
    assert repeated.status == "failed_uncommitted"
    assert repeated.error_code == "invocation_owner_mismatch"
    assert _store_revision(workspace) == after.store_revision

    # The integrity guard itself still fires on a conformant terminal effect,
    # and the persisted contamination record/event proven above stay intact.
    terminal = CoreRunTerminalService(workspace, clock=CLOCK)
    approval = terminal.record_internal_approval(
        InternalApprovalRequest.model_validate(
            {
                "schema_version": InternalApprovalRequest.schema_id,
                "request_id": "REQ-APPROVAL-CONTAMINATED",
                "run_id": RUN_ID,
                "package_id": "PACKAGE-UNWRITTEN",
                "approval_id": "APPROVAL-CONTAMINATED",
                "mode": "internal_management_review",
                "role": "content_owner",
                "decision": "approve",
                "reason": "probe the integrity guard on a contaminated run",
                "actor_id": "human-reviewer",
                "expected_store_revision": after.store_revision,
            },
            strict=True,
        )
    )
    assert approval.status == "failed_uncommitted"
    assert approval.error_code == "core_run_integrity_blocked"
    assert _store_revision(workspace) == after.store_revision




def test_claim_freeze_is_byte_deterministic_for_equivalent_inputs(
    tmp_path: Path,
) -> None:
    workspaces = [tmp_path / "left", tmp_path / "right"]
    ledgers: list[bytes] = []
    claim_payloads: list[list[dict[str, object]]] = []
    for root in workspaces:
        workspace = _workspace(root)
        _advance_to_analyst_ready(workspace)
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            snapshot = store.load_snapshot(RUN_ID)
            freeze = snapshot.claim_freezes[0]
            ledgers.append(
                store.read_artifact_revision_bytes(
                    RUN_ID,
                    freeze.ledger_artifact.artifact_id,
                    freeze.ledger_artifact.revision,
                )
            )
            claim_payloads.append(
                [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in snapshot.claims
                ]
            )
    assert ledgers[0] == ledgers[1]
    assert claim_payloads[0] == claim_payloads[1]



def test_default_core_spine_reaches_finalize_ready(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_finalize_ready(workspace)

    assert _stage(workspace, "scout").status == "complete"
    assert _stage(workspace, "screener").status == "complete"
    assert _stage(workspace, "claim-ledger").status == "complete"

    assert _stage(workspace, "auditor").status == "complete"
    assert _stage(workspace, "finalize").status == "ready"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        completed = store.load_snapshot(RUN_ID)
        audit_revision = next(
            item
            for item in completed.artifact_revisions
            if item.artifact_id == "audit_report" and item.revision == 1
        )
        audit_bytes = store.read_artifact_revision_bytes(
            RUN_ID,
            audit_revision.artifact_id,
            audit_revision.revision,
        )
    assert not completed.approvals
    assert not completed.deliveries
    assert not any(
        item.stage_id == "finalize" and item.transition_kind == "complete"
        for item in completed.stage_transitions
    )

    late_promotion = ArtifactAcceptanceService(
        workspace,
        clock=CLOCK,
    ).promote_audit_proposal(
        _record(
            AuditPromotionRequest,
            request_id="REQ-AUDIT-PROMOTE-LATE",
            run_id=RUN_ID,
            audit_proposal_id="PROP-AUDIT-001",
            expected_target_artifact={
                "artifact_id": "audited_brief",
                "revision": 1,
            },
            expected_audit_report_revision=1,
            expected_store_revision=completed.store_revision,
        )
    )
    assert late_promotion.status == "failed_uncommitted"
    assert late_promotion.error_code == "stage_not_current"
    assert _store_revision(workspace) == completed.store_revision
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert (
            store.read_artifact_revision_bytes(
                RUN_ID,
                audit_revision.artifact_id,
                audit_revision.revision,
            )
            == audit_bytes
        )


def test_historical_snapshot_prefix_excludes_future_rows_and_replays_old_request(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_claim_ledger_ready(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        initialization = history.transactions[0]
        prefix = history.snapshot_at_revision(RUN_ID, initialization.committed_revision)
        assert prefix.store_revision == 1
        assert not prefix.invocations
        assert not prefix.deliveries
        assert "candidate_claims" not in {item.artifact_id for item in prefix.artifacts}
        assert all(
            item.accepted_transaction_id == initialization.transaction_id
            for item in prefix.run_contract_bindings
        )
        CoreRunDomainVerifier().verify_history(history)
        binding = prefix.run_contract_bindings[0]
        replay = resolve_core_replay(
            store,
            run_id=RUN_ID,
            request_id=initialization.transaction_id,
            request_fingerprint=binding.request_fingerprint,
        )
        assert replay is not None
        assert replay.status == "replayed"
        assert replay.receipt == initialization
        with pytest.raises(CoreRunError, match="submission_replay_conflict"):
            resolve_core_replay(
                store,
                run_id=RUN_ID,
                request_id=initialization.transaction_id,
                request_fingerprint="f" * 64,
            )


def test_unowned_legacy_delivery_blocks_every_historical_prefix_and_replay(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _initialize(workspace)
    transaction_id = "TX-FUTURE-LEGACY-DELIVERY-002"
    event_id = "EVT-FUTURE-LEGACY-DELIVERY-002"
    approval_id = "APR-FUTURE-LEGACY-DELIVERY-002"
    with SQLiteControlStore.open(workspace / "briefloop.db", clock=CLOCK) as store:
        initialized = store.load_snapshot(RUN_ID)
        revision = initialized.artifact_revisions[0]
        unit = store.begin(
            RUN_ID,
            transaction_id,
            "legacy_delivery_fixture",
            initialized.store_revision,
        )
        unit.append_event(
            _record(
                EventEnvelope,
                event_id=event_id,
                run_id=RUN_ID,
                event_type="stage_status_changed",
                created_at=NOW,
                actor="cli",
                transaction_id=transaction_id,
                stage_id="finalize",
            )
        )
        unit.put_approval(
            _record(
                Approval,
                approval_id=approval_id,
                run_id=RUN_ID,
                mode="internal_management_review",
                role="content_owner",
                decision="approve",
                reason="Synthetic legacy delivery isolation fixture.",
                actor_id="human-test-operator",
                recorded_at=NOW,
                boundary=(
                    "internal_review_approval_records_only_not_public_release_authorization"
                ),
                event_id=event_id,
            )
        )
        unit.put_delivery(
            _record(
                Delivery,
                delivery_id="DEL-FUTURE-LEGACY-DELIVERY-002",
                run_id=RUN_ID,
                artifact_id=revision.artifact_id,
                artifact_revision=revision.revision,
                approval_id=approval_id,
                status="succeeded",
                target="local",
                channel="local-test",
                created_at=NOW,
                completed_at=NOW,
            )
        )
        unit.commit()

        history = store.load_history()
        initialization = history.transactions[0]
        prefix = history.snapshot_at_revision(RUN_ID, 1)
        assert not prefix.deliveries
        binding = prefix.run_contract_bindings[0]
        with pytest.raises(CoreRunError, match="historical_prefix_invalid"):
            CoreRunDomainVerifier().verify_history(history, through_revision=1)
        before_replay = store.current_revision
        with pytest.raises(CoreRunError, match="historical_prefix_invalid"):
            resolve_core_replay(
                store,
                run_id=RUN_ID,
                request_id=initialization.transaction_id,
                request_fingerprint=binding.request_fingerprint,
            )
        assert store.current_revision == before_replay
        with pytest.raises(CoreRunError) as error:
            CoreRunDomainVerifier().verify(store, RUN_ID)
        assert error.value.code == "historical_prefix_invalid"








def test_sealed_stage_has_no_active_invocation_even_when_started_pre_seal(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _advance_to_finalize_ready(workspace)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    target = next(
        item for item in snapshot.invocations if item.role_id == "source-planner"
    )
    verify_no_post_seal_records(snapshot)
    failed_preseal = replace(
        snapshot,
        invocations=tuple(
            item.model_copy(
                update={"status": "failed", "failure_reason": "synthetic_failure"}
            )
            if item.invocation_id == target.invocation_id
            else item
            for item in snapshot.invocations
        ),
    )
    verify_no_post_seal_records(failed_preseal)

    active_preseal = replace(
        snapshot,
        invocations=tuple(
            item.model_copy(
                update={
                    "status": "active",
                    "completed_at": None,
                    "failure_reason": None,
                }
            )
            if item.invocation_id == target.invocation_id
            else item
            for item in snapshot.invocations
        ),
    )
    with pytest.raises(CoreRunError, match="control_store_integrity_invalid"):
        verify_no_post_seal_records(active_preseal)








