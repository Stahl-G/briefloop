from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import yaml

from multi_agent_brief.cli.authority_guard import LEGACY_CONTROL_PATHS


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


def _controlstore_adapter(run_id: str):
    from copy import deepcopy

    from multi_agent_brief.contracts.v2 import RuntimeAdapterBinding
    from multi_agent_brief.control_store.serialization import canonical_fingerprint

    payload = deepcopy(RuntimeAdapterBinding.minimal_example)
    payload.update(
        run_id=run_id,
        runtime="codex",
        adapter_id="briefloop-codex-controlstore",
        role_ids=[
            "analyst",
            "auditor",
            "claim-ledger",
            "editor",
            "scout",
            "screener",
            "source-planner",
            "source-provider",
        ],
        supported_role_topologies=["default", "single_session", "strict"],
    )
    payload.pop("binding_fingerprint", None)
    payload["binding_fingerprint"] = canonical_fingerprint(payload)
    return RuntimeAdapterBinding.model_validate(payload, strict=True)


def initialize_workspace(path: Path) -> Path:
    # LEGACY-DELETE: retired public `state init --runtime operator`; workspace
    # bootstrap goes through the deterministic ControlStore seam only.
    from multi_agent_brief.cli.init_wizard import create_workspace
    from multi_agent_brief.runtime_host_v2.initialization import initialize_or_open_runtime
    from multi_agent_brief.workspace.init_profile import InitProfile

    create_workspace(
        path,
        InitProfile(
            company="ExampleCo",
            industry="manufacturing",
            brief_title="ExampleCo weekly brief",
            task_objective="Prepare the weekly manufacturing brief.",
            audience="management",
            audience_profile="management",
            focus_areas=["operations", "policy"],
            output_formats=["markdown", "docx"],
            web_search_mode="disabled",
            web_search_enabled=False,
        ),
        force=True,
    )
    initialize_or_open_runtime(path, adapter_loader=_controlstore_adapter)
    return path


def write_minimal_workspace_under(base_path: Path, name: str = "ws", **kwargs: object) -> Path:
    return write_minimal_workspace(base_path / name, **kwargs)


def write_workspace_files_under(base_path: Path, name: str = "ws", **kwargs: object) -> Path:
    return write_workspace_files(base_path / name, **kwargs)


def write_legacy_control_files(workspace: Path) -> Path:
    """Materialize the legacy control surface `classify_workspace_authority` keys on.

    Retired-surface probes assert that a legacy workspace is rejected with a typed
    token and zero writes. Building that precondition used to go through the
    runtime-state writers, which LD2-3 deletes; this seam writes the control files
    directly instead.

    Paths come from `LEGACY_CONTROL_PATHS` rather than literals so the fixture
    follows the guard whenever the guard's paths change.

    The workspace must not already carry `briefloop.db` -- SQLite authority is
    classified first and would mask the legacy classification.
    """

    for relative in LEGACY_CONTROL_PATHS:
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # `.jsonl` records are newline-delimited, so an empty file is the
        # minimal valid document; the rest are minimal valid JSON objects.
        target.write_text("" if target.suffix == ".jsonl" else "{}\n", encoding="utf-8")
    return workspace


def initialized_workspace_writer(
    writer: Callable[..., Path] = write_minimal_workspace_under,
    **default_kwargs: object,
) -> Callable[[Path], Path]:
    # LEGACY-DELETE: the retired operator writer and its kwargs are ignored;
    # bootstrap is the deterministic ControlStore seam in initialize_workspace.
    def _write(base_path: Path) -> Path:
        return initialize_workspace(base_path)

    return _write
