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
    """Central registry for legacy Contracts and strict v2 model contracts."""

    _registry: ClassVar[dict[str, type[Any]]] = {}

    @classmethod
    def register(cls, contract_cls: type[Any]) -> type[Any]:
        """Register a contract class. Returns the class for use as decorator."""
        schema_id = getattr(contract_cls, "schema_id", None)
        if not isinstance(schema_id, str) or not schema_id:
            raise TypeError("Registered contracts must define a non-empty schema_id.")
        existing = cls._registry.get(schema_id)
        if existing is not None and existing is not contract_cls:
            raise ValueError(f"Schema already registered: {schema_id}")
        cls._registry[schema_id] = contract_cls
        return contract_cls

    @classmethod
    def get(cls, schema_id: str) -> type[Any] | None:
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
        strict_validate = getattr(contract, "contract_validate", None)
        if strict_validate is not None:
            return strict_validate(data)
        return contract.validate(data)

    @classmethod
    def validate_or_raise(cls, schema_id: str, data: dict[str, Any]) -> None:
        """Validate or raise ContractError."""
        contract = cls._registry.get(schema_id)
        if contract is None:
            raise KeyError(f"Unknown schema: {schema_id}")
        strict_validate_or_raise = getattr(contract, "contract_validate_or_raise", None)
        if strict_validate_or_raise is not None:
            strict_validate_or_raise(data)
            return
        contract.validate_or_raise(data)

    @classmethod
    def json_schema(cls, schema_id: str) -> dict[str, Any]:
        """Return a registered contract's JSON Schema."""

        contract = cls._registry.get(schema_id)
        if contract is None:
            raise KeyError(f"Unknown schema: {schema_id}")
        strict_schema = getattr(contract, "contract_json_schema", None)
        if strict_schema is not None:
            return strict_schema()
        return contract.json_schema()

    @classmethod
    def example(cls, schema_id: str, detail: str) -> dict[str, Any]:
        """Return an embedded strict-contract example."""

        contract = cls._registry.get(schema_id)
        if contract is None:
            raise KeyError(f"Unknown schema: {schema_id}")
        strict_example = getattr(contract, "contract_example", None)
        if strict_example is None:
            raise ValueError(f"Schema does not publish examples: {schema_id}")
        return strict_example(detail)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered contracts (for testing)."""
        cls._registry.clear()
