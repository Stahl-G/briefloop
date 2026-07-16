"""Frozen component identity and canonical instrument hashing."""

from __future__ import annotations

from copy import deepcopy
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
from multi_agent_brief.semantic_evaluator.profile import LoadedProfile, load_profile
from multi_agent_brief.semantic_evaluator.prompts import (
    dimension_prompt_sha256,
    system_prompt_sha256,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_sha256,
    normalized_source_bytes,
)


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
    assert verify_instrument_manifest(first) is True
    reread = InstrumentManifest.model_validate_json(canonical_json_bytes(first))
    assert canonical_json_bytes(reread) == canonical_json_bytes(first)
    assert reread.instrument_config_sha256 == canonical_model_sha256(config)


def test_source_hash_normalization_changes_only_newline_encoding() -> None:
    assert normalized_source_bytes(b"line-1\r\nline-2\r") == (b"line-1\nline-2\n")
    assert normalized_source_bytes(b"\xef\xbb\xbfline\n") != (
        normalized_source_bytes(b"line\n")
    )


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
    assert len(manifest.schema_sha256s) == 14
    assert [item.component_id for item in manifest.implementation_components] == [
        "parser",
        "validator",
        "normalizer",
        "unit_planner",
    ]
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

    monkeypatch.setattr(instrument, "dimension_prompt_sha256", lambda: "f" * 64)
    prompt_changed = build_instrument_manifest(_config())
    assert prompt_changed.dimension_prompt_sha256 == "f" * 64
    assert prompt_changed.instrument_sha256 != original.instrument_sha256


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
