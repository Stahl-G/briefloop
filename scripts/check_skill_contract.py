#!/usr/bin/env python3
"""Check the lean BriefLoop operator Skill and packaged projection."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / ".agents" / "skills" / "briefloop"
SKILL = CANONICAL / "SKILL.md"
REFERENCE = CANONICAL / "references" / "codex-controlstore-v2.md"
EVALS = CANONICAL / "evals" / "evals.json"
CLAUDE_WRAPPER = ROOT / ".claude" / "skills" / "briefloop" / "SKILL.md"
PACKAGED = (
    ROOT
    / "src"
    / "multi_agent_brief"
    / "runtime_kits"
    / "codex"
    / "skills"
    / "briefloop"
)
PACKAGED_SKILL = PACKAGED / "SKILL.md"
PACKAGED_REFERENCE = PACKAGED / "references" / "controlstore-v2.md"


def _error(message: str) -> str:
    return f"[skill-contract] {message}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []
    required_files = (
        SKILL,
        REFERENCE,
        EVALS,
        CLAUDE_WRAPPER,
        PACKAGED_SKILL,
        PACKAGED_REFERENCE,
    )
    for path in required_files:
        if not path.exists():
            errors.append(_error(f"missing: {path.relative_to(ROOT)}"))
    if errors:
        _emit(errors)
        return 1

    skill = _read(SKILL)
    reference = _read(REFERENCE)
    wrapper = _read(CLAUDE_WRAPPER)
    packaged_skill = _read(PACKAGED_SKILL)

    for relative in sorted(set(re.findall(r"references/[a-z0-9-]+\.md", skill))):
        if not (CANONICAL / relative).exists():
            errors.append(_error(f"missing referenced file: {relative}"))

    for heading in (
        "## Scope",
        "## Purpose",
        "## Use When",
        "## Inputs",
        "## Outputs",
        "## Work",
        "## Handoff",
    ):
        if heading not in skill:
            errors.append(_error(f"canonical Skill missing heading: {heading}"))

    if ".agents/skills/briefloop/SKILL.md" not in wrapper:
        errors.append(_error("Claude wrapper does not point to canonical Skill"))

    required_protocol = (
        "CoreRunNextAction",
        "RoleTaskEnvelope",
        "runtime continue",
        "role_work_required",
        "needs_human",
        "needs_attention",
        "finalized_local",
        "runtime_action_stale",
        "package_ready",
        "delivered",
        "solar-stock-periodic",
        "market-data",
        "local_derivation_failed",
        "outcome_unknown",
        "FrozenGuidanceContext",
    )
    contract = "\n".join((skill, reference, packaged_skill))
    for phrase in required_protocol:
        if phrase not in contract:
            errors.append(_error(f"runtime protocol missing: {phrase}"))

    for command in (
        "briefloop runtime next",
        "briefloop runtime invocation-start",
        "briefloop runtime invocation-validate",
        "briefloop runtime invocation-accept",
        "briefloop runtime invocation-fail",
        "briefloop runtime apply",
        "--human-request",
    ):
        if command not in reference:
            errors.append(_error(f"runtime reference missing command: {command}"))

    expected_packaged_skill = skill.replace(
        "references/codex-controlstore-v2.md", "references/controlstore-v2.md"
    )
    if packaged_skill != expected_packaged_skill:
        errors.append(_error("packaged Skill differs from canonical projection"))
    if REFERENCE.read_bytes() != PACKAGED_REFERENCE.read_bytes():
        errors.append(_error("packaged ControlStore reference differs from canonical"))

    try:
        evals = json.loads(_read(EVALS))
    except json.JSONDecodeError as exc:
        errors.append(_error(f"evals/evals.json is invalid JSON: {exc}"))
    else:
        if evals.get("skill_name") != "briefloop":
            errors.append(_error("evals skill_name must be briefloop"))
        prompts = evals.get("evals")
        if not isinstance(prompts, list) or len(prompts) < 3:
            errors.append(_error("evals must contain at least three realistic prompts"))

    retired_files = (
        "version-matrix.md",
        "public-claims.md",
        "naming-and-compatibility.md",
        "repair-protocol.md",
        "runtime-workspace.md",
    )
    for filename in retired_files:
        if (CANONICAL / "references" / filename).exists():
            errors.append(_error(f"retired reference restored: {filename}"))

    retired_guidance = (
        "output/intermediate/workflow_state.json",
        "briefloop run --workspace <workspace> --runtime operator",
        "briefloop gates check --workspace",
        "briefloop finalize --config",
    )
    for phrase in retired_guidance:
        if phrase in contract:
            errors.append(_error(f"retired current guidance restored: {phrase}"))

    if "Do not create an agent swarm" not in skill:
        errors.append(_error("Skill does not lock the default single-session boundary"))

    if errors:
        _emit(errors)
        return 1
    print("[skill-contract] ok")
    return 0


def _emit(errors: list[str]) -> None:
    for error in errors:
        print(error, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
