"""finalize — regenerate reader-facing artifacts command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from multi_agent_brief.core.config import build_run_settings, get_output_config, load_config
from multi_agent_brief.experiments.target_contract import load_experiment_080_condition_metadata
from multi_agent_brief.orchestrator.runtime_state import (
    E_ACTIVE_REPAIR_OPEN,
    E_ASSESSMENT_TARGET_COMPLETE,
    RuntimeStateError,
    check_runtime_state,
    raise_if_auditable_target_complete_blocks_downstream,
    raise_if_active_repair_open,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.errors import E_TRANSACTION_INTEGRITY
from multi_agent_brief.outputs.finalize import finalize_reader_outputs


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the finalize subparser."""
    finalize_parser = subparsers.add_parser(
        "finalize",
        help="Regenerate reader-facing Markdown/DOCX from"
        " output/intermediate/audited_brief.md.",
    )
    finalize_parser.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )
    finalize_parser.add_argument("--output", help="Override output directory.")


def handle(args: argparse.Namespace) -> int:
    """Regenerate final reader-facing artifacts from audited internal markdown.

    This is a deterministic delivery gate for agent-assisted workflows where
    analyst/editor/auditor subagents write
    output/intermediate/audited_brief.md before reader-facing artifacts are
    rendered.
    """
    config_path = Path(args.config).resolve()
    workspace = config_path.parent
    config = load_config(str(config_path))
    settings = build_run_settings(
        config=config,
        input_dir=None,
        output_dir=args.output,
        name=None,
        language=None,
        audience=None,
    )
    output_config = get_output_config(config)

    try:
        _preflight_runtime_state_before_finalize(workspace)
        result = finalize_reader_outputs(
            output_dir=settings["output_dir"],
            project_name=settings["project_name"],
            output_formats=settings.get("output_formats", ["markdown"]),
            output_footer=settings.get("output_footer", ""),
            output_named_outputs=bool(
                settings.get("output_named_outputs", True)
            ),
            output_filename_template=settings.get("output_filename_template", ""),
            output_filename_tokens=settings.get("output_filename_tokens", {}),
            docx_template=output_config.get("docx_template", "default"),
            source_appendix_config=output_config.get("source_appendix", {}),
            workspace_dir=workspace,
        )
    except (FileNotFoundError, ValueError, RuntimeError, RuntimeStateError) as exc:
        print(f"[finalize] Error: {exc}", file=sys.stderr)
        return 1

    print(f"[finalize] Delivery Markdown: {result.delivery_markdown}")
    if result.delivery_docx:
        print(f"[finalize] Delivery DOCX: {result.delivery_docx}")
    elif result.docx_generation != "not_requested":
        print(
            f"[finalize] DOCX generation: {result.docx_generation}"
        )
    if result.source_appendix:
        print(f"[finalize] Source appendix audit copy: {result.source_appendix}")
    elif result.source_appendix_generation not in {"not_requested", "generated"}:
        print(f"[finalize] Source appendix audit copy: {result.source_appendix_generation}")
    if result.delivery_snapshot_dir:
        print(f"[finalize] Delivery snapshot: {result.delivery_snapshot_dir}")
    print("[finalize] Audit records remain under output/intermediate/.")
    print(
        "[finalize] Internal [src:<claim_id>] markers stripped from"
        " reader-facing artifacts."
    )
    return 0


def _preflight_runtime_state_before_finalize(workspace: Path) -> None:
    paths = runtime_state_paths(workspace)
    workflow_path = paths["workflow_state"]
    if not workflow_path.exists():
        return
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeStateError(
            f"workflow_state.json is unreadable before finalize: {exc}",
        ) from exc
    if not isinstance(workflow, dict):
        raise RuntimeStateError("workflow_state.json must contain an object before finalize.")
    try:
        raise_if_active_repair_open(workspace=workspace, workflow=workflow)
        if paths["runtime_manifest"].exists():
            try:
                check_runtime_state(workspace=workspace, actor="cli")
            except RuntimeStateError:
                raise
            except Exception as exc:
                raise RuntimeStateError(f"Unable to verify runtime state integrity before finalize: {exc}") from exc
            try:
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeStateError(
                    f"workflow_state.json is unreadable after runtime state refresh: {exc}",
                ) from exc
            if not isinstance(workflow, dict):
                raise RuntimeStateError("workflow_state.json must contain an object after runtime state refresh.")
            if _workspace_uses_auditable_brief_target(workspace):
                _raise_if_run_integrity_not_reference_eligible_before_finalize(workflow)
        raise_if_auditable_target_complete_blocks_downstream(
            workspace=workspace,
            workflow=workflow,
            command="finalize",
        )
    except RuntimeStateError as exc:
        if exc.error_code == E_ACTIVE_REPAIR_OPEN:
            raise
        if exc.error_code == E_ASSESSMENT_TARGET_COMPLETE:
            raise
        if exc.error_code == E_TRANSACTION_INTEGRITY:
            raise
        raise RuntimeStateError(f"Unable to verify runtime state before finalize: {exc}") from exc


def _raise_if_run_integrity_not_reference_eligible_before_finalize(workflow: dict[str, object]) -> None:
    integrity = workflow.get("run_integrity") if isinstance(workflow.get("run_integrity"), dict) else {}
    if integrity.get("status") == "clean" and integrity.get("reference_eligible") is True:
        return
    raise RuntimeStateError(
        "Runtime state integrity check failed because run integrity is not clean before finalize.",
        details={"run_integrity": integrity},
        error_code=E_TRANSACTION_INTEGRITY,
    )


def _workspace_uses_auditable_brief_target(workspace: Path) -> bool:
    metadata = load_experiment_080_condition_metadata(workspace)
    return isinstance(metadata, dict) and metadata.get("assessment_target") == "auditable_brief"
