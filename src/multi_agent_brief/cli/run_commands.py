"""run / start / handoff / prepare — runtime handoff launcher commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from multi_agent_brief.cli.start_commands import (
    VALID_RUNTIMES,
    VALID_RUNTIME_RECIPES,
    AgentHandoff,
    build_handoff,
    render_handoff_cli,
    write_handoff_artifacts,
)
from multi_agent_brief.audience_memory import (
    create_audience_profile_snapshot,
    profile_data_from_workspace_config,
)
from multi_agent_brief.controls.contract import ControlSwitchboardError
from multi_agent_brief.controls.switchboard import build_control_switchboard
from multi_agent_brief.improvement.memory import freeze_improvement_memory_for_run
from multi_agent_brief.improvement.state import ImprovementLedgerError
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    check_runtime_state,
    complete_stage_transaction,
    initialize_runtime_state,
    record_decision,
    record_handoff_written,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register run, start, handoff, and prepare subparsers."""

    run_parser = subparsers.add_parser(
        "run",
        help="Run a workspace through the selected agent runtime handoff.",
    )
    run_parser.add_argument(
        "--workspace", help="Path to workspace directory."
    )
    run_parser.add_argument(
        "--config",
        help="Path to workspace config.yaml (convenience alias for --workspace).",
    )
    run_parser.add_argument(
        "--runtime",
        default="auto",
        choices=list(VALID_RUNTIMES),
        help="Target runtime for handoff (default: auto, resolves to hermes).",
    )
    run_parser.add_argument(
        "--recipe",
        default="full",
        choices=list(VALID_RUNTIME_RECIPES),
        help="Runtime handoff recipe: full or fast-rerun (guidance only, not a Python pipeline).",
    )
    run_parser.add_argument(
        "--repo-workdir",
        help="Repository workdir (default: auto-detect source repo).",
    )
    run_parser.add_argument(
        "--venv", help="Virtual env path (default: auto-detect)."
    )
    run_parser.add_argument(
        "--skip-doctor", action="store_true", help="Skip doctor check."
    )

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="[legacy] Replaced by 'multi-agent-brief run'.",
    )
    prepare_parser.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )
    prepare_parser.add_argument("--input", help="Override input directory.")
    prepare_parser.add_argument("--output", help="Override output directory.")

    start_parser = subparsers.add_parser(
        "start",
        help="Alias for run: create runtime handoff for the current agent.",
    )
    start_parser.add_argument(
        "--workspace", help="Path to workspace directory."
    )
    start_parser.add_argument(
        "--runtime",
        default="auto",
        choices=list(VALID_RUNTIMES),
        help="Target runtime for handoff (default: auto, resolves to hermes).",
    )
    start_parser.add_argument(
        "--recipe",
        default="full",
        choices=list(VALID_RUNTIME_RECIPES),
        help="Runtime handoff recipe: full or fast-rerun (guidance only, not a Python pipeline).",
    )
    start_parser.add_argument(
        "--repo-workdir",
        help="Repository workdir (default: auto-detect source repo).",
    )
    start_parser.add_argument(
        "--venv", help="Virtual env path (default: auto-detect)."
    )
    start_parser.add_argument(
        "--skip-doctor", action="store_true", help="Skip doctor check."
    )

    handoff_parser = subparsers.add_parser(
        "handoff",
        help="Generate a runtime handoff artifact from a workspace config.",
    )
    handoff_parser.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    handoff_parser.add_argument(
        "--runtime",
        default="auto",
        choices=list(VALID_RUNTIMES),
        help="Target runtime for handoff (default: auto, resolves to hermes).",
    )
    handoff_parser.add_argument(
        "--recipe",
        default="full",
        choices=list(VALID_RUNTIME_RECIPES),
        help="Runtime handoff recipe: full or fast-rerun (guidance only, not a Python pipeline).",
    )
    handoff_parser.add_argument(
        "--repo-workdir",
        help="Repository workdir (default: auto-detect source repo).",
    )
    handoff_parser.add_argument(
        "--venv", help="Virtual env path (default: auto-detect)."
    )
    handoff_parser.add_argument(
        "--skip-doctor", action="store_true", help="Skip doctor check."
    )


def handle(args: argparse.Namespace) -> int:
    """Dispatch run / start / handoff / prepare commands."""
    if args.command == "prepare":
        return _run_prepare(args)
    if args.command == "handoff":
        return _run_handoff(args)
    # run and start both use the launcher
    return _run_launcher(args)


def _resolve_workspace(args: argparse.Namespace) -> Path | None:
    """Resolve workspace path from --workspace, --config, or CWD auto-detect."""
    workspace = getattr(args, "workspace", None)
    config_path = getattr(args, "config", None)

    if config_path and not workspace:
        cp = Path(config_path).resolve()
        if cp.is_file():
            workspace = str(cp.parent)
        elif cp.is_dir():
            workspace = str(cp)

    if not workspace:
        cwd = Path.cwd()
        if (cwd / "config.yaml").exists() and (cwd / "user.md").exists():
            workspace = str(cwd)

    if not workspace:
        return None

    ws_path = Path(workspace).resolve()
    if not (ws_path / "config.yaml").exists():
        return None
    return ws_path


def _run_launcher(args: argparse.Namespace) -> int:
    """run — standard runtime handoff launcher."""
    prefix = (
        "[start]" if getattr(args, "command", None) == "start" else "[run]"
    )

    workspace_path = _resolve_workspace(args)
    if workspace_path is None:
        print(f"{prefix} No workspace found.")
        print()
        print("For a real workspace:")
        print("  multi-agent-brief onboard")
        print("  multi-agent-brief init <workspace> --from-onboarding onboarding.json")
        print()
        print("For a demo only:")
        print("  multi-agent-brief init <workspace> --demo")
        return 1

    try:
        repo_workdir = resolve_repo_workdir(
            getattr(args, "repo_workdir", None),
            workspace=workspace_path,
        )
    except ValueError as exc:
        print(f"{prefix} {exc}")
        return 1

    handoff = build_handoff(
        workspace=workspace_path,
        repo_workdir=repo_workdir,
        runtime=args.runtime,
        recipe=getattr(args, "recipe", "full"),
        venv=getattr(args, "venv", None),
        run_doctor=not getattr(args, "skip_doctor", False),
    )

    written = _write_handoff_and_state(
        handoff=handoff,
        workspace=workspace_path,
        repo_workdir=repo_workdir,
        prefix=prefix,
    )
    if written is None:
        return 1
    md_path, json_path = written
    print(render_handoff_cli(handoff))
    print(f"{prefix} Handoff written: {md_path}")
    print(f"{prefix} Handoff JSON:  {json_path}")
    return 0


def _run_prepare(args: argparse.Namespace) -> int:
    """[legacy] prepare — replaced by the runtime handoff launcher."""
    print(
        "[legacy] prepare has been replaced by:"
        " multi-agent-brief run --workspace <workspace>"
    )
    return 1


def _run_handoff(args: argparse.Namespace) -> int:
    """handoff — generate runtime handoff from workspace config."""
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[error] config.yaml not found: {config_path}")
        return 1
    workspace = config_path.parent
    try:
        repo_workdir = resolve_repo_workdir(
            getattr(args, "repo_workdir", None),
            workspace=workspace,
        )
    except ValueError as exc:
        print(f"[handoff] {exc}")
        return 1

    handoff = build_handoff(
        workspace=workspace,
        repo_workdir=repo_workdir,
        runtime=args.runtime,
        recipe=getattr(args, "recipe", "full"),
        venv=getattr(args, "venv", None),
        run_doctor=not getattr(args, "skip_doctor", False),
    )

    written = _write_handoff_and_state(
        handoff=handoff,
        workspace=workspace,
        repo_workdir=repo_workdir,
        prefix="[handoff]",
    )
    if written is None:
        return 1
    md_path, json_path = written
    print(render_handoff_cli(handoff))
    print(f"[handoff] Written: {md_path}")
    print(f"[handoff] JSON:   {json_path}")
    return 0


def _write_handoff_and_state(
    *,
    handoff: AgentHandoff,
    workspace: Path,
    repo_workdir: Path,
    prefix: str,
) -> tuple[Path, Path] | None:
    """Initialize runtime control files and write handoff artifacts."""
    try:
        state = initialize_runtime_state(
            workspace=workspace,
            runtime=handoff.runtime,
            repo_workdir=repo_workdir,
            actor="cli",
            recipe=handoff.recipe,
        )
        snapshot = create_audience_profile_snapshot(
            workspace=workspace,
            run_id=str((state.get("manifest") or {}).get("run_id") or ""),
            profile_data=profile_data_from_workspace_config(workspace),
        )
        if snapshot.created:
            append_event(
                workspace=workspace,
                run_id=snapshot.run_id,
                event_type="audience_profile_snapshot_created",
                actor="cli",
                reason="Audience profile snapshot created for the current run.",
                metadata={
                    "audience_profile": "audience_profile.md",
                    "audience_profile_snapshot": snapshot.relative_path,
                    "profile_created": snapshot.profile_created,
                    "profile_missing": snapshot.profile_missing,
                    "source_sha256": snapshot.source_sha256,
                    "snapshot_sha256": snapshot.snapshot_sha256,
                },
            )
        if snapshot.profile_missing:
            handoff.notes.append(
                "Audience profile was missing; a default audience_profile.md and frozen snapshot were created for this run."
            )
        _record_doctor_state(
            handoff=handoff,
            workspace=workspace,
            repo_workdir=repo_workdir,
        )
        build_control_switchboard(
            workspace=workspace,
            repo_workdir=repo_workdir,
            actor="cli",
        )
        improvement_snapshot = freeze_improvement_memory_for_run(
            workspace=workspace,
            run_id=str((state.get("manifest") or {}).get("run_id") or ""),
        )
        _apply_improvement_memory_handoff(handoff, improvement_snapshot)
        if improvement_snapshot.snapshot_created_or_changed:
            append_event(
                workspace=workspace,
                run_id=str((state.get("manifest") or {}).get("run_id") or ""),
                event_type="improvement_memory_snapshot_created",
                actor="cli",
                reason="Improvement memory snapshot created for the current run.",
                metadata={
                    "ledger_sha256": improvement_snapshot.manifest_improvement["ledger_sha256"],
                    "memory_sha256": improvement_snapshot.manifest_improvement["memory_sha256"],
                    "snapshot_path": improvement_snapshot.snapshot_path,
                    "snapshot_sha256": improvement_snapshot.snapshot_sha256,
                    "materialized_entry_ids": improvement_snapshot.manifest_improvement["materialized_entry_ids"],
                },
            )
        md_path, json_path = write_handoff_artifacts(handoff, workspace)
        record_handoff_written(
            workspace=workspace,
            handoff_markdown=md_path,
            handoff_json=json_path,
            actor="cli",
        )
        check_runtime_state(
            workspace=workspace,
            repo_workdir=repo_workdir,
            actor="cli",
        )
        return md_path, json_path
    except RuntimeStateError as exc:
        print(f"{prefix} {exc}")
        return None
    except ControlSwitchboardError as exc:
        print(f"{prefix} {exc}")
        return None
    except ImprovementLedgerError as exc:
        print(f"{prefix} {exc}")
        return None


def _apply_improvement_memory_handoff(handoff: AgentHandoff, snapshot_result) -> None:
    if not snapshot_result.snapshot_path:
        handoff.improvement_memory_files = {}
        return

    handoff.improvement_memory_files = {
        "improvement_memory_snapshot": snapshot_result.snapshot_path,
    }
    rules = (
        "Read frozen improvement memory snapshot for this run:\n"
        f"- {snapshot_result.snapshot_path}\n"
        "Use it only as taste/audience guidance. It is not evidence, not source material, "
        "not Claim Ledger input, and not a repair instruction. It must not alter material "
        "facts, claims, citations, or source support. Mid-run ledger or memory edits apply "
        "to later runs only."
    )
    handoff.prompt = f"{handoff.prompt}\n\n{rules}"
    handoff.notes.append(
        "Improvement memory is frozen per run: read the improvement memory snapshot only when present; do not read live improvement memory projections as runtime guidance."
    )


def _record_doctor_state(
    *,
    handoff: AgentHandoff,
    workspace: Path,
    repo_workdir: Path,
) -> None:
    if handoff.doctor_status == "passed":
        complete_stage_transaction(
            workspace=workspace,
            repo_workdir=repo_workdir,
            stage_id="doctor",
            reason="Doctor passed during runtime handoff launch.",
            actor="cli",
        )
    elif handoff.doctor_status == "failed":
        record_decision(
            workspace=workspace,
            repo_workdir=repo_workdir,
            stage_id="doctor",
            decision="block_run",
            reason="Doctor failed during runtime handoff launch.",
            actor="cli",
        )
