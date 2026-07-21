"""Store-agnostic primitives for the local post-final Review Session.

The package deliberately accepts already-verified, typed projections.  It does
not discover workspaces, open ControlStore, or decide runtime legality.
"""

from .contracts import (
    PostFinalReviewContext,
    PostFinalReviewPolicyBinding,
    PostFinalReviewReadModel,
    QualityProjection,
    ReviewSessionDescriptor,
    SemanticReviewProjection,
)
from .launcher import ReviewLaunchResult, launch_review_session
from .static_qp import render_static_quality_panel

__all__ = [
    "PostFinalReviewContext",
    "PostFinalReviewPolicyBinding",
    "PostFinalReviewReadModel",
    "QualityProjection",
    "ReviewLaunchResult",
    "ReviewSessionDescriptor",
    "SemanticReviewProjection",
    "launch_review_session",
    "render_static_quality_panel",
]
