"""Deterministic initialization and Stage orchestration for fresh-v2 runs."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from typing import Callable

import yaml

from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevision,
    CoreRunEventBinding,
    CoreRunInitializeRequest,
    EventEnvelope,
    IntegrityCheckRequest,
    Invocation,
    InvocationStartRequest,
    RunContractBinding,
    RunIdentity,
    RunIntegrityRecord,
    StageArtifactBinding,
    StageCompleteRequest,
    StageGateBinding,
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
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    ValidatedRuntimeContractPayloads,
    load_runtime_contract_payloads,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.sources.doctor import run_doctor

from .errors import CoreRunError, CoreRunResult, core_run_error_code
from .integrity import RunIntegrityService, read_workspace_file
from .policy import (
    CORE_ARTIFACT_IDS,
    DOCTOR_IMPLEMENTATION,
    DOCTOR_VERSION,
    INTERNAL_CONTRACT_ARTIFACT_IDS,
    REQUIRED_AUDITOR_GATES,
    STAGE_ROLES,
    blob_workspace_path,
    derived_id,
    run_contract_fingerprint,
    transaction_type_for,
)
from .verifier import (
    CoreRunDomainVerifier,
    VerifiedCoreRun,
    _audit_targets_revision,
    resolve_core_replay,
)


_Clock = Callable[[], datetime]


class CoreRunService:
    """Own the fresh-v2 current-run binding and Stage transition graph."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        clock: _Clock | None = None,
    ) -> None:
        self.workspace = _workspace_root(workspace)
        try:
            self.repo_workdir = resolve_repo_workdir(
                None,
                workspace=self.workspace,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CoreRunError("core_run_contract_mismatch") from exc
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._verifier = CoreRunDomainVerifier()
        self._integrity = RunIntegrityService(self.workspace, clock=self._clock)

    def initialize(self, request: CoreRunInitializeRequest) -> CoreRunResult:
        try:
            return self._initialize(request)
        except (CoreRunError, ControlStoreError) as exc:
            return CoreRunResult(
                status="failed_uncommitted",
                error_code=core_run_error_code(exc),
            )

    def start_invocation(self, request: InvocationStartRequest) -> CoreRunResult:
        try:
            return self._start_invocation(request)
        except (CoreRunError, ControlStoreError) as exc:
            return CoreRunResult(
                status="failed_uncommitted",
                error_code=core_run_error_code(exc),
            )

    def doctor_check(self, request: IntegrityCheckRequest) -> CoreRunResult:
        try:
            return self._doctor_check(request)
        except (CoreRunError, ControlStoreError) as exc:
            return CoreRunResult(
                status="failed_uncommitted",
                error_code=core_run_error_code(exc),
            )

    def complete_stage(self, request: StageCompleteRequest) -> CoreRunResult:
        try:
            return self._complete_stage(request)
        except (CoreRunError, ControlStoreError) as exc:
            return CoreRunResult(
                status="failed_uncommitted",
                error_code=core_run_error_code(exc),
            )

    def _initialize(self, request: CoreRunInitializeRequest) -> CoreRunResult:
        database = self.workspace / "briefloop.db"
        if (
            not database.exists()
            and not database.is_symlink()
            and _legacy_control_state_present(self.workspace)
        ):
            raise CoreRunError("unsupported_schema_version")
        contracts = self._load_contracts()
        stage_bytes = canonical_json_bytes(contracts.stage_specs)
        artifact_bytes = canonical_json_bytes(contracts.artifact_contracts)
        policy_bytes = canonical_json_bytes(contracts.policy_pack)
        stage_hash = sha256_hex(stage_bytes)
        artifact_hash = sha256_hex(artifact_bytes)
        policy_hash = sha256_hex(policy_bytes)
        fingerprint = run_contract_fingerprint(
            runtime=request.runtime,
            stage_specs_schema=str(contracts.stage_specs["schema_version"]),
            stage_specs_sha256=stage_hash,
            artifact_contracts_schema=str(
                contracts.artifact_contracts["schema_version"]
            ),
            artifact_contracts_sha256=artifact_hash,
            policy_pack_schema=str(contracts.policy_pack["schema_version"]),
            policy_pack_name=str(contracts.policy_pack["policy_pack"]["name"]),
            policy_pack_sha256=policy_hash,
            run_direction=request.run_direction.model_dump(
                mode="json",
                exclude_unset=False,
            ),
            workspace_config_sha256=request.workspace_config_sha256,
            sources_config_sha256=request.sources_config_sha256,
            role_topology=request.role_topology,
            gate_strictness=request.gate_strictness,
            input_governance_required=request.input_governance_required,
        )
        request_fingerprint = canonical_fingerprint(
            {
                "request": request.model_dump(mode="json", exclude_unset=False),
                "contract_fingerprint": fingerprint,
            }
        )
        if database.exists() or database.is_symlink():
            with self._open_store() as existing:
                replay = resolve_core_replay(
                    existing,
                    run_id=request.run_id,
                    request_id=request.request_id,
                    request_fingerprint=request_fingerprint,
                )
                if replay is not None:
                    return replay
            raise CoreRunError("core_run_head_mismatch")

        config_sha256, sources_sha256 = workspace_input_fingerprints(self.workspace)
        if (
            config_sha256 != request.workspace_config_sha256
            or sources_sha256 != request.sources_config_sha256
        ):
            raise CoreRunError("core_run_contract_mismatch")

        created = False
        store: SQLiteControlStore | None = None
        try:
            store = SQLiteControlStore.create(
                database,
                workspace_id=request.workspace_id,
                clock=self._clock,
            )
            created = True
            now = _now(self._clock)
            event_id = derived_id("EVT-INIT", request.request_id, request_fingerprint)
            run = RunIdentity.model_validate(
                {
                    "schema_version": RunIdentity.schema_id,
                    "run_id": request.run_id,
                    "workspace_id": request.workspace_id,
                    "runtime": request.runtime,
                    "created_at": now,
                },
                strict=True,
            )
            head = WorkspaceRunHead.model_validate(
                {
                    "schema_version": WorkspaceRunHead.schema_id,
                    "workspace_id": request.workspace_id,
                    "current_run_id": request.run_id,
                    "updated_at": now,
                },
                strict=True,
            )
            contract_artifacts: list[tuple[ArtifactRecord, ArtifactRevision, bytes]] = []
            for artifact_id, payload in zip(
                INTERNAL_CONTRACT_ARTIFACT_IDS,
                (stage_bytes, artifact_bytes, policy_bytes),
            ):
                contract_artifacts.append(
                    _artifact_pair(
                        run_id=request.run_id,
                        artifact_id=artifact_id,
                        revision=1,
                        path=blob_workspace_path(sha256_hex(payload)),
                        artifact_format="json",
                        content=payload,
                        producer_kind="control_tool",
                        producer_id="core-v2-initializer",
                        created_at=now,
                        required=True,
                    )
                    + (payload,)
                )
            binding = RunContractBinding.model_validate(
                {
                    "schema_version": RunContractBinding.schema_id,
                    "run_id": request.run_id,
                    "workspace_id": request.workspace_id,
                    "runtime": request.runtime,
                    "stage_specs_schema": contracts.stage_specs["schema_version"],
                    "stage_specs_artifact": {
                        "artifact_id": INTERNAL_CONTRACT_ARTIFACT_IDS[0],
                        "revision": 1,
                    },
                    "stage_specs_sha256": stage_hash,
                    "artifact_contracts_schema": contracts.artifact_contracts[
                        "schema_version"
                    ],
                    "artifact_contracts_artifact": {
                        "artifact_id": INTERNAL_CONTRACT_ARTIFACT_IDS[1],
                        "revision": 1,
                    },
                    "artifact_contracts_sha256": artifact_hash,
                    "policy_pack_schema": contracts.policy_pack["schema_version"],
                    "policy_pack_name": contracts.policy_pack["policy_pack"]["name"],
                    "policy_pack_artifact": {
                        "artifact_id": INTERNAL_CONTRACT_ARTIFACT_IDS[2],
                        "revision": 1,
                    },
                    "policy_pack_sha256": policy_hash,
                    "run_direction": request.run_direction.model_dump(
                        mode="json",
                        exclude_unset=False,
                    ),
                    "workspace_config_sha256": request.workspace_config_sha256,
                    "sources_config_sha256": request.sources_config_sha256,
                    "role_topology": request.role_topology,
                    "gate_strictness": request.gate_strictness,
                    "input_governance_required": request.input_governance_required,
                    "contract_fingerprint": fingerprint,
                    "created_at": now,
                    "initialization_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": request_fingerprint,
                },
                strict=True,
            )
            event = _core_event(
                event_id=event_id,
                run_id=request.run_id,
                event_type="run_initialized",
                transaction_id=request.request_id,
                stage_id="doctor",
                decision="continue",
                reason="fresh-v2 initialization",
                created_at=now,
                binding=CoreRunEventBinding(
                    request_id=request.request_id,
                    request_fingerprint=request_fingerprint,
                    effect_kind="initialize",
                    primary_record_id=request.run_id,
                    outcome="committed",
                ),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("initialize"),
                0,
            )
            unit.put_run(run)
            unit.put_workspace_run_head(head)
            for artifact, revision, payload in contract_artifacts:
                unit.put_artifact(artifact)
                unit.put_artifact_revision(revision, payload)
            unit.put_run_contract_binding(binding)
            artifact_contracts = {
                str(item["artifact_id"]): item for item in contracts.artifacts
            }
            for artifact_id in CORE_ARTIFACT_IDS:
                row = artifact_contracts[artifact_id]
                unit.put_artifact(
                    ArtifactRecord.model_validate(
                        {
                            "schema_version": ArtifactRecord.schema_id,
                            "run_id": request.run_id,
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
            for position, stage in enumerate(contracts.stages):
                stage_id = str(stage["stage_id"])
                status = "ready" if position == 0 else "pending"
                transition = _initial_transition(
                    request=request,
                    stage_id=stage_id,
                    status=status,
                    contract_fingerprint=fingerprint,
                    event_id=event_id,
                    now=now,
                    request_fingerprint=request_fingerprint,
                )
                unit.put_stage_state(
                    StageState.model_validate(
                        {
                            "schema_version": StageState.schema_id,
                            "run_id": request.run_id,
                            "stage_id": stage_id,
                            "status": status,
                            "revision": 0,
                            "updated_at": now,
                        },
                        strict=True,
                    )
                )
                unit.append_stage_transition(transition)
            unit.append_run_integrity_record(
                RunIntegrityRecord.model_validate(
                    {
                        "schema_version": RunIntegrityRecord.schema_id,
                        "run_id": request.run_id,
                        "integrity_revision": 1,
                        "status": "clean",
                        "accepted_transaction_id": request.request_id,
                        "request_fingerprint": request_fingerprint,
                    },
                    strict=True,
                )
            )
            unit.append_event(event)
            receipt = unit.commit()
            self._verifier.verify(store, request.run_id)
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=request.run_id,
            )
        except CoreRunError:
            raise
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        finally:
            if store is not None:
                store.close()
            if created and database.exists():
                # A revision-zero database is not a valid initialized run.
                try:
                    reopened = SQLiteControlStore.open(database)
                    try:
                        initialized = reopened.current_revision > 0
                    finally:
                        reopened.close()
                except Exception:
                    initialized = False
                if not initialized:
                    _remove_created_store(database)

    def _start_invocation(self, request: InvocationStartRequest) -> CoreRunResult:
        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with self._open_store() as store:
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            verified = self._verifier.verify(store, request.run_id)
            self._require_store_revision(verified, request.expected_store_revision)
            if request.runtime != verified.snapshot.run.runtime:
                raise CoreRunError("invocation_owner_mismatch")
            stage = _stage_state(verified, request.stage_id)
            if stage.status != "ready" or request.role_id not in self._roles_for(
                verified,
                request.stage_id,
            ):
                raise CoreRunError("invocation_owner_mismatch")
            blocked = self._integrity.require_clean(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
            )
            if blocked is not None:
                return blocked
            now = _now(self._clock)
            invocation_id = derived_id("INV", request.request_id, fingerprint)
            event_id = derived_id("EVT-INVOKE", request.request_id, fingerprint)
            invocation = Invocation.model_validate(
                {
                    "schema_version": Invocation.schema_id,
                    "invocation_id": invocation_id,
                    "run_id": request.run_id,
                    "role_id": request.role_id,
                    "runtime": request.runtime,
                    "status": "active",
                    "started_at": now,
                },
                strict=True,
            )
            event = _core_event(
                event_id=event_id,
                run_id=request.run_id,
                event_type="role_invocation_started",
                transaction_id=request.request_id,
                stage_id=request.stage_id,
                decision="continue",
                reason="role invocation started",
                created_at=now,
                binding=CoreRunEventBinding(
                    request_id=request.request_id,
                    request_fingerprint=fingerprint,
                    effect_kind="invocation_start",
                    primary_record_id=invocation_id,
                    outcome="committed",
                ),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("invocation_start"),
                request.expected_store_revision,
            )
            unit.put_invocation(invocation)
            unit.append_event(event)
            receipt = unit.commit()
            self._verifier.verify(store, request.run_id)
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=invocation_id,
            )

    def _doctor_check(self, request: IntegrityCheckRequest) -> CoreRunResult:
        contracts = self._load_contracts()
        contract_hashes = (
            sha256_hex(canonical_json_bytes(contracts.stage_specs)),
            sha256_hex(canonical_json_bytes(contracts.artifact_contracts)),
            sha256_hex(canonical_json_bytes(contracts.policy_pack)),
        )
        config_sha256, sources_sha256 = workspace_input_fingerprints(self.workspace)
        try:
            doctor_results = run_doctor(
                config_path=self.workspace / "config.yaml",
                workspace_dir=self.workspace,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CoreRunError("doctor_check_failed") from exc
        result_statuses = tuple(result.status for result in doctor_results)
        if not result_statuses or any(status == "ERROR" for status in result_statuses):
            raise CoreRunError("doctor_check_failed")
        result_fingerprint = canonical_fingerprint(
            {
                "implementation": DOCTOR_IMPLEMENTATION,
                "version": DOCTOR_VERSION,
                "result_statuses": result_statuses,
                "contract_hashes": contract_hashes,
                "workspace_config_sha256": config_sha256,
                "sources_config_sha256": sources_sha256,
            }
        )
        fingerprint = canonical_fingerprint(
            {
                "request": request.model_dump(mode="json", exclude_unset=False),
                "doctor_result_fingerprint": result_fingerprint,
            }
        )
        with self._open_store() as store:
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            verified = self._verifier.verify(store, request.run_id)
            self._require_store_revision(verified, request.expected_store_revision)
            binding = verified.binding
            if (
                config_sha256 != binding.workspace_config_sha256
                or sources_sha256 != binding.sources_config_sha256
                or contract_hashes
                != (
                    binding.stage_specs_sha256,
                    binding.artifact_contracts_sha256,
                    binding.policy_pack_sha256,
                )
            ):
                raise CoreRunError("doctor_check_failed")
            if _stage_state(verified, "doctor").status != "ready":
                raise CoreRunError("stage_not_current")
            return self._commit_transition_set(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
                completed_stage_id="doctor",
                reason="deterministic doctor passed",
                artifact_revisions=(),
                gate_evaluation_ids=(),
                doctor_result=(result_fingerprint, DOCTOR_VERSION),
            )

    def _complete_stage(self, request: StageCompleteRequest) -> CoreRunResult:
        if request.stage_id == "doctor":
            raise CoreRunError("stage_decision_not_supported")
        request_base = request.model_dump(mode="json", exclude_unset=False)
        with self._open_store() as store:
            verified = self._verifier.verify(store, request.run_id)
            (
                required_revisions,
                gate_ids,
                producer_invocation_id,
                producer_tool_id,
            ) = (
                self._completion_bindings(
                    store,
                    verified,
                    request.stage_id,
                )
            )
            fingerprint = canonical_fingerprint(
                {
                    "request": request_base,
                    "derived_artifacts": [
                        (item.artifact_id, item.revision, item.sha256, usage)
                        for item, usage in required_revisions
                    ],
                    "derived_gates": list(gate_ids),
                    "producer_invocation_id": producer_invocation_id,
                    "producer_tool_id": producer_tool_id,
                }
            )
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            self._require_store_revision(verified, request.expected_store_revision)
            state = _stage_state(verified, request.stage_id)
            if state.status != "ready" or state.revision != request.expected_stage_revision:
                raise CoreRunError("stage_not_current")
            expected_artifacts = {
                (item.artifact_id, item.revision)
                for item in request.expected_artifact_revisions
            }
            actual_artifacts = {
                (item.artifact_id, item.revision)
                for item, _usage in required_revisions
            }
            if expected_artifacts != actual_artifacts:
                raise CoreRunError("stage_artifact_binding_invalid")
            if set(request.expected_gate_evaluation_ids) != set(gate_ids):
                raise CoreRunError("stage_gate_binding_invalid")
            blocked = self._integrity.require_clean(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
                additional_revisions=(
                    item for item, _usage in required_revisions
                ),
            )
            if blocked is not None:
                return blocked
            return self._commit_transition_set(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
                completed_stage_id=request.stage_id,
                reason=request.reason,
                artifact_revisions=required_revisions,
                gate_evaluation_ids=gate_ids,
                producer_invocation_id=producer_invocation_id,
                producer_tool_id=producer_tool_id,
            )

    def _completion_bindings(
        self,
        store: SQLiteControlStore,
        verified: VerifiedCoreRun,
        stage_id: str,
    ) -> tuple[
        tuple[tuple[ArtifactRevision, str], ...],
        tuple[str, ...],
        str | None,
        str | None,
    ]:
        snapshot = verified.snapshot
        artifacts = {item.artifact_id: item for item in snapshot.artifacts}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        selected: list[tuple[ArtifactRevision, str]] = []
        producer_invocation_id: str | None = None
        producer_tool_id: str | None = None
        invocations = {
            item.invocation_id: item for item in snapshot.invocations
        }

        def require_invocation(
            invocation_id: str,
            *,
            role_id: str,
        ) -> str:
            invocation = invocations.get(invocation_id)
            if (
                invocation is None
                or invocation.status != "completed"
                or invocation.role_id != role_id
                or invocation.runtime != snapshot.run.runtime
            ):
                raise CoreRunError("stage_artifact_binding_invalid")
            return invocation_id

        def require_proposal(
            kind: str,
            *,
            owner_stage_id: str,
            owner_role_id: str,
        ):
            proposal = _proposal(snapshot, kind)
            if (
                proposal.owner_stage_id != owner_stage_id
                or proposal.owner_role_id != owner_role_id
            ):
                raise CoreRunError("stage_artifact_binding_invalid")
            require_invocation(
                proposal.invocation_id,
                role_id=owner_role_id,
            )
            return proposal

        def require_submission(
            revision: ArtifactRevision,
            *,
            owner_stage_id: str,
            owner_role_id: str,
        ):
            submissions = [
                item
                for item in snapshot.owned_artifact_submissions
                if item.artifact_id == revision.artifact_id
                and item.artifact_revision == revision.revision
            ]
            if (
                len(submissions) != 1
                or submissions[0].owner_stage_id != owner_stage_id
                or submissions[0].owner_role_id != owner_role_id
            ):
                raise CoreRunError("stage_artifact_binding_invalid")
            submission = submissions[0]
            if submission.invocation_id is not None:
                require_invocation(
                    submission.invocation_id,
                    role_id=owner_role_id,
                )
            return submission

        def require_artifact(
            artifact_id: str,
            usage: str,
        ) -> ArtifactRevision:
            artifact = artifacts.get(artifact_id)
            if artifact is None or artifact.current_revision <= 0:
                raise CoreRunError("stage_artifact_binding_invalid")
            revision = revisions.get((artifact_id, artifact.current_revision))
            if revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            selected.append((revision, usage))
            return revision

        gate_ids: tuple[str, ...] = ()
        if stage_id == "source-discovery":
            candidates = require_artifact("source_candidates", "produced")
            submission = require_submission(
                candidates,
                owner_stage_id="source-discovery",
                owner_role_id="source-planner",
            )
            producer_invocation_id = submission.invocation_id
            eligible_sources = sorted(
                (item for item in snapshot.sources if item.claims_eligible),
                key=lambda item: item.source_id,
            )
            if not eligible_sources:
                raise CoreRunError("stage_artifact_binding_invalid")
            for source in eligible_sources:
                revision = revisions.get(
                    (
                        source.content_artifact_id,
                        source.content_artifact_revision,
                    )
                )
                if revision is None or revision.sha256 != source.content_sha256:
                    raise CoreRunError("control_store_integrity_invalid")
                selected.append((revision, "consumed"))
        elif stage_id == "input-governance":
            if verified.binding.input_governance_required:
                classification = require_artifact(
                    "input_classification",
                    "produced",
                )
                submission = require_submission(
                    classification,
                    owner_stage_id="input-governance",
                    owner_role_id="python_tool",
                )
                if submission.producer_tool_id != "input-governance-v2":
                    raise CoreRunError("stage_artifact_binding_invalid")
                producer_tool_id = submission.producer_tool_id
        elif stage_id == "scout":
            candidate = require_proposal(
                "candidate",
                owner_stage_id="scout",
                owner_role_id="scout",
            )
            producer_invocation_id = candidate.invocation_id
            selected.append(
                (
                    revisions[(candidate.artifact_id, candidate.artifact_revision)],
                    "produced",
                )
            )
            if verified.binding.role_topology in {"default", "human_assisted"}:
                screened = require_proposal(
                    "screened",
                    owner_stage_id="scout",
                    owner_role_id="scout",
                )
                selected.append(
                    (
                        revisions[(screened.artifact_id, screened.artifact_revision)],
                        "topology_required",
                    )
                )
        elif stage_id == "screener":
            if verified.binding.role_topology != "strict":
                raise CoreRunError("stage_decision_not_supported")
            screened = require_proposal(
                "screened",
                owner_stage_id="screener",
                owner_role_id="screener",
            )
            candidate = require_proposal(
                "candidate",
                owner_stage_id="scout",
                owner_role_id="scout",
            )
            producer_invocation_id = screened.invocation_id
            selected.extend(
                (
                    (
                        revisions[(screened.artifact_id, screened.artifact_revision)],
                        "produced",
                    ),
                    (
                        revisions[(candidate.artifact_id, candidate.artifact_revision)],
                        "consumed",
                    ),
                )
            )
        elif stage_id == "claim-ledger":
            if len(snapshot.claim_freezes) != 1:
                raise CoreRunError("claim_lineage_invalid")
            freeze = snapshot.claim_freezes[0]
            drafts = require_proposal(
                "claim_drafts",
                owner_stage_id="claim-ledger",
                owner_role_id="claim-ledger",
            )
            if drafts.proposal_id != freeze.claim_drafts_proposal_id:
                raise CoreRunError("claim_lineage_invalid")
            producer_invocation_id = drafts.invocation_id
            selected.extend(
                (
                    (
                        revisions[
                        (
                            freeze.claim_drafts_artifact.artifact_id,
                            freeze.claim_drafts_artifact.revision,
                        )
                        ],
                        "consumed",
                    ),
                    (
                        revisions[
                        (
                            freeze.ledger_artifact.artifact_id,
                            freeze.ledger_artifact.revision,
                        )
                        ],
                        "produced",
                    ),
                )
            )
        elif stage_id == "analyst":
            if verified.binding.role_topology == "human_assisted" and artifacts[
                "audited_brief"
            ].current_revision:
                brief = require_artifact("audited_brief", "topology_required")
                submission = require_submission(
                    brief,
                    owner_stage_id="analyst",
                    owner_role_id="writer",
                )
                producer_invocation_id = submission.invocation_id
            else:
                analyst = require_artifact("analyst_draft_snapshot", "produced")
                submission = require_submission(
                    analyst,
                    owner_stage_id="analyst",
                    owner_role_id="analyst",
                )
                producer_invocation_id = submission.invocation_id
        elif stage_id == "editor":
            brief = require_artifact("audited_brief", "produced")
            submission = require_submission(
                brief,
                owner_stage_id="editor",
                owner_role_id="editor",
            )
            producer_invocation_id = submission.invocation_id
            snapshot_revision = require_artifact(
                "analyst_draft_snapshot",
                "consumed",
            )
            submissions = [
                item
                for item in snapshot.owned_artifact_submissions
                if item.artifact_id == brief.artifact_id
                and item.artifact_revision == brief.revision
            ]
            if (
                len(submissions) != 1
                or submissions[0].parent_artifact is None
                or submissions[0].parent_artifact.artifact_id
                != snapshot_revision.artifact_id
                or submissions[0].parent_artifact.revision
                != snapshot_revision.revision
            ):
                raise CoreRunError("stage_artifact_binding_invalid")
        elif stage_id == "auditor":
            ledger = require_artifact("claim_ledger", "consumed")
            brief = require_artifact("audited_brief", "consumed")
            report = require_artifact("audit_report", "produced")
            gate_report = require_artifact(
                "auditor_quality_gate_report",
                "produced",
            )
            audit_submissions = [
                item
                for item in snapshot.owned_artifact_submissions
                if item.artifact_id == report.artifact_id
                and item.artifact_revision == report.revision
                and item.owner_stage_id == "auditor"
                and item.owner_role_id == "auditor"
                and item.source_proposal_id is not None
            ]
            if (
                len(audit_submissions) != 1
                or audit_submissions[0].invocation_id is None
            ):
                raise CoreRunError("stage_artifact_binding_invalid")
            producer_invocation_id = require_invocation(
                audit_submissions[0].invocation_id,
                role_id="auditor",
            )
            if artifacts["analyst_draft_snapshot"].current_revision:
                require_artifact("analyst_draft_snapshot", "consumed")
            del ledger
            audit_payload = store.read_artifact_revision_bytes(
                report.run_id,
                report.artifact_id,
                report.revision,
            )
            from multi_agent_brief.contracts.v2 import AuditReportArtifact
            from multi_agent_brief.intake_v2.scratch import parse_json_object

            try:
                audit = AuditReportArtifact.model_validate(
                    parse_json_object(audit_payload),
                    strict=True,
                )
            except Exception as exc:
                raise CoreRunError("stage_artifact_binding_invalid") from exc
            if (
                not _audit_targets_revision(audit, brief)
                or audit.decision == "fail"
                or any(finding.severity == "error" for finding in audit.findings)
            ):
                raise CoreRunError("stage_artifact_binding_invalid")
            evaluations = {
                item.gate_id: item
                for item in snapshot.gate_evaluations
                if item.report_artifact.artifact_id == gate_report.artifact_id
                and item.report_artifact.revision == gate_report.revision
            }
            if set(REQUIRED_AUDITOR_GATES) - set(evaluations):
                raise CoreRunError("stage_gate_binding_invalid")
            required = [evaluations[gate_id] for gate_id in REQUIRED_AUDITOR_GATES]
            if any(
                item.status not in {"pass", "warning"} or item.blocking
                for item in required
            ):
                raise CoreRunError("stage_gate_binding_invalid")
            gate_ids = tuple(item.evaluation_id for item in required)
        else:
            raise CoreRunError("stage_decision_not_supported")
        deduped = {
            (item.artifact_id, item.revision): (item, usage)
            for item, usage in selected
        }
        return (
            tuple(deduped[key] for key in sorted(deduped)),
            gate_ids,
            producer_invocation_id,
            producer_tool_id,
        )

    def _commit_transition_set(
        self,
        store: SQLiteControlStore,
        verified: VerifiedCoreRun,
        *,
        request_id: str,
        request_fingerprint: str,
        expected_store_revision: int,
        completed_stage_id: str,
        reason: str,
        artifact_revisions: Iterable[tuple[ArtifactRevision, str]],
        gate_evaluation_ids: Iterable[str],
        doctor_result: tuple[str, str] | None = None,
        producer_invocation_id: str | None = None,
        producer_tool_id: str | None = None,
    ) -> CoreRunResult:
        now = _now(self._clock)
        stage_order = [str(item["stage_id"]) for item in verified.stages]
        states = {item.stage_id: item for item in verified.snapshot.stage_states}
        current = states[completed_stage_id]
        transition_ids: list[str] = []
        transitions: list[StageTransitionRecord] = []
        transition_artifacts: dict[
            str,
            tuple[tuple[ArtifactRevision, str], ...],
        ] = {}
        state_updates: list[StageState] = []
        events: list[EventEnvelope] = []

        def add_transition(
            stage_id: str,
            *,
            transition_kind: str,
            result_status: str,
            transition_reason: str,
            topology: str | None = None,
            satisfaction_source_kind: str = "stage",
            satisfied_by_id: str | None = None,
            primary: bool = False,
            transition_producer_invocation_id: str | None = None,
        ) -> StageTransitionRecord:
            prior = states[stage_id]
            transition_id = derived_id(
                "TRN",
                request_id,
                stage_id,
                str(prior.revision + 1),
                transition_kind,
            )
            event_id = derived_id("EVT-STAGE", transition_id, request_fingerprint)
            payload: dict[str, object] = {
                "schema_version": StageTransitionRecord.schema_id,
                "transition_id": transition_id,
                "run_id": verified.snapshot.run.run_id,
                "stage_id": stage_id,
                "transition_kind": transition_kind,
                "requested_decision": "continue",
                "prior_status": prior.status,
                "prior_revision": prior.revision,
                "result_status": result_status,
                "result_revision": prior.revision + 1,
                "reason": transition_reason,
                "run_contract_fingerprint": verified.binding.contract_fingerprint,
                "actor": "system",
                "created_at": now,
                "transition_event_id": event_id,
                "accepted_transaction_id": request_id,
                "request_fingerprint": request_fingerprint,
            }
            if topology is not None:
                payload.update(
                    topology=topology,
                    satisfaction_source_kind=satisfaction_source_kind,
                    satisfied_by_id=satisfied_by_id,
                )
            if stage_id == "doctor" and doctor_result is not None:
                payload.update(
                    producer_tool_id=DOCTOR_IMPLEMENTATION,
                    producer_result_status="pass",
                    producer_result_fingerprint=doctor_result[0],
                    producer_implementation=DOCTOR_IMPLEMENTATION,
                    producer_version=doctor_result[1],
                )
            elif transition_producer_invocation_id is not None:
                payload["producer_invocation_id"] = (
                    transition_producer_invocation_id
                )
            elif primary and producer_tool_id is not None:
                payload["producer_tool_id"] = producer_tool_id
            transition = StageTransitionRecord.model_validate(payload, strict=True)
            binding = (
                CoreRunEventBinding(
                    request_id=request_id,
                    request_fingerprint=request_fingerprint,
                    effect_kind="stage_transition",
                    primary_record_id=transition_id,
                    outcome="committed",
                )
                if primary
                else None
            )
            event_type = (
                "stage_satisfied_by_topology"
                if transition_kind == "satisfied_by_topology"
                else "stage_status_changed"
            )
            events.append(
                _core_event(
                    event_id=event_id,
                    run_id=verified.snapshot.run.run_id,
                    event_type=event_type,
                    transaction_id=request_id,
                    stage_id=stage_id,
                    decision="continue",
                    reason=transition_reason,
                    created_at=now,
                    binding=binding,
                )
            )
            transitions.append(transition)
            transition_ids.append(transition_id)
            updated = StageState.model_validate(
                {
                    "schema_version": StageState.schema_id,
                    "run_id": verified.snapshot.run.run_id,
                    "stage_id": stage_id,
                    "status": result_status,
                    "revision": prior.revision + 1,
                    "updated_at": now,
                },
                strict=True,
            )
            states[stage_id] = updated
            state_updates.append(updated)
            return transition

        completed = add_transition(
            completed_stage_id,
            transition_kind="complete",
            result_status="complete",
            transition_reason=reason,
            primary=True,
            transition_producer_invocation_id=producer_invocation_id,
        )
        revisions = tuple(artifact_revisions)
        transition_artifacts[completed.transition_id] = revisions
        next_index = stage_order.index(completed_stage_id) + 1
        if (
            completed_stage_id == "scout"
            and verified.binding.role_topology in {"default", "human_assisted"}
        ):
            topology_transition = add_transition(
                "screener",
                transition_kind="satisfied_by_topology",
                result_status="complete",
                transition_reason="screener satisfied by scout topology",
                topology=verified.binding.role_topology,
                satisfied_by_id="scout",
                transition_producer_invocation_id=producer_invocation_id,
            )
            by_id = {revision.artifact_id: revision for revision, _usage in revisions}
            transition_artifacts[topology_transition.transition_id] = (
                (by_id["candidate_claims"], "consumed"),
                (by_id["screened_candidates"], "produced"),
            )
            next_index = stage_order.index("screener") + 1
        if (
            completed_stage_id == "analyst"
            and verified.binding.role_topology == "human_assisted"
            and any(
                revision.artifact_id == "audited_brief"
                for revision, _usage in revisions
            )
        ):
            topology_transition = add_transition(
                "editor",
                transition_kind="satisfied_by_topology",
                result_status="complete",
                transition_reason="editor satisfied by human-assisted writer",
                topology="human_assisted",
                satisfaction_source_kind="role",
                satisfied_by_id="writer",
                transition_producer_invocation_id=producer_invocation_id,
            )
            audited_brief = next(
                revision
                for revision, _usage in revisions
                if revision.artifact_id == "audited_brief"
            )
            transition_artifacts[topology_transition.transition_id] = (
                (audited_brief, "topology_required"),
            )
            next_index = stage_order.index("editor") + 1
        next_stage_id = stage_order[next_index]
        add_transition(
            next_stage_id,
            transition_kind="activate",
            result_status="ready",
            transition_reason=f"activated after {completed_stage_id}",
        )
        unit = store.begin(
            verified.snapshot.run.run_id,
            request_id,
            transaction_type_for("stage_transition"),
            expected_store_revision,
        )
        for transition in transitions:
            unit.append_stage_transition(transition)
        for state in state_updates:
            unit.put_stage_state(state)
        for transition_id, bound_revisions in transition_artifacts.items():
            ordered_revisions = sorted(
                bound_revisions,
                key=lambda item: (item[0].artifact_id, item[0].revision),
            )
            for position, (revision, usage) in enumerate(ordered_revisions):
                unit.put_stage_artifact_binding(
                    StageArtifactBinding.model_validate(
                        {
                            "schema_version": StageArtifactBinding.schema_id,
                            "run_id": verified.snapshot.run.run_id,
                            "transition_id": transition_id,
                            "position": position,
                            "artifact_id": revision.artifact_id,
                            "artifact_revision": revision.revision,
                            "artifact_sha256": revision.sha256,
                            "usage": usage,
                            "accepted_transaction_id": request_id,
                        },
                        strict=True,
                    )
                )
        evaluations = {
            item.evaluation_id: item for item in verified.snapshot.gate_evaluations
        }
        for evaluation_id in gate_evaluation_ids:
            evaluation = evaluations[evaluation_id]
            unit.put_stage_gate_binding(
                StageGateBinding.model_validate(
                    {
                        "schema_version": StageGateBinding.schema_id,
                        "run_id": verified.snapshot.run.run_id,
                        "transition_id": completed.transition_id,
                        "gate_id": evaluation.gate_id,
                        "evaluation_id": evaluation_id,
                        "accepted_transaction_id": request_id,
                    },
                    strict=True,
                )
            )
        for event in events:
            unit.append_event(event)
        receipt = unit.commit()
        self._verifier.verify(store, verified.snapshot.run.run_id)
        return CoreRunResult(
            status="committed",
            receipt=receipt,
            primary_record_id=completed.transition_id,
        )

    def _roles_for(self, verified: VerifiedCoreRun, stage_id: str) -> tuple[str, ...]:
        if (
            verified.binding.role_topology == "human_assisted"
            and stage_id == "analyst"
        ):
            return (*STAGE_ROLES.get(stage_id, ()), "writer")
        return STAGE_ROLES.get(stage_id, ())

    def _load_contracts(self) -> ValidatedRuntimeContractPayloads:
        try:
            return load_runtime_contract_payloads(self.repo_workdir)
        except Exception as exc:
            raise CoreRunError("core_run_contract_mismatch") from exc

    @staticmethod
    def _require_store_revision(
        verified: VerifiedCoreRun,
        expected_revision: int,
    ) -> None:
        if verified.snapshot.store_revision != expected_revision:
            raise CoreRunError("store_revision_conflict")

    def _open_store(self) -> SQLiteControlStore:
        try:
            return SQLiteControlStore.open(
                self.workspace / "briefloop.db",
                clock=self._clock,
            )
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc


def _artifact_pair(
    *,
    run_id: str,
    artifact_id: str,
    revision: int,
    path: str,
    artifact_format: str,
    content: bytes,
    producer_kind: str,
    producer_id: str,
    created_at: str,
    required: bool,
) -> tuple[ArtifactRecord, ArtifactRevision]:
    digest = sha256_hex(content)
    return (
        ArtifactRecord.model_validate(
            {
                "schema_version": ArtifactRecord.schema_id,
                "run_id": run_id,
                "artifact_id": artifact_id,
                "current_revision": revision,
                "status": "valid",
                "required": required,
                "path": path,
                "format": artifact_format,
            },
            strict=True,
        ),
        ArtifactRevision.model_validate(
            {
                "schema_version": ArtifactRevision.schema_id,
                "run_id": run_id,
                "artifact_id": artifact_id,
                "revision": revision,
                "path": path,
                "sha256": digest,
                "size_bytes": len(content),
                "frozen": True,
                "producer_kind": producer_kind,
                "producer_id": producer_id,
                "created_at": created_at,
            },
            strict=True,
        ),
    )


_SECRET_BEARING_INPUT_KEYS = frozenset(
    {
        "access_key",
        "api_key",
        "authorization",
        "client" "_secret",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
        "webhook",
    }
)
_SECRET_BEARING_INPUT_SUFFIXES = tuple(
    f"_{name}" for name in sorted(_SECRET_BEARING_INPUT_KEYS)
)
_LEGACY_CONTROL_PATHS = (
    "output/intermediate/runtime_manifest.json",
    "output/intermediate/workflow_state.json",
    "output/intermediate/artifact_registry.json",
    "output/intermediate/event_log.jsonl",
    "output/intermediate/finalize_report.json",
)


def workspace_input_fingerprints(workspace: Path) -> tuple[str, str]:
    """Return exact hashes only after both workspace inputs are secret-free."""

    root = _workspace_root(workspace)
    config = read_workspace_file(root, "config.yaml")
    sources = read_workspace_file(root, "sources.yaml")
    if (
        config.entry_kind != "regular_file"
        or config.content is None
        or config.sha256 is None
        or sources.entry_kind != "regular_file"
        or sources.content is None
        or sources.sha256 is None
    ):
        raise CoreRunError("core_run_contract_mismatch")
    _require_non_secret_mapping(config.content)
    _require_non_secret_mapping(sources.content)
    return config.sha256, sources.sha256


def _require_non_secret_mapping(content: bytes) -> None:
    try:
        payload = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise CoreRunError("core_run_contract_mismatch") from exc
    if type(payload) is not dict:
        raise CoreRunError("core_run_contract_mismatch")

    pending: list[object] = [payload]
    seen_containers: set[int] = set()
    while pending:
        value = pending.pop()
        if type(value) in {dict, list}:
            identity = id(value)
            if identity in seen_containers:
                raise CoreRunError("core_run_contract_mismatch")
            seen_containers.add(identity)
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise CoreRunError("core_run_contract_mismatch")
                normalized = key.strip().casefold().replace("-", "_")
                if (
                    normalized in _SECRET_BEARING_INPUT_KEYS
                    or normalized.endswith(_SECRET_BEARING_INPUT_SUFFIXES)
                ):
                    raise CoreRunError("core_run_contract_mismatch")
                pending.append(child)
        elif type(value) is list:
            pending.extend(value)


def _legacy_control_state_present(workspace: Path) -> bool:
    for relative_path in _LEGACY_CONTROL_PATHS:
        target = workspace / relative_path
        try:
            target.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        return True
    return False


def _initial_transition(
    *,
    request: CoreRunInitializeRequest,
    stage_id: str,
    status: str,
    contract_fingerprint: str,
    event_id: str,
    now: str,
    request_fingerprint: str,
) -> StageTransitionRecord:
    return StageTransitionRecord.model_validate(
        {
            "schema_version": StageTransitionRecord.schema_id,
            "transition_id": derived_id("TRN-INIT", request.request_id, stage_id),
            "run_id": request.run_id,
            "stage_id": stage_id,
            "transition_kind": "initialize",
            "result_status": status,
            "result_revision": 0,
            "reason": "fresh-v2 initialization",
            "run_contract_fingerprint": contract_fingerprint,
            "actor": "system",
            "created_at": now,
            "transition_event_id": event_id,
            "accepted_transaction_id": request.request_id,
            "request_fingerprint": request_fingerprint,
        },
        strict=True,
    )


def _core_event(
    *,
    event_id: str,
    run_id: str,
    event_type: str,
    transaction_id: str,
    stage_id: str | None,
    decision: str,
    reason: str,
    created_at: str,
    binding: CoreRunEventBinding | None,
    artifact_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope.model_validate(
        {
            "schema_version": EventEnvelope.schema_id,
            "event_id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "created_at": created_at,
            "actor": "system",
            "transaction_id": transaction_id,
            "stage_id": stage_id,
            "artifact_id": artifact_id,
            "decision": decision,
            "reason": reason,
            "metadata": {},
            "core_run_binding": binding,
        },
        strict=True,
    )


def _stage_state(verified: VerifiedCoreRun, stage_id: str) -> StageState:
    state = next(
        (item for item in verified.snapshot.stage_states if item.stage_id == stage_id),
        None,
    )
    if state is None:
        raise CoreRunError("stage_not_current")
    return state


def _proposal(snapshot: object, kind: str):
    artifacts = {
        item.artifact_id: item
        for item in snapshot.artifacts  # type: ignore[attr-defined]
    }
    values = [
        item
        for item in snapshot.accepted_proposals  # type: ignore[attr-defined]
        if item.proposal_kind == kind
        and artifacts.get(item.artifact_id) is not None
        and artifacts[item.artifact_id].current_revision == item.artifact_revision
    ]
    if len(values) != 1:
        raise CoreRunError("stage_artifact_binding_invalid")
    return values[0]


def _workspace_root(workspace: str | os.PathLike[str]) -> Path:
    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError
        return root
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CoreRunError("core_run_request_invalid") from exc


def _now(clock: _Clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CoreRunError("core_run_request_invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _remove_created_store(database: Path) -> None:
    for path in (
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    blob_root = database.with_name(f"{database.name}.blobs")
    if blob_root.exists() and not blob_root.is_symlink():
        shutil.rmtree(blob_root)


__all__ = ["CoreRunService", "workspace_input_fingerprints"]
