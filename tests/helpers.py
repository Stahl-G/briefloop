from __future__ import annotations

import hashlib
from collections.abc import Callable
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
    project_name: str = "Test Workspace",
    user_text: str = "# User\n",
    sources_text: str = "manual:\n  sources: []\n",
    include_input_dir: bool = False,
    include_output_dir: bool = False,
    input_path: str | None = None,
    output_path: str | None = None,
) -> Path:
    config: dict[str, object] = {"project": {"name": project_name}}
    if input_path is not None:
        config["input"] = {"path": input_path}
    if output_path is not None:
        config["output"] = {"path": output_path}
    return write_workspace_files(
        path,
        config_text=yaml.safe_dump(config, sort_keys=False),
        user_text=user_text,
        sources_text=sources_text,
        include_input_dir=include_input_dir,
        include_output_dir=include_output_dir,
    )


def write_workspace_files(
    path: Path,
    *,
    config_text: str,
    user_text: str = "# User\n",
    sources_text: str = "manual:\n  sources: []\n",
    include_input_dir: bool = False,
    include_output_dir: bool = False,
) -> Path:
    path.mkdir(parents=True)
    if include_input_dir:
        (path / "input").mkdir()
    if include_output_dir:
        (path / "output").mkdir()
    (path / "config.yaml").write_text(config_text, encoding="utf-8")
    (path / "sources.yaml").write_text(sources_text, encoding="utf-8")
    (path / "user.md").write_text(user_text, encoding="utf-8")
    return path


def initialize_workspace(path: Path) -> Path:
    from multi_agent_brief.cli.main import main

    assert main(["state", "init", "--workspace", str(path)]) == 0
    return path


def write_minimal_workspace_under(base_path: Path, name: str = "ws", **kwargs: object) -> Path:
    return write_minimal_workspace(base_path / name, **kwargs)


def write_workspace_files_under(base_path: Path, name: str = "ws", **kwargs: object) -> Path:
    return write_workspace_files(base_path / name, **kwargs)


def initialized_workspace_writer(
    writer: Callable[..., Path] = write_minimal_workspace_under,
    **default_kwargs: object,
) -> Callable[[Path], Path]:
    def _write(base_path: Path) -> Path:
        return initialize_workspace(writer(base_path, **default_kwargs))

    return _write
