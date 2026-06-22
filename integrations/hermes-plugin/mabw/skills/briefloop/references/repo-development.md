# Repo-Development Protocol

Read this when changing the BriefLoop source repository.

## Source Of Truth

- Runtime role source: `configs/agent_roles.yaml`
- Hand-maintained skills: `.agents/skills/*/SKILL.md`
- Hermes skill: `.agents/hermes-skills/*/SKILL.md`
- Generated platform assets: `.claude/agents/`, `.codex/agents/`,
  `.opencode/agents/`, `docs/agents/`
- Public architecture status: `docs/architecture-status.md`
- Support truth: `docs/support-matrix.md`

Update source files first. Regenerate or check generated assets afterward.

## Skill Impact

If a PR changes how agents should operate BriefLoop, update the canonical skill
or its references and run the skill contract tests.

Skill-impact areas include:

- runtime workspace operation
- MABW-080 experiment operation or future BriefLoop-090 readiness work that uses
  current 080 tooling
- repair protocol
- gates, status, finalize, or delivery behavior
- public claim boundaries
- naming and compatibility
- CLI commands or slash commands

## Validation

Run focused tests for changed areas. For skill changes, run:

```bash
python3 -m pytest -q tests/test_skill_contracts.py tests/test_briefloop_operator_skill.py
python3 scripts/check_skill_contract.py
python3 scripts/check_version_consistency.py
python3 scripts/check_release_consistency.py --no-tag
python3 scripts/check_capabilities.py
git diff --check
```
