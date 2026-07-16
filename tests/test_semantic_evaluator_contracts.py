"""Strict contract inventory and local-registry isolation."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from multi_agent_brief.contracts.base import SchemaRegistry
from multi_agent_brief.contracts.errors import pydantic_error_violations
from multi_agent_brief.contracts.v2 import V2_CONTRACT_IDS, V2_CONTRACT_MODELS
from multi_agent_brief.semantic_evaluator.contracts import (
    SEMANTIC_EVALUATOR_CONTRACT_IDS,
    SEMANTIC_EVALUATOR_CONTRACT_MODELS,
    AttemptRef,
    InputBinding,
)
from multi_agent_brief.semantic_evaluator.errors import (
    ADMISSION_REASON_CODES,
    PARSER_REASON_CODES,
    VALIDATION_REASON_CODES,
)
from multi_agent_brief.semantic_evaluator.profile import (
    FROZEN_PROFILE_SHA256,
    load_profile,
)


EXPECTED_IDS = (
    "briefloop.semantic_evaluator.reader_artifact.v1",
    "briefloop.semantic_evaluator.bounded_context.v1",
    "briefloop.semantic_evaluator.profile.v1",
    "briefloop.semantic_evaluator.instrument_config.v1",
    "briefloop.semantic_evaluator.admission_request.v1",
    "briefloop.semantic_evaluator.instrument_manifest.v1",
    "briefloop.semantic_evaluator.input_binding.v1",
    "briefloop.semantic_evaluator.assessment_plan.v1",
    "briefloop.semantic_evaluator.dimension_response.v1",
    "briefloop.semantic_evaluator.run.v1",
    "briefloop.semantic_evaluator.validation_report.v1",
    "briefloop.semantic_evaluator.event.v1",
    "briefloop.semantic_evaluator.laj_composition_witness.v1",
    "briefloop.semantic_evaluator.baseline.v1",
    "briefloop.semantic_evaluator.composition.v1",
    "briefloop.semantic_evaluator.presentation.v1",
)


def test_contract_inventory_is_exact_local_and_non_colliding() -> None:
    assert SEMANTIC_EVALUATOR_CONTRACT_IDS == EXPECTED_IDS
    assert len(SEMANTIC_EVALUATOR_CONTRACT_MODELS) == 16
    assert not set(SEMANTIC_EVALUATOR_CONTRACT_IDS) & set(V2_CONTRACT_IDS)
    assert all(
        item.startswith("briefloop.semantic_evaluator.") for item in EXPECTED_IDS
    )
    assert all("semantic_assessment_report" not in item for item in EXPECTED_IDS)
    assert tuple(model.schema_id for model in V2_CONTRACT_MODELS) == V2_CONTRACT_IDS
    assert all(SchemaRegistry.get(item) is None for item in EXPECTED_IDS)


@pytest.mark.parametrize(
    "model", SEMANTIC_EVALUATOR_CONTRACT_MODELS, ids=SEMANTIC_EVALUATOR_CONTRACT_IDS
)
def test_all_contract_examples_and_json_schemas_are_strict(model) -> None:
    model.model_validate(deepcopy(model.minimal_example))
    model.model_validate(deepcopy(model.full_example))
    schema = model.contract_json_schema()
    assert schema["$id"] == model.schema_id
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == model.schema_id
    assert model.model_config["strict"] is True
    assert model.model_config["extra"] == "forbid"
    assert model.model_config["validate_default"] is True
    assert model.model_config["allow_inf_nan"] is False


def test_strict_errors_are_value_free_and_do_not_coerce() -> None:
    payload = deepcopy(InputBinding.minimal_example)
    secret = "PRIVATE-SYNTHETIC-CANARY"
    payload["public_data_attestation"] = 1
    payload["attacker_extra"] = secret
    with pytest.raises(ValidationError) as caught:
        InputBinding.model_validate(payload)
    violations = pydantic_error_violations(caught.value)
    assert [(item.field, item.error) for item in violations] == [
        ("attacker_extra", "extra field is not permitted"),
        ("public_data_attestation", "must be a boolean"),
    ]
    assert secret not in "\n".join(str(item) for item in violations)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "attempt_ref": "attempt-invalid-failed",
            "dimension_id": "cross_section_consistency",
            "attempt_ordinal": 1,
            "prompt_request_sha256": "0" * 64,
            "status": "failed",
            "reason_code": None,
        },
        {
            "attempt_ref": "attempt-invalid-completed",
            "dimension_id": "cross_section_consistency",
            "attempt_ordinal": 1,
            "prompt_request_sha256": "0" * 64,
            "status": "completed",
            "reason_code": "provider_failed",
        },
    ],
)
def test_attempt_status_and_reason_are_single_consistent_record(payload) -> None:
    with pytest.raises(ValidationError):
        AttemptRef.model_validate(payload)


def test_frozen_profile_contains_nine_dimensions_and_exactly_25_full_entries() -> None:
    loaded = load_profile()
    profile = loaded.profile
    assert loaded.profile_sha256 == FROZEN_PROFILE_SHA256
    assert len(profile.dimensions) == 9
    sub_aspects = [
        item for dimension in profile.dimensions for item in dimension.sub_aspects
    ]
    assert len(sub_aspects) == 25
    assert len({item.sub_aspect_id for item in sub_aspects}) == 25
    for dimension in profile.dimensions:
        assert dimension.definition
        assert dimension.positive_criterion
        assert dimension.exclusions
        assert dimension.abstention_conditions
        assert dimension.o3_handoff_conditions
        for item in dimension.sub_aspects:
            assert item.definition
            assert item.positive_criterion
            assert item.exclusions
            assert item.abstention_conditions
            assert item.o3_handoff_conditions


def test_recut_error_vocabulary_is_complete_and_stable() -> None:
    vocabulary = set(
        [*ADMISSION_REASON_CODES, *PARSER_REASON_CODES, *VALIDATION_REASON_CODES]
    )
    assert {
        "admission_contract_invalid",
        "assessment_unit_failure_link_missing",
        "assessment_evidence_mismatch",
        "baseline_input_binding_mismatch",
        "composition_record_mismatch",
        "composition_witness_mismatch",
        "handoff_id_duplicate",
        "instrument_manifest_mismatch",
        "parser_duplicate_member",
    } <= vocabulary
