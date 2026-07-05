# Repo-Development Protocol

Read this when changing the BriefLoop source repository.

## Source Of Truth

- Runtime role source: `configs/agent_roles.yaml`
- Hand-maintained skills: `.agents/skills/*/SKILL.md`
- Hermes skill: `.agents/hermes-skills/*/SKILL.md`
- CodeBuddy project role agents: `.codebuddy/agents/briefloop-*.md`
  (hand-maintained, source-clone-only)
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
python3 scripts/check_product_baseline.py
python3 scripts/check_briefloop_skill_freshness.py
python3 scripts/check_version_consistency.py
python3 scripts/check_release_consistency.py --no-tag
python3 scripts/check_capabilities.py
git diff --check
```

For Product OS or control-plane PRs, also do at least one adversarial smoke for
the new surface:

- direct import smoke for any new Python module
- valid CLI path smoke when a command is added
- hand-edited artifact smoke for any new artifact
- invalid optional artifacts must not become authority
- missing artifact behavior must be explicit
- registry/event/state writer boundary must be checked
- projection artifacts must not create gate, release, or delivery authority
- include an artifact-level regression, not only a CLI-output assertion

For release/readme public surfaces, `scripts/check_product_baseline.py` is now
part of release readiness. It guards stable product entries, README boundary
wording, `README_en.md` compatibility-pointer shape, and forbidden positive
public claims.
