"""Packaged evaluation-case fixture resolution."""

from __future__ import annotations

from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator


@contextmanager
def evaluation_cases_root(cases_dir: str | Path | None = None) -> Iterator[Path]:
    """Yield a real filesystem path for evaluation cases."""
    if cases_dir is not None:
        yield Path(cases_dir).expanduser().resolve()
        return

    traversable = resources.files("multi_agent_brief.evaluation_cases").joinpath("fixtures")
    with resources.as_file(traversable) as resolved:
        yield Path(resolved)
