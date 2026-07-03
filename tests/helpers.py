from __future__ import annotations

import hashlib
from pathlib import Path

import yaml


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_minimal_workspace(
    path: Path,
    *,
    project_name: str,
    user_text: str,
    sources_text: str = "manual:\n  sources: []\n",
    include_input_dir: bool = False,
) -> Path:
    path.mkdir(parents=True)
    if include_input_dir:
        (path / "input").mkdir()
    (path / "config.yaml").write_text(
        yaml.safe_dump({"project": {"name": project_name}}, sort_keys=False),
        encoding="utf-8",
    )
    (path / "sources.yaml").write_text(sources_text, encoding="utf-8")
    (path / "user.md").write_text(user_text, encoding="utf-8")
    return path
