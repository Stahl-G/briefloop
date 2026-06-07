"""audit — deterministic audit command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.core.claim_ledger import ClaimLedger


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the audit subparser."""
    audit_parser = subparsers.add_parser(
        "audit",
        help="Run deterministic audit on an existing Markdown brief.",
    )
    audit_parser.add_argument("brief", help="Markdown brief to audit.")
    audit_parser.add_argument(
        "--ledger", required=True, help="Claim Ledger JSON file."
    )
    audit_parser.add_argument(
        "--output", help="Optional path for audit_report.json."
    )
    audit_parser.add_argument(
        "--report-date",
        default="",
        help="Report date for stale-source checks, e.g. 2026-06-02.",
    )
    audit_parser.add_argument(
        "--max-source-age-days", type=int, help="Maximum allowed source age."
    )
    audit_parser.add_argument(
        "--fail-on-stale-source",
        action="store_true",
        help="Treat stale sources as high-severity findings.",
    )


def handle(args: argparse.Namespace) -> int:
    """Run deterministic audit on a Markdown brief."""
    brief_path = Path(args.brief)
    if not brief_path.exists():
        raise FileNotFoundError(f"Brief not found: {brief_path}")
    ledger = ClaimLedger.import_json(args.ledger)
    report = run_deterministic_audit(
        brief_path.read_text(encoding="utf-8"),
        ledger,
        report_date=args.report_date,
        max_source_age_days=args.max_source_age_days,
        fail_on_stale_source=args.fail_on_stale_source,
    )
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0 if report.audit_status != "fail" else 2
