"""Hermes plugin adapter for Multi-Agent Brief Workflow (MABW)."""

from __future__ import annotations

from pathlib import Path

from . import schemas, tools


def handle_mabw(ctx, argstr: str):
    """Slash command entrypoint: /mabw <workspace>."""
    workspace = argstr.strip() or "./mabw-workspace"
    return (
        "MABW Hermes workflow\n\n"
        f"Workspace: {workspace}\n\n"
        "Collect the brief profile in chat, then use the MABW tools in this order:\n"
        "1. mabw_create_onboarding\n"
        "2. mabw_init_workspace\n"
        "3. mabw_run_handoff\n\n"
        "After handoff is created, read agent_handoff.md and continue the delegated workflow."
    )


def _register_command_compat(ctx, name: str, handler, description: str) -> None:
    """Register a slash command across Hermes versions that use help/description."""
    try:
        ctx.register_command(name, handler, description=description)
    except TypeError:
        ctx.register_command(name, handler, help=description)


def register(ctx):
    """Register MABW tools, slash command, and bundled skill."""
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
        "Start a Multi-Agent Brief Workflow onboarding and handoff flow.",
    )

    skill_md = Path(__file__).parent / "skills" / "mabw-workflow" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill("mabw-workflow", skill_md)
