"""Stable runtime-state facade.

The implementation is being decomposed behind this import path. Public
consumers should continue importing from ``multi_agent_brief.orchestrator.runtime_state``.
Private compatibility helpers are exposed only for existing in-repo tests during
the decomposition; they are not part of ``__all__``.
"""

from __future__ import annotations

from . import _impl


__all__ = list(_impl.__all__)

for _name in __all__:
    globals()[_name] = getattr(_impl, _name)

for _name in ("_allowed_decisions_for_stage", "_append_jsonl", "_sha256_file"):
    globals()[_name] = getattr(_impl, _name)

del _name
