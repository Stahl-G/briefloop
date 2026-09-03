"""Default-surface gating for Experimental CLI commands.

Hidden commands stay callable: only their help entries are removed.  This is
the single place that touches argparse internals, so the private-attribute
risk is contained and covered by tests.
"""

from __future__ import annotations

import argparse
import os

EXPERIMENTAL_ENV_VAR = "BRIEFLOOP_EXPERIMENTAL"

EXPERIMENTAL_COMMANDS = frozenset(
    {
        "experiments",
        "new",
        "packs",
        "validate-report-spec",
        "extract",
        "quality",
    }
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def experimental_enabled() -> bool:
    """Return True when the caller opted into the Experimental surface."""
    return os.environ.get(EXPERIMENTAL_ENV_VAR, "").strip().lower() in _TRUTHY


def hide_experimental_commands(
    subparsers: argparse._SubParsersAction,
    *,
    commands: frozenset[str] = EXPERIMENTAL_COMMANDS,
) -> None:
    """Remove Experimental commands from help output without unregistering them.

    argparse renders subcommands twice: once as a ``{a,b,c}`` metavar on the
    usage line, and once as a description list built from
    ``_choices_actions``.  Both have to be adjusted, and ``choices`` must be
    left intact so existing scripts keep working.
    """
    visible = [
        action.dest
        for action in subparsers._choices_actions
        if action.dest not in commands
    ]
    for action in list(subparsers._choices_actions):
        if action.dest in commands:
            subparsers._choices_actions.remove(action)
    subparsers.metavar = "{" + ",".join(visible) + "}"
