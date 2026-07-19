"""Active SQLite-only runtime host facade."""

from .contracts import (
    RoleTaskEnvelope,
    RuntimeDiagnoseReport,
    RuntimeInvocationResult,
)
from .errors import RuntimeHostError
from .initialization import InitializedRuntime, initialize_or_open_runtime
from .service import InvocationDispatch, RuntimeHostService

__all__ = [
    "InitializedRuntime",
    "InvocationDispatch",
    "RoleTaskEnvelope",
    "RuntimeDiagnoseReport",
    "RuntimeHostError",
    "RuntimeInvocationResult",
    "RuntimeHostService",
    "initialize_or_open_runtime",
]
