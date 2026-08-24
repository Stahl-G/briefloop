"""Fresh-v2 proposal and source intake.

Consumed by runtime_host_v2 and the intake-v2 CLI lane.
"""

from multi_agent_brief.intake_v2.errors import IntakeError, IntakeResult
from multi_agent_brief.intake_v2.service import (
    IntakeService,
    submit_proposal,
    submit_source,
)


__all__ = [
    "IntakeError",
    "IntakeResult",
    "IntakeService",
    "submit_proposal",
    "submit_source",
]
