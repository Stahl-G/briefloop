"""Store-first initialization boundary for the active Codex runtime."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
from typing import Callable

from pydantic import ValidationError
import yaml

from multi_agent_brief.cli.authority_guard import classify_workspace_authority
from multi_agent_brief.contracts.v2 import (
    CoreRunInitializeRequest,
    CoreRunNextAction,
    RuntimeAdapterBinding,
    WorkspaceControlStoreBootstrapV2,
)
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.core_run_v2.service import CoreRunService
from multi_agent_brief.core_run_v2.verifier import (
    CoreRunDomainVerifier,
    VerifiedCoreRun,
)

from .errors import RuntimeHostError
from .source_routes import derive_runtime_source_plan


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.YAMLError("duplicate mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class InitializedRuntime:
    verified: VerifiedCoreRun
    action: CoreRunNextAction
    initialized: bool


AdapterLoader = Callable[[str], RuntimeAdapterBinding]


def _read_regular_file(path: Path) -> bytes:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise RuntimeHostError("runtime_initialization_input_invalid")
        return path.read_bytes()
    except RuntimeHostError:
        raise
    except OSError as exc:
        raise RuntimeHostError("runtime_initialization_input_invalid") from exc


def _load_yaml_mapping(content: bytes) -> dict[str, object]:
    try:
        payload = yaml.load(content.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RuntimeHostError("runtime_initialization_input_invalid") from exc
    if type(payload) is not dict or any(type(key) is not str for key in payload):
        raise RuntimeHostError("runtime_initialization_input_invalid")
    return payload


def _verify_existing(
    workspace: Path,
    *,
    adapter_loader: AdapterLoader,
) -> InitializedRuntime:
    try:
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            head = store.load_workspace_run_head()
            if head is None:
                raise RuntimeHostError("control_store_integrity_invalid")
            verified = CoreRunDomainVerifier().verify(store, head.current_run_id)
            installed = adapter_loader(head.current_run_id)
            if installed != verified.runtime_adapter:
                raise RuntimeHostError("runtime_adapter_binding_mismatch")
            action = classify_core_run_next_action(verified)
            return InitializedRuntime(
                verified=verified, action=action, initialized=False
            )
    except RuntimeHostError:
        raise
    except Exception as exc:
        raise RuntimeHostError("control_store_integrity_invalid") from exc


def initialize_or_open_runtime(
    workspace: Path,
    *,
    adapter_loader: AdapterLoader,
) -> InitializedRuntime:
    authority = classify_workspace_authority(workspace)
    if authority.kind == "sqlite":
        return _verify_existing(workspace, adapter_loader=adapter_loader)
    if authority.kind == "legacy":
        raise RuntimeHostError("legacy_workspace_unsupported")
    if authority.kind == "invalid_sqlite":
        raise RuntimeHostError("control_store_integrity_invalid")

    config_bytes = _read_regular_file(workspace / "config.yaml")
    sources_bytes = _read_regular_file(workspace / "sources.yaml")
    config = _load_yaml_mapping(config_bytes)
    _load_yaml_mapping(sources_bytes)
    try:
        bootstrap = WorkspaceControlStoreBootstrapV2.model_validate(
            config.get("controlstore_v2"),
            strict=True,
        )
        adapter = adapter_loader(bootstrap.run_id)
        sources_sha256 = hashlib.sha256(sources_bytes).hexdigest()
        derive_runtime_source_plan(
            sources_bytes,
            run_id=bootstrap.run_id,
            sources_config_sha256=sources_sha256,
            run_direction=bootstrap.run_direction,
            workspace_root=workspace,
        )
        request = CoreRunInitializeRequest.model_validate(
            {
                "schema_version": CoreRunInitializeRequest.schema_id,
                "request_id": derived_id(
                    "REQ-CX-INIT",
                    bootstrap.workspace_id,
                    bootstrap.run_id,
                ),
                "workspace_id": bootstrap.workspace_id,
                "run_id": bootstrap.run_id,
                "runtime": bootstrap.runtime,
                "expected_store_revision": 0,
                "run_direction": bootstrap.run_direction.model_dump(
                    mode="json", exclude_unset=False
                ),
                "workspace_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                "sources_config_sha256": sources_sha256,
                "role_topology": bootstrap.role_topology,
                "gate_strictness": bootstrap.gate_strictness,
                "input_governance_required": bootstrap.input_governance_required,
                "runtime_adapter_binding": adapter.model_dump(
                    mode="json", exclude_unset=False
                ),
            },
            strict=True,
        )
    except (CoreRunError, ValidationError, ValueError) as exc:
        raise RuntimeHostError("runtime_initialization_input_invalid") from exc
    result = CoreRunService(workspace).initialize(request)
    if result.status not in {"committed", "replayed"}:
        raise RuntimeHostError(result.error_code or "control_store_integrity_invalid")
    current = _verify_existing(workspace, adapter_loader=adapter_loader)
    return InitializedRuntime(
        verified=current.verified,
        action=current.action,
        initialized=result.status == "committed",
    )


__all__ = ["AdapterLoader", "InitializedRuntime", "initialize_or_open_runtime"]
