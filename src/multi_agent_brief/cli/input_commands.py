"""inputs — workspace input classification and governance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multi_agent_brief.inputs.classifier import classify_input_dir
from multi_agent_brief.inputs.extractor import extract_input_documents


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the inputs subcommand group."""
    inputs_parser = subparsers.add_parser(
        "inputs", help="Input file classification and governance."
    )
    inputs_sub = inputs_parser.add_subparsers(
        dest="inputs_action", required=True
    )

    classify_parser = inputs_sub.add_parser(
        "classify",
        help="Classify input/ files by role (evidence / feedback / instruction / context).",
    )
    classify_parser.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    classify_parser.add_argument(
        "--output",
        help="Output JSON path (default: <output.path>/input_classification.json).",
    )
    classify_parser.add_argument(
        "--quiet", action="store_true", help="Suppress summary output."
    )

    extract_parser = inputs_sub.add_parser(
        "extract",
        help=(
            "Convert PDF/DOCX/image files under input/ to Markdown using MinerU"
            " before classification."
        ),
    )
    extract_parser.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    extract_parser.add_argument(
        "--backend",
        default="pipeline",
        help="MinerU backend for local CLI mode. Default: pipeline.",
    )
    extract_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate .mineru.md files even when they already exist.",
    )
    extract_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List extractable input files without running MinerU or writing Markdown.",
    )
    extract_parser.add_argument(
        "--output",
        help="Output JSON path (default: <output.path>/input_extraction_report.json).",
    )
    extract_parser.add_argument(
        "--quiet", action="store_true", help="Suppress summary output."
    )


def handle(args: argparse.Namespace) -> int:
    """Fail-closed stub for the retired public CLI surface.

    The parser registration is retained so the authority guard can return
    the typed rejection for workspace invocations; any no-workspace bypass
    lands here instead of executing legacy code.
    """

    print("runtime_command_unsupported")
    return 1

# NOTE: the public command surface of this module is retired. The
# SQLite ControlStore is the sole runtime authority; only the parser
# registration (typed rejections) and the stub below remain.
