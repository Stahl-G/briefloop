"""Canonical serialization for the nine persisted PR-1 control DTOs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from multi_agent_brief.contracts.v2 import (
    Approval,
    ArtifactRecord,
    ArtifactRevision,
    Delivery,
    EventEnvelope,
    Invocation,
    RunIdentity,
    StageState,
    StrictModel,
    TransactionReceipt,
)
from multi_agent_brief.control_store.errors import ControlStoreIntegrityError


CONTROL_RECORD_MODELS: tuple[type[StrictModel], ...] = (
    RunIdentity,
    StageState,
    Invocation,
    ArtifactRecord,
    ArtifactRevision,
    EventEnvelope,
    Approval,
    Delivery,
    TransactionReceipt,
)
CONTROL_RECORD_SCHEMA_IDS: tuple[str, ...] = tuple(
    model.schema_id for model in CONTROL_RECORD_MODELS
)

_ModelT = TypeVar("_ModelT", bound=StrictModel)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize validated JSON data with one byte representation."""

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ControlStoreIntegrityError("canonical_json_invalid") from exc
    return text.encode("utf-8")


def canonical_model_payload(model: StrictModel) -> dict[str, Any]:
    """Return the validated canonical JSON payload for a persisted DTO."""

    if type(model) not in CONTROL_RECORD_MODELS:
        raise ControlStoreIntegrityError("unsupported_control_record")
    payload = model.model_dump(mode="json", exclude_unset=False)
    if not isinstance(payload, dict):
        raise ControlStoreIntegrityError("canonical_payload_invalid")
    return payload


def canonical_model_text(model: StrictModel) -> str:
    return canonical_json_bytes(canonical_model_payload(model)).decode("utf-8")


def decode_model(model_type: type[_ModelT], payload_text: str) -> _ModelT:
    """Decode and revalidate one canonical row payload."""

    if model_type not in CONTROL_RECORD_MODELS:
        raise ControlStoreIntegrityError("unsupported_control_record")
    try:
        payload = json.loads(payload_text)
        model = model_type.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ControlStoreIntegrityError("stored_payload_invalid") from exc
    if canonical_model_text(model) != payload_text:
        raise ControlStoreIntegrityError("stored_payload_not_canonical")
    return model


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_fingerprint(value: Any) -> str:
    return sha256_hex(canonical_json_bytes(value))


__all__ = [
    "CONTROL_RECORD_MODELS",
    "CONTROL_RECORD_SCHEMA_IDS",
    "canonical_fingerprint",
    "canonical_json_bytes",
    "canonical_model_payload",
    "canonical_model_text",
    "decode_model",
    "sha256_hex",
]
