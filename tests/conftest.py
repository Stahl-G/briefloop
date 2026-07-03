from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.helpers import sha256_file, write_minimal_workspace


@pytest.fixture
def shared_sha256_file() -> Callable[[Path], str]:
    return sha256_file


@pytest.fixture
def minimal_workspace_factory(tmp_path: Path) -> Callable[..., Path]:
    def _factory(name: str = "ws", **kwargs: object) -> Path:
        return write_minimal_workspace(tmp_path / name, **kwargs)

    return _factory
