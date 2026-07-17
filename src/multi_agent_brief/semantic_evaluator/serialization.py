"""Evaluator-owned canonical research serialization and hashing."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel


class CanonicalSerializationError(ValueError):
    pass


def canonical_model_payload(
    model: BaseModel,
    *,
    exclude: Iterable[str] = (),
) -> dict[str, Any]:
    return model.model_dump(
        mode="json",
        exclude=set(exclude),
        exclude_unset=False,
    )


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = canonical_model_payload(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalSerializationError("canonical_json_invalid") from exc


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def canonical_model_sha256(
    model: BaseModel,
    *,
    exclude: Iterable[str] = (),
) -> str:
    return canonical_sha256(canonical_model_payload(model, exclude=exclude))


def normalized_utf8_text(value: bytes) -> str:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalSerializationError("invalid_utf8") from exc
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalized_source_bytes(value: bytes) -> bytes:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalSerializationError("invalid_utf8") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def source_sha256_for_module(module_name: str) -> str:
    from multi_agent_brief.semantic_evaluator.resources import (
        EvaluatorResourceError,
    )

    resolution_failed = False
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        resolution_failed = True
        spec = None
    if (
        resolution_failed
        or spec is None
        or spec.origin is None
        or spec.origin
        in {
            "built-in",
            "frozen",
        }
    ):
        raise EvaluatorResourceError("evaluator_source_unavailable") from None
    path = Path(spec.origin)
    read_failed = False
    try:
        source = path.read_bytes()
        normalized = normalized_source_bytes(source)
    except (OSError, CanonicalSerializationError):
        read_failed = True
    if read_failed:
        raise EvaluatorResourceError("evaluator_source_unavailable") from None
    return sha256_bytes(normalized)


def schema_sha256(model: type[BaseModel]) -> str:
    schema_method = getattr(model, "contract_json_schema", None)
    schema = schema_method() if callable(schema_method) else model.model_json_schema()
    return canonical_sha256(schema)


__all__ = [
    "CanonicalSerializationError",
    "canonical_json_bytes",
    "canonical_json_text",
    "canonical_model_payload",
    "canonical_model_sha256",
    "canonical_sha256",
    "normalized_source_bytes",
    "normalized_utf8_text",
    "schema_sha256",
    "sha256_bytes",
    "sha256_text",
    "source_sha256_for_module",
]
