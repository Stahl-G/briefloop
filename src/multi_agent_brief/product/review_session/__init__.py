"""Ephemeral transport for the canonical local post-final Review Session.

The dormant package-era projection remains Store-agnostic.  The actionable
launcher delegates all authority discovery and writes to the product services;
browser state and this transport never decide runtime legality.
"""

from .contracts import (
    PostFinalReviewContext,
    PostFinalReviewPolicyBinding,
    PostFinalReviewReadModel,
    QualityProjection,
    ReviewSessionDescriptor,
    SemanticReviewProjection,
)
from .launcher import (
    ReviewLaunchResult,
    launch_actionable_review_session,
    launch_review_session,
)
from .static_qp import render_static_quality_panel

__all__ = [
    "PostFinalReviewContext",
    "PostFinalReviewPolicyBinding",
    "PostFinalReviewReadModel",
    "QualityProjection",
    "ReviewLaunchResult",
    "ReviewSessionDescriptor",
    "SemanticReviewProjection",
    "launch_actionable_review_session",
    "launch_review_session",
    "render_static_quality_panel",
]
