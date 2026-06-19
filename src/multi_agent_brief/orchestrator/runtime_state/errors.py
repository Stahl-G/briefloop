"""Runtime-state errors and error-code constants."""

from __future__ import annotations

from typing import Any


E_STAGE_ALREADY_COMPLETED = "E_STAGE_ALREADY_COMPLETED"
E_STAGE_MISMATCH = "E_STAGE_MISMATCH"
E_REQUIRED_ARTIFACT_MISSING = "E_REQUIRED_ARTIFACT_MISSING"
E_ARTIFACT_INVALID = "E_ARTIFACT_INVALID"
E_CLAIM_DRAFT_CONTRACT_INVALID = "E_CLAIM_DRAFT_CONTRACT_INVALID"
E_ILLEGAL_TRANSITION = "E_ILLEGAL_TRANSITION"
E_MANIFEST_EXTENSION_LOST = "E_MANIFEST_EXTENSION_LOST"
E_TRANSACTION_PARTIAL_WRITE = "E_TRANSACTION_PARTIAL_WRITE"
E_TRANSACTION_INTEGRITY = "E_TRANSACTION_INTEGRITY"
E_RUNTIME_STATE_NOT_INITIALIZED = "E_RUNTIME_STATE_NOT_INITIALIZED"
E_RUN_ARCHIVE_FAILED = "E_RUN_ARCHIVE_FAILED"
E_FACT_LAYER_IMPORT_INVALID = "E_FACT_LAYER_IMPORT_INVALID"
E_QUALITY_GATE_REQUIRED = "E_QUALITY_GATE_REQUIRED"
E_READER_FINAL_GATE_FAILED = "E_READER_FINAL_GATE_FAILED"
E_COMPLETION_TRANSACTION_REQUIRED = "E_COMPLETION_TRANSACTION_REQUIRED"
E_REPAIR_TRANSACTION_REQUIRED = "E_REPAIR_TRANSACTION_REQUIRED"
E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN = "E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN"
E_REPAIR_NO_LEGAL_ROUTE = "E_REPAIR_NO_LEGAL_ROUTE"
E_FROZEN_GATE_REPORT_ALREADY_EXISTS = "E_FROZEN_GATE_REPORT_ALREADY_EXISTS"
E_ACTIVE_REPAIR_OPEN = "E_ACTIVE_REPAIR_OPEN"
E_ASSESSMENT_TARGET_COMPLETE = "E_ASSESSMENT_TARGET_COMPLETE"


class RuntimeStateError(Exception):
    """Raised when runtime state cannot be read or written safely."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details or {}
        self.error_code = error_code

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": False,
            "error": str(self),
            "details": self.details,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


def _wrap_archive_error(exc: Any) -> RuntimeStateError:
    return RuntimeStateError(
        str(exc),
        details=getattr(exc, "details", {}),
        error_code=getattr(exc, "error_code", None) or E_RUN_ARCHIVE_FAILED,
    )
