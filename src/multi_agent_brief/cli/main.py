"""Multi-Agent Brief Workflow — thin CLI router.

Every command group owns its subparser registration and handler logic in a
dedicated module.  main.py only creates the top-level parser, calls each
module's register(), and dispatches parsed args to the matching handler.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from multi_agent_brief import __version__
from multi_agent_brief.cli import (
    run_commands,
    onboard_commands,
    init_commands,
    hermes_commands,
    sources_commands,
    competitors_commands,
    audit_commands,
    finalize_commands,
    deliver_commands,
    analysis_commands,
    capability_commands,
    input_commands,
    status_commands,
    state_commands,
    feedback_commands,
    repair_commands,
    gates_commands,
    eval_cases_commands,
    claude_commands,
    provenance_commands,
    controls_commands,
    runtime_commands,
    improve_commands,
    experiments_commands,
    product_commands,
    secrets_commands,
)


def _default_prog() -> str:
    executable = Path(sys.argv[0]).stem
    return "briefloop" if executable == "briefloop" else "multi-agent-brief"


def build_parser(*, prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog or _default_prog())
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Runtime handoff launchers
    run_commands.register(subparsers)

    # Workspace lifecycle
    onboard_commands.register(subparsers)
    init_commands.register(subparsers)

    # Source health and discovery
    sources_commands.register_doctor(subparsers)
    sources_commands.register_sources(subparsers)
    secrets_commands.register(subparsers)

    # Competitor universe
    competitors_commands.register(subparsers)

    # Quality gates
    audit_commands.register(subparsers)
    finalize_commands.register(subparsers)
    deliver_commands.register(subparsers)

    # Analysis tooling
    analysis_commands.register_analysis_blocks(subparsers)
    analysis_commands.register_limitation_hygiene(subparsers)

    # Capability discovery and setup
    capability_commands.register_features(subparsers)
    capability_commands.register_capability(subparsers)
    capability_commands.register_recommend(subparsers)
    capability_commands.register_setup(subparsers)

    # Input governance
    input_commands.register(subparsers)

    # Read-only writer-facing workspace status
    status_commands.register(subparsers)

    # Orchestrator runtime state
    state_commands.register(subparsers)

    # Feedback and repair planning
    feedback_commands.register(subparsers)
    repair_commands.register(subparsers)

    # Deterministic quality gate controls
    gates_commands.register(subparsers)

    # Public-safe evaluation cases
    eval_cases_commands.register(subparsers)

    # Experimental measurement harnesses
    experiments_commands.register(subparsers)

    # Deterministic provenance projection
    provenance_commands.register(subparsers)

    # Orchestrator control switchboard
    controls_commands.register(subparsers)

    # Improvement Ledger lifecycle
    improve_commands.register(subparsers)

    # Workspace runtime kit install
    runtime_commands.register(subparsers)

    # Experimental product-layer report contracts
    product_commands.register_new_workspace(subparsers)
    product_commands.register_packs(subparsers)
    product_commands.register_validate_report_spec(subparsers)
    product_commands.register_extract(subparsers)

    # Claude Code install helpers
    claude_commands.register(subparsers)

    # Hermes runtime
    hermes_commands.register(subparsers)

    # Meta
    subparsers.add_parser("version", help="Print package version.")

    return parser


# ── dispatch table ──────────────────────────────────────────────────────────
# Each entry maps a command string (and optional sub-action) to a handler.
# For command groups with sub-actions (sources, competitors, hermes) the
# handler internally dispatches on args.<group>_action.


def _dispatch(args: argparse.Namespace) -> int:
    cmd = args.command

    if cmd == "version":
        print(__version__)
        return 0

    if cmd in ("run", "start", "handoff", "prepare"):
        return run_commands.handle(args)

    if cmd == "onboard":
        return onboard_commands.handle(args)

    if cmd == "init":
        return init_commands.handle(args)

    if cmd == "hermes":
        return hermes_commands.handle(args)

    if cmd == "doctor":
        return sources_commands.handle_doctor(args)

    if cmd == "sources":
        return sources_commands.handle_sources(args)

    if cmd == "secrets":
        return secrets_commands.handle(args)

    if cmd == "competitors":
        return competitors_commands.handle(args)

    if cmd == "audit":
        return audit_commands.handle(args)

    if cmd == "finalize":
        return finalize_commands.handle(args)

    if cmd == "deliver":
        return deliver_commands.handle(args)

    if cmd == "analysis-blocks":
        return analysis_commands.handle_analysis_blocks(args)

    if cmd == "limitation-hygiene":
        return analysis_commands.handle_limitation_hygiene(args)

    if cmd in ("features", "capability"):
        return capability_commands.handle_features_capability(args)

    if cmd == "recommend":
        return capability_commands.handle_recommend(args)

    if cmd == "setup":
        return capability_commands.handle_setup(args)

    if cmd == "inputs":
        return input_commands.handle(args)

    if cmd == "status":
        return status_commands.handle(args)

    if cmd == "state":
        return state_commands.handle(args)

    if cmd == "feedback":
        return feedback_commands.handle(args)

    if cmd == "repair":
        return repair_commands.handle(args)

    if cmd == "gates":
        return gates_commands.handle(args)

    if cmd == "eval-cases":
        return eval_cases_commands.handle(args)

    if cmd == "experiments":
        return experiments_commands.handle(args)

    if cmd == "provenance":
        return provenance_commands.handle(args)

    if cmd == "controls":
        return controls_commands.handle(args)

    if cmd == "improve":
        return improve_commands.handle(args)

    if cmd == "runtime":
        return runtime_commands.handle(args)

    if cmd == "packs":
        return product_commands.handle_packs(args)

    if cmd == "new":
        return product_commands.handle_new_workspace(args)

    if cmd == "validate-report-spec":
        return product_commands.handle_validate_report_spec(args)

    if cmd == "extract":
        return product_commands.handle_extract(args)

    if cmd == "claude":
        return claude_commands.handle(args)

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
