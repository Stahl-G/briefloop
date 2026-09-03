"""Strict v2 contract validation and legacy-read tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
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


def test_source_proposal_has_no_generic_metadata_escape_hatch() -> None:
    contract_id = "briefloop.source_proposal.v2"
    payload = SchemaRegistry.example(contract_id, "minimal")
    payload["metadata"] = {"claims_eligible": True}
    assert [
        (item.field, item.error)
        for item in SchemaRegistry.validate(contract_id, payload)
    ] == [("metadata", "extra field is not permitted")]


def test_legacy_inventory_is_exact_and_each_result_is_opaque_read_only() -> None:
    assert tuple(LEGACY_READ_ONLY_CONTRACTS) == (
        "atomic_claim_graph",
        "audit_report",
        "candidate_claims",
        "claim",
        "claim_drafts",
        "claim_support_matrix",
        "evidence_span_registry",
        "policy_profile",
        "report_spec",
        "screened_candidates",
        "semantic_assessment_report",
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

    invalid_legacy = read_contract_payload("claim", {"bad": object()})
    assert invalid_legacy.classification == "invalid"
    assert [(item.field, item.error) for item in invalid_legacy.violations] == [
        ("$", "must contain finite JSON-compatible values")
    ]

    non_finite_legacy = read_contract_payload("claim", {"bad": math.nan})
    assert non_finite_legacy.classification == "invalid"
    assert [(item.field, item.error) for item in non_finite_legacy.violations] == [
        ("$", "must contain finite JSON-compatible values")
    ]
