"""Dormant fresh-v2 proposal and source intake."""

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
