"""Deterministic identity of packaged and workspace DeepSeek Harness kits.

The experimental DSH kit is workspace comfort material: it never writes the
ControlStore and the Store never re-binds on install. This module gives the
kit the same exact-identity discipline as the Codex kit so ``runtime install
--runtime dsh`` fails closed on tampered, deleted, extra, or symlinked
members.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from importlib import resources
import os
from pathlib import Path
import stat

import yaml

from multi_agent_brief import __version__
from multi_agent_brief.contracts.v2 import RuntimeAdapterBinding
from multi_agent_brief.control_store.serialization import canonical_fingerprint

from .errors import RuntimeHostError


_ROLE_IDS = (
    "analyst",
    "auditor",
    "claim-ledger",
    "editor",
    "scout",
    "screener",
    "source-planner",
    "source-provider",
)
_ASSET_PATHS = (
    "README.md",
    "skills/briefloop/SKILL.md",
    "skills/briefloop/references/controlstore-v2.md",
    *(
        f"presets/briefloop-{role_id}/agent.cordis.yml"
        for role_id in (
            "source-planner",
            "source-provider",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
            "auditor",
        )
    ),
    *(f"presets/briefloop-{role_id}/preset.yml" for role_id in (
        "source-planner",
        "source-provider",
        "scout",
        "screener",
        "claim-ledger",
        "analyst",
        "editor",
        "auditor",
    )),
)
_ASSET_DIRECTORIES = frozenset(
    parent.as_posix()
    for relative in _ASSET_PATHS
    for parent in Path(relative).parents
    if parent != Path(".")
)

_PERSONA_ROW = "@deepseek-ai/dsh-persona"


def _binding_error(exc: BaseException | None = None) -> RuntimeHostError:
    error = RuntimeHostError("runtime_adapter_binding_mismatch")
    if exc is not None:
        if isinstance(exc, BaseException):
            error.__cause__ = exc
        else:
            error.add_note(str(exc))
    return error


def _read_packaged_asset(relative: str) -> bytes:
    try:
        text = (
            resources.files("multi_agent_brief")
            .joinpath("runtime_kits", "dsh", *relative.split("/"))
            .read_text(encoding="utf-8")
        )
        return text.encode("utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise _binding_error(exc)


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _binding_error(exc)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _binding_error()
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise _binding_error()
        return b"".join(chunks)
    except OSError as exc:
        raise _binding_error(exc)
    finally:
        os.close(descriptor)


def _inventory_workspace_kit(kit_root: Path) -> None:
    try:
        root_mode = kit_root.lstat().st_mode
    except OSError as exc:
        raise _binding_error(exc)
    if not stat.S_ISDIR(root_mode):
        raise _binding_error()

    files: set[str] = set()
    directories: set[str] = set()
    pending = [kit_root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise _binding_error(exc)
        for entry in entries:
            relative = Path(entry.path).relative_to(kit_root).as_posix()
            try:
                if entry.is_symlink():
                    raise _binding_error()
                if entry.is_dir(follow_symlinks=False):
                    directories.add(relative)
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    files.add(relative)
                else:
                    raise _binding_error()
            except OSError as exc:
                raise _binding_error(exc)
    if files != set(_ASSET_PATHS) or directories != set(_ASSET_DIRECTORIES):
        raise _binding_error()


def _read_workspace_assets(workspace: Path) -> dict[str, bytes]:
    kit_root = workspace / ".dsh"
    _inventory_workspace_kit(kit_root)
    return {
        relative: _read_regular_file(kit_root.joinpath(*relative.split("/")))
        for relative in _ASSET_PATHS
    }


def _preset_rows(content: bytes, path: str) -> list[dict[str, object]]:
    try:
        payload = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _binding_error(exc)
    if type(payload) is not list:
        raise _binding_error(f"{path}: preset composition must be a list")
    rows = [row for row in payload if type(row) is dict]
    if len(rows) != len(payload):
        raise _binding_error(f"{path}: non-mapping preset row")
    return rows


def _build_binding(
    run_id: str,
    contents: dict[str, bytes],
) -> RuntimeAdapterBinding:
    for role_id in _ROLE_IDS:
        agent_path = f"presets/briefloop-{role_id}/agent.cordis.yml"
        meta_path = f"presets/briefloop-{role_id}/preset.yml"
        rows = _preset_rows(contents[agent_path], agent_path)
        if not rows:
            raise _binding_error(f"{agent_path}: empty preset composition")
        first = rows[0]
        if (
            first.get("id") != "persona"
            or first.get("name") != _PERSONA_ROW
        ):
            raise _binding_error(f"{agent_path}: persona row missing")
        persona_config = first.get("config")
        if not isinstance(persona_config, dict):
            raise _binding_error(f"{agent_path}: persona config missing")
        persona_text = persona_config.get("text")
        if (
            not isinstance(persona_text, str)
            or f"BriefLoop {role_id} specialist" not in persona_text
        ):
            raise _binding_error(f"{agent_path}: role persona missing")
        try:
            meta_payload = yaml.safe_load(contents[meta_path].decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise _binding_error(exc)
        if (
            type(meta_payload) is not dict
            or meta_payload.get("name") != f"BriefLoop {role_id}"
        ):
            raise _binding_error(f"{meta_path}: preset metadata missing")

    hashes = {
        "dsh." + relative.replace("/", "."): hashlib.sha256(content).hexdigest()
        for relative, content in sorted(contents.items())
    }
    payload = {
        "schema_version": RuntimeAdapterBinding.schema_id,
        "run_id": run_id,
        "runtime": "dsh",
        "adapter_id": "briefloop-dsh-controlstore",
        "adapter_version": "1",
        "briefloop_version": __version__,
        "control_protocol": "controlstore_v2",
        "action_protocol": "core_run_next_action_v2",
        "proposal_protocol": "pydantic_scratch_v2",
        "role_ids": list(_ROLE_IDS),
        "supported_role_topologies": ["default", "single_session", "strict"],
        "adapter_asset_sha256": hashes,
        "max_delegation_depth": 1,
        "max_threads": 6,
    }
    payload["binding_fingerprint"] = canonical_fingerprint(payload)
    try:
        return RuntimeAdapterBinding.model_validate(payload, strict=True)
    except ValueError as exc:
        raise _binding_error(exc)


def load_dsh_adapter_binding(run_id: str) -> RuntimeAdapterBinding:
    """Load the packaged kit binding for install and compatibility checks."""

    return _build_binding(
        run_id,
        {relative: _read_packaged_asset(relative) for relative in _ASSET_PATHS},
    )


def load_workspace_dsh_adapter_binding(
    workspace: str | Path,
    run_id: str,
) -> RuntimeAdapterBinding:
    """Load the exact DSH kit that the workspace contains under ``.dsh/``."""

    root = Path(workspace).expanduser().resolve(strict=False)
    return _build_binding(run_id, _read_workspace_assets(root))


def workspace_dsh_adapter_loader(
    workspace: str | Path,
) -> Callable[[str], RuntimeAdapterBinding]:
    """Bind the AdapterLoader interface to one immutable workspace location."""

    root = Path(workspace).expanduser().resolve(strict=False)

    def _load(run_id: str) -> RuntimeAdapterBinding:
        return load_workspace_dsh_adapter_binding(root, run_id)

    return _load


__all__ = [
    "load_dsh_adapter_binding",
    "load_workspace_dsh_adapter_binding",
    "workspace_dsh_adapter_loader",
]
