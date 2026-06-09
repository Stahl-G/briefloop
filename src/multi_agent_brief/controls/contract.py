"""Contracts for the Orchestrator control switchboard.

The switchboard is runtime control context. It is not a workflow stage output
artifact and selecting a control never executes that control.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


CONTROL_SWITCHBOARD_SCHEMA = "multi-agent-brief-orchestrator-control-switchboard/v1"
CONTROL_SELECTIONS_SCHEMA = "multi-agent-brief-control-selections/v1"

CONTROL_SWITCHBOARD_FILE = "output/intermediate/orchestrator_control_switchboard.json"
CONTROL_SELECTIONS_FILE = "output/intermediate/control_selections.json"

CONTROL_SWITCHBOARD_FILES = {
    "orchestrator_control_switchboard": CONTROL_SWITCHBOARD_FILE,
    "control_selections": CONTROL_SELECTIONS_FILE,
}

RECOMMENDATIONS = {"suggested", "recommended", "required", "not_applicable"}
EXECUTION_TYPES = {
    "cli",
    "subagent",
    "human_action",
    "config_review",
    "role_guidance",
    "audit_rule",
    "none",
}
SELECTIONS = {"enable", "defer", "reject"}

CONTROL_IDS = {
    "quality_gates",
    "feedback_repair_plan",
    "analysis_blocks",
    "case_applicability",
    "limitation_hygiene",
    "local_signal_discovery",
    "consumer_pain_point_discovery",
    "provenance_projection",
}

HUMAN_APPROVAL_CONTROLS = {
    "local_signal_discovery",
    "consumer_pain_point_discovery",
}


class ControlSwitchboardError(Exception):
    """Raised when control switchboard state is invalid or cannot be written."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "details": self.details,
        }


def control_switchboard_paths(workspace: str | Path) -> dict[str, Path]:
    ws = Path(workspace).expanduser().resolve()
    return {key: ws / rel_path for key, rel_path in CONTROL_SWITCHBOARD_FILES.items()}


def path_is_absolute_any_platform(value: str) -> bool:
    return (
        Path(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def path_has_traversal_any_platform(value: str) -> bool:
    return (
        ".." in Path(value).parts
        or ".." in PurePosixPath(value).parts
        or ".." in PureWindowsPath(value).parts
    )


def ensure_safe_relative_path(value: str, *, label: str = "path") -> None:
    if not value or value == ".":
        return
    if value.lower().startswith("file://"):
        raise ControlSwitchboardError(
            f"{label} must not be a file:// path.",
            details={label: value},
        )
    if path_is_absolute_any_platform(value):
        raise ControlSwitchboardError(
            f"{label} must be relative, not absolute.",
            details={label: value},
        )
    if path_has_traversal_any_platform(value):
        raise ControlSwitchboardError(
            f"{label} must not contain path traversal.",
            details={label: value},
        )
