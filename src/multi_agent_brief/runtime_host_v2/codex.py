"""Deterministic identity of the installed packaged Codex runtime kit."""

from __future__ import annotations

import hashlib
from importlib import resources
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


def _read_asset(relative: str) -> bytes:
    try:
        return (
            resources.files("multi_agent_brief")
            .joinpath("runtime_kits", "codex", *relative.split("/"))
            .read_bytes()
        )
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeHostError("runtime_adapter_binding_mismatch") from exc


def load_codex_adapter_binding(run_id: str) -> RuntimeAdapterBinding:
    relative_assets = [
        "config.toml",
        "skills/briefloop/SKILL.md",
        "skills/briefloop/references/controlstore-v2.md",
        *(f"agents/briefloop-{role_id}.toml" for role_id in _ROLE_IDS),
    ]
    contents = {relative: _read_asset(relative) for relative in relative_assets}
    try:
        config = tomllib.loads(contents["config.toml"].decode("utf-8"))
        agents = config["agents"]
        if agents != {"max_threads": 6, "max_depth": 1}:
            raise RuntimeHostError("runtime_adapter_binding_mismatch")
        for role_id in _ROLE_IDS:
            payload = tomllib.loads(
                contents[f"agents/briefloop-{role_id}.toml"].decode("utf-8")
            )
            if payload.get("name") != role_id:
                raise RuntimeHostError("runtime_adapter_binding_mismatch")
    except RuntimeHostError:
        raise
    except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeHostError("runtime_adapter_binding_mismatch") from exc
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
        raise RuntimeHostError("runtime_adapter_binding_mismatch") from exc


__all__ = ["load_codex_adapter_binding"]
