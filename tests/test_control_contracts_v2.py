"""Strict v2 contract inventory, validation, and legacy-read tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from multi_agent_brief.contracts import (
    ContractError,
    LEGACY_READ_ONLY_CONTRACTS,
    SchemaRegistry,
    StrictModel,
    V2_CONTRACT_IDS,
    V2_CONTRACT_MODELS,
    read_contract_payload,
)


EXPECTED_V2_CONTRACT_IDS = (
    "briefloop.source_proposal.v2",
    "briefloop.candidate_claims_proposal.v2",
    "briefloop.screened_candidates_proposal.v2",
    "briefloop.claim_drafts_proposal.v2",
    "briefloop.audit_proposal.v2",
    "briefloop.artifact_submit_request.v2",
    "briefloop.run_identity.v2",
    "briefloop.stage_state.v2",
    "briefloop.artifact_record.v2",
    "briefloop.artifact_revision.v2",
    "briefloop.event_envelope.v2",
    "briefloop.invocation.v2",
    "briefloop.approval.v2",
    "briefloop.delivery.v2",
    "briefloop.transaction_receipt.v2",
)


def test_v2_contract_inventory_is_exact_and_uses_existing_registry() -> None:
    assert V2_CONTRACT_IDS == EXPECTED_V2_CONTRACT_IDS
    assert len(V2_CONTRACT_MODELS) == 15
    assert len(set(V2_CONTRACT_IDS)) == 15
    for contract_id, model in zip(V2_CONTRACT_IDS, V2_CONTRACT_MODELS):
        assert SchemaRegistry.get(contract_id) is model


def test_strict_model_contract_is_strict_and_forbids_extra_fields() -> None:
    config = StrictModel.model_config
    assert config["strict"] is True
    assert config["extra"] == "forbid"


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


@pytest.mark.parametrize(
    ("contract_id", "field", "invalid_value", "expected_error"),
    [
        ("briefloop.artifact_submit_request.v2", "size_bytes", "128", "must be an integer"),
        ("briefloop.artifact_record.v2", "required", 1, "must be a boolean"),
        ("briefloop.stage_state.v2", "status", "done", "must be one of the allowed values"),
        ("briefloop.run_identity.v2", "created_at", "July 14", "is invalid"),
        ("briefloop.run_identity.v2", "runtime", "Operator", "must be one of the allowed values"),
        ("briefloop.artifact_submit_request.v2", "sha256", "not-a-hash", "has invalid format"),
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
    invalid_url = SchemaRegistry.example(contract_id, "minimal")
    invalid_url["locator"]["url"] = "not a URL"
    assert [(item.field, item.error) for item in SchemaRegistry.validate(contract_id, invalid_url)] == [
        ("locator.web.url", "must be a valid URL")
    ]

    unknown_kind = SchemaRegistry.example(contract_id, "minimal")
    unknown_kind["locator"] = {"kind": "database", "url": "https://example.com"}
    assert [(item.field, item.error) for item in SchemaRegistry.validate(contract_id, unknown_kind)] == [
        ("locator", "has an unsupported discriminator")
    ]


def test_transaction_receipt_rejects_completion_before_start() -> None:
    contract_id = "briefloop.transaction_receipt.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    payload["completed_at"] = "2026-07-14T08:59:59Z"

    assert [(item.field, item.error) for item in SchemaRegistry.validate(contract_id, payload)] == [
        ("$", "is invalid")
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


def test_legacy_inventory_is_exact_and_each_result_is_read_only() -> None:
    assert tuple(LEGACY_READ_ONLY_CONTRACTS) == (
        "source_item",
        "candidate_claims",
        "screened_candidates",
        "claim_drafts",
        "audit_report",
        "artifact_submit_request",
        "runtime_manifest",
        "workflow_state",
        "artifact_registry_record",
        "artifact_revision",
        "event_log_event",
        "runtime_invocation",
        "human_approval",
        "delivery_record",
        "transaction_receipt",
    )
    for legacy_id, canonical_id in LEGACY_READ_ONLY_CONTRACTS.items():
        result = read_contract_payload(legacy_id, {"legacy": [1, {"ok": True}]})
        assert result.classification == "legacy_read_only"
        assert result.requested_schema_id == legacy_id
        assert result.canonical_schema_id == canonical_id
        assert result.canonical_model is None
        assert result.can_write is False
        assert isinstance(result.legacy_payload, MappingProxyType)
        assert result.legacy_payload["legacy"] == (1, MappingProxyType({"ok": True}))
        with pytest.raises(TypeError):
            result.legacy_payload["new"] = "forbidden"
        with pytest.raises(FrozenInstanceError):
            result.classification = "canonical_v2"


def test_canonical_v2_read_returns_model_but_wrong_version_never_becomes_legacy() -> None:
    contract_id = "briefloop.run_identity.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")

    canonical = read_contract_payload(contract_id, payload)
    assert canonical.classification == "canonical_v2"
    assert canonical.can_write is True
    assert canonical.canonical_model is not None
    assert canonical.legacy_payload is None

    payload["schema_version"] = "briefloop.run_identity.v1"
    wrong_version = read_contract_payload(contract_id, payload)
    assert wrong_version.classification == "invalid"
    assert wrong_version.can_write is False
    assert wrong_version.canonical_model is None
    assert wrong_version.legacy_payload is None
    assert [(item.field, item.error) for item in wrong_version.violations] == [
        ("schema_version", "must be one of the allowed values")
    ]


def test_unknown_or_non_json_legacy_payload_is_invalid_and_value_free() -> None:
    unknown = read_contract_payload("briefloop.unknown.v2", {})
    assert unknown.classification == "invalid"
    assert unknown.can_write is False
    assert [(item.field, item.error) for item in unknown.violations] == [
        ("schema_id", "unknown v2 contract")
    ]

    invalid_legacy = read_contract_payload("runtime_manifest", {"bad": object()})
    assert invalid_legacy.classification == "invalid"
    assert invalid_legacy.can_write is False
    assert [(item.field, item.error) for item in invalid_legacy.violations] == [
        ("$", "must contain JSON-compatible values")
    ]


def test_legacy_contract_class_remains_registered_and_compatible() -> None:
    from multi_agent_brief.contracts.schemas.source_item import SourceItemContract

    assert SchemaRegistry.get("source_item") is SourceItemContract
    assert SchemaRegistry.validate(
        "source_item",
        {
            "source_id": "S1",
            "source_name": "Test",
            "source_type": "local_file",
            "title": "Title",
            "content": "Body",
        },
    ) == []
