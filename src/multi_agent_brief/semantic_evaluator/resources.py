"""Packaged resource loading for source and non-editable wheel installs."""

from __future__ import annotations

from importlib.resources import files

from multi_agent_brief.semantic_evaluator.serialization import (
    CanonicalSerializationError,
    normalized_utf8_text,
    sha256_bytes,
)


RESOURCE_ROOTS = ("profiles", "prompts", "baselines")


class EvaluatorResourceError(RuntimeError):
    """Value-free marker owned only by packaged evaluator resource loaders."""


def _validate_parts(parts: tuple[str, ...]) -> None:
    if not parts or parts[0] not in RESOURCE_ROOTS:
        raise ValueError("unknown evaluator resource root")
    if any(
        not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts
    ):
        raise ValueError("invalid evaluator resource path")


def resource_bytes(*parts: str) -> bytes:
    _validate_parts(parts)
    read_failed = False
    try:
        resource = files("multi_agent_brief.semantic_evaluator").joinpath(*parts)
        content = resource.read_bytes()
    except OSError:
        read_failed = True
    if read_failed:
        raise EvaluatorResourceError("evaluator_resource_unavailable") from None
    return content


def resource_text(*parts: str) -> str:
    decode_failed = False
    try:
        text = normalized_utf8_text(resource_bytes(*parts))
    except EvaluatorResourceError:
        raise
    except CanonicalSerializationError:
        decode_failed = True
    if decode_failed:
        raise EvaluatorResourceError("evaluator_resource_unavailable") from None
    return text


def resource_sha256(*parts: str) -> str:
    return sha256_bytes(resource_text(*parts).encode("utf-8"))


__all__ = [
    "EvaluatorResourceError",
    "RESOURCE_ROOTS",
    "resource_bytes",
    "resource_sha256",
    "resource_text",
]
