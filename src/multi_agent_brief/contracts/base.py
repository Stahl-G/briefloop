"""Base contract class and schema registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from multi_agent_brief.contracts.errors import ContractError, FieldViolation


class Contract(ABC):
    """Base class for schema contracts.

    Subclasses define:
    - schema_id: unique identifier (e.g. "source_item", "claim")
    - schema_version: current version (e.g. "v1")
    - json_schema(): return the JSON Schema dict
    - validate(): return list of FieldViolation (empty = valid)
    - migrate(): transform data from an older version
    """

    schema_id: ClassVar[str]
    schema_version: ClassVar[str]

    @classmethod
    @abstractmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return the JSON Schema for this contract."""

    @classmethod
    @abstractmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        """Validate data against this contract. Returns violations (empty = valid)."""

    @classmethod
    @abstractmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        """Migrate data from an older schema version to the current version."""

    @classmethod
    def validate_or_raise(cls, data: dict[str, Any]) -> None:
        """Validate and raise ContractError if violations found."""
        violations = cls.validate(data)
        errors = [v for v in violations if v.severity == "error"]
        if errors:
            raise ContractError(
                violations=violations,
                schema_id=cls.schema_id,
                schema_version=cls.schema_version,
            )

    @classmethod
    def is_valid(cls, data: dict[str, Any]) -> bool:
        """Return True if data passes validation (no error-level violations)."""
        violations = cls.validate(data)
        return not any(v.severity == "error" for v in violations)


class SchemaRegistry:
    """Central registry mapping schema_id to Contract class."""

    _registry: ClassVar[dict[str, type[Contract]]] = {}

    @classmethod
    def register(cls, contract_cls: type[Contract]) -> type[Contract]:
        """Register a contract class. Returns the class for use as decorator."""
        cls._registry[contract_cls.schema_id] = contract_cls
        return contract_cls

    @classmethod
    def get(cls, schema_id: str) -> type[Contract] | None:
        """Get a registered contract class by schema_id."""
        return cls._registry.get(schema_id)

    @classmethod
    def all_ids(cls) -> list[str]:
        """Return all registered schema IDs."""
        return sorted(cls._registry.keys())

    @classmethod
    def validate(cls, schema_id: str, data: dict[str, Any]) -> list[FieldViolation]:
        """Validate data against a registered schema. Raises KeyError if unknown."""
        contract = cls._registry.get(schema_id)
        if contract is None:
            raise KeyError(f"Unknown schema: {schema_id}")
        return contract.validate(data)

    @classmethod
    def validate_or_raise(cls, schema_id: str, data: dict[str, Any]) -> None:
        """Validate or raise ContractError."""
        contract = cls._registry.get(schema_id)
        if contract is None:
            raise KeyError(f"Unknown schema: {schema_id}")
        contract.validate_or_raise(data)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered contracts (for testing)."""
        cls._registry.clear()
