"""Deterministic identity of packaged and workspace Codex runtime kits."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from importlib import resources
import os
from pathlib import Path
import stat
import tomllib

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
    "config.toml",
    "skills/briefloop/SKILL.md",
    "skills/briefloop/references/controlstore-v2.md",
    *(f"agents/briefloop-{role_id}.toml" for role_id in _ROLE_IDS),
)
_ASSET_DIRECTORIES = frozenset(
    parent.as_posix()
    for relative in _ASSET_PATHS
    for parent in Path(relative).parents
    if parent != Path(".")
)


def _binding_error(exc: BaseException | None = None) -> RuntimeHostError:
    error = RuntimeHostError("runtime_adapter_binding_mismatch")
    if exc is not None:
        error.__cause__ = exc
    return error


def _read_packaged_asset(relative: str) -> bytes:
    try:
        return (
            resources.files("multi_agent_brief")
            .joinpath("runtime_kits", "codex", *relative.split("/"))
            .read_bytes()
        )
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
    kit_root = workspace / ".codex"
    _inventory_workspace_kit(kit_root)
    return {
        relative: _read_regular_file(kit_root.joinpath(*relative.split("/")))
        for relative in _ASSET_PATHS
    }


def _build_binding(
    run_id: str,
    contents: dict[str, bytes],
) -> RuntimeAdapterBinding:
    try:
        config = tomllib.loads(contents["config.toml"].decode("utf-8"))
        agents = config["agents"]
        if agents != {"max_threads": 6, "max_depth": 1}:
            raise _binding_error()
        for role_id in _ROLE_IDS:
            payload = tomllib.loads(
                contents[f"agents/briefloop-{role_id}.toml"].decode("utf-8")
            )
            if payload.get("name") != role_id:
                raise _binding_error()
    except RuntimeHostError:
        raise
    except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _binding_error(exc)
    hashes = {
        "codex." + relative.replace("/", "."): hashlib.sha256(content).hexdigest()
        for relative, content in sorted(contents.items())
    }
    payload = {
        "schema_version": RuntimeAdapterBinding.schema_id,
        "run_id": run_id,
        "runtime": "codex",
        "adapter_id": "briefloop-codex-controlstore",
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


def load_codex_adapter_binding(run_id: str) -> RuntimeAdapterBinding:
    """Load the packaged kit binding for install and compatibility checks."""

    return _build_binding(
        run_id,
        {relative: _read_packaged_asset(relative) for relative in _ASSET_PATHS},
    )


def load_workspace_codex_adapter_binding(
    workspace: str | Path,
    run_id: str,
) -> RuntimeAdapterBinding:
    """Load the exact Codex kit that the workspace runtime can discover."""

    root = Path(workspace).expanduser().resolve(strict=False)
    return _build_binding(run_id, _read_workspace_assets(root))


def workspace_codex_adapter_loader(
    workspace: str | Path,
) -> Callable[[str], RuntimeAdapterBinding]:
    """Bind the AdapterLoader interface to one immutable workspace location."""

    root = Path(workspace).expanduser().resolve(strict=False)

    def _load(run_id: str) -> RuntimeAdapterBinding:
        return load_workspace_codex_adapter_binding(root, run_id)

    return _load


__all__ = [
    "load_codex_adapter_binding",
    "load_workspace_codex_adapter_binding",
    "workspace_codex_adapter_loader",
]
