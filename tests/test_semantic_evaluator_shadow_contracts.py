"""Strict schema and stable-vocabulary tests for PR-SE-2 evidence records."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from multi_agent_brief.semantic_evaluator.errors import SHADOW_REASON_CODES
from multi_agent_brief.semantic_evaluator.serialization import schema_sha256
from multi_agent_brief.semantic_evaluator.shadow_contracts import (
    SHADOW_CONTRACT_MODELS,
    SHADOW_SCHEMA_IDS,
    ArchiveMember,
    ProviderAttemptRecord,
    ShadowArchiveManifest,
    ShadowExecutionManifest,
    ShadowExecutionPolicy,
    ShadowRunReceipt,
    ShadowRunRequest,
)


def test_six_shadow_contracts_have_unique_frozen_schema_ids_and_examples() -> None:
    assert (
        tuple(model.schema_id for model in SHADOW_CONTRACT_MODELS) == SHADOW_SCHEMA_IDS
    )
    assert len(set(SHADOW_SCHEMA_IDS)) == 6
    assert list(SHADOW_SCHEMA_IDS) == list(dict.fromkeys(SHADOW_SCHEMA_IDS))
    for model in SHADOW_CONTRACT_MODELS:
        assert model.model_validate(model.contract_example("minimal"))
        assert len(schema_sha256(model)) == 64


@pytest.mark.parametrize("model", SHADOW_CONTRACT_MODELS)
def test_shadow_contracts_reject_extra_members_and_coercion(model) -> None:
    extra = deepcopy(model.minimal_example)
    extra["undeclared"] = "forbidden"
    with pytest.raises(ValidationError):
        model.model_validate(extra)
    wrong_schema = deepcopy(model.minimal_example)
    wrong_schema["schema_version"] = True
    with pytest.raises(ValidationError):
        model.model_validate(wrong_schema)


@pytest.mark.parametrize(
    ("model", "field"),
    (
        (ShadowExecutionPolicy, "execution_policy_sha256"),
        (ShadowExecutionManifest, "execution_sha256"),
        (ShadowRunRequest, "shadow_request_sha256"),
        (ProviderAttemptRecord, "attempt_record_sha256"),
        (ShadowArchiveManifest, "archive_manifest_sha256"),
        (ShadowRunReceipt, "receipt_sha256"),
    ),
)
def test_every_shadow_self_hash_is_verified(model, field: str) -> None:
    payload = deepcopy(model.minimal_example)
    payload[field] = "f" * 64
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_shadow_manifest_inventory_is_complete_sorted_and_exact() -> None:
    payload = deepcopy(ShadowExecutionManifest.minimal_example)
    payload["shadow_schema_sha256s"] = dict(
        reversed(list(payload["shadow_schema_sha256s"].items()))
    )
    with pytest.raises(ValidationError):
        ShadowExecutionManifest.model_validate(payload)


def test_archive_member_paths_are_relative_posix_and_canonical() -> None:
    for value in ("../escape", "/absolute", "nested//file", "nested\\file"):
        with pytest.raises(ValidationError):
            ArchiveMember(path=value, size_bytes=0, sha256="0" * 64)


def test_only_the_seven_frozen_shadow_reason_codes_are_added() -> None:
    assert SHADOW_REASON_CODES == (
        "shadow_request_invalid",
        "shadow_request_conflict",
        "shadow_adapter_unavailable",
        "provider_identity_mismatch",
        "shadow_archive_incomplete",
        "shadow_archive_invalid",
        "shadow_archive_publish_failed",
    )
