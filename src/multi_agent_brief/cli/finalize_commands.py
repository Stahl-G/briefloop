"""finalize — regenerate reader-facing artifacts command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from multi_agent_brief.core.config import build_run_settings, load_config
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
    config = load_config(str(config_path))
    settings = build_run_settings(
        config=config,
        input_dir=None,
        output_dir=args.output,
        name=None,
        language=None,
        audience=None,
    )

    try:
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
            docx_template=(config.get("output", {}) or {}).get(
                "docx_template", "default"
            ),
            source_appendix_config=(config.get("output", {}) or {}).get(
                "source_appendix", {}
            ),
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"[finalize] Error: {exc}", file=sys.stderr)
        return 1

    print(f"[finalize] Reader brief: {result.reader_brief}")
    if result.named_reader_brief:
        print(
            f"[finalize] Named reader brief:"
            f" {result.named_reader_brief}"
        )
    if result.reader_docx:
        print(f"[finalize] Reader DOCX: {result.reader_docx}")
    elif result.docx_generation != "not_requested":
        print(
            f"[finalize] DOCX generation: {result.docx_generation}"
        )
    if result.source_appendix:
        print(f"[finalize] Source appendix: {result.source_appendix}")
    elif result.source_appendix_generation not in {"not_requested", "generated"}:
        print(f"[finalize] Source appendix: {result.source_appendix_generation}")
    print(
        "[finalize] Internal [src:CLAIM_ID] markers stripped from"
        " reader-facing artifacts."
    )
    return 0
