"""Shared delivery-eligibility rule.

Single source for whether a run's integrity state permits reader-facing
delivery. The deliver executor and the completion projection must both
consume this rule so they can never fork (projection saying valid while the
executor rejects, or vice versa).

Product ruling (v1.0 RC): after a contaminated run recovers through the
supersede/repair lane, reruns downstream, and completes finalize, delivery may
become valid — but the run is permanently non-reference-eligible. Raw
contaminated, unknown, and mid-recovery states remain blocked.
"""

from __future__ import annotations

from typing import Any, Mapping

from multi_agent_brief.orchestrator.recovery_state import (
    RECOVERY_COMPLETED_NON_REFERENCE,
)
from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    RUN_INTEGRITY_CONTAMINATED_REPAIRED,
)


DELIVERY_ALLOWED_CLEAN = "allowed_clean_reference_eligible"
DELIVERY_ALLOWED_NON_REFERENCE = "allowed_contaminated_repaired_non_reference"
DELIVERY_BLOCKED_RUN_INTEGRITY = "blocked_run_integrity_not_clean"


def evaluate_delivery_eligibility(
    run_integrity: Mapping[str, Any] | None,
    *,
    recovery_truth: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether run integrity permits reader-facing delivery.

    - clean + reference_eligible: delivery allowed, reference-eligible.
    - contaminated_repaired (terminal recovery written by finalize-complete):
      delivery allowed, permanently non-reference-eligible.
    - contaminated / unknown / anything mid-recovery: delivery blocked.
    """

    integrity = run_integrity if isinstance(run_integrity, Mapping) else {}
    status = str(integrity.get("status") or "").strip()
    if status == RUN_INTEGRITY_CLEAN and integrity.get("reference_eligible") is True:
        return {
            "allowed": True,
            "reference_eligible": True,
            "code": DELIVERY_ALLOWED_CLEAN,
            "run_integrity_status": status,
        }
    recovery = recovery_truth if isinstance(recovery_truth, Mapping) else {}
    if (
        status == RUN_INTEGRITY_CONTAMINATED_REPAIRED
        and recovery.get("status") == RECOVERY_COMPLETED_NON_REFERENCE
        and recovery.get("delivery_allowed") is True
    ):
        return {
            "allowed": True,
            "reference_eligible": False,
            "code": DELIVERY_ALLOWED_NON_REFERENCE,
            "run_integrity_status": status,
        }
    return {
        "allowed": False,
        "reference_eligible": False,
        "code": DELIVERY_BLOCKED_RUN_INTEGRITY,
        "run_integrity_status": status or "unknown",
    }
