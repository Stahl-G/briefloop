#!/usr/bin/env python3
"""Create a deterministic, API-free BriefLoop demo workspace."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "examples" / "reference-workspaces" / "industry-weekly-demo"
ARTIFACTS_DIR = REFERENCE_DIR / "artifacts"


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _prepare_output_dir(output_dir: str | None) -> Path:
    if not output_dir:
        return Path(tempfile.mkdtemp(prefix="briefloop-demo.")).resolve()

    path = Path(output_dir).expanduser().resolve()
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"output path exists and is not a directory: {path}")
        if any(path.iterdir()):
            raise RuntimeError(f"output directory exists and is not empty: {path}")
    else:
        path.mkdir(parents=True)
    return path


def create_demo_workspace(output_dir: str | None = None) -> Path:
    if not ARTIFACTS_DIR.is_dir():
        raise RuntimeError(f"reference artifacts not found: {ARTIFACTS_DIR}")

    root = _prepare_output_dir(output_dir)
    workspace = root / "industry-weekly-demo"
    for directory in (
        workspace / "input" / "sources",
        workspace / "output" / "delivery",
        workspace / "output" / "intermediate" / "gates",
        workspace / "output" / "audit",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    _write_text(
        workspace / "config.yaml",
        """project:
  name: BriefLoop Industry Weekly Demo
output:
  path: output
input:
  path: input
""",
    )
    _write_text(
        workspace / "sources.yaml",
        """manual:
  enabled: false
  sources: []
""",
    )
    _write_text(
        workspace / "user.md",
        """# BriefLoop Industry Weekly Demo

This workspace is copied from the public-safe reference package. It is a
deterministic demo of artifact traceability, not a live source run.
""",
    )

    copies = [
        (REFERENCE_DIR / "README.md", workspace / "README.md"),
        (ARTIFACTS_DIR / "final_brief.md", workspace / "output" / "delivery" / "brief.md"),
        (
            ARTIFACTS_DIR / "claim_ledger.json",
            workspace / "output" / "intermediate" / "claim_ledger.json",
        ),
        (
            ARTIFACTS_DIR / "quality_gate_report.json",
            workspace / "output" / "intermediate" / "quality_gate_report.json",
        ),
        (
            ARTIFACTS_DIR / "quality_gate_report.json",
            workspace
            / "output"
            / "intermediate"
            / "gates"
            / "auditor_quality_gate_report.json",
        ),
        (
            ARTIFACTS_DIR / "quality_summary.md",
            workspace / "output" / "intermediate" / "quality_summary.md",
        ),
        (
            ARTIFACTS_DIR / "source_appendix.md",
            workspace / "output" / "source_appendix.md",
        ),
        (
            ARTIFACTS_DIR / "event_log_excerpt.jsonl",
            workspace / "output" / "intermediate" / "event_log_excerpt.jsonl",
        ),
    ]
    for source, target in copies:
        shutil.copyfile(source, target)

    _write_text(
        workspace / "output" / "audit" / "README.md",
        """# Demo Audit Bundle Notes

This deterministic demo keeps audit artifacts in the workspace for inspection.
It is not a release approval, benchmark result, semantic proof, or delivery
authorization.
""",
    )
    return workspace


def _print_success(workspace: Path) -> None:
    print(
        f"""BriefLoop demo complete.

Workspace:
- {workspace}

Open:
- {workspace / "output" / "delivery" / "brief.md"}
- {workspace / "output" / "intermediate" / "claim_ledger.json"}
- {workspace / "output" / "intermediate" / "quality_summary.md"}
- {workspace / "output" / "source_appendix.md"}
- {workspace / "output" / "intermediate" / "event_log_excerpt.jsonl"}

This demo is deterministic and does not call an LLM, fetch sources, or require
API keys. It shows traceability and process accountability, not semantic proof
or output-quality improvement proof."""
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        help="Directory where the demo workspace root should be created.",
    )
    args = parser.parse_args(argv)
    try:
        workspace = create_demo_workspace(output_dir=args.output)
    except RuntimeError as exc:
        print(f"[demo] {exc}", file=sys.stderr)
        return 1
    _print_success(workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
