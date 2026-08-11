"""Active SQLite-only runtime host facade."""

from .contracts import (
    FinalizedLocalReviewFacts,
    FinalizedLocalReviewProjection,
    RoleTaskEnvelope,
    RuntimeContinuationResult,
    RuntimeDiagnoseReport,
    RuntimeInvocationResult,
)
from .errors import RuntimeHostError
from .initialization import InitializedRuntime, initialize_or_open_runtime
from .projections import build_finalized_local_review_projection
from .service import InvocationDispatch, RuntimeHostService

__all__ = [
    "InitializedRuntime",
    "InvocationDispatch",
    "FinalizedLocalReviewFacts",
    "FinalizedLocalReviewProjection",
    "RoleTaskEnvelope",
    "RuntimeContinuationResult",
    "RuntimeDiagnoseReport",
    "RuntimeHostError",
    "RuntimeInvocationResult",
    "RuntimeHostService",
    "build_finalized_local_review_projection",
    "initialize_or_open_runtime",
]
