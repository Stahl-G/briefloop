"""Read-only domain replay for the dormant fresh-v2 core run spine."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from multi_agent_brief.contracts.v2 import (
    AuditReportArtifact,
    CandidateClaimsProposal,
    ClaimDraftsProposal,
    CoreRunEventBinding,
    EventEnvelope,
    InvocationStartRequest,
    RunContractBinding,
    ScreenedCandidatesProposal,
    TransactionReceipt,
)
from multi_agent_brief.control_store import ControlStoreSnapshot, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.intake_v2.scratch import parse_json_object
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    ValidatedRuntimeContractPayloads,
    validate_runtime_contract_payloads,
)
from multi_agent_brief.quality_gates.contract import GATE_IDS

from .errors import CoreRunError, CoreRunResult
from .policy import (
    CLAIM_EPISTEMIC,
    CORE_ARTIFACT_IDS,
    INTERNAL_CONTRACT_ARTIFACT_IDS,
    REQUIRED_AUDITOR_GATES,
    derived_id,
    normalize_text,
    run_contract_fingerprint,
    transaction_type_for,
)


@dataclass(frozen=True)
class VerifiedCoreRun:
    snapshot: ControlStoreSnapshot
    binding: RunContractBinding
    contracts: ValidatedRuntimeContractPayloads

    @property
    def stages(self) -> tuple[dict[str, Any], ...]:
        return self.contracts.stages

    @property
    def artifacts(self) -> tuple[dict[str, Any], ...]:
        return self.contracts.artifacts


@dataclass(frozen=True)
class _CoreEffectBindingRule:
    transaction_type: str
    event_types: frozenset[str]
    primary_family: str


_CORE_EFFECT_BINDING_RULES = {
    "initialize": _CoreEffectBindingRule(
        transaction_type_for("initialize"),
        frozenset({"run_initialized"}),
        "run_contract_binding",
    ),
    "invocation_start": _CoreEffectBindingRule(
        transaction_type_for("invocation_start"),
        frozenset({"role_invocation_started"}),
        "invocation",
    ),
    "owned_artifact_acceptance": _CoreEffectBindingRule(
        transaction_type_for("owned_artifact_acceptance"),
        frozenset({"owned_artifact_accepted"}),
        "owned_artifact_submission",
    ),
    "claim_freeze": _CoreEffectBindingRule(
        transaction_type_for("claim_freeze"),
        frozenset({"claim_ledger_frozen"}),
        "claim_freeze",
    ),
    "audit_promotion": _CoreEffectBindingRule(
        transaction_type_for("audit_promotion"),
        frozenset({"audit_proposal_promoted"}),
        "audit_submission",
    ),
    "gate_evaluation": _CoreEffectBindingRule(
        transaction_type_for("gate_evaluation"),
        frozenset({"quality_gate_checked"}),
        "gate_batch",
    ),
    "stage_transition": _CoreEffectBindingRule(
        transaction_type_for("stage_transition"),
        frozenset({"stage_status_changed", "stage_satisfied_by_topology"}),
        "stage_transition",
    ),
    "integrity_contamination": _CoreEffectBindingRule(
        transaction_type_for("integrity_contamination"),
        frozenset({"run_integrity_contaminated"}),
        "run_integrity_record",
    ),
}


class CoreRunDomainVerifier:
    """Replay business legality from one structurally verified Store snapshot."""

    def verify(
        self,
        store: SQLiteControlStore,
        run_id: str,
    ) -> VerifiedCoreRun:
        try:
            snapshot = store.load_snapshot(run_id)
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        if len(snapshot.run_contract_bindings) != 1:
            raise CoreRunError("core_run_not_initialized")
        binding = snapshot.run_contract_bindings[0]
        head = snapshot.workspace_run_head
        if (
            head is None
            or head.current_run_id != run_id
            or head.workspace_id != snapshot.run.workspace_id
            or binding.run_id != run_id
            or binding.workspace_id != snapshot.run.workspace_id
            or binding.runtime != snapshot.run.runtime
        ):
            raise CoreRunError("core_run_head_mismatch")
        contracts = self._load_contracts(store, binding)
        self._verify_contract_fingerprint(binding)
        self._verify_receipt_bindings(snapshot)
        self._verify_invocation_ownership(snapshot, binding)
        self._verify_artifact_graph(snapshot, contracts, binding)
        self._verify_stage_chain(store, snapshot, contracts, binding)
        self._verify_integrity_chain(snapshot)
        self._verify_claim_chain(store, snapshot, binding)
        self._verify_gate_chain(store, snapshot, binding, contracts)
        return VerifiedCoreRun(
            snapshot=snapshot,
            binding=binding,
            contracts=contracts,
        )

    @staticmethod
    def _load_contracts(
        store: SQLiteControlStore,
        binding: RunContractBinding,
    ) -> ValidatedRuntimeContractPayloads:
        try:
            stage_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.stage_specs_artifact.artifact_id,
                binding.stage_specs_artifact.revision,
            )
            artifact_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.artifact_contracts_artifact.artifact_id,
                binding.artifact_contracts_artifact.revision,
            )
            policy_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.policy_pack_artifact.artifact_id,
                binding.policy_pack_artifact.revision,
            )
            if (
                sha256_hex(stage_bytes) != binding.stage_specs_sha256
                or sha256_hex(artifact_bytes) != binding.artifact_contracts_sha256
                or sha256_hex(policy_bytes) != binding.policy_pack_sha256
            ):
                raise CoreRunError("core_run_contract_mismatch")
            stage_payload = parse_json_object(stage_bytes)
            artifact_payload = parse_json_object(artifact_bytes)
            policy_payload = parse_json_object(policy_bytes)
            contracts = validate_runtime_contract_payloads(
                stage_payload,
                artifact_payload,
                policy_payload,
            )
        except CoreRunError:
            raise
        except Exception as exc:
            raise CoreRunError("core_run_contract_mismatch") from exc
        if (
            stage_payload.get("schema_version") != binding.stage_specs_schema
            or artifact_payload.get("schema_version")
            != binding.artifact_contracts_schema
            or policy_payload.get("schema_version") != binding.policy_pack_schema
            or policy_payload.get("policy_pack", {}).get("name")
            != binding.policy_pack_name
        ):
            raise CoreRunError("core_run_contract_mismatch")
        return contracts

    @staticmethod
    def _verify_contract_fingerprint(binding: RunContractBinding) -> None:
        expected = run_contract_fingerprint(
            runtime=binding.runtime,
            stage_specs_schema=binding.stage_specs_schema,
            stage_specs_sha256=binding.stage_specs_sha256,
            artifact_contracts_schema=binding.artifact_contracts_schema,
            artifact_contracts_sha256=binding.artifact_contracts_sha256,
            policy_pack_schema=binding.policy_pack_schema,
            policy_pack_name=binding.policy_pack_name,
            policy_pack_sha256=binding.policy_pack_sha256,
            run_direction=binding.run_direction.model_dump(
                mode="json",
                exclude_unset=False,
            ),
            workspace_config_sha256=binding.workspace_config_sha256,
            sources_config_sha256=binding.sources_config_sha256,
            role_topology=binding.role_topology,
            gate_strictness=binding.gate_strictness,
            input_governance_required=binding.input_governance_required,
        )
        if expected != binding.contract_fingerprint:
            raise CoreRunError("core_run_contract_mismatch")

    @staticmethod
    def _verify_receipt_bindings(snapshot: ControlStoreSnapshot) -> None:
        for receipt in snapshot.transactions:
            if not receipt.transaction_type.startswith("core-v2-"):
                continue
            _verified_core_receipt_binding(snapshot, receipt)

    @staticmethod
    def _verify_invocation_ownership(
        snapshot: ControlStoreSnapshot,
        binding: RunContractBinding,
    ) -> None:
        invocation_stages: dict[str, str] = {}
        for event in snapshot.events:
            core = event.core_run_binding
            if core is None or core.effect_kind != "invocation_start":
                continue
            if (
                event.stage_id is None
                or core.primary_record_id in invocation_stages
            ):
                raise CoreRunError("control_store_integrity_invalid")
            invocation_stages[core.primary_record_id] = event.stage_id
        invocations = {
            item.invocation_id: item for item in snapshot.invocations
        }
        if set(invocations) != set(invocation_stages):
            raise CoreRunError("control_store_integrity_invalid")
        for invocation in invocations.values():
            if (
                invocation.run_id != snapshot.run.run_id
                or invocation.runtime != snapshot.run.runtime
            ):
                raise CoreRunError("control_store_integrity_invalid")

        def require_invocation(
            invocation_id: str,
            *,
            stage_id: str,
            role_id: str,
            completed: bool,
        ) -> None:
            invocation = invocations.get(invocation_id)
            if (
                invocation is None
                or invocation_stages.get(invocation_id) != stage_id
                or invocation.role_id != role_id
                or (completed and invocation.status != "completed")
            ):
                raise CoreRunError("control_store_integrity_invalid")

        for source in snapshot.sources:
            require_invocation(
                source.invocation_id,
                stage_id="source-discovery",
                role_id="source-provider",
                completed=True,
            )

        proposal_owners = {
            "candidate": {("scout", "scout")},
            "claim_drafts": {("claim-ledger", "claim-ledger")},
            "audit": {("auditor", "auditor")},
            "screened": {("scout", "scout"), ("screener", "screener")},
        }
        for proposal in snapshot.accepted_proposals:
            allowed = proposal_owners.get(proposal.proposal_kind)
            owner = (
                proposal.owner_stage_id,
                proposal.owner_role_id,
            )
            if allowed is None or owner not in allowed:
                raise CoreRunError("control_store_integrity_invalid")
            require_invocation(
                proposal.invocation_id,
                stage_id=owner[0],
                role_id=owner[1],
                completed=True,
            )

        owned_artifact_owners = {
            "source_candidates": ("source-discovery", "source-planner"),
            "input_classification": ("input-governance", "python_tool"),
            "analyst_draft_snapshot": ("analyst", "analyst"),
            "audit_report": ("auditor", "auditor"),
        }
        for submission in snapshot.owned_artifact_submissions:
            if submission.artifact_id == "audited_brief":
                expected = (
                    ("analyst", "writer")
                    if binding.role_topology == "human_assisted"
                    else ("editor", "editor")
                )
            else:
                expected = owned_artifact_owners.get(submission.artifact_id)
            if expected is None or (
                submission.owner_stage_id,
                submission.owner_role_id,
            ) != expected:
                raise CoreRunError("control_store_integrity_invalid")
            if submission.invocation_id is None:
                if (
                    submission.artifact_id != "input_classification"
                    or submission.producer_tool_id != "input-governance-v2"
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            else:
                require_invocation(
                    submission.invocation_id,
                    stage_id=expected[0],
                    role_id=expected[1],
                    completed=True,
                )

    @staticmethod
    def _verify_artifact_graph(
        snapshot: ControlStoreSnapshot,
        contracts: ValidatedRuntimeContractPayloads,
        binding: RunContractBinding,
    ) -> None:
        artifacts = {item.artifact_id: item for item in snapshot.artifacts}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        contract_rows = {
            str(item["artifact_id"]): item for item in contracts.artifacts
        }
        if not set(CORE_ARTIFACT_IDS) <= set(contract_rows):
            raise CoreRunError("control_store_integrity_invalid")

        source_artifact_ids: set[str] = set()
        for source in snapshot.sources:
            source_artifact_ids.add(source.content_artifact_id)
            if source.raw_payload_artifact_id is not None:
                source_artifact_ids.add(source.raw_payload_artifact_id)
        proposal_artifact_ids = {
            item.artifact_id for item in snapshot.accepted_proposals
        }
        expected_ids = (
            set(CORE_ARTIFACT_IDS)
            | set(INTERNAL_CONTRACT_ARTIFACT_IDS)
            | source_artifact_ids
            | proposal_artifact_ids
        )
        if set(artifacts) != expected_ids:
            raise CoreRunError("control_store_integrity_invalid")

        for artifact_id in CORE_ARTIFACT_IDS:
            artifact = artifacts[artifact_id]
            contract = contract_rows[artifact_id]
            if (
                artifact.path != contract["path"]
                or artifact.format != contract["format"]
                or artifact.required is not contract["required"]
            ):
                raise CoreRunError("control_store_integrity_invalid")

        contract_refs = {
            binding.stage_specs_artifact.artifact_id: (
                binding.stage_specs_artifact.revision,
                binding.stage_specs_sha256,
            ),
            binding.artifact_contracts_artifact.artifact_id: (
                binding.artifact_contracts_artifact.revision,
                binding.artifact_contracts_sha256,
            ),
            binding.policy_pack_artifact.artifact_id: (
                binding.policy_pack_artifact.revision,
                binding.policy_pack_sha256,
            ),
        }
        if set(contract_refs) != set(INTERNAL_CONTRACT_ARTIFACT_IDS):
            raise CoreRunError("control_store_integrity_invalid")

        expected_producers: dict[tuple[str, int], tuple[str, str]] = {}

        def bind_producer(
            artifact_id: str,
            revision_number: int,
            producer: tuple[str, str],
        ) -> None:
            key = (artifact_id, revision_number)
            prior = expected_producers.get(key)
            if prior is not None and prior != producer:
                raise CoreRunError("control_store_integrity_invalid")
            expected_producers[key] = producer

        for artifact_id, (revision_number, digest) in contract_refs.items():
            revision = revisions.get((artifact_id, revision_number))
            artifact = artifacts[artifact_id]
            if (
                revision is None
                or revision.sha256 != digest
                or artifact.current_revision != revision_number
                or artifact.format != "json"
                or not artifact.required
            ):
                raise CoreRunError("control_store_integrity_invalid")
            bind_producer(
                artifact_id,
                revision_number,
                ("control_tool", "core-v2-initializer"),
            )

        for source in snapshot.sources:
            bind_producer(
                source.content_artifact_id,
                source.content_artifact_revision,
                ("workflow_stage", "source-discovery"),
            )
            if source.raw_payload_artifact_id is not None:
                bind_producer(
                    source.raw_payload_artifact_id,
                    source.raw_payload_artifact_revision,  # type: ignore[arg-type]
                    ("workflow_stage", "source-discovery"),
                )
        for proposal in snapshot.accepted_proposals:
            bind_producer(
                proposal.artifact_id,
                proposal.artifact_revision,
                ("workflow_stage", proposal.owner_stage_id),
            )
        for submission in snapshot.owned_artifact_submissions:
            producer = (
                ("control_tool", "audit-proposal-promoter-v2")
                if submission.source_proposal_id is not None
                else (
                    "workflow_stage",
                    submission.owner_role_id,
                )
                if submission.invocation_id is not None
                else ("control_tool", submission.owner_role_id)
            )
            bind_producer(
                submission.artifact_id,
                submission.artifact_revision,
                producer,
            )
        for freeze in snapshot.claim_freezes:
            bind_producer(
                freeze.ledger_artifact.artifact_id,
                freeze.ledger_artifact.revision,
                ("control_tool", "claim-freeze-v2"),
            )
        for evaluation in snapshot.gate_evaluations:
            bind_producer(
                evaluation.report_artifact.artifact_id,
                evaluation.report_artifact.revision,
                ("control_tool", "core-v2-preloaded-quality-gates"),
            )

        if set(revisions) != set(expected_producers):
            raise CoreRunError("control_store_integrity_invalid")
        for key, revision in revisions.items():
            if (
                not revision.frozen
                or (revision.producer_kind, revision.producer_id)
                != expected_producers[key]
            ):
                raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_stage_chain(
        store: SQLiteControlStore,
        snapshot: ControlStoreSnapshot,
        contracts: ValidatedRuntimeContractPayloads,
        binding: RunContractBinding,
    ) -> None:
        stage_ids = [str(item["stage_id"]) for item in contracts.stages]
        states = {state.stage_id: state for state in snapshot.stage_states}
        if set(states) != set(stage_ids):
            raise CoreRunError("control_store_integrity_invalid")
        transitions: dict[str, list[object]] = {stage_id: [] for stage_id in stage_ids}
        for transition in snapshot.stage_transitions:
            if transition.stage_id not in transitions:
                raise CoreRunError("control_store_integrity_invalid")
            transitions[transition.stage_id].append(transition)
        for stage_id in stage_ids:
            rows = sorted(
                transitions[stage_id],
                key=lambda item: item.result_revision,  # type: ignore[attr-defined]
            )
            if not rows or rows[0].transition_kind != "initialize":
                raise CoreRunError("control_store_integrity_invalid")
            for revision, row in enumerate(rows):
                if (
                    row.result_revision != revision
                    or row.run_contract_fingerprint != binding.contract_fingerprint
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                if revision and (
                    row.prior_revision != revision - 1
                    or row.prior_status != rows[revision - 1].result_status
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            state = states[stage_id]
            if (
                state.revision != rows[-1].result_revision
                or state.status != rows[-1].result_status
            ):
                raise CoreRunError("control_store_integrity_invalid")

        transition_by_id = {
            item.transition_id: item for item in snapshot.stage_transitions
        }
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        artifact_bindings: dict[str, list[object]] = {}
        for artifact_binding in snapshot.stage_artifact_bindings:
            transition = transition_by_id.get(artifact_binding.transition_id)
            revision = revisions.get(
                (
                    artifact_binding.artifact_id,
                    artifact_binding.artifact_revision,
                )
            )
            if (
                transition is None
                or transition.transition_kind
                not in {"complete", "satisfied_by_topology"}
                or revision is None
                or revision.sha256 != artifact_binding.artifact_sha256
                or artifact_binding.accepted_transaction_id
                != transition.accepted_transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            artifact_bindings.setdefault(transition.transition_id, []).append(
                artifact_binding
            )
        for values in artifact_bindings.values():
            positions = sorted(item.position for item in values)  # type: ignore[attr-defined]
            if positions != list(range(len(positions))):
                raise CoreRunError("control_store_integrity_invalid")

        evaluations = {
            item.evaluation_id: item for item in snapshot.gate_evaluations
        }
        gate_bindings: dict[str, list[object]] = {}
        for gate_binding in snapshot.stage_gate_bindings:
            transition = transition_by_id.get(gate_binding.transition_id)
            evaluation = evaluations.get(gate_binding.evaluation_id)
            if (
                transition is None
                or transition.stage_id != "auditor"
                or transition.transition_kind != "complete"
                or evaluation is None
                or evaluation.gate_id != gate_binding.gate_id
                or evaluation.stage_id != "auditor"
                or gate_binding.accepted_transaction_id
                != transition.accepted_transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            gate_bindings.setdefault(gate_binding.transition_id, []).append(
                gate_binding
            )

        artifacts = {item.artifact_id: item for item in snapshot.artifacts}

        def current_revision(artifact_id: str):
            artifact = artifacts.get(artifact_id)
            if artifact is None or artifact.current_revision <= 0:
                raise CoreRunError("control_store_integrity_invalid")
            revision = revisions.get((artifact_id, artifact.current_revision))
            if revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            return revision

        def current_proposal(kind: str):
            values = [
                item
                for item in snapshot.accepted_proposals
                if item.proposal_kind == kind
                and artifacts.get(item.artifact_id) is not None
                and artifacts[item.artifact_id].current_revision
                == item.artifact_revision
            ]
            if len(values) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            return values[0]

        def proposal_revision(kind: str):
            proposal = current_proposal(kind)
            revision = revisions.get(
                (proposal.artifact_id, proposal.artifact_revision)
            )
            if revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            return revision

        def expected_artifacts_for(transition):
            stage_id = transition.stage_id
            kind = transition.transition_kind
            if kind == "satisfied_by_topology":
                if stage_id == "screener" and binding.role_topology in {
                    "default",
                    "human_assisted",
                }:
                    return (
                        (proposal_revision("candidate"), "consumed"),
                        (proposal_revision("screened"), "produced"),
                    )
                if stage_id == "editor" and binding.role_topology == "human_assisted":
                    return ((current_revision("audited_brief"), "topology_required"),)
                raise CoreRunError("control_store_integrity_invalid")
            if kind != "complete":
                return ()
            if stage_id == "doctor":
                return ()
            if stage_id == "source-discovery":
                eligible_sources = sorted(
                    (item for item in snapshot.sources if item.claims_eligible),
                    key=lambda item: item.source_id,
                )
                if not eligible_sources:
                    raise CoreRunError("control_store_integrity_invalid")
                source_revisions = []
                for source in eligible_sources:
                    revision = revisions.get(
                        (
                            source.content_artifact_id,
                            source.content_artifact_revision,
                        )
                    )
                    if revision is None or revision.sha256 != source.content_sha256:
                        raise CoreRunError("control_store_integrity_invalid")
                    source_revisions.append((revision, "consumed"))
                return (
                    (current_revision("source_candidates"), "produced"),
                    *source_revisions,
                )
            if stage_id == "input-governance":
                if not binding.input_governance_required:
                    return ()
                return ((current_revision("input_classification"), "produced"),)
            if stage_id == "scout":
                selected = [(proposal_revision("candidate"), "produced")]
                if binding.role_topology in {"default", "human_assisted"}:
                    selected.append(
                        (proposal_revision("screened"), "topology_required")
                    )
                return tuple(selected)
            if stage_id == "screener":
                if binding.role_topology != "strict":
                    raise CoreRunError("control_store_integrity_invalid")
                return (
                    (proposal_revision("screened"), "produced"),
                    (proposal_revision("candidate"), "consumed"),
                )
            if stage_id == "claim-ledger":
                if len(snapshot.claim_freezes) != 1:
                    raise CoreRunError("control_store_integrity_invalid")
                freeze = snapshot.claim_freezes[0]
                draft = revisions.get(
                    (
                        freeze.claim_drafts_artifact.artifact_id,
                        freeze.claim_drafts_artifact.revision,
                    )
                )
                ledger = revisions.get(
                    (
                        freeze.ledger_artifact.artifact_id,
                        freeze.ledger_artifact.revision,
                    )
                )
                if draft is None or ledger is None:
                    raise CoreRunError("control_store_integrity_invalid")
                return ((draft, "consumed"), (ledger, "produced"))
            if stage_id == "analyst":
                if binding.role_topology == "human_assisted":
                    return (
                        (current_revision("audited_brief"), "topology_required"),
                    )
                return (
                    (current_revision("analyst_draft_snapshot"), "produced"),
                )
            if stage_id == "editor":
                if binding.role_topology == "human_assisted":
                    raise CoreRunError("control_store_integrity_invalid")
                return (
                    (current_revision("audited_brief"), "produced"),
                    (current_revision("analyst_draft_snapshot"), "consumed"),
                )
            if stage_id == "auditor":
                selected = [
                    (current_revision("claim_ledger"), "consumed"),
                    (current_revision("audited_brief"), "consumed"),
                    (current_revision("audit_report"), "produced"),
                    (
                        current_revision("auditor_quality_gate_report"),
                        "produced",
                    ),
                ]
                analyst = artifacts.get("analyst_draft_snapshot")
                if analyst is not None and analyst.current_revision:
                    selected.append(
                        (current_revision("analyst_draft_snapshot"), "consumed")
                    )
                return tuple(selected)
            raise CoreRunError("control_store_integrity_invalid")

        for transition in snapshot.stage_transitions:
            expected = sorted(
                expected_artifacts_for(transition),
                key=lambda item: (item[0].artifact_id, item[0].revision),
            )
            actual = sorted(
                artifact_bindings.get(transition.transition_id, []),
                key=lambda item: item.position,
            )
            expected_signature = [
                (
                    position,
                    revision.artifact_id,
                    revision.revision,
                    revision.sha256,
                    usage,
                )
                for position, (revision, usage) in enumerate(expected)
            ]
            actual_signature = [
                (
                    item.position,
                    item.artifact_id,
                    item.artifact_revision,
                    item.artifact_sha256,
                    item.usage,
                )
                for item in actual
            ]
            if actual_signature != expected_signature:
                raise CoreRunError("control_store_integrity_invalid")

            actual_gates = {
                (item.gate_id, item.evaluation_id)
                for item in gate_bindings.get(transition.transition_id, [])
            }
            if transition.stage_id == "auditor" and transition.transition_kind == "complete":
                gate_report = current_revision("auditor_quality_gate_report")
                required = {
                    (
                        item.gate_id,
                        item.evaluation_id,
                    )
                    for item in snapshot.gate_evaluations
                    if item.gate_id in REQUIRED_AUDITOR_GATES
                    and item.report_artifact.artifact_id == gate_report.artifact_id
                    and item.report_artifact.revision == gate_report.revision
                    and item.status in {"pass", "warning"}
                    and not item.blocking
                }
                if {gate_id for gate_id, _evaluation_id in required} != set(
                    REQUIRED_AUDITOR_GATES
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                expected_gates = required
                audit_revision = current_revision("audit_report")
                try:
                    audit = AuditReportArtifact.model_validate(
                        parse_json_object(
                            store.read_artifact_revision_bytes(
                                snapshot.run.run_id,
                                audit_revision.artifact_id,
                                audit_revision.revision,
                            )
                        ),
                        strict=True,
                    )
                except Exception as exc:
                    raise CoreRunError("control_store_integrity_invalid") from exc
                if audit.decision == "fail" or any(
                    finding.severity == "error" for finding in audit.findings
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            else:
                expected_gates = set()
            if actual_gates != expected_gates:
                raise CoreRunError("control_store_integrity_invalid")

        invocation_stages = {
            event.core_run_binding.primary_record_id: event.stage_id
            for event in snapshot.events
            if event.core_run_binding is not None
            and event.core_run_binding.effect_kind == "invocation_start"
        }
        invocations = {
            item.invocation_id: item for item in snapshot.invocations
        }
        producer_roles = {
            "source-discovery": {"source-planner"},
            "scout": {"scout"},
            "screener": {"screener"},
            "claim-ledger": {"claim-ledger"},
            "analyst": (
                {"writer"}
                if binding.role_topology == "human_assisted"
                else {"analyst"}
            ),
            "editor": {"editor"},
            "auditor": {"auditor"},
        }
        for transition in snapshot.stage_transitions:
            if transition.transition_kind == "initialize":
                continue
            if transition.transition_kind == "activate":
                if (
                    transition.producer_invocation_id is not None
                    or transition.producer_tool_id is not None
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                continue
            if transition.stage_id == "doctor":
                if (
                    transition.producer_invocation_id is not None
                    or transition.producer_tool_id != "core-v2-doctor"
                    or transition.producer_result_status != "pass"
                    or transition.producer_implementation != "core-v2-doctor"
                    or transition.producer_version != "1"
                    or transition.producer_result_fingerprint is None
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                continue
            if transition.stage_id == "input-governance":
                if binding.input_governance_required:
                    if (
                        transition.producer_tool_id != "input-governance-v2"
                        or transition.producer_invocation_id is not None
                    ):
                        raise CoreRunError("control_store_integrity_invalid")
                elif (
                    transition.producer_tool_id is not None
                    or transition.producer_invocation_id is not None
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                continue
            if (
                transition.transition_kind == "satisfied_by_topology"
                and transition.stage_id == "screener"
            ):
                expected_roles = {"scout"}
                expected_invocation_stage = "scout"
            elif (
                transition.transition_kind == "satisfied_by_topology"
                and transition.stage_id == "editor"
            ):
                expected_roles = {"writer"}
                expected_invocation_stage = "analyst"
            else:
                expected_roles = producer_roles.get(transition.stage_id, set())
                expected_invocation_stage = transition.stage_id
            invocation = invocations.get(transition.producer_invocation_id or "")
            if (
                invocation is None
                or invocation.status != "completed"
                or invocation.role_id not in expected_roles
                or invocation_stages.get(invocation.invocation_id)
                != expected_invocation_stage
            ):
                raise CoreRunError("control_store_integrity_invalid")
        ready = [stage_id for stage_id in stage_ids if states[stage_id].status == "ready"]
        if len(ready) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        first_unfinished = next(
            (
                stage_id
                for stage_id in stage_ids
                if states[stage_id].status not in {"complete", "skipped"}
            ),
            None,
        )
        if ready[0] != first_unfinished:
            raise CoreRunError("control_store_integrity_invalid")

        expected_initial_artifacts = set(CORE_ARTIFACT_IDS)
        if not expected_initial_artifacts <= {
            item.artifact_id for item in snapshot.artifacts
        }:
            raise CoreRunError("control_store_integrity_invalid")
        initial = {
            item.artifact_id
            for item in snapshot.artifacts
            if item.current_revision == 0
        }
        if not initial <= expected_initial_artifacts:
            raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_integrity_chain(snapshot: ControlStoreSnapshot) -> None:
        rows = sorted(
            snapshot.run_integrity_records,
            key=lambda item: item.integrity_revision,
        )
        if not rows or rows[0].status != "clean" or rows[0].integrity_revision != 1:
            raise CoreRunError("control_store_integrity_invalid")
        contaminated = False
        for revision, row in enumerate(rows, start=1):
            if row.integrity_revision != revision:
                raise CoreRunError("control_store_integrity_invalid")
            if row.status == "contaminated":
                contaminated = True
            elif contaminated:
                raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_claim_chain(
        store: SQLiteControlStore,
        snapshot: ControlStoreSnapshot,
        binding: RunContractBinding,
    ) -> None:
        if not snapshot.claim_freezes:
            if snapshot.claims or snapshot.claim_source_bindings:
                raise CoreRunError("control_store_integrity_invalid")
            return
        if len(snapshot.claim_freezes) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        freeze = snapshot.claim_freezes[0]
        proposals = {
            item.proposal_id: item for item in snapshot.accepted_proposals
        }
        drafts_record = proposals.get(freeze.claim_drafts_proposal_id)
        screened_record = proposals.get(freeze.screened_proposal_id)
        candidate_record = proposals.get(freeze.candidate_proposal_id)
        if (
            drafts_record is None
            or drafts_record.proposal_kind != "claim_drafts"
            or screened_record is None
            or screened_record.proposal_kind != "screened"
            or candidate_record is None
            or candidate_record.proposal_kind != "candidate"
            or drafts_record.parent_proposal_id != screened_record.proposal_id
            or screened_record.parent_proposal_id != candidate_record.proposal_id
            or freeze.run_contract_fingerprint != binding.contract_fingerprint
            or freeze.normalization_policy != "sorted_sequential_v2"
        ):
            raise CoreRunError("control_store_integrity_invalid")
        try:
            drafts_bytes = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                drafts_record.artifact_id,
                drafts_record.artifact_revision,
            )
            screened_bytes = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                screened_record.artifact_id,
                screened_record.artifact_revision,
            )
            candidate_bytes = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                candidate_record.artifact_id,
                candidate_record.artifact_revision,
            )
            drafts = ClaimDraftsProposal.model_validate(
                parse_json_object(drafts_bytes),
                strict=True,
            )
            screened = ScreenedCandidatesProposal.model_validate(
                parse_json_object(screened_bytes),
                strict=True,
            )
            candidates = CandidateClaimsProposal.model_validate(
                parse_json_object(candidate_bytes),
                strict=True,
            )
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        if (
            sha256_hex(drafts_bytes) != drafts_record.proposal_sha256
            or sha256_hex(screened_bytes) != screened_record.proposal_sha256
            or sha256_hex(candidate_bytes) != candidate_record.proposal_sha256
            or freeze.claim_drafts_sha256 != sha256_hex(drafts_bytes)
            or drafts.proposal_id != drafts_record.proposal_id
            or drafts.screened_candidates_proposal_id != screened.proposal_id
            or screened.proposal_id != screened_record.proposal_id
            or screened.candidate_claims_proposal_id != candidates.proposal_id
            or candidates.proposal_id != candidate_record.proposal_id
        ):
            raise CoreRunError("control_store_integrity_invalid")

        candidate_sources = {
            item.candidate_id: item.source_id for item in candidates.candidates
        }
        decisions = {
            item.candidate_id: item.decision for item in screened.decisions
        }
        if set(candidate_sources) != set(decisions):
            raise CoreRunError("control_store_integrity_invalid")
        selected_source_ids = {
            candidate_sources[candidate_id]
            for candidate_id, decision in decisions.items()
            if decision == "selected"
        }
        sources = {item.source_id: item for item in snapshot.sources}
        transaction_revisions = {
            item.transaction_id: item.committed_revision
            for item in snapshot.transactions
        }
        drafts_revision = transaction_revisions.get(
            drafts_record.accepted_transaction_id
        )
        freeze_revision = transaction_revisions.get(freeze.accepted_transaction_id)
        if drafts_revision is None or freeze_revision is None:
            raise CoreRunError("control_store_integrity_invalid")

        canonical_drafts = sorted(
            drafts.drafts,
            key=lambda item: (
                tuple(sorted(item.source_ids)),
                normalize_text(item.statement),
                normalize_text(item.evidence_text),
                item.claim_type,
                item.draft_id,
            ),
        )
        claims = sorted(snapshot.claims, key=lambda item: item.ordinal)
        if (
            len(claims) != freeze.claim_count
            or len(claims) != len(canonical_drafts)
            or [item.ordinal for item in claims] != list(range(1, len(claims) + 1))
            or [item.claim_id for item in claims]
            != [f"CL-{index:04d}" for index in range(1, len(claims) + 1)]
            or any(item.freeze_id != freeze.freeze_id for item in claims)
        ):
            raise CoreRunError("control_store_integrity_invalid")
        by_claim: dict[str, list[object]] = defaultdict(list)
        for source_binding in snapshot.claim_source_bindings:
            by_claim[source_binding.claim_id].append(source_binding)
        ledger_claims: list[dict[str, object]] = []
        duplicate_statements: dict[str, list[str]] = defaultdict(list)
        for claim, draft in zip(claims, canonical_drafts):
            source_ids = tuple(sorted(draft.source_ids))
            if not source_ids:
                raise CoreRunError("control_store_integrity_invalid")
            for source_id in source_ids:
                source = sources.get(source_id)
                source_revision = (
                    None
                    if source is None
                    else transaction_revisions.get(source.accepted_transaction_id)
                )
                if (
                    source is None
                    or not source.claims_eligible
                    or source_id not in selected_source_ids
                    or source_revision is None
                    or not source_revision < drafts_revision < freeze_revision
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            statement = normalize_text(draft.statement)
            evidence = normalize_text(draft.evidence_text)
            duplicate_statements[statement.casefold()].append(draft.draft_id)
            expected_claim = {
                "schema_version": claim.schema_id,
                "run_id": snapshot.run.run_id,
                "claim_id": claim.claim_id,
                "freeze_id": freeze.freeze_id,
                "ordinal": claim.ordinal,
                "claim_drafts_proposal_id": drafts.proposal_id,
                "draft_id": draft.draft_id,
                "statement": statement,
                "evidence_text": evidence,
                "primary_source_id": source_ids[0],
                "claim_type": draft.claim_type,
                "confidence": "medium",
                "requires_audit": True,
                "epistemic_type": CLAIM_EPISTEMIC[draft.claim_type],
                "evidence_relation": "direct",
                "applicability_reason": None,
                "limitations": [],
                "metadata": {"source_ids": list(source_ids)},
                "created_at": freeze.frozen_at,
                "accepted_transaction_id": freeze.accepted_transaction_id,
            }
            if claim.model_dump(mode="json", exclude_unset=False) != expected_claim:
                raise CoreRunError("control_store_integrity_invalid")
            source_bindings = sorted(
                by_claim.get(claim.claim_id, []),
                key=lambda item: item.position,  # type: ignore[attr-defined]
            )
            expected_bindings = [
                {
                    "schema_version": source_binding.schema_id,
                    "run_id": snapshot.run.run_id,
                    "claim_id": claim.claim_id,
                    "source_id": source_id,
                    "position": position,
                    "citation_role": "primary" if position == 0 else "additional",
                    "claim_drafts_proposal_id": drafts.proposal_id,
                    "accepted_transaction_id": freeze.accepted_transaction_id,
                }
                for position, (source_binding, source_id) in enumerate(
                    zip(source_bindings, source_ids)
                )
            ] if len(source_bindings) == len(source_ids) else []
            if [
                item.model_dump(mode="json", exclude_unset=False)
                for item in source_bindings
            ] != expected_bindings:
                raise CoreRunError("control_store_integrity_invalid")
            primary = sources[source_ids[0]]
            locator = primary.locator.model_dump(mode="json", exclude_unset=False)
            ledger_claims.append(
                {
                    "claim_id": claim.claim_id,
                    "statement": statement,
                    "source_id": source_ids[0],
                    "evidence_text": evidence,
                    "source_url": locator.get("url", locator.get("path", "")),
                    "source_type": primary.retrieval_source_type,
                    "claim_type": draft.claim_type,
                    "confidence": "medium",
                    "requires_audit": True,
                    "created_by": "claim-ledger",
                    "used_in_sections": [],
                    "metadata": {
                        "source_ids": list(source_ids),
                        "source_title": primary.title,
                        "source_category": primary.source_category,
                        "published_at": primary.published_at,
                        "retrieved_at": primary.retrieved_at,
                        "underlying_evidence_type": primary.underlying_evidence_type,
                    },
                    "schema_version": "v2",
                    "epistemic_type": CLAIM_EPISTEMIC[draft.claim_type],
                    "evidence_relation": "direct",
                    "applicability_reason": "",
                    "limitations": [],
                }
            )
        warnings = [
            {
                "warning_type": "lexical_duplicate_statement",
                "draft_ids": sorted(draft_ids),
            }
            for draft_ids in duplicate_statements.values()
            if len(draft_ids) > 1
        ]
        warnings.sort(key=lambda item: item["draft_ids"])
        if (
            [item.model_dump(mode="json", exclude_unset=False) for item in freeze.warnings]
            != warnings
            or freeze.warning_count != len(warnings)
        ):
            raise CoreRunError("control_store_integrity_invalid")
        ledger_bytes = canonical_json_bytes({"claims": ledger_claims}) + b"\n"
        try:
            stored_ledger = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                freeze.ledger_artifact.artifact_id,
                freeze.ledger_artifact.revision,
            )
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        if (
            stored_ledger != ledger_bytes
            or freeze.ledger_sha256 != sha256_hex(ledger_bytes)
            or freeze.claim_drafts_artifact.artifact_id != drafts_record.artifact_id
            or freeze.claim_drafts_artifact.revision
            != drafts_record.artifact_revision
        ):
            raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_gate_chain(
        store: SQLiteControlStore,
        snapshot: ControlStoreSnapshot,
        binding: RunContractBinding,
        contracts: ValidatedRuntimeContractPayloads,
    ) -> None:
        if not snapshot.gate_evaluations:
            if snapshot.gate_findings or snapshot.gate_artifact_bindings:
                raise CoreRunError("control_store_integrity_invalid")
            return
        evaluations = {item.evaluation_id: item for item in snapshot.gate_evaluations}
        batches = {item.gate_batch_id for item in evaluations.values()}
        if len(batches) != 1 or {
            item.gate_id for item in evaluations.values()
        } != set(GATE_IDS):
            raise CoreRunError("control_store_integrity_invalid")
        ordered_evaluations = sorted(
            evaluations.values(),
            key=lambda item: item.gate_id,
        )
        if len(ordered_evaluations) != len(GATE_IDS):
            raise CoreRunError("control_store_integrity_invalid")
        policy_version = f"{binding.policy_pack_name}:{binding.policy_pack_sha256[:16]}"
        report_refs = {
            (item.report_artifact.artifact_id, item.report_artifact.revision)
            for item in ordered_evaluations
        }
        event_ids = {item.evaluation_event_id for item in ordered_evaluations}
        request_ids = {
            item.accepted_transaction_id for item in ordered_evaluations
        }
        fingerprints = {
            item.request_fingerprint for item in ordered_evaluations
        }
        if (
            len(report_refs) != 1
            or len(event_ids) != 1
            or len(request_ids) != 1
            or len(fingerprints) != 1
            or any(
                item.policy_version != policy_version
                or item.run_contract_fingerprint != binding.contract_fingerprint
                or item.producer_implementation
                != "core-v2-preloaded-quality-gates"
                or item.producer_version != "1"
                for item in ordered_evaluations
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")

        findings = {
            (item.evaluation_id, item.finding_id): item
            for item in snapshot.gate_findings
        }
        ordered_findings: list[object] = []
        for evaluation in ordered_evaluations:
            selected = []
            for finding_id in evaluation.finding_ids:
                finding = findings.get((evaluation.evaluation_id, finding_id))
                if finding is None or finding.gate_id != evaluation.gate_id:
                    raise CoreRunError("control_store_integrity_invalid")
                selected.append(finding)
            expected_status = (
                evaluation.status in {"fail", "unavailable", "invalid"}
            )
            if evaluation.blocking != expected_status:
                raise CoreRunError("control_store_integrity_invalid")
            ordered_findings.extend(selected)
        if len(ordered_findings) != len(snapshot.gate_findings):
            raise CoreRunError("control_store_integrity_invalid")

        bindings_by_evaluation: dict[str, list[object]] = {}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        for artifact_binding in snapshot.gate_artifact_bindings:
            evaluation = evaluations.get(artifact_binding.evaluation_id)
            revision = revisions.get(
                (
                    artifact_binding.artifact_id,
                    artifact_binding.artifact_revision,
                )
            )
            if (
                evaluation is None
                or revision is None
                or revision.sha256 != artifact_binding.artifact_sha256
                or artifact_binding.accepted_transaction_id
                != evaluation.accepted_transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            bindings_by_evaluation.setdefault(
                artifact_binding.evaluation_id,
                [],
            ).append(artifact_binding)
        canonical_bindings: list[object] | None = None
        for evaluation in ordered_evaluations:
            selected = sorted(
                bindings_by_evaluation.get(evaluation.evaluation_id, []),
                key=lambda item: item.position,  # type: ignore[attr-defined]
            )
            if [item.position for item in selected] != list(range(len(selected))):
                raise CoreRunError("control_store_integrity_invalid")
            signature = [
                (
                    item.position,
                    item.artifact_id,
                    item.artifact_revision,
                    item.artifact_sha256,
                    item.usage,
                )
                for item in selected
            ]
            if canonical_bindings is None:
                canonical_bindings = signature
            elif signature != canonical_bindings:
                raise CoreRunError("control_store_integrity_invalid")
        if not canonical_bindings:
            raise CoreRunError("control_store_integrity_invalid")

        report_artifact_id, report_revision_number = next(iter(report_refs))
        try:
            report_bytes = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                report_artifact_id,
                report_revision_number,
            )
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        input_artifacts = [
            {
                "artifact_id": item[1],
                "revision": item[2],
                "sha256": item[3],
                "usage": item[4],
            }
            for item in canonical_bindings
        ]
        expected_report = {
            "schema_version": "briefloop.gate_report.v2",
            "run_id": snapshot.run.run_id,
            "stage_id": "auditor",
            "gate_batch_id": next(iter(batches)),
            "policy_version": policy_version,
            "run_contract_fingerprint": binding.contract_fingerprint,
            "input_artifacts": input_artifacts,
            "evaluations": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in ordered_evaluations
            ],
            "findings": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in ordered_findings
            ],
        }
        if report_bytes != canonical_json_bytes(expected_report) + b"\n":
            raise CoreRunError("control_store_integrity_invalid")

        try:
            from .gates import _gate_finding_record, _replay_gate_outcomes

            replayed = _replay_gate_outcomes(
                store,
                snapshot,
                binding,
                stages=tuple(dict(item) for item in contracts.stages),
                artifacts=tuple(dict(item) for item in contracts.artifacts),
            )
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        for evaluation in ordered_evaluations:
            forced_status, raw_findings = replayed[evaluation.gate_id]
            expected_findings = [
                _gate_finding_record(
                    run_id=snapshot.run.run_id,
                    evaluation_id=evaluation.evaluation_id,
                    gate_id=evaluation.gate_id,
                    position=position,
                    raw=raw,
                    accepted_transaction_id=evaluation.accepted_transaction_id,
                )
                for position, raw in enumerate(raw_findings, start=1)
            ]
            actual_findings = [
                findings[(evaluation.evaluation_id, finding_id)]
                for finding_id in evaluation.finding_ids
            ]
            if actual_findings != expected_findings:
                raise CoreRunError("control_store_integrity_invalid")
            replay_blocking = any(
                item.blocking_level == "blocking" for item in expected_findings
            )
            replay_status = (
                forced_status
                if forced_status is not None
                else (
                    "fail"
                    if replay_blocking
                    else ("warning" if expected_findings else "pass")
                )
            )
            if (
                evaluation.status != replay_status
                or evaluation.blocking != replay_blocking
            ):
                raise CoreRunError("control_store_integrity_invalid")


def _verified_core_receipt_binding(
    snapshot: ControlStoreSnapshot,
    receipt: TransactionReceipt,
) -> tuple[EventEnvelope, CoreRunEventBinding]:
    """Bind one core transaction to its exact effect and primary record."""

    events = {event.event_id: event for event in snapshot.events}
    bound = [
        events[event_id]
        for event_id in receipt.event_ids
        if event_id in events and events[event_id].core_run_binding is not None
    ]
    if len(bound) != 1:
        raise CoreRunError("control_store_integrity_invalid")
    event = bound[0]
    binding = event.core_run_binding
    if binding is None:
        raise CoreRunError("control_store_integrity_invalid")
    rule = _CORE_EFFECT_BINDING_RULES.get(binding.effect_kind)
    if (
        rule is None
        or receipt.run_id != snapshot.run.run_id
        or receipt.transaction_type != rule.transaction_type
        or event.event_type not in rule.event_types
        or binding.request_id != receipt.transaction_id
        or event.transaction_id != receipt.transaction_id
    ):
        raise CoreRunError("control_store_integrity_invalid")

    primary_id = binding.primary_record_id
    fingerprint = binding.request_fingerprint
    transaction_id = receipt.transaction_id

    if rule.primary_family == "run_contract_binding":
        refs = [item.run_id for item in receipt.run_contract_bindings]
        records = [
            item
            for item in snapshot.run_contract_bindings
            if item.run_id == primary_id
        ]
        if (
            refs != [primary_id]
            or primary_id != receipt.run_id
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].initialization_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "invocation":
        records = [
            item for item in snapshot.invocations if item.invocation_id == primary_id
        ]
        expected_fingerprint = (
            None
            if len(records) != 1 or event.stage_id is None
            else canonical_fingerprint(
                {
                    "schema_version": InvocationStartRequest.schema_id,
                    "request_id": transaction_id,
                    "run_id": receipt.run_id,
                    "stage_id": event.stage_id,
                    "role_id": records[0].role_id,
                    "runtime": records[0].runtime,
                    "expected_store_revision": receipt.prior_revision,
                }
            )
        )
        if (
            len(records) != 1
            or fingerprint != expected_fingerprint
            or primary_id != derived_id("INV", transaction_id, fingerprint)
            or records[0].run_id != receipt.run_id
            or event.stage_id is None
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family in {
        "owned_artifact_submission",
        "audit_submission",
    }:
        refs = [
            item.submission_id for item in receipt.owned_artifact_submissions
        ]
        records = [
            item
            for item in snapshot.owned_artifact_submissions
            if item.submission_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].accepted_event_id != event.event_id
            or (
                rule.primary_family == "audit_submission"
                and (
                    records[0].artifact_id != "audit_report"
                    or records[0].source_proposal_id is None
                )
            )
            or (
                rule.primary_family == "owned_artifact_submission"
                and records[0].artifact_id == "audit_report"
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "claim_freeze":
        refs = [item.freeze_id for item in receipt.claim_freezes]
        records = [
            item for item in snapshot.claim_freezes if item.freeze_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].freeze_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "gate_batch":
        evaluation_ids = [item.evaluation_id for item in receipt.gate_evaluations]
        records = [
            item
            for item in snapshot.gate_evaluations
            if item.evaluation_id in evaluation_ids
        ]
        if (
            not evaluation_ids
            or len(records) != len(evaluation_ids)
            or {item.gate_batch_id for item in records} != {primary_id}
            or any(
                item.accepted_transaction_id != transaction_id
                or item.request_fingerprint != fingerprint
                or item.evaluation_event_id != event.event_id
                for item in records
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "stage_transition":
        transition_ids = [item.transition_id for item in receipt.stage_transitions]
        records = [
            item
            for item in snapshot.stage_transitions
            if item.transition_id in transition_ids
        ]
        primary = [item for item in records if item.transition_id == primary_id]
        if (
            primary_id not in transition_ids
            or len(records) != len(transition_ids)
            or len(primary) != 1
            or primary[0].transition_event_id != event.event_id
            or any(
                item.accepted_transaction_id != transaction_id
                or item.request_fingerprint != fingerprint
                for item in records
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "run_integrity_record":
        try:
            integrity_revision = int(primary_id)
        except ValueError as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        refs = [item.integrity_revision for item in receipt.run_integrity_records]
        records = [
            item
            for item in snapshot.run_integrity_records
            if item.integrity_revision == integrity_revision
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        observation_fingerprint = canonical_fingerprint(
            {
                "run_id": record.run_id,
                "artifact_id": record.affected_artifact_id,
                "artifact_revision": record.affected_artifact_revision,
                "expected_workspace_path": record.expected_workspace_path,
                "expected_sha256": record.expected_sha256,
                "observed_entry_kind": record.observed_entry_kind,
                "observed_sha256": record.observed_sha256,
            }
        )
        if (
            refs != [integrity_revision]
            or record.status != "contaminated"
            or record.accepted_transaction_id != transaction_id
            or record.first_detected_event_id != event.event_id
            or record.request_fingerprint != observation_fingerprint
        ):
            raise CoreRunError("control_store_integrity_invalid")
    else:  # pragma: no cover - the frozen table exhausts this branch.
        raise CoreRunError("control_store_integrity_invalid")
    return event, binding


def resolve_core_replay(
    store: SQLiteControlStore,
    *,
    run_id: str,
    request_id: str,
    request_fingerprint: str,
) -> CoreRunResult | None:
    """Return one exact receipt-owned replay before current-state checks."""

    try:
        receipt = store.load_transaction_receipt(run_id, request_id)
    except Exception as exc:
        raise CoreRunError("control_store_integrity_invalid") from exc
    if receipt is None:
        return None
    try:
        snapshot = store.load_snapshot(run_id)
    except Exception as exc:
        raise CoreRunError("control_store_integrity_invalid") from exc
    event, binding = _verified_core_receipt_binding(snapshot, receipt)
    if binding.request_id != request_id:
        raise CoreRunError("control_store_integrity_invalid")
    if binding.request_fingerprint != request_fingerprint:
        raise CoreRunError("submission_replay_conflict")
    if binding.outcome == "blocked":
        return CoreRunResult(
            status="blocked",
            receipt=receipt,
            error_code=event.reason or "core_run_integrity_blocked",
            primary_record_id=binding.primary_record_id,
        )
    return CoreRunResult(
        status="replayed",
        receipt=receipt,
        primary_record_id=binding.primary_record_id,
    )


__all__ = [
    "CoreRunDomainVerifier",
    "VerifiedCoreRun",
    "resolve_core_replay",
]
