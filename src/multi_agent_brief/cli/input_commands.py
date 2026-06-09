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
    """Dispatch inputs subcommands."""
    if args.inputs_action == "classify":
        return _handle_classify(args)
    if args.inputs_action == "extract":
        return _handle_extract(args)
    print(f"[inputs] Unknown action: {args.inputs_action}", flush=True)
    return 1


# ---------------------------------------------------------------------------
# Classify command
# ---------------------------------------------------------------------------

def _handle_classify(args: argparse.Namespace) -> int:
    """Classify input/ files by role."""
    resolved = _resolve_workspace_paths(args.config)
    if resolved.get("error"):
        print(resolved["error"], flush=True)
        return 1
    input_path = resolved["input_path"]
    output_path = (
        Path(args.output)
        if args.output
        else resolved["output_dir"] / "input_classification.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    classified = classify_input_dir(input_path)

    payload = json.dumps(classified, ensure_ascii=False, indent=2)
    output_path.write_text(payload, encoding="utf-8")

    if not args.quiet:
        _print_summary(classified)

    print(f"[inputs] Classification written: {output_path}", flush=True)
    return 0


def _handle_extract(args: argparse.Namespace) -> int:
    """Extract binary input documents into Markdown with MinerU."""
    resolved = _resolve_workspace_paths(args.config)
    if resolved.get("error"):
        print(resolved["error"], flush=True)
        return 1

    input_path = resolved["input_path"]
    output_dir = resolved["output_dir"]
    report_path = Path(args.output) if args.output else output_dir / "input_extraction_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report = extract_input_documents(
        input_path=input_path,
        workspace=resolved["workspace"],
        output_dir=output_dir,
        backend=str(args.backend or "pipeline"),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not args.quiet:
        _print_extraction_summary(report)

    print(f"[inputs] Extraction report written: {report_path}", flush=True)
    return 1 if report.get("status") in {"failed", "completed_with_errors"} else 0


def _resolve_workspace_paths(config: str | Path) -> dict[str, Any]:
    """Resolve workspace, input path, and output dir from config.yaml."""
    config_path = Path(config).resolve()
    if not config_path.exists():
        return {"error": f"[inputs] config.yaml not found: {config_path}"}

    workspace = config_path.parent

    # ── load config to read input.path / output.path ──
    from multi_agent_brief.core.config import load_config
    try:
        cfg = load_config(config_path)
    except Exception as exc:
        return {"error": f"[inputs] Failed to read config: {exc}"}

    input_cfg = cfg.get("input", {}) or {}
    output_cfg = cfg.get("output", {}) or {}

    raw_input_path = input_cfg.get("path", "input")
    input_path = Path(raw_input_path)
    if not input_path.is_absolute():
        input_path = workspace / input_path

    if not input_path.is_dir():
        return {"error": f"[inputs] No input directory found: {input_path}"}

    raw_output_path = output_cfg.get("path", "output")
    output_dir = Path(raw_output_path)
    if not output_dir.is_absolute():
        output_dir = workspace / output_dir

    return {
        "workspace": workspace,
        "input_path": input_path,
        "output_dir": output_dir,
    }


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

def _print_summary(classified: dict[str, Any]) -> None:
    """Print a human-readable summary."""
    print()
    print("=== Input Classification Summary ===")
    for role, label in [
        ("evidence", "📄 Evidence (claims-eligible)"),
        ("feedback", "✏️  Feedback (editorial direction)"),
        ("instruction", "📋 Instructions (task guidance)"),
        ("context", "📎 Context (background reference)"),
    ]:
        files = classified.get(role, [])
        count = len(files)
        print(f"  {label}: {count} file(s)")
        for f in files:
            note = ""
            if f.get("flagged"):
                note = " ⚠️ (suspicious content markers)"
            print(f"    - {f['name']}{note}")

    skipped_files = classified.get("skipped", [])
    skipped_count = len(skipped_files)
    print(f"  ⏭️  Skipped: {skipped_count} file(s)")
    if skipped_count > 0:
        for f in skipped_files:
            reason = f.get("reason", "unknown")
            suggested = f" → {f['suggested_role']}" if f.get("suggested_role") else ""
            print(f"    - {f['name']}  ({reason}{suggested})")
    print()


def _print_extraction_summary(report: dict[str, Any]) -> None:
    """Print a human-readable extraction summary."""
    print()
    print("=== Input Document Extraction Summary ===")
    print(f"  Status: {report.get('status', 'unknown')}")
    print(f"  Extracted: {len(report.get('extracted', []))} file(s)")
    for item in report.get("extracted", []):
        print(f"    - {item.get('input_relative_path')} -> {item.get('markdown_path')}")
    print(f"  Skipped: {len(report.get('skipped', []))} file(s)")
    for item in report.get("skipped", []):
        reason = item.get("reason") or item.get("status", "unknown")
        print(f"    - {item.get('input_relative_path', item.get('name'))} ({reason})")
    errors = report.get("errors", [])
    if errors:
        print("  Errors:")
        for error in errors:
            print(f"    - {error}")
    print()
