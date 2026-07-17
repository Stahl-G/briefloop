"""Frozen component identity and canonical instrument hashing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re

import pytest

from multi_agent_brief.semantic_evaluator import instrument
from multi_agent_brief.semantic_evaluator.contracts import (
    SEMANTIC_EVALUATOR_CONTRACT_IDS,
    InstrumentConfig,
    InstrumentManifest,
)
from multi_agent_brief.semantic_evaluator.instrument import (
    FREEZE_MANIFEST_SHA256,
    FROZEN_DESIGN_SHA256,
    build_instrument_manifest,
    verify_instrument_manifest,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.parser import PARSER_VERSION
from multi_agent_brief.semantic_evaluator.profile import LoadedProfile, load_profile
from multi_agent_brief.semantic_evaluator.prompts import (
    PROMPT_ASSEMBLER_VERSION,
    dimension_prompt_sha256,
    system_prompt_sha256,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_payload,
    canonical_model_sha256,
    canonical_sha256,
    normalized_source_bytes,
    source_sha256_for_module,
)
from multi_agent_brief.semantic_evaluator.resources import EvaluatorResourceError
from multi_agent_brief.semantic_evaluator.validator import VALIDATOR_VERSION


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _config(**changes) -> InstrumentConfig:
    payload = deepcopy(InstrumentConfig.minimal_example)
    for path, value in changes.items():
        target = payload
        parts = path.split("__")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return InstrumentConfig.model_validate(payload)


def test_same_frozen_components_produce_identical_canonical_manifest() -> None:
    config = _config()
    first = build_instrument_manifest(config)
    second = build_instrument_manifest(config)
    assert first == second
    assert first.instrument_sha256 == second.instrument_sha256
    assert verify_instrument_manifest(first, config) is True
    reread = InstrumentManifest.model_validate_json(canonical_json_bytes(first))
    assert canonical_json_bytes(reread) == canonical_json_bytes(first)
    assert reread.instrument_config_sha256 == canonical_model_sha256(config)


def test_source_hash_normalization_changes_only_newline_encoding() -> None:
    assert normalized_source_bytes(b"line-1\r\nline-2\r") == (b"line-1\nline-2\n")
    assert normalized_source_bytes(b"\xef\xbb\xbfline\n") != (
        normalized_source_bytes(b"line\n")
    )


def test_source_resolution_failure_uses_one_value_free_package_marker() -> None:
    with pytest.raises(EvaluatorResourceError) as caught:
        source_sha256_for_module("synthetic_missing.semantic_evaluator_component")
    assert str(caught.value) == "evaluator_source_unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_manifest_binds_exact_frozen_resources_schemas_and_source_components() -> None:
    manifest = build_instrument_manifest(_config())
    assert manifest.frozen_design_sha256 == FROZEN_DESIGN_SHA256
    assert manifest.freeze_manifest_sha256 == FREEZE_MANIFEST_SHA256
    assert manifest.profile_sha256 == load_profile().profile_sha256
    assert manifest.system_prompt_sha256 == system_prompt_sha256()
    assert manifest.dimension_prompt_sha256 == dimension_prompt_sha256()
    assert tuple(manifest.schema_sha256s) == tuple(
        sorted(SEMANTIC_EVALUATOR_CONTRACT_IDS)
    )
    assert len(manifest.schema_sha256s) == 16
    assert [item.component_id for item in manifest.implementation_components] == [
        "parser",
        "validator",
        "normalizer",
        "unit_planner",
        "prompt_assembler",
    ]
    versions = {
        item.component_id: item.implementation_version
        for item in manifest.implementation_components
    }
    assert PARSER_VERSION == "strict_dimension_json_v2"
    assert VALIDATOR_VERSION == "dimension_validator_v3"
    assert PROMPT_ASSEMBLER_VERSION == "dimension_prompt_assembler_v2"
    assert versions["validator"] == VALIDATOR_VERSION
    assert versions["parser"] == PARSER_VERSION
    assert versions["prompt_assembler"] == PROMPT_ASSEMBLER_VERSION
    assert all(
        SHA256_RE.fullmatch(item.source_sha256)
        for item in manifest.implementation_components
    )
    serialized = canonical_json_bytes(manifest)
    assert b"/private/" not in serialized
    assert b"/Users/" not in serialized


def test_each_representative_bound_component_change_rotates_instrument_hash(
    monkeypatch,
) -> None:
    original = build_instrument_manifest(_config())
    decoding_changed = build_instrument_manifest(_config(decoding__temperature=0.1))
    model_changed = build_instrument_manifest(_config(model_id="fake-model-next"))
    assert decoding_changed.instrument_sha256 != original.instrument_sha256
    assert model_changed.instrument_sha256 != original.instrument_sha256

    original_acquirer = instrument.acquire_resource_snapshot

    def changed_prompt_resource(**kwargs):
        resources = original_acquirer(**kwargs)
        return replace(
            resources,
            prompts=replace(resources.prompts, dimension_sha256="f" * 64),
        )

    monkeypatch.setattr(
        instrument,
        "acquire_resource_snapshot",
        changed_prompt_resource,
    )
    prompt_changed = build_instrument_manifest(_config())
    assert prompt_changed.dimension_prompt_sha256 == "f" * 64
    assert prompt_changed.instrument_sha256 != original.instrument_sha256


def test_prompt_assembler_source_change_rotates_identity_without_resource_change(
    monkeypatch,
) -> None:
    config = _config()
    original = build_instrument_manifest(config)
    original_source_hasher = instrument.source_sha256_for_module

    def changed_source(module_name: str) -> str:
        if module_name == "multi_agent_brief.semantic_evaluator.prompts":
            return "e" * 64
        return original_source_hasher(module_name)

    monkeypatch.setattr(instrument, "source_sha256_for_module", changed_source)
    changed = build_instrument_manifest(config)
    assert changed.system_prompt_sha256 == original.system_prompt_sha256
    assert changed.dimension_prompt_sha256 == original.dimension_prompt_sha256
    assert changed.implementation_components[-1].source_sha256 == "e" * 64
    assert changed.instrument_sha256 != original.instrument_sha256


def test_self_consistent_but_non_current_manifest_is_rejected() -> None:
    config = _config()
    current = build_instrument_manifest(config)
    payload = current.model_dump(mode="json")
    payload["model_version"] = "self-consistent-forgery"
    payload["instrument_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "instrument_sha256"}
    )
    forged = InstrumentManifest.model_validate(payload)
    with pytest.raises(SemanticEvaluatorError, match="instrument_manifest_mismatch"):
        verify_instrument_manifest(forged, config)


def test_old_four_component_manifest_has_no_compatibility_path() -> None:
    config = _config()
    payload = build_instrument_manifest(config).model_dump(mode="json")
    payload["implementation_components"] = payload["implementation_components"][:-1]
    with pytest.raises(SemanticEvaluatorError, match="instrument_manifest_mismatch"):
        verify_instrument_manifest(payload, config)


@pytest.mark.parametrize("mutation", ["missing", "extra", "malformed"])
def test_malformed_manifest_errors_retain_no_caller_values(mutation: str) -> None:
    config = _config()
    payload = build_instrument_manifest(config).model_dump(mode="json")
    hidden_detail = "PRIVATE-SYNTHETIC-CANARY-DO-NOT-RENDER"
    if mutation == "missing":
        payload.pop("provider_id")
        payload["unexpected_private"] = hidden_detail
    elif mutation == "extra":
        payload["unexpected_private"] = hidden_detail
    else:
        payload["implementation_components"] = hidden_detail
    with pytest.raises(SemanticEvaluatorError) as caught:
        verify_instrument_manifest(payload, config)
    assert caught.value.reason_code == "instrument_manifest_mismatch"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert hidden_detail not in repr(caught.value)


def test_malformed_instrument_config_is_rejected_value_free_at_public_boundaries() -> (
    None
):
    config = _config()
    manifest = build_instrument_manifest(config)
    hidden_detail = "PRIVATE SYNTHETIC CONFIG VALUE"
    malformed = config.model_copy(update={"provider_id": hidden_detail})
    for operation in (
        lambda: build_instrument_manifest(malformed),
        lambda: verify_instrument_manifest(manifest, malformed),
    ):
        with pytest.raises(SemanticEvaluatorError) as caught:
            operation()
        assert caught.value.reason_code == "instrument_manifest_mismatch"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert hidden_detail not in repr(caught.value)


def test_explicit_unavailable_model_version_is_bound_not_inferred() -> None:
    manifest = build_instrument_manifest(_config(model_version="unavailable"))
    assert manifest.model_version == "unavailable"
    assert manifest.provider_id == "fake-provider"
    assert manifest.model_id == "fake-model"


def test_injected_profile_hash_cannot_forge_instrument_identity() -> None:
    loaded = load_profile()
    forged = LoadedProfile(profile=loaded.profile, profile_sha256="0" * 64)
    with pytest.raises(SemanticEvaluatorError, match="profile_invalid"):
        build_instrument_manifest(_config(), loaded_profile=forged)
