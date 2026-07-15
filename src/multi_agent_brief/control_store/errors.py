"""Stable, value-free errors for the typed ControlStore substrate."""

from __future__ import annotations


class ControlStoreError(RuntimeError):
    """Base error carrying only a fixed machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ControlStoreConflict(ControlStoreError):
    """Optimistic revision, identity, or replay conflict."""


class ControlStoreIntegrityError(ControlStoreError):
    """The persisted store, a blob, or a relational constraint is invalid."""


class ControlStoreSchemaError(ControlStoreError):
    """The SQLite schema is missing, corrupt, or from an unsupported future."""


class ControlStoreStateError(ControlStoreError):
    """The UoW or store API was used in an invalid local state."""


__all__ = [
    "ControlStoreConflict",
    "ControlStoreError",
    "ControlStoreIntegrityError",
    "ControlStoreSchemaError",
    "ControlStoreStateError",
]
