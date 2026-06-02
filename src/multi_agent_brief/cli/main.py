from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief import __version__
from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.cli.init_wizard import build_profile_from_args, create_demo_workspace, create_workspace
from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.config import build_run_settings, load_config
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


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

    init_parser = subparsers.add_parser("init", help="Create a reusable brief workspace.")
    init_parser.add_argument("target", nargs="?", default="brief-workspace", help="Target workspace directory.")
    init_parser.add_argument("--demo", action="store_true", help="Create the existing synthetic demo workspace.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing init files.")
    init_parser.add_argument("--language", choices=["en-US", "zh-CN", "bilingual"], help="Wizard/interface language.")
    init_parser.add_argument("--output-language", choices=["en-US", "zh-CN", "bilingual"], help="Generated brief language.")
    init_parser.add_argument("--company", help="Company or organization name.")
    init_parser.add_argument("--role", help="User role, e.g. strategy_office.")
    init_parser.add_argument("--industry", help="Industry slug, e.g. solar.")
    init_parser.add_argument("--title", help="Brief title.")
    init_parser.add_argument("--audience", help="Target reader group.")
    init_parser.add_argument("--focus-areas", help="Comma-separated focus areas.")
    init_parser.add_argument("--cadence", choices=["weekly", "biweekly", "monthly", "ad_hoc"], help="Reporting cadence.")
    init_parser.add_argument("--selector-max-items", type=int, help="Maximum selected items per brief.")
    init_parser.add_argument("--rag", choices=["on", "off"], help="Enable or disable retrieval settings.")
    init_parser.add_argument("--retrieval-provider", choices=["ollama", "gemini"], help="Retrieval provider.")
    init_parser.add_argument("--output-formats", help="Comma-separated output formats.")
    init_parser.add_argument("--source-profile", choices=["conservative", "research", "aggressive_signal", "custom"], help="Source collection profile.")

    doctor_parser = subparsers.add_parser("doctor", help="Check source configuration health.")
    doctor_parser.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")

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
    create_demo_workspace(target, force=args.force)
    print(f"Created demo workspace: {target}")
    print(f"Run: multi-agent-brief run --config {target / 'config.yaml'}")
    return 0


def init_workspace_from_args(args: argparse.Namespace) -> int:
    target = Path(args.target)
    profile = build_profile_from_args(args)
    create_workspace(target, profile, force=args.force)
    print(f"Created brief workspace: {target}")
    print(f"Run: multi-agent-brief run --config {target / 'config.yaml'}")
    return 0


def run_doctor_from_args(args: argparse.Namespace) -> int:
    results = run_doctor(config_path=args.config)
    print(format_doctor_report(results))
    errors = sum(1 for r in results if r.status == "ERROR")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_pipeline_from_args(args)
    if args.command == "audit":
        return run_audit_from_args(args)
    if args.command == "init":
        return init_demo_from_args(args) if args.demo else init_workspace_from_args(args)
    if args.command == "doctor":
        return run_doctor_from_args(args)
    if args.command == "version":
        print(__version__)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
