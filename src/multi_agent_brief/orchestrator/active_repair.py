"""Shared active-repair predicates.

Runtime guards and read-only projections both need the same definition of an
open repair transaction. Keep the predicate in this dependency-light module so
contracts can consume it without importing the runtime-state package facade.
"""

from __future__ import annotations

from typing import Any, Mapping


def active_repair_is_open(workflow: Mapping[str, Any] | None) -> bool:
    """Return whether workflow state contains an active repair transaction.

    Any dict-valued ``active_repair`` field is open, even if the object is empty
    or malformed. Deterministic guards and read-only projections must agree on
    this predicate.
    """

    return isinstance(workflow, Mapping) and isinstance(workflow.get("active_repair"), dict)
