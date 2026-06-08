# AGENTS.md

## Purpose

Multi-Agent Brief Workflow is a subagent-first briefing toolkit.

Python CLI commands provide onboarding, workspace setup, runtime handoff, source tooling, validation, audit checks, and final rendering. The selected agent runtime coordinates the brief workflow through handoff artifacts and role-specific agents.

## Instruction Scope

This repository contains both development code and runtime agent contracts.

In repository development mode, files under `.agents/skills/`, `.agents/hermes-skills/`, `.claude/agents/`, `.codex/agents/`, and `.opencode/agents/` are source assets to inspect, edit, and test. Their role instructions become active only when the corresponding runtime explicitly invokes that role or skill.

Use `multi-agent-brief run --workspace <workspace>` to create a runtime handoff. The handoff artifact, not this repository manual, is the execution contract for a specific brief run.

## Environment Separation

Keep three instruction environments separate:

- Personal Codex environment: `~/.codex/AGENTS.md` is private user-level guidance. Do not copy its personal workflows, private paths, or local preferences into this public repository.
- Repository development environment: this `AGENTS.md` guides contributors and coding agents working on the MABW source repo.
- End-user brief workspace environment: generated workspaces use `config.yaml`, `sources.yaml`, `user.md`, runtime handoff artifacts, and role skills. They must not depend on this repository `AGENTS.md` as their execution contract.

Do not treat repository development instructions as user-facing product behavior. If users need guidance, put it in README, docs, CLI help, generated handoff artifacts, or runtime skills.

## Context Mode

### Repository development mode

If the current directory contains `pyproject.toml`, `src/`, `tests/`, or `scripts/`, treat it as the source repository.

Use repository files for development, debugging, tests, generated configs, and documentation updates.

### Generated workspace mode

If the current directory contains `config.yaml`, `sources.yaml`, `user.md`, and `input/`, treat it as an end-user brief workspace.

Use workspace files as task context. Treat repository README, examples, agent configs, and docs as references, not source evidence.

## Standard User Path

For a real brief workspace:

```bash
multi-agent-brief onboard
multi-agent-brief init <workspace> --from-onboarding onboarding.json
multi-agent-brief run --workspace <workspace>
```

For demo exploration:

```bash
multi-agent-brief init <workspace> --demo
multi-agent-brief run --workspace <workspace>
```

`run` is the standard user-facing runtime handoff launcher. Hermes is the default runtime unless another runtime is selected.

## Runtime Handoff

Supported runtimes:

- `hermes`: Hermes parent agent uses `delegate_task` children.
- `claude`: Claude Code uses the repository command workflow.
- `opencode`: OpenCode uses the repository command and agent files.
- `codex`: Codex uses repository agent instructions and generated configs.
- `manual`: prints the artifact workflow.

Canonical workflow:

```text
doctor
→ source discovery when configured
→ input governance when available
→ scout
→ screener
→ claim-ledger
→ analyst
→ editor
→ auditor
→ finalize
```

## Development Standards

Use these standards for every repository change, especially before opening, updating, or merging a PR.

For architecture orientation before roadmap-driven work, read `docs/agent-dev-guide.zh-CN.md`, `docs/architecture-status.md`, `docs/MIGRATION.md`, `docs/orchestrator-contracts.md`, and `docs/support-matrix.md`.

### Version And Release Semantics

- If a branch, PR, README, changelog, or commit title claims a released version such as `v0.6.0`, update the release source files together: `VERSION`, `pyproject.toml`, `README.md`, `README_en.md`, `CHANGELOG.md`, Hermes skill metadata, and any version defaults in code.
- If the change is only a pre-release architecture slice, do not describe it as the current released version. Use wording such as "planned", "pre-v0.6", or "v0.6 milestone target".
- The current version line must describe implemented, tested capability. Roadmap goals are not release notes.
- Run version checks after any release or version wording change.

### Architecture Truth

- `multi-agent-brief run` must stay a runtime handoff launcher. It must not generate the brief through a Python full pipeline.
- `prepare` must stay legacy/deprecated and must not call a removed Python workflow.
- Do not reintroduce `BriefPipeline`, Python fake Agent classes, or support-matrix wording that makes the removed Python pipeline look available.
- When changing architecture, update `docs/architecture-status.md`, `docs/MIGRATION.md`, `docs/support-matrix.md`, `README.md`, and `README_en.md` if user-facing capability or support status changes.

### Packaging And Install Paths

- Source-clone behavior and installed-package behavior are different surfaces. Test both when changing CLI entrypoints, runtime handoff, contract references, generated assets, installers, or package data.
- If runtime code needs files outside `src/`, either package those files and resolve them from installed resources, or explicitly mark the feature as source-clone-only in docs and support matrix.
- curl, PowerShell, Homebrew, and PyPI/archive installs must not rely on an untracked local source repo unless the docs clearly say so.
- Non-dev smoke should include `init`, `doctor`, and `run --workspace` for a demo workspace when `run` behavior changes.

### Public Planning Boundary

- Keep public roadmap and architecture docs high-level.
- Detailed implementation plans, schema drafts, private golden cases, prompt notes, failure taxonomies, and commercial scenario design belong only in ignored paths such as `private_planning/`, `docs/internal/`, `*.private.md`, or `*.internal.md`.
- Do not commit private planning files. Public docs may link to public-safe implementation overviews only when they do not expose private schema, prompts, or golden cases.

### Generated Assets And Source Of Truth

- Update source files first, then regenerate/check generated files.
- Runtime role source is `configs/agent_roles.yaml`.
- Hand-maintained skills live under `.agents/skills/` and `.agents/hermes-skills/`.
- Generated platform assets live under `.claude/agents/`, `.codex/agents/`, `.opencode/agents/`, and `docs/agents/`.
- When generated platform assets change, run `python3 scripts/generate_agent_configs.py --check` before finishing.

### CI And Smoke Reliability

- Release checks must be hermetic to the repo checkout. Avoid scripts that accidentally import an older globally installed package instead of current `src/`.
- Add or update tests for the user-facing surface that changed, not only the helper function.
- Do not remove CI failures by weakening the contract. If a check is obsolete, update it to match the new architecture and document why.
- Before push, check both `README.md` and `README_en.md` for current user-facing behavior.

## Onboarding

Use `multi-agent-brief onboard` for requirement capture. It collects company or organization, industry or theme, task objective, audience, language, cadence, source style, output style, must-watch topics, excluded topics, and source/search preference.

For details, see `docs/onboarding.md`.

## Role Details

- `.agents/skills/*/SKILL.md` — runtime capability contracts, hand-maintained.
- `.agents/hermes-skills/*/SKILL.md` — Hermes runtime skills and reference files, hand-maintained.
- `.claude/agents/*.md` — Claude Code subagent definitions.
- `.codex/agents/*.toml` — Codex custom agent configs.
- `.opencode/agents/*.md` — OpenCode subagent definitions.
- `docs/agents/` — generated platform adapter documentation.

## Artifact Contract

Expected workflow artifacts:

```text
output/intermediate/candidate_claims.json
output/intermediate/screened_candidates.json
output/intermediate/claim_ledger.json
output/intermediate/audited_brief.md
output/intermediate/audit_report.json
output/brief.md
```

Detailed schemas, delivery gates, and harness rules belong in tests, validators, audit rules, and docs.

## Common Validation

Use focused tests for changed areas. Common checks:

```bash
python3 -m pytest -q
python3 scripts/generate_agent_configs.py --check
python3 scripts/check_version_consistency.py
python3 scripts/check_release_consistency.py --no-tag
python3 scripts/check_capabilities.py
```

For install-path or runtime-handoff changes, also run a non-editable install smoke from outside the repo:

```bash
python3 -m venv /tmp/mabw-install-smoke
/tmp/mabw-install-smoke/bin/python -m pip install .
/tmp/mabw-install-smoke/bin/multi-agent-brief init /tmp/mabw-smoke --demo --force
/tmp/mabw-install-smoke/bin/multi-agent-brief doctor --config /tmp/mabw-smoke/config.yaml
cd /tmp
/tmp/mabw-install-smoke/bin/multi-agent-brief run --workspace /tmp/mabw-smoke --skip-doctor
```
