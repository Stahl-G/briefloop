#!/usr/bin/env python3
"""Check runtime asset inventory and package-data parity."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent

REQUIRED_SOURCE_ASSETS = [
    ".agents/skills/orchestrator/SKILL.md",
    ".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md",
    ".claude/commands/briefloop.md",
    ".claude/commands/mabw.md",
    ".claude/commands/generate-brief.md",
    ".claude/agents/orchestrator.md",
    ".opencode/commands/briefloop.md",
    ".opencode/commands/generate-brief.md",
    ".opencode/agents/brief-orchestrator.md",
    "integrations/hermes-plugin/README.md",
    "integrations/hermes-plugin/mabw/plugin.yaml",
    ".agents/skills/briefloop-workbuddy/SKILL.md",
    ".agents/skills/briefloop-workbuddy/references/quickstart.md",
    ".agents/skills/briefloop-workbuddy/references/workspace-workflow.md",
    ".agents/skills/briefloop-workbuddy/references/artifact-boundary.md",
    ".agents/skills/briefloop-workbuddy/references/status-and-gates.md",
    ".agents/skills/briefloop-workbuddy/references/repair-protocol.md",
    ".agents/skills/briefloop-workbuddy/references/workbuddy-safety.md",
    ".codebuddy/agents/briefloop-scout.md",
    ".codebuddy/agents/briefloop-screener.md",
    ".codebuddy/agents/briefloop-claim-ledger.md",
    ".codebuddy/agents/briefloop-analyst.md",
    ".codebuddy/agents/briefloop-editor.md",
    ".codebuddy/agents/briefloop-auditor.md",
    ".codebuddy/agents/briefloop-formatter.md",
    ".codebuddy/skills/briefloop/SKILL.md",
    "scripts/check_workbuddy_skill_pack.py",
    "scripts/install.sh",
    "scripts/install.ps1",
    "Formula/multi-agent-brief.rb",
]

REQUIRED_PACKAGE_FILES = [
    "src/multi_agent_brief/configs/orchestrator_contract.yaml",
    "src/multi_agent_brief/configs/stage_specs.yaml",
    "src/multi_agent_brief/configs/artifact_contracts.yaml",
    "src/multi_agent_brief/configs/policy_packs/default.yaml",
    "src/multi_agent_brief/configs/policy_profiles/manufacturing_default.yaml",
    "src/multi_agent_brief/evaluation_cases/fixtures/manifest.yaml",
]

REQUIRED_PACKAGE_DATA_PATTERNS = [
    '"configs/*.yaml"',
    '"configs/policy_packs/*.yaml"',
    '"configs/policy_profiles/*.yaml"',
    '"configs/report_packs/*.yaml"',
    '"configs/report_templates/*.yaml"',
    '"evaluation_cases/fixtures/*.yaml"',
    '"evaluation_cases/fixtures/cases/*/workspace/*.yaml"',
    '"evaluation_cases/fixtures/cases/*/workspace/*.md"',
    '"evaluation_cases/fixtures/cases/*/workspace/output/intermediate/*.json"',
    '"evaluation_cases/fixtures/cases/*/workspace/output/intermediate/finalize_candidate/*/*.md"',
]


def main() -> int:
    errors: list[str] = []
    for rel in REQUIRED_SOURCE_ASSETS:
        path = ROOT / rel
        if not path.exists():
            errors.append(f"missing source runtime asset: {rel}")

    for rel in REQUIRED_PACKAGE_FILES:
        path = ROOT / rel
        if not path.exists():
            errors.append(f"missing packaged runtime data file: {rel}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for pattern in REQUIRED_PACKAGE_DATA_PATTERNS:
        if pattern not in pyproject:
            errors.append(f"pyproject package-data missing pattern: {pattern}")

    if errors:
        print("Runtime Asset Parity Check")
        print("=" * 32)
        for error in errors:
            print(f"  [FAIL] {error}")
        print(f"\nFAILED: {len(errors)} issue(s).")
        return 1

    print("[OK] Runtime source assets and packaged contract/eval data are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
