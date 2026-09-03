"""Strict contract inventory and local-registry isolation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from multi_agent_brief.contracts.base import SchemaRegistry
from multi_agent_brief.contracts.v2 import V2_CONTRACT_IDS, V2_CONTRACT_MODELS
from multi_agent_brief.semantic_evaluator.contracts import (
    SEMANTIC_EVALUATOR_CONTRACT_IDS,
    SEMANTIC_EVALUATOR_CONTRACT_MODELS,
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
