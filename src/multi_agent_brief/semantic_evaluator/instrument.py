"""Deterministic identity for the frozen Semantic Evaluator instrument."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from multi_agent_brief.semantic_evaluator.contracts import (
    INSTRUMENT_MANIFEST_SCHEMA_ID,
    SEMANTIC_EVALUATOR_CONTRACT_MODELS,
    ImplementationComponent,
    InstrumentConfig,
    InstrumentManifest,
)
from multi_agent_brief.semantic_evaluator.normalization import NORMALIZER_VERSION
from multi_agent_brief.semantic_evaluator.parser import PARSER_VERSION
from multi_agent_brief.semantic_evaluator.profile import (
    LoadedProfile,
    load_profile,
    validate_loaded_profile,
)
from multi_agent_brief.semantic_evaluator.prompts import (
    PROMPT_ASSEMBLER_VERSION,
    dimension_prompt_sha256,
    system_prompt_sha256,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_payload,
    canonical_model_sha256,
    canonical_sha256,
    schema_sha256,
    source_sha256_for_module,
)
from multi_agent_brief.semantic_evaluator.unit_planner import UNIT_PLANNER_VERSION
from multi_agent_brief.semantic_evaluator.validator import VALIDATOR_VERSION


FROZEN_DESIGN_SHA256 = (
    "a3781d65dab268763afa3a8dba9ed99fc9b872df220d176b4af1c519d496e3d6"
)
FREEZE_MANIFEST_SHA256 = (
    "47e261afa64bf4a99824a4b94bccdca21b195f3694c338918f8d7973219b7065"
)

_IMPLEMENTATIONS = (
    ("parser", PARSER_VERSION, "multi_agent_brief.semantic_evaluator.parser"),
    ("validator", VALIDATOR_VERSION, "multi_agent_brief.semantic_evaluator.validator"),
    (
        "normalizer",
        NORMALIZER_VERSION,
        "multi_agent_brief.semantic_evaluator.normalization",
    ),
    (
        "unit_planner",
        UNIT_PLANNER_VERSION,
        "multi_agent_brief.semantic_evaluator.unit_planner",
    ),
    (
        "prompt_assembler",
        PROMPT_ASSEMBLER_VERSION,
        "multi_agent_brief.semantic_evaluator.prompts",
    ),
)


def build_instrument_manifest(
    config: InstrumentConfig,
    *,
    loaded_profile: LoadedProfile | None = None,
) -> InstrumentManifest:
    profile = loaded_profile or load_profile()
    validate_loaded_profile(profile)
    schema_hashes = {
        model.schema_id: schema_sha256(model)
        for model in sorted(
            SEMANTIC_EVALUATOR_CONTRACT_MODELS,
            key=lambda item: item.schema_id,
        )
    }
    components = [
        ImplementationComponent(
            component_id=component_id,
            implementation_version=version,
            source_sha256=source_sha256_for_module(module_name),
        ).model_dump(mode="json")
        for component_id, version, module_name in _IMPLEMENTATIONS
    ]
    config_sha = canonical_model_sha256(config)
    component_identity = {
        "frozen_design_sha256": FROZEN_DESIGN_SHA256,
        "freeze_manifest_sha256": FREEZE_MANIFEST_SHA256,
        "profile_sha256": profile.profile_sha256,
        "system_prompt_sha256": system_prompt_sha256(),
        "dimension_prompt_sha256": dimension_prompt_sha256(),
        "schema_sha256s": schema_hashes,
        "implementation_components": components,
        "retry_policy_sha256": canonical_model_sha256(config.retry_policy),
        "decoding_sha256": canonical_model_sha256(config.decoding),
        "instrument_config_sha256": config_sha,
        "provider_id": config.provider_id,
        "model_id": config.model_id,
        "model_version": config.model_version,
        "prompt_sizer_id": config.prompt_sizer.sizer_id,
        "prompt_sizer_version": config.prompt_sizer.sizer_version,
        "language": config.language,
        "max_context_tokens": config.prompt_sizer.max_context_tokens,
        "reserved_output_tokens": config.prompt_sizer.reserved_output_tokens,
        "transport_policy": config.transport_policy.model_dump(mode="json"),
    }
    payload = {
        "schema_version": INSTRUMENT_MANIFEST_SCHEMA_ID,
        "manifest_id": f"manifest-{canonical_sha256(component_identity)[:12]}",
        **component_identity,
    }
    return InstrumentManifest.model_validate(
        {**payload, "instrument_sha256": canonical_sha256(payload)}
    )


def verify_instrument_manifest(
    manifest: InstrumentManifest | dict[str, Any],
    config: InstrumentConfig,
    *,
    loaded_profile: LoadedProfile | None = None,
) -> bool:
    try:
        strict_manifest = InstrumentManifest.model_validate(
            manifest.model_dump(mode="json")
            if isinstance(manifest, InstrumentManifest)
            else manifest
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise SemanticEvaluatorError("instrument_manifest_mismatch") from exc
    payload = canonical_model_payload(
        strict_manifest,
        exclude=("instrument_sha256",),
    )
    if strict_manifest.instrument_sha256 != canonical_sha256(payload):
        raise SemanticEvaluatorError("instrument_manifest_mismatch")
    expected = build_instrument_manifest(config, loaded_profile=loaded_profile)
    if canonical_json_bytes(strict_manifest) != canonical_json_bytes(expected):
        raise SemanticEvaluatorError("instrument_manifest_mismatch")
    return True


__all__ = [
    "FREEZE_MANIFEST_SHA256",
    "FROZEN_DESIGN_SHA256",
    "build_instrument_manifest",
    "verify_instrument_manifest",
]
