"""Shared boundary rule for advisory semantic-support proposal findings.

This is a leaf module (it imports nothing from ``audit``) so every consumer —
the repair router, audit score/status recompute, and feedback ingest — can share
one definition of "this finding is an advisory proposal, keep it out of workflow
signals" without creating an import cycle with ``audit.semantic`` /
``audit.interfaces``.
"""

from __future__ import annotations

from typing import Any

SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE = "semantic_support_proposal"


def is_advisory_semantic_support_proposal(finding_type: Any, severity: Any) -> bool:
    """True only for adapter-produced advisory proposal findings.

    The adapter always emits proposals at ``low`` severity. Consumers use this to
    keep advisory proposals out of repair routes, audit score/status, and
    feedback issues.

    The severity check is deliberate: a finding whose ``finding_type`` is
    ``semantic_support_proposal`` but whose severity is NOT ``low`` is malformed
    or spoofed. It is not treated as advisory and must fail closed — scored,
    routed, and ingested like any other finding — rather than silently escaping
    the workflow.
    """

    return (
        str(finding_type or "").strip().lower() == SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE
        and str(severity or "").strip().lower() == "low"
    )
