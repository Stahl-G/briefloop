"""analysis-blocks and limitation-hygiene commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief.core.claim_ledger import ClaimLedger


def register_analysis_blocks(subparsers: argparse._SubParsersAction) -> None:
    """Register the analysis-blocks subparser."""
    ab_parser = subparsers.add_parser(
        "analysis-blocks",
        help="Build analysis blocks from a claim ledger.",
    )
    ab_parser.add_argument(
        "--ledger", required=True, help="Path to claim_ledger.json."
    )
    ab_parser.add_argument(
        "--output",
        help="Output path for analysis_blocks.json"
        " (default: same dir as ledger).",
    )
    ab_parser.add_argument(
        "--audience",
        default="default",
        choices=["management", "research", "default"],
        help="Heading style for markdown preview.",
    )
    ab_parser.add_argument(
        "--language",
        default="en-US",
        choices=["en-US", "zh-CN"],
        help="Language for labels.",
    )
    ab_parser.add_argument(
        "--markdown",
        action="store_true",
        help="Also print structured markdown to stdout.",
    )


def register_limitation_hygiene(subparsers: argparse._SubParsersAction) -> None:
    """Register the limitation-hygiene subparser."""
    lh_parser = subparsers.add_parser(
        "limitation-hygiene",
        help="Audit limitation hygiene from a claim ledger.",
    )
    lh_parser.add_argument(
        "--ledger", required=True, help="Path to claim_ledger.json."
    )
    lh_parser.add_argument(
        "--output",
        help="Output path for limitation_hygiene_report.json"
        " (default: same dir as ledger).",
    )


def handle_analysis_blocks(args: argparse.Namespace) -> int:
    """Build analysis blocks from a claim ledger and export JSON."""
    from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
    from multi_agent_brief.analysis_blocks.renderer import (
        render_analysis_blocks,
    )

    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"[error] Claim ledger not found: {ledger_path}")
        return 1

    ledger = ClaimLedger.import_json(ledger_path)
    blocks = build_analysis_blocks(ledger)

    # Default output: same directory as ledger
    output_path = (
        Path(args.output)
        if args.output
        else ledger_path.parent / "analysis_blocks.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = [b.to_dict() for b in blocks]
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[analysis-blocks] Wrote {len(blocks)} blocks to {output_path}"
    )

    if args.markdown:
        md = render_analysis_blocks(
            blocks, ledger, audience=args.audience, language=args.language
        )
        print()
        print(md)

    return 0


def handle_limitation_hygiene(args: argparse.Namespace) -> int:
    """Audit limitation hygiene from a claim ledger."""
    from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
    from multi_agent_brief.audit.limitation_hygiene import (
        audit_limitation_hygiene,
        format_limitation_hygiene_report,
    )

    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"[error] Claim ledger not found: {ledger_path}")
        return 1

    ledger = ClaimLedger.import_json(ledger_path)
    blocks = build_analysis_blocks(ledger)
    report = audit_limitation_hygiene(blocks, ledger)

    # Default output: same directory as ledger
    output_path = (
        Path(args.output)
        if args.output
        else ledger_path.parent / "limitation_hygiene_report.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(format_limitation_hygiene_report(report))
    print(
        f"\n[limitation-hygiene] Wrote report to {output_path}"
    )

    warnings = sum(
        1 for f in report.findings if f.severity == "warning"
    )
    fails = sum(1 for f in report.findings if f.severity == "fail")
    return 1 if fails else 0
