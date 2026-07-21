"""Re-export shim for the relocated runtime error vocabulary (LD2-2b).

The definitions now live in :mod:`multi_agent_brief.contracts.runtime_errors`.
This shim keeps legacy runtime_state imports working until LD2-3 deletes the
stack. It must not define anything of its own.
"""

from __future__ import annotations

from multi_agent_brief.contracts.runtime_errors import *  # noqa: F401,F403
from multi_agent_brief.contracts.runtime_errors import (  # noqa: F401
    RuntimeStateError,
    _wrap_archive_error,
)
