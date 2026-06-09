"""Hermes plugin adapter for Multi-Agent Brief Workflow (MABW)."""

from __future__ import annotations

import json
from pathlib import Path

from . import schemas, tools


def _usage() -> str:
    return (
        "Usage:\n"
        "  /mabw doctor                  — Check environment and report status.\n"
        "  /mabw new                      — Start a fresh brief from onboarding.\n"
        "  /mabw run <workspace>          — Handoff + delegated sequence for an existing workspace.\n"
        "  /mabw continue <workspace>     — Read handoff and resume delegated workflow."
    )


def handle_mabw(ctx, argstr: str):
    """Slash command router: /mabw doctor | new | run <ws> | continue <ws>."""
    parts = argstr.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "doctor":
        report = json.loads(tools.env_doctor({}))
        lines = ["[mabw doctor] Environment status:"]
        lines.append(f"  repo_found:      {report.get('repo_found')}")
        if report.get("repo_root"):
            lines.append(f"  repo_root:       {report['repo_root']}")
        lines.append(f"  plugin_enabled:  {report.get('plugin_enabled')}")
        lines.append(f"  mabw_bin:        {report.get('mabw_bin')}")
        lines.append(f"  version:         {report.get('version')}")
        lines.append(f"  venv_found:      {report.get('venv_found')}")
        lines.append(f"  workspace_found: {report.get('workspace_found')}")
        if report.get("workspaces"):
            lines.append(f"  workspaces:      {', '.join(report['workspaces'])}")
        lines.append(f"  next_action:     {report.get('next_action')}")
        if report.get("hint"):
            lines.append(f"  hint:            {report['hint']}")
        return "\n".join(lines)

    if sub == "new":
        report = json.loads(tools.env_doctor({}))
        if report.get("next_action") not in ("collect_onboarding",):
            return (
                f"[mabw new] Environment not ready: next_action={report.get('next_action')}\n"
                f"Hint: {report.get('hint')}\n\n"
                "Run /mabw doctor for full status."
            )
        return (
            "[mabw new] Starting fresh brief workflow.\n\n"
            "Step 1 — Collect these fields in chat:\n"
            "  company_or_org, industry_or_theme, task_objective,\n"
            "  audience, language, cadence, source_style, output_style,\n"
            "  must_watch, forbidden_sources, web_search_mode.\n\n"
            "Step 2 — Call tools in order:\n"
            "  mabw_create_onboarding → mabw_init_workspace → mabw_run_handoff\n\n"
            "Step 3 — Read <workspace>/output/intermediate/agent_handoff.md\n\n"
            "Step 4 — Continue as the Orchestrator main agent with delegate_task:\n"
            "  scout → screener → claim-ledger → analyst → editor → auditor\n\n"
            "Read contract references before delegation:\n"
            "  configs/orchestrator_contract.yaml, configs/stage_specs.yaml,\n"
            "  configs/artifact_contracts.yaml, configs/policy_packs/default.yaml\n\n"
            "Decision vocabulary:\n"
            "  continue, retry_stage, delegate_repair, request_human_review, block_run, finalize\n\n"
            "Optional feedback controls:\n"
            "  multi-agent-brief feedback ingest/plan/resolve/show/validate structure feedback but do not run repair.\n\n"
            "Orchestrator control switchboard:\n"
            "  Read output/intermediate/orchestrator_control_switchboard.json and record choices with controls select.\n"
            "  Selection is not execution; explicitly run the selected CLI, subagent, or human action afterward.\n\n"
            "Optional quality gate controls:\n"
            "  multi-agent-brief gates check/show/validate writes quality_gate_report.json but does not fetch sources or repair.\n\n"
            "Optional provenance projection:\n"
            "  multi-agent-brief provenance build/show/validate writes provenance_graph.json for audit/debug review only.\n\n"
            "Step 5 — Before finalize, run gates/state controls:\n"
            "  multi-agent-brief gates check --workspace <workspace>\n"
            "  multi-agent-brief state check --workspace <workspace> --strict\n"
            "  multi-agent-brief state decide --workspace <workspace> --stage auditor --decision continue --reason \"Audit and quality gates passed.\"\n\n"
            "Step 6 — Run finalize only after gates/state pass; finalize alone is not a quality-gate executor:\n"
            "  multi-agent-brief finalize --config <workspace>/config.yaml"
        )

    if sub == "run":
        workspace = rest if rest else "./mabw-workspace"
        ws_path = tools._resolve_workspace(workspace)

        # 1. env doctor
        report = json.loads(tools.env_doctor({}))
        if report.get("next_action") in ("clone_repo", "run_setup", "activate_venv"):
            return (
                f"[mabw run] Environment not ready: next_action={report.get('next_action')}\n"
                f"Hint: {report.get('hint')}\n\n"
                "Run /mabw doctor for full status."
            )

        # 2. run handoff
        handoff = tools.run_handoff({"workspace": str(ws_path), "runtime": "hermes"})
        ho = json.loads(handoff)
        if not ho.get("ok"):
            return f"[mabw run] Handoff failed:\n{handoff}"

        lines = [
            f"[mabw run] Handoff complete for: {ws_path}",
            f"  handoff_md: {ho.get('handoff_md')}",
        ]
        if ho.get("handoff_md_exists"):
            lines.append("")
            lines.append("Continue in this Hermes session as the Orchestrator main agent with delegate_task:")
            lines.append("  scout → screener → claim-ledger → analyst → editor → auditor")
            lines.append("")
            lines.append("Read contract references before delegation:")
            lines.append("  configs/orchestrator_contract.yaml, configs/stage_specs.yaml")
            lines.append("  configs/artifact_contracts.yaml, configs/policy_packs/default.yaml")
            lines.append("")
            lines.append("Decide: continue, retry_stage, delegate_repair, request_human_review, block_run, or finalize.")
            lines.append("Read orchestrator_control_switchboard.json and record control choices with controls select; selection is not execution.")
            lines.append("Use feedback ingest/plan/resolve/show/validate only when audit findings or human feedback exist; these commands do not execute repair.")
            lines.append("Use gates check/show/validate before finalize; these commands do not fetch sources or repair.")
            lines.append("Use provenance build/show/validate only when an audit/debug graph is useful; it is not semantic proof and is not required before finalize.")
            lines.append("")
            lines.append("Before finalize, run:")
            lines.append(f"  multi-agent-brief controls select --workspace {ws_path} --control quality_gates --selection enable --reason \"Use quality gates before finalize.\"")
            lines.append(f"  multi-agent-brief gates check --workspace {ws_path}")
            lines.append(f"  multi-agent-brief state check --workspace {ws_path} --strict")
            lines.append(f"  multi-agent-brief state decide --workspace {ws_path} --stage auditor --decision continue --reason \"Audit and quality gates passed.\"")
            lines.append("")
            lines.append("Then run finalize. finalize alone is not a quality-gate executor:")
            lines.append(f"  multi-agent-brief finalize --config {ws_path}/config.yaml")
            lines.append("")
            lines.append("Optional provenance projection after runtime state exists:")
            lines.append(f"  multi-agent-brief provenance build --workspace {ws_path}")
            lines.append(f"  multi-agent-brief provenance show --workspace {ws_path} --json")
            lines.append(f"  multi-agent-brief provenance validate --workspace {ws_path}")
        else:
            lines.append("")
            lines.append(f"Handoff not found. Check init state of {ws_path}.")
        return "\n".join(lines)

    if sub == "continue":
        workspace = rest if rest else "./mabw-workspace"
        ws_path = tools._resolve_workspace(workspace)
        handoff_md = ws_path / "output" / "intermediate" / "agent_handoff.md"

        if not handoff_md.exists():
            return (
                f"[mabw continue] Handoff not found at: {handoff_md}\n"
                f"Run /mabw run {workspace} first, or /mabw new for a fresh brief."
            )

        lines = [
            f"[mabw continue] Resuming from: {ws_path}",
            f"Handoff: {handoff_md}",
            "",
            "Read the handoff file, then continue as the Orchestrator main agent with delegate_task:",
            "  scout → screener → claim-ledger → analyst → editor → auditor",
            "",
            "Read contract references before delegation:",
            "  configs/orchestrator_contract.yaml, configs/stage_specs.yaml",
            "  configs/artifact_contracts.yaml, configs/policy_packs/default.yaml",
            "",
            "Decide: continue, retry_stage, delegate_repair, request_human_review, block_run, or finalize.",
            "Read orchestrator_control_switchboard.json and record control choices with controls select; selection is not execution.",
            "Use feedback ingest/plan/resolve/show/validate only when audit findings or human feedback exist; these commands do not execute repair.",
            "Use gates check/show/validate before finalize; these commands do not fetch sources or repair.",
            "Use provenance build/show/validate only when an audit/debug graph is useful; it is not semantic proof and is not required before finalize.",
            "",
            "Before finalize, run:",
            f"  multi-agent-brief controls select --workspace {ws_path} --control quality_gates --selection enable --reason \"Use quality gates before finalize.\"",
            f"  multi-agent-brief gates check --workspace {ws_path}",
            f"  multi-agent-brief state check --workspace {ws_path} --strict",
            f"  multi-agent-brief state decide --workspace {ws_path} --stage auditor --decision continue --reason \"Audit and quality gates passed.\"",
            "",
            "Then run finalize. finalize alone is not a quality-gate executor:",
            f"  multi-agent-brief finalize --config {ws_path}/config.yaml",
            "",
            "Optional provenance projection after runtime state exists:",
            f"  multi-agent-brief provenance build --workspace {ws_path}",
            f"  multi-agent-brief provenance show --workspace {ws_path} --json",
            f"  multi-agent-brief provenance validate --workspace {ws_path}",
        ]
        return "\n".join(lines)

    # No recognized subcommand — show usage
    return _usage()


def _register_command_compat(ctx, name: str, handler, description: str) -> None:
    """Register a slash command across Hermes versions that use help/description."""
    try:
        ctx.register_command(name, handler, description=description)
    except TypeError:
        ctx.register_command(name, handler, help=description)


def register(ctx):
    """Register MABW tools, slash command, and bundled skill."""
    ctx.register_tool(
        name="mabw_env_doctor",
        toolset="mabw",
        schema=schemas.MABW_ENV_DOCTOR,
        handler=tools.env_doctor,
    )
    ctx.register_tool(
        name="mabw_create_onboarding",
        toolset="mabw",
        schema=schemas.MABW_CREATE_ONBOARDING,
        handler=tools.create_onboarding,
    )
    ctx.register_tool(
        name="mabw_init_workspace",
        toolset="mabw",
        schema=schemas.MABW_INIT_WORKSPACE,
        handler=tools.init_workspace,
    )
    ctx.register_tool(
        name="mabw_run_handoff",
        toolset="mabw",
        schema=schemas.MABW_RUN_HANDOFF,
        handler=tools.run_handoff,
    )

    _register_command_compat(
        ctx,
        "mabw",
        handle_mabw,
        "MABW workflow router — /mabw doctor | new | run <ws> | continue <ws>.",
    )

    skill_md = Path(__file__).parent / "skills" / "mabw-workflow" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill("mabw-workflow", skill_md)
