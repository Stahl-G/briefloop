"""Orchestrator control switchboard runtime surface."""

from multi_agent_brief.controls.contract import (
    CONTROL_SELECTIONS_FILE,
    CONTROL_SWITCHBOARD_FILE,
    CONTROL_SWITCHBOARD_FILES,
)
from multi_agent_brief.controls.switchboard import (
    build_control_switchboard,
    show_control_switchboard,
    select_control,
    validate_control_switchboard,
)

__all__ = [
    "CONTROL_SELECTIONS_FILE",
    "CONTROL_SWITCHBOARD_FILE",
    "CONTROL_SWITCHBOARD_FILES",
    "build_control_switchboard",
    "show_control_switchboard",
    "select_control",
    "validate_control_switchboard",
]
