"""Packaged resource loading for source and non-editable wheel installs."""

from __future__ import annotations

from importlib.resources import files

from multi_agent_brief.semantic_evaluator.serialization import (
    normalized_utf8_text,
    sha256_bytes,
)


RESOURCE_ROOTS = ("profiles", "prompts", "baselines")


def _validate_parts(parts: tuple[str, ...]) -> None:
    if not parts or parts[0] not in RESOURCE_ROOTS:
        raise ValueError("unknown evaluator resource root")
    if any(
        not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts
    ):
        raise ValueError("invalid evaluator resource path")


def resource_bytes(*parts: str) -> bytes:
    _validate_parts(parts)
    resource = files("multi_agent_brief.semantic_evaluator").joinpath(*parts)
    try:
        return resource.read_bytes()
    except OSError as exc:
        raise FileNotFoundError("evaluator_resource_unavailable") from exc


def resource_text(*parts: str) -> str:
    return normalized_utf8_text(resource_bytes(*parts))


def resource_sha256(*parts: str) -> str:
    return sha256_bytes(resource_text(*parts).encode("utf-8"))


__all__ = [
    "RESOURCE_ROOTS",
    "resource_bytes",
    "resource_sha256",
    "resource_text",
]
