from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief import __version__
from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.config import build_run_settings, load_config
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


DEMO_NEWS = {
    "source_url": "https://example.com/synthetic-market-news",
    "published_at": "2026-06-01",
    "items": [
        "A public market tracker reported that utility-scale storage demand continued to expand in the Southwest during May 2026.",
        "A policy update indicated that new interconnection queue reforms may shorten approval timelines for selected renewable projects.",
        "A competitor announced a 2 GW manufacturing capacity expansion plan, with commercial production expected in 2027.",
    ],
}

DEMO_MARKET_DATA = {
    "source_url": "https://example.com/synthetic-market-data",
    "published_at": "2026-06-01",
    "items": [
        "Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels.",
        "Synthetic battery storage system quotes remained broadly stable at $140 per kWh for benchmark project assumptions.",
    ],
}

DEMO_CONFIG = """project:
  name: "Synthetic Market Brief Demo"
  language: "en-US"
  audience: "management"

report:
  date: "2026-06-02"
  max_source_age_days: 14
  fail_on_stale_source: true

input:
  path: "input"

output:
  path: "output"
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="multi-agent-brief")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the local MVP brief pipeline.")
    run_parser.add_argument("input_dir", nargs="?", help="Directory containing .md, .txt, or .json input files.")
    run_parser.add_argument("--config", help="YAML config file. Provides input/output/project settings.")
    run_parser.add_argument("--output", help="Output directory.")
    run_parser.add_argument("--name", help="Brief title.")
    run_parser.add_argument("--language")
    run_parser.add_argument("--audience")

    audit_parser = subparsers.add_parser("audit", help="Run deterministic audit on an existing Markdown brief.")
    audit_parser.add_argument("brief", help="Markdown brief to audit.")
    audit_parser.add_argument("--ledger", required=True, help="Claim Ledger JSON file.")
    audit_parser.add_argument("--output", help="Optional path for audit_report.json.")
    audit_parser.add_argument("--report-date", default="", help="Report date for stale-source checks, e.g. 2026-06-02.")
    audit_parser.add_argument("--max-source-age-days", type=int, help="Maximum allowed source age.")
    audit_parser.add_argument("--fail-on-stale-source", action="store_true", help="Treat stale sources as high-severity findings.")

    init_parser = subparsers.add_parser("init", help="Create a synthetic demo workspace.")
    init_parser.add_argument("target", nargs="?", default="brief-demo", help="Target demo directory.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing demo files.")

    subparsers.add_parser("version", help="Print package version.")
    return parser


def run_pipeline_from_args(args: argparse.Namespace) -> int:
    config = load_config(args.config) if args.config else None
    settings = build_run_settings(
        config=config,
        input_dir=args.input_dir,
        output_dir=args.output,
        name=args.name,
        language=args.language,
        audience=args.audience,
    )
    context = PipelineContext(**settings)
    outputs = BriefPipeline().run(context)
    for output in outputs:
        print(f"[{output.agent_name}] {output.summary}")
    return 0


def run_audit_from_args(args: argparse.Namespace) -> int:
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


def init_demo_from_args(args: argparse.Namespace) -> int:
    target = Path(args.target)
    input_dir = target / "input"
    files = {
        target / "config.yaml": DEMO_CONFIG,
        input_dir / "news.json": json.dumps(DEMO_NEWS, indent=2),
        input_dir / "market_data.json": json.dumps(DEMO_MARKET_DATA, indent=2),
    }
    for path in files:
        if path.exists() and not args.force:
            raise FileExistsError(f"Refusing to overwrite existing file: {path}. Use --force to overwrite.")
    input_dir.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    print(f"Created demo workspace: {target}")
    print(f"Run: multi-agent-brief run --config {target / 'config.yaml'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_pipeline_from_args(args)
    if args.command == "audit":
        return run_audit_from_args(args)
    if args.command == "init":
        return init_demo_from_args(args)
    if args.command == "version":
        print(__version__)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
