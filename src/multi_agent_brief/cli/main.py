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
    sources_commands,
    competitors_commands,
    capability_commands,
    status_commands,
    runtime_commands,
    experiments_commands,
    product_commands,
    secrets_commands,
    contract_commands,
    intake_v2_commands,
    core_v2_commands,
    market_data_commands,
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

    # Capability discovery and setup
    capability_commands.register_features(subparsers)
    capability_commands.register_capability(subparsers)
    capability_commands.register_recommend(subparsers)
    capability_commands.register_setup(subparsers)

    # Read-only writer-facing workspace status
    status_commands.register(subparsers)

    # Experimental measurement harnesses
    experiments_commands.register(subparsers)

    # Workspace runtime kit install
    runtime_commands.register(subparsers)

    # Experimental product-layer report contracts
    product_commands.register_new_workspace(subparsers)
    product_commands.register_packs(subparsers)
    product_commands.register_validate_report_spec(subparsers)
    product_commands.register_extract(subparsers)
    product_commands.register_quality(subparsers)

    # Read-only strict contract schemas and examples
    contract_commands.register(subparsers)

    # Market data snapshot acquisition and projection
    market_data_commands.register(subparsers)

    # Dormant fresh-v2 ControlStore intake; no active adapter invokes it.
    intake_v2_commands.register(subparsers)

    # Dormant fresh-v2 core-run harness; no active adapter invokes it.
    core_v2_commands.register(subparsers)

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

    if cmd == "doctor":
        return sources_commands.handle_doctor(args)

    if cmd == "sources":
        return sources_commands.handle_sources(args)

    if cmd == "secrets":
        return secrets_commands.handle(args)

    if cmd == "competitors":
        return competitors_commands.handle(args)

    if cmd in ("features", "capability"):
        return capability_commands.handle_features_capability(args)

    if cmd == "recommend":
        return capability_commands.handle_recommend(args)

    if cmd == "setup":
        return capability_commands.handle_setup(args)

    if cmd == "status":
        return status_commands.handle(args)

    if cmd == "experiments":
        return experiments_commands.handle(args)

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

    if cmd == "quality":
        return product_commands.handle_quality(args)

    if cmd == "contract":
        return contract_commands.handle(args)

    if cmd == "market-data":
        return market_data_commands.handle(args)

    if cmd == "intake-v2":
        return intake_v2_commands.handle(args)

    if cmd == "core-v2":
        return core_v2_commands.handle(args)

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = getattr(args, "workspace", None)
    if workspace is None:
        config = getattr(args, "config", None)
        if config is not None:
            workspace = Path(config).expanduser().resolve().parent
    if workspace is not None:
        from multi_agent_brief.cli.authority_guard import (
            active_command_authority_error,
        )

        error = active_command_authority_error(
            Path(workspace).expanduser().resolve(),
            str(args.command),
        )
        if error is not None:
            print(error)
            return 1
    return _dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
