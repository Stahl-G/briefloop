"""Strict v2 contract inventory, validation, and legacy-read tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
from types import MappingProxyType
from typing import get_args

import pytest
from pydantic import ValidationError

from multi_agent_brief.contracts import (
    ArtifactIdentityRecord,
    ArtifactIdentityReference,
    ContractError,
    LEGACY_READ_ONLY_CONTRACTS,
    SchemaRegistry,
    StrictModel,
    V2_CONTRACT_IDS,
    V2_CONTRACT_MODELS,
    read_contract_payload,
)
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES


EXPECTED_V2_CONTRACT_IDS = (
    "briefloop.source_proposal.v2",
    "briefloop.source_commit_request.v2",
    "briefloop.candidate_claims_proposal.v2",
    "briefloop.screened_candidates_proposal.v2",
    "briefloop.claim_drafts_proposal.v2",
    "briefloop.audit_proposal.v2",
    "briefloop.artifact_submit_request.v2",
    "briefloop.workspace_run_head.v2",
    "briefloop.accepted_source_record.v2",
    "briefloop.accepted_proposal_record.v2",
    "briefloop.proposal_source_binding.v2",
    "briefloop.run_identity.v2",
    "briefloop.stage_state.v2",
    "briefloop.artifact_record.v2",
    "briefloop.artifact_identity_record.v2",
    "briefloop.artifact_revision.v2",
    "briefloop.event_envelope.v2",
    "briefloop.invocation.v2",
    "briefloop.approval.v2",
    "briefloop.delivery.v2",
    "briefloop.transaction_receipt.v2",
    "briefloop.run_direction.v2",
    "briefloop.workspace_controlstore_bootstrap.v2",
    "briefloop.runtime_adapter_binding.v2",
    "briefloop.runtime_web_search_request_spec.v2",
    "briefloop.runtime_web_search_acquisition_spec.v2",
    "briefloop.runtime_cached_package_acquisition_spec.v2",
    "briefloop.runtime_newsapi_acquisition_spec.v2",
    "briefloop.runtime_source_route_binding.v2",
    "briefloop.runtime_source_plan_binding.v2",
    "briefloop.core_run_next_action.v2",
    "briefloop.core_run_initialize_request.v2",
    "briefloop.run_contract_binding.v2",
    "briefloop.invocation_start_request.v2",
    "briefloop.invocation_failure_request.v2",
    "briefloop.owned_artifact_submit_request.v2",
    "briefloop.owned_artifact_submission_record.v2",
    "briefloop.claim_record.v2",
    "briefloop.claim_source_binding.v2",
    "briefloop.claim_freeze_record.v2",
    "briefloop.claim_freeze_request.v2",
    "briefloop.stage_transition_record.v2",
    "briefloop.stage_artifact_binding.v2",
    "briefloop.stage_gate_binding.v2",
    "briefloop.stage_complete_request.v2",
    "briefloop.gate_finding_record.v2",
    "briefloop.gate_evaluation_record.v2",
    "briefloop.gate_artifact_binding.v2",
    "briefloop.gate_check_request.v2",
    "briefloop.audit_promotion_request.v2",
    "briefloop.audit_report_artifact.v2",
    "briefloop.run_integrity_record.v2",
    "briefloop.integrity_check_request.v2",
    "briefloop.repair_cycle_record.v2",
    "briefloop.artifact_supersession_record.v2",
    "briefloop.repair_completion_record.v2",
    "briefloop.recovery_completion_record.v2",
    "briefloop.run_head_transition_record.v2",
    "briefloop.finalize_render_record.v2",
    "briefloop.finalization_record.v2",
    "briefloop.run_archive_record.v2",
    "briefloop.run_archive_artifact_binding.v2",
    "briefloop.package_ready_record.v2",
    "briefloop.package_artifact_binding.v2",
    "briefloop.approval_package_binding.v2",
    "briefloop.delivery_authorization_record.v2",
    "briefloop.delivery_attempt_record.v2",
    "briefloop.delivery_result_record.v2",
    "briefloop.delivery_result_observation.v2",
    "briefloop.repair_start_request.v2",
    "briefloop.artifact_supersede_request.v2",
    "briefloop.artifact_revert_request.v2",
    "briefloop.repair_complete_request.v2",
    "briefloop.recovery_complete_request.v2",
    "briefloop.run_reset_request.v2",
    "briefloop.finalize_render_request.v2",
    "briefloop.finalize_complete_request.v2",
    "briefloop.internal_approval_request.v2",
    "briefloop.delivery_authorization_request.v2",
    "briefloop.delivery_attempt_request.v2",
    "briefloop.delivery_result_request.v2",
    "briefloop.checkout_revision.v2",
    "briefloop.checkout_revision_member.v2",
    "briefloop.receipt_checkout_binding.v2",
    "briefloop.publication_identity.v1",
    "briefloop.checkout_publication_intent.v2",
    "briefloop.checkout_publication_member.v2",
    "briefloop.checkout_publication_ack.v2",
    "briefloop.checkout_publication_cleanup_observation.v2",
)


def test_v2_contract_inventory_is_exact_and_uses_existing_registry() -> None:
    assert V2_CONTRACT_IDS == EXPECTED_V2_CONTRACT_IDS
    assert len(V2_CONTRACT_MODELS) == 89
    assert len(set(V2_CONTRACT_IDS)) == 89
    for contract_id, model in zip(V2_CONTRACT_IDS, V2_CONTRACT_MODELS):
        assert SchemaRegistry.get(contract_id) is model


def test_strict_model_contract_is_strict_and_forbids_extra_fields() -> None:
    config = StrictModel.model_config
    assert config["strict"] is True
    assert config["extra"] == "forbid"
    assert config["allow_inf_nan"] is False


@pytest.mark.parametrize("model", V2_CONTRACT_MODELS, ids=V2_CONTRACT_IDS)
@pytest.mark.parametrize("detail", ("minimal", "full"))
def test_every_embedded_example_is_valid_and_published_in_schema(model, detail) -> None:
    example = SchemaRegistry.example(model.schema_id, detail)

    assert SchemaRegistry.validate(model.schema_id, example) == []
    schema = SchemaRegistry.json_schema(model.schema_id)
    assert schema["$id"] == model.schema_id
    assert schema["examples"][0] == SchemaRegistry.example(model.schema_id, "minimal")
    assert schema["examples"][1] == SchemaRegistry.example(model.schema_id, "full")
    assert schema["additionalProperties"] is False


def test_exported_schema_carries_the_constraints_used_by_after_validators() -> None:
    source_schema = SchemaRegistry.json_schema("briefloop.source_proposal.v2")
    source_properties = source_schema["properties"]
    assert source_properties["title"]["minLength"] == 1
    assert source_properties["title"]["pattern"] == r"^\S(?:[\s\S]*\S)?$"
    assert source_properties["retrieved_at"] == {
        "format": "date-time",
        "pattern": r"^\d{4}-\d{2}-\d{2}T[\s\S]*(?:Z|[+-]\d{2}:\d{2})$",
        "title": "Retrieved At",
        "type": "string",
    }
    published_schema = source_properties["published_at"]["anyOf"][0]
    assert published_schema["format"] == "date"
    assert published_schema["pattern"] == r"^\d{4}-\d{2}-\d{2}$"

    file_path_schema = source_schema["$defs"]["FileSourceLocator"]["properties"]["path"]
    submit_path_schema = SchemaRegistry.json_schema(
        "briefloop.artifact_submit_request.v2"
    )["properties"]["input_path"]
    assert file_path_schema["minLength"] == 1
    assert "(?!/)" in file_path_schema["pattern"]
    assert "\\.{1,2}" in file_path_schema["pattern"]
    assert submit_path_schema["minLength"] == 1
    assert submit_path_schema["pattern"].startswith("^scratch/")
    assert "(?:json|md)" in submit_path_schema["pattern"]


@pytest.mark.parametrize(
    ("contract_id", "field", "invalid_value", "expected_error"),
    [
        (
            "briefloop.artifact_submit_request.v2",
            "expected_store_revision",
            "1",
            "must be an integer",
        ),
        ("briefloop.artifact_record.v2", "required", 1, "must be a boolean"),
        (
            "briefloop.stage_state.v2",
            "status",
            "done",
            "must be one of the allowed values",
        ),
        ("briefloop.run_identity.v2", "created_at", "July 14", "is invalid"),
        (
            "briefloop.run_identity.v2",
            "runtime",
            "Operator",
            "must be one of the allowed values",
        ),
        (
            "briefloop.artifact_revision.v2",
            "sha256",
            "not-a-hash",
            "has invalid format",
        ),
    ],
)
def test_strict_type_enum_and_date_failures_are_stable(
    contract_id: str,
    field: str,
    invalid_value: object,
    expected_error: str,
) -> None:
    payload = SchemaRegistry.example(contract_id, "minimal")
    payload[field] = invalid_value

    violations = SchemaRegistry.validate(contract_id, payload)

    assert [(item.field, item.error, item.severity) for item in violations] == [
        (field, expected_error, "error")
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("web_search_mode", "automatic"),
        ("search_backend", "custom"),
    ],
)
def test_run_direction_reuses_the_existing_search_mode_contract(
    field: str,
    value: str,
) -> None:
    payload = SchemaRegistry.example("briefloop.run_direction.v2", "minimal")
    payload[field] = value
    if field == "search_backend":
        payload["web_search_mode"] = "external_api"

    assert [item.field for item in SchemaRegistry.validate(
        "briefloop.run_direction.v2",
        payload,
    )] == [field]


def test_extra_field_error_is_value_free_and_does_not_expose_pydantic_message() -> None:
    contract_id = "briefloop.run_identity.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    secret = "DO-NOT-EXPOSE-THIS-VALUE"
    payload["attacker_extra"] = secret

    violations = SchemaRegistry.validate(contract_id, payload)
    rendered = "\n".join(str(item) for item in violations)

    assert [(item.field, item.error) for item in violations] == [
        ("attacker_extra", "extra field is not permitted")
    ]
    assert secret not in rendered
    assert "Extra inputs are not permitted" not in rendered
    assert "errors.pydantic.dev" not in rendered
    assert "('attacker_extra',)" not in rendered

    with pytest.raises(ContractError) as exc:
        SchemaRegistry.validate_or_raise(contract_id, payload)
    assert exc.value.schema_id == contract_id
    assert exc.value.schema_version == "2"
    assert secret not in str(exc.value)


def test_discriminated_source_locator_rejects_invalid_url_and_unknown_kind() -> None:
    contract_id = "briefloop.source_proposal.v2"
    invalid_url = SchemaRegistry.example(contract_id, "full")
    invalid_url["locator"]["url"] = "not a URL"
    assert [
        (item.field, item.error)
        for item in SchemaRegistry.validate(contract_id, invalid_url)
    ] == [("locator.web.url", "must be a valid URL")]

    unknown_kind = SchemaRegistry.example(contract_id, "minimal")
    unknown_kind["locator"] = {"kind": "database", "url": "https://example.com"}
    assert [
        (item.field, item.error)
        for item in SchemaRegistry.validate(contract_id, unknown_kind)
    ] == [("locator", "has an unsupported discriminator")]


def test_artifact_submit_request_binds_invocation_scratch_input_and_precondition() -> (
    None
):
    contract_id = "briefloop.artifact_submit_request.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    assert set(payload) == {
        "schema_version",
        "request_id",
        "run_id",
        "artifact_id",
        "invocation_id",
        "input_path",
        "expected_store_revision",
        "expected_artifact_revision",
    }

    for invalid_path, expected_field in (
        ("output/intermediate/candidate_claims.json", "input_path"),
        ("scratch/INV-OTHER/candidate_claims.json", "$"),
        ("scratch/INV-SCOUT-001/other.json", "$"),
        ("scratch/INV-SCOUT-001/candidate_claims.md", "$"),
    ):
        invalid = dict(payload)
        invalid["input_path"] = invalid_path
        assert [
            (item.field, item.error)
            for item in SchemaRegistry.validate(contract_id, invalid)
        ] == [(expected_field, "is invalid")]

    for derived_field in ("stage_id", "format", "sha256", "size_bytes", "submitted_at"):
        invalid = dict(payload)
        invalid[derived_field] = "agent-supplied"
        assert [
            (item.field, item.error)
            for item in SchemaRegistry.validate(contract_id, invalid)
        ] == [(derived_field, "extra field is not permitted")]


def test_source_commit_request_paths_are_exactly_invocation_scoped() -> None:
    contract_id = "briefloop.source_commit_request.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    assert SchemaRegistry.validate(contract_id, payload) == []
    for field, value in (
        ("proposal_path", "scratch/INV-OTHER/source_proposal.json"),
        ("proposal_path", "scratch/INV-SOURCE-001/other.json"),
        ("content_path", "scratch/INV-SOURCE-001/source_content.exe"),
        ("raw_payload_path", "scratch/INV-SOURCE-001/source_raw.pdf"),
    ):
        invalid = {**payload, field: value}
        assert SchemaRegistry.validate(contract_id, invalid)


def test_source_proposal_has_no_generic_metadata_escape_hatch() -> None:
    contract_id = "briefloop.source_proposal.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    payload["metadata"] = {"claims_eligible": True}
    assert [
        (item.field, item.error)
        for item in SchemaRegistry.validate(contract_id, payload)
    ] == [("metadata", "extra field is not permitted")]


def test_receipt_identity_source_and_proposal_relations_are_unique() -> None:
    contract_id = "briefloop.transaction_receipt.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    for field in ("source_ids", "proposal_ids"):
        invalid = {**payload, field: ["IDENTITY-001", "IDENTITY-001"]}
        assert SchemaRegistry.validate(contract_id, invalid)
    invalid = {
        **payload,
        "artifact_identities": [
            {"artifact_id": "artifact-a"},
            {"artifact_id": "artifact-a"},
        ],
    }
    assert SchemaRegistry.validate(contract_id, invalid)


def test_artifact_identity_record_and_reference_are_exact_strict_contracts() -> None:
    payload = SchemaRegistry.example(ArtifactIdentityRecord.schema_id, "minimal")
    assert ArtifactIdentityRecord.model_validate(payload, strict=True)
    for field in payload:
        invalid = dict(payload)
        invalid.pop(field)
        with pytest.raises(ValidationError):
            ArtifactIdentityRecord.model_validate(invalid, strict=True)
    for field, value in (
        ("required", 1),
        ("initial_path", "/absolute/path.json"),
        ("format", "md"),
        ("accepted_transaction_id", 7),
    ):
        with pytest.raises(ValidationError):
            ArtifactIdentityRecord.model_validate(
                {**payload, field: value},
                strict=True,
            )
    with pytest.raises(ValidationError):
        ArtifactIdentityRecord.model_validate(
            {**payload, "media_type": "application/json"},
            strict=True,
        )
    assert ArtifactIdentityReference.model_validate(
        {"artifact_id": "artifact-a"},
        strict=True,
    ).artifact_id == "artifact-a"
    for invalid in ({}, {"artifact_id": 1}, {"artifact_id": "artifact-a", "x": 1}):
        with pytest.raises(ValidationError):
            ArtifactIdentityReference.model_validate(invalid, strict=True)






def test_intake_event_types_require_exact_typed_binding() -> None:
    contract_id = "briefloop.event_envelope.v2"
    base = SchemaRegistry.example(contract_id, "minimal")
    common = {
        "request_id": "REQ-001",
        "request_fingerprint": "a" * 64,
        "invocation_id": "INV-001",
        "reason_code": None,
    }
    source = {
        **base,
        "event_type": "source_evidence_committed",
        "intake_binding": {
            **common,
            "outcome": "committed",
            "source_id": "SRC-001",
            "proposal_id": None,
        },
    }
    proposal = {
        **base,
        "event_type": "role_proposal_committed",
        "intake_binding": {
            **common,
            "outcome": "committed",
            "source_id": None,
            "proposal_id": "PROP-001",
        },
    }
    rejection = {
        **base,
        "event_type": "intake_rejected",
        "intake_binding": {
            **common,
            "outcome": "rejected",
            "source_id": None,
            "proposal_id": None,
            "reason_code": "proposal_contract_invalid",
        },
    }
    assert SchemaRegistry.validate(contract_id, source) == []
    assert SchemaRegistry.validate(contract_id, proposal) == []
    assert SchemaRegistry.validate(contract_id, rejection) == []
    assert SchemaRegistry.validate(
        contract_id,
        {**source, "intake_binding": None},
    )
    assert SchemaRegistry.validate(
        contract_id,
        {**base, "intake_binding": source["intake_binding"]},
    )




@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
@pytest.mark.parametrize(
    "contract_id",
    ("briefloop.source_proposal.v2", "briefloop.event_envelope.v2"),
)
def test_nested_non_finite_json_values_are_rejected_value_free(
    contract_id: str,
    value: float,
) -> None:
    payload = SchemaRegistry.example(contract_id, "minimal")
    payload["metadata"] = {"nested": [value]}

    assert [
        (item.field, item.error)
        for item in SchemaRegistry.validate(contract_id, payload)
    ] == [("$", "must contain only finite JSON numbers")]


def test_transaction_receipt_requires_revision_advance() -> None:
    contract_id = "briefloop.transaction_receipt.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    payload["prior_revision"] = 1
    payload["committed_revision"] = 1

    assert [
        (item.field, item.error)
        for item in SchemaRegistry.validate(contract_id, payload)
    ] == [("$", "is invalid")]


def test_run_integrity_contract_distinguishes_initial_and_recovered_clean() -> None:
    contract_id = "briefloop.run_integrity_record.v2"
    initial = SchemaRegistry.example(contract_id, "minimal")
    assert SchemaRegistry.validate(contract_id, initial) == []

    recovered = {
        **initial,
        "integrity_revision": 3,
        "prior_integrity_revision": 2,
    }
    assert SchemaRegistry.validate(contract_id, recovered) == []

    for invalid in (
        {**recovered, "prior_integrity_revision": 1},
        {**recovered, "reason_code": "frozen_artifact_contaminated"},
        {**initial, "integrity_revision": 2},
    ):
        assert [
            (item.field, item.error)
            for item in SchemaRegistry.validate(contract_id, invalid)
        ] == [("$", "is invalid")]


def test_control_dto_examples_cover_required_revision_and_identity_bindings() -> None:
    revision = SchemaRegistry.example("briefloop.artifact_revision.v2", "minimal")
    assert revision["path"].startswith("output/artifacts/")
    assert revision["frozen"] is True

    invocation = SchemaRegistry.example("briefloop.invocation.v2", "full")
    assert invocation["role_id"] == "scout"
    assert invocation["runtime"] in VALID_RUNTIMES

    receipt = SchemaRegistry.example("briefloop.transaction_receipt.v2", "full")
    assert receipt["committed_revision"] > receipt["prior_revision"]
    assert receipt["artifact_revisions"] == [
        {"artifact_id": "candidate_claims", "revision": 1}
    ]
    assert receipt["artifact_identities"] == [
        {"artifact_id": "candidate_claims"}
    ]


def test_nested_error_path_uses_stable_briefloop_format() -> None:
    contract_id = "briefloop.candidate_claims_proposal.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    payload["candidates"][0]["confidence"] = "certain"

    violations = SchemaRegistry.validate(contract_id, payload)

    assert [(item.field, item.error) for item in violations] == [
        ("candidates[0].confidence", "must be one of the allowed values")
    ]


def test_local_identity_duplicates_fail_without_migrating_business_authority() -> None:
    contract_id = "briefloop.candidate_claims_proposal.v2"
    payload = SchemaRegistry.example(contract_id, "full")
    payload["candidates"][1]["candidate_id"] = payload["candidates"][0]["candidate_id"]

    violations = SchemaRegistry.validate(contract_id, payload)

    assert [(item.field, item.error) for item in violations] == [("$", "is invalid")]


def test_legacy_inventory_is_exact_and_each_result_is_opaque_read_only() -> None:
    assert tuple(LEGACY_READ_ONLY_CONTRACTS) == (
        "analysis_card",
        "atomic_claim_graph",
        "audit_report",
        "candidate_claims",
        "candidate_item",
        "claim",
        "claim_drafts",
        "claim_support_matrix",
        "evidence_span_registry",
        "market_event",
        "policy_profile",
        "report_spec",
        "screened_candidates",
        "semantic_assessment_report",
        "source_evidence_pack_manifest",
        "source_item",
    )
    for legacy_id in LEGACY_READ_ONLY_CONTRACTS:
        result = read_contract_payload(legacy_id, {"legacy": [1, {"ok": True}]})
        assert result.classification == "opaque_legacy_read_only"
        assert result.requested_schema_id == legacy_id
        assert result.canonical_model is None
        assert not hasattr(result, "canonical_schema_id")
        assert not hasattr(result, "can_write")
        assert isinstance(result.legacy_payload, MappingProxyType)
        assert result.legacy_payload["legacy"] == (1, MappingProxyType({"ok": True}))
        with pytest.raises(TypeError):
            result.legacy_payload["new"] = "forbidden"
        with pytest.raises(FrozenInstanceError):
            result.classification = "canonical_v2"


def test_canonical_v2_read_returns_model_but_wrong_version_never_becomes_legacy() -> (
    None
):
    contract_id = "briefloop.run_identity.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")

    canonical = read_contract_payload(contract_id, payload)
    assert canonical.classification == "canonical_v2"
    assert canonical.canonical_model is not None
    assert canonical.legacy_payload is None
    assert not hasattr(canonical, "can_write")

    canonical.canonical_model.runtime = "auto"
    assert [
        (item.field, item.error)
        for item in SchemaRegistry.validate(
            contract_id,
            canonical.canonical_model.model_dump(),
        )
    ] == [("runtime", "must be one of the allowed values")]

    payload["schema_version"] = "briefloop.run_identity.v1"
    wrong_version = read_contract_payload(contract_id, payload)
    assert wrong_version.classification == "invalid"
    assert wrong_version.canonical_model is None
    assert wrong_version.legacy_payload is None
    assert [(item.field, item.error) for item in wrong_version.violations] == [
        ("schema_version", "must be one of the allowed values")
    ]


def test_unknown_or_non_json_legacy_payload_is_invalid_and_value_free() -> None:
    unknown = read_contract_payload("briefloop.unknown.v2", {})
    assert unknown.classification == "invalid"
    assert [(item.field, item.error) for item in unknown.violations] == [
        ("schema_id", "unknown v2 contract")
    ]

    invalid_legacy = read_contract_payload("source_item", {"bad": object()})
    assert invalid_legacy.classification == "invalid"
    assert [(item.field, item.error) for item in invalid_legacy.violations] == [
        ("$", "must contain finite JSON-compatible values")
    ]

    non_finite_legacy = read_contract_payload("source_item", {"bad": math.nan})
    assert non_finite_legacy.classification == "invalid"
    assert [(item.field, item.error) for item in non_finite_legacy.violations] == [
        ("$", "must contain finite JSON-compatible values")
    ]


def test_legacy_contract_class_remains_registered_and_compatible() -> None:
    from multi_agent_brief.contracts.schemas.source_item import SourceItemContract

    assert SchemaRegistry.get("source_item") is SourceItemContract
    assert (
        SchemaRegistry.validate(
            "source_item",
            {
                "source_id": "S1",
                "source_name": "Test",
                "source_type": "local_file",
                "title": "Title",
                "content": "Body",
            },
        )
        == []
    )
